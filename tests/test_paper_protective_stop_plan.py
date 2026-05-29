import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_protective_stop_plan


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def filled_entry(**overrides) -> dict:
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
        "stop_price": 95,
        "take_profit": 112,
        "broker_order_id": "entry-o-1",
        "status": "filled",
        "filled_quantity": 200,
        "avg_fill_price": 100.2,
        "submitted_at": "2026-05-26T13:30:00+00:00",
    }
    record.update(overrides)
    return record


def execution_state(orders: list[dict]) -> dict:
    return {
        "date": "2026-05-26",
        "generated_at": "2026-05-26T15:00:00+00:00",
        "orders": orders,
    }


class PaperProtectiveStopPlanTest(unittest.TestCase):
    def seed_state(self, root: Path, orders: list[dict]) -> Path:
        path = root / "runtime" / "paper" / "2026-05-26" / "paper-execution-state.json"
        write_json(path, execution_state(orders))
        return path

    def test_plan_creates_mit_sell_stop_for_filled_long_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])

            result = paper_protective_stop_plan.run(
                paper_protective_stop_plan.build_args(repo_root=str(root), date="2026-05-26")
            )

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["summary"]["stop_candidates"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            candidate = payload["stop_candidates"][0]
            self.assertEqual(candidate["intent_id"], "intent-1")
            self.assertEqual(candidate["side"], "sell")
            self.assertEqual(candidate["order_type"], "MIT")
            self.assertEqual(candidate["trigger_price"], 95.0)
            self.assertEqual(candidate["quantity"], 200)
            self.assertEqual(candidate["remark"], "tca-stop:intent-1")
            self.assertEqual(
                candidate["preview_command"],
                [
                    "longbridge",
                    "order",
                    "sell",
                    "MU.US",
                    "200",
                    "--order-type",
                    "MIT",
                    "--trigger-price",
                    "95",
                    "--tif",
                    "gtc",
                    "--remark",
                    "tca-stop:intent-1",
                    "--format",
                    "json",
                ],
            )

    def test_plan_blocks_unfilled_partial_missing_stop_and_existing_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(
                root,
                [
                    filled_entry(intent_id="accepted", status="accepted", filled_quantity=0),
                    filled_entry(intent_id="partial", status="partially_filled", filled_quantity=50),
                    filled_entry(intent_id="missing-stop", stop_price=None),
                    filled_entry(intent_id="existing-stop", stop_order_id="stop-o-1"),
                ],
            )

            result = paper_protective_stop_plan.run(
                paper_protective_stop_plan.build_args(repo_root=str(root), date="2026-05-26")
            )

            self.assertEqual(result["summary"]["stop_candidates"], 0)
            self.assertEqual(result["summary"]["blocked"], 4)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            reasons = {item["intent_id"]: item["reason"] for item in payload["blocked"]}
            self.assertEqual(reasons["accepted"], "entry order is not fully filled")
            self.assertEqual(reasons["partial"], "entry order is not fully filled")
            self.assertEqual(reasons["missing-stop"], "stop_price is required")
            self.assertEqual(reasons["existing-stop"], "protective stop already exists")

    def test_execute_is_rejected_until_stop_adapter_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])

            with self.assertRaises(PermissionError):
                paper_protective_stop_plan.run(
                    paper_protective_stop_plan.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

    def test_wrapper_exposes_protective_stop_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-protective-stop-plan",
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["workflow"], "paper-protective-stop-plan")
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["summary"]["stop_candidates"], 1)


if __name__ == "__main__":
    unittest.main()
