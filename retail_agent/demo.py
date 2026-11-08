"""Explicitly synthetic offline mode: deterministic planner and Python aggregation.

This demonstrates CLI/workflow/security without claiming a live Gemini/BQ execution.
"""

import hashlib
import re
from collections import defaultdict
from datetime import date

from .models import Dimension, Finding, Plan, QuerySpec, Report


class DemoModel:
    def reset_budget(self):
        pass

    def plan(self, question, context, feedback, trace_id):
        lower = question.lower()
        if "churn" in lower:
            return Plan(
                intent="clarify",
                message="Churn is undefined here. Agree an inactivity "
                "window and cohort first; cohort analysis is a production extension.",
            )
        if any(w in lower for w in ("schema", "structure", "available", "what can")):
            return Plan(intent="schema")
        if not any(
            w in lower
            for w in (
                "revenue",
                "sales",
                "spend",
                "report",
                "product",
                "customer",
                "compare",
                "monthly",
            )
        ):
            return Plan(intent="refuse", message="I can help with retail data analysis only.")
        dimensions = ["month"]
        if "product" in lower or "compare" in lower:
            dimensions = ["product_id"]
        if "state" in lower:
            dimensions = ["state"]
        if "top customer" in lower:
            dimensions = ["customer_key"]
        products = [int(p) for p in re.findall(r"\b\d+\b", question) if int(p) < 100]
        states = re.findall(r"\b(California|Texas)\b", question, flags=re.I)
        states = [s.title() for s in states]
        queries = [
            QuerySpec(
                label="Synthetic Q1 2025 comparison",
                dimensions=dimensions,
                metrics=["revenue", "units", "orders", "customers", "avg_order_value"],
                start_date=date(2025, 1, 1),
                end_date=date(2025, 4, 1),
                product_ids=products,
                states=states,
                order_by="revenue",
            )
        ]
        if "report" in lower:
            queries.append(
                queries[0].model_copy(
                    update={"dimensions": [Dimension.product_id], "label": "Synthetic product mix"}
                )
            )
        return Plan(
            intent="analyze", message="DEMO: fixed synthetic Q1 2025 data.", queries=queries
        )

    def report(self, question, results, context, trace_id):
        evidence = [f"{r['id']}:r1" for r in results if r["rows"]]
        return Report(
            title="Synthetic retail performance report",
            findings=[
                Finding(
                    text="The evidence tables show completed sales within the selected product scope.",
                    evidence=evidence[:5],
                    kind="observation",
                )
            ],
            actions=["Validate product mix and completed units before changing promotions."],
            limitations=[
                "Synthetic fixtures demonstrate behavior; these are not live retail results."
            ],
        )


class DemoRunner:
    def run(self, query, spec, trace_id, query_id, events):
        facts = []
        for month in (1, 2, 3):
            for product in query.parameters["selected_products"]:
                for n in range(1, 7):
                    day = date(2025, month, n)
                    state = "California" if n % 2 else "Texas"
                    if not spec.start_date <= day < spec.end_date:
                        continue
                    if spec.states and state not in spec.states:
                        continue
                    if spec.countries and "United States" not in spec.countries:
                        continue
                    key = hashlib.sha256(
                        (query.parameters["pseudonym_secret"] + f":demo-{product}-{n}").encode()
                    ).hexdigest()
                    facts.append(
                        {
                            "month": day.strftime("%Y-%m"),
                            "day": day,
                            "product_id": product,
                            "category": "Synthetic category",
                            "state": state,
                            "country": "United States",
                            "customer_key": key,
                            "status": "Returned" if n == 6 else "Complete",
                            "sale_price": float(10 * product + month),
                            "order_id": f"{month}-{product}-{n}",
                        }
                    )
        groups = defaultdict(list)
        for fact in facts:
            groups[tuple(fact[d.value] for d in spec.dimensions)].append(fact)
        rows = []
        for dims, items in groups.items():
            complete = [x for x in items if x["status"] == "Complete"]
            revenue = sum(x["sale_price"] for x in complete)
            complete_orders = len({x["order_id"] for x in complete})
            metrics = {
                "revenue": round(revenue, 2),
                "units": len(complete),
                "orders": len({x["order_id"] for x in items}),
                "customers": len({x["customer_key"] for x in items}),
                "avg_order_value": round(revenue / complete_orders, 2) if complete_orders else None,
                "return_rate": sum(x["status"] == "Returned" for x in items) / len(items),
            }
            row = dict(zip([d.value for d in spec.dimensions], dims))
            row.update({m.value: metrics[m.value] for m in spec.metrics})
            row["sample_items"] = len(items)
            rows.append(row)
        rows.sort(
            key=lambda r: (r[spec.order_by] is not None, r[spec.order_by]), reverse=spec.descending
        )
        events.emit(
            trace_id,
            "query_complete",
            query_id=query_id,
            rows=min(len(rows), spec.limit),
            sql_hash=query.fingerprint,
            bytes_processed=0,
            job_id="synthetic",
        )
        return {
            "rows": rows[: spec.limit],
            "truncated": len(rows) > spec.limit,
            "bytes_processed": 0,
            "job_id": "synthetic",
        }
