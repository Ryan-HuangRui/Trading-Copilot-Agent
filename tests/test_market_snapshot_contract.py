import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from market_data_provider import (
    FallbackMarketDataClient,
    longbridge_symbol,
    normalize_longbridge_kline,
)
from market_snapshot import build_symbol_snapshot, latest_bar_dates, merge_symbols, snapshot_filename


class MarketSnapshotContractTest(unittest.TestCase):
    def test_snapshot_filename_contract(self):
        self.assertEqual(snapshot_filename("1day"), "daily-snapshot.json")
        self.assertEqual(snapshot_filename("5min"), "5min-snapshot.json")

    def test_merge_symbols_uppercases_and_deduplicates(self):
        self.assertEqual(merge_symbols(["mu", "NVDA"], ["MU", "pltr"]), ["MU", "NVDA", "PLTR"])

    def test_longbridge_symbol_adds_market_suffix_without_mangling_share_class(self):
        self.assertEqual(longbridge_symbol("MU"), "MU.US")
        self.assertEqual(longbridge_symbol("BRK.B"), "BRK.B.US")
        self.assertEqual(longbridge_symbol("700.HK"), "700.HK")

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

    def test_normalize_longbridge_kline_matches_snapshot_contract(self):
        payload = normalize_longbridge_kline(
            "MU.US",
            "1day",
            [
                {"time": "2026-05-05 04:00:00", "open": "96", "high": "102", "low": "95", "close": "100", "volume": "1000"},
                {"time": "2026-05-06 04:00:00", "open": "100", "high": "110", "low": "99", "close": "108", "volume": "1200"},
            ],
        )

        self.assertEqual(payload["meta"]["symbol"], "MU")
        self.assertEqual(payload["meta"]["provider"], "longbridge")
        self.assertEqual(payload["values"][0]["datetime"], "2026-05-06")
        self.assertEqual(payload["values"][1]["datetime"], "2026-05-05")

    def test_longbridge_oldest_to_newest_fixture_feeds_latest_snapshot_bar(self):
        payload = normalize_longbridge_kline(
            "MU.US",
            "1day",
            [
                {"time": "2026-05-20 04:00:00", "open": "734.955", "high": "735.680", "low": "700.660", "close": "731.990", "volume": "48827357"},
                {"time": "2026-05-21 04:00:00", "open": "736.360", "high": "764.900", "low": "732.200", "close": "762.100", "volume": "42461460"},
                {"time": "2026-05-22 04:00:00", "open": "756.815", "high": "780.200", "low": "747.200", "close": "751.000", "volume": "36002915"},
                {"time": "2026-05-26 04:00:00", "open": "820.500", "high": "916.800", "low": "820.295", "close": "895.880", "volume": "76560763"},
                {"time": "2026-05-27 04:00:00", "open": "955.660", "high": "956.160", "low": "888.150", "close": "928.410", "volume": "72295713"},
            ],
        )
        snapshot = build_symbol_snapshot("MU", payload, Path("raw_data/2026-05-27/1day/MU.json"))

        self.assertEqual(snapshot["latest"]["datetime"], "2026-05-27")
        self.assertEqual(snapshot["latest"]["close"], "928.410")
        self.assertEqual(snapshot["previous"]["datetime"], "2026-05-26")

    def test_fallback_client_uses_twelve_data_when_longbridge_fails(self):
        class FailingPrimary:
            name = "longbridge"

            def time_series(self, **kwargs):
                raise RuntimeError("no permission")

        class Fallback:
            name = "twelve_data"

            def time_series(self, **kwargs):
                return {
                    "meta": {"symbol": kwargs["symbol"]},
                    "values": [
                        {"datetime": "2026-05-06", "open": "100", "high": "110", "low": "99", "close": "108", "volume": "1200"}
                    ],
                }

        payload = FallbackMarketDataClient(FailingPrimary(), Fallback()).time_series(
            symbol="MU",
            interval="1day",
            outputsize=200,
        )

        self.assertEqual(payload["meta"]["provider"], "twelve_data")
        self.assertEqual(payload["meta"]["fallback_from"], "longbridge")
        self.assertIn("no permission", payload["meta"]["primary_error"])


if __name__ == "__main__":
    unittest.main()
