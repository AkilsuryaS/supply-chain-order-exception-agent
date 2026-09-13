import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as vercel_app
from supply_chain_poc.agentic.runtime import AgentRunError


class VercelAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(vercel_app.app)

    def test_preview_and_security_headers(self) -> None:
        response = self.client.get("/")

        self.assertEqual(200, response.status_code)
        self.assertIn('id="hf-token"', response.text)
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        self.assertIn("traceparent", response.headers)

    def test_health_and_deterministic_triage(self) -> None:
        health = self.client.get("/health")
        order = self.client.get("/erp/orders/PO-10004")
        result = self.client.post("/agent/triage", json=order.json())

        self.assertEqual("0.7.0", health.json()["version"])
        self.assertEqual(200, result.status_code)
        self.assertEqual("LATE_SHIPMENT", result.json()["exception_type"])

    def test_request_token_is_forwarded_without_storage(self) -> None:
        class FakeAgent:
            @staticmethod
            def run(order_id: str, planner_notes: str) -> dict:
                return {"order_id": order_id, "planner_notes_received": bool(planner_notes)}

        with patch.object(
            vercel_app.HuggingFaceResponseAgent,
            "from_env",
            return_value=FakeAgent(),
        ) as factory:
            response = self.client.post(
                "/agent/llm-triage",
                headers={"X-HF-Token": "hf_requesttoken"},
                json={"order_id": "PO-10004", "planner_notes": "test"},
            )

        self.assertEqual(200, response.status_code)
        factory.assert_called_once_with(token_override="hf_requesttoken")
        self.assertNotIn("hf_requesttoken", response.text)

    def test_retryable_provider_failure_has_machine_readable_response(self) -> None:
        class UnavailableAgent:
            @staticmethod
            def run(_order_id: str, _planner_notes: str) -> dict:
                raise AgentRunError("Model provider temporarily unavailable", retryable=True)

        with patch.object(
            vercel_app.HuggingFaceResponseAgent,
            "from_env",
            return_value=UnavailableAgent(),
        ):
            response = self.client.post(
                "/agent/llm-triage",
                headers={"X-HF-Token": "hf_requesttoken"},
                json={"order_id": "PO-10004", "planner_notes": "test"},
            )

        self.assertEqual(503, response.status_code)
        self.assertEqual("provider_unavailable", response.json()["code"])
        self.assertTrue(response.json()["retryable"])


if __name__ == "__main__":
    unittest.main()
