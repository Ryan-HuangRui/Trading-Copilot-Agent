import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def lesson(date: str, symbol: str) -> dict:
    return {
        "kind": "daily_lesson",
        "created_at": f"{date}T00:00:00+00:00",
        "date": date,
        "lesson_type": "plan_quality",
        "symbol": symbol,
        "setup": "breakout_pullback_continuation.md",
        "problem": "missing_take_profit",
        "evidence": [f"{symbol}: missing_take_profit"],
        "suggested_constraint": "conditional_executable plans must include TP1 and minimum RR",
        "status": "candidate",
    }


class LearningReviewTest(unittest.TestCase):
    def test_learning_review_promotes_repeated_lessons_to_pattern_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_dir = root / "runtime" / "learning"
            learning_dir.mkdir(parents=True)
            lessons = [
                lesson("2026-05-24", "MU"),
                lesson("2026-05-25", "AMD"),
                lesson("2026-05-26", "TSM"),
            ]
            (learning_dir / "daily_lessons.jsonl").write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in lessons) + "\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "learning_review.py"),
                    "--repo-root",
                    str(root),
                    "--end-date",
                    "2026-05-26",
                    "--lookback-days",
                    "20",
                    "--min-count",
                    "3",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["pattern_candidates"], 1)
            candidate = payload["pattern_candidates"][0]
            self.assertEqual(candidate["problem"], "missing_take_profit")
            self.assertEqual(candidate["seen_count"], 3)
            self.assertEqual(candidate["promotion_status"], "needs_human_review")
            self.assertTrue((learning_dir / "pattern_candidates.jsonl").exists())
            markdown = (root / "report" / "learning" / "pattern-review.md").read_text(encoding="utf-8")
            self.assertIn("missing_take_profit", markdown)

    def test_learning_review_promotes_repeated_position_discipline_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = root / "runtime" / "journal"
            journal.mkdir(parents=True)
            records = []
            for date, symbol in (("2026-05-24", "TSLA"), ("2026-05-25", "SMCI"), ("2026-05-26", "NVDA")):
                records.append(
                    {
                        "kind": "position_review",
                        "position_review_id": f"position:{date}:{symbol}",
                        "date": date,
                        "symbol": symbol,
                        "in_today_signals": False,
                        "review_required": True,
                        "trade_link_state": "no_trade_record",
                        "risk_state": "not_in_plan",
                    }
                )
            (journal / "position_reviews.jsonl").write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "learning_review.py"),
                    "--repo-root",
                    str(root),
                    "--end-date",
                    "2026-05-26",
                    "--lookback-days",
                    "20",
                    "--min-count",
                    "3",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["position_reviews"], 3)
            candidate = payload["pattern_candidates"][0]
            self.assertEqual(candidate["problem"], "position_without_plan")
            self.assertEqual(candidate["setup"], "position_discipline")
            self.assertEqual(candidate["seen_count"], 3)
            self.assertIn("TSLA", candidate["symbols"])

    def test_promote_lesson_dry_run_and_apply_validated_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_dir = root / "runtime" / "learning"
            learning_dir.mkdir(parents=True)
            candidate = {
                "kind": "pattern_candidate",
                "pattern_id": "missing_take_profit__breakout_pullback_continuation",
                "problem": "missing_take_profit",
                "setup": "breakout_pullback_continuation.md",
                "lesson_type": "plan_quality",
                "seen_count": 3,
                "first_seen": "2026-05-24",
                "last_seen": "2026-05-26",
                "symbols": ["AMD", "MU", "TSM"],
                "suggested_constraint": "conditional_executable plans must include TP1 and minimum RR",
                "promotion_status": "needs_human_review",
            }
            (learning_dir / "pattern_candidates.jsonl").write_text(
                json.dumps(candidate, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            validated = root / "knowledge" / "evolution" / "validated_lessons.md"

            dry_run = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "promote_lesson.py"),
                    "--repo-root",
                    str(root),
                    "--pattern-id",
                    "missing_take_profit__breakout_pullback_continuation",
                    "--dry-run",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(dry_run.returncode, 0, msg=dry_run.stderr or dry_run.stdout)
            self.assertFalse(validated.exists())
            self.assertIn("validated_lesson", json.loads(dry_run.stdout)["markdown_block"])

            apply = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "promote_lesson.py"),
                    "--repo-root",
                    str(root),
                    "--pattern-id",
                    "missing_take_profit__breakout_pullback_continuation",
                    "--apply",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(apply.returncode, 0, msg=apply.stderr or apply.stdout)
            payload = json.loads(apply.stdout)
            self.assertTrue(payload["applied"])
            text = validated.read_text(encoding="utf-8")
            self.assertIn("missing_take_profit__breakout_pullback_continuation", text)
            self.assertIn("canonical rulebook", text)


if __name__ == "__main__":
    unittest.main()
