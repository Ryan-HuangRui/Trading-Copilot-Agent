import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from extract_monitor_signals import extract
from validate_trade_plan import validate


class MonitorAgentDecisionTest(unittest.TestCase):
    def test_extract_monitor_signals_writes_watch_only_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monitor = root / "report" / "latest-monitor.json"
            monitor.parent.mkdir(parents=True)
            monitor.write_text(
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
                                "stop": 95,
                                "trigger_detail": {"type": "break_above", "price": 100, "text": "break"},
                                "invalidation_detail": {"type": "break_below", "price": 95, "text": "stop"},
                                "reason": "fixture scan",
                                "risk_quality": "acceptable",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            setup = root / "knowledge" / "refined" / "setups" / "strong_breakout_trend_following.md"
            setup.parent.mkdir(parents=True)
            setup.write_text("# setup\n", encoding="utf-8")

            result = extract(
                Namespace(
                    monitor=str(monitor),
                    date="2026-05-26",
                    timezone="America/New_York",
                    max_signals=5,
                    append=False,
                    journal_dir="runtime/journal",
                    repo_root=str(root),
                    signals_output=None,
                )
            )

            sidecar = (root / "report" / "2026-05-26" / "monitor-signals.json").resolve()
            self.assertEqual(result["signals_path"], str(sidecar))
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload["session"], "monitor")
            self.assertEqual(payload["signals"][0]["plan_type"], "watch_only")
            self.assertEqual(payload["signals"][0]["execution_status"], "watch_only")
            validation = validate(Namespace(date="2026-05-26", session="monitor", signals=str(sidecar), repo_root=str(root)))
            self.assertEqual(validation["status"], "pass")


if __name__ == "__main__":
    unittest.main()
