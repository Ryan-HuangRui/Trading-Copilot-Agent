import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentResearchContractTest(unittest.TestCase):
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

    def test_agent_research_context_skeleton_writes_status_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "research-context.json"

            payload = self.run_wrapper(
                "agent-research-context",
                "--date",
                "2026-05-26",
                "--symbol",
                "mu",
                "--symbol",
                "NVDA",
                "--output",
                str(output),
            )

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["workflow"], "agent-research-context")
            self.assertEqual(payload["date"], "2026-05-26")
            self.assertFalse(payload["skipped"])
            self.assertEqual(payload["artifacts"], [str(output)])

            artifact = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(artifact["schema_version"], 1)
            self.assertEqual(artifact["workflow"], "agent-research-context")
            self.assertEqual(artifact["symbols"], ["MU", "NVDA"])
            self.assertTrue(artifact["experimental"])

    def test_agent_research_reports_skeleton_writes_per_symbol_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "agents"

            payload = self.run_wrapper(
                "agent-research-reports",
                "--date",
                "2026-05-26",
                "--symbol",
                "MU",
                "--placeholder",
                "--output-dir",
                str(output_dir),
            )

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["workflow"], "agent-research-reports")
            self.assertEqual(payload["date"], "2026-05-26")
            self.assertEqual(len(payload["artifacts"]), 5)

            report = json.loads((output_dir / "MU" / "market_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["report_type"], "market")
            self.assertEqual(report["symbol"], "MU")
            self.assertTrue(report["experimental"])
            self.assertEqual(report["evidence"], [])

    def test_agent_decision_skeleton_marks_placeholder_not_for_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "agents"

            payload = self.run_wrapper(
                "agent-decision",
                "--date",
                "2026-05-26",
                "--symbol",
                "MU",
                "--output-dir",
                str(output_dir),
            )

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["workflow"], "agent-decision")
            self.assertEqual(len(payload["artifacts"]), 2)

            decision = json.loads((output_dir / "MU" / "decision.json").read_text(encoding="utf-8"))
            self.assertTrue(decision["experimental"])
            self.assertTrue(decision["not_for_execution"])
            self.assertEqual(decision["plan_type"], "no_trade")
            self.assertEqual(decision["execution_status"], "no_trade")
            self.assertNotIn("order", decision)
            self.assertNotIn("broker_command", decision)

    def test_agent_memory_review_skeleton_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "memory-review.json"

            payload = self.run_wrapper(
                "agent-memory-review",
                "--date",
                "2026-05-26",
                "--symbol",
                "MU",
                "--output",
                str(output),
            )

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["workflow"], "agent-memory-review")
            self.assertEqual(payload["artifacts"], [str(output)])
            review = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(review["read_only"])
            self.assertEqual(review["memory_matches"], [])


if __name__ == "__main__":
    unittest.main()
