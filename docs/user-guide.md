# User guide

This guide is for reviewers who want to understand the project without first reading the source code.

## Run the public demo

Open [the hosted application](https://supply-chain-order-exception-agent.vercel.app/agent/llm-triage).

### Deterministic baseline

1. Enter `PO-10004` for a late shipment or `PO-10005` for an inventory shortage.
2. Add optional planner notes. Notes are untrusted context and cannot change policy.
3. Select **Run deterministic baseline**. No token is required.
4. Review the exception, severity, recommendation, evidence, approval status, decision ID, and policy version.

This mode shows the authoritative business-rule result. Its behavior is repeatable for the same input and policy version.

### Hugging Face agent

1. Create a fine-grained Hugging Face token with **Make calls to Inference Providers** permission.
2. Paste only the value beginning with `hf_` into the password field.
3. Select **Run Hugging Face agent**.
4. Compare the result with the deterministic baseline and inspect the tools listed in the decision trace.

The token is sent over HTTPS in a request header and used only for that inference request. The UI does not put it in local storage, logs, traces, audit events, or the Git repository. A narrowly scoped token is recommended. Model-provider capacity and account credits are external dependencies, so a temporary provider error can still occur after bounded retries.

## What to inspect

- **Evidence** explains the order facts used for the recommendation.
- **Approval** shows whether a person must approve the proposed response.
- **Decision ID** connects a response to its audit event.
- **Policy version** identifies the rule set that controlled the decision.
- **Model and tools** distinguish deterministic execution from the LLM investigation path.
- **Metrics** expose request, latency, decision, and retry signals.
- **Traces** show correlated stages across HTTP, data access, tools, rules, and model calls.
- **Audit** exposes recent versioned decision records without prompts or tokens.
- **Mock ERP** shows the synthetic source records used by the demo.

Hosted observability buffers are process-local and may reset when Vercel starts another function instance. This is acceptable for the study demo; a production implementation should export telemetry and decision records to durable services.

## Run from GitHub

Use Python 3.11 or newer:

```bash
git clone https://github.com/AkilsuryaS/supply-chain-order-exception-agent.git
cd supply-chain-order-exception-agent
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
python -m supply_chain_poc.api --port 8000
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`. Then open `http://127.0.0.1:8000`.

## Suggested study sequence

1. Run the UI in both deterministic and LLM modes.
2. Read `supply_chain_poc/engine.py` to understand authoritative classification.
3. Read `supply_chain_poc/agentic/tools.py` to inspect the agent’s bounded capabilities.
4. Read `supply_chain_poc/agentic/runtime.py` to follow the tool loop, retries, and output validation.
5. Read `supply_chain_poc/observability.py` and inspect the live telemetry endpoints.
6. Run the test suite and change one synthetic scenario or policy threshold.
7. Review the system design and identify the controls needed before connecting real data.

## Safe scope

This project provides decision support only. It must not be represented as autonomous ERP execution or as validated production business policy. Real adoption requires organization-specific data mapping, security, evaluation, human approval, monitoring, and operational ownership.
