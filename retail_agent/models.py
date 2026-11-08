from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dimension(str, Enum):
    month = "month"
    day = "day"
    product_id = "product_id"
    category = "category"
    state = "state"
    country = "country"
    customer_key = "customer_key"
    status = "status"


class Metric(str, Enum):
    revenue = "revenue"
    units = "units"
    orders = "orders"
    customers = "customers"
    avg_order_value = "avg_order_value"
    return_rate = "return_rate"


class QuerySpec(StrictModel):
    label: str = Field(min_length=1, max_length=100)
    dimensions: list[Dimension] = Field(default_factory=list, max_length=3)
    metrics: list[Metric] = Field(min_length=1, max_length=6)
    start_date: date
    end_date: date
    product_ids: list[int] = Field(default_factory=list, max_length=100)
    states: list[str] = Field(default_factory=list, max_length=10)
    countries: list[str] = Field(default_factory=list, max_length=10)
    order_by: str = Field(default="revenue", max_length=30)
    descending: bool = True
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def coherent(self):
        names = [x.value for x in self.dimensions + self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate dimensions or metrics")
        if self.start_date >= self.end_date:
            raise ValueError("Use a nonempty half-open date range")
        if (self.end_date - self.start_date).days > 3660:
            raise ValueError("Date range exceeds ten years")
        if self.order_by not in names:
            raise ValueError("Sort by a selected dimension or metric")
        if any(p <= 0 for p in self.product_ids):
            raise ValueError("Product IDs must be positive")
        if any(len(x) > 80 for x in self.states + self.countries):
            raise ValueError("Filter too long")
        return self


class Plan(StrictModel):
    intent: Literal["analyze", "schema", "clarify", "refuse"]
    message: str = Field(default="", max_length=1200)
    queries: list[QuerySpec] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def has_work(self):
        if self.intent == "analyze" and not self.queries:
            raise ValueError("Analysis requires a query")
        if self.intent != "analyze" and self.queries:
            raise ValueError("Only analysis may run queries")
        return self


class Finding(StrictModel):
    text: str = Field(min_length=1, max_length=650)
    evidence: list[str] = Field(min_length=1, max_length=5)
    kind: Literal["observation", "hypothesis"]


class Report(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    findings: list[Finding] = Field(default_factory=list, max_length=6)
    actions: list[str] = Field(default_factory=list, max_length=5)
    limitations: list[str] = Field(default_factory=list, max_length=5)
