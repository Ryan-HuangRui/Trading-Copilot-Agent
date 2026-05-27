import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReviewWorkflowsTest(unittest.TestCase):
    def test_daily_self_review_writes_markdown_and_dedupes_review_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = root / "runtime" / "journal"
            journal.mkdir(parents=True)
            (journal / "signals.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "signal",
                        "signal_id": "sig-1",
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "symbol": "MU",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (journal / "outcomes.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "outcome",
                        "outcome_id": "out-1",
                        "signal_id": "sig-1",
                        "review_date": "2026-05-26",
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "outcome": "triggered",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(ROOT / "script" / "daily_self_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--append",
            ]
            first = subprocess.run(command, check=False, text=True, capture_output=True)
            second = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(first.returncode, 0, msg=first.stderr or first.stdout)
            self.assertEqual(second.returncode, 0, msg=second.stderr or second.stdout)
            first_payload = json.loads(first.stdout)
            second_payload = json.loads(second.stdout)
            self.assertEqual(first_payload["summary"]["by_outcome"], {"triggered": 1})
            self.assertEqual(first_payload["appended"], ["daily:2026-05-26"])
            self.assertEqual(second_payload["appended"], [])
            self.assertTrue((root / "report" / "2026-05-26" / "self-review.md").exists())

    def test_weekly_review_writes_week_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = root / "runtime" / "journal"
            journal.mkdir(parents=True)
            (journal / "outcomes.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "outcome",
                        "outcome_id": "out-1",
                        "review_date": "2026-05-26",
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "outcome": "not_triggered",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(ROOT / "script" / "weekly_review.py"),
                "--repo-root",
                str(root),
                "--week",
                "2026-W22",
                "--append",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["by_outcome"], {"not_triggered": 1})
            self.assertTrue((root / "report" / "weekly" / "2026-W22.md").exists())
            self.assertEqual(payload["appended"], ["weekly:2026-W22"])

    def test_extract_monitor_signals_appends_actionable_scans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report"
            report.mkdir()
            (report / "latest-monitor.json").write_text(
                json.dumps(
                    {
                        "risk_per_trade_pct": 1,
                        "scans": [
                            {
                                "symbol": "MU",
                                "status": "可执行",
                                "reason": "上升趋势+20Bar突破+放量",
                                "trigger": 100,
                                "stop": 95,
                                "invalid": "跌破 95",
                            },
                            {"symbol": "NVDA", "status": "观察中"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(ROOT / "script" / "extract_monitor_signals.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--append",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual([signal["symbol"] for signal in payload["signals"]], ["MU"])
            self.assertEqual(payload["signals"][0]["trigger_price"], 100.0)
            self.assertEqual(len(payload["appended"]), 1)


if __name__ == "__main__":
    unittest.main()
