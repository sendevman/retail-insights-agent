# Retail Analyst

A CLI data analysis assistant for retail executives, backed by **Gemini**, **BigQuery**,
**LangGraph** and **Pydantic**. It answers sales questions, handles follow-ups, compares
products/customers/regions, and creates saved reports with evidence and proposed actions.

The key boundary: **Gemini selects a typed analysis plan; Python compiles and executes
the SQL.** A prompt cannot remove the product predicate, select a PII column, run arbitrary
SQL, or confirm a destructive action. The generated SQL is dynamic, but its grammar,
metrics, joins and authorization are controlled by code.

## Start here

1. [High-level design and architecture](docs/ARCHITECTURE.md)
2. [Technical explanation and requirement mapping](docs/TECHNICAL_DESIGN.md)
3. [Evaluation and operational runbook](docs/EVALUATION_AND_OPERATIONS.md)
4. [Demo transcript](examples/demo-transcript.txt)
5. [Source references](docs/RESOURCES.md)

## Quick start: offline demo

Requires Python 3.11 or 3.12. Run from the repository root.

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m retail_agent --demo --user alice
```

No API key, billing project, or cloud account is needed for this mode. It deliberately
uses a deterministic example planner and synthetic Q1 2025 fixtures, aggregated in Python.
**Demo results are not BigQuery results and do not evaluate Gemini's analytical quality.**

Try:

```text
What data is available?
Compare products 1 and 2
Show top customers by spend
Create a report for Q1 2025 with action items for Q2
/reports
/prefer bullets
Delete all the reports we made in this conversation
/cancel
Show customer emails
Compare products 1 and 11
/quit
```

The demo's natural-language parser is intentionally narrow. Live mode uses Gemini to
interpret arbitrary questions supported by the typed analysis language.

## Live mode: Gemini + BigQuery

1. Create a Google Cloud billing/query project; enable the BigQuery API. Authenticate
   using [Application Default Credentials](https://cloud.google.com/docs/authentication/provide-credentials-adc).
   For a development machine with the Google Cloud CLI installed:

   ```bash
   gcloud auth application-default login
   gcloud auth application-default set-quota-project YOUR_PROJECT_ID
   gcloud services enable bigquery.googleapis.com --project YOUR_PROJECT_ID
   ```

2. The query identity needs `roles/bigquery.jobUser` on the billing project and read access
   to the public dataset. Grant only the necessary ADC service usage permission if the
   quota-project command requests it. No database write role is needed. See
   [Google's Python quickstart](https://cloud.google.com/bigquery/docs/quickstarts/quickstart-client-libraries).

3. Obtain a Gemini key in [Google AI Studio](https://aistudio.google.com/). Copy
   `.env.example` to `.env`, then set:

   ```dotenv
   GEMINI_API_KEY=YOUR_KEY
   GEMINI_MODEL=gemini-3.8-flash
   GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
   BIGQUERY_LOCATION=US
   MAX_BYTES_BILLED=1000000000
   PSEUDONYM_SECRET=YOUR_STABLE_RANDOM_SECRET_AT_LEAST_32_CHARACTERS
   ```

   Generate a secret once with
   `python -c "import secrets; print(secrets.token_hex(32))"`. Keep it stable and private.
   The model name is configurable; choose an available structured-output-capable Gemini
   model for your account. A missing/unsupported model produces a safe error; the program
   does not silently switch providers. API quotas and pricing depend on your account;
   do not assume the free tier will cover every workload.

4. Review `config/access.json`. Alice is assigned products 1–10; Bob is assigned 11–20.
   These are **example assignments**, since TheLook has no organization entitlement table.
   Change the assignments as the trusted operator, outside chat.

5. Run:

   ```bash
   python -m retail_agent --user alice
   ```

Example live questions:

```text
Compare monthly completed revenue for products 1 and 2 during Q1 2025.
How do their completed units and average order values compare?
Compare California and Texas during 2025, including spend and customer counts.
Show the top 10 customers by completed spend in 2025 using opaque customer keys.
Create a report for Q1 2025 including insights and proposed actions for Q2.
What can this database tell us about churn?
```

Use product IDs in this prototype; product name resolution is a production extension.
The tiny sample entitlement may have sparse regional sales: a no-data response can be correct.

## Saved reports and confirmation

Requests to create/generate/make/save a report automatically save a successful analysis.
Use `/save` to save another completed answer. `/reports` lists your reports and `/show ID`
reads one. Reports remain available across restarts. `/prefer tables` or `/prefer bullets`
stores an explicit preference per user.

```text
Delete all reports mentioning Client X
Delete all the reports we made in this conversation
```

Both commands **only preview**. The CLI lists exact IDs/titles and issues `/confirm TOKEN`.
The token expires after 120 seconds; it is bound to the user, conversation and selected
report versions, and can be used once. Saying “yes” does not confirm. `/cancel` or another
chat message cancels the preview. New matching reports are not added to an existing preview.
Deletion hides the selected reports through a transactional soft delete; physical retention
and administrative recovery are separate production policies.

`--session demo-review` keeps a stable conversation identifier for saved-report selection:

```bash
python -m retail_agent --demo --user alice --session demo-review
```

Conversation **analytical context** lasts only for the running process, up to four typed plans.
Resuming a session identifier restores report scope, not chat history.

## Tests and reproducibility

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
python -m compileall -q retail_agent
```

