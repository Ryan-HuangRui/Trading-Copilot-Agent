import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from longbridge_cli_adapter import ensure_read_only_command


class PluginTradeSnapshotTest(unittest.TestCase):
    def test_normalizes_broker_executions_and_order_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ibkr = root / "ibkr.json"
            ibkr.write_text(
                json.dumps(
                    {
                        "trades": [
                            {
                                "trade_id": "ib-1",
                                "order_id": 10,
                                "symbol": "MU",
                                "side": "SELL",
                                "size": 1,
                                "price": 100,
                                "currency": "USD",
                                "commission": 0.2,
                                "realized_pnl": -5,
                                "trade_time": "2026-07-28T13:45:00Z",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            executions = root / "lb-executions.json"
            executions.write_text(
                json.dumps(
                    {
                        "executions": [
                            {
                                "order_id": "lb-1",
                                "symbol": "NVDA.US",
                                "side": "Buy",
                                "quantity": "2",
                                "price": "200",
                                "trade_done_at": "2026-07-28T14:00:00Z",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            orders = root / "lb-orders.json"
            orders.write_text(
                json.dumps(
                    {
                        "orders": [
                            {
                                "order_id": "lb-1",
                                "symbol": "NVDA.US",
                                "side": "Buy",
                                "order_type": "LO",
                                "status": "FilledStatus",
                                "submitted_at": "2026-07-28T13:59:00Z",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = root / "trade-snapshot.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "plugin_trade_snapshot.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-07-28",
                    "--ibkr-trades",
                    str(ibkr),
                    "--longbridge-executions",
                    str(executions),
                    "--longbridge-orders",
                    str(orders),
                    "--output",
                    str(output),
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            snapshot = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["summary"]["executions"], 2)
            self.assertEqual(snapshot["summary"]["buy_executions"], 1)
            self.assertEqual(snapshot["summary"]["sell_executions"], 1)
            self.assertEqual(
                snapshot["summary"]["notional_by_currency_side"],
                {"UNKNOWN:buy": 400.0, "USD:sell": 100.0},
            )
            by_symbol = {row["symbol"]: row for row in snapshot["executions"]}
            self.assertEqual(by_symbol["NVDA"]["order_type"], "LO")
            self.assertEqual(by_symbol["NVDA"]["status"], "FilledStatus")
            self.assertEqual(by_symbol["MU"]["realized_pnl"], -5.0)

    def test_filters_ibkr_utc_trade_to_new_york_market_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ibkr = root / "ibkr.json"
            ibkr.write_text(
                json.dumps(
                    {
                        "trades": [
                            {
                                "trade_id": "prior-market-day",
                                "symbol": "AMD",
                                "side": "BUY",
                                "size": 1,
                                "price": 100,
                                "trade_time": "2026-07-28T01:00:00Z",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "plugin_trade_snapshot.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-07-28",
                    "--ibkr-trades",
                    str(ibkr),
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            snapshot = json.loads(Path(payload["output"]).read_text(encoding="utf-8"))
            self.assertEqual(snapshot["summary"]["executions"], 0)

    def test_longbridge_order_allowlist_rejects_mutations(self):
        ensure_read_only_command(["order", "--format", "json"])
        ensure_read_only_command(["order", "executions", "--format", "json"])
        for command in (
            ["order", "cancel", "123", "--format", "json"],
            ["order", "replace", "123", "--format", "json"],
            ["order", "submit", "--format", "json"],
        ):
            with self.assertRaises(ValueError):
                ensure_read_only_command(command)


if __name__ == "__main__":
    unittest.main()
