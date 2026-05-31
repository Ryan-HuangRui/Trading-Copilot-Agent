import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_trade_submit


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def valid_preview() -> dict:
    return {
        "date": "2026-05-26",
        "session": "pre-market",
        "dry_run": True,
        "orders": [
            {
                "signal_id": "sig-1",
                "symbol": "MU",
                "longbridge_symbol": "MU.US",
                "setup": "breakout_pullback_continuation.md",
                "side": "buy",
                "status": "ready",
                "quantity": 200,
                "entry_price": 100,
                "stop_price": 95,
                "take_profit": 112,
                "risk_per_share": 5,
                "max_account_risk_pct": 1,
                "estimated_account_risk": 1000,
                "estimated_notional": 20000,
                "order_type": "LO",
                "tif": "day",
            }
        ],
    }


def monitor_preview() -> dict:
    payload = valid_preview()
    payload["session"] = "monitor"
    payload["orders"][0]["status"] = "blocked"
    payload["orders"][0]["reasons"] = ["execution_status is not conditional_executable"]
    return payload


def second_valid_order() -> dict:
    order = dict(valid_preview()["orders"][0])
    order.update(
        {
            "signal_id": "sig-2",
            "symbol": "AAPL",
            "longbridge_symbol": "AAPL.US",
            "quantity": 10,
            "entry_price": 200,
            "stop_price": 190,
            "take_profit": 225,
            "risk_per_share": 10,
            "estimated_account_risk": 100,
            "estimated_notional": 2000,
        }
    )
    return order


def paper_snapshot() -> dict:
    return {
        "date": "2026-05-26",
        "account_channel": "lb_papertrading",
        "account": {"net_liquidation": 100000, "cash": 25000, "currency": "USD"},
        "positions": [],
        "orders": [],
        "executions": [],
    }


