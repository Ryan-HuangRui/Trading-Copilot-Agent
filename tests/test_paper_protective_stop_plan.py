import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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

            adapter = unittest.mock.Mock()
            adapter.submit_protective_stop_order.return_value = {
                "broker": "longbridge",
                "account_channel": "lb_papertrading",
                "broker_order_id": "stop-o-1",
                "raw_request": {"command": ["order", "sell", "MU.US"]},
                "raw_response": {"order_id": "stop-o-1", "status": "submitted"},
            }

            with patch.object(paper_protective_stop_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_protective_stop_plan.run(
                    paper_protective_stop_plan.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["summary"]["submitted"], 1)
            adapter.submit_protective_stop_order.assert_called_once()
            stop_intent = adapter.submit_protective_stop_order.call_args.args[0]
            self.assertEqual(stop_intent["order_type"], "MIT")
            self.assertEqual(stop_intent["trigger_price"], 95.0)
            self.assertEqual(adapter.submit_protective_stop_order.call_args.kwargs["execute"], True)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["submitted"][0]["broker_order_id"], "stop-o-1")
            stops_path = root / "runtime" / "paper" / "2026-05-26" / "paper-stop-orders.jsonl"
            record = json.loads(stops_path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["intent_id"], "intent-1")
            self.assertEqual(record["broker_order_id"], "stop-o-1")

    def test_execute_skips_duplicate_stop_journal_without_calling_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])
            stops_path = root / "runtime" / "paper" / "2026-05-26" / "paper-stop-orders.jsonl"
            stops_path.parent.mkdir(parents=True, exist_ok=True)
            stops_path.write_text(json.dumps({"intent_id": "intent-1", "broker_order_id": "stop-o-1"}) + "\n", encoding="utf-8")
            adapter = unittest.mock.Mock()

            with patch.object(paper_protective_stop_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_protective_stop_plan.run(
                    paper_protective_stop_plan.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

            adapter.submit_protective_stop_order.assert_not_called()
            self.assertEqual(result["summary"]["stop_candidates"], 0)
            self.assertEqual(result["summary"]["blocked"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["blocked"][0]["reason"], "protective stop already submitted")

    def test_execute_records_error_and_continues_other_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(
                root,
                [
                    filled_entry(intent_id="intent-1", broker_order_id="entry-o-1"),
                    filled_entry(intent_id="intent-2", broker_order_id="entry-o-2"),
                ],
            )
            adapter = unittest.mock.Mock()
            adapter.submit_protective_stop_order.side_effect = [
                RuntimeError("broker rejected stop"),
                {
                    "broker": "longbridge",
                    "account_channel": "lb_papertrading",
                    "broker_order_id": "stop-o-2",
                    "raw_request": {"command": ["order", "sell", "MU.US"]},
                    "raw_response": {"order_id": "stop-o-2", "status": "submitted"},
                },
            ]

            with patch.object(paper_protective_stop_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_protective_stop_plan.run(
                    paper_protective_stop_plan.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

            self.assertEqual(result["summary"]["submitted"], 1)
            self.assertEqual(result["summary"]["errors"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertIn("broker rejected stop", payload["errors"][0]["error"])

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
