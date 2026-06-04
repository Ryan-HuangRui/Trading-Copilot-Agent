import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import trading_copilot

GOOD_REPORT = """# 今日盘前完整报告（2026-05-26）

## 总览
- 今日最多3个重点标的：MU

## 消息层汇总
### 特朗普持仓与交易变化
- 数据来源：未接入结构化 OGE/Open Cabinet 披露输入；本节不构成交易信号。
- 持仓变化：未获取到可核验的最新披露。
- 交易变化：未获取到可核验的最新披露。
- 对今日计划影响：只作为消息层风险背景，不能提升任何标的执行等级。

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

    def run_wrapper_raw(self, *args):
        return subprocess.run(
            [sys.executable, "script/trading_copilot.py", *args],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )

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

    def test_validate_trade_plan_wrapper_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            sidecar = Path(tmp) / "pre-market-signals.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "direction": "long",
                                "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                                "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                                "risk": {
                                    "max_risk_pct": 1,
                                    "max_account_risk_pct": 1,
                                    "risk_per_share": 5,
                                },
                                "status": "planned",
                                "plan_type": "trade_plan",
                                "execution_status": "conditional_executable",
                                "entry": {"trigger_price": 100, "confirmation": "pullback holds"},
                                "stop": {"initial_stop": 95},
                                "take_profit": {"tp1": 112},
                                "execution_rules": {"skip_conditions": ["market turns risk-off"]},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = self.run_wrapper(
                "validate-trade-plan",
                "--date",
                "2026-05-26",
                "--session",
                "pre-market",
                "--signals",
                str(sidecar),
            )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "validate-trade-plan")
        self.assertEqual(payload["validation"]["status"], "pass")
        self.assertEqual(payload["artifacts"], [str(sidecar)])

    def test_intraday_dry_run_chains_monitor_preview_submit_without_execute(self):
        calls = []

        def fake_run_child(command):
            calls.append(command)
            if command[0] == "script/extract_monitor_signals.py":
                payload = {
                    "status": "success",
                    "date": "2026-05-26",
                    "signals_path": "report/2026-05-26/monitor-signals.json",
                    "signals": [{"symbol": "MU"}],
                    "appended": [],
                    "skipped_duplicates": [],
                }
            elif command[0] == "script/validate_trade_plan.py":
                payload = {"status": "pass", "errors": [], "warnings": [], "checked_artifacts": ["report/2026-05-26/monitor-signals.json"]}
            elif command[0] == "script/paper_trade_preview.py":
                payload = {"status": "success", "date": "2026-05-26", "output": "report/2026-05-26/paper-trade-preview.json", "summary": {"ready": 0}}
            elif command[0] == "script/paper_trade_submit.py":
                payload = {
                    "status": "success",
                    "date": "2026-05-26",
                    "output": "report/2026-05-26/paper-trade-submission.json",
                    "dry_run": True,
                    "summary": {"ready": 0},
                }
            elif command[0] == "script/feishu_summary.py":
                payload = {"status": "success", "output": "report/2026-05-26/monitor-feishu-summary.md", "summary": {"candidate": 1}}
            else:
                payload = {"status": "success"}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        args = Namespace(
            date="2026-05-26",
            signals=None,
            monitor="report/latest-monitor.json",
            max_signals=5,
            timezone="America/New_York",
            signals_output=None,
            account_snapshot=None,
            preview_output=None,
            submit_output=None,
            summary_output=None,
            default_market="US",
            tif="day",
            max_daily_risk_pct=3.0,
            max_daily_orders=3,
            learning_dir="runtime/learning",
        )

        with patch.object(trading_copilot, "run_child", side_effect=fake_run_child), patch.object(
            trading_copilot, "emit", side_effect=SystemExit
        ) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_intraday_dry_run(args)

        self.assertEqual(
            [command[0] for command in calls],
            [
                "script/extract_monitor_signals.py",
                "script/validate_trade_plan.py",
                "script/paper_trade_preview.py",
                "script/paper_trade_submit.py",
                "script/feishu_summary.py",
            ],
        )
        self.assertNotIn("--execute", [part for command in calls for part in command])
        submit_command = calls[3]
        self.assertIn("--session", submit_command)
        self.assertIn("monitor", submit_command)
        payload = emit.call_args.args[0]
        self.assertEqual(payload["workflow"], "intraday-dry-run")
        self.assertTrue(payload["dry_run"])

    def test_intraday_dry_run_accepts_codex_reviewed_signals_without_extracting(self):
        calls = []

        def fake_run_child(command):
            calls.append(command)
            if command[0] == "script/validate_trade_plan.py":
                payload = {"status": "pass", "errors": [], "warnings": [], "checked_artifacts": ["report/2026-05-26/monitor-signals.json"]}
            elif command[0] == "script/paper_trade_preview.py":
                payload = {"status": "success", "date": "2026-05-26", "output": "report/2026-05-26/paper-trade-preview.json", "summary": {"ready": 1}}
            elif command[0] == "script/paper_trade_submit.py":
                payload = {
                    "status": "success",
                    "date": "2026-05-26",
                    "output": "report/2026-05-26/paper-trade-submission.json",
                    "dry_run": True,
                    "summary": {"ready": 1},
                }
            elif command[0] == "script/feishu_summary.py":
                payload = {"status": "success", "output": "report/2026-05-26/monitor-feishu-summary.md", "summary": {"conditional_executable": 1}}
            else:
                payload = {"status": "success"}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        args = Namespace(
            date="2026-05-26",
            signals="report/2026-05-26/monitor-signals.json",
            monitor="report/latest-monitor.json",
            max_signals=5,
            timezone="America/New_York",
            signals_output=None,
            account_snapshot=None,
            preview_output=None,
            submit_output=None,
            summary_output=None,
            default_market="US",
            tif="day",
            max_daily_risk_pct=3.0,
            max_daily_orders=3,
            learning_dir="runtime/learning",
        )

        with patch.object(trading_copilot, "run_child", side_effect=fake_run_child), patch.object(
            trading_copilot, "emit", side_effect=SystemExit
        ) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_intraday_dry_run(args)

        self.assertEqual(
            [command[0] for command in calls],
            [
                "script/validate_trade_plan.py",
                "script/paper_trade_preview.py",
                "script/paper_trade_submit.py",
                "script/feishu_summary.py",
            ],
        )
        self.assertEqual(calls[0][calls[0].index("--signals") + 1], "report/2026-05-26/monitor-signals.json")
        payload = emit.call_args.args[0]
        self.assertEqual(payload["signals_path"], "report/2026-05-26/monitor-signals.json")
        self.assertEqual(payload["submit_summary"], {"ready": 1})
        self.assertEqual(payload["artifacts"][-1], "report/2026-05-26/monitor-feishu-summary.md")

    def test_paper_lifecycle_chains_sync_exit_plans_and_review(self):
        calls = []

        def fake_run_child(command):
            calls.append(command)
            workflow = Path(command[0]).stem
            outputs = {
                "paper_account_snapshot": {"status": "success", "date": "2026-05-26", "output": "runtime/paper/2026-05-26/paper-account-snapshot.json"},
                "paper_order_sync": {"status": "success", "date": "2026-05-26", "output": "runtime/paper/2026-05-26/paper-execution-state.json", "summary": {"filled": 1}},
                "paper_order_cancel": {"status": "success", "date": "2026-05-26", "output": "report/2026-05-26/paper-order-cancel-plan.json", "dry_run": True, "summary": {"cancel_candidates": 0}},
                "paper_protective_stop_plan": {"status": "success", "date": "2026-05-26", "output": "report/2026-05-26/paper-protective-stop-plan.json", "dry_run": True, "summary": {"stop_candidates": 1}},
                "paper_take_profit_plan": {"status": "success", "date": "2026-05-26", "output": "report/2026-05-26/paper-take-profit-plan.json", "dry_run": True, "summary": {"take_profit_candidates": 1}},
                "paper_exit_plan": {"status": "success", "date": "2026-05-26", "output": "report/2026-05-26/paper-exit-plan.json", "dry_run": True, "summary": {"exit_candidates": 0}},
                "paper_break_even_stop_plan": {"status": "success", "date": "2026-05-26", "output": "report/2026-05-26/paper-break-even-stop-plan.json", "dry_run": True, "summary": {"move_candidates": 0}},
                "paper_event_ledger": {"status": "success", "date": "2026-05-26", "output": "report/2026-05-26/paper-event-ledger.json", "events_journal": "runtime/journal/events.jsonl", "summary": {"events": 3}},
                "paper_execution_review": {"status": "success", "date": "2026-05-26", "output": "report/2026-05-26/paper-execution-review.json", "markdown": "report/2026-05-26/paper-execution-review.md", "summary": {"orders": 1}},
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(outputs[workflow]), "")

        args = Namespace(
            date="2026-05-26",
            repo_root=str(ROOT),
            paper_account_input=None,
            paper_execution_config="config/paper_execution.local.json",
            longbridge_cli=None,
            execute_cancel=False,
            execute_protective_stop=False,
            execute_take_profit=False,
            execute_exit=False,
            execute_break_even_stop=False,
            resize_stop_before_take_profit=False,
            append_lessons=False,
            strategy_review=False,
            expire_after_minutes=90,
            stop_tif="gtc",
            take_profit_tif="gtc",
            exit_order_type="MO",
            exit_tif="day",
            break_even_tif="gtc",
            exit_fraction=0.5,
            learning_dir="runtime/learning",
        )

        with patch.object(trading_copilot, "run_child", side_effect=fake_run_child), patch.object(
            trading_copilot, "emit", side_effect=SystemExit
        ) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_paper_lifecycle(args)

        self.assertEqual(
            [Path(command[0]).stem for command in calls],
            [
                "paper_account_snapshot",
                "paper_order_sync",
                "paper_order_cancel",
                "paper_account_snapshot",
                "paper_order_sync",
                "paper_protective_stop_plan",
                "paper_take_profit_plan",
                "paper_exit_plan",
                "paper_break_even_stop_plan",
                "paper_account_snapshot",
                "paper_order_sync",
                "paper_event_ledger",
                "paper_execution_review",
            ],
        )
        self.assertNotIn("--execute", [part for command in calls for part in command])
        payload = emit.call_args.args[0]
        self.assertEqual(payload["workflow"], "paper-lifecycle")
        self.assertEqual(payload["summary"]["paper_order_sync"]["filled"], 1)
        self.assertIn("report/2026-05-26/paper-execution-review.json", payload["artifacts"])

    def test_paper_lifecycle_applies_independent_execute_flags(self):
        calls = []

        def fake_run_child(command):
            calls.append(command)
            workflow = Path(command[0]).stem
            payload = {"status": "success", "date": "2026-05-26", "summary": {}, "output": f"artifact/{workflow}.json"}
            if workflow in {"paper_order_cancel", "paper_protective_stop_plan", "paper_take_profit_plan", "paper_break_even_stop_plan"}:
                payload["dry_run"] = "--execute" not in command
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        args = Namespace(
            date="2026-05-26",
            repo_root=str(ROOT),
            paper_account_input=None,
            paper_execution_config="config/paper_execution.local.json",
            longbridge_cli="/usr/local/bin/longbridge",
            execute_cancel=True,
            execute_protective_stop=True,
            execute_take_profit=True,
            execute_exit=True,
            execute_break_even_stop=True,
            resize_stop_before_take_profit=True,
            append_lessons=True,
            strategy_review=True,
            expire_after_minutes=90,
            stop_tif="gtc",
            take_profit_tif="gtc",
            exit_order_type="MO",
            exit_tif="day",
            break_even_tif="gtc",
            exit_fraction=0.5,
            learning_dir="runtime/learning",
        )

        with patch.object(trading_copilot, "run_child", side_effect=fake_run_child), patch.object(
            trading_copilot, "emit", side_effect=SystemExit
        ) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_paper_lifecycle(args)

        by_workflow = {Path(command[0]).stem: command for command in calls}
        self.assertIn("--execute", by_workflow["paper_order_cancel"])
        self.assertIn("--execute", by_workflow["paper_protective_stop_plan"])
        self.assertIn("--execute", by_workflow["paper_take_profit_plan"])
        self.assertIn("--resize-stop-before-submit", by_workflow["paper_take_profit_plan"])
        self.assertIn("--execute", by_workflow["paper_exit_plan"])
        self.assertIn("--order-type", by_workflow["paper_exit_plan"])
        self.assertIn("--execute", by_workflow["paper_break_even_stop_plan"])
        self.assertIn("--paper-execution-config", by_workflow["paper_order_cancel"])
        self.assertIn("/usr/local/bin/longbridge", by_workflow["paper_order_cancel"])
        self.assertIn("paper_learning_lessons", [Path(command[0]).stem for command in calls])
        self.assertIn("paper_strategy_review", [Path(command[0]).stem for command in calls])
        payload = emit.call_args.args[0]
        self.assertTrue(payload["execute_requested"]["cancel"])
        self.assertTrue(payload["execute_requested"]["take_profit"])
        self.assertTrue(payload["execute_requested"]["exit"])

    def test_paper_take_profit_wrapper_passes_stop_resize_options(self):
        calls = []

        def fake_run_child(command):
            calls.append(command)
            payload = {
                "status": "success",
                "date": "2026-05-26",
                "output": "report/2026-05-26/paper-take-profit-plan.json",
                "dry_run": False,
                "summary": {"take_profit_candidates": 1, "resized_stops": 1, "submitted": 1},
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        args = Namespace(
            date="2026-05-26",
            repo_root=str(ROOT),
            state="runtime/paper/2026-05-26/paper-execution-state.json",
            output="report/2026-05-26/paper-take-profit-plan.json",
            take_profit_journal="runtime/paper/2026-05-26/paper-take-profit-orders.jsonl",
            stops_journal="runtime/paper/2026-05-26/paper-stop-orders.jsonl",
            exit_fraction=0.5,
            tif="gtc",
            resize_stop_before_submit=True,
            longbridge_cli="/usr/local/bin/longbridge",
            paper_execution_config="config/paper_execution.local.json",
            execute=True,
        )

        with patch.object(trading_copilot, "run_child", side_effect=fake_run_child), patch.object(
            trading_copilot, "emit", side_effect=SystemExit
        ) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_paper_take_profit_plan(args)

        command = calls[0]
        self.assertEqual(command[0], "script/paper_take_profit_plan.py")
        self.assertIn("--stops-journal", command)
        self.assertEqual(
            command[command.index("--stops-journal") + 1],
            "runtime/paper/2026-05-26/paper-stop-orders.jsonl",
        )
        self.assertIn("--take-profit-journal", command)
        self.assertIn("--resize-stop-before-submit", command)
        self.assertIn("--execute", command)
        self.assertIn("--paper-execution-config", command)
        payload = emit.call_args.args[0]
        self.assertEqual(payload["workflow"], "paper-take-profit-plan")
        self.assertEqual(payload["summary"]["resized_stops"], 1)

    def test_paper_exit_plan_wrapper_passes_execute_and_order_options(self):
        calls = []

        def fake_run_child(command):
            calls.append(command)
            payload = {
                "status": "success",
                "date": "2026-05-26",
                "output": "report/2026-05-26/paper-exit-plan.json",
                "dry_run": False,
                "summary": {"exit_candidates": 1, "submitted": 1},
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        args = Namespace(
            date="2026-05-26",
            repo_root=str(ROOT),
            state="runtime/paper/2026-05-26/paper-execution-state.json",
            intraday_state="runtime/intraday/2026-05-26/state.json",
            decisions="report/2026-05-26/paper-exit-decisions.json",
            output="report/2026-05-26/paper-exit-plan.json",
            exits_journal="runtime/paper/2026-05-26/paper-exit-orders.jsonl",
            order_type="MIT",
            limit_price=None,
            trigger_price=95,
            trailing_amount=None,
            trailing_percent=None,
            limit_offset=None,
            tif="gtc",
            expire_date=None,
            outside_rth=None,
            execute=True,
            longbridge_cli="/usr/local/bin/longbridge",
            paper_execution_config="config/paper_execution.local.json",
        )

        with patch.object(trading_copilot, "run_child", side_effect=fake_run_child), patch.object(
            trading_copilot, "emit", side_effect=SystemExit
        ) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_paper_exit_plan(args)

        command = calls[0]
        self.assertEqual(command[0], "script/paper_exit_plan.py")
        self.assertIn("--intraday-state", command)
        self.assertIn("--decisions", command)
        self.assertIn("--exits-journal", command)
        self.assertIn("--order-type", command)
        self.assertEqual(command[command.index("--order-type") + 1], "MIT")
        self.assertIn("--trigger-price", command)
        self.assertIn("--execute", command)
        self.assertIn("--paper-execution-config", command)
        payload = emit.call_args.args[0]
        self.assertEqual(payload["workflow"], "paper-exit-plan")
        self.assertEqual(payload["summary"]["submitted"], 1)

    def test_paper_lifecycle_wrapper_smoke_with_fixture_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            date = "2026-05-26"
            paper_dir = root / "runtime" / "paper" / date
            report_dir = root / "report" / date
            setup_dir = root / "knowledge" / "refined" / "setups"
            paper_dir.mkdir(parents=True)
            report_dir.mkdir(parents=True)
            setup_dir.mkdir(parents=True)
            (setup_dir / "breakout_pullback_continuation.md").write_text(
                "# Breakout Pullback Continuation\n", encoding="utf-8"
            )
            intent_id = f"{date}:pre-market:MU:abc123"
            order = {
                "kind": "paper_order",
                "intent_id": intent_id,
                "source_signal_id": "sig-1",
                "date": date,
                "session": "pre-market",
                "symbol": "MU",
                "longbridge_symbol": "MU.US",
                "side": "buy",
                "order_type": "LO",
                "quantity": 200,
                "limit_price": 100,
                "trigger_price": 100,
                "initial_stop": 95,
                "take_profit": 112,
                "setup": "Breakout Pullback Continuation",
                "setup_files": ["breakout_pullback_continuation.md"],
                "remark": f"tca:{intent_id}",
                "broker_order_id": "paper-o-1",
                "submit_status": "submitted",
                "submitted_at": "2026-05-26T13:30:00+00:00",
            }
            (paper_dir / "paper-orders.jsonl").write_text(json.dumps(order, ensure_ascii=False) + "\n", encoding="utf-8")
            preview = {
                "date": date,
                "session": "pre-market",
                "dry_run": True,
                "orders": [
                    {
                        "signal_id": "sig-1",
                        "symbol": "MU",
                        "longbridge_symbol": "MU.US",
                        "setup": "Breakout Pullback Continuation",
                        "side": "buy",
                        "order_type": "LO",
                        "quantity": 200,
                        "entry_price": 100,
                        "limit_price": 100,
                        "stop_price": 95,
                        "take_profit": 112,
                        "status": "ready",
                    }
                ],
                "summary": {"ready": 1, "blocked": 0},
            }
            (report_dir / "paper-trade-preview.json").write_text(json.dumps(preview, ensure_ascii=False), encoding="utf-8")
            fixture = {
                "auth": {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}},
                "account": {"net_liquidation": 100000, "cash": 25000},
                "positions": [{"symbol": "MU.US", "quantity": 200, "cost_price": 100.2, "market_value": 20400}],
                "orders": [
                    {
                        "order_id": "paper-o-1",
                        "symbol": "MU.US",
                        "market": "US",
                        "side": "buy",
                        "quantity": 200,
                        "price": 100,
                        "status": "filled",
                        "raw": {"remark": f"tca:{intent_id}"},
                    }
                ],
                "executions": [
                    {
                        "order_id": "paper-o-1",
                        "symbol": "MU.US",
                        "market": "US",
                        "side": "buy",
                        "quantity": 200,
                        "price": 100.2,
                        "raw": {"order_id": "paper-o-1"},
                    }
                ],
            }
            fixture_path = root / "paper-fixture.json"
            fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-lifecycle",
                    "--repo-root",
                    str(root),
                    "--date",
                    date,
                    "--paper-account-input",
                    str(fixture_path),
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["workflow"], "paper-lifecycle")
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["summary"]["paper_order_sync"]["filled"], 1)
            self.assertEqual(payload["summary"]["paper_execution_review"]["filled"], 1)
            self.assertTrue((paper_dir / "paper-account-snapshot.json").exists())
            self.assertTrue((paper_dir / "paper-execution-state.json").exists())
            self.assertTrue((report_dir / "paper-protective-stop-plan.json").exists())
            self.assertTrue((report_dir / "paper-take-profit-plan.json").exists())
            self.assertTrue((report_dir / "paper-break-even-stop-plan.json").exists())
            self.assertTrue((report_dir / "paper-execution-review.json").exists())

    def test_intraday_paper_entry_uses_dedicated_script_and_execute_gate(self):
        calls = []

        def fake_run_child(command):
            calls.append(command)
            payload = {
                "status": "success",
                "date": "2026-05-26",
                "output": "report/2026-05-26/intraday-paper-entry.json",
                "dry_run": False,
                "summary": {"submitted": 1},
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        args = Namespace(
            date="2026-05-26",
            preview=None,
            account_snapshot=None,
            orders_journal=None,
            signals=None,
            output=None,
            longbridge_cli=None,
            require_validation=True,
            execute=True,
            max_daily_risk_pct=3.0,
            max_daily_orders=1,
            paper_execution_config=None,
        )

        with patch.object(trading_copilot, "run_child", side_effect=fake_run_child), patch.object(
            trading_copilot, "emit", side_effect=SystemExit
        ) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_intraday_paper_entry(args)

        self.assertEqual(calls[0][0], "script/intraday_paper_entry.py")
        self.assertIn("--execute", calls[0])
        payload = emit.call_args.args[0]
        self.assertEqual(payload["workflow"], "intraday-paper-entry")
        self.assertFalse(payload["dry_run"])
        self.assertEqual(payload["summary"]["submitted"], 1)

    def test_sync_longbridge_can_require_report_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = Path(tmp) / "exec-brief.md"
            report.write_text(GOOD_REPORT, encoding="utf-8")
            sidecar = root / "pre-market-signals.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "direction": "long",
                                "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                                "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                                "risk": {
                                    "max_risk_pct": 1,
                                    "max_account_risk_pct": 1,
                                    "risk_per_share": 5,
                                },
                                "status": "planned",
                                "plan_type": "trade_plan",
                                "execution_status": "conditional_executable",
                                "entry": {"trigger_price": 100, "confirmation": "pullback holds"},
                                "stop": {"initial_stop": 95},
                                "take_profit": {"tp1": 112},
                                "execution_rules": {"skip_conditions": ["market turns risk-off"]},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = self.run_wrapper(
                "sync-longbridge-watchlist",
                "--session",
                "pre-market",
                "--date",
                "2026-05-26",
                "--report",
                str(report),
                "--signals",
                str(sidecar),
                "--symbol",
                "MU",
                "--require-validation",
            )

        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["symbols"], ["MU.US"])
        self.assertEqual(payload["validation"]["status"], "pass")
        self.assertEqual(payload["trade_plan_validation"]["status"], "pass")

    def test_sync_longbridge_require_validation_fails_invalid_trade_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "exec-brief.md"
            report.write_text(GOOD_REPORT, encoding="utf-8")
            sidecar = root / "pre-market-signals.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "direction": "long",
                                "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                                "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                                "risk": {
                                    "max_risk_pct": 1,
                                    "max_account_risk_pct": 1,
                                    "risk_per_share": 5,
                                },
                                "status": "planned",
                                "plan_type": "trade_plan",
                                "execution_status": "conditional_executable",
                                "entry": {"trigger_price": 100, "confirmation": "pullback holds"},
                                "stop": {"initial_stop": 95},
                                "execution_rules": {"skip_conditions": ["market turns risk-off"]},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            proc = self.run_wrapper_raw(
                "sync-longbridge-watchlist",
                "--session",
                "pre-market",
                "--date",
                "2026-05-26",
                "--report",
                str(report),
                "--signals",
                str(sidecar),
                "--symbol",
                "MU",
                "--require-validation",
            )

        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["workflow"], "sync-longbridge-watchlist")
        self.assertIn("take_profit.tp1", payload["reason"])

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

    def test_plan_review_wrapper_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal_dir = root / "journal"
            journal_dir.mkdir()
            (journal_dir / "signals.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "signal",
                        "signal_id": "sig-1",
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "symbol": "MU",
                        "plan_type": "watch_only",
                        "execution_status": "watch_only",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = self.run_wrapper(
                "plan-review",
                "--date",
                "2026-05-26",
                "--journal-dir",
                str(journal_dir),
                "--output",
                str(root / "plan-review"),
            )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "plan-review")
        self.assertEqual(payload["summary"]["plans"], 1)
        self.assertEqual(payload["summary"]["quality"], {"watch_only": 1})

    def test_learning_review_wrapper_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_dir = root / "learning"
            learning_dir.mkdir()
            records = []
            for date, symbol in (("2026-05-24", "MU"), ("2026-05-25", "AMD"), ("2026-05-26", "TSM")):
                records.append(
                    {
                        "kind": "daily_lesson",
                        "date": date,
                        "lesson_type": "plan_quality",
                        "symbol": symbol,
                        "setup": "breakout_pullback_continuation.md",
                        "problem": "missing_take_profit",
                        "suggested_constraint": "conditional_executable plans must include TP1",
                    }
                )
            (learning_dir / "daily_lessons.jsonl").write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            payload = self.run_wrapper(
                "learning-review",
                "--learning-dir",
                str(learning_dir),
                "--end-date",
                "2026-05-26",
                "--lookback-days",
                "20",
                "--min-count",
                "3",
                "--output",
                str(root / "pattern-review"),
            )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "learning-review")
        self.assertEqual(payload["summary"]["pattern_candidates"], 1)

    def test_promote_lesson_wrapper_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learning_dir = root / "learning"
            learning_dir.mkdir()
            candidate = {
                "kind": "pattern_candidate",
                "pattern_id": "missing_take_profit__breakout_pullback_continuation",
                "problem": "missing_take_profit",
                "setup": "breakout_pullback_continuation.md",
                "lesson_type": "plan_quality",
                "seen_count": 3,
                "first_seen": "2026-05-24",
                "last_seen": "2026-05-26",
                "suggested_constraint": "conditional_executable plans must include TP1",
                "promotion_status": "needs_human_review",
            }
            (learning_dir / "pattern_candidates.jsonl").write_text(
                json.dumps(candidate, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            output = root / "validated_lessons.md"

            payload = self.run_wrapper(
                "promote-lesson",
                "--learning-dir",
                str(learning_dir),
                "--pattern-id",
                "missing_take_profit__breakout_pullback_continuation",
                "--output",
                str(output),
                "--apply",
            )
            self.assertTrue(output.exists())

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "promote-lesson")
        self.assertTrue(payload["applied"])

    def test_feishu_summary_wrapper_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-05-26"
            report_dir.mkdir(parents=True)
            signals = report_dir / "pre-market-signals.json"
            signals.write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "status": "planned",
                                "plan_type": "watch_only",
                                "execution_status": "watch_only",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = self.run_wrapper(
                "feishu-summary",
                "--date",
                "2026-05-26",
                "--session",
                "pre-market",
                "--signals",
                str(signals),
                "--output",
                str(root / "feishu-summary.md"),
            )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "feishu-summary")
        self.assertEqual(payload["summary"]["watch_only"], 1)
        self.assertTrue(payload["artifacts"][0].endswith("feishu-summary.md"))

    def test_pre_market_expected_outputs_include_signals_sidecar(self):
        proc = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"report_date": "2026-05-26", "context_path": "report/2026-05-26/pre-market-context.json"}),
            stderr="",
        )
        args = Namespace(
            watchlist="config/watchlist.json",
            interval="1day",
            timezone="America/New_York",
            date=None,
            snapshot_date=None,
            skip_non_trading_day=False,
        )

        with patch.object(trading_copilot, "run_child", return_value=proc), patch.object(
            trading_copilot, "emit", side_effect=SystemExit
        ) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_pre_market(args)

        payload = emit.call_args.args[0]
        self.assertIn("report/2026-05-26/pre-market-signals.json", payload["expected_agent_outputs"])

    def test_pre_market_injects_external_disclosure_artifact_at_wrapper_layer(self):
        proc = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"report_date": "2026-05-26", "context_path": "report/2026-05-26/pre-market-context.json"}),
            stderr="",
        )
        disclosure = {
            "status": "success",
            "artifacts": ["report/2026-05-26/external-disclosures/trump-trades.json"],
            "summary": {"matched_transactions": 1},
        }
        args = Namespace(
            watchlist="config/watchlist.json",
            interval="1day",
            timezone="America/New_York",
            date=None,
            snapshot_date=None,
            skip_non_trading_day=False,
            include_agent_research=False,
            agent_symbol=[],
            include_external_disclosures=True,
            external_disclosure_symbol=["MU"],
            external_disclosure_input=None,
            external_disclosure_lookback_days=120,
        )

        with patch.object(trading_copilot, "run_child", return_value=proc), patch.object(
            trading_copilot, "run_external_disclosure_pipeline", return_value=disclosure
        ) as external_disclosures, patch.object(trading_copilot, "emit", side_effect=SystemExit) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_pre_market(args)

        external_disclosures.assert_called_once_with(
            date="2026-05-26",
            symbols=["MU"],
            input_path=None,
            lookback_days=120,
        )
        payload = emit.call_args.args[0]
        self.assertIn("external_disclosures", payload)
        self.assertIn(
            "report/2026-05-26/external-disclosures/trump-trades.json",
            payload["next_agent_inputs"],
        )

    def test_pre_market_include_agent_research_injects_artifacts_at_wrapper_layer(self):
        proc = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"report_date": "2026-05-26", "context_path": "report/2026-05-26/pre-market-context.json"}),
            stderr="",
        )
        research = {
            "status": "success",
            "artifacts": ["report/2026-05-26/agents/MU/decision.json"],
            "symbols": ["MU"],
        }
        args = Namespace(
            watchlist="config/watchlist.json",
            interval="1day",
            timezone="America/New_York",
            date=None,
            snapshot_date=None,
            skip_non_trading_day=False,
            include_agent_research=True,
            agent_symbol=["MU"],
        )

        with patch.object(trading_copilot, "run_child", return_value=proc), patch.object(
            trading_copilot, "run_agent_research_pipeline", return_value=research
        ) as agent_research, patch.object(trading_copilot, "emit", side_effect=SystemExit) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_pre_market(args)

        agent_research.assert_called_once_with(
            date="2026-05-26",
            symbols=["MU"],
            context_path="report/2026-05-26/pre-market-context.json",
            external_disclosures_path=None,
        )
        payload = emit.call_args.args[0]
        self.assertIn("report/2026-05-26/agents/MU/decision.json", payload["next_agent_inputs"])
        self.assertIn("agent_research", payload)

    def test_post_market_expected_outputs_include_signals_sidecar(self):
        proc = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"snapshot_date": "2026-05-26", "snapshot_path": "report/2026-05-26/daily-snapshot.json"}),
            stderr="",
        )
        args = Namespace(
            watchlist="config/watchlist.json",
            interval="1day",
            outputsize=200,
            timezone="America/New_York",
            date=None,
            skip_non_trading_day=False,
            sp500_screen=False,
            sp500_top=100,
            sp500_candidates=15,
            sp500_source="ishares_ivv",
            extra_symbol=[],
            include_journal_signals=False,
            include_position_symbols=False,
            market_data_source="longbridge",
            fallback_market_data_source="twelve",
            longbridge_cli=None,
            longbridge_default_market="US",
        )

        with patch.object(trading_copilot, "run_child", return_value=proc), patch.object(
            trading_copilot, "emit", side_effect=SystemExit
        ) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_post_market(args)

        payload = emit.call_args.args[0]
        self.assertIn("report/2026-05-26/post-market-signals.json", payload["expected_agent_outputs"])
        self.assertIn("report/2026-05-26/intraday.md", payload["next_agent_inputs"])
        self.assertIn("runtime/intraday/2026-05-26/state.json", payload["next_agent_inputs"])
        self.assertIn("runtime/intraday/2026-05-26/events.jsonl", payload["next_agent_inputs"])

    def test_post_market_include_agent_research_injects_artifacts_at_wrapper_layer(self):
        proc = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"snapshot_date": "2026-05-26", "snapshot_path": "report/2026-05-26/daily-snapshot.json"}),
            stderr="",
        )
        research = {
            "status": "success",
            "artifacts": ["report/2026-05-26/agents/MU/decision.json"],
            "symbols": ["MU"],
        }
        args = Namespace(
            watchlist="config/watchlist.json",
            interval="1day",
            outputsize=200,
            timezone="America/New_York",
            date=None,
            skip_non_trading_day=False,
            sp500_screen=False,
            sp500_top=100,
            sp500_candidates=15,
            sp500_source="ishares_ivv",
            extra_symbol=[],
            include_journal_signals=False,
            include_position_symbols=False,
            market_data_source="longbridge",
            fallback_market_data_source="twelve",
            longbridge_cli=None,
            longbridge_default_market="US",
            include_agent_research=True,
            agent_symbol=["MU"],
        )

        with patch.object(trading_copilot, "run_child", return_value=proc), patch.object(
            trading_copilot, "run_agent_research_pipeline", return_value=research
        ) as agent_research, patch.object(trading_copilot, "emit", side_effect=SystemExit) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_post_market(args)

        agent_research.assert_called_once_with(
            date="2026-05-26",
            symbols=["MU"],
            snapshot_path="report/2026-05-26/daily-snapshot.json",
        )
        payload = emit.call_args.args[0]
        self.assertIn("report/2026-05-26/agents/MU/decision.json", payload["next_agent_inputs"])
        self.assertIn("agent_research", payload)

    def test_data_quality_wrapper_contract(self):
        proc = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "success",
                    "date": "2026-05-26",
                    "quality_status": "warn",
                    "artifacts": ["report/2026-05-26/data-quality.json", "report/2026-05-26/data-quality.md"],
                    "focused_fallback_symbols": [{"symbol": "MU"}],
                    "missing_focused_symbols": [],
                }
            ),
            stderr="",
        )
        args = Namespace(
            date="2026-05-26",
            session="all",
            snapshot=None,
            account_snapshot=None,
            output_json=None,
            output_md=None,
            account_delta_threshold_pct=5.0,
            abnormal_move_threshold_pct=20.0,
        )

        with patch.object(trading_copilot, "run_child", return_value=proc), patch.object(
            trading_copilot, "emit", side_effect=SystemExit
        ) as emit:
            with self.assertRaises(SystemExit):
                trading_copilot.run_data_quality(args)

        payload = emit.call_args.args[0]
        self.assertEqual(payload["workflow"], "data-quality")
        self.assertEqual(payload["quality_status"], "warn")
        self.assertEqual(payload["focused_fallback_symbols"], [{"symbol": "MU"}])

    def test_pre_market_deliver_orders_data_quality_before_journal_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signals = root / "pre-market-signals.json"
            signals.write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "status": "planned",
                                "plan_type": "watch_only",
                                "execution_status": "watch_only",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manifest = root / "pre-market-run-manifest.json"
            summary = root / "feishu-summary.md"
            calls = []

            def fake_run_child(command):
                calls.append(command[0])
                payload = {"status": "success", "artifacts": []}
                if command[0] == "script/validate_report.py":
                    payload = {"status": "pass", "errors": [], "warnings": [], "checked_reports": []}
                elif command[0] == "script/validate_trade_plan.py":
                    payload = {"status": "pass", "errors": [], "warnings": [], "checked_artifacts": [str(signals)]}
                elif command[0] == "script/data_quality.py":
                    payload = {
                        "status": "success",
                        "quality_status": "pass",
                        "artifacts": ["report/2026-05-26/data-quality.json"],
                        "focused_fallback_symbols": [],
                        "missing_focused_symbols": [],
                    }
                elif command[0] == "script/extract_report_signals.py":
                    payload = {"status": "success", "artifacts": [], "appended": [{"symbol": "MU"}], "skipped_duplicates": []}
                elif command[0] == "script/feishu_summary.py":
                    payload = {"status": "success", "output": str(summary), "summary": {"watch_only": 1}}
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

            args = Namespace(
                date="2026-05-26",
                watchlist="config/watchlist.json",
                report=None,
                signals=str(signals),
                skip_agent_validation=True,
                no_append_journal=False,
                journal_dir=str(root / "journal"),
                skip_account=True,
                account_snapshot=None,
                position_config="config/position_review.json",
                sync_longbridge=False,
                execute_sync=False,
                group_name="今日关注",
                sync_mode="add",
                sync_method="auto",
                max_symbols=3,
                longbridge_cli=None,
                learning_dir=str(root / "learning"),
                manifest_output=str(manifest),
                summary_output=str(summary),
                delivery_guard=False,
                delivery_kind="exec-brief",
                mark_sent=False,
            )

            with patch.object(trading_copilot, "run_child", side_effect=fake_run_child), patch.object(
                trading_copilot, "emit", side_effect=SystemExit
            ) as emit:
                with self.assertRaises(SystemExit):
                    trading_copilot.run_pre_market_deliver(args)

            self.assertLess(calls.index("script/data_quality.py"), calls.index("script/extract_report_signals.py"))
            self.assertTrue(manifest.exists())
            payload = emit.call_args.args[0]
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["workflow"], "pre-market-deliver")
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["status"], "success")
            self.assertEqual(manifest_payload["focused_symbols"], ["MU"])
            self.assertIn("focus-selection", [step["name"] for step in manifest_payload["steps"]])

    def test_post_market_deliver_orders_data_quality_before_journal_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signals = root / "post-market-signals.json"
            signals.write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "post-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "status": "planned",
                                "plan_type": "watch_only",
                                "execution_status": "watch_only",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manifest = root / "post-market-run-manifest.json"
            summary = root / "feishu-summary.md"
            calls = []

            def fake_run_child(command):
                calls.append(command[0])
                payload = {"status": "success", "artifacts": []}
                if command[0] == "script/validate_report.py":
                    payload = {"status": "pass", "errors": [], "warnings": [], "checked_reports": []}
                elif command[0] == "script/validate_trade_plan.py":
                    payload = {"status": "pass", "errors": [], "warnings": [], "checked_artifacts": [str(signals)]}
                elif command[0] == "script/data_quality.py":
                    payload = {
                        "status": "success",
                        "quality_status": "pass",
                        "artifacts": ["report/2026-05-26/data-quality.json"],
                        "focused_fallback_symbols": [],
                        "missing_focused_symbols": [],
                    }
                elif command[0] == "script/focus_selection.py":
                    payload = {"status": "success", "output": str(root / "focus-selection.json"), "selected": 1}
                elif command[0] == "script/extract_report_signals.py":
                    payload = {"status": "success", "artifacts": [], "appended": [{"symbol": "MU"}], "skipped_duplicates": []}
                elif command[0] == "script/journal_review.py":
                    payload = {"status": "success", "outcomes_path": str(root / "outcomes.jsonl"), "summary": {}}
                elif command[0] == "script/plan_review.py":
                    payload = {"status": "success", "artifacts": [str(root / "plan-review.json")], "summary": {}}
                elif command[0] == "script/daily_self_review.py":
                    payload = {"status": "success", "output": str(root / "self-review.md"), "summary": {}}
                elif command[0] == "script/feishu_summary.py":
                    payload = {"status": "success", "output": str(summary), "summary": {"watch_only": 1}}
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

            args = Namespace(
                date="2026-05-26",
                watchlist="config/watchlist.json",
                report=None,
                signals=str(signals),
                snapshot=None,
                skip_agent_validation=True,
                skip_outcomes=False,
                append_outcomes=False,
                no_append_journal=False,
                journal_dir=str(root / "journal"),
                skip_account=True,
                account_snapshot=None,
                position_config="config/position_review.json",
                skip_plan_review=False,
                append_lessons=False,
                skip_learning_review=False,
                learning_lookback_days=20,
                skip_self_review=False,
                append_self_review=False,
                sync_longbridge=False,
                execute_sync=False,
                group_name="今日关注",
                sync_mode="replace",
                sync_method="auto",
                max_symbols=3,
                longbridge_cli=None,
                learning_dir=str(root / "learning"),
                manifest_output=str(manifest),
                summary_output=str(summary),
                delivery_guard=False,
                mark_sent=False,
            )

            with patch.object(trading_copilot, "run_child", side_effect=fake_run_child), patch.object(
                trading_copilot, "emit", side_effect=SystemExit
            ) as emit:
                with self.assertRaises(SystemExit):
                    trading_copilot.run_post_market_deliver(args)

            self.assertLess(calls.index("script/data_quality.py"), calls.index("script/extract_report_signals.py"))
            self.assertLess(calls.index("script/focus_selection.py"), calls.index("script/extract_report_signals.py"))
            self.assertTrue(manifest.exists())
            payload = emit.call_args.args[0]
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["workflow"], "post-market-deliver")
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["status"], "success")
            self.assertEqual(manifest_payload["focused_symbols"], ["MU"])

    def test_account_snapshot_wrapper_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "account.json"
            fixture.write_text(json.dumps({"account": {"net_liquidation": 1000}, "positions": []}), encoding="utf-8")

            payload = self.run_wrapper(
                "account-snapshot",
                "--date",
                "2026-05-27",
                "--input",
                str(fixture),
                "--output",
                str(root / "account-snapshot.json"),
            )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "account-snapshot")
        self.assertEqual(payload["date"], "2026-05-27")
        self.assertEqual(payload["positions_count"], 0)

    def test_position_review_wrapper_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = root / "account-snapshot.json"
            account.write_text(
                json.dumps(
                    {
                        "account": {"net_liquidation": 1000},
                        "positions": [{"symbol": "MU", "last_price": 96, "market_value": 100}],
                    }
                ),
                encoding="utf-8",
            )
            signals = root / "signals.json"
            signals.write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "trigger": {"price": 100},
                                "invalidation": {"price": 95},
                                "risk": {"max_risk_pct": 1},
                                "status": "planned",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = self.run_wrapper(
                "position-review",
                "--date",
                "2026-05-27",
                "--account-snapshot",
                str(account),
                "--signals",
                str(signals),
                "--output",
                str(root / "position-review"),
                "--append",
                "--journal-dir",
                str(root / "journal"),
            )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["workflow"], "position-review")
        self.assertEqual(payload["summary"]["positions"], 1)


if __name__ == "__main__":
    unittest.main()
