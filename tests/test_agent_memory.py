import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from agent_memory import append_memory, export_sqlite, review_memory


def decision(path: Path) -> Path:
    payload = {
        "decision_id": "2026-05-26:agent-decision:MU",
        "date": "2026-05-26",
        "symbol": "MU",
        "decision_label": "watch_only",
        "plan_type": "watch_only",
        "execution_status": "watch_only",
        "evidence_ids": ["m1", "t1"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class AgentMemoryTest(unittest.TestCase):
    def run_wrapper(self, *args):
        proc = subprocess.run(
            [sys.executable, "script/trading_copilot.py", *args],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
        return json.loads(proc.stdout)

    def test_append_memory_is_idempotent_by_decision_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "trading_memory.md"
            decision_path = decision(root / "decision.json")
            args = Namespace(
                decision=str(decision_path),
                memory_path=str(memory),
                outcome_status="not_triggered",
                reflection="Kept as watch only.",
            )

            first = append_memory(args)
            second = append_memory(args)

            self.assertTrue(first["appended"])
            self.assertFalse(second["appended"])
            text = memory.read_text(encoding="utf-8")
            self.assertEqual(text.count("2026-05-26:agent-decision:MU"), 2)

    def test_review_memory_is_read_only_and_cannot_raise_execution_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "trading_memory.md"
            decision_path = decision(root / "decision.json")
            append_memory(
                Namespace(
                    decision=str(decision_path),
                    memory_path=str(memory),
                    outcome_status="not_triggered",
                    reflection="Review required next time.",
                )
            )

            result = review_memory(Namespace(memory_path=str(memory), date="2026-05-26", symbol=["MU"], output=None))

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["read_only"])
            self.assertEqual(result["execution_status_effect"], "cannot_raise")
            self.assertEqual(len(result["memory_matches"]), 1)

    def test_export_sqlite_rebuilds_memory_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "trading_memory.md"
            sqlite_path = root / "trading_memory.sqlite"
            decision_path = decision(root / "decision.json")
            append_memory(
                Namespace(
                    decision=str(decision_path),
                    memory_path=str(memory),
                    outcome_status="not_triggered",
                    reflection="Kept as watch only.",
                )
            )

            result = export_sqlite(Namespace(memory_path=str(memory), sqlite_output=str(sqlite_path)))

            self.assertEqual(result["rows"], 1)
            with sqlite3.connect(sqlite_path) as conn:
                rows = conn.execute("select decision_id, symbol from memories").fetchall()
            self.assertEqual(rows, [("2026-05-26:agent-decision:MU", "MU")])

    def test_wrapper_agent_memory_review_reads_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "trading_memory.md"
            output = root / "review.json"
            decision_path = decision(root / "decision.json")
            append_memory(
                Namespace(
                    decision=str(decision_path),
                    memory_path=str(memory),
                    outcome_status="not_triggered",
                    reflection="Review required.",
                )
            )

            payload = self.run_wrapper(
                "agent-memory-review",
                "--date",
                "2026-05-26",
                "--symbol",
                "MU",
                "--memory-path",
                str(memory),
                "--output",
                str(output),
            )

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["summary"]["matches"], 1)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
