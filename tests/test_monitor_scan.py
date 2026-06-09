import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from monitor_scan import analyze_long_signal


class MonitorScanTest(unittest.TestCase):
    def test_executable_scan_includes_setup_backed_fields(self):
        bars = []
        for idx in range(60):
            close = 50 + idx * 0.5
            bars.append(
                {
                    "dt": f"2026-05-26 10:{idx:02d}:00",
                    "open": close - 0.1,
                    "high": close + 0.2,
                    "low": close - 0.4,
                    "close": close,
                    "volume": 1000,
                }
            )
        bars[-1]["close"] = max(bar["high"] for bar in bars[-21:-1]) + 1
        bars[-1]["high"] = bars[-1]["close"] + 0.2
        bars[-1]["volume"] = 5000

        scan = analyze_long_signal("MU", bars)

        self.assertEqual(scan["status"], "可执行")
        self.assertEqual(scan["setup"], "strong_breakout_trend_following.md")
        self.assertEqual(scan["setup_files"], ["strong_breakout_trend_following.md", "tight_range_breakout_filter.md"])
        self.assertTrue(scan["journal_appendable"])
        self.assertEqual(scan["risk_quality"], "acceptable")
        self.assertIn("price", scan["trigger_detail"])
        self.assertIn("price", scan["invalidation_detail"])
        self.assertEqual(scan["latest_bar"]["close"], bars[-1]["close"])
        self.assertEqual(len(scan["recent_bars"]), 20)
        self.assertEqual(scan["recent_bars"][-1]["dt"], bars[-1]["dt"])
        self.assertEqual(scan["price_data_interval"], "scan_interval")


if __name__ == "__main__":
    unittest.main()
