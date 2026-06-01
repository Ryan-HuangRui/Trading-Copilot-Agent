import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import focus_selection
import inspect_pre_market_context
import llm_generation_manifest


class FocusAndManifestToolsTest(unittest.TestCase):
    def test_focus_selection_merges_signal_and_agent_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-05-26"
            report_dir.mkdir(parents=True)
            signals = report_dir / "pre-market-signals.json"
            signals.write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "execution_status": "watch_only",
                                "plan_type": "watch_only",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            context = report_dir / "pre-market-context.json"
            context.write_text(
                json.dumps({"snapshot": {"symbols": [{"symbol": "MU"}, {"symbol": "NVDA"}]}}),
                encoding="utf-8",
            )
            decision_dir = report_dir / "agents" / "MU"
            decision_dir.mkdir(parents=True)
            (decision_dir / "decision.json").write_text(
                json.dumps(
                    {
                        "rank_score": 72,
                        "why_focus": "relative strength",
                        "why_not_executable": "needs intraday confirmation",
                    }
                ),
                encoding="utf-8",
            )

            output = report_dir / "focus-selection.json"
            result = focus_selection.run(
                Namespace(
                    date="2026-05-26",
                    session="pre-market",
                    signals=str(signals),
                    context=str(context),
                    snapshot=None,
                    agents_dir=None,
                    output=str(output),
                    max_nonfocus=20,
                    repo_root=str(root),
                )
            )

            self.assertEqual(result["status"], "success")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected"][0]["symbol"], "MU")
            self.assertEqual(payload["selected"][0]["rank_score"], 72)
            self.assertEqual(payload["rejected_or_nonfocus"][0]["symbol"], "NVDA")

    def test_inspect_pre_market_context_outputs_fixed_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-05-26"
            report_dir.mkdir(parents=True)
            context = report_dir / "pre-market-context.json"
            context.write_text(
                json.dumps(
                    {
                        "report_date": "2026-05-26",
                        "source_snapshot_date": "2026-05-22",
                        "snapshot": {
                            "symbols": [
                                {"symbol": "MU", "provider": "longbridge", "latest_date": "2026-05-22"},
                                {"symbol": "NVDA", "provider": "twelve", "error": "fallback"},
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = inspect_pre_market_context.run(
                Namespace(date="2026-05-26", context=str(context), output=None, repo_root=str(root))
            )

            self.assertEqual(result["symbol_count"], 2)
            self.assertEqual(result["provider_summary"], {"longbridge": 1, "twelve": 1})
            self.assertEqual(result["missing_symbols"], ["NVDA"])

    def test_llm_generation_manifest_hashes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "prompt.md"
            report = root / "report.md"
            prompt.write_text("prompt", encoding="utf-8")
            report.write_text("report", encoding="utf-8")
            output = root / "llm.json"

            result = llm_generation_manifest.run(
                Namespace(
                    date="2026-05-26",
                    session="pre-market",
                    model="gpt-test",
                    runner="codex",
                    prompt=str(prompt),
                    input=[str(prompt)],
                    generated_output=[str(report)],
                    notes="fixture",
                    output=str(output),
                    repo_root=str(root),
                )
            )

            self.assertEqual(result["status"], "success")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["model"], "gpt-test")
            self.assertTrue(payload["outputs"][0]["sha256"])


if __name__ == "__main__":
    unittest.main()
