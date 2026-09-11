# Supply Chain Order Exception Agent

Personal study project and proof of concept for automating supply-chain order-exception triage.

The project explores how a multi-step decision-support agent can reduce the repetitive work involved in reviewing ERP orders. It pulls order records, detects operational exceptions, assigns severity, and recommends the next action while retaining human approval for higher-risk decisions.

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
```

This project demonstrates a three-stage workflow:

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

In another terminal:

```bash
curl 'http://127.0.0.1:8000/health'
curl 'http://127.0.0.1:8000/erp/orders?limit=3'
curl 'http://127.0.0.1:8000/agent/exceptions'
curl 'http://127.0.0.1:8000/erp/orders?expected_exception=LATE_SHIPMENT&limit=5'
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
| GET | `/erp/orders` | Mock ERP order feed; supports `limit` and `expected_exception` |
| GET | `/erp/orders/{order_id}` | Retrieve one order |
| GET | `/agent/exceptions` | Run the complete POC pipeline over the mock feed |
| POST | `/agent/triage` | Classify and recommend an action for one supplied order |

## Why synthetic data first

Synthetic data makes exception coverage, demos, and tests repeatable. The generator deliberately creates normal orders plus five exception types. The `expected_exception` field is evaluation metadata and would not exist in a production ERP feed.

When real data is available, keep the classification and recommendation modules and replace `load_orders()` in `supply_chain_poc/api.py` with an ERP connector. Begin with a read-only export or reporting API rather than a write-enabled ERP integration.

## POC limitations

- Rules are illustrative and must be calibrated to actual business policies.
- Currency conversion, units of measure, partial receipts, order-line joins, calendars, and supplier acknowledgements are simplified.
- Recommendations are decision support only; no ERP transaction or supplier message is executed.
- `confidence` is `1.0` because classification is rule-based. A trained probability should replace it only after labeled historical decisions are available.

## Evaluation results

The current test dataset contains 200 orders: 75 control records and 25 records for each configured exception. The engine matches all generated ground-truth labels. This confirms that the generator and rules agree; it is not evidence of real-world predictive accuracy.

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
- Add authentication, persistence, observability, and container deployment.

## Repository layout

```text
data/                  Synthetic ERP seed data
examples/              Example API request
outputs/               Formatted data workbook
scripts/               Workbook-generation utility
supply_chain_poc/      Generator, rule engine, and mock API
tests/                 Repeatable rule-engine tests
```

## Responsible-use note

This is an educational decision-support prototype. It does not modify ERP transactions, contact suppliers or customers, or execute inventory and spending decisions. Production use would require organization-specific policies, access controls, audit logging, monitoring, and human approval boundaries.
