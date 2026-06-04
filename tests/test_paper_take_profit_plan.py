import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_take_profit_plan


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


class PaperTakeProfitPlanTest(unittest.TestCase):
    def seed_state(self, root: Path, orders: list[dict]) -> Path:
        path = root / "runtime" / "paper" / "2026-05-26" / "paper-execution-state.json"
        write_json(path, execution_state(orders))
        return path

    def test_plan_creates_lo_sell_tp1_for_filled_long_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])

            result = paper_take_profit_plan.run(
                paper_take_profit_plan.build_args(repo_root=str(root), date="2026-05-26")
            )

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["summary"]["take_profit_candidates"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            candidate = payload["take_profit_candidates"][0]
            self.assertEqual(candidate["intent_id"], "intent-1")
            self.assertEqual(candidate["side"], "sell")
            self.assertEqual(candidate["order_type"], "LO")
            self.assertEqual(candidate["limit_price"], 112.0)
            self.assertEqual(candidate["quantity"], 100)
            self.assertEqual(candidate["remark"], "tca-tp1:intent-1")
            self.assertEqual(
                candidate["preview_command"],
                [
                    "longbridge",
                    "order",
                    "sell",
                    "MU.US",
                    "100",
                    "--price",
                    "112",
                    "--order-type",
                    "LO",
                    "--tif",
                    "gtc",
                    "--remark",
                    "tca-tp1:intent-1",
                    "--format",
                    "json",
                ],
            )

    def test_plan_blocks_unfilled_missing_tp_existing_tp_and_invalid_fraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(
                root,
                [
                    filled_entry(intent_id="accepted", status="accepted", filled_quantity=0),
                    filled_entry(intent_id="missing-tp", take_profit=None),
                    filled_entry(intent_id="existing-tp", take_profit_order_id="tp-o-1"),
                ],
            )

            result = paper_take_profit_plan.run(
                paper_take_profit_plan.build_args(repo_root=str(root), date="2026-05-26")
            )

            self.assertEqual(result["summary"]["take_profit_candidates"], 0)
            self.assertEqual(result["summary"]["blocked"], 3)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            reasons = {item["intent_id"]: item["reason"] for item in payload["blocked"]}
            self.assertEqual(reasons["accepted"], "entry order is not fully filled")
            self.assertEqual(reasons["missing-tp"], "take_profit is required")
            self.assertEqual(reasons["existing-tp"], "take-profit order already exists")

            with self.assertRaises(ValueError):
                paper_take_profit_plan.run(
                    paper_take_profit_plan.build_args(repo_root=str(root), date="2026-05-26", exit_fraction=0)
                )

    def test_execute_records_tp_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])

            adapter = unittest.mock.Mock()
            adapter.submit_take_profit_order.return_value = {
                "broker": "longbridge",
                "account_channel": "lb_papertrading",
                "broker_order_id": "tp-o-1",
                "raw_request": {"command": ["order", "sell", "MU.US"]},
                "raw_response": {"order_id": "tp-o-1", "status": "submitted"},
            }

            with patch.object(paper_take_profit_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_take_profit_plan.run(
                    paper_take_profit_plan.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["summary"]["submitted"], 1)
            adapter.submit_take_profit_order.assert_called_once()
            tp_intent = adapter.submit_take_profit_order.call_args.args[0]
            self.assertEqual(tp_intent["order_type"], "LO")
            self.assertEqual(tp_intent["limit_price"], 112.0)
            self.assertEqual(adapter.submit_take_profit_order.call_args.kwargs["execute"], True)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["submitted"][0]["broker_order_id"], "tp-o-1")
            take_profit_path = root / "runtime" / "paper" / "2026-05-26" / "paper-take-profit-orders.jsonl"
            record = json.loads(take_profit_path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["intent_id"], "intent-1")
            self.assertEqual(record["broker_order_id"], "tp-o-1")

    def test_execute_blocks_when_active_stop_would_over_exit_after_tp1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(
                root,
                [
                    filled_entry(
                        protective_stop_order_id="stop-o-1",
                        stop_status="accepted",
                        protective_stop_quantity=200,
                    )
                ],
            )
            adapter = unittest.mock.Mock()

            with patch.object(paper_take_profit_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_take_profit_plan.run(
                    paper_take_profit_plan.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

            adapter.submit_take_profit_order.assert_not_called()
            self.assertEqual(result["summary"]["submitted"], 0)
            self.assertEqual(result["summary"]["blocked"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["blocked"][0]["reason"], "active protective stop quantity exceeds post-TP1 remaining quantity")

    def test_execute_allows_tp1_when_active_stop_quantity_matches_remaining(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(
                root,
                [
                    filled_entry(
                        protective_stop_order_id="stop-o-1",
                        stop_status="accepted",
                        protective_stop_quantity=100,
                    )
                ],
            )
            adapter = unittest.mock.Mock()
            adapter.submit_take_profit_order.return_value = {
                "broker": "longbridge",
                "account_channel": "lb_papertrading",
                "broker_order_id": "tp-o-1",
                "raw_request": {"command": ["order", "sell", "MU.US"]},
                "raw_response": {"order_id": "tp-o-1", "status": "submitted"},
            }

            with patch.object(paper_take_profit_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_take_profit_plan.run(
                    paper_take_profit_plan.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

            self.assertEqual(result["summary"]["submitted"], 1)
            adapter.submit_take_profit_order.assert_called_once()

    def test_execute_can_resize_full_stop_before_submitting_tp1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(
                root,
                [
                    filled_entry(
                        protective_stop_order_id="stop-o-1",
                        stop_status="accepted",
                        protective_stop_quantity=200,
                        stop_price=95,
                    )
                ],
            )
            adapter = unittest.mock.Mock()
            adapter.cancel_order.return_value = {
                "broker": "longbridge",
                "account_channel": "lb_papertrading",
                "broker_order_id": "stop-o-1",
                "raw_request": {"command": ["order", "cancel", "stop-o-1"]},
                "raw_response": {"order_id": "stop-o-1", "status": "cancelled"},
            }
            adapter.submit_protective_stop_order.return_value = {
                "broker": "longbridge",
                "account_channel": "lb_papertrading",
                "broker_order_id": "stop-o-2",
                "raw_request": {"command": ["order", "sell", "MU.US"]},
                "raw_response": {"order_id": "stop-o-2", "status": "submitted"},
            }
            adapter.submit_take_profit_order.return_value = {
                "broker": "longbridge",
                "account_channel": "lb_papertrading",
                "broker_order_id": "tp-o-1",
                "raw_request": {"command": ["order", "sell", "MU.US"]},
                "raw_response": {"order_id": "tp-o-1", "status": "submitted"},
            }

            with patch.object(paper_take_profit_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_take_profit_plan.run(
                    paper_take_profit_plan.build_args(
                        repo_root=str(root),
                        date="2026-05-26",
                        execute=True,
                        resize_stop_before_submit=True,
                    )
                )

            self.assertEqual(result["summary"]["resized_stops"], 1)
            self.assertEqual(result["summary"]["submitted"], 1)
            adapter.cancel_order.assert_called_once_with("stop-o-1", execute=True, action="take_profit_stop_resize")
            resized_stop = adapter.submit_protective_stop_order.call_args.args[0]
            self.assertEqual(resized_stop["quantity"], 100)
            self.assertEqual(resized_stop["trigger_price"], 95)
            self.assertEqual(
                adapter.submit_protective_stop_order.call_args.kwargs,
                {"execute": True, "action": "take_profit_stop_resize"},
            )
            adapter.submit_take_profit_order.assert_called_once()

            stop_path = root / "runtime" / "paper" / "2026-05-26" / "paper-stop-orders.jsonl"
            stop_record = json.loads(stop_path.read_text(encoding="utf-8").strip())
            self.assertEqual(stop_record["kind"], "paper_resized_stop_order")
            self.assertEqual(stop_record["broker_order_id"], "stop-o-2")
            self.assertEqual(stop_record["replaces_broker_order_id"], "stop-o-1")
            self.assertEqual(stop_record["quantity"], 100)

    def test_execute_skips_duplicate_take_profit_journal_without_calling_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])
            take_profit_path = root / "runtime" / "paper" / "2026-05-26" / "paper-take-profit-orders.jsonl"
            take_profit_path.parent.mkdir(parents=True, exist_ok=True)
            take_profit_path.write_text(json.dumps({"intent_id": "intent-1", "broker_order_id": "tp-o-1"}) + "\n", encoding="utf-8")
            adapter = unittest.mock.Mock()

            with patch.object(paper_take_profit_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_take_profit_plan.run(
                    paper_take_profit_plan.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

            adapter.submit_take_profit_order.assert_not_called()
            self.assertEqual(result["summary"]["take_profit_candidates"], 0)
            self.assertEqual(result["summary"]["blocked"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["blocked"][0]["reason"], "take-profit already submitted")

    def test_execute_records_error_and_continues_other_take_profits(self):
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
            adapter.submit_take_profit_order.side_effect = [
                RuntimeError("broker rejected take profit"),
                {
                    "broker": "longbridge",
                    "account_channel": "lb_papertrading",
                    "broker_order_id": "tp-o-2",
                    "raw_request": {"command": ["order", "sell", "MU.US"]},
                    "raw_response": {"order_id": "tp-o-2", "status": "submitted"},
                },
            ]

            with patch.object(paper_take_profit_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_take_profit_plan.run(
                    paper_take_profit_plan.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

            self.assertEqual(result["summary"]["submitted"], 1)
            self.assertEqual(result["summary"]["errors"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertIn("broker rejected take profit", payload["errors"][0]["error"])

    def test_wrapper_exposes_take_profit_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_state(root, [filled_entry()])

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-take-profit-plan",
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
            self.assertEqual(payload["workflow"], "paper-take-profit-plan")
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["summary"]["take_profit_candidates"], 1)


if __name__ == "__main__":
    unittest.main()
