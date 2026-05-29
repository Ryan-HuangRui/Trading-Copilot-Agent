import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_order_cancel


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def order_state(**overrides) -> dict:
    record = {
        "intent_id": "intent-1",
        "source_signal_id": "sig-1",
        "date": "2026-05-26",
        "session": "pre-market",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "side": "buy",
        "order_type": "LO",
        "quantity": 200,
        "limit_price": 100,
        "broker_order_id": "paper-o-1",
        "remark": "tca:intent-1",
        "submitted_at": "2026-05-26T13:30:00+00:00",
        "status": "accepted",
        "filled_quantity": 0,
    }
    record.update(overrides)
    return record


def execution_state(orders: list[dict]) -> dict:
    return {
        "date": "2026-05-26",
        "generated_at": "2026-05-26T15:00:00+00:00",
        "summary": {"total": len(orders)},
        "orders": orders,
    }


class PaperOrderCancelTest(unittest.TestCase):
    def seed_state(self, root: Path, orders: list[dict]) -> Path:
        path = root / "runtime" / "paper" / "2026-05-26" / "paper-execution-state.json"
        write_json(path, execution_state(orders))
        return path

    def test_cancel_plan_marks_expired_unfilled_entry_order_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [order_state()])

            result = paper_order_cancel.run(
                paper_order_cancel.build_args(
                    repo_root=str(root),
                    date="2026-05-26",
                    now="2026-05-26T14:45:00+00:00",
                    expire_after_minutes=60,
                )
            )

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["summary"]["cancel_candidates"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            candidate = payload["cancel_candidates"][0]
            self.assertEqual(candidate["intent_id"], "intent-1")
            self.assertEqual(candidate["broker_order_id"], "paper-o-1")
            self.assertEqual(candidate["reason"], "entry order exceeded max open duration")
            self.assertEqual(candidate["open_minutes"], 75)
            self.assertEqual(payload["blocked"], [])

    def test_cancel_plan_blocks_filled_partial_and_missing_broker_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(
                root,
                [
                    order_state(intent_id="filled", status="filled", filled_quantity=200),
                    order_state(intent_id="partial", status="partially_filled", filled_quantity=50),
                    order_state(intent_id="missing-id", broker_order_id=None),
                    order_state(intent_id="newer", submitted_at="2026-05-26T14:20:00+00:00"),
                ],
            )

            result = paper_order_cancel.run(
                paper_order_cancel.build_args(
                    repo_root=str(root),
                    date="2026-05-26",
                    now="2026-05-26T14:45:00+00:00",
                    expire_after_minutes=60,
                )
            )

            self.assertEqual(result["summary"]["cancel_candidates"], 0)
            self.assertEqual(result["summary"]["blocked"], 4)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            reasons = {item["intent_id"]: item["reason"] for item in payload["blocked"]}
            self.assertEqual(reasons["filled"], "order status is not cancellable")
            self.assertEqual(reasons["partial"], "order has fills")
            self.assertEqual(reasons["missing-id"], "broker_order_id is required for cancel")
            self.assertEqual(reasons["newer"], "entry order has not exceeded max open duration")

    def test_execute_is_rejected_until_cancel_adapter_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [order_state()])

            with self.assertRaises(PermissionError):
                paper_order_cancel.run(
                    paper_order_cancel.build_args(
                        repo_root=str(root),
                        date="2026-05-26",
                        now="2026-05-26T14:45:00+00:00",
                        expire_after_minutes=60,
                        execute=True,
                    )
                )

    def test_wrapper_exposes_paper_order_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [order_state()])

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-order-cancel",
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                    "--now",
                    "2026-05-26T14:45:00+00:00",
                    "--expire-after-minutes",
                    "60",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["workflow"], "paper-order-cancel")
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["summary"]["cancel_candidates"], 1)


if __name__ == "__main__":
    unittest.main()
