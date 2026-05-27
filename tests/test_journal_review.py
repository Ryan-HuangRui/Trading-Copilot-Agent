import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from journal_review import backfill, evaluate_signal, planned_target_date, summarize


class JournalReviewTest(unittest.TestCase):
    def test_planned_target_date_uses_next_trading_day_for_post_market(self):
        signal = {"date": "2026-05-22", "session": "post-market"}

        self.assertEqual(planned_target_date(signal), "2026-05-26")

    def test_evaluate_signal_triggered_and_invalidated(self):
        signal = {
            "signal_id": "sig-1",
            "date": "2026-05-26",
            "session": "pre-market",
            "symbol": "MU",
            "setup": "breakout_pullback_continuation.md",
            "trigger": "突破 100 后回踩确认",
            "invalidation": "跌破 95",
        }
        symbols = {
            "MU": {
                "latest": {
                    "datetime": "2026-05-26",
                    "open": "98",
                    "high": "102",
                    "low": "94",
                    "close": "99",
                }
            }
        }

        outcome = evaluate_signal(signal, "2026-05-26", symbols)

        self.assertEqual(outcome["outcome"], "triggered_and_invalidated")
        self.assertTrue(outcome["trigger_hit"])
        self.assertTrue(outcome["invalidation_hit"])
        self.assertEqual(outcome["trigger_price"], 100.0)
        self.assertEqual(outcome["invalidation_price"], 95.0)

    def test_summarize_counts_by_outcome_setup_and_symbol(self):
        payload = summarize(
            [
                {"outcome": "triggered", "setup": "a.md", "symbol": "MU"},
                {"outcome": "not_triggered", "setup": "a.md", "symbol": "MU"},
                {"outcome": "triggered", "setup": "b.md", "symbol": "NVDA"},
            ]
        )

        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["by_outcome"]["triggered"], 2)
        self.assertEqual(payload["by_setup"]["a.md"]["not_triggered"], 1)
        self.assertEqual(payload["by_symbol"]["MU"]["triggered"], 1)

    def test_cli_append_deduplicates_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal_dir = root / "runtime" / "journal"
            journal_dir.mkdir(parents=True)
            signal = {
                "kind": "signal",
                "signal_id": "sig-1",
                "date": "2026-05-26",
                "session": "pre-market",
                "symbol": "MU",
                "setup": "breakout_pullback_continuation.md",
                "trigger": "突破 100 后回踩确认",
                "invalidation": "跌破 95",
                "source_report": "report/2026-05-26/exec-brief.md",
            }
            (journal_dir / "signals.jsonl").write_text(json.dumps(signal, ensure_ascii=False) + "\n", encoding="utf-8")
            report_dir = root / "report" / "2026-05-26"
            report_dir.mkdir(parents=True)
            snapshot = {
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
            (report_dir / "daily-snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")

            command = [
                sys.executable,
                str(ROOT / "script" / "journal_review.py"),
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
            self.assertEqual(len(first_payload["appended"]), 1)
            self.assertEqual(len(second_payload["appended"]), 0)
            self.assertEqual(len(second_payload["skipped_duplicates"]), 1)
            lines = (journal_dir / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()
