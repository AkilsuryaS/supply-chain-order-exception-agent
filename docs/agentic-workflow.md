# LLM agentic workflow

## Purpose

The LLM layer investigates an order, selects relevant read-only tools, incorporates unstructured planner notes, and proposes an allowed next action. Deterministic code remains authoritative for exception classification, severity, policy, and approval requirements.

The implementation uses the OpenAI Responses API directly because this workflow benefits from owning the tool loop, state transitions, validation, and audit behavior. The [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create) supports custom function tools, structured JSON output, tool-choice controls, and response state. The [function-calling guide](https://developers.openai.com/api/docs/guides/function-calling) and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs) define the underlying contracts.

## Runtime flow

```text
POST /agent/llm-triage
          |
          v
  Correlation and root trace
          |
          v
   LLM manager agent
          |
          +--> get_order (mandatory)
          +--> run_deterministic_triage (mandatory)
          +--> get_action_policy (mandatory)
          +--> find_inventory_alternatives (optional)
          +--> get_supplier_summary (optional)
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

The model controls the investigation loop. It receives tool descriptions, decides which tool to call next, observes tool results, and continues until it can produce a final action proposal. Optional inventory and supplier tools allow it to gather more context when useful.

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
3. Mandatory-tool checks require the requested order, authoritative rules, and matching policy to be consulted.
4. Structured Outputs constrain the model response to the action-proposal schema.
5. Post-model code prevents changed classifications, changed severity, disallowed action codes, or reduced approval requirements.
6. The workflow proposes actions only. It cannot execute them.

An LLM guardrail is useful for semantic quality, but it must not replace these code-level controls. If future function tools can create holds, transfers, expedites, or supplier messages, each tool needs authorization, fresh-state validation, an idempotency key, spend and quantity limits, and human approval before the side effect.

## Running the LLM workflow

Install the optional SDK dependency:

```bash
python -m pip install -e '.[llm]'
```

Configure credentials without committing them:

```bash
cp .env.example .env
export OPENAI_API_KEY='your-key'
export OPENAI_MODEL='gpt-5.5'
```

Start the API and submit an investigation:

```bash
python -m supply_chain_poc.api --port 8000
curl -X POST 'http://127.0.0.1:8000/agent/llm-triage' \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: study-run-001' \
  --data @examples/llm_request.json
```

The endpoint fails with `503` when the SDK or API key is missing. It does not disguise a deterministic response as an LLM result.

## Model and data handling

The model is configured with `OPENAI_MODEL` so deployments can choose and pin a model after evaluation. The application explicitly sends `store=False`. Production teams must still review their retention, residency, contractual, and security requirements before sending ERP data to any model provider.

Only provide fields that the decision requires. Tokenize or omit customer and supplier identity where possible, remove secrets and personal data, and treat free text as a higher-risk input. Maintain an allowlist of fields per tool rather than forwarding raw ERP objects.

## Evaluation before production

Use three evaluation layers:

- Deterministic tests for exception thresholds, policies, and approval boundaries.
- Agent trajectory tests for mandatory tools, tool arguments, maximum turns, and failure behavior.
- Outcome evaluations for action-code accuracy, grounded evidence, unsupported claims, approval recall, latency, and cost.

Build a sanitized historical dataset that includes the order snapshot, notes available at decision time, planner action, approval result, and eventual outcome. Split by time rather than randomly so evaluation resembles future operations. The official [Evals guide](https://developers.openai.com/api/docs/guides/evals) can be used to compare model and prompt versions systematically.

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
Stateless triage API --------> OpenAI Responses API
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
- Add retries with jitter only for retry-safe failures and enforce a total run deadline.
- Add model rate limits, budget limits, circuit breaking, and deterministic failure behavior.
- Pin dependencies and model versions through a tested release process.
- Run offline and shadow-mode evaluations before enabling any side-effecting tool.
- Separate proposal generation from action execution.
