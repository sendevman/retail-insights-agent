"""Hardened replacement for the supplied DataFrame-based BigQueryRunner.

Only CompiledQuery values produced by our compiler enter this adapter.
"""

import time
from concurrent.futures import TimeoutError as FutureTimeout
from decimal import Decimal

from google.api_core import exceptions as gexc
from google.cloud import bigquery

from .safety import QueryError


class BigQueryRunner:
    def __init__(self, project_id, location="US", max_bytes=1_000_000_000, client=None):
        self.client = client or bigquery.Client(project=project_id)
        self.location = location
        self.max_bytes = max_bytes

    @staticmethod
    def _parameters(values):
        arrays = {
            "allowed_products": "INT64",
            "selected_products": "INT64",
            "states": "STRING",
            "countries": "STRING",
        }
        types = {
            "start_date": "DATE",
            "end_date": "DATE",
            "row_limit": "INT64",
            "pseudonym_secret": "STRING",
        }
        return [
            bigquery.ArrayQueryParameter(k, arrays[k], v)
            if k in arrays
            else bigquery.ScalarQueryParameter(k, types[k], v)
            for k, v in values.items()
        ]

    def run(self, query, spec, trace_id, query_id, events):
        parameters = self._parameters(query.parameters)
        job = None
        started = time.monotonic()
        try:
            dry_config = bigquery.QueryJobConfig(
                dry_run=True,
                use_query_cache=False,
                use_legacy_sql=False,
                query_parameters=parameters,
                maximum_bytes_billed=self.max_bytes,
            )
            dry = self.client.query(
                query.sql,
                job_config=dry_config,
                location=self.location,
                retry=None,
                timeout=15,
                job_retry=None,
            )
            if (dry.total_bytes_processed or 0) > self.max_bytes:
                raise QueryError("byte_budget_exceeded")
            config = bigquery.QueryJobConfig(
                use_legacy_sql=False,
                query_parameters=parameters,
                maximum_bytes_billed=self.max_bytes,
                labels={"application": "retail-analyst", "trace": trace_id[:32]},
                job_timeout_ms=30_000,
            )
            # Explicit ID prevents duplicate execution after an uncertain network response.
            job_id = f"retail_{trace_id}_{query_id}_{query.fingerprint}"
            try:
                job = self.client.query(
                    query.sql,
                    job_config=config,
                    location=self.location,
                    job_id=job_id,
                    retry=None,
                    timeout=15,
                    job_retry=None,
                )
            except gexc.Conflict:
                job = self.client.get_job(job_id, location=self.location, retry=None, timeout=10)
            except (gexc.ServiceUnavailable, gexc.DeadlineExceeded):
                # Reconcile an ambiguous submission before any retry with the same ID.
                try:
                    job = self.client.get_job(
                        job_id, location=self.location, retry=None, timeout=10
                    )
                except gexc.NotFound:
                    raise QueryError("warehouse_unavailable", retryable=True) from None
            rows = list(
                job.result(timeout=30, max_results=query.limit + 1, retry=None, job_retry=None)
            )
            truncated = len(rows) > query.limit
            safe = [
                {k: float(v) if isinstance(v, Decimal) else v for k, v in dict(r).items()}
                for r in rows[: query.limit]
            ]
            events.emit(
                trace_id,
                "query_complete",
                query_id=query_id,
                job_id=job.job_id,
                sql_hash=query.fingerprint,
                rows=len(safe),
                bytes_processed=job.total_bytes_processed or 0,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
            return {
                "rows": safe,
                "truncated": truncated,
                "bytes_processed": job.total_bytes_processed or 0,
                "job_id": job.job_id,
            }
        except (FutureTimeout, TimeoutError, gexc.DeadlineExceeded):
            if job is not None:
                try:
                    job.cancel(retry=None, timeout=5)
                except Exception:
                    pass
            raise QueryError("query_timeout") from None
        except gexc.BadRequest:
            raise QueryError("query_invalid") from None
        except gexc.Forbidden:
            raise QueryError("warehouse_access_or_quota_denied") from None
        except (gexc.TooManyRequests, gexc.ServiceUnavailable, gexc.InternalServerError):
            raise QueryError("warehouse_unavailable", retryable=True) from None
        except gexc.GoogleAPICallError:
            raise QueryError("warehouse_error") from None
