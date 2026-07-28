import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PluginAccountSnapshotTest(unittest.TestCase):
    def test_normalizes_and_aggregates_cross_broker_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ibkr_positions = root / "ibkr-positions.json"
            ibkr_positions.write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "contract_description": "NVDA",
                                "position": 7,
                                "average_price": 200,
                                "market_price": 210,
                                "market_value": 1470,
                                "unrealized_pnl": 70,
                                "currency": "USD",
                                "asset_class": "STK",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            ibkr_account = root / "ibkr-account.json"
            ibkr_account.write_text(
                json.dumps({"currency": "USD", "net_liquidation": 20000, "total_cash_value": 5000}),
                encoding="utf-8",
            )
            longbridge_positions = root / "longbridge-positions.json"
            longbridge_positions.write_text(
                json.dumps(
                    {
                        "list": [
                            {
                                "stock_info": [
                                    {
                                        "symbol": "NVDA.US",
                                        "quantity": "3",
                                        "cost_price": "190",
                                        "currency": "USD",
                                    },
                                    {
                                        "symbol": "TQQQ.US",
                                        "quantity": "5",
                                        "cost_price": "60",
                                        "currency": "USD",
                                    },
                                ]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            longbridge_account = root / "longbridge-account.json"
            longbridge_account.write_text(
                json.dumps([{"currency": "USD", "net_assets": "10000", "total_cash": "1000"}]),
                encoding="utf-8",
            )
            output = root / "snapshot.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "plugin_account_snapshot.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-07-28",
                    "--ibkr-positions",
                    str(ibkr_positions),
                    "--ibkr-account",
                    str(ibkr_account),
                    "--longbridge-positions",
                    str(longbridge_positions),
                    "--longbridge-account",
                    str(longbridge_account),
                    "--output",
                    str(output),
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            snapshot = json.loads(output.read_text(encoding="utf-8"))
            by_symbol = {row["symbol"]: row for row in snapshot["positions"]}
            self.assertEqual(by_symbol["NVDA"]["quantity"], 10)
            self.assertEqual(by_symbol["NVDA"]["brokers"], ["ibkr", "longbridge"])
            self.assertTrue(by_symbol["NVDA"]["cross_broker_overlap"])
            self.assertIsNone(by_symbol["NVDA"]["market_value"])
            self.assertEqual(snapshot["account"]["net_liquidation"], 30000)
            self.assertEqual(snapshot["summary"]["cross_broker_overlaps"], ["NVDA"])

    def test_accepts_partial_plugin_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            positions = root / "longbridge.json"
            positions.write_text(
                json.dumps(
                    {
                        "list": [
                            {
                                "stock_info": [
                                    {
                                        "symbol": "AMD.US",
                                        "quantity": "2",
                                        "cost_price": "100",
                                        "currency": "USD",
                                    }
                                ]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "plugin_account_snapshot.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-07-28",
                    "--longbridge-positions",
                    str(positions),
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["brokers"], ["longbridge"])
            snapshot = json.loads(Path(payload["output"]).read_text(encoding="utf-8"))
            self.assertEqual(
                snapshot["account"]["aggregation_status"],
                "unavailable_mixed_currency_or_incomplete",
            )


if __name__ == "__main__":
    unittest.main()
