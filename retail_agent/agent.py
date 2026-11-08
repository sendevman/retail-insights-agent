import json
import secrets
import time
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .knowledge import retrieve
from .models import Plan
from .safety import SCHEMA_HELP, QueryError, SafeError, private_request, redact
from .sql import COMPILER_VERSION, compile_query


class State(TypedDict, total=False):
    question: str
    trace_id: str
    context: dict
    plan: Plan
    results: list[dict]
    feedback: str
    plan_attempts: int
    output: str
    report_title: str
    report_ready: bool


def cell(value):
    # Bounded, escaped table values; never expose full pseudonymous identifiers.
    text = str(value) if value is not None else "—"
    return redact(text).replace("|", "\\|").replace("\n", " ")[:160]


def render_evidence(results, format_preference):
    blocks = []
    for result in results:
        spec = result["spec"]
        scope = ",".join(map(str, spec["product_ids"])) or "all assigned products"
        blocks.append(
            f"\n{result['id']}: {cell(result['label'])}\n"
            f"Period: {spec['start_date']} ≤ date < {spec['end_date']} UTC; scope: {scope}"
        )
        if result.get("error"):
            blocks.append(f"Unavailable ({result['error']}); excluded from conclusions.")
            continue
        rows = result["rows"]
        if not rows:
            blocks.append("No matching data. Filters were retained; no broader query was run.")
            continue
        headers = list(rows[0])
        if format_preference == "tables":
            blocks.append("| Evidence | " + " | ".join(headers) + " |")
            blocks.append("| " + " | ".join(["---"] * (len(headers) + 1)) + " |")
            for i, row in enumerate(rows, 1):
                blocks.append(
                    f"| {result['id']}:r{i} | " + " | ".join(cell(row[h]) for h in headers) + " |"
                )
        else:
            for i, row in enumerate(rows, 1):
                blocks.append(
                    f"- [{result['id']}:r{i}] " + "; ".join(f"{h}: {cell(row[h])}" for h in headers)
                )
        if result["truncated"]:
            blocks.append("Top rows only; the result is truncated. Do not total this table.")
    return "\n".join(blocks)


