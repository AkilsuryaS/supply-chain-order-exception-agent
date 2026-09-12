# Supply Chain Order Exception Agent

Personal study project and proof of concept for automating supply-chain order-exception triage.

The project explores how a multi-step decision-support agent can reduce the repetitive work involved in reviewing ERP orders. It includes both a deterministic baseline and an optional LLM tool-calling workflow. The LLM investigates orders through bounded read-only tools while deterministic rules retain control of classifications, severity, policy, and approval requirements.

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

## Quick start

Python 3.11 or newer is the only runtime requirement.

```bash
python -m supply_chain_poc.data_generator
python -m unittest discover -s tests -v
python -m supply_chain_poc.api --port 8000
```

To enable the real LLM agentic workflow:

```bash
python -m pip install -e '.[llm]'
export OPENAI_API_KEY='your-key'
export OPENAI_MODEL='gpt-5.5'
python -m supply_chain_poc.api --port 8000
```

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
| GET | `/health` | Service health check |
| GET | `/ready` | Data-source readiness check |
| GET | `/metrics` | Prometheus-format service and agent metrics |
| GET | `/erp/orders` | Mock ERP order feed; supports `limit` and `expected_exception` |
| GET | `/erp/orders/{order_id}` | Retrieve one order |
| GET | `/agent/exceptions` | Run the complete POC pipeline over the mock feed |
| POST | `/agent/triage` | Classify and recommend an action for one supplied order |
| POST | `/agent/llm-triage` | Run the LLM tool-calling investigation for an order ID |
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

The optional agent uses the OpenAI Responses API with strict function tools and Structured Outputs. It must call the order, deterministic-triage, and policy tools before returning a proposal. It can independently decide whether inventory-alternative and supplier-history tools would improve the recommendation.

Application guardrails reject any proposal that changes the deterministic exception or severity, selects a disallowed action, investigates the wrong order, or lowers a required approval. Model prompts and complete ERP payloads are not written to operational logs. Read the [LLM agentic workflow](docs/agentic-workflow.md) for tool contracts, safety controls, evaluation gates, production topology, and the path to optional specialist agents.

## Why synthetic data first

Synthetic data makes exception coverage, demos, and tests repeatable. The generator deliberately creates normal orders plus five exception types. The `expected_exception` field is evaluation metadata and would not exist in a production ERP feed.

When real data is available, keep the classification and recommendation modules and replace `load_orders()` in `supply_chain_poc/api.py` with an ERP connector. Begin with a read-only export or reporting API rather than a write-enabled ERP integration.

## POC limitations

- Rules are illustrative and must be calibrated to actual business policies.
- Currency conversion, units of measure, partial receipts, order-line joins, calendars, and supplier acknowledgements are simplified.
- Recommendations are decision support only; no ERP transaction or supplier message is executed.
- `confidence` is `1.0` because classification is rule-based. A trained probability should replace it only after labeled historical decisions are available.
- LLM calls require an API key and the optional `llm` dependency. Automated tests use a deterministic fake model and do not make billable network requests.
- The included HTTP server and in-memory telemetry stores are demonstrators, not production infrastructure.

## Evaluation results

The current test dataset contains 200 orders: 75 control records and 25 records for each configured exception. The engine matches all generated ground-truth labels. Agent tests also verify mandatory tool use, strict output configuration, trace correlation, ground-truth isolation, and rejection of model attempts to change authoritative decisions. This confirms implementation behavior; it is not evidence of real-world recommendation quality.

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
supply_chain_poc/      Generator, rule engine, and mock API
  agentic/             LLM prompt, tools, schemas, and Responses API loop
tests/                 Repeatable rule-engine tests
docs/                  System-design and observability notes
```

## Responsible-use note

This is an educational decision-support prototype. It does not modify ERP transactions, contact suppliers or customers, or execute inventory and spending decisions. Production use would require organization-specific policies, access controls, audit logging, monitoring, and human approval boundaries.
