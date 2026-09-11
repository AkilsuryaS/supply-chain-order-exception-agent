import unittest

from supply_chain_poc.data_generator import generate_orders
from supply_chain_poc.engine import triage_order


class EngineTests(unittest.TestCase):
    def test_generated_ground_truth(self):
        orders = generate_orders(count=80, seed=42)
        for order in orders:
            result = triage_order(order)
            self.assertEqual(order["expected_exception"], result["exception_type"], order["order_id"])

    def test_high_impact_recommendation_requires_approval(self):
        order = generate_orders(count=4, seed=42)[3]
        order["current_eta"] = "2026-10-30"
        order["customer_priority"] = "CRITICAL"
        result = triage_order(order)
        self.assertEqual("CRITICAL", result["severity"])
        self.assertTrue(result["requires_approval"])

    def test_missing_field_is_data_quality(self):
        order = generate_orders(count=1)[0]
        order["sku"] = ""
        self.assertEqual("DATA_QUALITY", triage_order(order)["exception_type"])


if __name__ == "__main__":
    unittest.main()

