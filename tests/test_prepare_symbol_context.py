import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from prepare_symbol_context import build_context


class FakeClient:
    name = "longbridge_with_twelve_data_fallback"

    def time_series(self, symbol, interval, outputsize):
        return {
            "meta": {"symbol": symbol, "interval": interval, "provider": "longbridge"},
            "values": [
                {
                    "datetime": "2026-07-17 15:55:00" if interval != "1day" else "2026-07-17",
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "close": "100.5",
                    "volume": "1000",
                }
            ],
        }


class PrepareSymbolContextTest(unittest.TestCase):
    def test_defaults_to_longbridge_first_four_timeframes(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                repo_root=tmp,
                symbol="MU.US",
                date="2026-07-17",
                timezone="America/New_York",
                interval=None,
                outputsize=120,
                market_data_source="longbridge",
                fallback_market_data_source="twelve",
                longbridge_cli=None,
                longbridge_default_market="US",
                output=None,
            )
            with patch("prepare_symbol_context.build_market_data_client", return_value=FakeClient()) as build:
                payload = build_context(args)

            build.assert_called_once_with(
                repo_root=Path(tmp).resolve(),
                primary="longbridge",
                fallback="twelve",
                longbridge_cli=None,
                longbridge_default_market="US",
            )
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["requested_intervals"], ["1day", "1h", "15min", "5min"])
            self.assertEqual(list(payload["timeframes"]), ["1day", "1h", "15min", "5min"])
            self.assertTrue(Path(payload["output"]).exists())


if __name__ == "__main__":
    unittest.main()
