from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


MARKET_TIMEZONE = "America/New_York"


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> dt.date:
    day = dt.date(year, month, 1)
    offset = (weekday - day.weekday()) % 7
    return day + dt.timedelta(days=offset + 7 * (nth - 1))


def last_weekday(year: int, month: int, weekday: int) -> dt.date:
    if month == 12:
        day = dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    else:
        day = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return day - dt.timedelta(days=(day.weekday() - weekday) % 7)


def observed_fixed_holiday(year: int, month: int, day: int) -> dt.date:
    holiday = dt.date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - dt.timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + dt.timedelta(days=1)
    return holiday


def easter_date(year: int) -> dt.date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def market_holidays(year: int) -> dict[dt.date, str]:
    holidays = {
        observed_fixed_holiday(year, 1, 1): "New Year's Day",
        nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        nth_weekday(year, 2, 0, 3): "Washington's Birthday",
        easter_date(year) - dt.timedelta(days=2): "Good Friday",
        last_weekday(year, 5, 0): "Memorial Day",
        observed_fixed_holiday(year, 6, 19): "Juneteenth National Independence Day",
        observed_fixed_holiday(year, 7, 4): "Independence Day",
        nth_weekday(year, 9, 0, 1): "Labor Day",
        nth_weekday(year, 11, 3, 4): "Thanksgiving Day",
        observed_fixed_holiday(year, 12, 25): "Christmas Day",
    }

    # Observed holidays can fall into the adjacent calendar year.
    for adjacent_year in (year - 1, year + 1):
        for holiday, name in list(market_holidays_without_adjacent(adjacent_year).items()):
            if holiday.year == year:
                holidays[holiday] = name

    return holidays


def market_holidays_without_adjacent(year: int) -> dict[dt.date, str]:
    return {
        observed_fixed_holiday(year, 1, 1): "New Year's Day",
        nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        nth_weekday(year, 2, 0, 3): "Washington's Birthday",
        easter_date(year) - dt.timedelta(days=2): "Good Friday",
        last_weekday(year, 5, 0): "Memorial Day",
        observed_fixed_holiday(year, 6, 19): "Juneteenth National Independence Day",
        observed_fixed_holiday(year, 7, 4): "Independence Day",
        nth_weekday(year, 9, 0, 1): "Labor Day",
        nth_weekday(year, 11, 3, 4): "Thanksgiving Day",
        observed_fixed_holiday(year, 12, 25): "Christmas Day",
    }


def resolve_market_date(date_text: str | None = None, timezone: str = MARKET_TIMEZONE) -> dt.date:
    if date_text:
        return dt.date.fromisoformat(date_text)
    return dt.datetime.now(ZoneInfo(timezone)).date()


def trading_day_status(day: dt.date) -> dict:
    if day.weekday() >= 5:
        return {
            "date": day.isoformat(),
            "is_trading_day": False,
            "reason": "weekend",
        }

    holidays = market_holidays(day.year)
    if day in holidays:
        return {
            "date": day.isoformat(),
            "is_trading_day": False,
            "reason": "market_holiday",
            "holiday": holidays[day],
        }

    return {
        "date": day.isoformat(),
        "is_trading_day": True,
        "reason": "regular_session",
    }


def previous_trading_day(day: dt.date) -> dt.date:
    cursor = day - dt.timedelta(days=1)
    while not trading_day_status(cursor)["is_trading_day"]:
        cursor -= dt.timedelta(days=1)
    return cursor


def next_trading_day(day: dt.date) -> dt.date:
    cursor = day + dt.timedelta(days=1)
    while not trading_day_status(cursor)["is_trading_day"]:
        cursor += dt.timedelta(days=1)
    return cursor
