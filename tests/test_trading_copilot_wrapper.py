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
