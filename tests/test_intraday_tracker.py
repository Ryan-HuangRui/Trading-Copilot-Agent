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
            self.assertEqual(state["symbols"]["INTC"]["state"], "price_touched")
            self.assertNotIn("ORCL", state["symbols"])
            markdown = intraday_md.read_text(encoding="utf-8")
            self.assertIn("previous note", markdown)
            self.assertIn("MU: near_trigger", markdown)
            self.assertIn("INTC: price_touched", markdown)
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
                        "INTC": {"symbol": "INTC", "state": "price_touched", "bar_timestamp": "2026-05-26 10:30:00"},
                    },
                },
            )

            result = intraday_tracker.run(self.args(root))

            self.assertEqual(result["summary"]["events"], 0)
            events_path = root / "runtime" / "intraday" / "2026-05-26" / "events.jsonl"
            self.assertFalse(events_path.exists())

    def test_run_suppresses_repeated_near_trigger_on_new_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            write_json(
                root / "runtime" / "intraday" / "2026-05-26" / "state.json",
                {
                    "date": "2026-05-26",
                    "symbols": {
                        "MU": {"symbol": "MU", "state": "near_trigger", "bar_timestamp": "2026-05-26 10:30:00"},
                    },
                },
            )
            write_json(
                root / "report" / "latest-monitor.json",
                {
                    "scans": [
                        {
                            "symbol": "MU",
                            "status": "临近触发",
                            "reason": "still near trigger",
                            "trigger": 100,
                            "stop": 95,
                            "risk_quality": "watch_only",
                            "bar_timestamp": "2026-05-26 10:35:00",
                        }
                    ]
                },
            )

            result = intraday_tracker.run(self.args(root))

            self.assertEqual(result["summary"]["events"], 0)
            self.assertFalse((root / "runtime" / "intraday" / "2026-05-26" / "events.jsonl").exists())
            state = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["symbols"]["MU"]["bar_timestamp"], "2026-05-26 10:35:00")

    def test_run_suppresses_repeated_one_shot_state_on_new_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed_inputs(root)
            write_json(
                root / "runtime" / "intraday" / "2026-05-26" / "state.json",
                {
                    "date": "2026-05-26",
                    "symbols": {
                        "MU": {
                            "symbol": "MU",
                            "state": "triggered_and_invalidated",
                            "bar_timestamp": "2026-05-26 10:30:00",
                        },
                    },
                },
            )

            self.assertFalse(
                intraday_tracker.should_emit_event(
                    {"state": "triggered_and_invalidated", "bar_timestamp": "2026-05-26 10:30:00"},
                    {"state": "triggered_and_invalidated", "bar_timestamp": "2026-05-26 10:35:00"},
                )
            )

    def test_run_ignores_stale_previous_day_monitor_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "report" / "2026-05-26" / "pre-market-signals.json",
                {
                    "date": "2026-05-26",
                    "session": "pre-market",
                    "signals": [
                        {
                            "symbol": "DELL",
                            "trigger": {"type": "break_above", "price": 100},
                            "invalidation": {"type": "break_below", "price": 95},
                        }
                    ],
                },
            )
            write_json(root / "config" / "intraday_watchlist.json", {"symbols": []})
            write_json(
                root / "report" / "latest-monitor.json",
                {
                    "scans": [
                        {
                            "symbol": "DELL",
                            "status": "临近触发",
                            "reason": "previous day close was near trigger",
                            "trigger": 100,
                            "stop": 95,
                            "bar_timestamp": "2026-05-22 15:55:00",
                        }
                    ]
                },
            )

            result = intraday_tracker.run(self.args(root))

            self.assertEqual(result["summary"]["events"], 0)
            state = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["symbols"]["DELL"]["state"], "stale")
            self.assertIn("stale", state["symbols"]["DELL"]["reason"])
            self.assertFalse((root / "runtime" / "intraday" / "2026-05-26" / "events.jsonl").exists())

    def test_run_classifies_no_chase_failed_hold_and_daily_range_touched_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "report" / "2026-05-26" / "pre-market-signals.json",
                {
                    "date": "2026-05-26",
                    "session": "pre-market",
                    "signals": [
                        {
                            "symbol": "MU",
                            "entry": {
                                "trigger_price": 100,
                                "no_chase_rule": "gap above trigger too far from stop",
                            },
                            "trigger": {"type": "break_above", "price": 100},
                            "invalidation": {"type": "break_below", "price": 95},
                        }
                    ],
                },
            )
            write_json(root / "config" / "intraday_watchlist.json", {"symbols": []})
            write_json(
                root / "report" / "latest-monitor.json",
                {
                    "scans": [
                        {
                            "symbol": "MU",
                            "status": "可执行",
                            "reason": "gap above trigger",
                            "last": 106,
                            "trigger": 100,
                            "stop": 95,
                            "risk_quality": "acceptable",
                            "bar_timestamp": "2026-05-26 09:30:00",
                            "latest_bar": {"dt": "2026-05-26 09:30:00", "open": 106, "high": 108, "low": 105, "close": 106},
                        }
                    ]
                },
            )

            first = intraday_tracker.run(self.args(root, as_of="2026-05-26T13:35:00+00:00"))
            state = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(first["summary"]["events"], 1)
            self.assertEqual(state["symbols"]["MU"]["state"], "no_chase_gap")

            write_json(
                root / "report" / "latest-monitor.json",
                {
                    "scans": [
                        {
                            "symbol": "MU",
                            "status": "观察中",
                            "reason": "lost trigger hold",
                            "last": 98,
                            "trigger": 100,
                            "stop": 95,
                            "bar_timestamp": "2026-05-26 09:35:00",
                        }
                    ]
                },
            )
            second = intraday_tracker.run(self.args(root, as_of="2026-05-26T13:40:00+00:00"))
            state = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(second["summary"]["events"], 1)
            self.assertEqual(state["symbols"]["MU"]["state"], "price_touched")

            write_json(
                root / "report" / "latest-monitor.json",
                {
                    "scans": [
                        {
                            "symbol": "MU",
                            "status": "观察中",
                            "reason": "fell through invalidation",
                            "last": 94,
                            "trigger": 100,
                            "stop": 95,
                            "bar_timestamp": "2026-05-26 09:40:00",
                            "price_evidence": {"key_levels": {"intraday_high": 108, "intraday_low": 94}},
                        }
                    ]
                },
            )
            third = intraday_tracker.run(self.args(root, as_of="2026-05-26T13:45:00+00:00"))
            state = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(third["summary"]["events"], 1)
            self.assertEqual(state["symbols"]["MU"]["state"], "daily_range_touched_both_order_unknown")
            self.assertEqual(state["symbols"]["MU"]["price_observation_state"], "daily_range_touched_both_order_unknown")
            self.assertEqual(state["symbols"]["MU"]["trade_candidate_state"], None)

    def test_pre_market_plan_levels_override_monitor_dynamic_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "report" / "2026-05-26" / "pre-market-signals.json",
                {
                    "signals": [
                        {
                            "symbol": "MU",
                            "trigger": {"type": "break_above", "price": 100},
                            "invalidation": {"type": "break_below", "price": 95},
                        }
                    ]
                },
            )
            write_json(root / "config" / "intraday_watchlist.json", {"symbols": []})
            write_json(
                root / "report" / "latest-monitor.json",
                {
                    "scans": [
                        {
                            "symbol": "MU",
                            "status": "观察中",
                            "last": 104,
                            "trigger_detail": {"type": "dynamic_break_above", "price": 110},
                            "invalidation_detail": {"type": "dynamic_break_below", "price": 102},
                            "bar_timestamp": "2026-05-26 10:30:00",
                            "price_evidence": {"key_levels": {"intraday_high": 104, "intraday_low": 101}},
                        }
                    ]
                },
            )

            intraday_tracker.run(self.args(root))

            state = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "state.json").read_text(encoding="utf-8"))
            row = state["symbols"]["MU"]
            self.assertEqual(row["trigger_price"], 100.0)
            self.assertEqual(row["invalidation_price"], 95.0)
            self.assertEqual(row["level_source"], "pre_market_plan")
            self.assertEqual(row["monitor_trigger"], 110.0)
            self.assertEqual(row["monitor_stop"], 102.0)
            self.assertEqual(row["state"], "price_touched")

    def test_manual_watchlist_uses_monitor_dynamic_levels_without_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "report" / "2026-05-26" / "pre-market-signals.json", {"signals": []})
            write_json(root / "config" / "intraday_watchlist.json", {"symbols": ["AMD"]})
            write_json(
                root / "report" / "latest-monitor.json",
                {
                    "scans": [
                        {
                            "symbol": "AMD",
                            "status": "观察中",
                            "last": 101,
                            "trigger_detail": {"type": "dynamic_break_above", "price": 100},
                            "invalidation_detail": {"type": "dynamic_break_below", "price": 95},
                            "bar_timestamp": "2026-05-26 10:30:00",
                        }
                    ]
                },
            )

            intraday_tracker.run(self.args(root))

            state = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "state.json").read_text(encoding="utf-8"))
            row = state["symbols"]["AMD"]
            self.assertEqual(row["trigger_price"], 100.0)
            self.assertEqual(row["invalidation_price"], 95.0)
            self.assertEqual(row["level_source"], "monitor_scan_dynamic")
            self.assertEqual(row["state"], "price_touched")

    def test_invalid_none_invalidation_detail_does_not_invalidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "report" / "2026-05-26" / "pre-market-signals.json", {"signals": []})
            write_json(root / "config" / "intraday_watchlist.json", {"symbols": ["AMD"]})
            write_json(
                root / "report" / "latest-monitor.json",
                {
                    "scans": [
                        {
                            "symbol": "AMD",
                            "status": "观察中",
                            "last": 94,
                            "trigger_detail": {"type": "dynamic_break_above", "price": 100},
                            "invalidation_detail": {"type": "none", "price": 95},
                            "bar_timestamp": "2026-05-26 10:30:00",
                            "price_evidence": {"key_levels": {"intraday_low": 94}},
                        }
                    ]
                },
            )

            intraday_tracker.run(self.args(root))

            state = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "state.json").read_text(encoding="utf-8"))
            row = state["symbols"]["AMD"]
            self.assertEqual(row["invalidation_price"], None)
            self.assertEqual(row["state"], "waiting")

    def test_run_emits_risk_warning_for_vwap_and_previous_low_breaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "report" / "2026-05-26" / "pre-market-signals.json", {"signals": []})
            write_json(root / "config" / "intraday_watchlist.json", {"symbols": ["AVGO"]})
            write_json(
                root / "report" / "latest-monitor.json",
                {
                    "scans": [
                        {
                            "symbol": "AVGO",
                            "status": "观察中",
                            "reason": "structure incomplete",
                            "last": 389,
                            "trigger": 392,
                            "stop": 388,
                            "bar_timestamp": "2026-05-26 11:00:00",
                            "price_evidence": {
                                "key_levels": {
                                    "vwap": 393,
                                    "previous_day_low": 391,
                                }
                            },
                        }
                    ]
                },
            )

            result = intraday_tracker.run(self.args(root, top_n=5))

            self.assertEqual(result["summary"]["events"], 1)
            state = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["symbols"]["AVGO"]["state"], "risk_warning")
            self.assertEqual(state["symbols"]["AVGO"]["risk_alerts"], ["below_vwap", "below_previous_day_low"])
            events = [
                json.loads(line)
                for line in (root / "runtime" / "intraday" / "2026-05-26" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[0]["risk_alerts"], ["below_vwap", "below_previous_day_low"])


if __name__ == "__main__":
    unittest.main()
