import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_order_recover


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def preview_payload() -> dict:
    return {
        "date": "2026-05-26",
        "session": "pre-market",
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
        "account": {},
        "positions": [],
        "orders": [],
        "executions": [],
    }


def order_detail() -> dict:
    return {
        "order_id": "paper-o-1",
        "symbol": "MU.US",
        "side": "Buy",
        "quantity": "200",
        "price": "100.000",
        "order_type": "LO",
        "status": "New",
        "submitted_at": "2026-05-26T13:30:00Z",
    }


class PaperOrderRecoverTest(unittest.TestCase):
    def seed_inputs(self, root: Path) -> None:
        write_json(root / "report" / "2026-05-26" / "paper-trade-preview.json", preview_payload())
        write_json(root / "runtime" / "paper" / "2026-05-26" / "paper-account-snapshot.json", paper_snapshot())
        write_json(root / "runtime" / "paper" / "2026-05-26" / "order-detail.json", order_detail())

    def test_recover_dry_run_builds_record_without_appending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            args = paper_order_recover.build_parser().parse_args(
                [
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                    "--session",
                    "pre-market",
                    "--broker-order-id",
                    "paper-o-1",
                    "--order-detail",
                    "runtime/paper/2026-05-26/order-detail.json",
                ]
            )

            result = paper_order_recover.run(args)

            self.assertEqual(result["summary"]["appended"], 0)
            self.assertFalse((root / "runtime" / "paper" / "2026-05-26" / "paper-orders.jsonl").exists())
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["record"]["broker_order_id"], "paper-o-1")
            self.assertEqual(payload["record"]["submit_status"], "submitted")

    def test_recover_append_is_idempotent_by_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            args = paper_order_recover.build_parser().parse_args(
                [
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                    "--session",
                    "pre-market",
                    "--broker-order-id",
                    "paper-o-1",
                    "--order-detail",
                    "runtime/paper/2026-05-26/order-detail.json",
                    "--append",
                ]
            )

            first = paper_order_recover.run(args)
            second = paper_order_recover.run(args)

            self.assertEqual(first["summary"]["appended"], 1)
            self.assertEqual(second["summary"]["skipped_duplicates"], 1)
            orders_path = root / "runtime" / "paper" / "2026-05-26" / "paper-orders.jsonl"
            records = [json.loads(line) for line in orders_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["broker_order_id"], "paper-o-1")

    def test_wrapper_exposes_paper_order_recover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-order-recover",
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                    "--session",
                    "pre-market",
                    "--broker-order-id",
                    "paper-o-1",
                    "--order-detail",
                    "runtime/paper/2026-05-26/order-detail.json",
                    "--append",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["workflow"], "paper-order-recover")
            self.assertEqual(payload["summary"]["appended"], 1)


if __name__ == "__main__":
    unittest.main()
