import argparse
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from validate_report import validate


GOOD_REPORT = """# 今日盘前完整报告（2026-05-26）

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


if __name__ == "__main__":
    unittest.main()
