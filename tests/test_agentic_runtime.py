import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from supply_chain_poc.agentic.runtime import (
    AgentConfig,
    AgentConfigurationError,
    OpenAIResponseAgent,
    ProposalGuardrailError,
)
from supply_chain_poc.agentic.tools import CsvOrderRepository, SupplyChainTools
from supply_chain_poc.engine import triage_order
from supply_chain_poc.observability import AUDIT_EVENTS, METRICS, TRACES, request_context


def function_call(name: str, arguments: dict, index: int) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=json.dumps(arguments),
        call_id=f"call-{index}",
    )


class FakeResponses:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("No fake response remains")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = FakeResponses(responses)


def fake_response(response_id: str, *, calls: list | None = None, output_text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=response_id,
        output=calls or [],
        output_text=output_text,
        usage=SimpleNamespace(input_tokens=100, output_tokens=25),
    )


class AgenticRuntimeTests(unittest.TestCase):
    def setUp(self):
        AUDIT_EVENTS.clear()
        TRACES.clear()
        METRICS.reset()
        self.tools = SupplyChainTools(CsvOrderRepository())
        self.order_id = "PO-10004"
        deterministic = triage_order(self.tools.repository.get_order(self.order_id))
        self.proposal = {
            "order_id": self.order_id,
            "summary": "The supplier ETA is later than the promised date.",
            "primary_exception": deterministic["exception_type"],
            "severity": deterministic["severity"],
            "action_code": "REQUEST_SUPPLIER_RECOVERY",
            "recommended_action": "Ask the supplier for a dated recovery plan.",
            "rationale": ["A confirmed recovery date is needed before considering expediting."],
            "evidence_used": ["The deterministic rules detected a late shipment."],
            "risk_flags": ["Delivery commitment at risk"],
            "requires_approval": deterministic["requires_approval"],
            "approval_reason": "The deterministic severity requires planner approval."
            if deterministic["requires_approval"]
            else None,
            "confidence": 0.91,
        }
        self.deterministic = deterministic
        AUDIT_EVENTS.clear()
        TRACES.clear()
        METRICS.reset()

    def build_client(self, proposal: dict | None = None) -> FakeClient:
        proposal = proposal or self.proposal
        return FakeClient(
            [
                fake_response("resp-1", calls=[function_call("get_order", {"order_id": self.order_id}, 1)]),
                fake_response(
                    "resp-2",
                    calls=[function_call("run_deterministic_triage", {"order_id": self.order_id}, 2)],
                ),
                fake_response(
                    "resp-3",
                    calls=[
                        function_call(
                            "get_action_policy",
                            {
                                "exception_type": self.deterministic["exception_type"],
                                "severity": self.deterministic["severity"],
                            },
                            3,
                        )
                    ],
                ),
                fake_response("resp-4", output_text=json.dumps(proposal)),
            ]
        )

    def test_agent_executes_tools_and_returns_guarded_structured_proposal(self):
        client = self.build_client()
        agent = OpenAIResponseAgent(client, self.tools, AgentConfig(model="test-model", max_turns=6))
        trace_id = "e" * 32
        with request_context("agent-request-1", trace_id):
            result = agent.run(self.order_id, "Supplier asked for a one-day extension.")

        self.assertEqual("llm_tool_calling", result["agent_mode"])
        self.assertEqual("resp-4", result["response_id"])
        self.assertEqual(
            ["get_order", "run_deterministic_triage", "get_action_policy"],
            result["tools_called"],
        )
        self.assertEqual(self.deterministic["exception_type"], result["primary_exception"])
        self.assertNotEqual(result["decision_id"], result["deterministic_decision_id"])

        first_call = client.responses.calls[0]
        self.assertFalse(first_call["store"])
        self.assertTrue(first_call["text"]["format"]["strict"])
        self.assertIn("planner_notes", first_call["input"])
        self.assertEqual("resp-1", client.responses.calls[1]["previous_response_id"])

        traces = TRACES.recent(100)
        self.assertEqual({trace_id}, {item["trace_id"] for item in traces})
        self.assertIn("llm.agent_run", {item["name"] for item in traces})
        self.assertIn("llm.model", {item["name"] for item in traces})
        self.assertIn("llm.tool", {item["name"] for item in traces})
        self.assertEqual("LLM_ACTION_PROPOSAL", AUDIT_EVENTS.recent(1)[0]["decision_type"])

    def test_guardrail_rejects_changed_exception(self):
        invalid = {**self.proposal, "primary_exception": "PRICE_MISMATCH"}
        agent = OpenAIResponseAgent(self.build_client(invalid), self.tools, AgentConfig(model="test-model", max_turns=6))
        with self.assertRaisesRegex(ProposalGuardrailError, "changed the deterministic exception"):
            agent.run(self.order_id)
        self.assertIn('status="rejected"', METRICS.render_prometheus())

    def test_order_tool_does_not_leak_ground_truth_label(self):
        result = self.tools.call("get_order", {"order_id": self.order_id})
        self.assertNotIn("expected_exception", result["order"])

    def test_missing_api_key_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(AgentConfigurationError, "OPENAI_API_KEY"):
                OpenAIResponseAgent.from_env(self.tools)


if __name__ == "__main__":
    unittest.main()
