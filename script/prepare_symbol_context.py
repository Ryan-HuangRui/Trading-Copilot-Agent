#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from market_calendar import MARKET_TIMEZONE, resolve_market_date, trading_day_status
from market_data_provider import build_market_data_client, display_symbol
from market_snapshot import DEFAULT_SUPPLEMENTAL_INTERVALS, load_env, raw_data_dir, report_day_dir
from twelve_data_client import save_json


DEFAULT_INTERVALS = ("1day", *DEFAULT_SUPPLEMENTAL_INTERVALS)
DEFAULT_OUTPUTSIZE = {"1day": 200, "1h": 120, "15min": 120, "5min": 120}


def ordered_intervals(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or DEFAULT_INTERVALS:
        interval = str(value or "").strip().lower()
        if interval and interval not in seen:
            seen.add(interval)
            result.append(interval)
    return result


def build_context(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    load_env(repo_root)
    symbol = display_symbol(args.symbol)
    report_date = resolve_market_date(args.date, timezone=args.timezone).isoformat()
    intervals = ordered_intervals(args.interval)
    client = build_market_data_client(
        repo_root=repo_root,
        primary=args.market_data_source,
        fallback=args.fallback_market_data_source,
        longbridge_cli=args.longbridge_cli,
        longbridge_default_market=args.longbridge_default_market,
    )

    timeframes: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for interval in intervals:
        raw_dir = raw_data_dir(repo_root, report_date, interval)
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{symbol}.json"
        try:
            data = client.time_series(
                symbol=symbol,
                interval=interval,
                outputsize=DEFAULT_OUTPUTSIZE.get(interval, args.outputsize),
            )
            save_json(raw_path, data)
            values = list(data.get("values") or [])
            timeframes[interval] = {
                "meta": data.get("meta") if isinstance(data.get("meta"), dict) else {},
                "latest": values[0] if values else None,
                "bars": values,
                "raw_path": str(raw_path),
            }
        except Exception as exc:
            errors.append({"interval": interval, "error": str(exc)})

    output = (
        Path(args.output)
        if args.output
        else report_day_dir(repo_root, report_date) / f"symbol-{symbol}-context.json"
    )
    if not output.is_absolute():
        output = repo_root / output
    payload = {
        "schema_version": "symbol-price-context/v1",
        "status": "success" if not errors else ("partial" if timeframes else "failed"),
        "date": report_date,
        "symbol": symbol,
        "requested_intervals": intervals,
        "market_data_source": getattr(client, "name", args.market_data_source),
        "primary_market_data_source": args.market_data_source,
        "fallback_market_data_source": args.fallback_market_data_source,
        "trading_day": trading_day_status(resolve_market_date(args.date, timezone=args.timezone)),
        "timeframes": timeframes,
        "errors": errors,
        "safety": {
            "real_account_read_only": True,
            "not_an_order_instruction": True,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**payload, "output": str(output)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Longbridge-first multi-timeframe context for one symbol")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--date")
    parser.add_argument("--timezone", default=MARKET_TIMEZONE)
    parser.add_argument("--interval", action="append", help="Repeat to override default 1day, 1h, 15min, 5min intervals.")
    parser.add_argument("--outputsize", type=int, default=120)
    parser.add_argument("--market-data-source", choices=["longbridge", "twelve"], default="longbridge")
    parser.add_argument("--fallback-market-data-source", choices=["twelve", "longbridge", "none"], default="twelve")
    parser.add_argument("--longbridge-cli")
    parser.add_argument("--longbridge-default-market", default="US")
    parser.add_argument("--output")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    payload = build_context(build_parser().parse_args())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] != "failed" else 1)


if __name__ == "__main__":
    main()
