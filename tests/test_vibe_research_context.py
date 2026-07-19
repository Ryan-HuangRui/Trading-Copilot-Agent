import hashlib
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import trading_copilot
from vibe_research_context import run


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class VibeResearchContextTest(unittest.TestCase):
    def args(self, root: Path, **overrides):
        values = {
            "date": "2026-07-18",
            "session": "post-market",
            "run_id": "swarm-123",
            "target": "NVDA.US",
            "symbol": ["NVDA.US"],
            "objective": "Audit the demand thesis",
            "as_of": "2026-07-18",
            "status": "completed",
            "result": "report/research/vibe-swarm/swarm-123/result.json",
            "summary": "report/research/vibe-swarm/swarm-123/summary.md",
            "provider": "openai",
            "model": "test-model",
            "quality_label": "RESEARCH_ONLY",
            "confidence": 0.6,
            "limitation": ["Fixture research only."],
            "output": None,
            "repo_root": str(root),
        }
        values.update(overrides)
        return Namespace(**values)

    def write_artifacts(self, root: Path) -> tuple[Path, Path]:
        run_dir = root / "report" / "research" / "vibe-swarm" / "swarm-123"
        run_dir.mkdir(parents=True)
        result = run_dir / "result.json"
        summary = run_dir / "summary.md"
        result.write_text('{"status":"completed"}', encoding="utf-8")
        summary.write_text("# Research summary\n", encoding="utf-8")
        return result, summary

    def test_completed_run_writes_idempotent_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, summary = self.write_artifacts(root)

            first = run(self.args(root))
            second = run(self.args(root, confidence=0.7))

            context_path = root / "report" / "2026-07-18" / "agents" / "vibe-research-context.json"
            context = json.loads(context_path.read_text(encoding="utf-8"))
            self.assertEqual(first["status"], "success")
            self.assertEqual(second["status"], "success")
            self.assertEqual(len(context["runs"]), 1)
            self.assertEqual(context["runs"][0]["confidence"], 0.7)
            self.assertEqual(context["runs"][0]["symbols"], ["NVDA"])
            self.assertTrue(context["runs"][0]["usable_as_agent_evidence"])
            self.assertEqual(context["runs"][0]["artifacts"][0]["sha256"], sha256(result))
            self.assertEqual(context["runs"][0]["artifacts"][1]["sha256"], sha256(summary))

    def test_completed_run_requires_persisted_result_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "completed runs require"):
                run(self.args(root, result=None, summary=None))

    def test_attach_adds_only_completed_hash_validated_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_artifacts(root)
            indexed = run(self.args(root))
            response = {
                "status": "success",
                "artifacts": [],
                "next_agent_inputs": [],
            }
            context_path = root / indexed["artifacts"][0]

            with patch.object(trading_copilot, "ROOT", root):
                trading_copilot.attach_vibe_research_inputs(
                    response=response,
                    date="2026-07-18",
                    session="post-market",
                    include=True,
                    explicit_path=str(context_path),
                )

            self.assertEqual(response["vibe_research"]["status"], "success")
            self.assertEqual(response["vibe_research"]["usable_runs"], 1)
            self.assertIn("report/2026-07-18/agents/vibe-research-context.json", response["next_agent_inputs"])
            self.assertIn("report/research/vibe-swarm/swarm-123/summary.md", response["next_agent_inputs"])

    def test_attach_keeps_pending_research_non_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(self.args(root, status="pending", result=None, summary=None))
            response = {
                "status": "success",
                "artifacts": [],
                "next_agent_inputs": [],
            }

            with patch.object(trading_copilot, "ROOT", root):
                trading_copilot.attach_vibe_research_inputs(
                    response=response,
                    date="2026-07-18",
                    session="pre-market",
                    include=True,
                    explicit_path=None,
                )

            self.assertEqual(response["status"], "success")
            self.assertEqual(response["vibe_research"]["status"], "skipped")
            self.assertEqual(response["vibe_research"]["pending_runs"], 1)
            self.assertEqual(response["next_agent_inputs"], [])


if __name__ == "__main__":
    unittest.main()
