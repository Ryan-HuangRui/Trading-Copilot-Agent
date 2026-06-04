import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentResearchProviderContractsTest(unittest.TestCase):
    def test_config_defines_fixture_only_non_market_providers(self):
        config = json.loads((ROOT / "config" / "agent_research.json").read_text(encoding="utf-8"))

        self.assertEqual(config["schema_version"], 1)
        self.assertEqual(config["market_data"]["primary_source"], "longbridge")
        self.assertEqual(config["market_data"]["fallback_source"], "twelve")
        for name in ("fundamentals", "news", "sentiment"):
            provider = config["providers"][name]
            self.assertEqual(provider["mode"], "fixture")
            self.assertFalse(provider["live_enabled"])
            self.assertIn("required_evidence_fields", provider)
        external = config["providers"]["external_disclosures"]
        self.assertEqual(external["mode"], "open_cabinet")
        self.assertTrue(external["live_enabled"])
        self.assertEqual(external["official_slug"], "trump-donald-j")
        self.assertIn("required_evidence_fields", external)

    def test_fixture_evidence_contains_required_fields(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "agent_research" / "provider_contract_fixture.json").read_text(encoding="utf-8"))

        required = {"evidence_id", "source", "source_type", "symbol", "summary", "confidence", "limitations"}
        for item in fixture["evidence"]:
            self.assertTrue(required.issubset(item))
            self.assertTrue(item.get("as_of") or item.get("published_at"))
            self.assertIsInstance(item["limitations"], list)


if __name__ == "__main__":
    unittest.main()
