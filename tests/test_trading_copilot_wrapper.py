import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

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


class TradingCopilotWrapperTest(unittest.TestCase):
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

    def test_trading_day_check_wrapper_contract(self):
        payload = self.run_wrapper("trading-day-check", "--date", "2026-05-06")

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "trading-day-check")
        self.assertEqual(payload["date"], "2026-05-06")
        self.assertFalse(payload["skipped"])
        self.assertIn("artifacts", payload)
        self.assertIn("reason", payload)
        self.assertTrue(payload["is_trading_day"])

    def test_sync_longbridge_dry_run_wrapper_contract(self):
        payload = self.run_wrapper(
            "sync-longbridge-watchlist",
            "--session",
            "pre-market",
            "--symbol",
            "MU",
            "--symbol",
            "NVDA",
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "sync-longbridge-watchlist")
        self.assertEqual(payload["sync_mode"], "add")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["symbols"], ["MU.US", "NVDA.US"])

    def test_validate_report_wrapper_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "pre-market.md"
            report.write_text(GOOD_REPORT, encoding="utf-8")

            payload = self.run_wrapper(
                "validate-report",
                "--date",
                "2026-05-26",
                "--session",
                "pre-market",
                "--report",
                str(report),
            )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "validate-report")
        self.assertEqual(payload["date"], "2026-05-26")
        self.assertEqual(payload["validation"]["status"], "pass")
        self.assertEqual(payload["artifacts"], [str(report)])

    def test_sync_longbridge_can_require_report_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "exec-brief.md"
            report.write_text(GOOD_REPORT, encoding="utf-8")

            payload = self.run_wrapper(
                "sync-longbridge-watchlist",
                "--session",
                "pre-market",
                "--date",
                "2026-05-26",
                "--report",
                str(report),
                "--symbol",
                "MU",
                "--require-validation",
            )

        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["symbols"], ["MU.US"])
        self.assertEqual(payload["validation"]["status"], "pass")

    def test_extract_report_signals_wrapper_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "exec-brief.md"
            report.write_text(GOOD_REPORT, encoding="utf-8")
            journal_dir = Path(tmp) / "journal"

            payload = self.run_wrapper(
                "extract-report-signals",
                "--date",
                "2026-05-26",
                "--session",
                "pre-market",
                "--report",
                str(report),
                "--journal-dir",
                str(journal_dir),
                "--append",
            )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "extract-report-signals")
        self.assertEqual(payload["date"], "2026-05-26")
        self.assertEqual([signal["symbol"] for signal in payload["signals"]], ["MU"])
        self.assertEqual(len(payload["appended"]), 1)

    def test_backfill_signal_outcomes_wrapper_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal_dir = root / "journal"
            journal_dir.mkdir()
            signal = {
                "kind": "signal",
                "signal_id": "sig-1",
                "date": "2026-05-26",
                "session": "pre-market",
                "symbol": "MU",
                "setup": "breakout_pullback_continuation.md",
                "trigger": "突破 100 后确认",
                "invalidation": "跌破 95",
            }
            (journal_dir / "signals.jsonl").write_text(json.dumps(signal, ensure_ascii=False) + "\n", encoding="utf-8")
            snapshot = root / "daily-snapshot.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "snapshot_date": "2026-05-26",
                        "symbols": [
                            {
                                "symbol": "MU",
                                "latest": {
                                    "datetime": "2026-05-26",
                                    "open": "99",
                                    "high": "101",
                                    "low": "96",
                                    "close": "100",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = self.run_wrapper(
                "backfill-signal-outcomes",
                "--date",
                "2026-05-26",
                "--snapshot",
                str(snapshot),
                "--journal-dir",
                str(journal_dir),
                "--append",
            )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "backfill-signal-outcomes")
        self.assertEqual(payload["summary"]["by_outcome"], {"triggered": 1})
        self.assertEqual(len(payload["appended"]), 1)


if __name__ == "__main__":
    unittest.main()
