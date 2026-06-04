import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowSmokeTest(unittest.TestCase):
    def test_workflow_smoke_test_runs_fixture_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup_dir = root / "knowledge" / "refined" / "setups"
            setup_dir.mkdir(parents=True)
            (setup_dir / "breakout_pullback_continuation.md").write_text("# setup\n", encoding="utf-8")
            (setup_dir / "strong_breakout_trend_following.md").write_text("# setup\n", encoding="utf-8")
            report_dir = root / "report" / "2026-05-26"
            report_dir.mkdir(parents=True)
            markdown = """# 今日盘前执行简版（2026-05-26）
## 总览
- 今日最多3个重点标的：MU
## 消息层汇总
### 特朗普持仓与交易变化
- 数据来源：未接入结构化 OGE/Open Cabinet 披露输入；本节不构成交易信号。
- 持仓变化：未获取到可核验的最新披露。
- 交易变化：未获取到可核验的最新披露。
- 对今日计划影响：只作为消息层风险背景，不能提升任何标的执行等级。
## 执行清单（逐标的）
### MU
- 参考 setup：breakout_pullback_continuation.md
- 触发条件：突破 100
- 失效条件：跌破 95
- 风险约束：单笔风险 <=1%
"""
            (report_dir / "exec-brief.md").write_text(markdown, encoding="utf-8")
            (report_dir / "pre-market.md").write_text(markdown, encoding="utf-8")
            (report_dir / "pre-market-signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "source_report": "report/2026-05-26/exec-brief.md",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "direction": "long",
                                "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
                                "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
                                "risk": {"max_risk_pct": 1, "max_account_risk_pct": 1, "risk_per_share": 5},
                                "status": "planned",
                                "plan_type": "trade_plan",
                                "execution_status": "conditional_executable",
                                "entry": {"trigger_price": 100, "order_type": "LO"},
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
            (report_dir / "daily-snapshot.json").write_text(
                json.dumps(
                    {
                        "snapshot_date": "2026-05-26",
                        "symbols": [
                            {
                                "symbol": "MU",
                                "latest": {
                                    "datetime": "2026-05-26",
                                    "open": "98",
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
            (root / "report").mkdir(exist_ok=True)
            (root / "report" / "latest-monitor.json").write_text(
                json.dumps(
                    {
                        "risk_per_trade_pct": 1,
                        "scans": [
                            {
                                "symbol": "MU",
                                "status": "可执行",
                                "setup": "strong_breakout_trend_following.md",
                                "setup_files": ["strong_breakout_trend_following.md"],
                                "trigger": 100,
                                "trigger_detail": {"price": 100},
                                "stop": 95,
                                "invalidation_detail": {"price": 95},
                                "journal_appendable": True,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            account_fixture = root / "account-fixture.json"
            account_fixture.write_text(
                json.dumps(
                    {
                        "account": {"net_liquidation": 100000, "cash": 90000, "currency": "USD"},
                        "positions": [
                            {
                                "symbol": "MU.US",
                                "quantity": 10,
                                "last_price": 100,
                                "market_value": 1000,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            paper_fixture = root / "paper-fixture.json"
            paper_fixture.write_text(
                json.dumps(
                    {
                        "auth": {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}},
                        "account": [{"net_assets": "100000", "total_cash": "25000", "currency": "USD"}],
                        "positions": [],
                        "orders": [{"order_id": "paper-o-1", "symbol": "MU.US", "side": "buy", "quantity": 200}],
                        "executions": [
                            {
                                "order_id": "paper-o-1",
                                "symbol": "MU.US",
                                "side": "buy",
                                "quantity": 200,
                                "price": 100.2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "workflow_smoke_test.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                    "--week",
                    "2026-W22",
                    "--account-input",
                    str(account_fixture),
                    "--paper-input",
                    str(paper_fixture),
                    "--paper-lifecycle-smoke",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["steps"]["validate-report"]["validation"]["status"], "pass")
            self.assertEqual(payload["steps"]["validate-trade-plan"]["validation"]["status"], "pass")
            self.assertIn("account-snapshot", payload["steps"])
            self.assertIn("position-review", payload["steps"])
            self.assertIn("paper-account-snapshot", payload["steps"])
            self.assertIn("paper-trade-preview", payload["steps"])
            self.assertIn("paper-trade-submit", payload["steps"])
            self.assertIn("paper-trade-review", payload["steps"])
            self.assertIn("paper-order-sync", payload["steps"])
            self.assertIn("paper-protective-stop-plan", payload["steps"])
            self.assertIn("paper-take-profit-plan", payload["steps"])
            self.assertIn("paper-exit-plan", payload["steps"])
            self.assertIn("paper-break-even-stop-plan", payload["steps"])
            self.assertIn("paper-event-ledger", payload["steps"])
            self.assertIn("paper-execution-review", payload["steps"])
            self.assertIn("intraday-lifecycle-append", payload["steps"])
            self.assertEqual(payload["steps"]["paper-trade-preview"]["summary"]["ready"], 1)
            self.assertEqual(payload["steps"]["paper-trade-submit"]["summary"]["ready"], 1)
            self.assertEqual(payload["steps"]["paper-trade-review"]["summary"]["filled"], 1)
            self.assertEqual(payload["steps"]["paper-order-sync"]["summary"]["filled"], 1)
            self.assertEqual(payload["steps"]["paper-protective-stop-plan"]["summary"]["stop_candidates"], 1)
            self.assertEqual(payload["steps"]["paper-take-profit-plan"]["summary"]["take_profit_candidates"], 1)
            self.assertEqual(payload["steps"]["paper-exit-plan"]["summary"]["exit_candidates"], 1)
            self.assertEqual(payload["steps"]["paper-break-even-stop-plan"]["summary"]["move_candidates"], 1)
            self.assertGreaterEqual(payload["steps"]["paper-event-ledger"]["summary"]["events_written_for_date"], 1)
            self.assertTrue(payload["steps"]["intraday-lifecycle-append"]["should_notify"])
            self.assertTrue((root / "report" / "2026-05-26" / "self-review.md").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "plan-review.md").exists())
            self.assertTrue((root / "report" / "learning" / "pattern-review.md").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "feishu-summary.md").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "position-review.json").exists())
            self.assertTrue((root / "runtime" / "paper" / "2026-05-26" / "paper-account-snapshot.json").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "paper-trade-preview.json").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "paper-trade-submission.json").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "paper-trade-review.json").exists())
            self.assertTrue((root / "runtime" / "paper" / "2026-05-26" / "paper-execution-state.json").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "paper-protective-stop-plan.json").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "paper-take-profit-plan.json").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "paper-exit-plan.json").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "paper-break-even-stop-plan.json").exists())
            self.assertTrue((root / "report" / "2026-05-26" / "intraday-lifecycle-summary.json").exists())
            self.assertTrue((root / "report" / "weekly" / "2026-W22.md").exists())


if __name__ == "__main__":
    unittest.main()
