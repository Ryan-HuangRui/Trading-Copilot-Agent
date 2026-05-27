import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from market_snapshot import build_symbol_snapshot, latest_bar_dates, merge_symbols, snapshot_filename


class MarketSnapshotContractTest(unittest.TestCase):
    def test_snapshot_filename_contract(self):
        self.assertEqual(snapshot_filename("1day"), "daily-snapshot.json")
        self.assertEqual(snapshot_filename("5min"), "5min-snapshot.json")

    def test_merge_symbols_uppercases_and_deduplicates(self):
        self.assertEqual(merge_symbols(["mu", "NVDA"], ["MU", "pltr"]), ["MU", "NVDA", "PLTR"])

    def test_build_symbol_snapshot_computes_core_metrics(self):
        data = {
            "meta": {"symbol": "MU"},
            "values": [
                {"datetime": "2026-05-06", "open": "100", "high": "110", "low": "99", "close": "108", "volume": "1200"},
                {"datetime": "2026-05-05", "open": "96", "high": "102", "low": "95", "close": "100", "volume": "1000"},
            ],
        }

        snapshot = build_symbol_snapshot("MU", data, Path("raw_data/2026-05-06/1day/MU.json"))

        self.assertEqual(snapshot["symbol"], "MU")
        self.assertEqual(snapshot["latest"]["datetime"], "2026-05-06")
        self.assertEqual(snapshot["metrics"]["close_delta_pct"], 8.0)
        self.assertEqual(snapshot["metrics"]["intraday_change_pct"], 8.0)
        self.assertEqual(snapshot["raw_path"], "raw_data/2026-05-06/1day/MU.json")

    def test_latest_bar_dates_returns_unique_sorted_dates(self):
        symbols = [
            {"latest": {"datetime": "2026-05-06"}},
            {"latest": {"datetime": "2026-05-05"}},
            {"latest": {"datetime": "2026-05-06"}},
            {"latest": {}},
        ]

        self.assertEqual(latest_bar_dates(symbols), ["2026-05-05", "2026-05-06"])


if __name__ == "__main__":
    unittest.main()
