# Supply Chain Order Exception Agent

Personal study project and proof of concept for automating supply-chain order-exception triage.

The project explores how a multi-step decision-support agent can reduce the repetitive work involved in reviewing ERP orders. It includes both a deterministic baseline and an optional Hugging Face LLM tool-calling workflow. The open-weight model investigates orders through bounded read-only tools while deterministic rules retain control of classifications, severity, policy, and approval requirements.

**[Try the live application](https://supply-chain-order-exception-agent.vercel.app/agent/llm-triage)** · **[Read the user guide](docs/user-guide.md)** · **[Explore the system design](docs/system-design.md)**

## Try it as a user

No installation or credential is required for the deterministic workflow:

1. Open the [live application](https://supply-chain-order-exception-agent.vercel.app/agent/llm-triage).
2. Keep `PO-10004` to study a late shipment, or use `PO-10005` for an inventory shortage.
3. Select **Run deterministic baseline** to see the exception, severity, evidence, recommended action, approval boundary, and decision ID.
4. Open **Metrics**, **Traces**, **Audit**, or **Mock ERP** to inspect the operational evidence behind the workflow.
5. Optionally enter a fine-grained Hugging Face token with Inference Providers permission and select **Run Hugging Face agent** to compare the guarded LLM investigation.

The demo is read-only. It never updates an ERP order, contacts a supplier, or executes the recommendation. The included records are synthetic and contain no company data.

## What this study demonstrates

This is an end-to-end engineering study rather than only an LLM prompt demo. It examines:

- How to separate ERP ingestion, deterministic classification, policy, LLM reasoning, and human approval.
- Where an LLM adds value: gathering context, selecting bounded tools, explaining evidence, and proposing a policy-allowed next action.
- Where an LLM should not have authority: changing the detected exception, lowering severity, bypassing approval, or executing transactions.
- How structured outputs and post-model guardrails turn probabilistic output into a controlled proposal.
- How logs, metrics, distributed traces, and decision audits make an agent observable and reviewable.
- How synthetic ground truth, trajectory tests, and failure injection support repeatable evaluation before real ERP data is available.

## Project goals

- Model a realistic order-exception workflow without using confidential company data.
- Keep classification rules explainable and independently testable.
- Separate data ingestion, exception classification, and recommendation logic.
- Measure triage quality against labeled synthetic scenarios.
- Provide a safe foundation for later ERP, dashboard, and machine-learning integrations.

## Architecture

```text
Synthetic ERP data / future ERP connector
                 |
                 v
          Data validation
                 |
                 v
        Exception detection
                 |
                 v
     Severity and recommendation
                 |
                 v
       Human approval decision

Every stage emits correlated traces, metrics, structured logs, and decision audit events.
```

The deterministic baseline demonstrates a three-stage workflow:

1. Pull ERP-like order data from a mock HTTP API.
2. Classify late shipment, inventory, quantity, price, and data-quality exceptions.
3. Recommend a next action and flag decisions that require human approval.

The synthetic dataset is deterministic and includes a ground-truth `expected_exception` column. It contains no real company or customer data.

## Demonstrated exception types

| Exception | Detection signal | Example recommendation |
| --- | --- | --- |
| Late shipment | Current ETA exceeds the promised date | Request a supplier recovery date and evaluate expediting |
| Inventory shortage | Available inventory is below the required quantity | Check another plant and propose a stock transfer |
| Quantity shortfall | Confirmed quantity is below ordered quantity | Split the order or evaluate an alternate supplier |
| Price mismatch | Price differs from contract by more than 2% | Place the order on commercial hold and validate pricing |
| Data quality | A required ERP field is missing or invalid | Correct the record before operational processing |

## Real-world value and beneficiaries

| Beneficiary | Current operational burden | How this workflow could help |
| --- | --- | --- |
| Supply and order planners | Repeatedly inspect dates, quantities, inventory, and notes across many orders | Prioritize a queue, summarize evidence, and propose the next policy-approved step |
| Procurement and supplier managers | Chase late confirmations and recovery dates across suppliers | Identify supplier-related risks and prepare a consistent recovery request |
| Inventory and fulfillment teams | Search plants or locations manually when stock is short | Surface alternate inventory context before a planner decides on a transfer |
| Customer service and account teams | Discover delivery risk late and reconstruct the reason from multiple systems | Provide an explainable exception summary and escalation status |
| Supply-chain control-tower leaders | Lack consistent severity, decision lineage, and triage performance measures | Standardize policy, monitor workload and latency, and audit decisions |
| Platform, data, and AI engineering teams | Need a safe pattern for connecting agents to ERP data | Reuse the connector, tool, guardrail, evaluation, and observability boundaries |
| Risk, compliance, and internal audit | Need evidence that automation did not bypass business controls | Review policy versions, approval requirements, traces, and linked decision IDs |

In a real operation, the intended result is shorter time-to-diagnosis, more consistent triage, fewer missed high-risk orders, and better evidence for human decisions. Those are outcome hypotheses for a pilot—not claims proven by this synthetic POC. A production trial should measure median triage time, exception backlog age, precision/recall by exception type, recommendation acceptance, override reasons, and policy violations.

## From study project to production pilot

1. Connect a read-only ERP reporting API or sanitized export behind the existing repository interface.
2. Map organization-specific fields and calibrate rules against historical planner decisions.
3. Run silently in shadow mode and compare recommendations with actual outcomes.
4. Add identity, role-based access, a durable decision store, and an approval workflow.
5. Export OpenTelemetry signals and alerts to the organization’s observability platform.
6. Pilot with one exception family, business unit, or planner group and define rollback criteria.
7. Add write actions only through a separate, idempotent execution service after governance approval.

## Quick start

Use Python 3.11 or newer. The sole runtime package supplies a maintained TLS certificate bundle.

```bash
python -m pip install -e .
python -m supply_chain_poc.data_generator
python -m unittest discover -s tests -v
python -m supply_chain_poc.api --port 8000
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) for the interactive study-project preview. The deterministic baseline works without credentials. Hosted LLM investigation requires the free Hugging Face token described below.

## Public Vercel demo

The production deployment serves the UI and API from one HTTPS origin, so visitors can test it without installing the project. The optional LLM token is carried in the `X-HF-Token` header for that request only; it is not stored by the UI or included in application telemetry.

```bash
npx vercel@latest
npx vercel@latest --prod
```

See the [Vercel deployment guide](docs/vercel-deployment.md) for validation commands, Git-based deployments, credential handling, and serverless observability limitations. In-memory traces and audit records are best-effort on Vercel because each function instance has its own short-lived process; production deployments should export them to durable services.

To enable the real LLM agentic workflow, create a fine-grained Hugging Face token with **Make calls to Inference Providers** permission. A free Hugging Face account includes a small monthly inference credit; it is intended for experimentation, not unlimited production traffic. For the local POC, paste the token into the password field in the preview. It is sent only to the local backend for that request and is not placed in browser storage or telemetry.

For a server-managed deployment, configure the token before starting the service:

```bash
export HF_TOKEN='hf_your_token'
export HF_MODEL='Qwen/Qwen3-32B'
python -m supply_chain_poc.api --port 8000
```

No OpenAI account, API key, model, or SDK is used. The HTTP client uses Python's standard library plus `certifi` for verified TLS and calls the Hugging Face router directly. To self-host instead, point `HF_BASE_URL` at a Responses-compatible gateway serving a Hugging Face model; tokens are optional for a trusted local endpoint.

The bring-your-own-token UI is intended for localhost study and HTTPS-protected demos. A production service should normally keep provider credentials server-side or use delegated authorization rather than collecting personal access tokens from end users.

In another terminal:

```bash
curl 'http://127.0.0.1:8000/health'
curl 'http://127.0.0.1:8000/ready'
curl 'http://127.0.0.1:8000/erp/orders?limit=3'
curl 'http://127.0.0.1:8000/agent/exceptions'
curl 'http://127.0.0.1:8000/erp/orders?expected_exception=LATE_SHIPMENT&limit=5'
curl 'http://127.0.0.1:8000/metrics'
curl 'http://127.0.0.1:8000/observability/traces?limit=10'
curl 'http://127.0.0.1:8000/observability/audit?limit=10'
curl -X POST 'http://127.0.0.1:8000/agent/llm-triage' \
  -H 'Content-Type: application/json' \
  --data @examples/llm_request.json
```

To classify a record supplied by another system:

```bash
curl -X POST 'http://127.0.0.1:8000/agent/triage' \
  -H 'Content-Type: application/json' \
  --data @examples/order.json
```

## Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/`, `/preview`, `/agent/llm-triage` | Open the interactive browser preview |
| GET | `/health` | Service health check |
| GET | `/ready` | Data-source readiness check |
| GET | `/metrics` | Prometheus-format service and agent metrics |
| GET | `/erp/orders` | Mock ERP order feed; supports `limit` and `expected_exception` |
| GET | `/erp/orders/{order_id}` | Retrieve one order |
| GET | `/agent/exceptions` | Run the complete POC pipeline over the mock feed |
| POST | `/agent/triage` | Classify and recommend an action for one supplied order |
| POST | `/agent/llm-triage` | Run the LLM investigation; accepts an optional ephemeral `X-HF-Token` header |
| GET | `/observability/traces` | Inspect recent correlated spans |
| GET | `/observability/audit` | Inspect recent decision audit events |

## Observability

The POC includes dependency-light implementations of four complementary signals:

- Structured JSON logs written to stdout
- W3C-compatible trace IDs with nested spans for data pull, classification, severity, and recommendation
- Prometheus text metrics for HTTP traffic, stage latency, data pulls, and agent decisions
- Decision audit events containing the policy version and approval boundary

Send `X-Request-ID` or a valid W3C `traceparent` header to continue an upstream correlation context. Both identifiers are returned with the response. Read the [system design](docs/system-design.md) and [observability design](docs/observability.md) for component boundaries, failure behavior, signal definitions, cardinality controls, proposed service-level indicators, privacy boundaries, and the OpenTelemetry production path.

## LLM agent workflow

The optional agent uses Hugging Face Inference Providers and a Responses-compatible API with strict function tools and Structured Outputs. Its default is the open-weight `Qwen/Qwen3-32B` model using the router's automatic fastest-provider policy. Short, retry-safe provider failures receive bounded retries; authentication, validation, and guardrail failures are never retried. Before inference, the application always loads the order, runs deterministic triage, and selects the matching policy. The model then independently decides whether inventory-alternative and supplier-history tools would improve its recommendation.

`google/gemma-4-12B-it` is supported through a self-hosted compatible gateway by setting `HF_MODEL`, but it is not the hosted default because Hugging Face currently exposes no Inference Provider mapping for that repository.

Application guardrails reject any proposal that changes the deterministic exception or severity, selects a disallowed action, investigates the wrong order, or lowers a required approval. Model prompts and complete ERP payloads are not written to operational logs. Read the [LLM agentic workflow](docs/agentic-workflow.md) for tool contracts, safety controls, evaluation gates, production topology, and the path to optional specialist agents.

## Why synthetic data first

Synthetic data makes exception coverage, demos, and tests repeatable. The generator deliberately creates normal orders plus five exception types. The `expected_exception` field is evaluation metadata and would not exist in a production ERP feed.

When real data is available, keep the classification and recommendation modules and replace `load_orders()` in `supply_chain_poc/api.py` with an ERP connector. Begin with a read-only export or reporting API rather than a write-enabled ERP integration.

## POC limitations

- Rules are illustrative and must be calibrated to actual business policies.
- Currency conversion, units of measure, partial receipts, order-line joins, calendars, and supplier acknowledgements are simplified.
- Recommendations are decision support only; no ERP transaction or supplier message is executed.
- `confidence` is `1.0` because classification is rule-based. A trained probability should replace it only after labeled historical decisions are available.
- Hosted LLM calls require `HF_TOKEN` and consume Hugging Face inference credits. Automated tests use a deterministic fake model and do not make network requests.
- The local HTTP server and in-memory telemetry stores are demonstrators, not production infrastructure. On Vercel, telemetry is process-local and may reset on cold starts.

## Evaluation results

The current test dataset contains 200 orders: 75 control records and 25 records for each configured exception. The engine matches all generated ground-truth labels. Agent tests also verify mandatory context hydration, optional model-selected tool use, strict output configuration, trace correlation, ground-truth isolation, and rejection of model attempts to change authoritative decisions. This confirms implementation behavior; it is not evidence of real-world recommendation quality.

Run the repeatable evaluation with:

```bash
python -m unittest discover -s tests -v
```

## Study roadmap

- Add an operations dashboard with filters, evidence, and approve/reject controls.
- Import a public supply-chain dataset through a separate connector.
- Add support for multiple simultaneous exceptions on one order.
- Externalize policies and thresholds into configuration.
- Capture reviewer decisions and measure recommendation acceptance.
- Compare deterministic rules with a supervised model trained on labeled decisions.
- Add authentication, durable decision storage, and container deployment.
- Replace in-memory trace and audit buffers with OpenTelemetry and durable storage.
- Build a sanitized historical evaluation set and compare prompts and models before deployment.

## Repository layout

```text
data/                  Synthetic ERP seed data
examples/              Example API request
outputs/               Formatted data workbook
scripts/               Workbook-generation utility
app.py                 FastAPI/Vercel application entry point
api/index.py            Vercel Python Function adapter
supply_chain_poc/      Generator, rule engine, and mock API
  agentic/             LLM prompt, tools, schemas, and Responses API loop
tests/                 Repeatable rule-engine tests
docs/                  System-design and observability notes
```

## Responsible-use note

This is an educational decision-support prototype. It does not modify ERP transactions, contact suppliers or customers, or execute inventory and spending decisions. Production use would require organization-specific policies, access controls, audit logging, monitoring, and human approval boundaries.
