import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_break_even_stop_plan


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


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
        "tp1_status": "filled",
        "tp1_filled_quantity": 100,
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


class PaperBreakEvenStopPlanTest(unittest.TestCase):
    def seed_state(self, root: Path, orders: list[dict]) -> None:
        path = root / "runtime" / "paper" / "2026-05-26" / "paper-execution-state.json"
        write_json(path, execution_state(orders))

    def seed_stops(self, root: Path, records: list[dict] | None = None) -> None:
        path = root / "runtime" / "paper" / "2026-05-26" / "paper-stop-orders.jsonl"
        write_jsonl(
            path,
            records
            or [
                {
                    "kind": "paper_stop_order",
                    "intent_id": "intent-1",
                    "broker_order_id": "stop-o-1",
                    "trigger_price": 95,
                }
            ],
        )

    def test_plan_creates_break_even_move_after_tp1_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])
            self.seed_stops(root)

            result = paper_break_even_stop_plan.run(
                paper_break_even_stop_plan.build_args(repo_root=str(root), date="2026-05-26")
            )

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["summary"]["move_candidates"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            candidate = payload["move_candidates"][0]
            self.assertEqual(candidate["intent_id"], "intent-1")
            self.assertEqual(candidate["existing_stop_order_id"], "stop-o-1")
            self.assertEqual(candidate["remaining_quantity"], 100)
            self.assertEqual(candidate["trigger_price"], 100.2)
            self.assertEqual(candidate["remark"], "tca-be-stop:intent-1")
            self.assertEqual(candidate["preview_steps"][0]["action"], "cancel_existing_stop")
            self.assertEqual(candidate["preview_steps"][1]["command"][0:5], ["longbridge", "order", "sell", "MU.US", "100"])

    def test_plan_creates_lit_break_even_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])
            self.seed_stops(root)

            result = paper_break_even_stop_plan.run(
                paper_break_even_stop_plan.build_args(
                    repo_root=str(root),
                    date="2026-05-26",
                    order_type="LIT",
                    limit_price=100.0,
                )
            )

            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            candidate = payload["move_candidates"][0]
            self.assertEqual(candidate["order_type"], "LIT")
            self.assertEqual(candidate["limit_price"], 100.0)
            self.assertEqual(candidate["trigger_price"], 100.2)
            command = candidate["preview_steps"][1]["command"]
            self.assertEqual(command[command.index("--order-type") + 1], "LIT")
            self.assertEqual(command[command.index("--price") + 1], "100")
            self.assertEqual(command[command.index("--trigger-price") + 1], "100.2")

    def test_plan_blocks_trailing_break_even_stop_without_trailing_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])
            self.seed_stops(root)

            result = paper_break_even_stop_plan.run(
                paper_break_even_stop_plan.build_args(repo_root=str(root), date="2026-05-26", order_type="TSLPPCT")
            )

            self.assertEqual(result["summary"]["move_candidates"], 0)
            self.assertEqual(result["summary"]["blocked"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertIn("trailing_percent must be > 0 for TSLPPCT", payload["blocked"][0]["reason"])

    def test_plan_blocks_without_tp1_stop_or_remaining_quantity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(
                root,
                [
                    filled_entry(intent_id="no-tp1", tp1_status=None, tp1_filled_quantity=0),
                    filled_entry(intent_id="no-stop"),
                    filled_entry(intent_id="closed", remaining_quantity=0, tp1_filled_quantity=200),
                ],
            )
            self.seed_stops(root, [{"intent_id": "no-tp1", "broker_order_id": "stop-o-1", "trigger_price": 95}])

            result = paper_break_even_stop_plan.run(
                paper_break_even_stop_plan.build_args(repo_root=str(root), date="2026-05-26")
            )

            self.assertEqual(result["summary"]["move_candidates"], 0)
            self.assertEqual(result["summary"]["blocked"], 3)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            reasons = {item["intent_id"]: item["reason"] for item in payload["blocked"]}
            self.assertEqual(reasons["no-tp1"], "TP1 fill evidence is required")
            self.assertEqual(reasons["no-stop"], "existing protective stop order is required")
            self.assertEqual(reasons["closed"], "existing protective stop order is required")

    def test_plan_blocks_when_stop_already_at_break_even_and_rejects_negative_buffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry(stop_price=101)])
            self.seed_stops(root)

            result = paper_break_even_stop_plan.run(
                paper_break_even_stop_plan.build_args(repo_root=str(root), date="2026-05-26")
            )

            self.assertEqual(result["summary"]["move_candidates"], 0)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["blocked"][0]["reason"], "stop is already at or above break-even")

            with self.assertRaises(ValueError):
                paper_break_even_stop_plan.run(
                    paper_break_even_stop_plan.build_args(repo_root=str(root), date="2026-05-26", buffer_pct=-0.1)
                )

    def test_wrapper_exposes_break_even_stop_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])
            self.seed_stops(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-break-even-stop-plan",
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
            self.assertEqual(payload["workflow"], "paper-break-even-stop-plan")
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["summary"]["move_candidates"], 1)

    def test_execute_moves_stop_with_cancel_then_submit_when_gate_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])
            self.seed_stops(root)
            config = root / "config" / "paper_execution.json"
            write_json(
                config,
                {
                    "paper_execution": {
                        "broker_writes_enabled": True,
                        "allow_break_even_stop_move": True,
                    }
                },
            )
            adapter = unittest.mock.Mock()
            adapter.cancel_order.return_value = {
                "broker_order_id": "stop-o-1",
                "raw_request": {"command": ["order", "cancel", "stop-o-1"]},
                "raw_response": {"order_id": "stop-o-1", "status": "cancelled"},
                "account_channel": "lb_papertrading",
            }
            adapter.submit_protective_stop_order.return_value = {
                "broker_order_id": "stop-o-2",
                "raw_request": {"command": ["order", "sell", "MU.US"]},
                "raw_response": {"order_id": "stop-o-2", "status": "submitted"},
                "account_channel": "lb_papertrading",
            }

            with patch.object(paper_break_even_stop_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_break_even_stop_plan.run(
                    paper_break_even_stop_plan.build_args(
                        repo_root=str(root),
                        date="2026-05-26",
                        execute=True,
                        paper_execution_config=str(config),
                    )
                )

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["summary"]["moved"], 1)
            adapter.cancel_order.assert_called_once_with("stop-o-1", execute=True, action="break_even_stop_move")
            submitted_intent = adapter.submit_protective_stop_order.call_args.args[0]
            self.assertEqual(submitted_intent["trigger_price"], 100.2)
            self.assertEqual(submitted_intent["quantity"], 100)
            self.assertEqual(
                adapter.submit_protective_stop_order.call_args.kwargs,
                {"execute": True, "action": "break_even_stop_move"},
            )
            records = [
                json.loads(line)
                for line in (root / "runtime" / "paper" / "2026-05-26" / "paper-stop-orders.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(records[-1]["kind"], "paper_break_even_stop_order")
            self.assertEqual(records[-1]["broker_order_id"], "stop-o-2")
            self.assertEqual(records[-1]["replaces_broker_order_id"], "stop-o-1")

    def test_execute_moves_lit_stop_with_shape_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])
            self.seed_stops(root)
            config = root / "config" / "paper_execution.json"
            write_json(
                config,
                {
                    "paper_execution": {
                        "broker_writes_enabled": True,
                        "allow_break_even_stop_move": True,
                    }
                },
            )
            adapter = unittest.mock.Mock()
            adapter.cancel_order.return_value = {
                "broker_order_id": "stop-o-1",
                "raw_request": {},
                "raw_response": {},
                "account_channel": "lb_papertrading",
            }
            adapter.submit_protective_stop_order.return_value = {
                "broker_order_id": "stop-o-2",
                "raw_request": {},
                "raw_response": {},
                "account_channel": "lb_papertrading",
            }

            with patch.object(paper_break_even_stop_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_break_even_stop_plan.run(
                    paper_break_even_stop_plan.build_args(
                        repo_root=str(root),
                        date="2026-05-26",
                        execute=True,
                        paper_execution_config=str(config),
                        order_type="LIT",
                        limit_price=100.0,
                    )
                )

            self.assertEqual(result["summary"]["moved"], 1)
            submitted_intent = adapter.submit_protective_stop_order.call_args.args[0]
            self.assertEqual(submitted_intent["order_type"], "LIT")
            self.assertEqual(submitted_intent["limit_price"], 100.0)
            self.assertEqual(submitted_intent["trigger_price"], 100.2)
            record = json.loads(
                (root / "runtime" / "paper" / "2026-05-26" / "paper-stop-orders.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[-1]
            )
            self.assertEqual(record["order_type"], "LIT")
            self.assertEqual(record["limit_price"], 100.0)


if __name__ == "__main__":
    unittest.main()
