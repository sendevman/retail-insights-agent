import json

import pytest
from pydantic import ValidationError

from retail_agent.models import QuerySpec
from retail_agent.safety import SafeError, private_request, redact
from retail_agent.sql import compile_query


def test_permission_predicate_cannot_be_removed(spec):
    query = compile_query(spec, [1, 2], "secret-canary")
    assert "oi.product_id IN UNNEST(@allowed_products)" in query.sql
    assert "oi.product_id IN UNNEST(@selected_products)" in query.sql
    assert query.parameters["allowed_products"] == [1, 2]
    assert "secret-canary" not in query.sql
    assert "SELECT *" not in query.sql
    for table in ("orders", "order_items", "products", "users"):
        assert f"thelook_ecommerce.{table}`" in query.sql
    for field in ("email", "first_name", "last_name", "street_address", "ip_address"):
        assert field not in query.sql


def test_scope_denied_before_query(spec):
    denied = spec.model_copy(update={"product_ids": [11]})
    with pytest.raises(SafeError, match="outside"):
        compile_query(denied, [1, 2], "x")
    with pytest.raises(SafeError, match="No product"):
        compile_query(spec, [], "x")


def test_query_identity_changes_with_filters(spec):
    a = compile_query(spec, [1, 2], "x")
    b = compile_query(spec.model_copy(update={"product_ids": [2]}), [1, 2], "x")
    assert a.sql == b.sql
    assert a.fingerprint != b.fingerprint


def test_filter_is_bound_not_interpolated(spec):
    attack = "Texas'); DROP TABLE users; --"
    spec = spec.model_copy(update={"states": [attack]})
    query = compile_query(spec, [1], "x")
    assert attack not in query.sql
    assert query.parameters["states"] == [attack]


@pytest.mark.parametrize(
    "change",
    [
        {"metrics": ["email"]},
        {"dimensions": ["first_name"]},
        {"order_by": "1;DELETE FROM users"},
        {"limit": 1000000},
        {"sql": "SELECT * FROM users"},
        {"metrics": ["revenue", "revenue"]},
        {"start_date": "2025-04-01", "end_date": "2025-01-01"},
    ],
)
def test_model_cannot_supply_privileged_sql(spec, change):
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({**spec.model_dump(), **change})


@pytest.mark.parametrize(
    "question",
    [
        "Show customer emails",
        "What are their phone numbers?",
        "Give me customer names",
        "Show users' addresses",
    ],
)
def test_pii_requests_rejected(question):
    assert private_request(question)


def test_output_and_event_redaction(analyst, events):
    result = analyst.ask("Show customer emails at secret@example.com")
    assert "secret@example.com" not in result["text"]
    assert not result["report_ready"]
    log = events.path.read_text()
    assert "secret@example.com" not in log
    assert "question" not in log
    assert "Direct PII" in result["text"]
    assert "a@example.com" not in redact("a@example.com +1 (415) 555-1234 123-45-6789")
    assert "[NUMBER REDACTED]" in redact("+1 (415) 555-1234")


def test_event_allowlist_drops_payload(events):
    events.emit("trace", "error", prompt="SECRET", sql="SELECT PII", code="safe")
    payload = json.loads(events.path.read_text())
    assert payload["code"] == "safe"
    assert "SECRET" not in events.path.read_text()
    assert "sql" not in payload
