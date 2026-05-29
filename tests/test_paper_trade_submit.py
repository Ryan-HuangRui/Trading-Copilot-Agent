import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    unittest.main()
