import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_order_replace


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def execution_state(**overrides) -> dict:
    order = {
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
        "status": "accepted",
        "filled_quantity": 0,
    }
    order.update(overrides)
    return {"schema_version": "paper-execution-state/v2", "date": "2026-05-26", "orders": [order]}


def replace_decisions(**overrides) -> dict:
    decision = {
        "intent_id": "intent-1",
        "symbol": "MU",
        "action": "replace_pending",
        "execution_status": "conditional_executable",
        "reason": "updated pullback limit after intraday structure tightened",
        "new_quantity": 150,
        "new_limit_price": 99.5,
        "risk_check": {
            "remaining_unfilled_quantity": 200,
            "max_account_risk_pct": 1,
            "risk_per_share": 4.5,
        },
        "evidence": ["report/latest-monitor.json"],
    }
    decision.update(overrides)
    return {"date": "2026-05-26", "workflow": "paper-order-replace-decision", "decisions": [decision]}


class PaperOrderReplaceTest(unittest.TestCase):
    def seed(self, root: Path, *, order_overrides: dict | None = None, decision_overrides: dict | None = None) -> tuple[Path, Path]:
        state_path = root / "runtime" / "paper" / "2026-05-26" / "paper-execution-state.json"
        decisions_path = root / "report" / "2026-05-26" / "paper-replace-decisions.json"
        write_json(state_path, execution_state(**(order_overrides or {})))
        write_json(decisions_path, replace_decisions(**(decision_overrides or {})))
        return state_path, decisions_path

    def test_dry_run_builds_replace_candidate_from_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            result = paper_order_replace.run(paper_order_replace.build_args(repo_root=str(root), date="2026-05-26"))

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["summary"]["replace_candidates"], 1)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            candidate = payload["replace_candidates"][0]
            self.assertEqual(candidate["broker_order_id"], "paper-o-1")
            self.assertEqual(candidate["new_quantity"], 150)
            self.assertEqual(candidate["new_limit_price"], 99.5)
            self.assertEqual(
                candidate["preview_command"],
                ["longbridge", "order", "replace", "paper-o-1", "--qty", "150", "--price", "99.5", "--format", "json"],
            )

    def test_blocks_non_pending_order_and_incomplete_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root, order_overrides={"status": "filled", "filled_quantity": 200})

            filled = paper_order_replace.run(paper_order_replace.build_args(repo_root=str(root), date="2026-05-26"))
            self.assertEqual(filled["summary"]["replace_candidates"], 0)
            payload = json.loads(Path(filled["output"]).read_text(encoding="utf-8"))
            self.assertIn("order status is not replaceable", {item["reason"] for item in payload["blocked"]})

            self.seed(root, order_overrides={"status": "accepted", "filled_quantity": 0}, decision_overrides={"execution_status": "watch_only"})
            watch_only = paper_order_replace.run(paper_order_replace.build_args(repo_root=str(root), date="2026-05-26"))
            self.assertEqual(watch_only["summary"]["replace_candidates"], 0)
            payload = json.loads(Path(watch_only["output"]).read_text(encoding="utf-8"))
            self.assertIn("replace decision is not conditional_executable", {item["reason"] for item in payload["blocked"]})

    def test_execute_replaces_candidate_and_appends_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)
            adapter = unittest.mock.Mock()
            adapter.replace_order.return_value = {
                "broker": "longbridge",
                "account_channel": "lb_papertrading",
                "broker_order_id": "paper-o-1",
                "raw_request": {"command": ["order", "replace", "paper-o-1"]},
                "raw_response": {"order_id": "paper-o-1", "status": "replaced"},
            }

            with patch.object(paper_order_replace, "LongbridgePaperOrderAdapter", return_value=adapter):
                result = paper_order_replace.run(
                    paper_order_replace.build_args(repo_root=str(root), date="2026-05-26", execute=True)
                )

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["summary"]["replaced"], 1)
            adapter.replace_order.assert_called_once_with("paper-o-1", quantity=150, limit_price=99.5, execute=True)
            journal = root / "runtime" / "paper" / "2026-05-26" / "paper-replace-orders.jsonl"
            record = json.loads(journal.read_text(encoding="utf-8").strip())
            self.assertEqual(record["kind"], "paper_replace_order")
            self.assertEqual(record["broker_order_id"], "paper-o-1")
            self.assertEqual(record["new_quantity"], 150)
            self.assertEqual(record["new_limit_price"], 99.5)


if __name__ == "__main__":
    unittest.main()
