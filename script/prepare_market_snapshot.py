#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from market_calendar import MARKET_TIMEZONE, resolve_market_date, trading_day_status
from market_snapshot import build_market_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch latest completed daily bars into a per-day market snapshot")
    parser.add_argument("--watchlist", default="config/watchlist.json")
    parser.add_argument("--interval", default="1day")
    parser.add_argument("--outputsize", type=int, default=200)
    parser.add_argument("--date", help="Snapshot trading date in YYYY-MM-DD. Defaults to today in America/New_York.")
    parser.add_argument("--timezone", default=MARKET_TIMEZONE)
    parser.add_argument("--skip-non-trading-day", action="store_true")
    parser.add_argument("--sp500-screen", action="store_true", help="Fetch S&P 500 top holdings, score them, and merge dynamic candidates into the snapshot.")
    parser.add_argument("--sp500-top", type=int, default=100, help="Number of top S&P 500 holdings to evaluate when --sp500-screen is enabled.")
    parser.add_argument("--sp500-candidates", type=int, default=15, help="Number of dynamic candidates to keep when --sp500-screen is enabled.")
    parser.add_argument("--sp500-source", choices=["ishares_ivv", "slickcharts"], default="ishares_ivv", help="S&P 500 universe source. iShares IVV CSV is the stable default.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    snapshot_date = resolve_market_date(args.date, timezone=args.timezone)
    guard = trading_day_status(snapshot_date)
    if args.skip_non_trading_day and not guard["is_trading_day"]:
        print(json.dumps({"skipped": True, "guard": guard}, ensure_ascii=False, indent=2))
        return

    snapshot, path = build_market_snapshot(
        repo_root=repo_root,
        snapshot_date=snapshot_date.isoformat(),
        trading_day=guard,
        watchlist_path=args.watchlist,
        interval=args.interval,
        outputsize=args.outputsize,
        sp500_screen=args.sp500_screen,
        sp500_top=args.sp500_top,
        sp500_candidates=args.sp500_candidates,
        sp500_source=args.sp500_source,
    )

    print(json.dumps(
        {
            "snapshot_path": str(path),
            "snapshot_date": snapshot["snapshot_date"],
            "symbols": len(snapshot["symbols"]),
            "watchlist_symbols": len(snapshot.get("watchlist_symbols", [])),
            "dynamic_universe_symbols": len(snapshot.get("dynamic_universe_symbols", [])),
            "candidate_universe_path": snapshot.get("candidate_universe_path"),
            "errors": len(snapshot["errors"]),
            "latest_bar_dates": snapshot.get("latest_bar_dates", []),
            "stale_data": snapshot.get("stale_data", False),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
