import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FeishuSummaryTest(unittest.TestCase):
    def test_feishu_summary_writes_execution_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-05-26"
            report_dir.mkdir(parents=True)
            (report_dir / "pre-market-signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "direction": "long",
                                "status": "planned",
                                "plan_type": "trade_plan",
                                "execution_status": "conditional_executable",
                                "entry": {"trigger_price": 100, "confirmation": "5m close above trigger"},
                                "stop": {"initial_stop": 95},
                                "take_profit": {"tp1": 112},
                                "risk": {"max_account_risk_pct": 1, "risk_per_share": 5},
                                "execution_rules": {"skip_conditions": ["market turns risk-off"]},
                            },
                            {
                                "symbol": "AMD",
                                "setup": "breakout_pullback_continuation.md",
                                "status": "planned",
                                "plan_type": "watch_only",
                                "execution_status": "watch_only",
                                "notes": "等待回踩确认",
                            },
                            {
                                "symbol": "SNOW",
                                "setup": "NO VALID SETUP",
                                "status": "no_trade",
                                "plan_type": "no_trade",
                                "execution_status": "no_trade",
                                "notes": "结构不清晰",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (report_dir / "position-review.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "summary": {"positions": 2, "review_required": 1, "trade_link_missing": 1},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (report_dir / "plan-review.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "summary": {
                            "plans": 3,
                            "quality": {"complete_trade_plan": 1, "watch_only": 1, "no_trade": 1},
                            "outcomes": {"triggered": 1, "not_triggered": 2},
                            "trade_state": {"no_trade_record": 3},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (report_dir / "data-quality.json").write_text(
                json.dumps(
                    {
                        "status": "warn",
                        "quality_status": "warn",
                        "stale_data": False,
                        "focused_fallback_symbols": [
                            {
                                "symbol": "MU",
                                "provider": "twelve_data",
                                "fallback_from": "longbridge",
                                "primary_error": "permission denied",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (report_dir / "pre-market-run-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "workflow": "pre-market-deliver",
                        "session": "pre-market",
                        "date": "2026-05-26",
                        "git_sha": "abc123",
                        "dirty_files": ["M config/watchlist.json"],
                        "artifacts": ["report/2026-05-26/pre-market-signals.json"],
                        "steps": [
                            {"name": "validate-report", "status": "success", "stdout": {"status": "pass"}},
                            {"name": "validate-trade-plan", "status": "success", "stdout": {"status": "pass"}},
                            {
                                "name": "extract-report-signals",
                                "status": "success",
                                "stdout": {"status": "success", "appended": [{"symbol": "MU"}], "skipped_duplicates": []},
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            learning = root / "runtime" / "learning"
            learning.mkdir(parents=True)
            (learning / "daily_lessons.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "daily_lesson",
                        "date": "2026-05-26",
                        "symbol": "MU",
                        "problem": "missing_outcome",
                        "suggested_constraint": "review snapshot coverage before judging plan quality",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "feishu_summary.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                    "--session",
                    "pre-market",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "success")
            output = Path(payload["output"])
            self.assertTrue(output.exists())
            markdown = output.read_text(encoding="utf-8")
            self.assertIn("【Workflow】", markdown)
            self.assertIn("workflow：pre-market-deliver", markdown)
            self.assertIn("【Validation】", markdown)
            self.assertIn("validate-report：success", markdown)
            self.assertIn("【Journal】", markdown)
            self.assertIn("【今日可执行交易计划】", markdown)
            self.assertIn("MU：入场 100", markdown)
            self.assertIn("止损 95", markdown)
            self.assertIn("TP1 112", markdown)
            self.assertIn("【观察候选】", markdown)
            self.assertIn("AMD", markdown)
            self.assertIn("【NO TRADE】", markdown)
            self.assertIn("SNOW", markdown)
            self.assertIn("持仓复核摘要", markdown)
            self.assertIn("数据质量", markdown)
            self.assertIn("MU 使用 twelve_data fallback", markdown)
            self.assertEqual(payload["summary"]["data_quality_status"], "warn")
            self.assertEqual(payload["summary"]["focused_fallback_symbols"], 1)

    def test_feishu_summary_supports_monitor_candidate_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-05-26"
            report_dir.mkdir(parents=True)
            (report_dir / "monitor-signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "monitor",
                        "summary": {"candidate": 1, "blocked": 2, "skipped": 3},
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "strong_breakout_trend_following.md",
                                "status": "observed",
                                "plan_type": "watch_only",
                                "execution_status": "watch_only",
                                "notes": "盘中观察候选",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (report_dir / "data-quality.json").write_text(
                json.dumps({"status": "warn", "stale_data": False, "focused_fallback_symbols": []}, ensure_ascii=False),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "feishu_summary.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                    "--session",
                    "monitor",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["candidate"], 1)
            self.assertEqual(payload["summary"]["blocked"], 2)
            content = Path(payload["output"]).read_text(encoding="utf-8")
            self.assertIn("【盘中候选状态】", content)

    def test_post_market_feishu_summary_includes_intraday_recap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-05-26"
            intraday_dir = root / "runtime" / "intraday" / "2026-05-26"
            report_dir.mkdir(parents=True)
            intraday_dir.mkdir(parents=True)
            (report_dir / "post-market-signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "post-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "NO VALID SETUP",
                                "status": "planned",
                                "plan_type": "watch_only",
                                "execution_status": "watch_only",
                                "notes": "继续观察",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (report_dir / "data-quality.json").write_text(
                json.dumps({"status": "pass", "stale_data": False, "focused_fallback_symbols": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            (report_dir / "post-market-run-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "workflow": "post-market-deliver",
                        "session": "post-market",
                        "date": "2026-05-26",
                        "git_sha": "abc123",
                        "dirty_files": [],
                        "artifacts": ["report/2026-05-26/post-market-signals.json"],
                        "steps": [{"name": "validate-report", "status": "success", "stdout": {"status": "pass"}}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (report_dir / "intraday.md").write_text("# intraday\n", encoding="utf-8")
            (intraday_dir / "state.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "generated_at": "2026-05-26T16:00:00+00:00",
                        "focus_symbols": ["MU", "AMD"],
                        "symbols": {
                            "MU": {"state": "waiting"},
                            "AMD": {"state": "near_trigger"},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (intraday_dir / "events.jsonl").write_text(
                json.dumps({"event_id": "e1", "symbol": "AMD", "state": "near_trigger", "notify": True}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            (intraday_dir / "sent-events.json").write_text(
                json.dumps({"sent_event_ids": ["e1"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (report_dir / "workflow-review.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "workflow": "daily-workflow-review",
                        "date": "2026-05-26",
                        "artifacts": [
                            "report/2026-05-26/workflow-review.json",
                            "report/2026-05-26/workflow-review.md",
                        ],
                        "summary": {
                            "workflow_status": {
                                "pre_market": "success",
                                "intraday": "available",
                                "post_market": "success",
                            },
                            "intraday_events": 1,
                            "intraday_failures": 0,
                            "missed_or_misjudged": {
                                "possible_missed_candidates": 0,
                                "touch_fade_or_invalidated": 1,
                                "not_triggered": 2,
                                "not_evaluable": 0,
                                "confirmed_no_missed_executable": True,
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "feishu_summary.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                    "--session",
                    "post-market",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["summary"]["intraday_available"])
            self.assertEqual(payload["summary"]["intraday_events"], 1)
            self.assertEqual(payload["summary"]["intraday_notify_events"], 1)
            self.assertTrue(payload["summary"]["workflow_review_available"])
            self.assertEqual(payload["summary"]["workflow_possible_missed_candidates"], 0)
            self.assertEqual(payload["summary"]["workflow_touch_fade_or_invalidated"], 1)
            content = Path(payload["output"]).read_text(encoding="utf-8")
            self.assertIn("【盘中监控回顾】", content)
            self.assertIn("关注池：MU, AMD", content)
            self.assertIn("near_trigger=1", content)
            self.assertIn("已发送=1", content)
            self.assertIn("【当日工作过程复盘】", content)
            self.assertIn("可能漏接候选：0", content)
            self.assertIn("触价后回落/失效：1", content)


if __name__ == "__main__":
    unittest.main()
