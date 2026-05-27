import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from longbridge_cli_adapter import ensure_read_only_command


class PositionReviewTest(unittest.TestCase):
    def test_longbridge_adapter_rejects_write_commands(self):
        with self.assertRaises(ValueError):
            ensure_read_only_command(["order", "submit", "--symbol", "MU"])
        with self.assertRaises(ValueError):
            ensure_read_only_command(["watchlist", "update", "group-1"])
        self.assertEqual(ensure_read_only_command(["account", "positions", "--format", "json"]), None)

    def test_account_snapshot_from_fixture_writes_normalized_runtime_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture.json"
            fixture.write_text(
                json.dumps(
                    {
                        "account": {"net_liquidation": 100000, "cash": 20000, "currency": "USD"},
                        "positions": [
                            {
                                "symbol": "MU.US",
                                "quantity": 100,
                                "avg_cost": 90.5,
                                "last_price": 96.2,
                                "market_value": 9620,
                                "unrealized_pnl": 570,
                                "unrealized_pnl_pct": 6.3,
                                "currency": "USD",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(ROOT / "script" / "longbridge_account_snapshot.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-27",
                "--input",
                str(fixture),
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            output = Path(payload["output"])
            self.assertTrue(output.exists())
            snapshot = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["positions"][0]["symbol"], "MU")
            self.assertEqual(snapshot["positions"][0]["market"], "US")
            self.assertEqual(snapshot["account"]["net_liquidation"], 100000.0)

    def test_position_review_flags_plan_and_invalidation_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account_dir = root / "runtime" / "account" / "2026-05-27"
            account_dir.mkdir(parents=True)
            (account_dir / "account-snapshot.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "account": {"net_liquidation": 100000, "cash": 20000, "currency": "USD"},
                        "positions": [
                            {
                                "symbol": "MU",
                                "market": "US",
                                "quantity": 100,
                                "avg_cost": 90,
                                "last_price": 96,
                                "market_value": 9600,
                                "unrealized_pnl": 600,
                                "unrealized_pnl_pct": 6.67,
                            },
                            {
                                "symbol": "OLD",
                                "market": "US",
                                "quantity": 10,
                                "last_price": 20,
                                "market_value": 200,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report_dir = root / "report" / "2026-05-27"
            report_dir.mkdir(parents=True)
            (report_dir / "signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "trigger": {"price": 100, "text": "突破 100"},
                                "invalidation": {"price": 95, "text": "跌破 95"},
                                "risk": {"max_risk_pct": 1},
                                "status": "planned",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(ROOT / "script" / "position_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-27",
                "--append",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["positions"], 2)
            review = json.loads((report_dir / "position-review.json").read_text(encoding="utf-8"))
            by_symbol = {item["symbol"]: item for item in review["position_reviews"]}
            self.assertTrue(by_symbol["MU"]["in_today_signals"])
            self.assertEqual(by_symbol["MU"]["risk_state"], "close_to_invalidation")
            self.assertFalse(by_symbol["OLD"]["in_today_signals"])
            self.assertTrue(by_symbol["OLD"]["review_required"])
            lines = (root / "runtime" / "journal" / "position_reviews.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
