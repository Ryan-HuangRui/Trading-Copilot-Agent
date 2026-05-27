#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from longbridge_cli_adapter import fetch_account_snapshot, longbridge_cli_path


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected object")
    return data


def output_path(repo_root: Path, date: str, explicit_output: str | None) -> Path:
    if explicit_output:
        path = Path(explicit_output)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "runtime" / "account" / date / "account-snapshot.json"


def current_date(timezone_name: str) -> str:
    return dt.datetime.now(ZoneInfo(timezone_name)).date().isoformat()


def first_value(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def unwrap_items(payload: Any) -> list[Any]:
    current = payload
    if isinstance(current, dict):
        for key in ("data", "result", "items", "positions", "list", "securities"):
            value = current.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                current = value
        return [current]
    if isinstance(current, list):
        return current
    return []


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_symbol(raw: Any) -> tuple[str, str | None]:
    value = str(raw or "").upper()
    if "." in value:
        symbol, market = value.split(".", 1)
        return symbol, market
    return value, None


def normalize_account(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        payload = raw[0]
    elif isinstance(raw, dict):
        payload = raw
    else:
        payload = {}
    account = first_value(payload, ("account", "cash_info", "asset", "assets"))
    if isinstance(account, dict):
        payload = account
    net_liquidation = first_value(payload, ("net_liquidation", "net_asset", "net_assets", "total_asset", "total_assets", "nav"))
    cash = first_value(payload, ("cash", "cash_balance", "available_cash", "buying_power", "buy_power", "total_cash"))
    currency = first_value(payload, ("currency", "cash_currency"))
    return {
        "net_liquidation": to_float(net_liquidation),
        "cash": to_float(cash),
        "currency": str(currency or "USD"),
    }


def normalize_position(item: dict[str, Any]) -> dict[str, Any]:
    raw_symbol = first_value(item, ("symbol", "security", "code", "ticker"))
    symbol, market = normalize_symbol(raw_symbol)
    market = first_value(item, ("market", "exchange")) or market
    quantity = first_value(item, ("quantity", "qty", "position", "shares"))
    avg_cost = first_value(item, ("avg_cost", "average_cost", "cost_price"))
    last_price = first_value(item, ("last_price", "price", "market_price", "current_price"))
    market_value = first_value(item, ("market_value", "value", "position_value"))
    unrealized_pnl = first_value(item, ("unrealized_pnl", "pnl", "profit"))
    unrealized_pnl_pct = first_value(item, ("unrealized_pnl_pct", "pnl_pct", "profit_pct"))
    currency = first_value(item, ("currency", "settlement_currency"))
    return {
        "symbol": symbol,
        "market": str(market or "US").upper(),
        "quantity": to_float(quantity),
        "avg_cost": to_float(avg_cost),
        "last_price": to_float(last_price),
        "market_value": to_float(market_value),
        "unrealized_pnl": to_float(unrealized_pnl),
        "unrealized_pnl_pct": to_float(unrealized_pnl_pct),
        "currency": str(currency or "USD"),
    }


def normalize_payload(raw: dict[str, Any], date: str, source: str) -> dict[str, Any]:
    raw_positions = raw.get("positions")
    positions = [
        normalize_position(item)
        for item in unwrap_items(raw_positions)
        if isinstance(item, dict) and first_value(item, ("symbol", "security", "code", "ticker"))
    ]
    return {
        "date": date,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account": normalize_account(raw.get("account")),
        "positions": positions,
        "safety_note": "Read-only account snapshot. This workflow does not place, cancel, or modify orders.",
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    date = args.date or current_date(args.timezone)
    if args.input:
        input_path = Path(args.input)
        raw = read_json(input_path if input_path.is_absolute() else repo_root / input_path)
        source = str(input_path)
    else:
        cli = longbridge_cli_path(args.longbridge_cli)
        raw = fetch_account_snapshot(cli)
        source = "longbridge-cli"
    payload = normalize_payload(raw, date, source)
    output = output_path(repo_root, date, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "success",
        "date": date,
        "output": str(output),
        "positions_count": len(payload["positions"]),
        "source": source,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write a read-only Longbridge account snapshot")
    parser.add_argument("--date")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--input", help="Read account payload from JSON fixture instead of Longbridge CLI")
    parser.add_argument("--output")
    parser.add_argument("--longbridge-cli")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        payload = run(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
