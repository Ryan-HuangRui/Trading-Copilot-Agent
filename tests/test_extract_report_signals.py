import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from extract_report_signals import extract_post_market, extract_pre_market


PRE_MARKET_BRIEF = """# 今日盘前执行简版（2026-05-26）

## 总览
- 今日最多3个重点标的：DELL、QCOM、SNOW

## 执行清单（逐标的）

### DELL
- 参考 setup：strong_breakout_trend_following.md + breakout_pullback_continuation.md
- 主场景：若突破昨高并回踩不失守，才评估延续。
- 失效条件：跌破 265.21。
- 执行要点（1行）：只做确认，不追第一波。

### QCOM
- 参考 setup：strong_breakout_trend_following.md
- 主场景：若守住昨收并重新上攻，等待确认。
- 失效条件：跌破 214.17。
- 执行要点（1行）：止损距离过宽则放弃。

### SNOW
- 参考 setup：channel_break_reversal.md
- 主场景：突破后回踩不失守昨收。
- 失效条件：跌破 167.40。
- 执行要点（1行）：等触发和失效位成立。
"""

POST_MARKET_REPORT = """# 今日盘后复盘（2026-05-22）

## 明日观察清单
- DELL：参考 `strong_breakout_trend_following.md；breakout_pullback_continuation.md`；触发条件是突破 298.32 后守住 295.22 附近；放弃条件是跌破 265.21；单笔风险 <=1%。
- QCOM：参考 `strong_breakout_trend_following.md`；触发条件是突破 243.00 后守住 238.15 附近；放弃条件是跌破 214.17；单笔风险 <=1%。
"""


class ExtractReportSignalsTest(unittest.TestCase):
    def test_extracts_pre_market_focus_symbols_only(self):
        signals = extract_pre_market(PRE_MARKET_BRIEF, "2026-05-26", "report/2026-05-26/exec-brief.md", 2)

        self.assertEqual([signal["symbol"] for signal in signals], ["DELL", "QCOM"])
        self.assertEqual(signals[0]["setup"], "strong_breakout_trend_following.md")
        self.assertEqual(
            signals[0]["setup_files"],
            ["strong_breakout_trend_following.md", "breakout_pullback_continuation.md"],
        )
        self.assertIn("突破昨高", signals[0]["trigger"])
        self.assertEqual(signals[0]["status"], "planned")

    def test_extracts_post_market_observation_list(self):
        signals = extract_post_market(POST_MARKET_REPORT, "2026-05-22", "report/2026-05-22/post-market.md", 3)

        self.assertEqual([signal["symbol"] for signal in signals], ["DELL", "QCOM"])
        self.assertIn("298.32", signals[0]["trigger"])
        self.assertIn("265.21", signals[0]["invalidation"])
        self.assertEqual(signals[0]["risk"], "<=1%")

    def test_cli_append_deduplicates_signal_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-05-26"
            report_dir.mkdir(parents=True)
            (report_dir / "exec-brief.md").write_text(PRE_MARKET_BRIEF, encoding="utf-8")

            command = [
                sys.executable,
                str(ROOT / "script" / "extract_report_signals.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--session",
                "pre-market",
                "--append",
            ]
            first = subprocess.run(command, check=False, text=True, capture_output=True)
            second = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(first.returncode, 0, msg=first.stderr or first.stdout)
            self.assertEqual(second.returncode, 0, msg=second.stderr or second.stdout)
            first_payload = json.loads(first.stdout)
            second_payload = json.loads(second.stdout)
            self.assertEqual(len(first_payload["appended"]), 3)
            self.assertEqual(len(second_payload["appended"]), 0)
            self.assertEqual(len(second_payload["skipped_duplicates"]), 3)
            lines = (root / "runtime" / "journal" / "signals.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
