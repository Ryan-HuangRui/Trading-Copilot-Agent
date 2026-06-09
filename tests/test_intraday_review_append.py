import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import intraday_review_append


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class IntradayReviewAppendTest(unittest.TestCase):
    def args(self, root: Path, **overrides):
        defaults = {
            "repo_root": str(root),
            "date": "2026-05-26",
            "signals": None,
            "submission": None,
            "context": None,
            "markdown": None,
            "timezone": "America/New_York",
            "as_of": "2026-05-26T14:45:00+00:00",
            "max_notes_chars": 120,
        }
        defaults.update(overrides)
        return Namespace(**defaults)

    def seed_signals(self, root: Path) -> None:
        write_json(
            root / "report" / "2026-05-26" / "monitor-signals.json",
            {
                "date": "2026-05-26",
                "session": "monitor",
                "source_report": "report/latest-monitor.json",
                "signals": [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "plan_type": "watch_only",
                        "execution_status": "watch_only",
                        "trigger": {"price": 101.2},
                        "invalidation": {"price": 98.1},
                        "entry": {"trigger_price": 101.2, "order_type": "LO"},
                        "stop": {"initial_stop": 98.1},
                        "take_profit": {"tp1": None},
                        "risk": {"risk_per_share": 3.1, "text": "缺少 TP1，RR 不可验证"},
                        "notes": "Codex review: near trigger but still watch_only",
                    },
                    {
                        "symbol": "AMD",
                        "setup": "strong_breakout_trend_following.md",
                        "plan_type": "trade_plan",
                        "execution_status": "conditional_executable",
                        "trigger": {"price": 180.0},
                        "invalidation": {"price": 176.0},
                        "entry": {"trigger_price": 180.0, "order_type": "LO"},
                        "stop": {"initial_stop": 176.0},
                        "take_profit": {"tp1": 188.5},
                        "risk": {"risk_per_share": 4.0, "text": "RR >= 2"},
                        "notes": "Codex review: complete monitor Trade Plan Card",
                    },
                ],
            },
        )
        write_json(
            root / "report" / "2026-05-26" / "paper-trade-submission.json",
            {
                "dry_run": True,
                "summary": {"total": 2, "ready": 1, "submitted": 0, "blocked": 1, "errors": 0},
            },
        )
        write_json(
            root / "report" / "2026-05-26" / "intraday-opportunity-context.json",
            {"summary": {"observation_scans": 6, "deterministic_candidate_scans": 2, "template_signals": 6}},
        )

    def test_appends_codex_review_section_to_intraday_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_signals(root)
            markdown = root / "report" / "2026-05-26" / "intraday.md"
            markdown.parent.mkdir(parents=True, exist_ok=True)
            markdown.write_text("# 2026-05-26 Intraday Tracker\n\n## 10:40 EDT\n- MU: waiting\n", encoding="utf-8")

            result = intraday_review_append.run(self.args(root))

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["summary"]["signals"], 2)
            self.assertEqual(result["summary"]["conditional_executable"], 1)
            text = markdown.read_text(encoding="utf-8")
            self.assertIn("## 10:45 EDT Codex 机会评审", text)
            self.assertIn("- 汇总：signals=2；conditional_executable=1；watch_only=1；no_trade=0", text)
            self.assertIn("- Codex 输入：observation_scans=6；deterministic_candidate_scans=2；template_signals=6", text)
            self.assertIn("- dry-run：ready=1；submitted=0；blocked=1；errors=0", text)
            self.assertIn("- MU: watch_only / watch_only", text)
            self.assertIn("- AMD: trade_plan / conditional_executable", text)
            self.assertIn("本段为 Codex 盘中评审记录", text)

    def test_missing_signals_file_skips_without_creating_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = intraday_review_append.run(self.args(root))

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "signals file missing")
            self.assertFalse((root / "report" / "2026-05-26" / "intraday.md").exists())


if __name__ == "__main__":
    unittest.main()
