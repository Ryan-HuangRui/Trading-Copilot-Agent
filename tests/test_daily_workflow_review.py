import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class DailyWorkflowReviewTest(unittest.TestCase):
    def test_review_marks_touch_and_fade_as_not_missed_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            date = "2026-06-17"
            report = root / "report" / date
            intraday = root / "runtime" / "intraday" / date
            report.mkdir(parents=True)
            intraday.mkdir(parents=True)

            write_json(
                report / "daily-snapshot.json",
                {
                    "snapshot_date": date,
                    "symbols": [
                        {
                            "symbol": "MU",
                            "latest": {"datetime": date, "high": "101", "low": "94", "close": "96"},
                        }
                    ],
                },
            )
            write_json(
                report / "monitor-signals.json",
                {
                    "date": date,
                    "session": "monitor",
                    "summary": {"total": 1, "conditional_executable": 0, "watch_only": 0, "no_trade": 1, "ready": 0},
                    "signals": [
                        {
                            "symbol": "MU",
                            "plan_type": "no_trade",
                            "execution_status": "no_trade",
                            "trigger": {"price": 100},
                            "stop": {"initial_stop": 95},
                        }
                    ],
                },
            )
            write_json(
                report / "intraday-opportunity-context.json",
                {
                    "date": date,
                    "summary": {"observation_scans": 1, "deterministic_candidate_scans": 0},
                    "observation_scans": [
                        {
                            "symbol": "MU",
                            "price_evidence": {
                                "bars": {
                                    "5min": [
                                        {"dt": "2026-06-17 13:30:00", "high": 101, "low": 99, "close": 99},
                                        {"dt": "2026-06-17 13:35:00", "high": 99, "low": 94, "close": 96},
                                    ]
                                }
                            },
                        }
                    ],
                },
            )
            write_json(intraday / "state.json", {"date": date, "symbols": {"MU": {"state": "price_touched"}}})
            (intraday / "events.jsonl").write_text(
                json.dumps({"event_id": "e1", "symbol": "MU", "event_type": "price_touched", "notify": True})
                + "\n",
                encoding="utf-8",
            )
            write_json(
                report / "post-market-run-manifest.json",
                {"workflow": "post-market-deliver", "status": "success", "steps": []},
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "daily_workflow_review.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    date,
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            review = payload["summary"]["missed_or_misjudged"]
            self.assertEqual(review["possible_missed_candidates"], 0)
            self.assertEqual(review["touch_fade_or_invalidated"], 1)
            self.assertEqual(review["confirmed_no_missed_executable"], True)
            markdown = Path(payload["markdown_output"]).read_text(encoding="utf-8")
            self.assertIn("未发现可执行漏判", markdown)
            self.assertIn("MU", markdown)

    def test_review_flags_premarket_watch_followthrough_as_possible_process_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            date = "2026-06-15"
            report = root / "report" / date
            report.mkdir(parents=True)

            write_json(
                report / "pre-market-signals.json",
                {
                    "date": date,
                    "session": "pre-market",
                    "signals": [
                        {
                            "symbol": "AMD",
                            "plan_type": "watch_only",
                            "execution_status": "watch_only",
                            "trigger": {"price": 100},
                            "stop": {"initial_stop": 95},
                        }
                    ],
                },
            )
            write_json(
                report / "daily-snapshot.json",
                {
                    "snapshot_date": date,
                    "symbols": [
                        {
                            "symbol": "AMD",
                            "latest": {"datetime": date, "high": "111", "low": "99", "close": "108"},
                        }
                    ],
                },
            )
            write_json(
                report / "pre-market-run-manifest.json",
                {"workflow": "pre-market-deliver", "status": "success", "steps": []},
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "daily_workflow_review.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    date,
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            review = payload["summary"]["missed_or_misjudged"]
            self.assertEqual(review["possible_missed_candidates"], 1)
            self.assertEqual(payload["price_reviews"][0]["symbol"], "AMD")
            self.assertEqual(payload["price_reviews"][0]["classification"], "possible_process_miss")
            markdown = Path(payload["markdown_output"]).read_text(encoding="utf-8")
            self.assertIn("可能漏接", markdown)


if __name__ == "__main__":
    unittest.main()
