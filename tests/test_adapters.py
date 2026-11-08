from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.api_core import exceptions as gexc

from retail_agent.bq_client import BigQueryRunner
from retail_agent.llm import Gemini
from retail_agent.models import Plan
from retail_agent.safety import QueryError
from retail_agent.sql import compile_query


def fake_job(rows=None):
    job = Mock()
    job.job_id = "test-job"
    job.total_bytes_processed = 200
    job.result.return_value = rows or []
    return job


def test_live_adapter_dry_run_budget_and_query_parameters(spec, events):
    client = Mock()
    job = fake_job([{"product_id": 1, "revenue": 180.0}])
    client.query.side_effect = [SimpleNamespace(total_bytes_processed=100), job]
    runner = BigQueryRunner("project", client=client)
    query = compile_query(spec, [1, 2], "secret")
    result = runner.run(query, spec, "trace", "q1", events)
    dry, actual = client.query.call_args_list
    assert dry.kwargs["job_config"].dry_run is True
    assert dry.kwargs["retry"] is None and actual.kwargs["job_retry"] is None
    assert actual.kwargs["job_config"].maximum_bytes_billed == 1_000_000_000
    assert int(actual.kwargs["job_config"].job_timeout_ms) == 30000
    params = {p.name: p for p in actual.kwargs["job_config"].query_parameters}
    assert params["allowed_products"].values == [1, 2]
    assert params["pseudonym_secret"].value == "secret"
    assert "secret" not in events.path.read_text()
    assert result["rows"][0]["revenue"] == 180.0


def test_byte_budget_prevents_execution(spec, events):
    client = Mock()
    client.query.return_value = SimpleNamespace(total_bytes_processed=1000)
    runner = BigQueryRunner("project", max_bytes=100, client=client)
    with pytest.raises(QueryError, match="byte_budget"):
        runner.run(compile_query(spec, [1], "x"), spec, "trace", "q1", events)
    assert client.query.call_count == 1


def test_timeout_cancels_job(spec, events):
    client = Mock()
    job = fake_job()
    job.result.side_effect = TimeoutError("PII from upstream")
    client.query.side_effect = [SimpleNamespace(total_bytes_processed=100), job]
    runner = BigQueryRunner("project", client=client)
    with pytest.raises(QueryError, match="query_timeout"):
        runner.run(compile_query(spec, [1], "x"), spec, "trace", "q1", events)
    job.cancel.assert_called_once()


def test_duplicate_job_reconciled(spec, events):
    client = Mock()
    job = fake_job([{"revenue": 1}])
    client.query.side_effect = [SimpleNamespace(total_bytes_processed=100), gexc.Conflict("exists")]
    client.get_job.return_value = job
    runner = BigQueryRunner("project", client=client)
    assert runner.run(compile_query(spec, [1], "x"), spec, "trace", "q1", events)["rows"]
    client.get_job.assert_called_once()


def test_ambiguous_submission_reconciles_before_retry(spec, events):
    client = Mock()
    client.query.side_effect = [
        SimpleNamespace(total_bytes_processed=100),
        gexc.ServiceUnavailable("upstream secret"),
    ]
    client.get_job.return_value = fake_job([{"revenue": 1}])
    runner = BigQueryRunner("project", client=client)
    assert runner.run(compile_query(spec, [1], "x"), spec, "trace", "q1", events)["rows"]
    assert client.query.call_count == 2
    client.get_job.assert_called_once()


def test_truncation_uses_one_extra_row(spec, events):
    spec = spec.model_copy(update={"limit": 1})
    client = Mock()
    client.query.side_effect = [
        SimpleNamespace(total_bytes_processed=100),
        fake_job([{"revenue": 2}, {"revenue": 1}]),
    ]
    runner = BigQueryRunner("project", client=client)
    data = runner.run(compile_query(spec, [1], "x"), spec, "trace", "q1", events)
    assert data["truncated"] and len(data["rows"]) == 1


def test_provider_exception_not_exposed(spec, events):
    client = Mock()
    client.query.side_effect = gexc.BadRequest("email='private@example.com'")
    runner = BigQueryRunner("project", client=client)
    with pytest.raises(QueryError) as caught:
        runner.run(compile_query(spec, [1], "x"), spec, "trace", "q1", events)
    assert str(caught.value) == "query_invalid"


def test_gemini_structured_output_and_bounded_calls(events):
    model = Gemini("dummy", "gemini-3.8-flash", events, client=Mock())
    model.client.models.generate_content.return_value = SimpleNamespace(
        text='{"intent":"schema","message":"","queries":[]}', usage_metadata=None
    )
    for _ in range(5):
        assert model._generate(Plan, "system", {}, "trace").intent == "schema"
    with pytest.raises(QueryError, match="budget"):
        model._generate(Plan, "system", {}, "trace")
    config = model.client.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.automatic_continuation is False
    assert model.client.models.generate_content.call_count == 5
