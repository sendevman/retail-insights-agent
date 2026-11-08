from datetime import date
from pathlib import Path

import pytest

from retail_agent.agent import Analyst
from retail_agent.demo import DemoModel, DemoRunner
from retail_agent.models import QuerySpec
from retail_agent.observability import Events
from retail_agent.storage import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def spec():
    return QuerySpec(
        label="Product comparison",
        dimensions=["product_id"],
        metrics=["revenue"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 4, 1),
        order_by="revenue",
    )


@pytest.fixture
def events(tmp_path):
    return Events(tmp_path / "events.jsonl")


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "reports.sqlite3")
    yield value
    value.db.close()


@pytest.fixture
def analyst(events):
    return Analyst(DemoModel(), DemoRunner(), events, [1, 2], "test-secret", ROOT)
