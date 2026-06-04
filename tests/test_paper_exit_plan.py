import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_exit_plan


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def execution_state(**order_overrides) -> dict:
    order = {
        "intent_id": "2026-05-26:pre-market:MU:abc123",
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
        "status": "filled",
        "filled_quantity": 200,
        "avg_fill_price": 100.2,
        "remaining_quantity": 200,
        "protective_stop_order_id": "stop-o-1",
        "stop_status": "accepted",
        "take_profit_order_id": "tp-o-1",
        "take_profit_status": "accepted",
        "lifecycle": {"overall_status": "open_protected", "remaining_quantity": 200},
    }
    order.update(order_overrides)
    return {"schema_version": "paper-execution-state/v2", "date": "2026-05-26", "orders": [order]}


def intraday_state(state: str = "invalidated") -> dict:
    return {
        "date": "2026-05-26",
        "workflow": "intraday-tracker",
        "symbols": {
            "MU": {
                "symbol": "MU",
                "state": state,
                "reason": "跌破盘前失效位",
                "bar_timestamp": "2026-05-26 15:45:00",
                "invalidation_price": 95,
            }
        },
    }


def exit_decisions(*, execution_status: str = "conditional_executable", action: str = "exit_remaining") -> dict:
    return {
        "date": "2026-05-26",
        "workflow": "paper-exit-decision",
        "decisions": [
            {
                "intent_id": "2026-05-26:pre-market:MU:abc123",
                "symbol": "MU",
                "action": action,
                "execution_status": execution_status,
                "reason": "5m lower-high breakdown with failed reclaim",
                "risk_check": {
                    "remaining_quantity": 200,
                    "cancel_open_exits_first": True,
                    "max_loss_if_exit_now_r": 1.1,
                },
                "evidence": ["runtime/intraday/2026-05-26/state.json", "report/latest-monitor.json"],
            }
        ],
    }


class PaperExitPlanTest(unittest.TestCase):
    def seed(self, root: Path, *, state: str = "invalidated", order_overrides: dict | None = None) -> None:
        write_json(
            root / "runtime" / "paper" / "2026-05-26" / "paper-execution-state.json",
            execution_state(**(order_overrides or {})),
        )
        write_json(root / "runtime" / "intraday" / "2026-05-26" / "state.json", intraday_state(state))

    def test_dry_run_builds_market_exit_for_invalidated_open_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            result = paper_exit_plan.run(paper_exit_plan.build_args(repo_root=str(root), date="2026-05-26"))

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["summary"]["exit_candidates"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            candidate = payload["exit_candidates"][0]
            self.assertEqual(candidate["symbol"], "MU")
            self.assertEqual(candidate["side"], "sell")
            self.assertEqual(candidate["order_type"], "MO")
            self.assertEqual(candidate["quantity"], 200)
            self.assertEqual(candidate["exit_reason"], "intraday_plan_invalidated")
            self.assertEqual(
                [step["action"] for step in candidate["preview_steps"]],
                ["cancel_existing_stop", "cancel_existing_take_profit", "submit_exit_order"],
            )
            self.assertIn("--order-type", candidate["preview_steps"][-1]["command"])
            self.assertNotIn("--price", candidate["preview_steps"][-1]["command"])

    def test_ignores_waiting_state_and_closed_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root, state="waiting")

            waiting = paper_exit_plan.run(paper_exit_plan.build_args(repo_root=str(root), date="2026-05-26"))
            self.assertEqual(waiting["summary"]["exit_candidates"], 0)

            self.seed(root, state="invalidated", order_overrides={"lifecycle": {"overall_status": "closed"}, "remaining_quantity": 0})
            closed = paper_exit_plan.run(paper_exit_plan.build_args(repo_root=str(root), date="2026-05-26"))
            self.assertEqual(closed["summary"]["exit_candidates"], 0)

    def test_llm_exit_decision_can_trigger_exit_before_hard_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root, state="waiting")
            decisions_path = root / "report" / "2026-05-26" / "paper-exit-decisions.json"
            write_json(decisions_path, exit_decisions())

            result = paper_exit_plan.run(
                paper_exit_plan.build_args(
                    repo_root=str(root),
                    date="2026-05-26",
                    decisions=str(decisions_path),
                )
            )

            self.assertEqual(result["summary"]["exit_candidates"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            candidate = payload["exit_candidates"][0]
            self.assertEqual(candidate["exit_reason"], "llm_exit_decision")
            self.assertEqual(candidate["decision_reason"], "5m lower-high breakdown with failed reclaim")
            self.assertEqual(candidate["decision_execution_status"], "conditional_executable")

    def test_llm_exit_decision_must_be_complete_and_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root, state="waiting")
            decisions_path = root / "report" / "2026-05-26" / "paper-exit-decisions.json"
            write_json(decisions_path, exit_decisions(execution_status="watch_only"))

            result = paper_exit_plan.run(
                paper_exit_plan.build_args(
                    repo_root=str(root),
                    date="2026-05-26",
                    decisions=str(decisions_path),
                )
            )

            self.assertEqual(result["summary"]["exit_candidates"], 0)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertIn("exit decision is not conditional_executable", {row["reason"] for row in payload["blocked"]})

    def test_execute_cancels_open_exits_then_submits_exit_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)
            adapter = unittest.mock.Mock()
            adapter.cancel_order.side_effect = [
                {"broker_order_id": "stop-o-1", "account_channel": "lb_papertrading", "raw_request": {}, "raw_response": {}},
                {"broker_order_id": "tp-o-1", "account_channel": "lb_papertrading", "raw_request": {}, "raw_response": {}},
            ]
            adapter.submit_order.return_value = {
                "broker_order_id": "exit-o-1",
                "account_channel": "lb_papertrading",
                "raw_request": {"command": ["order", "sell", "MU.US"]},
                "raw_response": {"order_id": "exit-o-1"},
            }

            with patch.object(paper_exit_plan, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_exit_plan.run(
                    paper_exit_plan.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["summary"]["submitted"], 1)
            self.assertEqual(adapter.cancel_order.call_count, 2)
            adapter.cancel_order.assert_any_call("stop-o-1", execute=True, action="exit_cancel_replace")
            adapter.cancel_order.assert_any_call("tp-o-1", execute=True, action="exit_cancel_replace")
            exit_intent = adapter.submit_order.call_args.args[0]
            self.assertEqual(exit_intent["side"], "sell")
            self.assertEqual(exit_intent["order_type"], "MO")
            self.assertEqual(adapter.submit_order.call_args.kwargs, {"execute": True, "action": "exit_submit"})

            exit_path = root / "runtime" / "paper" / "2026-05-26" / "paper-exit-orders.jsonl"
            record = json.loads(exit_path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["kind"], "paper_exit_order")
            self.assertEqual(record["broker_order_id"], "exit-o-1")
            self.assertEqual(record["exit_reason"], "intraday_plan_invalidated")
            self.assertEqual(record["quantity"], 200)


if __name__ == "__main__":
    unittest.main()
