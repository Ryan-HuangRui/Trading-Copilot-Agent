import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from agent_market_data import build_market_data_payload, run as run_market_data


def sample_snapshot() -> dict:
    return {
        "snapshot_date": "2026-05-26",
        "interval": "1day",
        "market_data_source": "longbridge_with_twelve_data_fallback",
        "primary_market_data_source": "longbridge",
        "fallback_market_data_source": "twelve",
        "symbols": [
            {
                "symbol": "MU",
                "meta": {"provider": "longbridge", "symbol": "MU"},
                "latest": {"datetime": "2026-05-26", "open": "100", "high": "110", "low": "98", "close": "108", "volume": "1200"},
                "previous": {"datetime": "2026-05-25", "open": "96", "high": "101", "low": "95", "close": "100", "volume": "900"},
                "metrics": {"close_delta_pct": 8.0},
                "bars": [
                    {"datetime": "2026-05-26", "open": "100", "high": "110", "low": "98", "close": "108", "volume": "1200"},
                    {"datetime": "2026-05-25", "open": "96", "high": "101", "low": "95", "close": "100", "volume": "900"},
                ],
                "sources": ["watchlist"],
            }
        ],
        "errors": [],
    }


class AgentMarketDataTest(unittest.TestCase):
    def test_build_market_data_payload_from_snapshot_preserves_provider_metadata(self):
        payload = build_market_data_payload(
            date="2026-05-26",
            symbols=["mu", "NVDA"],
            source_payload=sample_snapshot(),
            source_path=Path("report/2026-05-26/daily-snapshot.json"),
            source_kind="daily_snapshot",
        )

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["tool"], "agent_market_data")
        self.assertEqual(payload["symbols"], ["MU", "NVDA"])
        self.assertEqual(payload["provider_metadata"]["primary_market_data_source"], "longbridge")
        self.assertEqual(payload["provider_metadata"]["fallback_market_data_source"], "twelve")
        self.assertEqual(len(payload["evidence"]), 1)
        evidence = payload["evidence"][0]
        self.assertEqual(evidence["source_type"], "market_data")
        self.assertEqual(evidence["as_of"], "2026-05-26")
        self.assertEqual(evidence["symbol"], "MU")
        self.assertEqual(evidence["confidence"], 0.95)
        self.assertIn("latest close", evidence["summary"])
        self.assertEqual(payload["missing_symbols"], ["NVDA"])

    def test_run_writes_market_data_artifact_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "daily-snapshot.json"
            output = root / "market-data.json"
            snapshot.write_text(json.dumps(sample_snapshot(), ensure_ascii=False), encoding="utf-8")

            result = run_market_data(
                Namespace(
                    date="2026-05-26",
                    symbol=["MU"],
                    snapshot=str(snapshot),
                    context=None,
                    output=str(output),
                    repo_root=str(ROOT),
                )
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["output"], str(output))
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written["source_kind"], "daily_snapshot")
            self.assertEqual(written["market_data"]["MU"]["latest"]["close"], "108")


if __name__ == "__main__":
    unittest.main()
