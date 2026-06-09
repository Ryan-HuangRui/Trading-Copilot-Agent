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


def preview_payload(order_overrides: dict | None = None) -> dict:
    order = {
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
    if order_overrides:
        order.update(order_overrides)
    return {
        "date": "2026-05-26",
        "session": "pre-market",
        "orders": [order],
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


def order_detail(overrides: dict | None = None) -> dict:
    detail = {
        "order_id": "paper-o-1",
        "symbol": "MU.US",
        "side": "Buy",
        "quantity": "200",
        "price": "100.000",
        "order_type": "LO",
        "status": "New",
        "submitted_at": "2026-05-26T13:30:00Z",
    }
    if overrides:
        detail.update(overrides)
    return detail


class PaperOrderRecoverTest(unittest.TestCase):
    def seed_inputs(self, root: Path, *, preview_overrides: dict | None = None, detail_overrides: dict | None = None) -> None:
        write_json(root / "report" / "2026-05-26" / "paper-trade-preview.json", preview_payload(preview_overrides))
        write_json(root / "runtime" / "paper" / "2026-05-26" / "paper-account-snapshot.json", paper_snapshot())
        write_json(root / "runtime" / "paper" / "2026-05-26" / "order-detail.json", order_detail(detail_overrides))

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

    def test_recover_supports_market_order_without_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root, preview_overrides={"order_type": "MO", "entry_price": None, "reference_price": 100}, detail_overrides={"order_type": "MO", "price": None})
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

            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            record = payload["record"]
            self.assertEqual(record["order_type"], "MO")
            self.assertIsNone(record["limit_price"])
            self.assertNotIn("--price", record["raw_request"]["command"])

    def test_recover_supports_conditional_lit_order_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(
                root,
                preview_overrides={
                    "order_type": "LIT",
                    "entry_price": 100,
                    "trigger_price": 101,
                    "tif": "gtd",
                    "expire_date": "2026-05-27",
                    "outside_rth": "RTH_ONLY",
                },
                detail_overrides={
                    "order_type": "LIT",
                    "price": "100.000",
                    "trigger_price": "101.000",
                    "tif": "gtd",
                    "expire_date": "2026-05-27",
                    "outside_rth": "RTH_ONLY",
                },
            )
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

            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            command = payload["record"]["raw_request"]["command"]
            self.assertEqual(payload["record"]["order_type"], "LIT")
            self.assertEqual(payload["record"]["trigger_price"], 101.0)
            self.assertEqual(payload["record"]["tif"], "gtd")
            self.assertIn("--trigger-price", command)
            self.assertIn("--expire-date", command)
            self.assertIn("--outside-rth", command)

    def test_recover_supports_trailing_percent_order_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(
                root,
                preview_overrides={
                    "order_type": "TSLPPCT",
                    "entry_price": None,
                    "reference_price": 100,
                    "trailing_percent": 2.5,
                    "limit_offset": 0.3,
                },
                detail_overrides={
                    "order_type": "TSLPPCT",
                    "price": None,
                    "trailing_percent": "2.5",
                    "limit_offset": "0.3",
                },
            )
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

            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            command = payload["record"]["raw_request"]["command"]
            self.assertEqual(payload["record"]["order_type"], "TSLPPCT")
            self.assertEqual(payload["record"]["trailing_percent"], 2.5)
            self.assertIn("--trailing-percent", command)
            self.assertIn("--limit-offset", command)

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
