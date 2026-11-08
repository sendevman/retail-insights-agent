"""Compile a small typed relational language, never execute model-authored SQL."""

import hashlib
import json
from dataclasses import dataclass

from .models import QuerySpec
from .safety import SafeError

DATASET = "bigquery-public-data.thelook_ecommerce"
COMPILER_VERSION = "scoped-item-v1"
DIMENSIONS = {
    "month": "FORMAT_DATE('%Y-%m', event_date)",
    "day": "event_date",
    "product_id": "product_id",
    "category": "category",
    "state": "state",
    "country": "country",
    "customer_key": "customer_key",
    "status": "status",
}
METRICS = {
    "revenue": "ROUND(SUM(IF(status = 'Complete', sale_price, 0)), 2)",
    "units": "COUNTIF(status = 'Complete')",
    "orders": "COUNT(DISTINCT order_id)",
    "customers": "COUNT(DISTINCT customer_key)",
    "avg_order_value": "ROUND(SAFE_DIVIDE(SUM(IF(status = 'Complete', sale_price, 0)), "
    "COUNT(DISTINCT IF(status = 'Complete', order_id, NULL))), 2)",
    "return_rate": "SAFE_DIVIDE(COUNTIF(status = 'Returned'), COUNT(*))",
}


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: dict
    fingerprint: str
    limit: int


def compile_query(spec: QuerySpec, allowed_products: list[int], secret: str) -> CompiledQuery:
    # Authorization is checked independently of all model instructions.
    allowed = set(allowed_products)
    if not allowed or any(type(p) is not int or p <= 0 for p in allowed):
        raise SafeError("No product access is configured.")
    if set(spec.product_ids) - allowed:
        raise SafeError("That request includes products outside your assigned scope.")
    selected = sorted(set(spec.product_ids) if spec.product_ids else allowed)
    columns = [f"{DIMENSIONS[d.value]} AS {d.value}" for d in spec.dimensions]
    columns += [f"{METRICS[m.value]} AS {m.value}" for m in spec.metrics]
    group = "GROUP BY " + ", ".join(d.value for d in spec.dimensions) if spec.dimensions else ""
    direction = "DESC" if spec.descending else "ASC"
    # BQ SHA256 with a high-entropy secret prefix is a pseudonym, not an HMAC;
    # this is documented explicitly and a production tokenization service replaces it.
    sql = f"""WITH scoped_sales AS (
  SELECT oi.order_id, oi.product_id, oi.sale_price, oi.status,
         DATE(oi.created_at, 'UTC') AS event_date,
         p.category, u.state, u.country,
         TO_HEX(SHA256(CONCAT(@pseudonym_secret, ':', CAST(u.id AS STRING)))) AS customer_key
  FROM `{DATASET}.order_items` AS oi
  JOIN `{DATASET}.orders` AS o ON o.order_id = oi.order_id AND o.user_id = oi.user_id
  JOIN `{DATASET}.products` AS p ON p.id = oi.product_id
  JOIN `{DATASET}.users` AS u ON u.id = oi.user_id
  WHERE oi.product_id IN UNNEST(@allowed_products)
    AND oi.product_id IN UNNEST(@selected_products)
    AND oi.created_at >= TIMESTAMP(@start_date)
    AND oi.created_at < TIMESTAMP(@end_date)
    AND (ARRAY_LENGTH(@states) = 0 OR u.state IN UNNEST(@states))
    AND (ARRAY_LENGTH(@countries) = 0 OR u.country IN UNNEST(@countries))
)
SELECT {", ".join(columns)}, COUNT(*) AS sample_items
FROM scoped_sales
{group}
HAVING COUNT(*) > 0
ORDER BY {spec.order_by} {direction}
LIMIT @row_limit"""
    parameters = {
        "allowed_products": sorted(allowed),
        "selected_products": selected,
        "pseudonym_secret": secret,
        "start_date": spec.start_date,
        "end_date": spec.end_date,
        "states": spec.states,
        "countries": spec.countries,
        "row_limit": spec.limit + 1,
    }
    # Include hashed filter values so repaired plans never reuse a different query's job.
    identity = {k: v for k, v in parameters.items() if k != "pseudonym_secret"}
    fingerprint = hashlib.sha256(
        (sql + json.dumps(identity, sort_keys=True, default=str)).encode()
    ).hexdigest()[:16]
    return CompiledQuery(sql, parameters, fingerprint, spec.limit)
