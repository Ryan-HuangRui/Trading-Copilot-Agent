#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from journal_review import planned_target_date, read_jsonl
from market_calendar import MARKET_TIMEZONE, resolve_market_date, trading_day_status
from market_snapshot import build_market_snapshot


def normalized_symbol(value: object) -> str:
    return str(value or "").split(".", 1)[0].upper()


def extra_symbols_from_journal(repo_root: Path, snapshot_date: str) -> list[str]:
    path = repo_root / "runtime" / "journal" / "signals.jsonl"
    symbols = []
    for record in read_jsonl(path):
        if record.get("kind") != "signal" or planned_target_date(record) != snapshot_date:
            continue
        symbol = normalized_symbol(record.get("symbol"))
        if symbol:
            symbols.append(symbol)
    return symbols


def extra_symbols_from_account(repo_root: Path, snapshot_date: str) -> list[str]:
    path = repo_root / "runtime" / "account" / snapshot_date / "account-snapshot.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    symbols = []
    for position in payload.get("positions", []):
        if not isinstance(position, dict):
            continue
        symbol = normalized_symbol(position.get("symbol"))
        if symbol:
            symbols.append(symbol)
    return symbols


def ordered_unique(symbols: list[str]) -> list[str]:
    seen = set()
    result = []
    for symbol in symbols:
        normalized = normalized_symbol(symbol)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


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
    parser.add_argument("--extra-symbol", action="append", default=[], help="Extra symbol to force into the snapshot.")
    parser.add_argument("--include-journal-signals", action="store_true", help="Force symbols from journal signals targeting this snapshot date into the snapshot.")
    parser.add_argument("--include-position-symbols", action="store_true", help="Force symbols from runtime account snapshot positions into the snapshot.")
    parser.add_argument("--market-data-source", default="longbridge", choices=["longbridge", "twelve"], help="Primary market data provider.")
    parser.add_argument("--fallback-market-data-source", default="twelve", choices=["twelve", "longbridge", "none"], help="Fallback market data provider.")
    parser.add_argument("--longbridge-cli", help="Explicit Longbridge CLI path.")
    parser.add_argument("--longbridge-default-market", default="US", help="Market suffix for bare symbols when using Longbridge.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    snapshot_date = resolve_market_date(args.date, timezone=args.timezone)
    guard = trading_day_status(snapshot_date)
    if args.skip_non_trading_day and not guard["is_trading_day"]:
        print(json.dumps({"skipped": True, "guard": guard}, ensure_ascii=False, indent=2))
        return

    extra_symbols = list(args.extra_symbol or [])
    if args.include_journal_signals:
        extra_symbols.extend(extra_symbols_from_journal(repo_root, snapshot_date.isoformat()))
    if args.include_position_symbols:
        extra_symbols.extend(extra_symbols_from_account(repo_root, snapshot_date.isoformat()))
    extra_symbols = ordered_unique(extra_symbols)

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
        extra_symbols=extra_symbols,
        market_data_source=args.market_data_source,
        fallback_market_data_source=args.fallback_market_data_source,
        longbridge_cli=args.longbridge_cli,
        longbridge_default_market=args.longbridge_default_market,
    )

    print(json.dumps(
        {
            "snapshot_path": str(path),
            "snapshot_date": snapshot["snapshot_date"],
            "symbols": len(snapshot["symbols"]),
            "watchlist_symbols": len(snapshot.get("watchlist_symbols", [])),
            "dynamic_universe_symbols": len(snapshot.get("dynamic_universe_symbols", [])),
            "extra_symbols": snapshot.get("extra_symbols", []),
            "market_data_source": snapshot.get("market_data_source"),
            "primary_market_data_source": snapshot.get("primary_market_data_source"),
            "fallback_market_data_source": snapshot.get("fallback_market_data_source"),
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
