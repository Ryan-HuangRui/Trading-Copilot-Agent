#!/usr/bin/env python3
import argparse
import json
import sys

from market_calendar import MARKET_TIMEZONE, resolve_market_date, trading_day_status


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether a date is a regular US stock-market trading day")
    parser.add_argument("--date", help="Market date in YYYY-MM-DD. Defaults to today in America/New_York.")
    parser.add_argument("--timezone", default=MARKET_TIMEZONE)
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--exit-non-trading", action="store_true", help="Return exit code 2 on non-trading days")
    args = parser.parse_args()

    day = resolve_market_date(args.date, timezone=args.timezone)
    result = trading_day_status(day)
    result["timezone"] = args.timezone

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "TRADING_DAY" if result["is_trading_day"] else "NON_TRADING_DAY"
        detail = result.get("holiday") or result["reason"]
        print(f"{status} {result['date']} {detail}")

    if args.exit_non_trading and not result["is_trading_day"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
