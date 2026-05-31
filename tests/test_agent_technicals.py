import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from agent_technicals import build_technicals_payload, run as run_technicals


def bars(count: int = 30) -> list[dict]:
    rows = []
    for index in range(count):
        close = 100 + index
        rows.insert(
            0,
            {
                "datetime": f"2026-05-{index + 1:02d}",
                "open": str(close - 1),
                "high": str(close + 2),
                "low": str(close - 3),
                "close": str(close),
                "volume": str(1000 + index),
            },
        )
    return rows


class AgentTechnicalsTest(unittest.TestCase):
    def test_build_technicals_payload_computes_fixture_indicators(self):
        source = {
            "date": "2026-05-30",
            "symbols": ["MU"],
            "market_data": {
                "MU": {
                    "latest": bars(30)[0],
                    "bars": bars(30),
                }
            },
        }

        payload = build_technicals_payload(
            date="2026-05-30",
            symbols=["MU"],
            source_payload=source,
            source_path=Path("market-data.json"),
        )

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["tool"], "agent_technicals")
        self.assertEqual(len(payload["evidence"]), 1)
        metrics = payload["technicals"]["MU"]["metrics"]
        self.assertAlmostEqual(metrics["sma_20"], 119.5)
        self.assertAlmostEqual(metrics["sma_50"], 114.5)
        self.assertGreater(metrics["atr_14"], 0)
        self.assertIsNotNone(metrics["rsi_14"])
        self.assertEqual(payload["evidence"][0]["source_type"], "technical_indicator")

    def test_run_writes_technicals_artifact_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "market-data.json"
            output = root / "technicals.json"
            source.write_text(
                json.dumps(
                    {
                        "date": "2026-05-30",
                        "symbols": ["MU"],
                        "market_data": {"MU": {"latest": bars(30)[0], "bars": bars(30)}},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_technicals(
                Namespace(
                    date="2026-05-30",
                    symbol=["MU"],
                    market_data=str(source),
                    output=str(output),
                    repo_root=str(ROOT),
                )
            )

            self.assertEqual(result["status"], "success")
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("MU", written["technicals"])


if __name__ == "__main__":
    unittest.main()