class Analyst:
    def __init__(self, model, runner, events, allowed_products, secret, root: Path):
        self.model, self.runner, self.events = model, runner, events
        self.allowed, self.secret, self.root = allowed_products, secret, root
        self.history = []
        self.query_calls = 0
        graph = StateGraph(State)
        graph.add_node("plan", self._plan)
        graph.add_node("query", self._query)
        graph.add_node("report", self._report)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", lambda s: END if s.get("output") else "query")
        graph.add_conditional_edges(
            "query",
            lambda s: "plan" if s.get("feedback") else (END if s.get("output") else "report"),
        )
        graph.add_edge("report", END)
        self.graph = graph.compile()  # No automatic external tracing/checkpoint persistence.

    def ask(self, question, format_preference="tables"):
        trace_id = secrets.token_hex(12)
        started = time.monotonic()
        self.model.reset_budget()
        self.query_calls = 0
        self.events.emit(trace_id, "turn_started", compiler_version=COMPILER_VERSION)
        try:
            if len(question) > 4000:
                raise SafeError("Please shorten the question to 4,000 characters.")
            if private_request(question):
                raise SafeError(
                    "Direct PII cannot be displayed. Ask for aggregates or opaque "
                    "customer keys instead."
                )
            persona = json.loads((self.root / "config/persona.json").read_text())
            # Only these fields influence style; arbitrary new policy fields are ignored.
            persona = {
                "version": str(persona["version"])[:80],
                "tone": str(persona["tone"])[:800],
                "max_findings": min(6, max(1, int(persona["max_findings"]))),
            }
            golden = retrieve(self.root / "knowledge/trios.json", redact(question), self.allowed)
            self.events.emit(
                trace_id,
                "context_loaded",
                persona_version=persona["version"],
                golden_ids=[x["id"] for x in golden],
            )
            context = {
                "allowed_products": self.allowed,
                "persona": persona,
                "golden_examples": golden,
                "history": self.history[-4:],
                "format_preference": format_preference,
            }
            state = self.graph.invoke(
                {
                    "question": redact(question),
                    "trace_id": trace_id,
                    "context": context,
                    "plan_attempts": 0,
                    "feedback": "",
                    "results": [],
                    "report_ready": False,
                },
                config={"recursion_limit": 12},
            )
            output = redact(state["output"])
            # Keep typed analytical context only, not transcript/user text/results.
            if state.get("plan"):
                self.history.append(state["plan"].model_dump(mode="json"))
                self.history = self.history[-4:]
            self.events.emit(
                trace_id,
                "turn_complete",
                status="ok",
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
            return {
                "text": output + f"\n\nTrace: {trace_id}",
                "trace_id": trace_id,
                "report_ready": state.get("report_ready", False),
                "title": redact(state.get("report_title", "Analysis")),
            }
        except Exception as exc:
            # Raw exception messages may contain queries/PII/secrets. Never print/log them.
            code = exc.code if isinstance(exc, QueryError) else type(exc).__name__
            self.events.emit(
                trace_id,
                "turn_failed",
                code=code,
                status="failed",
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
            message = (
                str(exc)
                if isinstance(exc, SafeError)
                else ("The analysis could not be completed. Check configuration or try again.")
            )
            return {
                "text": redact(message) + f"\nTrace: {trace_id}",
                "trace_id": trace_id,
                "report_ready": False,
                "title": "",
            }

    def _plan(self, state):
        attempt = state["plan_attempts"] + 1
        self.events.emit(state["trace_id"], "stage_started", stage="plan", attempt=attempt)
        try:
            plan = self.model.plan(
                state["question"], state["context"], state.get("feedback", ""), state["trace_id"]
            )
            # Validate again even when a fake/alternate provider constructs the object.
            plan = Plan.model_validate(plan.model_dump())
            for spec in plan.queries:
                compile_query(spec, self.allowed, self.secret)
        except (QueryError, ValidationError) as exc:
            if attempt < 2 and (
                isinstance(exc, ValidationError) or exc.code == "model_response_invalid"
            ):
                # One bounded repair, no raw validation messages/input echo.
                plan = self.model.plan(
                    state["question"],
                    state["context"],
                    "Invalid structured plan. Recheck schema and date/sort rules.",
                    state["trace_id"],
                )
                attempt += 1
                plan = Plan.model_validate(plan.model_dump())
                for spec in plan.queries:
                    compile_query(spec, self.allowed, self.secret)
            else:
                raise
        if state.get("feedback") and state.get("plan"):
            # A repair may simplify projections but cannot silently widen filters/dates.
            original = state["plan"].queries
            if len(plan.queries) != len(original):
                raise SafeError("The repaired plan changed scope. Please restate the analysis.")
            filters = ("start_date", "end_date", "product_ids", "states", "countries")
            if any(
                getattr(new, key) != getattr(old, key)
                for new, old in zip(plan.queries, original)
                for key in filters
            ):
                raise SafeError("The repaired plan changed filters. Please restate the analysis.")
        updates = {"plan": plan, "plan_attempts": attempt, "feedback": "", "results": []}
        if plan.intent != "analyze":
            # Use fixed refusal/schema language instead of a freeform off-topic completion.
            if plan.intent == "schema":
                updates["output"] = SCHEMA_HELP
            elif plan.intent == "refuse":
                updates["output"] = (
                    "I can help with retail data analysis within your product access."
                )
            else:
                updates["output"] = redact(plan.message or "Please clarify the period and metric.")
        return updates

    def _query(self, state):
        results = []
        for i, spec in enumerate(state["plan"].queries, 1):
            query_id = f"q{i}"
            query = compile_query(spec, self.allowed, self.secret)
            result = {"id": query_id, "label": spec.label, "spec": spec.model_dump(mode="json")}
            for attempt in range(2):
                try:
                    if self.query_calls >= 6:
                        raise QueryError("query_call_budget_exhausted")
                    self.query_calls += 1
                    data = self.runner.run(query, spec, state["trace_id"], query_id, self.events)
                    # Use short opaque aliases, not full persistent customer pseudonyms.
                    for row in data["rows"]:
                        for key, value in row.items():
                            if isinstance(value, str):
                                row[key] = redact(value)
                        if "customer_key" in row:
                            row["customer_key"] = "customer-" + row["customer_key"][:16]
                    result.update(data)
                    if not data["rows"]:
                        self.events.emit(state["trace_id"], "query_empty", query_id=query_id)
                        # Retry exactly once: same filters, no authorization/date widening.
                        if attempt == 0:
                            continue
                    break
                except QueryError as exc:
                    self.events.emit(
                        state["trace_id"],
                        "query_error",
                        query_id=query_id,
                        code=exc.code,
                        attempt=attempt + 1,
                    )
                    if exc.code == "query_invalid" and state["plan_attempts"] < 2:
                        return {
                            "feedback": "Warehouse rejected the compiled query. Rebuild a "
                            "simpler valid plan with the same requested scope and dates.",
                            "results": [],
                        }
                    if exc.retryable and attempt == 0:
                        time.sleep(0.25)
                        continue
                    result.update(rows=[], truncated=False, error=exc.code)
                    break
            results.append(result)
        return {"results": results, "feedback": ""}

    def _report(self, state):
        results = state["results"]
        evidence = render_evidence(results, state["context"]["format_preference"])
        available = [r for r in results if r.get("rows")]
        if not available:
            return {
                "output": "No usable results. Check dates, filters, or service status.\n"
                + evidence,
                "report_ready": False,
            }
        try:
            report = self.model.report(
                state["question"], results, state["context"], state["trace_id"]
            )
            valid_refs = {
                f"{r['id']}:r{i}" for r in available for i in range(1, len(r["rows"]) + 1)
            }
            if any(ref not in valid_refs for f in report.findings for ref in f.evidence):
                raise QueryError("report_evidence_invalid")
            lines = [report.title, "", "AI interpretation (verify against the evidence below):"]
            for finding in report.findings[: state["context"]["persona"]["max_findings"]]:
                lines.append(
                    f"- {finding.kind.title()}: {finding.text} [{', '.join(finding.evidence)}]"
                )
            if report.actions:
                lines += ["", "Proposed actions:"] + ["- " + a[:650] for a in report.actions]
            if report.limitations:
                lines += ["", "Limitations:"] + ["- " + a[:650] for a in report.limitations]
            title = report.title
        except Exception:
            self.events.emit(
                state["trace_id"], "report_fallback", code="report_unavailable_or_invalid"
            )
            title = "Retail analysis — evidence only"
            lines = [
                title,
                "Narrative unavailable. Verified query evidence is shown below.",
                "Proposed action: validate the observed differences before changing operations.",
            ]
        if any(r.get("error") for r in results):
            lines.append("PARTIAL REPORT: one or more queries failed; conclusions are incomplete.")
        lines += [
            "",
            state["plan"].message,
            evidence,
            "",
            "Revenue: completed item sale price, excluding returned/cancelled items. "
            "Observational data cannot establish causes. Customer keys are pseudonymous.",
        ]
        return {"output": "\n".join(lines), "report_title": title, "report_ready": True}
