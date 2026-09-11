import json
import logging
import unittest

from supply_chain_poc.api import route_template
from supply_chain_poc.data_generator import generate_orders
from supply_chain_poc.engine import triage_order
from supply_chain_poc.observability import (
    AUDIT_EVENTS,
    JsonFormatter,
    METRICS,
    TRACES,
    parse_traceparent,
    request_context,
    span,
)


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        AUDIT_EVENTS.clear()
        TRACES.clear()
        METRICS.reset()

    def test_agent_steps_share_one_trace_and_form_a_hierarchy(self):
        trace_id = "a" * 32
        with request_context("request-123", trace_id):
            result = triage_order(generate_orders(count=4, seed=42)[3])

        traces = TRACES.recent(20)
        self.assertEqual({trace_id}, {item["trace_id"] for item in traces})
        by_name = {item["name"]: item for item in traces}
        self.assertEqual(by_name["agent.order"]["span_id"], by_name["agent.classification"]["parent_span_id"])
        self.assertEqual(by_name["agent.order"]["span_id"], by_name["agent.severity"]["parent_span_id"])
        self.assertEqual("LATE_SHIPMENT", by_name["agent.order"]["attributes"]["exception_type"])

        audit = AUDIT_EVENTS.recent(1)[0]
        self.assertEqual(trace_id, audit["trace_id"])
        self.assertEqual(result["exception_type"], audit["exception_type"])
        self.assertEqual(result["decision_id"], audit["decision_id"])
        self.assertEqual(result["policy_version"], audit["policy_version"])
        self.assertNotIn("customer_id", audit)

    def test_metrics_include_stage_duration_and_decision_count(self):
        triage_order(generate_orders(count=1, seed=42)[0])
        metrics = METRICS.render_prometheus()
        self.assertIn("supply_chain_span_duration_seconds_count", metrics)
        self.assertIn("supply_chain_triage_decisions_total", metrics)
        self.assertIn('exception_type="NO_EXCEPTION"', metrics)

    def test_error_span_is_recorded(self):
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with request_context("request-error", "b" * 32):
                with span("agent.test_failure"):
                    raise RuntimeError("test failure")
        trace = TRACES.recent(1)[0]
        self.assertEqual("ERROR", trace["status"])
        self.assertEqual("RuntimeError", trace["error_type"])

    def test_traceparent_parser_and_low_cardinality_route(self):
        value = f"00-{'c' * 32}-{'d' * 16}-01"
        self.assertEqual("c" * 32, parse_traceparent(value))
        self.assertIsNone(parse_traceparent("invalid"))
        self.assertEqual("/erp/orders/{order_id}", route_template("/erp/orders/PO-10001"))

    def test_log_formatter_emits_machine_readable_json(self):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "request complete", (), None)
        record.event_data = {"event": "test.completed", "trace_id": "a" * 32, "status": 200}
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual("test.completed", payload["event"])
        self.assertEqual("a" * 32, payload["trace_id"])
        self.assertEqual(200, payload["status"])


if __name__ == "__main__":
    unittest.main()
