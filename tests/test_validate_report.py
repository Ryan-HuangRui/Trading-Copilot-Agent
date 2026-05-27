import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from validate_report import validate


GOOD_REPORT = """# 今日盘前完整报告（2026-05-26）

## 总览
- 今日最多3个重点标的：MU

## 重点执行候选
### MU
- 参考 setup：breakout_pullback_continuation.md
- 触发条件：突破 100 后回踩站稳。
- 主场景：只作为候选观察，不是交易指令。
- 备选场景：跌回区间中部则等待。
- 失效条件：跌破 95。
- 风险约束：单笔风险 <=1%；止损过宽则放弃。
"""


class ValidateReportTest(unittest.TestCase):
    def make_repo(self, report_text):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        setup_dir = root / "knowledge" / "refined" / "setups"
        setup_dir.mkdir(parents=True)
        (setup_dir / "breakout_pullback_continuation.md").write_text("# setup\n", encoding="utf-8")
        report_dir = root / "report" / "2026-05-26"
        report_dir.mkdir(parents=True)
        (report_dir / "pre-market.md").write_text(report_text, encoding="utf-8")
        (report_dir / "exec-brief.md").write_text(report_text, encoding="utf-8")
        return temp, root

    def validate_repo(self, root):
        args = argparse.Namespace(
            repo_root=str(root),
            date="2026-05-26",
            session="pre-market",
            report="report/2026-05-26/pre-market.md",
        )
        return validate(args)

    def write_sidecar(self, root, signals):
        sidecar = {
            "date": "2026-05-26",
            "session": "pre-market",
            "source_report": "report/2026-05-26/exec-brief.md",
            "signals": signals,
        }
        (root / "report" / "2026-05-26" / "signals.json").write_text(
            json.dumps(sidecar, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_passes_good_report(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["errors"], [])

    def test_fails_missing_setup_and_invalidation(self):
        bad_report = """# 报告
### MU
- 触发条件：突破 100。
- 风险约束：单笔风险 <=1%。
"""
        temp, root = self.make_repo(bad_report)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("missing setup" in error for error in payload["errors"]))
        self.assertTrue(any("invalidation" in error for error in payload["errors"]))

    def test_fails_unknown_setup(self):
        report = GOOD_REPORT.replace("breakout_pullback_continuation.md", "missing_setup.md")
        temp, root = self.make_repo(report)
        with temp:
            payload = self.validate_repo(root)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("does not exist" in error for error in payload["errors"]))

    def test_default_session_requires_and_validates_signals_sidecar(self):
        temp, root = self.make_repo(GOOD_REPORT.replace("今日盘前完整报告", "今日盘前执行简版"))
        with temp:
            self.write_sidecar(
                root,
                [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                        "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                        "risk": {"max_risk_pct": 1},
                        "status": "planned",
                    }
                ],
            )
            args = argparse.Namespace(
                repo_root=str(root),
                date="2026-05-26",
                session="pre-market",
                report=None,
                signals=None,
            )
            payload = validate(args)

        self.assertEqual(payload["status"], "pass")
        self.assertTrue(payload["checked_signals"].endswith("signals.json"))

    def test_default_session_fails_missing_signals_sidecar(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            args = argparse.Namespace(
                repo_root=str(root),
                date="2026-05-26",
                session="pre-market",
                report=None,
                signals=None,
            )
            payload = validate(args)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("missing structured signal sidecar" in error for error in payload["errors"]))

    def test_actionable_sidecar_requires_trigger_price(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            self.write_sidecar(
                root,
                [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "trigger": {"type": "break_above", "text": "突破 100"},
                        "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                        "risk": {"max_risk_pct": 1},
                        "status": "planned",
                    }
                ],
            )
            args = argparse.Namespace(repo_root=str(root), date="2026-05-26", session="pre-market", report=None, signals=None)
            payload = validate(args)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("trigger.price" in error for error in payload["errors"]))

    def test_actionable_sidecar_requires_invalidation_price(self):
        temp, root = self.make_repo(GOOD_REPORT)
        with temp:
            self.write_sidecar(
                root,
                [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                        "invalidation": {"type": "break_below", "text": "跌破 95"},
                        "risk": {"max_risk_pct": 1},
                        "status": "planned",
                    }
                ],
            )
            args = argparse.Namespace(repo_root=str(root), date="2026-05-26", session="pre-market", report=None, signals=None)
            payload = validate(args)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("invalidation.price" in error for error in payload["errors"]))

    def test_sidecar_symbols_must_match_markdown_focus_list_exactly(self):
        report = GOOD_REPORT.replace("今日最多3个重点标的：MU", "今日最多3个重点标的：MU、NVDA")
        temp, root = self.make_repo(report)
        with temp:
            self.write_sidecar(
                root,
                [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                        "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                        "risk": {"max_risk_pct": 1},
                        "status": "planned",
                    }
                ],
            )
            args = argparse.Namespace(repo_root=str(root), date="2026-05-26", session="pre-market", report=None, signals=None)
            payload = validate(args)

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("do not match focus list" in error for error in payload["errors"]))


if __name__ == "__main__":
    unittest.main()