The tests use no credentials. They cover semantic SQL compilation, authorization,
parameterization, PII blocking, deletion isolation/expiry/replay/version checks, retry
budgets, empty results, failed narratives, evidence references, query timeout/cancellation,
dry-run budgets and ambiguous BigQuery submissions. `requirements-lock.txt` records the
exact tested dependency closure; it can replace the requirements install for reproduction
on Python 3.12. CI additionally targets Python 3.11. That CI matrix is provided, not claimed
to have been run remotely.

## Security and scope of this submission

- Live BigQuery/Gemini calls require your own credentials; they were **not executed** in the
  build environment. The adapters were tested with mocked provider responses.
- This is a working prototype with a production design, not a deployed production service.
- `--user` is a **trusted local identity selector**, not authentication. Anyone who controls
  the local machine/configuration/database can impersonate test identities. Multi-user
  deployment requires the OIDC and warehouse IAM controls in the HLD.
- Direct PII columns never enter model-visible query results. Output redaction is an extra
  check, not a universal named-entity detector. Opaque customer keys remain pseudonymous
  personal data, not anonymous data. If the client's PII policy disallows pseudonyms, disable
  customer-level reporting and retain aggregate-only reports.
- Facts come from compiled SQL. Narrative evidence references are checked; semantic truth,
  all arithmetic, causal claims and complete intent coverage still require evaluation/human
  review. The prototype labels AI interpretation and hypotheses rather than asserting a
  fully verified analyst judgment.
- No automatic model training, external email, web access, charts, cohort churn calculations,
  production DLP, SSO, or admin portal is implemented. Their designs are documented.
- Golden records are **synthetic illustrative analyst examples**. The example SQL is retrieved
  as knowledge, never executed; actual expert sign-off is needed before production use.

## Publishing the submission

Create a new **public** repository on your account and run these commands from this folder:

```bash
git init
git config user.name "Alexsandru Solca"
git config user.email "alexandrusolca@outlook.com"
git add .
git commit -m "Build retail analysis assistant prototype and production design"
git branch -M main
git remote add origin https://github.com/sendevman/retail-insights-agent.git
git push -u origin main
```

Share the repository URL with the assessors. Do not include `.env`, ADC credentials,
`runtime/`, real user conversations, or customer reports. The archive intentionally excludes
them. There is no fabricated deployment link, live result or author experience claim.

The downloadable submission also includes `retail-insights-agent.bundle`. To preserve the
prepared commit and author identity, clone that bundle instead of reinitializing:

```bash
git clone retail-insights-agent.bundle retail-insights-agent
cd retail-insights-agent
git remote remove origin
gh repo create sendevman/retail-insights-agent --public --source=. --remote=origin --push
```

The final command requires GitHub CLI authentication as `sendevman` and creates a public
repository. These commands are a publishing route; they do not indicate that publication
has already occurred.

