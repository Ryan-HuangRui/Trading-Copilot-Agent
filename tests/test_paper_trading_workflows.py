import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from longbridge_paper_trade_adapter import ensure_paper_account, ensure_paper_read_command
from paper_trade_preview import build_order_preview


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def valid_signals() -> dict:
    return {
        "date": "2026-05-26",
        "session": "pre-market",
        "signals": [
            {
                "symbol": "MU",
                "setup": "breakout_pullback_continuation.md",
                "direction": "long",
                "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                "risk": {
                    "max_risk_pct": 1,
                    "max_account_risk_pct": 1,
                    "risk_per_share": 5,
                },
                "status": "planned",
                "plan_type": "trade_plan",
                "execution_status": "conditional_executable",
                "entry": {"trigger_price": 100, "order_type": "LO"},
                "stop": {"initial_stop": 95},
                "take_profit": {"tp1": 112},
                "execution_rules": {"skip_conditions": ["market turns risk-off"]},
            }
        ],
    }


def paper_snapshot() -> dict:
    return {
        "date": "2026-05-26",
        "account_channel": "lb_papertrading",
        "account": {"net_liquidation": 100000, "cash": 25000, "currency": "USD"},
        "positions": [],
        "orders": [],
        "executions": [],
    }


class PaperTradingWorkflowTest(unittest.TestCase):
    def run_script(self, script: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "script" / script), *args],
            cwd=cwd or ROOT,
            check=False,
            text=True,
            capture_output=True,
        )

    def test_ensure_paper_account_rejects_live_channel(self):
        with self.assertRaises(ValueError):
            ensure_paper_account({"account": {"account_channel": "lb_live"}, "token": {"status": "valid"}})

    def test_paper_adapter_rejects_write_commands(self):
        self.assertEqual(ensure_paper_read_command(["order", "--format", "json"]), None)
        self.assertEqual(ensure_paper_read_command(["order", "executions", "--format", "json"]), None)
        with self.assertRaises(ValueError):
            ensure_paper_read_command(["order", "buy", "MU.US", "1", "--price", "100"])

    def test_build_order_preview_computes_risk_limited_quantity(self):
        preview = build_order_preview(
            signal=valid_signals()["signals"][0],
            account=paper_snapshot()["account"],
            default_market="US",
            tif="day",
        )

        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["symbol"], "MU")
        self.assertEqual(preview["longbridge_symbol"], "MU.US")
        self.assertEqual(preview["side"], "buy")
        self.assertEqual(preview["quantity"], 200)
        self.assertEqual(preview["estimated_account_risk"], 1000.0)
        self.assertEqual(preview["estimated_notional"], 20000.0)
        self.assertEqual(
            preview["preview_command"],
            [
                "longbridge",
                "order",
                "buy",
                "MU.US",
                "200",
                "--price",
                "100",
                "--order-type",
                "LO",
                "--tif",
                "day",
                "--format",
                "json",
            ],
        )

    def test_paper_account_snapshot_from_fixture_writes_orders_and_executions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "paper-fixture.json"
            write_json(
                fixture,
                {
                    "auth": {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}},
                    "account": [{"net_assets": "100000", "total_cash": "25000", "currency": "USD"}],
                    "positions": [],
                    "orders": [{"order_id": "o-1", "symbol": "MU.US", "side": "buy", "quantity": 200}],
                    "executions": [{"order_id": "o-1", "symbol": "MU.US", "side": "buy", "quantity": 200, "price": 100.2}],
                },
            )

            proc = self.run_script(
                "paper_account_snapshot.py",
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--input",
                str(fixture),
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            output = Path(payload["output"])
            snapshot = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["account_channel"], "lb_papertrading")
            self.assertEqual(snapshot["orders"][0]["order_id"], "o-1")
            self.assertEqual(snapshot["executions"][0]["price"], 100.2)
            self.assertIn("does not submit orders", snapshot["safety_note"])

    def test_paper_trade_preview_script_requires_validation_and_writes_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signals = root / "report" / "2026-05-26" / "pre-market-signals.json"
            account = root / "runtime" / "paper" / "2026-05-26" / "paper-account-snapshot.json"
            setup = root / "knowledge" / "refined" / "setups" / "breakout_pullback_continuation.md"
            write_json(signals, valid_signals())
            write_json(account, paper_snapshot())
            setup.parent.mkdir(parents=True, exist_ok=True)
            setup.write_text("# setup\n", encoding="utf-8")

            proc = self.run_script(
                "paper_trade_preview.py",
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--session",
                "pre-market",
                "--require-validation",
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            output = Path(payload["output"])
            self.assertEqual(payload["summary"]["ready"], 1)
            preview = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(preview["dry_run"])
            self.assertEqual(preview["orders"][0]["quantity"], 200)

    def test_paper_trade_review_appends_matched_execution_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preview = root / "report" / "2026-05-26" / "paper-trade-preview.json"
            snapshot = root / "runtime" / "paper" / "2026-05-26" / "paper-account-snapshot.json"
            write_json(
                preview,
                {
                    "date": "2026-05-26",
                    "session": "pre-market",
                    "orders": [
                        {
                            "signal_id": "sig-1",
                            "symbol": "MU",
                            "longbridge_symbol": "MU.US",
                            "side": "buy",
                            "quantity": 200,
                            "entry_price": 100,
                            "stop_price": 95,
                            "setup": "breakout_pullback_continuation.md",
                            "status": "ready",
                        }
                    ],
                },
            )
            write_json(
                snapshot,
                {
                    **paper_snapshot(),
                    "executions": [
                        {
                            "order_id": "o-1",
                            "symbol": "MU.US",
                            "side": "buy",
                            "quantity": 200,
                            "price": 100.2,
                        }
                    ],
                },
            )

            proc = self.run_script(
                "paper_trade_review.py",
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--session",
                "pre-market",
                "--append",
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["filled"], 1)
            trades_path = root / "runtime" / "journal" / "trades.jsonl"
            record = json.loads(trades_path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["kind"], "trade")
            self.assertEqual(record["status"], "entered")
            self.assertEqual(record["source_signal_id"], "sig-1")
            self.assertEqual(record["paper_order_id"], "o-1")

    def test_paper_trade_review_prefers_submitted_order_id_over_symbol_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preview = root / "report" / "2026-05-26" / "paper-trade-preview.json"
            snapshot = root / "runtime" / "paper" / "2026-05-26" / "paper-account-snapshot.json"
            orders_journal = root / "runtime" / "paper" / "2026-05-26" / "paper-orders.jsonl"
            write_json(
                preview,
                {
                    "date": "2026-05-26",
                    "session": "pre-market",
                    "orders": [
                        {
                            "signal_id": "sig-1",
                            "symbol": "MU",
                            "longbridge_symbol": "MU.US",
                            "side": "buy",
                            "quantity": 100,
                            "entry_price": 100,
                            "stop_price": 95,
                            "take_profit": 112,
                            "setup": "breakout_pullback_continuation.md",
                            "status": "ready",
                        },
                        {
                            "signal_id": "sig-2",
                            "symbol": "MU",
                            "longbridge_symbol": "MU.US",
                            "side": "buy",
                            "quantity": 200,
                            "entry_price": 100,
                            "stop_price": 95,
                            "take_profit": 112,
                            "setup": "breakout_pullback_continuation.md",
                            "status": "ready",
                        },
                    ],
                },
            )
            orders_journal.parent.mkdir(parents=True, exist_ok=True)
            orders_journal.write_text(
                json.dumps(
                    {
                        "intent_id": "intent-2",
                        "source_signal_id": "sig-2",
                        "broker_order_id": "o-2",
                        "remark": "tca:intent-2",
                        "symbol": "MU",
                        "longbridge_symbol": "MU.US",
                        "side": "buy",
                        "quantity": 200,
                        "limit_price": 100,
                        "stop_price": 95,
                        "take_profit": 112,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            write_json(
                snapshot,
                {
                    **paper_snapshot(),
                    "executions": [
                        {
                            "order_id": "o-2",
                            "symbol": "MU",
                            "market": "US",
                            "side": "buy",
                            "quantity": 200,
                            "price": 100.2,
                        }
                    ],
                },
            )

            proc = self.run_script(
                "paper_trade_review.py",
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--session",
                "pre-market",
                "--orders-journal",
                str(orders_journal),
                "--append",
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["filled"], 1)
            review = json.loads((root / "report" / "2026-05-26" / "paper-trade-review.json").read_text(encoding="utf-8"))
            filled = [item for item in review["reviews"] if item["paper_status"] == "filled"]
            self.assertEqual(filled[0]["match"]["method"], "broker_order_id")
            trades_path = root / "runtime" / "journal" / "trades.jsonl"
            record = json.loads(trades_path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["source_signal_id"], "sig-2")
            self.assertEqual(record["intent_id"], "intent-2")
            self.assertEqual(record["broker_order_id"], "o-2")
            self.assertEqual(record["planned_entry"], 100.0)
            self.assertEqual(record["take_profit"], 112.0)
            self.assertAlmostEqual(record["slippage_pct"], 0.2)

    def test_wrapper_exposes_paper_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signals = root / "report" / "2026-05-26" / "pre-market-signals.json"
            account = root / "runtime" / "paper" / "2026-05-26" / "paper-account-snapshot.json"
            setup = root / "knowledge" / "refined" / "setups" / "breakout_pullback_continuation.md"
            write_json(signals, valid_signals())
            write_json(account, paper_snapshot())
            setup.parent.mkdir(parents=True, exist_ok=True)
            setup.write_text("# setup\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-trade-preview",
                    "--date",
                    "2026-05-26",
                    "--session",
                    "pre-market",
                    "--repo-root",
                    str(root),
                    "--require-validation",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["workflow"], "paper-trade-preview")
            self.assertEqual(payload["summary"]["ready"], 1)


if __name__ == "__main__":
    unittest.main()
