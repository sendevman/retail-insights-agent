# Evaluation, observability and operations

## Acceptance strategy

Separate four questions: did the agent understand the intent, calculate the correct facts,
interpret them honestly, and let the executive complete the task safely? A fluent paragraph
is not evidence of accuracy. Exact SQL string matching is also insufficient: equivalent
queries can be valid, and syntactically valid SQL can answer the wrong business question.

Use a frozen, authorized test snapshot with seeded edge cases. Analysts create held-out
question/expected-scope/expected-result/report-rubric cases. Keep near-duplicates and all
paraphrases of an example in the same split, so retrieved Golden Trios do not leak test
answers. Run a temporal holdout to catch schema/seasonality drift. Track model, prompt,
compiler, metric, persona, retrieval and snapshot versions with every evaluation.

Proposed predeployment benchmark:

| Set | Minimum initial coverage | What is checked |
| --- | --- | --- |
| Core analysis | 40 analyst-authored cases | Monthly/year-to-date sales, product/state comparison, top opaque customers, zeros/nulls and partial periods |
| Follow-ups | 20 multi-turn sessions | Correct dates/products survive ellipsis; clarification resets scope intentionally |
| Reports | 15 quarterly/why questions | Intent coverage, supported comparisons, correct limitations, useful proposed actions |
| Safety | 60 adversarial cases | PII, SQL/prompt injection, entitlement bypass, malicious Golden content, export attempts |
| Destructive UX | 20 scenarios | Selection precision, owner isolation, confirmation expiry/replay/change/concurrency |
| Reliability | 20 injected failure cases | Timeouts, 429/503, malformed model output, zero rows, one-query failure, storage outage |

These are proposed counts for a client test corpus, not a claim that this benchmark already
exists. The automated source tests are the implemented validation; results are recorded in
`VALIDATION.md`.

## Intent, evidence and report evaluation

1. **Intent contract:** check required products, dates, grain, metric definitions, comparison
   baseline and requested deliverable. Clarification is correct when churn/fiscal-year/identity
   definitions are missing; confidently inventing a definition is a failure.
2. **Execution correctness:** execute reference and generated plans on the same snapshot;
   compare normalized result sets with appropriate ordering/rounding tolerances. Cover
   boundary dates, mixed-status orders, products from different scopes in the same order,
   no completed items, duplicate dimensions, null state, and invalid foreign keys.
3. **Evidence consistency:** every stated numeric fact resolves to a result cell or a logged,
   deterministic derived calculation. Compare denominators and units. Reference existence
   alone does not prove that a sentence is true. The prototype checks references only;
   production adds a structured claim ledger and calculator-based numeric validation.
4. **Interpretation:** analyst rubric scores relevance, completeness, treatment of uncertainty,
   distinction between observation/hypothesis, and action usefulness. Use a Gemini judge for
   triage, calibrated against at least two human reviewers; do not make it the sole release gate.
5. **Grounding:** measure unsupported claim rate, cited-evidence precision and intent coverage.
   Test whether removing a Golden record changes definitions unexpectedly. Golden SQL
   is guidance, never a substitute for executing current authorized data.

For production, require a report to pass policy/DLP and numeric evidence validation before
publishing. Unsupported claims are removed or downgraded to hypotheses; if insufficient
support remains, return evidence and limitations or route to analyst review. High-impact
business decisions still belong to the executive, not an automated causal conclusion.

Proposed release gates, to agree with the client:

- Zero observed cross-user/product/report disclosure and unconfirmed-deletion failures in
  the mandatory security suite; any one blocks release. Passing a suite is not proof against
  all possible attacks.
- At least 95% correct semantic results on the defined core benchmark and at least 90%
  complete intent coverage. Report the denominator and errors, not just one aggregate score.
- Zero fabricated direct PII in seeded tests; all sensitive-canary strings blocked from
  model context, rendered outputs, saved reports and normal logs.
- At least 90% evidence-supported factual claims by human review and no unsupported
  critical numerical/causal claim. Failed critical claims block release even if average passes.
- Reliability and cost gates measured on the target deployment: proposed p95 answer time
  under 45 seconds for normal two-query requests, not a promise of current API performance.

## UX evaluation

Recruit 5–8 representative nontechnical executives for moderated tests with realistic tasks:
compare two products, refine the period, ask “why,” generate a quarterly report, retrieve it,
delete selected own reports, and recover from no-data/service failure. Provide no command
training beyond the help surface when testing discoverability.

Measure task completion, time to first useful evidence, clarification count, incorrect scope
acceptance, ability to identify a hypothesis, report usefulness, deletion comprehension and
abandonment. Use think-aloud interviews and a short usability questionnaire (e.g. SUS).
Count whether users understood **exactly which reports would be deleted**, not merely
whether they could press confirm. Test color-independent errors, keyboard access, screen
reader announcements and long-report pagination in the future web UI.

The production chat should stream plain-language status (“Checking your product access,”
“Comparing the two periods”), show dates/filters in a readable summary, and preserve the
conversation after recoverable errors. The prototype prints a synchronous CLI response;
it does not implement the web progress or accessibility experience.

## Observability

`runtime/events.jsonl` contains allowlisted structured events. Each answer prints a trace
ID. Search that trace to correlate plan stages, context version/Golden IDs, query IDs,
SQL+filter hashes, BQ job IDs, row/byte counts, model token use, safe error codes and
turn duration. Prompts, query parameter values, results, credentials and raw exceptions
are not logged. Saved reports contain only rendered safe analysis; they are not debugging logs.

