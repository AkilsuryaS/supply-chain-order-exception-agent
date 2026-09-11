# Observability design

The POC treats observability as part of the agent contract. Every request and order decision can be reconstructed from correlated logs, spans, metrics, and audit events without logging the full ERP payload.

## Signal model

```text
HTTP request
  trace_id + request_id
          |
          +-- data.pull
          |
          +-- agent.batch
                 |
                 +-- agent.order
                       +-- agent.classification
                       +-- agent.severity
                       +-- agent.recommendation
                              |
                              +-- decision audit event

All spans ------> bounded trace store ------> /observability/traces
All stages -----> counters and durations ---> /metrics
Request summary -> structured JSON stdout
Decisions ------> bounded audit store ------> /observability/audit
```

## Structured logging

Logs are emitted as one JSON object per line. Request logs include:

- UTC timestamp and service version
- Event name and log level
- `request_id`, `trace_id`, and `span_id`
- Normalized route, HTTP status, and duration
- Error type for failures

The implementation deliberately avoids request bodies, customer identifiers, pricing data, and recommendation evidence in operational logs. Production deployments should add centralized redaction and access controls before accepting real ERP data.

Set `LOG_LEVEL=DEBUG` to emit individual span-completion logs. The default `INFO` level records service lifecycle, request summaries, and batch summaries without producing one log line per order.

## Tracing

The API accepts a W3C `traceparent` header and returns the active context in the response. If the caller does not provide one, the service creates a trace ID. A separate `X-Request-ID` is accepted or generated for operational correlation.

Each order has a root `agent.order` span. Classification, severity scoring, and recommendation are children of that span. Batch processing adds an `agent.batch` parent, and HTTP processing adds the outer `http.request` span.

The POC stores recent spans in a thread-safe, bounded memory buffer. This makes the design demonstrable without infrastructure, but the records disappear when the process restarts.

## Metrics

`GET /metrics` exposes Prometheus text format with:

- HTTP request counts by method, normalized route, and status
- HTTP request duration count and sum
- Data-pull counts by source and outcome
- Span counts and duration by fixed stage and status
- Triage decisions by exception type and severity

Routes are normalized before they become metric labels. Order IDs, supplier IDs, request IDs, and trace IDs are never metric labels because their high cardinality would make a production metrics backend expensive and unstable.

## Decision audit trail

Every triage decision records the order ID, trace ID, request ID, exception type, severity, score, approval requirement, timestamp, and policy version. The audit record intentionally excludes the complete input payload.

The in-memory audit endpoint is for local demonstration only. A production audit sink should be append-only, encrypted, access controlled, retained according to policy, and queryable by order and trace ID.

## Health model

- `/health` is a liveness check. It confirms that the process can respond.
- `/ready` is a readiness check. It verifies that the configured data source can be read.
- `/metrics` supports service and agent-performance monitoring.

Liveness must not depend on the ERP because an ERP outage should make the service unready, not cause an orchestrator to restart an otherwise healthy process repeatedly.

## Proposed service-level indicators

| Indicator | POC measurement | Example initial objective |
| --- | --- | --- |
| Availability | Successful HTTP requests / total requests | 99.5% |
| Single-order latency | `/agent/triage` request duration | p95 below 250 ms |
| Batch latency | `agent.batch` duration | p95 below 5 seconds for 200 orders |
| Data-pull reliability | Successful pulls / total pulls | 99% |
| Decision coverage | Orders producing a valid decision / orders evaluated | 99.9% |
| Approval pressure | Decisions requiring approval / exception decisions | Monitor and calibrate |

These are starting hypotheses, not measured production commitments. Real objectives should follow observed traffic, ERP behavior, and planner expectations.

## Production migration

The local interfaces map directly to a production stack:

| POC component | Production replacement |
| --- | --- |
| JSON stdout | Fluent Bit or platform log collector to Loki, Elasticsearch, or a cloud log service |
| In-memory spans | OpenTelemetry SDK and Collector to Tempo, Jaeger, or a cloud trace service |
| In-process metrics | Prometheus scrape endpoint and Grafana dashboards |
| In-memory audit buffer | Append-only PostgreSQL table, event stream, or immutable object storage |
| Local readiness check | ERP connectivity, credential validity, schema, and freshness checks |

The business engine depends only on the observability interface. Export backends can therefore change without rewriting classification or recommendation rules.
