import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class IntradayOpportunityContextTest(unittest.TestCase):
    def test_context_builds_codex_review_inputs_and_sidecar_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup = root / "knowledge" / "refined" / "setups" / "strong_breakout_trend_following.md"
            setup.parent.mkdir(parents=True, exist_ok=True)
            setup.write_text("# setup\n", encoding="utf-8")
            write_json(
                root / "report" / "latest-monitor.json",
                {
                    "risk_per_trade_pct": 1,
                    "interval": "5min",
                    "scans": [
                        {
                            "symbol": "MU",
                            "status": "可执行",
                            "setup": "strong_breakout_trend_following.md",
                            "setup_files": ["strong_breakout_trend_following.md"],
                            "reason": "上升趋势+20Bar突破+放量",
                            "trigger": 100,
                            "stop": 95,
                            "target1": 112,
                            "trigger_detail": {"type": "break_above", "price": 100, "text": "breaks 100"},
                            "invalidation_detail": {"type": "break_below", "price": 95, "text": "breaks 95"},
                            "risk_quality": "acceptable",
                            "journal_appendable": True,
                            "bar_timestamp": "2026-05-26T14:30:00",
                        },
                        {"symbol": "NVDA", "status": "观察中", "setup": "NO VALID SETUP"},
                    ],
                },
            )
            write_json(
                root / "report" / "2026-05-26" / "pre-market-signals.json",
                {
                    "date": "2026-05-26",
                    "session": "pre-market",
                    "signals": [
                        {
                            "symbol": "MU",
                            "setup": "strong_breakout_trend_following.md",
                            "plan_type": "trade_plan",
                            "execution_status": "conditional_executable",
                            "entry": {"trigger_price": 99},
                            "stop": {"initial_stop": 95},
                            "take_profit": {"tp1": 112},
                            "risk": {"max_account_risk_pct": 1, "risk_per_share": 4},
                        }
                    ],
                },
            )
            write_json(
                root / "runtime" / "intraday" / "2026-05-26" / "state.json",
                {"symbols": {"MU": {"classification": "near_trigger", "last_price": 100.2}}},
            )
            write_json(
                root / "runtime" / "paper" / "2026-05-26" / "paper-execution-state.json",
                {"summary": {"filled": 0}, "orders": []},
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "intraday_opportunity_context.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "success")
            output = Path(payload["output"])
            context = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(context["date"], "2026-05-26")
            self.assertEqual(context["llm_contract"]["output_signals"], "report/2026-05-26/monitor-signals.json")
            self.assertEqual([item["symbol"] for item in context["candidate_scans"]], ["MU"])
            self.assertEqual(context["candidate_scans"][0]["premarket_plan"]["execution_status"], "conditional_executable")
            template = context["sidecar_template"]["signals"][0]
            self.assertEqual(template["symbol"], "MU")
            self.assertEqual(template["plan_type"], "watch_only")
            self.assertEqual(template["execution_status"], "watch_only")
            self.assertEqual(template["entry"]["trigger_price"], 100)
            self.assertEqual(template["take_profit"]["tp1"], 112)


if __name__ == "__main__":
    unittest.main()
