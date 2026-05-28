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
            self.assertIn("【今日可执行交易计划】", markdown)
            self.assertIn("MU：入场 100", markdown)
            self.assertIn("止损 95", markdown)
            self.assertIn("TP1 112", markdown)
            self.assertIn("【观察候选】", markdown)
            self.assertIn("AMD", markdown)
            self.assertIn("【NO TRADE】", markdown)
            self.assertIn("SNOW", markdown)
            self.assertIn("持仓复核摘要", markdown)
            self.assertIn("需人工复核：1", markdown)
            self.assertIn("昨日计划复盘", markdown)
            self.assertIn("今日新增 lesson", markdown)


if __name__ == "__main__":
    unittest.main()
