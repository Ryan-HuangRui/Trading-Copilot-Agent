import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import intraday_lifecycle_append


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class IntradayLifecycleAppendTest(unittest.TestCase):
    def args(self, root: Path, **overrides):
        defaults = {
            "repo_root": str(root),
            "date": "2026-05-26",
            "markdown": None,
            "output": None,
            "timezone": "America/New_York",
            "as_of": "2026-05-26T15:05:00+00:00",
        }
        defaults.update(overrides)
        return Namespace(**defaults)

    def seed_artifacts(self, root: Path) -> None:
        report = root / "report" / "2026-05-26"
        runtime = root / "runtime" / "paper" / "2026-05-26"
        write_json(runtime / "paper-execution-state.json", {"summary": {"submitted": 1, "filled": 1, "cancelled": 0}})
        write_json(report / "paper-order-cancel-plan.json", {"dry_run": False, "summary": {"cancel_candidates": 1, "cancelled": 1, "errors": 0}})
        write_json(report / "paper-protective-stop-plan.json", {"dry_run": False, "summary": {"stop_candidates": 1, "submitted": 1, "errors": 0}})
        write_json(report / "paper-take-profit-plan.json", {"dry_run": True, "summary": {"take_profit_candidates": 1, "submitted": 0, "blocked": 1}})
        write_json(report / "paper-exit-plan.json", {"dry_run": True, "summary": {"exit_candidates": 1, "submitted": 0, "blocked": 1}})
        write_json(report / "paper-break-even-stop-plan.json", {"dry_run": True, "summary": {"move_candidates": 1, "moved": 0, "blocked": 1}})
        write_json(report / "paper-event-ledger.json", {"summary": {"events": 3}})
        write_json(report / "paper-execution-review.json", {"summary": {"orders": 1, "fills": 1}})

    def test_appends_lifecycle_section_and_summary_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_artifacts(root)
            markdown = root / "report" / "2026-05-26" / "intraday.md"
            markdown.parent.mkdir(parents=True, exist_ok=True)
            markdown.write_text("# 2026-05-26 Intraday Tracker\n\n## 10:55 EDT\n- MU: triggered\n", encoding="utf-8")

            result = intraday_lifecycle_append.run(self.args(root))

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["should_notify"])
            self.assertEqual(result["summary"]["cancel_candidates"], 1)
            self.assertEqual(result["summary"]["protective_stop_submitted"], 1)
            self.assertEqual(result["summary"]["exit_candidates"], 1)
            text = markdown.read_text(encoding="utf-8")
            self.assertIn("## 11:05 EDT 模拟盘生命周期", text)
            self.assertIn("订单同步：submitted=1；filled=1；cancelled=0", text)
            self.assertIn("撤单：candidates=1；cancelled=1；errors=0", text)
            self.assertIn("保护止损：candidates=1；submitted=1；errors=0", text)
            self.assertIn("完整退出：candidates=1；submitted=0；blocked=1", text)
            self.assertIn("本段为模拟盘生命周期审计记录", text)
            summary_path = root / "report" / "2026-05-26" / "intraday-lifecycle-summary.json"
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["summary"]["break_even_move_candidates"], 1)

    def test_missing_lifecycle_artifacts_skips_without_creating_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = intraday_lifecycle_append.run(self.args(root))

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "lifecycle artifacts missing")
            self.assertFalse((root / "report" / "2026-05-26" / "intraday.md").exists())


if __name__ == "__main__":
    unittest.main()
