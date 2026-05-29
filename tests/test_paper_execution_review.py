import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_execution_review


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def preview() -> dict:
    return {
        "date": "2026-05-26",
        "session": "pre-market",
        "orders": [
            {
                "signal_id": "sig-1",
                "symbol": "MU",
                "setup": "breakout_pullback_continuation.md",
                "side": "buy",
                "status": "ready",
                "quantity": 200,
                "entry_price": 100,
                "stop_price": 95,
                "take_profit": 112,
                "risk_per_share": 5,
            }
        ],
    }


def execution_state(**entry_overrides) -> dict:
    entry = {
        "intent_id": "intent-1",
        "source_signal_id": "sig-1",
        "symbol": "MU",
        "setup": "breakout_pullback_continuation.md",
        "side": "buy",
        "quantity": 200,
        "broker_order_id": "entry-o-1",
        "status": "filled",
        "filled_quantity": 200,
        "avg_fill_price": 100.2,
        "limit_price": 100,
        "stop_price": 95,
        "take_profit": 112,
        "protective_stop_order_id": "stop-o-1",
        "stop_status": "accepted",
        "take_profit_order_id": "tp-o-1",
        "tp1_status": "filled",
        "tp1_filled_quantity": 100,
        "remaining_quantity": 100,
    }
    entry.update(entry_overrides)
    return {
        "date": "2026-05-26",
        "orders": [entry],
        "protective_stops": [
            {
                "kind": "paper_stop_order",
                "intent_id": "intent-1",
                "broker_order_id": "stop-o-1",
                "status": "accepted",
                "trigger_price": 95,
                "filled_quantity": 0,
            }
        ],
        "take_profit_orders": [
            {
                "kind": "paper_take_profit_order",
                "intent_id": "intent-1",
                "broker_order_id": "tp-o-1",
                "status": "filled",
                "filled_quantity": 100,
                "avg_fill_price": 112.1,
            }
        ],
    }


class PaperExecutionReviewTest(unittest.TestCase):
    def seed(self, root: Path, state: dict | None = None) -> None:
        write_json(root / "report" / "2026-05-26" / "paper-trade-preview.json", preview())
        write_json(root / "runtime" / "paper" / "2026-05-26" / "paper-execution-state.json", state or execution_state())

    def test_review_writes_json_and_markdown_with_result_r(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            result = paper_execution_review.run(paper_execution_review.build_args(repo_root=str(root), date="2026-05-26"))

            self.assertEqual(result["summary"]["tp1_filled"], 1)
            self.assertEqual(result["summary"]["average_result_r"], 2.38)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            review = payload["reviews"][0]
            self.assertEqual(review["plan_adherence"], "passed")
            self.assertEqual(review["risk_discipline"], "passed")
            self.assertEqual(review["result_r"], 2.38)
            self.assertEqual(review["planned_rr"], 2.4)
            markdown = Path(result["markdown"]).read_text(encoding="utf-8")
            self.assertIn("Paper Execution Review", markdown)
            self.assertIn("Average result R: 2.38", markdown)

    def test_review_flags_missing_protective_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = execution_state(protective_stop_order_id=None, stop_status=None)
            state["protective_stops"] = []
            self.seed(root, state)

            result = paper_execution_review.run(paper_execution_review.build_args(repo_root=str(root), date="2026-05-26"))

            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            review = payload["reviews"][0]
            self.assertEqual(review["risk_discipline"], "missing_protective_stop")
            self.assertIn("filled paper entry had no synced protective stop", review["candidate_lessons"])

    def test_wrapper_exposes_paper_execution_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-execution-review",
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
            self.assertEqual(payload["workflow"], "paper-execution-review")
            self.assertEqual(payload["summary"]["tp1_filled"], 1)
            self.assertEqual(len(payload["artifacts"]), 2)


if __name__ == "__main__":
    unittest.main()
