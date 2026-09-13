from pathlib import Path
import unittest

from supply_chain_poc.api import WEB_DIR, route_template


class WebPreviewTests(unittest.TestCase):
    def test_preview_assets_are_packaged_and_linked(self) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

        self.assertTrue((WEB_DIR / "app.css").is_file())
        self.assertTrue((WEB_DIR / "app.js").is_file())
        self.assertIn('href="/assets/app.css"', html)
        self.assertIn('src="/assets/app.js"', html)
        self.assertNotIn("<script>", html)

    def test_preview_supports_llm_and_deterministic_modes(self) -> None:
        javascript = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('requestJson("/agent/llm-triage"', javascript)
        self.assertIn('requestJson("/agent/triage"', javascript)
        self.assertIn("/erp/orders/", javascript)
        self.assertIn('type="password"', html)
        self.assertIn('headers["X-HF-Token"] = token', javascript)
        self.assertNotIn("localStorage", javascript)
        self.assertNotIn("sessionStorage", javascript)

    def test_preview_explains_study_scope_and_beneficiaries(self) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn("Personal engineering study", html)
        self.assertIn("Who could benefit?", html)
        self.assertIn("Order planners", html)
        self.assertIn("A safe adoption path", html)
        self.assertIn("github.com/AkilsuryaS/supply-chain-order-exception-agent", html)

    def test_static_asset_routes_have_bounded_metric_cardinality(self) -> None:
        self.assertEqual(route_template("/assets/app.js"), "/assets/{asset}")
        self.assertEqual(route_template("/assets/unknown.png"), "/assets/{asset}")
        self.assertEqual(route_template("/preview"), "/preview")


if __name__ == "__main__":
    unittest.main()
