import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReviewWorkflowsTest(unittest.TestCase):
    def test_plan_review_writes_plan_report_and_learning_lesson(self):
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
                        "setup": "breakout_pullback_continuation.md",
                        "plan_type": "trade_plan",
                        "execution_status": "conditional_executable",
                        "entry": {"trigger_price": 100},
                        "stop": {"initial_stop": 95},
                        "risk_detail": {"max_account_risk_pct": 1, "risk_per_share": 5},
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
                        "outcome": "triggered",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            command = [
                sys.executable,
                str(ROOT / "script" / "plan_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--append-lessons",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["plans"], 1)
            self.assertEqual(payload["summary"]["quality"]["incomplete_trade_plan"], 1)
            self.assertEqual(payload["summary"]["outcomes"], {"triggered": 1})
            self.assertTrue((root / "report" / "2026-05-26" / "plan-review.md").exists())
            review = json.loads((root / "report" / "2026-05-26" / "plan-review.json").read_text(encoding="utf-8"))
            self.assertEqual(review["plan_reviews"][0]["quality_state"], "incomplete_trade_plan")
            lessons = (root / "runtime" / "learning" / "daily_lessons.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lessons), 1)
            self.assertIn("missing_take_profit", lessons[0])

    def test_plan_review_includes_position_discipline(self):
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
                        "setup": "breakout_pullback_continuation.md",
                        "plan_type": "trade_plan",
                        "execution_status": "conditional_executable",
                        "entry": {"trigger_price": 100},
                        "stop": {"initial_stop": 95},
                        "take_profit": {"tp1": 112},
                        "risk_detail": {"max_account_risk_pct": 1, "risk_per_share": 5},
                        "execution_rules": {"skip_conditions": ["market turns risk-off"]},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (journal / "position_reviews.jsonl").write_text(
                "\n".join(
                    json.dumps(record, ensure_ascii=False)
                    for record in [
                        {
                            "kind": "position_review",
                            "position_review_id": "position:2026-05-26:MU",
                            "date": "2026-05-26",
                            "symbol": "MU",
                            "in_today_signals": True,
                            "review_required": False,
                            "trade_link_state": "linked_to_source_signal",
                            "risk_state": "normal",
                            "source_signal_id": "sig-1",
                        },
                        {
                            "kind": "position_review",
                            "position_review_id": "position:2026-05-26:TSLA",
                            "date": "2026-05-26",
                            "symbol": "TSLA",
                            "in_today_signals": False,
                            "review_required": True,
                            "trade_link_state": "no_trade_record",
                            "risk_state": "not_in_plan",
                        },
                        {
                            "kind": "position_review",
                            "position_review_id": "position:2026-05-26:NVDA",
                            "date": "2026-05-26",
                            "symbol": "NVDA",
                            "in_today_signals": False,
                            "review_required": True,
                            "trade_link_state": "trade_missing_source_signal_id",
                            "risk_state": "close_to_invalidation",
                            "distance_to_invalidation_pct": 1.5,
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            command = [
                sys.executable,
                str(ROOT / "script" / "plan_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--append-lessons",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            discipline = payload["summary"]["position_discipline"]
            self.assertEqual(discipline["position_reviews"], 3)
            self.assertEqual(discipline["positions_without_plan"], 2)
            self.assertEqual(discipline["missing_trade_link"], 2)
            self.assertEqual(discipline["missing_source_signal_id"], 1)
            self.assertEqual(discipline["close_to_invalidation_without_trade_record"], 1)
            review = json.loads((root / "report" / "2026-05-26" / "plan-review.json").read_text(encoding="utf-8"))
            self.assertEqual(review["position_discipline"]["positions_without_plan"], ["NVDA", "TSLA"])
            markdown = (root / "report" / "2026-05-26" / "plan-review.md").read_text(encoding="utf-8")
            self.assertIn("## 持仓纪律复盘", markdown)
            self.assertIn("有持仓但无计划：NVDA, TSLA", markdown)
            lessons = (root / "runtime" / "learning" / "daily_lessons.jsonl").read_text(encoding="utf-8")
            self.assertIn("position_without_plan", lessons)

    def test_plan_review_groups_duplicate_symbols_by_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = root / "runtime" / "journal"
            journal.mkdir(parents=True)
            records = [
                {
                    "kind": "signal",
                    "signal_id": "sig-pre",
                    "date": "2026-05-26",
                    "session": "pre-market",
                    "symbol": "MU",
                    "setup": "breakout_pullback_continuation.md",
                    "plan_type": "watch_only",
                    "execution_status": "watch_only",
                    "entry": {"trigger_price": 100},
                    "stop": {"initial_stop": 95},
                    "take_profit": {"tp1": 112},
                    "risk_detail": {"max_account_risk_pct": 1, "risk_per_share": 5},
                    "execution_rules": {"skip_conditions": ["market turns risk-off"]},
                },
                {
                    "kind": "signal",
                    "signal_id": "sig-post",
                    "date": "2026-05-22",
                    "session": "post-market",
                    "symbol": "MU",
                    "setup": "trend_pullback_high2_low2.md",
                    "plan_type": "watch_only",
                    "execution_status": "watch_only",
                    "entry": {"trigger_price": 102},
                    "stop": {"initial_stop": 96},
                    "take_profit": {"tp1": 114},
                    "risk_detail": {"max_account_risk_pct": 1, "risk_per_share": 6},
                    "execution_rules": {"skip_conditions": ["no follow-through"]},
                },
            ]
            (journal / "signals.jsonl").write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )
            outcomes = [
                {
                    "kind": "outcome",
                    "outcome_id": "out-pre",
                    "signal_id": "sig-pre",
                    "review_date": "2026-05-26",
                    "symbol": "MU",
                    "outcome": "triggered_and_invalidated",
                },
                {
                    "kind": "outcome",
                    "outcome_id": "out-post",
                    "signal_id": "sig-post",
                    "review_date": "2026-05-26",
                    "symbol": "MU",
                    "outcome": "invalidated",
                },
            ]
            (journal / "outcomes.jsonl").write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in outcomes) + "\n",
                encoding="utf-8",
            )

            command = [
                sys.executable,
                str(ROOT / "script" / "plan_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["plans"], 2)
            self.assertEqual(payload["summary"]["sessions"], {"post-market": 1, "pre-market": 1})
            review = json.loads((root / "report" / "2026-05-26" / "plan-review.json").read_text(encoding="utf-8"))
            self.assertEqual(review["session_groups"], {"post-market": ["MU"], "pre-market": ["MU"]})
            markdown = (root / "report" / "2026-05-26" / "plan-review.md").read_text(encoding="utf-8")
            self.assertIn("### post-market", markdown)
            self.assertIn("#### MU", markdown)
            self.assertIn("- session：post-market", markdown)
            self.assertIn("### pre-market", markdown)

    def test_plan_review_includes_intraday_execution_layers(self):
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
                        "setup": "breakout_pullback_continuation.md",
                        "plan_type": "trade_plan",
                        "execution_status": "conditional_executable",
                        "entry": {"trigger_price": 100},
                        "stop": {"initial_stop": 95},
                        "take_profit": {"tp1": 112},
                        "risk_detail": {"max_account_risk_pct": 1, "risk_per_share": 5},
                        "execution_rules": {"skip_conditions": ["risk-off tape"]},
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
                        "outcome": "triggered",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            write_report = root / "report" / "2026-05-26"
            write_report.mkdir(parents=True)
            (root / "runtime" / "intraday" / "2026-05-26").mkdir(parents=True)
            (root / "runtime" / "intraday" / "2026-05-26" / "state.json").write_text(
                json.dumps({"symbols": {"MU": {"state": "price_touched", "bar_timestamp": "2026-05-26 10:35:00"}}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (write_report / "monitor-signals.json").write_text(
                json.dumps(
                    {
                        "signals": [
                            {
                                "signal_id": "sig-monitor",
                                "symbol": "MU",
                                "plan_type": "trade_plan",
                                "execution_status": "conditional_executable",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (write_report / "paper-trade-submission.json").write_text(
                json.dumps(
                    {
                        "ready": [{"intent": {"source_signal_id": "sig-1", "symbol": "MU.US"}}],
                        "submitted": [],
                        "blocked": [],
                        "summary": {"ready": 1, "submitted": 0, "blocked": 0},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            command = [
                sys.executable,
                str(ROOT / "script" / "plan_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(
                payload["summary"]["execution_layers"],
                {
                    "price_triggered": 1,
                    "intraday_confirmed": 1,
                    "codex_candidate": 1,
                    "paper_ready": 1,
                    "paper_submitted": 0,
                    "trade_recorded": 0,
                },
            )
            review = json.loads((root / "report" / "2026-05-26" / "plan-review.json").read_text(encoding="utf-8"))
            layers = review["plan_reviews"][0]["execution_layers"]
            self.assertTrue(layers["price_triggered"])
            self.assertTrue(layers["intraday_confirmed"])
            self.assertTrue(layers["codex_candidate"])
            self.assertTrue(layers["paper_ready"])
            self.assertEqual(review["plan_reviews"][0]["intraday_state"], "price_touched")
            self.assertEqual(review["plan_reviews"][0]["paper_submission_state"], "ready")
            markdown = (root / "report" / "2026-05-26" / "plan-review.md").read_text(encoding="utf-8")
            self.assertIn("## 执行漏斗分层", markdown)
            self.assertIn("价格触发=1", markdown)
            self.assertIn("盘中确认=1", markdown)
            self.assertIn("paper ready=1", markdown)

    def test_plan_review_reports_detailed_funnel_and_paper_block_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = root / "runtime" / "journal"
            journal.mkdir(parents=True)
            records = []
            for symbol in ("AMD", "MU"):
                records.append(
                    {
                        "kind": "signal",
                        "signal_id": f"sig-{symbol}",
                        "date": "2026-06-15",
                        "session": "pre-market",
                        "symbol": symbol,
                        "setup": "breakout_pullback_continuation.md",
                        "plan_type": "watch_only",
                        "execution_status": "watch_only",
                        "entry": {"trigger_price": 100},
                        "stop": {"initial_stop": 95},
                    }
                )
            (journal / "signals.jsonl").write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )
            (journal / "outcomes.jsonl").write_text(
                "\n".join(
                    json.dumps(
                        {
                            "kind": "outcome",
                            "outcome_id": f"out-{symbol}",
                            "signal_id": f"sig-{symbol}",
                            "review_date": "2026-06-15",
                            "symbol": symbol,
                            "outcome": "triggered",
                        },
                        ensure_ascii=False,
                    )
                    for symbol in ("AMD", "MU")
                )
                + "\n",
                encoding="utf-8",
            )
            report = root / "report" / "2026-06-15"
            report.mkdir(parents=True)
            (root / "runtime" / "intraday" / "2026-06-15").mkdir(parents=True)
            (root / "runtime" / "intraday" / "2026-06-15" / "state.json").write_text(
                json.dumps(
                    {
                        "symbols": {
                            "AMD": {"state": "price_touched", "bar_timestamp": "2026-06-15 10:35:00"},
                            "MU": {"state": "price_touched", "bar_timestamp": "2026-06-15 10:35:00"},
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (report / "monitor-signals.json").write_text(
                json.dumps(
                    {
                        "signals": [
                            {"signal_id": "mon-AMD", "symbol": "AMD", "plan_type": "no_trade", "execution_status": "no_trade"},
                            {"signal_id": "mon-MU", "symbol": "MU", "plan_type": "no_trade", "execution_status": "no_trade"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            blocked_reasons = [
                "execution_status is not conditional_executable",
                "plan_type is not trade_plan",
                "risk.max_account_risk_pct must be > 0",
            ]
            (report / "paper-trade-submission.json").write_text(
                json.dumps(
                    {
                        "ready": [],
                        "submitted": [],
                        "blocked": [
                            {
                                "intent": {"source_signal_id": f"sig-{symbol}", "symbol": f"{symbol}.US"},
                                "preview": {"reasons": blocked_reasons},
                                "risk_guard": {"passed": False, "errors": []},
                            }
                            for symbol in ("AMD", "MU")
                        ],
                        "summary": {"ready": 0, "submitted": 0, "blocked": 2},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            command = [
                sys.executable,
                str(ROOT / "script" / "plan_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-06-15",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            funnel = payload["summary"]["execution_funnel"]
            self.assertEqual(funnel["price_touched"], 2)
            self.assertEqual(funnel["intraday_state_confirmed"], 2)
            self.assertEqual(funnel["codex_reviewed"], 2)
            self.assertEqual(funnel["codex_no_trade"], 2)
            self.assertEqual(funnel["codex_conditional_executable"], 0)
            self.assertEqual(funnel["complete_trade_plan_card"], 0)
            self.assertEqual(funnel["validation_passed"], 0)
            self.assertEqual(funnel["preview_ready"], 0)
            self.assertEqual(funnel["risk_guard_passed"], 0)
            self.assertEqual(funnel["submit_requested"], 0)
            self.assertEqual(funnel["broker_submitted"], 0)
            self.assertEqual(funnel["trade_recorded"], 0)
            self.assertEqual(
                payload["summary"]["paper_block_reasons"],
                {
                    "execution_status is not conditional_executable": 2,
                    "plan_type is not trade_plan": 2,
                    "risk.max_account_risk_pct must be > 0": 2,
                },
            )
            markdown = (root / "report" / "2026-06-15" / "plan-review.md").read_text(encoding="utf-8")
            self.assertIn("codex_conditional_executable=0", markdown)
            self.assertIn("paper ready=0", markdown)

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
            (journal / "position_reviews.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "position_review",
                        "position_review_id": "position:2026-05-26:MU",
                        "date": "2026-05-26",
                        "symbol": "MU",
                        "review_required": True,
                        "trade_link_state": "linked_to_source_signal",
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
            markdown = (root / "report" / "2026-05-26" / "self-review.md").read_text(encoding="utf-8")
            self.assertIn("持仓复核记录数：1", markdown)
            self.assertIn("持仓交易关联：{\"linked_to_source_signal\": 1}", markdown)

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
            (journal / "position_reviews.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "position_review",
                        "position_review_id": "position:2026-05-26:MU",
                        "date": "2026-05-26",
                        "symbol": "MU",
                        "review_required": True,
                        "trade_link_state": "trade_missing_source_signal_id",
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
            markdown = (root / "report" / "weekly" / "2026-W22.md").read_text(encoding="utf-8")
            self.assertIn("position review 数：1", markdown)
            self.assertIn("持仓交易关联：{\"trade_missing_source_signal_id\": 1}", markdown)
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

    def test_extract_monitor_signal_id_changes_with_trigger_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report"
            report.mkdir()
            command = [
                sys.executable,
                str(ROOT / "script" / "extract_monitor_signals.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-26",
                "--append",
            ]
            for trigger in (100, 101):
                (report / "latest-monitor.json").write_text(
                    json.dumps(
                        {
                            "risk_per_trade_pct": 1,
                            "scans": [
                                {
                                    "symbol": "MU",
                                    "status": "可执行",
                                    "setup": "strong_breakout_trend_following.md",
                                    "setup_files": ["strong_breakout_trend_following.md"],
                                    "reason": "上升趋势+20Bar突破+放量",
                                    "trigger": trigger,
                                    "trigger_detail": {"type": "break_above", "price": trigger},
                                    "invalidation_detail": {"type": "break_below", "price": 95},
                                    "risk_quality": "acceptable",
                                    "journal_appendable": True,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                proc = subprocess.run(command, check=False, text=True, capture_output=True)
                self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)

            lines = (root / "runtime" / "journal" / "signals.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
