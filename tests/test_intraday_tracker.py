import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import intraday_tracker


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class IntradayTrackerTest(unittest.TestCase):
    def args(self, root: Path, **overrides):
        defaults = {
            "repo_root": str(root),
            "date": "2026-05-26",
            "pre_market_signals": None,
            "manual_watchlist": "config/intraday_watchlist.json",
            "monitor": "report/latest-monitor.json",
            "top_n": 1,
            "state": None,
            "events": None,
            "markdown": None,
            "timezone": "America/New_York",
            "as_of": "2026-05-26T14:35:00+00:00",
        }
        defaults.update(overrides)
        return Namespace(**defaults)

    def seed_inputs(self, root: Path) -> None:
        write_json(
            root / "report" / "2026-05-26" / "pre-market-signals.json",
            {
                "date": "2026-05-26",
                "session": "pre-market",
                "signals": [
                    {
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "trigger": {"type": "break_above", "price": 100, "text": "break 100"},
                        "invalidation": {"type": "break_below", "price": 95, "text": "below 95"},
                    },
                    {
                        "symbol": "ORCL",
                        "setup": "strong_breakout_trend_following.md",
                        "trigger": {"type": "break_above", "price": 120, "text": "break 120"},
                        "invalidation": {"type": "break_below", "price": 116, "text": "below 116"},
                    },
                ],
            },
        )
        write_json(root / "config" / "intraday_watchlist.json", {"symbols": ["INTC", "MU"]})
        write_json(
            root / "report" / "latest-monitor.json",
            {
                "risk_per_trade_pct": 1,
                "interval": "5min",
                "scans": [
                    {
                        "symbol": "MU",
                        "status": "临近触发",
                        "reason": "price is near trigger",
                        "trigger": 100,
                        "stop": 95,
                        "risk_quality": "watch_only",
                        "bar_timestamp": "2026-05-26 10:30:00",
                    },
                    {
                        "symbol": "INTC",
                        "status": "可执行",
                        "reason": "breakout confirmed",
                        "trigger": 31,
                        "stop": 29,
                        "risk_quality": "acceptable",
                        "bar_timestamp": "2026-05-26 10:30:00",
                    },
                    {
                        "symbol": "ORCL",
                        "status": "观察中",
                        "reason": "not in top one",
                    },
                ],
            },
        )

    def test_run_reads_existing_markdown_appends_state_and_changed_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            intraday_md = root / "report" / "2026-05-26" / "intraday.md"
            intraday_md.write_text("# 2026-05-26 Intraday Tracker\n\n## 10:30 ET\n- previous note\n", encoding="utf-8")

            result = intraday_tracker.run(self.args(root))

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["summary"]["tracked"], 2)
            self.assertEqual(result["summary"]["events"], 2)
            state = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["symbols"]["MU"]["state"], "near_trigger")
            self.assertEqual(state["symbols"]["INTC"]["state"], "triggered")
            self.assertNotIn("ORCL", state["symbols"])
            markdown = intraday_md.read_text(encoding="utf-8")
            self.assertIn("previous note", markdown)
            self.assertIn("MU: near_trigger", markdown)
            self.assertIn("INTC: triggered", markdown)
            events = [
                json.loads(line)
                for line in (root / "runtime" / "intraday" / "2026-05-26" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([event["symbol"] for event in events], ["MU", "INTC"])
            self.assertTrue(all(event["notify"] for event in events))

    def test_run_does_not_append_duplicate_events_when_state_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            write_json(
                root / "runtime" / "intraday" / "2026-05-26" / "state.json",
                {
                    "date": "2026-05-26",
                    "symbols": {
                        "MU": {"symbol": "MU", "state": "near_trigger", "bar_timestamp": "2026-05-26 10:30:00"},
                        "INTC": {"symbol": "INTC", "state": "triggered", "bar_timestamp": "2026-05-26 10:30:00"},
                    },
                },
            )

            result = intraday_tracker.run(self.args(root))

            self.assertEqual(result["summary"]["events"], 0)
            events_path = root / "runtime" / "intraday" / "2026-05-26" / "events.jsonl"
            self.assertFalse(events_path.exists())


if __name__ == "__main__":
    unittest.main()
