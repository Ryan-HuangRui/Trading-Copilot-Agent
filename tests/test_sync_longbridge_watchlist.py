import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from sync_longbridge_watchlist import (
    extract_focus_symbols,
    group_symbol_payloads,
    normalize_symbol,
    ordered_unique,
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


if __name__ == "__main__":
    unittest.main()
