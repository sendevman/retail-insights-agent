# Validation record

Checked on 2026-11-08 using Python 3.12 in a fresh virtual environment installed from requirements-lock.txt.

| Check | Observed result |
| --- | --- |
| `python -m pytest -q` | 53 passed in 1.13 seconds; one third-party pytest deprecation warning |
| `python -m ruff check .` | All checks passed |
| `python -m compileall -q retail_agent` | Passed |
| Synthetic CLI workflow | Captured in examples/demo-transcript.txt |
| Live Gemini and BigQuery execution | Not run; requires evaluator credentials |
| Remote GitHub Actions | Not run; public publication remains pending |

The automated checks cover typed plan validation, SQL scope and parameter binding, direct PII rejection, report ownership and confirmation transactions, retries and error paths, provider adapter contracts, report evidence references, preferences, and synthetic numerical results. They do not establish live model accuracy, production isolation, or universal PII detection.

The offline runner uses Python aggregation over deterministic synthetic facts; it does not execute the compiled BigQuery SQL. Validate generated SQL through BigQuery dry runs and reference-result comparisons before production use.

Commit author requested by the user: Alexsandru Solca <alexandrusolca@outlook.com>. The accompanying Git bundle preserves the local repository history and author identity. No public repository or cloud deployment is claimed by this validation record.
