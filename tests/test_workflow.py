import json

from retail_agent.cli import Chat
from retail_agent.demo import DemoModel, DemoRunner
from retail_agent.knowledge import retrieve
from retail_agent.models import Plan
from retail_agent.safety import QueryError


def test_correct_synthetic_metrics_and_report(analyst):
    result = analyst.ask("Compare products 1 and 2")
    assert "330.0" in result["text"] and "180.0" in result["text"]
    assert result["report_ready"]
    assert "Proposed actions" in result["text"]
    assert "q1:r1" in result["text"]


def test_schema_and_undefined_churn(analyst):
    assert "order_items" in analyst.ask("What data is available?")["text"]
    result = analyst.ask("Why did churn spike last month?")
    assert "undefined" in result["text"]
    assert not result["report_ready"]


def test_cross_scope_denied_before_runner(analyst):
    class NeverRun:
        def run(self, *args):
            raise AssertionError("Unauthorized query ran")

    analyst.runner = NeverRun()
    assert "outside your assigned scope" in analyst.ask("Compare products 1 and 11")["text"]


def test_one_transient_retry(analyst):
    class Flaky(DemoRunner):
        calls = 0

        def run(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise QueryError("warehouse_unavailable", retryable=True)
            return super().run(*args)

    runner = Flaky()
    analyst.runner = runner
    assert analyst.ask("Compare products 1 and 2")["report_ready"]
    assert runner.calls == 2


def test_permanent_failure_not_retried(analyst):
    class Failed:
        calls = 0

        def run(self, *args):
            self.calls += 1
            raise QueryError("warehouse_access_or_quota_denied")

    analyst.runner = Failed()
    result = analyst.ask("Compare products 1 and 2")
    assert not result["report_ready"]
    assert analyst.runner.calls == 1


def test_empty_retries_same_filters_once(analyst):
    class Empty:
        calls = []

        def run(self, query, spec, *args):
            self.calls.append(query.parameters)
            return {"rows": [], "truncated": False}

    runner = Empty()
    analyst.runner = runner
    result = analyst.ask("Compare products 1 and 2")
    assert "No matching data" in result["text"]
    assert len(runner.calls) == 2 and runner.calls[0] == runner.calls[1]


def test_query_syntax_error_causes_one_replan(analyst):
    class InvalidOnce(DemoRunner):
        calls = 0

        def run(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise QueryError("query_invalid")
            return super().run(*args)

    runner = InvalidOnce()
    analyst.runner = runner
    assert analyst.ask("Compare products 1 and 2")["report_ready"]
    assert runner.calls == 2


def test_syntax_repair_cannot_widen_dates(analyst):
    from datetime import date

    class ChangeScope(DemoModel):
        calls = 0

        def plan(self, *args):
            self.calls += 1
            plan = super().plan(*args)
            if self.calls > 1:
                plan.queries[0].start_date = date(2024, 1, 1)
            return plan

    class Invalid:
        def run(self, *args):
            raise QueryError("query_invalid")

    analyst.model = ChangeScope()
    analyst.runner = Invalid()
    assert "changed filters" in analyst.ask("Compare products 1 and 2")["text"]


def test_six_query_call_budget_across_replanning(analyst):
    class Exhausted(DemoRunner):
        calls = 0

        def run(self, *args):
            self.calls += 1
            # Return an empty first query twice, then syntax error in the second query.
            if self.calls == 3:
                raise QueryError("query_invalid")
            return {"rows": [], "truncated": False}

    analyst.runner = Exhausted()
    analyst.ask("Create a Q1 2025 report")
    assert analyst.runner.calls == 6


def test_bad_report_falls_back_to_evidence(analyst):
    class NoReport(DemoModel):
        def report(self, *args):
            raise RuntimeError("secret@example.com")

    analyst.model = NoReport()
    result = analyst.ask("Compare products 1 and 2")
    assert "evidence only" in result["text"]
    assert "330.0" in result["text"]
    assert "secret@example.com" not in result["text"]


def test_unknown_evidence_rejected(analyst):
    class BadCitation(DemoModel):
        def report(self, *args):
            result = super().report(*args)
            result.findings[0].evidence = ["q8:r100"]
            return result

    analyst.model = BadCitation()
    assert "evidence only" in analyst.ask("Compare products 1 and 2")["text"]


def test_partial_report_discloses_failure(analyst):
    class Partial(DemoRunner):
        def run(self, query, spec, trace, qid, events):
            if qid == "q2":
                raise QueryError("warehouse_unavailable")
            return super().run(query, spec, trace, qid, events)

    analyst.runner = Partial()
    result = analyst.ask("Create a Q1 2025 report")
    assert "PARTIAL REPORT" in result["text"]
    assert result["report_ready"]


def test_preferences_persist_and_auto_save(analyst, store, events):
    chat = Chat(analyst, store, "alice", "s1", events)
    assert "saved" in chat.handle("/prefer bullets")
    assert store.preference("alice") == "bullets"
    assert store.preference("bob") == "tables"
    result = chat.handle("Create a Q1 2025 report")
    assert "Report saved:" in result
    assert "- [q1:r1]" in result
    assert len(store.list("alice")) == 1


def test_golden_acl_and_version_filter(tmp_path):
    path = tmp_path / "trios.json"
    base = {"question": "monthly revenue", "status": "approved", "schema_version": "scoped-item-v1"}
    path.write_text(
        json.dumps(
            [
                {**base, "id": "ok", "product_ids": []},
                {**base, "id": "no-access", "product_ids": [999]},
                {**base, "id": "old", "schema_version": "old"},
                {**base, "id": "draft", "status": "draft"},
            ]
        )
    )
    assert [r["id"] for r in retrieve(path, "monthly revenue", [1, 2])] == ["ok"]


def test_history_contains_only_typed_context(analyst):
    analyst.ask("Compare products 1 and 2")
    assert analyst.history[-1]["intent"] == "analyze"
    assert "queries" in analyst.history[-1]
    assert "rows" not in analyst.history[-1]


def test_off_topic_refused(analyst):
    assert "retail data analysis" in analyst.ask("Write a poem")["text"]


def test_invalid_plan_repairs_once(analyst):
    class BrokenOnce(DemoModel):
        calls = 0

        def plan(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise QueryError("model_response_invalid")
            return super().plan(*args)

    analyst.model = BrokenOnce()
    assert analyst.ask("Compare products 1 and 2")["report_ready"]
    assert analyst.model.calls == 2


def test_plan_without_analysis_cannot_query():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Plan(intent="analyze", queries=[])
