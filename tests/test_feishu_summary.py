import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FeishuSummaryTest(unittest.TestCase):
    def test_feishu_summary_prioritizes_analysis_over_execution_log(self):
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
            (report_dir / "pre-market.md").write_text(
                """# 今日盘前完整报告（2026-05-26）

## 1. 市场环境
今日没有可直接执行计划，MU 和 AMD 只适合等待盘中确认。

## 2. 今日最多3个重点标的
今日最多3个重点标的：MU, AMD

## 3. 重点观察候选
### MU
- 结构：强势突破后等待回踩。
- 触发条件：重新站上 100 后回踩不破。
- 关键位：确认位 100；失效位 95。
- 失效/放弃条件：跌破 95 或大盘 risk-off。
- 风险约束：单笔账户风险 <=1%。

### AMD
- 结构：突破候选但仍需确认。
- 触发条件：等待回踩确认。
- 关键位：确认位 90；失效位 85。
- 失效/放弃条件：跌破 85。
- 风险约束：单笔账户风险 <=1%。

## 5. 组合与流程风控
- 单笔账户风险不得超过 1%。
""",
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
            self.assertIn("# 盘前分析摘要", markdown)
            self.assertIn("【核心结论】", markdown)
            self.assertIn("来源：report/2026-05-26/pre-market.md", markdown)
            self.assertIn("今日没有可直接执行计划", markdown)
            self.assertIn("【今日重点标的分析】", markdown)
            self.assertIn("MU", markdown)
            self.assertIn("触发条件：重新站上 100 后回踩不破", markdown)
            self.assertIn("AMD", markdown)
            self.assertIn("【NO TRADE】", markdown)
            self.assertIn("SNOW", markdown)
            self.assertIn("数据质量", markdown)
            self.assertIn("MU 使用 twelve_data fallback", markdown)
            self.assertIn("【运行校验】", markdown)
            self.assertIn("validation：通过", markdown)
            self.assertNotIn("【Workflow】", markdown)
            self.assertNotIn("【生成 artifacts】", markdown)
            self.assertNotIn("【Journal】", markdown)
            self.assertNotIn("workflow：pre-market-deliver", markdown)
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
            (report_dir / "post-market.md").write_text(
                """# 今日盘后复盘（2026-05-26）

## 复盘结论
- MU 继续作为明日观察，不因盘中触价直接升级。
- 明日执行纪律：全部从 watch_only 开始。

## 明日观察清单
- MU：观察 100 上方突破后的回踩质量。

## 重点标的复盘
### MU
- 结构结论：偏强整理但尚未确认趋势延续。
- 明日主观察：放量站上 100 后回踩不破。
- 关键位：观察 100；风险参考 95。
- 失效/放弃条件：跌破 95 后无法收回。
- 风险提醒：不得因为盘中 touched 状态单独升级。

## NO TRADE
- AMD：结构不足。
""",
                encoding="utf-8",
            )
            (report_dir / "daily-snapshot.json").write_text(
                json.dumps(
                    {
                        "snapshot_date": "2026-05-26",
                        "market_data_source": "fixture",
                        "dynamic_universe_enabled": True,
                        "symbols": [
                            {"symbol": "SPY", "metrics": {"close_delta_pct": 0.5}},
                            {"symbol": "QQQ", "metrics": {"close_delta_pct": 0.8}},
                            {"symbol": "VIX", "metrics": {"close_delta_pct": -2.0}},
                            {
                                "symbol": "MU",
                                "sector": "Information Technology",
                                "metrics": {"close_delta_pct": 3.2},
                            },
                            {
                                "symbol": "AMD",
                                "sector": "Information Technology",
                                "metrics": {"close_delta_pct": -0.4},
                            },
                            {
                                "symbol": "XOM",
                                "sector": "Energy",
                                "metrics": {"close_delta_pct": 1.1},
                            },
                            {
                                "symbol": "CVX",
                                "sector": "Energy",
                                "metrics": {"close_delta_pct": -0.2},
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (report_dir / "longbridge-market-context.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "workflow": "longbridge-market-context",
                        "date": "2026-05-26",
                        "source": "longbridge",
                        "market": [
                            {"symbol": "SPY", "label": "S&P 500 ETF", "metrics": {"close_delta_pct": 0.5}},
                            {"symbol": "QQQ", "label": "Nasdaq 100 ETF", "metrics": {"close_delta_pct": 0.8}},
                            {"symbol": "VIX", "label": "VIX", "metrics": {"close_delta_pct": -2.0}},
                        ],
                        "industries": [
                            {"symbol": "XLK", "label": "科技", "metrics": {"close_delta_pct": 1.4}},
                            {"symbol": "XLE", "label": "能源", "metrics": {"close_delta_pct": 0.45}},
                            {"symbol": "XLU", "label": "公用事业", "metrics": {"close_delta_pct": -0.2}},
                        ],
                        "errors": [],
                        "summary": {
                            "market": {"count": 3, "up": 2, "down": 1, "flat": 0, "avg_pct": -0.23},
                            "industries": {"count": 3, "up": 2, "down": 1, "flat": 0, "avg_pct": 0.55},
                            "errors": 0,
                        },
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
            self.assertTrue(payload["summary"]["market_snapshot_available"])
            self.assertEqual(payload["summary"]["market_breadth_count"], 4)
            self.assertEqual(payload["summary"]["market_sector_count"], 2)
            self.assertTrue(payload["summary"]["longbridge_market_context_available"])
            self.assertEqual(payload["summary"]["longbridge_market_context_status"], "success")
            self.assertEqual(payload["summary"]["longbridge_market_context_errors"], 0)
            content = Path(payload["output"]).read_text(encoding="utf-8")
            self.assertIn("# 盘后分析摘要", content)
            self.assertIn("来源：report/2026-05-26/post-market.md", content)
            self.assertIn("MU 继续作为明日观察", content)
            self.assertIn("【市场与行业】", content)
            self.assertIn("Longbridge 大盘数据：status=success", content)
            self.assertIn("主要指数/风向标：S&P 500 ETF(SPY) +0.50%；Nasdaq 100 ETF(QQQ) +0.80%；VIX -2.00%", content)
            self.assertIn("Longbridge 行业代理 ETF 领涨：科技(XLK) +1.40%；能源(XLE) +0.45%", content)
            self.assertIn("Longbridge 行业代理 ETF 领跌/防御：公用事业(XLU) -0.20%", content)
            self.assertIn("【明日重点标的分析】", content)
            self.assertIn("结构结论：偏强整理但尚未确认趋势延续", content)
            self.assertIn("明日主观察：放量站上 100 后回踩不破", content)
            self.assertIn("【盘中监控回顾】", content)
            self.assertIn("关注池：MU, AMD", content)
            self.assertIn("near_trigger=1", content)
            self.assertNotIn("已发送=1", content)
            self.assertIn("【当日复盘结论】", content)
            self.assertIn("可能漏接候选：0", content)
            self.assertIn("触价后回落/失效：1", content)


if __name__ == "__main__":
    unittest.main()
