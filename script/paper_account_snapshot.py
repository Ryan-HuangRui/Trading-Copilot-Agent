#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from longbridge_account_snapshot import normalize_account, normalize_position, read_json, unwrap_items
from longbridge_paper_trade_adapter import ensure_paper_account, fetch_paper_snapshot, longbridge_cli_path


def current_date(timezone_name: str) -> str:
    return dt.datetime.now(ZoneInfo(timezone_name)).date().isoformat()


def output_path(repo_root: Path, date: str, explicit_output: str | None) -> Path:
    if explicit_output:
        path = Path(explicit_output)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "runtime" / "paper" / date / "paper-account-snapshot.json"


def first_value(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


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


def normalize_order(item: dict[str, Any]) -> dict[str, Any]:
    raw_symbol = first_value(item, ("symbol", "security", "code", "ticker"))
    symbol, market = normalize_symbol(raw_symbol)
    order_id = first_value(item, ("order_id", "id", "orderId"))
    side = first_value(item, ("side", "direction", "action"))
    quantity = first_value(item, ("quantity", "qty", "submitted_quantity", "shares"))
    price = first_value(item, ("price", "limit_price", "submitted_price"))
    status = first_value(item, ("status", "order_status", "state"))
    return {
        "order_id": str(order_id or ""),
        "symbol": symbol,
        "market": str(first_value(item, ("market", "exchange")) or market or "US").upper(),
        "side": str(side or "").lower(),
        "quantity": to_float(quantity),
        "price": to_float(price),
        "status": str(status or ""),
        "raw": item,
    }


def normalize_execution(item: dict[str, Any]) -> dict[str, Any]:
    raw_symbol = first_value(item, ("symbol", "security", "code", "ticker"))
    symbol, market = normalize_symbol(raw_symbol)
    order_id = first_value(item, ("order_id", "id", "orderId"))
    side = first_value(item, ("side", "direction", "action"))
    quantity = first_value(item, ("quantity", "qty", "filled_quantity", "shares"))
    price = first_value(item, ("price", "filled_price", "avg_price"))
    return {
        "order_id": str(order_id or ""),
        "symbol": symbol,
        "market": str(first_value(item, ("market", "exchange")) or market or "US").upper(),
        "side": str(side or "").lower(),
        "quantity": to_float(quantity),
        "price": to_float(price),
        "raw": item,
    }


def normalize_payload(raw: dict[str, Any], date: str, source: str) -> dict[str, Any]:
    auth = raw.get("auth")
    if not isinstance(auth, dict):
        auth = {"account": {"account_channel": raw.get("account_channel")}, "token": {"status": "valid"}}
    account_channel = ensure_paper_account(auth)
    positions = [
        normalize_position(item)
        for item in unwrap_items(raw.get("positions"))
        if isinstance(item, dict) and first_value(item, ("symbol", "security", "code", "ticker"))
    ]
    orders = [
        normalize_order(item)
        for item in unwrap_items(raw.get("orders"))
        if isinstance(item, dict) and first_value(item, ("symbol", "security", "code", "ticker"))
    ]
    executions = [
        normalize_execution(item)
        for item in unwrap_items(raw.get("executions"))
        if isinstance(item, dict) and first_value(item, ("symbol", "security", "code", "ticker"))
    ]
    return {
        "date": date,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account_channel": account_channel,
        "account": normalize_account(raw.get("account")),
        "positions": positions,
        "orders": orders,
        "executions": executions,
        "safety_note": "Read-only paper account snapshot. This workflow does not submit orders.",
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
        raw = fetch_paper_snapshot(cli)
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
        "orders_count": len(payload["orders"]),
        "executions_count": len(payload["executions"]),
        "account_channel": payload["account_channel"],
        "source": source,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write a read-only Longbridge paper account snapshot")
    parser.add_argument("--date")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--input", help="Read paper account payload from JSON fixture instead of Longbridge CLI")
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
