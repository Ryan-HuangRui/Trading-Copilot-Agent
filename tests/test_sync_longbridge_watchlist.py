import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from sync_longbridge_watchlist import (
    detect_other_group_drift,
    extract_focus_symbols,
    group_symbol_payloads,
    load_symbols,
    normalize_symbol,
    ordered_unique,
    sync_with_longbridge_cli,
    watchlist_update_args,
)


class SyncLongbridgeWatchlistTest(unittest.TestCase):
    def test_extract_pre_market_focus_symbols_filters_non_ticker_tokens(self):
        markdown = "- 今日最多3个重点标的：AI, API, MU, MA20, NO, PLTR, R, TSM\n"

        self.assertEqual(extract_focus_symbols(markdown, "pre-market"), ["MU", "PLTR", "TSM"])

    def test_extract_post_market_section_symbols(self):
        markdown = """
## 明日观察清单
- `MU`：突破后回踩观察
- API：这不是股票代码
- NVDA：趋势延续观察

## 复盘结论
- 结束
"""

        self.assertEqual(extract_focus_symbols(markdown, "post-market"), ["MU", "NVDA"])

    def test_normalize_symbol_adds_default_market_only_for_bare_tickers(self):
        self.assertEqual(normalize_symbol("mu", "US"), "MU.US")
        self.assertEqual(normalize_symbol("00700.HK", "US"), "00700.HK")

    def test_ordered_unique_preserves_first_seen_order(self):
        self.assertEqual(ordered_unique(["MU", "NVDA", "MU", "PLTR"]), ["MU", "NVDA", "PLTR"])

    def test_group_symbol_payloads_handles_cli_shapes(self):
        payload = {
            "securities": [
                {"symbol": "MU.US"},
                {"security": "NVDA.US"},
                "PLTR.US",
            ]
        }

        self.assertEqual(group_symbol_payloads(payload), ["MU.US", "NVDA.US", "PLTR.US"])

    def test_watchlist_update_args_uses_group_update_not_global_delete(self):
        args = watchlist_update_args("group-1", ["MU.US", "NVDA.US"], "replace")

        self.assertEqual(args[:5], ["watchlist", "update", "group-1", "--mode", "replace"])
        self.assertIn("--add", args)
        self.assertNotIn("delete", args)

    def test_detect_other_group_drift_ignores_target_group(self):
        before = [
            {"group_id": "focus", "group_name": "今日关注", "symbols": ["OLD.US"]},
            {"group_id": "core", "group_name": "核心自选", "symbols": ["KEEP.US"]},
        ]
        after = [
            {"group_id": "focus", "group_name": "今日关注", "symbols": ["NEW.US"]},
            {"group_id": "core", "group_name": "核心自选", "symbols": ["BAD.US"]},
        ]

        drift = detect_other_group_drift(before, after, "focus", "今日关注")

        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["before"]["group_name"], "核心自选")

    def test_sync_with_cli_restores_drifted_non_target_groups(self):
        initial_groups = {
            "groups": [
                {"id": "focus", "name": "今日关注", "securities": [{"symbol": "OLD.US"}]},
                {"id": "core", "name": "核心自选", "securities": [{"symbol": "KEEP.US"}]},
            ]
        }
        after_target_update = {
            "groups": [
                {"id": "focus", "name": "今日关注", "securities": [{"symbol": "NEW.US"}]},
                {"id": "core", "name": "核心自选", "securities": [{"symbol": "BAD.US"}]},
            ]
        }
        after_restore = {
            "groups": [
                {"id": "focus", "name": "今日关注", "securities": [{"symbol": "NEW.US"}]},
                {"id": "core", "name": "核心自选", "securities": [{"symbol": "KEEP.US"}]},
            ]
        }
        calls = []

        def fake_run_longbridge_cli(_cli, args):
            calls.append(args)
            if args == ["watchlist", "--format", "json"]:
                watchlist_reads = sum(1 for call in calls if call == ["watchlist", "--format", "json"])
                if watchlist_reads == 1:
                    return initial_groups
                if watchlist_reads == 2:
                    return after_target_update
                return after_restore
            return {}

        with patch("sync_longbridge_watchlist.run_longbridge_cli", side_effect=fake_run_longbridge_cli):
            result = sync_with_longbridge_cli(
                cli=sys.executable,
                group_name="今日关注",
                symbols=["NEW.US"],
                create=False,
                sync_mode="replace",
            )

        self.assertIn(
            ["watchlist", "update", "focus", "--mode", "replace", "--add", "NEW.US", "--format", "json"],
            calls,
        )
        self.assertIn(
            ["watchlist", "update", "core", "--mode", "replace", "--add", "KEEP.US", "--format", "json"],
            calls,
        )
        guard = result["other_groups_guard"]
        self.assertEqual(guard["checked_groups"], 1)
        self.assertEqual(guard["restored_groups"][0]["group_name"], "核心自选")
        self.assertEqual(guard["restored_groups"][0]["restored_symbols"], ["KEEP.US"])
        self.assertEqual(guard["remaining_drift"], [])

    def test_load_symbols_prefers_session_signals_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-05-26"
            report_dir.mkdir(parents=True)
            (report_dir / "exec-brief.md").write_text("- 今日最多3个重点标的：OLD\n", encoding="utf-8")
            (report_dir / "pre-market-signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "signals": [
                            {"symbol": "MU", "setup": "breakout_pullback_continuation.md", "status": "planned"},
                            {"symbol": "NVDA", "setup": "strong_breakout_trend_following.md", "status": "planned"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                symbol=None,
                report=None,
                signals=None,
                date="2026-05-26",
                session="pre-market",
                default_market="US",
                max_symbols=3,
            )

            self.assertEqual(load_symbols(args, root), ["MU.US", "NVDA.US"])


if __name__ == "__main__":
    unittest.main()
