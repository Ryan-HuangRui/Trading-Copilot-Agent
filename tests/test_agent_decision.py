import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from agent_decision import run as run_decision
from validate_agent_decision import validate


def write_report_set(root: Path) -> None:
    symbol_dir = root / "MU"
    symbol_dir.mkdir(parents=True)
    for report_type, evidence_id, source_type in [
        ("market", "m1", "market_data"),
        ("technicals", "t1", "technical_indicator"),
        ("fundamentals", "f1", "fundamentals"),
        ("news", "n1", "news"),
        ("sentiment", "s1", "sentiment"),
    ]:
        (symbol_dir / f"{report_type}_report.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "report_type": report_type,
                    "date": "2026-05-26",
                    "symbol": "MU",
                    "evidence": [
                        {
                            "evidence_id": evidence_id,
                            "source": "fixture",
                            "source_type": source_type,
                            "as_of": "2026-05-26",
                            "symbol": "MU",
                            "summary": f"{report_type} evidence",
                            "confidence": 0.8,
                            "limitations": [],
                        }
                    ],
                    "facts": [],
                    "derived_metrics": {"rsi_14": 61} if report_type == "technicals" else {},
                    "scores": {},
                    "limitations": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


class AgentDecisionTest(unittest.TestCase):
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

    def test_run_generates_role_reports_and_watch_only_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp) / "agents"
            output_dir = Path(tmp) / "out"
            write_report_set(reports_dir)

            result = run_decision(
                Namespace(
                    date="2026-05-26",
                    symbol=["MU"],
                    reports_dir=str(reports_dir),
                    output_dir=str(output_dir),
                    memory=None,
                    repo_root=str(ROOT),
                )
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(len(result["artifacts"]), 5)
            decision = json.loads((output_dir / "MU" / "decision.json").read_text(encoding="utf-8"))
            self.assertEqual(decision["plan_type"], "watch_only")
            self.assertEqual(decision["execution_status"], "watch_only")
            self.assertIn("m1", decision["evidence_ids"])
            self.assertNotIn("broker_command", decision)

            validation = validate(
                Namespace(date="2026-05-26", symbol=["MU"], decision_dir=str(output_dir), repo_root=str(ROOT))
            )
            self.assertEqual(validation["status"], "pass")

    def test_wrapper_generates_and_validates_agent_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp) / "agents"
            output_dir = Path(tmp) / "out"
            write_report_set(reports_dir)

            generated = self.run_wrapper(
                "agent-decision",
                "--date",
                "2026-05-26",
                "--symbol",
                "MU",
                "--reports-dir",
                str(reports_dir),
                "--output-dir",
                str(output_dir),
            )
            self.assertEqual(len(generated["artifacts"]), 5)

            validated = self.run_wrapper(
                "validate-agent-decision",
                "--date",
                "2026-05-26",
                "--symbol",
                "MU",
                "--decision-dir",
                str(output_dir),
            )
            self.assertEqual(validated["validation"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
