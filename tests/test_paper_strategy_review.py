import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_strategy_review


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def execution_review(date: str, result_r: float, symbol: str = "MU", setup: str = "breakout_pullback_continuation.md") -> dict:
    return {
        "date": date,
        "summary": {"total": 1},
        "reviews": [
            {
                "intent_id": f"{date}:{symbol}",
                "source_signal_id": f"sig-{date}",
                "symbol": symbol,
                "setup": setup,
                "side": "buy",
                "status": "filled",
                "outcome": "tp1_filled" if result_r > 0 else "stop_filled",
                "result_r": result_r,
                "slippage_pct": 0.2,
                "plan_adherence": "passed",
                "candidate_lessons": [] if result_r > 0 else ["review false trigger"],
            }
        ],
    }


class PaperStrategyReviewTest(unittest.TestCase):
    def seed(self, root: Path) -> None:
        write_json(root / "report" / "2026-05-26" / "paper-execution-review.json", execution_review("2026-05-26", 2.38))
        write_json(root / "report" / "2026-05-27" / "paper-execution-review.json", execution_review("2026-05-27", -1.0))

    def test_strategy_review_aggregates_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            result = paper_strategy_review.run(paper_strategy_review.build_args(repo_root=str(root)))

            self.assertEqual(result["summary"]["reviews"], 2)
            self.assertEqual(result["summary"]["average_r"], 0.69)
            payload = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            setup = payload["by_setup"][0]
            self.assertEqual(setup["planned_count"], 2)
            self.assertEqual(setup["filled_count"], 2)
            self.assertEqual(setup["average_r"], 0.69)
            self.assertEqual(setup["median_r"], 0.69)
            self.assertEqual(setup["win_rate_pct"], 50.0)
            self.assertEqual(setup["candidate_lessons"], 1)
            markdown = Path(result["markdown"]).read_text(encoding="utf-8")
            self.assertIn("Paper Strategy Review", markdown)

    def test_wrapper_exposes_strategy_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-strategy-review",
                    "--repo-root",
                    str(root),
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["workflow"], "paper-strategy-review")
            self.assertEqual(payload["summary"]["reviews"], 2)
            self.assertEqual(len(payload["artifacts"]), 2)


if __name__ == "__main__":
    unittest.main()
