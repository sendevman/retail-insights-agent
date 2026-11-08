# Resources and implementation notes

Official references consulted for the implementation and production design:

- [Gemini model catalog](https://ai.google.dev/gemini-api/docs/models) and
  [Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash):
  configurable structured-output model choice; check availability in the evaluator's account.
- [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output):
  Pydantic/JSON response schemas. A valid schema does not prove semantic correctness.
- [Google GenAI Python SDK](https://github.com/googleapis/python-genai):
  native client, HTTP timeout/retry configuration and automatic continuation controls.
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview) and
  [StateGraph reference](https://reference.langchain.com/python/langgraph/graph/state/StateGraph):
  typed explicit workflows and branches, independent of a generic tool-calling agent.
- [BigQuery Python quickstart](https://cloud.google.com/bigquery/docs/quickstarts/quickstart-client-libraries)
  and [ADC setup](https://cloud.google.com/docs/authentication/provide-credentials-adc):
  credentials for public-dataset queries billed to your own project.
- [BigQuery running queries / dry runs](https://cloud.google.com/bigquery/docs/running-queries)
  and [query cost controls](https://cloud.google.com/bigquery/docs/best-practices-costs):
  dry-run validation and maximum bytes billed. LIMIT alone is not a scan-cost control.
- [BigQuery parameterized queries](https://cloud.google.com/bigquery/docs/parameterized-queries):
  bind values; identifiers must still come from an allowlist/compiler.
- [BigQuery row-level security](https://cloud.google.com/bigquery/docs/row-level-security-intro),
  [authorized views](https://cloud.google.com/bigquery/docs/authorized-views) and
  [RLS security best practices](https://cloud.google.com/bigquery/docs/best-practices-row-level-security):
  warehouse enforcement and caveats for shared principals/inference.
- [Google Cloud retail data codelab](https://codelabs.developers.google.com/codelabs/bigquery-measures):
  TheLook item-grain data context. The live dry run is authoritative for actual schema compatibility.
- [Sensitive Data Protection](https://cloud.google.com/sensitive-data-protection/docs):
  proposed production inspection/tokenization; not implemented by the local regex masker.

## Supplied BigQuery client

The attached `bq_client.py` used `google.cloud.bigquery.Client`, executed arbitrary SQL,
returned a pandas DataFrame, exposed full table schemas, and logged raw exception messages.
It provided the basic integration pattern. The replacement `retail_agent/bq_client.py`
keeps the narrow execution-adapter role and removes the DataFrame/pandas requirement.

Changes: typed compiler inputs, fixed approved tables/columns, product predicates, bound
parameters, dry-run estimates, execution byte cap, deadlines/cancellation, bounded rows,
idempotent job IDs, safe errors and structured telemetry. Schema questions are answered
from a curated safe catalog rather than revealing all raw columns to the LLM.

No real credentials, raw source records or proprietary analyst Trios are included.
TheLook is a public dataset: the prototype controls access **through this assistant**;
it cannot prevent someone from independently querying the public source outside the app.
Production isolation assumes the client's actual dataset is private.

