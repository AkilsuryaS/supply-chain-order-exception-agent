# LLM agentic workflow

## Purpose

The LLM layer investigates an order, selects relevant read-only tools, incorporates unstructured planner notes, and proposes an allowed next action. Deterministic code remains authoritative for exception classification, severity, policy, and approval requirements.

The implementation calls the [Hugging Face Responses-compatible API](https://huggingface.co/docs/inference-providers/en/guides/responses-api) directly because this workflow benefits from owning the tool loop, state transitions, validation, and audit behavior. Hugging Face Inference Providers support tool calling and structured outputs across compatible open-weight models. The default is `Qwen/Qwen3-32B`, allowing automatic fastest-provider routing; the model and endpoint remain configurable.

## Runtime flow

```text
POST /agent/llm-triage
          |
          v
  Correlation and root trace
          |
          v
 Application context hydration
          |
          +--> get_order
          +--> run_deterministic_triage
          +--> get_action_policy
          |
          v
   LLM manager agent
          |
          +--> find_inventory_alternatives (model-selected)
          +--> get_supplier_summary (model-selected)
          |
          v
 Strict JSON action proposal
          |
          v
 Deterministic output guardrail
          |
     pass | reject
          v
 Versioned audit event + response
```

Every model response and tool execution is a child span of `llm.agent_run`. The application records model-call latency, token counts, tool outcomes, accepted or rejected proposals, response IDs, and decision IDs without logging prompts or order payloads.

## Why this is genuinely agentic

The application deterministically hydrates the three safety-critical inputs before inference so model-specific tool-calling behavior cannot bypass them. The model controls the remaining investigation loop: it receives the trusted context plus optional tool descriptions, decides whether more evidence is useful, observes any tool results, and continues until it can produce a final action proposal.

The system is not merely asking an LLM to classify a row. The LLM orchestrates bounded tools while application code controls authorization and validates the final decision.

## Tool contracts

| Tool | Role | Side effects |
| --- | --- | --- |
| `get_order` | Fetch normalized order context | None |
| `run_deterministic_triage` | Produce authoritative exception, severity, and approval baseline | Audit event only |
| `get_action_policy` | Return allowed action codes and approval rules | None |
| `find_inventory_alternatives` | Search other plants for possible inventory | None; does not reserve stock |
| `get_supplier_summary` | Summarize observed supplier lateness and risk | None |

Tool schemas use strict JSON arguments. The model never receives a write-enabled ERP tool in this stage.

## Layered controls

1. Input limits reject missing or oversized order identifiers and planner notes.
2. Prompt instructions treat ERP fields and planner notes as untrusted data.
3. Application-controlled context hydration always supplies the requested order, authoritative rules, and matching policy before inference.
4. Structured Outputs constrain the model response to the action-proposal schema.
5. Post-model code prevents changed classifications, changed severity, disallowed action codes, or reduced approval requirements.
6. The workflow proposes actions only. It cannot execute them.

An LLM guardrail is useful for semantic quality, but it must not replace these code-level controls. If future function tools can create holds, transfers, expedites, or supplier messages, each tool needs authorization, fresh-state validation, an idempotency key, spend and quantity limits, and human approval before the side effect.

## Running the LLM workflow

For the local POC, enter a fine-grained token in the preview's password field. The browser sends it as `X-HF-Token` only for that request; neither browser storage nor application telemetry receives it. Alternatively, configure a server-side credential without committing it:

```bash
export HF_TOKEN='hf_your_token'
export HF_MODEL='Qwen/Qwen3-32B'
```

Start the API and submit an investigation:

```bash
python -m supply_chain_poc.api --port 8000
curl -X POST 'http://127.0.0.1:8000/agent/llm-triage' \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: study-run-001' \
  --data @examples/llm_request.json
```

The endpoint uses the request token when supplied and otherwise falls back to server-side `HF_TOKEN`. It fails with `503` when the hosted router token is missing or malformed. It does not disguise a deterministic response as an LLM result. The client uses Python's standard library plus a maintained CA bundle for verified TLS; no OpenAI SDK or account is required.

For local or private deployment, run an open-weight Hugging Face model behind a Responses-compatible inference gateway and configure:

```bash
export HF_BASE_URL='http://127.0.0.1:8001/v1'
export HF_MODEL='Qwen/Qwen3-8B'
```

`HF_TOKEN` is optional for a trusted local endpoint. Model weights and compute are then local, so Hugging Face hosted inference credits are not consumed.

## Model and data handling

The model is configured with `HF_MODEL` and the endpoint with `HF_BASE_URL`, so deployments can evaluate and pin a routed or self-hosted model. The application explicitly sends `store=False`. Production teams must still review their retention, residency, contractual, and security requirements before sending ERP data to any model provider.

Only provide fields that the decision requires. Tokenize or omit customer and supplier identity where possible, remove secrets and personal data, and treat free text as a higher-risk input. Maintain an allowlist of fields per tool rather than forwarding raw ERP objects.

The bring-your-own-token field is appropriate for localhost study and HTTPS-protected demos. Production systems should normally use server-side workload credentials or delegated authorization; they should not collect users' personal access tokens in an application form.

## Evaluation before production

Use three evaluation layers:

- Deterministic tests for exception thresholds, policies, and approval boundaries.
- Agent trajectory tests for mandatory context hydration, optional tool arguments, maximum turns, and failure behavior.
- Outcome evaluations for action-code accuracy, grounded evidence, unsupported claims, approval recall, latency, and cost.

Build a sanitized historical dataset that includes the order snapshot, notes available at decision time, planner action, approval result, and eventual outcome. Split by time rather than randomly so evaluation resembles future operations, and compare every candidate model and prompt against the same replay set.

Critical release gates should include:

- 100% approval recall for actions that require approval
- 0 accepted actions outside the policy allowlist
- 0 changed deterministic classifications or severity values
- A defined minimum agreement with expert planners
- A bounded p95 cost and latency per order
- Successful adversarial tests against prompt injection in notes and source fields

## Production topology

```text
API gateway and identity
          |
          v
Stateless triage API --------> Hugging Face router or self-hosted model
          |
          +----> read-only ERP adapter / sanitized decision view
          +----> policy service with versioned rules
          +----> durable workflow and decision database
          +----> queue for batch investigations
          +----> OpenTelemetry Collector
                          +--> logs
                          +--> metrics
                          +--> traces

Approved action request
          |
          v
Separate action-execution service
          +--> authorization
          +--> idempotency ledger
          +--> fresh ERP state check
          +--> ERP write
```

The model-facing service should remain stateless. Persist run state, tool results, approvals, and final decisions in application-owned storage. Long batches should run through a durable queue with retries, dead-letter handling, concurrency limits, and per-source backpressure.

## Extending to multiple agents

Start with the single manager agent in this repository. Add specialists only when they have materially different tools or instructions, such as inventory reallocation, supplier recovery, or commercial compliance. Expose specialists as bounded tools when the manager should retain control of the final answer. Use handoffs only when a specialist should own the remainder of an interactive workflow.

Multi-agent designs increase model calls, latency, cost, and evaluation surface. They should be introduced based on measured failures of the single-agent workflow rather than as a default architecture.

## Production checklist

- Replace the demonstration HTTP server with a supported web framework and production server.
- Put identity and authorization in front of all data, trace, and audit endpoints.
- Replace CSV access with a read-only ERP adapter and a versioned normalized schema.
- Move policies from code into a reviewed, versioned policy service or repository.
- Persist audit records and workflow state in durable storage.
- Export traces through OpenTelemetry and aggregate metrics across processes.
- Add a total agent-run deadline and circuit breaker around the existing short, budgeted retries for retry-safe failures.
- Add model rate limits, budget limits, circuit breaking, and deterministic failure behavior.
- Pin dependencies and model versions through a tested release process.
- Run offline and shadow-mode evaluations before enabling any side-effecting tool.
- Separate proposal generation from action execution.