```bash
# Replace the trace value with one printed by the CLI.
rg 'TRACE_ID' runtime/events.jsonl
```

Use `--runtime PATH` to isolate a test run's local state. Do not upload the runtime folder
to a public repository. The prototype has no separate telemetry backend or dashboard.

Proposed production metrics:

| Layer | Metrics | Diagnostic value |
| --- | --- | --- |
| Intent/agent | Answer success, clarification/refusal, incomplete intent, analyst correction rate | Finds bad routing or missing semantic capabilities |
| Retrieval | Eligible candidate count, recall@k on labeled cases, rerank time, Golden coverage/version mismatch | Separates knowledge failures from SQL failures |
| Planning | Invalid-plan rate, repair success, query count/turn, scope rejection rate | Reveals model/schema mismatch or attacks |
| Warehouse | Dry-run failure, syntax failure, empty result, bytes/turn, job latency, timeout/cancel success | Distinguishes data absence, cost and platform failure |
| Grounding | Invalid references, failed numeric checks, unsupported critical claims, DLP blocks | Measures factual/privacy safeguards |
| Destructive | Preview/confirm/cancel/expire counts, version conflicts, ownership denials, outbox cleanup lag | Confirms oversight is being used and detects anomalies |
| Providers | 429/5xx/timeout rate, circuit-breaker state, token usage | Capacity and service health |
| Cost | Input/output tokens and reserved/actual BQ bytes by model/version/tenant | Budget control and prompt regressions |
| UX | First useful response time, total p50/p95, abandon/retry rate, user feedback | Identifies a technically correct but frustrating experience |

Do not put customer/user IDs, report titles, message text or raw product lists in metric
labels. Use low-cardinality model/version/stage/outcome labels; keep restricted correlation
identifiers in traces. Cost is calculated from versioned price configuration, not guessed
from an obsolete fixed rate. Provider-reported hidden/cached/thinking usage is tracked when
available; the prototype records only prompt/candidate token fields.

Production OpenTelemetry spans: `chat.turn`, `auth.resolve`, `golden.retrieve`, `plan.generate`,
`sql.validate`, `bq.dry_run`, `bq.execute`, `report.generate`, `report.check`, `report.save`,
`delete.preview`, `delete.confirm`. Propagate trace/operation IDs through HTTP and Cloud Tasks.
Link worker spans after resume. Record parent span and attempt so a retry is distinguishable
from a separate user request. Never log hidden model chain-of-thought.

For deep dives into **message correspondence**, store redacted user/assistant messages,
tool request/response envelopes, typed plans and evidence pointers in a separate encrypted,
short-retention trace store only under the agreed client policy. Give support metadata access
by default; require audited elevated access for payload inspection. Identify messages by
ID/parent/sequence and record the exact model/persona/metric/Golden versions. Exclude secrets
and raw PII; DLP failure prevents storing the payload. Deterministic replay uses a safe frozen
snapshot and mocked external side effects. The prototype deliberately has metadata-only
traces and cannot reconstruct a full verbatim conversation after exit.

## Alerts and incident playbooks

Initial thresholds are hypotheses to tune after a baseline:

- Any confirmed unauthorized data disclosure or deletion without a valid confirmation:
  page security/on-call, suspend affected capability, preserve restricted audit evidence,
  revoke compromised identities/tokens and involve the data owner.
- Over 5% failed turns or provider 429/5xx across 15 minutes with a minimum of 20 requests:
  page service owner, inspect upstream health, reduce concurrency and activate circuit breaker.
- p95 normal-query latency above 45 seconds over 15 minutes: inspect BQ jobs, provider
  latency and queue wait; do not blindly increase retries.
- Repeated budget exhaustion or daily budget 80% used: notify platform owner and apply
  configured reservations/limits. Avoid hidden expensive fallbacks.
- Persona/Golden release increases invalid-plan or unsupported-claim rate beyond the
  evaluation baseline: revert the active version pointer and compare traces by version.

To debug one answer: start at its trace ID, confirm authorization/version context, inspect
retrieved IDs and typed plan under approved access, locate BQ job and safe evidence snapshot,
then inspect report validation and rendering. Classify the failure as intent, knowledge,
semantic SQL, data quality, infrastructure, report grounding or UX; assign a concrete regression
case. Do not use production PII in a public issue or test fixture.

## Deployment and recovery

Use infrastructure as code for private networking, Cloud SQL HA/backups/PITR, Cloud Storage
retention/lifecycle, Cloud Run identities and least-privilege roles. Keep source curation,
query execution, report mutation, knowledge publication and persona editing in separate
roles. Production deployments require actual IAM/RLS integration tests on separate identities;
application mocks cannot establish warehouse isolation.

CI runs offline tests and lint. A protected staging gate runs authenticated BigQuery/Gemini
smoke tests on a fixed snapshot with production-like IAM and safe canaries. Release pinned
artifacts, apply backward-compatible migrations, canary to an approved pilot group, compare
quality/cost/latency, then expand. Roll back code/model configuration and active persona/Golden
versions independently. Never roll back an audit or resurrect a deleted report accidentally.

Proposed initial RPO is 15 minutes and RTO is 4 hours for metadata/reports, subject to a
tested backup/restore plan and business agreement. Test object/database consistency on restore
and reapply deletion tombstones/legal holds. Source data recovery is owned by the data platform.

