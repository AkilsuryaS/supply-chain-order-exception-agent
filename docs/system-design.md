# System design

## Scope

The system is a read-only decision-support POC for supply-chain order exceptions. It demonstrates the boundary between ERP data access, deterministic business rules, an optional LLM tool-calling investigator, recommendations, human approval, and operational telemetry. It intentionally does not execute ERP changes or send external messages.

## Design principles

- Keep business decisions deterministic, explainable, and versioned.
- Isolate ERP-specific code behind a data-source boundary.
- Treat recommendations as proposals until an explicit approval policy allows execution.
- Correlate every decision with its request, trace, policy version, and audit event.
- Avoid high-cardinality metric labels and sensitive payload logging.
- Degrade visibly when the data source is unavailable rather than returning plausible empty results.
- Fail closed when the LLM, required tools, structured output, or post-model guardrails fail.

## Components

```text
Caller
  |
  v
HTTP API and correlation context
  |
  +---- health, readiness, metrics, traces, audit
  |
  v
Order source adapter
  |
  v
Agent orchestration
  +---- classification
  +---- severity scoring
  +---- recommendation policy
  |
  +---- optional LLM manager
          +---- application-controlled rule and policy hydration
          +---- optional context tools
          +---- structured action proposal
          +---- deterministic output guardrail
  |
  v
Decision response + append-only audit intent
```

### API boundary

The HTTP layer validates protocol concerns, generates or propagates correlation IDs, normalizes routes for metrics, and converts unexpected errors into a stable error response. It does not contain classification rules.

### Data-source boundary

`load_orders()` currently reads a synthetic CSV file. A production connector should implement the same conceptual operation using a read-only ERP API, export, replica, or integration platform. Source-specific field mapping should happen before the business engine.

### Business engine

The engine evaluates all configured exception rules, ranks simultaneous issues, chooses a primary exception, calculates severity, and maps the result to a recommendation. It has no HTTP or storage dependency.

### Policy and approval boundary

Recommendations return `requires_approval`. The POC never treats a recommendation as an executed action. A future execution service should be a separate component with authorization, idempotency, spend and inventory limits, and an immutable record of the approver.

### LLM orchestration boundary

The optional LLM manager owns investigation order and tool selection, not business authority. It must load the requested order, run deterministic triage, and retrieve the matching policy. It may then call inventory or supplier-context tools. Strict output schemas constrain the final proposal, and application code rejects any attempt to change authoritative fields or approval controls. See [the LLM agentic workflow](agentic-workflow.md).

### Observability boundary

The observability module owns correlation context, JSON logging, spans, metrics, and audit events. Business code calls this interface without depending on a specific vendor backend. See [observability design](observability.md).

## Request and decision flow

1. Accept or generate `X-Request-ID` and W3C trace context.
2. Start the `http.request` span.
3. Pull and normalize source records under `data.pull`.
4. Start `agent.batch` for batch operations.
5. Start one `agent.order` span for each order.
6. Run classification, severity, and recommendation child spans.
7. Write a decision audit event with a unique decision ID and policy version.
8. Return the decision and correlation headers.
9. Record request count, duration, status, and a structured completion log.

For `/agent/llm-triage`, steps 4–7 run inside an `llm.agent_run` span. The application sends tool results back to the model until it returns a structured proposal or reaches the configured turn limit. A second, linked audit event records the accepted LLM proposal and its deterministic parent decision.

## Data contracts

The input contract contains order identity, supplier and SKU references, dates, quantities, inventory position, commercial values, and customer/supplier risk context. The output contract contains:

- Unique `decision_id`
- `order_id`
- Primary and additional exception types
- Severity, score, and confidence semantics
- Evidence and recommended action
- Approval requirement
- `policy_version`
- LLM action code, model, response ID, and linked deterministic decision ID when applicable

The synthetic `expected_exception` field is evaluation metadata. Production connectors must remove or ignore it before classification.

## Reliability and failure behavior

| Failure | Current behavior | Production control |
| --- | --- | --- |
| Missing or invalid order field | Return a data-quality exception | Dead-letter queue and source-owner workflow |
| Data source unavailable | Readiness fails; request returns an error | Retry with jitter, circuit breaker, alert, and last-success freshness |
| Unexpected rule error | HTTP 500 with request ID; error span and metric | Error budget alert and replayable work item |
| Duplicate read request | Safe because classification is read-only | Cache or deduplicate by source version if needed |
| Duplicate future action | Not applicable in this POC | Mandatory idempotency key and action ledger |
| Process restart | In-memory telemetry is lost | External telemetry and durable audit stores |
| Slow batch | Duration is visible by request and agent stage | Queue-based workers, concurrency limits, and backpressure |
| Model timeout or provider error | LLM endpoint fails closed with correlated error | Deadline, bounded retry policy, circuit breaker, and explicit deterministic fallback product decision |
| Invalid or unsafe model proposal | Post-model guardrail rejects the proposal | Alert, retained trace, evaluation regression, and planner routing |

## Scaling path

For higher volume, separate synchronous single-order triage from asynchronous batch processing. The API would place batch work on a durable queue, workers would claim partitions using a run ID, and results would be written to a decision store. Concurrency should be limited per ERP source to avoid amplifying upstream failures. A batch status resource would expose progress, failures, and retry state.

The rules are CPU-light. ERP and model latency, data volume, token budgets, and downstream action safety are more likely constraints than classification compute.

## Security and governance

- Use read-only, least-privilege ERP credentials for ingestion.
- Authenticate every non-health endpoint and authorize audit access separately.
- Encrypt source data and audit records in transit and at rest.
- Redact or tokenize customer, supplier, commercial, and free-text data in logs.
- Version rules, thresholds, and recommendation policies.
- Record the human approver and before/after state for future executed actions.
- Apply retention and deletion policies to operational data while preserving required audit evidence.

## Testing strategy

- Unit tests verify each exception boundary and severity threshold.
- Generated ground truth verifies rule and generator agreement.
- Observability tests verify trace hierarchy, propagation, error spans, audit correlation, and metric exposure.
- Agent trajectory tests use a deterministic fake model to verify tool calls, schemas, turn limits, and post-model guardrails without billable network calls.
- Contract tests should be added for each real ERP adapter.
- Replay tests should use sanitized historical exceptions before production rollout.
- Failure-injection tests should cover ERP timeouts, malformed records, partial batches, and telemetry-backend outages.

## Key tradeoffs

The deterministic POC uses standard-library components and in-memory telemetry to remain immediately runnable. The optional LLM path calls Hugging Face Inference Providers—or a self-hosted compatible endpoint—through a dependency-free Responses API client and explicit tool loop. This demonstrates contracts and signal design but does not provide durable storage, distributed export, authentication, or multi-process aggregation. The intended production evolution is a supported web runtime, durable workflow state, OpenTelemetry, and external metrics, trace, log, and audit backends rather than expanding the in-memory stores.
