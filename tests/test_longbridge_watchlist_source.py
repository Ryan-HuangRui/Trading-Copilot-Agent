import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from longbridge_watchlist_source import (
    DEFAULT_SOURCE_GROUPS,
    collect_source_symbols,
    config_symbol,
    refresh_watchlist,
)


class LongbridgeWatchlistSourceTest(unittest.TestCase):
    def test_config_symbol_strips_us_suffix_only(self):
        self.assertEqual(config_symbol("mu.us"), "MU")
        self.assertEqual(config_symbol("00700.HK"), "00700.HK")

    def test_collect_source_symbols_uses_named_groups_in_config_order(self):
        snapshots = [
            {"group_id": "old", "group_name": "老朋友", "symbols": ["NVDA.US", "MU.US"]},
            {"group_id": "pos", "group_name": "持仓", "symbols": ["MU.US", "TSM.US"]},
            {"group_id": "skip", "group_name": "其他", "symbols": ["BAD.US"]},
        ]

        payload = collect_source_symbols(snapshots, ["持仓", "ibkr持仓", "老朋友"])

        self.assertEqual(payload["symbols"], ["MU", "TSM", "NVDA"])
        self.assertEqual(payload["longbridge_symbols"], ["MU.US", "TSM.US", "NVDA.US"])
        self.assertEqual(payload["missing_groups"], ["ibkr持仓"])

    def test_refresh_watchlist_overwrites_manual_file_from_longbridge_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            watchlist = root / "config" / "watchlist.json"
            watchlist.parent.mkdir()
            watchlist.write_text(json.dumps({"symbols": ["OLD"]}), encoding="utf-8")
            snapshots = [
                {"group_id": "pos", "group_name": "持仓", "symbols": ["MU.US", "TSM.US"]},
                {"group_id": "hbm", "group_name": "AI先进封装HBM", "symbols": ["NVDA.US", "MU.US"]},
            ]

            with patch("longbridge_watchlist_source.fetch_longbridge_snapshots", return_value=(snapshots, "cli", None)):
                payload = refresh_watchlist(
                    repo_root=root,
                    watchlist_path="config/watchlist.json",
                    group_names=DEFAULT_SOURCE_GROUPS,
                    method="auto",
                )

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["source"], "longbridge")
            self.assertTrue(payload["updated_watchlist"])
            self.assertEqual(json.loads(watchlist.read_text(encoding="utf-8"))["symbols"], ["MU", "TSM", "NVDA"])

    def test_refresh_watchlist_falls_back_to_manual_without_overwriting_when_longbridge_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            watchlist = root / "config" / "watchlist.json"
            watchlist.parent.mkdir()
            watchlist.write_text(json.dumps({"symbols": ["OLD", "MU"]}), encoding="utf-8")

            with patch("longbridge_watchlist_source.fetch_longbridge_snapshots", side_effect=RuntimeError("offline")):
                payload = refresh_watchlist(repo_root=root, watchlist_path="config/watchlist.json")

            self.assertEqual(payload["status"], "fallback")
            self.assertEqual(payload["source"], "manual")
            self.assertFalse(payload["updated_watchlist"])
            self.assertEqual(payload["symbols"], ["OLD", "MU"])
            self.assertEqual(json.loads(watchlist.read_text(encoding="utf-8"))["symbols"], ["OLD", "MU"])


if __name__ == "__main__":
    unittest.main()
