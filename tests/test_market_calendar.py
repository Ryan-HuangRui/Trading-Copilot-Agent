import datetime as dt
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from market_calendar import next_trading_day, previous_trading_day, trading_day_status


class MarketCalendarTest(unittest.TestCase):
    def test_regular_trading_day(self):
        status = trading_day_status(dt.date(2026, 5, 6))

        self.assertTrue(status["is_trading_day"])
        self.assertEqual(status["reason"], "regular_session")

    def test_weekend_is_not_trading_day(self):
        status = trading_day_status(dt.date(2026, 5, 9))

        self.assertFalse(status["is_trading_day"])
        self.assertEqual(status["reason"], "weekend")

    def test_market_holiday_is_not_trading_day(self):
        status = trading_day_status(dt.date(2026, 5, 25))

        self.assertFalse(status["is_trading_day"])
        self.assertEqual(status["reason"], "market_holiday")
        self.assertEqual(status["holiday"], "Memorial Day")

    def test_previous_and_next_trading_day_skip_weekends_and_holidays(self):
        self.assertEqual(previous_trading_day(dt.date(2026, 5, 26)), dt.date(2026, 5, 22))
        self.assertEqual(next_trading_day(dt.date(2026, 5, 22)), dt.date(2026, 5, 26))


if __name__ == "__main__":
    unittest.main()
