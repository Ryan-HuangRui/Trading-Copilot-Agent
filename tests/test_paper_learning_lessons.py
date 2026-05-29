import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_learning_lessons


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def execution_review() -> dict:
    return {
        "date": "2026-05-26",
        "source_execution_state": "runtime/paper/2026-05-26/paper-execution-state.json",
        "reviews": [
            {
                "intent_id": "intent-1",
                "source_signal_id": "sig-1",
                "symbol": "MU",
                "setup": "breakout_pullback_continuation.md",
                "candidate_lessons": ["filled paper entry had no synced protective stop"],
            }
        ],
    }


class PaperLearningLessonsTest(unittest.TestCase):
    def seed(self, root: Path) -> None:
        write_json(root / "report" / "2026-05-26" / "paper-execution-review.json", execution_review())

    def test_extracts_and_appends_lessons_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            first = paper_learning_lessons.run(
                paper_learning_lessons.build_args(repo_root=str(root), date="2026-05-26", append=True)
            )
            second = paper_learning_lessons.run(
                paper_learning_lessons.build_args(repo_root=str(root), date="2026-05-26", append=True)
            )

            self.assertEqual(first["summary"]["appended"], 1)
            self.assertEqual(second["summary"]["appended"], 0)
            self.assertEqual(second["summary"]["skipped_duplicates"], 1)
            lessons_path = root / "runtime" / "learning" / "daily_lessons.jsonl"
            records = [json.loads(line) for line in lessons_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["lesson_type"], "paper_execution")
            self.assertEqual(records[0]["problem"], "missing_protective_stop")
            self.assertEqual(records[0]["status"], "candidate")

    def test_wrapper_exposes_paper_learning_lessons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-learning-lessons",
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                    "--append",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["workflow"], "paper-learning-lessons")
            self.assertEqual(payload["summary"]["appended"], 1)


if __name__ == "__main__":
    unittest.main()