class PaperTradeSubmitTest(unittest.TestCase):
    def args(self, root: Path, **overrides) -> Namespace:
        values = {
            "repo_root": str(root),
            "date": "2026-05-26",
            "session": "pre-market",
            "preview": None,
            "account_snapshot": None,
            "orders_journal": None,
            "signals": None,
            "output": None,
            "require_validation": False,
            "execute": False,
            "longbridge_cli": None,
            "max_daily_risk_pct": 3.0,
            "max_daily_orders": 3,
        }
        values.update(overrides)
        return Namespace(**values)

    def run_submit(self, root: Path, *extra_args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "script" / "paper_trade_submit.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--session",
                "pre-market",
                *extra_args,
            ],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )

    def seed_inputs(self, root: Path) -> None:
        write_json(root / "report" / "2026-05-26" / "paper-trade-preview.json", valid_preview())
        write_json(root / "runtime" / "paper" / "2026-05-26" / "paper-account-snapshot.json", paper_snapshot())
        setup = root / "knowledge" / "refined" / "setups" / "breakout_pullback_continuation.md"
        setup.parent.mkdir(parents=True, exist_ok=True)
        setup.write_text("# setup\n", encoding="utf-8")
        write_json(
            root / "report" / "2026-05-26" / "pre-market-signals.json",
            {
                "date": "2026-05-26",
                "session": "pre-market",
                "signals": [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "direction": "long",
                        "trigger": {"type": "break_above", "price": 100, "text": "breaks 100"},
                        "invalidation": {"type": "break_below", "price": 95, "text": "breaks 95"},
                        "risk": {"max_risk_pct": 1, "max_account_risk_pct": 1, "risk_per_share": 5},
                        "status": "planned",
                        "plan_type": "trade_plan",
                        "execution_status": "conditional_executable",
                        "entry": {"trigger_price": 100},
                        "stop": {"initial_stop": 95},
                        "take_profit": {"tp1": 112},
                        "execution_rules": {"skip_conditions": ["risk-off"]},
                    }
                ],
            },
        )

    def seed_monitor_inputs(self, root: Path) -> None:
        write_json(root / "report" / "2026-05-26" / "paper-trade-preview.json", monitor_preview())
        write_json(root / "runtime" / "paper" / "2026-05-26" / "paper-account-snapshot.json", paper_snapshot())
        setup = root / "knowledge" / "refined" / "setups" / "strong_breakout_trend_following.md"
        setup.parent.mkdir(parents=True, exist_ok=True)
        setup.write_text("# setup\n", encoding="utf-8")
        write_json(
            root / "report" / "2026-05-26" / "monitor-signals.json",
            {
                "date": "2026-05-26",
                "session": "monitor",
                "source_report": "report/latest-monitor.json",
                "signals": [
                    {
                        "symbol": "MU",
                        "setup": "strong_breakout_trend_following.md",
                        "direction": "long",
                        "trigger": {"type": "break_above", "price": 100, "text": "break"},
                        "invalidation": {"type": "break_below", "price": 95, "text": "stop"},
                        "risk": {"max_risk_pct": 1, "text": "monitor dry-run only"},
                        "status": "observed",
                        "plan_type": "watch_only",
                        "execution_status": "watch_only",
                    }
                ],
            },
        )

    def test_submit_dry_run_writes_submission_without_orders_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)

            proc = self.run_submit(root, "--require-validation")

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "success")
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["summary"]["ready"], 1)
            output = Path(payload["output"])
            submission = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(submission["submitted"], [])
            self.assertEqual(submission["ready"][0]["intent"]["source_signal_id"], "sig-1")
            self.assertFalse((root / "runtime" / "paper" / "2026-05-26" / "paper-orders.jsonl").exists())

    def test_submit_dry_run_skips_duplicate_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            first = self.run_submit(root)
            intent_id = json.loads(Path(json.loads(first.stdout)["output"]).read_text(encoding="utf-8"))["ready"][0]["intent"]["intent_id"]
            orders_path = root / "runtime" / "paper" / "2026-05-26" / "paper-orders.jsonl"
            orders_path.parent.mkdir(parents=True, exist_ok=True)
            orders_path.write_text(json.dumps({"intent_id": intent_id}) + "\n", encoding="utf-8")

            proc = self.run_submit(root)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            submission = json.loads(Path(json.loads(proc.stdout)["output"]).read_text(encoding="utf-8"))
            self.assertEqual(len(submission["skipped_duplicates"]), 1)
            self.assertEqual(submission["summary"]["skipped_duplicates"], 1)

    def test_monitor_submit_dry_run_is_allowed_but_has_no_ready_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_monitor_inputs(root)

            result = paper_trade_submit.run(self.args(root, session="monitor", require_validation=True))

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["summary"]["ready"], 0)

    def test_monitor_submit_execute_is_hard_rejected_before_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_monitor_inputs(root)

            with patch.object(paper_trade_submit, "LongbridgePaperOrderAdapter") as adapter:
                with self.assertRaises(PermissionError):
                    paper_trade_submit.run(self.args(root, session="monitor", execute=True))

            adapter.assert_not_called()

    def test_wrapper_exposes_paper_trade_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-trade-submit",
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                    "--session",
                    "pre-market",
                    "--require-validation",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["workflow"], "paper-trade-submit")
            self.assertEqual(payload["summary"]["ready"], 1)

    def test_execute_submits_ready_intent_and_writes_orders_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            adapter = unittest.mock.Mock()
            adapter.submit_limit_order.return_value = {
                "broker": "longbridge",
                "account_channel": "lb_papertrading",
                "broker_order_id": "order-1",
                "raw_request": {"command": ["order", "buy"], "remark": "tca:test"},
                "raw_response": {"order_id": "order-1", "status": "submitted"},
            }

            with patch.object(paper_trade_submit, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_trade_submit.run(self.args(root, execute=True))

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["summary"]["submitted"], 1)
            self.assertEqual(result["summary"]["ready"], 0)
            adapter.submit_limit_order.assert_called_once()
            called_intent = adapter.submit_limit_order.call_args.args[0]
            self.assertEqual(called_intent["remark"], f"tca:{called_intent['intent_id']}")
            self.assertEqual(adapter.submit_limit_order.call_args.kwargs["execute"], True)
            orders_path = root / "runtime" / "paper" / "2026-05-26" / "paper-orders.jsonl"
            records = [json.loads(line) for line in orders_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["broker_order_id"], "order-1")
            self.assertFalse(records[0]["dry_run"])
            self.assertEqual(records[0]["submit_status"], "submitted")
            submission = json.loads((root / "report" / "2026-05-26" / "paper-trade-submission.json").read_text(encoding="utf-8"))
            self.assertEqual(submission["submitted"][0]["broker_order_id"], "order-1")

    def test_execute_skips_duplicate_without_calling_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            dry_run = paper_trade_submit.run(self.args(root))
            submission = json.loads(Path(dry_run["output"]).read_text(encoding="utf-8"))
            intent_id = submission["ready"][0]["intent"]["intent_id"]
            orders_path = root / "runtime" / "paper" / "2026-05-26" / "paper-orders.jsonl"
            orders_path.parent.mkdir(parents=True, exist_ok=True)
            orders_path.write_text(json.dumps({"intent_id": intent_id, "submit_status": "submitted"}) + "\n", encoding="utf-8")

            adapter = unittest.mock.Mock()
            with patch.object(paper_trade_submit, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_trade_submit.run(self.args(root, execute=True))

            adapter.submit_limit_order.assert_not_called()
            self.assertEqual(result["summary"]["skipped_duplicates"], 1)
            self.assertEqual(result["summary"]["submitted"], 0)

    def test_execute_records_error_and_continues_other_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            preview = valid_preview()
            preview["orders"].append(second_valid_order())
            write_json(root / "report" / "2026-05-26" / "paper-trade-preview.json", preview)
            adapter = unittest.mock.Mock()
            adapter.submit_limit_order.side_effect = [
                RuntimeError("broker rejected order"),
                {
                    "broker": "longbridge",
                    "account_channel": "lb_papertrading",
                    "broker_order_id": "order-2",
                    "raw_request": {"command": ["order", "buy"]},
                    "raw_response": {"order_id": "order-2", "status": "submitted"},
                },
            ]

            with patch.object(paper_trade_submit, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_trade_submit.run(self.args(root, execute=True))

            self.assertEqual(result["summary"]["submitted"], 1)
            self.assertEqual(result["summary"]["errors"], 1)
            submission = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertIn("broker rejected order", submission["errors"][0]["error"])
            records = [
                json.loads(line)
                for line in (root / "runtime" / "paper" / "2026-05-26" / "paper-orders.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["broker_order_id"], "order-2")


if __name__ == "__main__":
    unittest.main()
