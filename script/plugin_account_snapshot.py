#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if payload.get(key) not in (None, ""):
            return payload[key]
    return None


def normalize_symbol(value: Any, market: Any = None) -> tuple[str, str]:
    raw = str(value or "").strip().upper()
    if "." in raw:
        symbol, suffix = raw.split(".", 1)
        return symbol, suffix
    return raw, str(market or "US").upper()


def clean_payload(raw: Any) -> Any:
    if isinstance(raw, dict) and isinstance(raw.get("structuredContent"), (dict, list)):
        return raw["structuredContent"]
    return raw


def ibkr_positions(raw: Any) -> list[dict[str, Any]]:
    payload = clean_payload(raw)
    rows = payload.get("positions", []) if isinstance(payload, dict) else []
    positions = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol, market = normalize_symbol(row.get("contract_description"))
        if not symbol:
            continue
        positions.append(
            {
                "broker": "ibkr",
                "symbol": symbol,
                "market": market,
                "quantity": to_float(row.get("position")),
                "avg_cost": to_float(row.get("average_price")),
                "last_price": to_float(row.get("market_price")),
                "market_value": to_float(row.get("market_value")),
                "unrealized_pnl": to_float(row.get("unrealized_pnl")),
                "daily_pnl": to_float(row.get("daily_pnl")),
                "currency": str(row.get("currency") or "USD"),
                "asset_class": row.get("asset_class"),
            }
        )
    return positions


def longbridge_positions(raw: Any) -> list[dict[str, Any]]:
    payload = clean_payload(raw)
    channels = payload.get("list", []) if isinstance(payload, dict) else []
    positions = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        for row in channel.get("stock_info", []):
            if not isinstance(row, dict):
                continue
            symbol, market = normalize_symbol(row.get("symbol"), row.get("market"))
            if not symbol:
                continue
            positions.append(
                {
                    "broker": "longbridge",
                    "account_channel": channel.get("account_channel"),
                    "symbol": symbol,
                    "symbol_name": row.get("symbol_name"),
                    "market": market,
                    "quantity": to_float(row.get("quantity")),
                    "available_quantity": to_float(row.get("available_quantity")),
                    "avg_cost": to_float(row.get("cost_price")),
                    "currency": str(row.get("currency") or "USD"),
                    "asset_class": "STK",
                }
            )
    return positions


def ibkr_account(summary_raw: Any, balances_raw: Any) -> dict[str, Any] | None:
    summary = clean_payload(summary_raw) if summary_raw is not None else {}
    balances = clean_payload(balances_raw) if balances_raw is not None else {}
    if not isinstance(summary, dict):
        summary = {}
    balance_rows = balances.get("balances", []) if isinstance(balances, dict) else []
    base = next(
        (row for row in balance_rows if isinstance(row, dict) and row.get("currency") == "BASE"),
        {},
    )
    if not summary and not base:
        return None
    return {
        "broker": "ibkr",
        "currency": str(summary.get("currency") or "BASE"),
        "net_liquidation": to_float(first_value(summary, "net_liquidation") or base.get("net_liquidation_value")),
        "cash": to_float(first_value(summary, "total_cash_value") or base.get("cash_balance")),
        "gross_position_value": to_float(
            first_value(summary, "gross_position_value") or base.get("stock_market_value")
        ),
        "available_funds": to_float(summary.get("available_funds")),
        "buying_power": to_float(summary.get("buying_power")),
        "initial_margin": to_float(summary.get("initial_margin")),
        "maintenance_margin": to_float(summary.get("maintenance_margin")),
    }


def longbridge_account(raw: Any) -> dict[str, Any] | None:
    payload = clean_payload(raw)
    if isinstance(payload, dict):
        rows = payload.get("balances") or payload.get("list") or [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    row = next((item for item in rows if isinstance(item, dict)), None)
    if row is None:
        return None
    return {
        "broker": "longbridge",
        "currency": str(row.get("currency") or "USD"),
        "net_liquidation": to_float(first_value(row, "net_assets", "net_liquidation", "total_assets")),
        "cash": to_float(first_value(row, "total_cash", "cash")),
        "buying_power": to_float(first_value(row, "buy_power", "buying_power")),
        "initial_margin": to_float(row.get("init_margin")),
        "maintenance_margin": to_float(row.get("maintenance_margin")),
        "margin_call": to_float(row.get("margin_call")),
        "risk_level": row.get("risk_level"),
    }


def weighted_average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = []
    for row in rows:
        quantity = to_float(row.get("quantity"))
        value = to_float(row.get(key))
        if quantity is None or value is None:
            return None
        values.append((abs(quantity), value))
    denominator = sum(quantity for quantity, _ in values)
    if denominator == 0:
        return None
    return sum(quantity * value for quantity, value in values) / denominator


def sum_if_complete(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [to_float(row.get(key)) for row in rows]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def aggregate_positions(broker_positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in broker_positions:
        grouped.setdefault((str(row["symbol"]), str(row["currency"])), []).append(row)
    aggregated = []
    for (symbol, currency), rows in sorted(grouped.items()):
        quantity = sum(to_float(row.get("quantity")) or 0.0 for row in rows)
        brokers = sorted({str(row["broker"]) for row in rows})
        payload = {
            "symbol": symbol,
            "market": str(rows[0].get("market") or "US"),
            "quantity": quantity,
            "avg_cost": weighted_average(rows, "avg_cost"),
            "last_price": next(
                (to_float(row.get("last_price")) for row in rows if to_float(row.get("last_price")) is not None),
                None,
            ),
            "market_value": sum_if_complete(rows, "market_value"),
            "unrealized_pnl": sum_if_complete(rows, "unrealized_pnl"),
            "daily_pnl": sum_if_complete(rows, "daily_pnl"),
            "currency": currency,
            "brokers": brokers,
            "cross_broker_overlap": len(brokers) > 1,
            "broker_positions": rows,
        }
        aggregated.append(payload)
    return aggregated


def aggregate_account(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [row for row in accounts if row.get("net_liquidation") is not None]
    currencies = {str(row.get("currency")) for row in usable}
    if usable and len(usable) == len(accounts) and len(currencies) == 1:
        currency = currencies.pop()
        cash_values = [to_float(row.get("cash")) for row in accounts]
        return {
            "currency": currency,
            "net_liquidation": sum(float(row["net_liquidation"]) for row in accounts),
            "cash": (
                sum(value for value in cash_values if value is not None)
                if all(value is not None for value in cash_values)
                else None
            ),
            "aggregation_status": "complete_same_currency",
        }
    return {
        "currency": None,
        "net_liquidation": None,
        "cash": None,
        "aggregation_status": "unavailable_mixed_currency_or_incomplete",
    }


def resolve_input(repo_root: Path, value: str | None) -> Any:
    if not value:
        return None
    path = Path(value)
    return read_json(path if path.is_absolute() else repo_root / path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    ibkr_position_payload = resolve_input(repo_root, args.ibkr_positions)
    longbridge_position_payload = resolve_input(repo_root, args.longbridge_positions)
    broker_positions = []
    if ibkr_position_payload is not None:
        broker_positions.extend(ibkr_positions(ibkr_position_payload))
    if longbridge_position_payload is not None:
        broker_positions.extend(longbridge_positions(longbridge_position_payload))

    accounts = []
    ibkr = ibkr_account(
        resolve_input(repo_root, args.ibkr_account),
        resolve_input(repo_root, args.ibkr_balances),
    )
    if ibkr:
        accounts.append(ibkr)
    longbridge = longbridge_account(resolve_input(repo_root, args.longbridge_account))
    if longbridge:
        accounts.append(longbridge)
    if not broker_positions and not accounts:
        raise ValueError("at least one readable IBKR or Longbridge plugin input is required")

    positions = aggregate_positions(broker_positions)
    output = (
        Path(args.output)
        if args.output
        else repo_root / "runtime" / "account" / args.date / "plugin-account-snapshot.json"
    )
    if not output.is_absolute():
        output = repo_root / output
    payload = {
        "schema_version": 1,
        "date": args.date,
        "source": "codex-app-plugins",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account": aggregate_account(accounts),
        "accounts": accounts,
        "positions": positions,
        "broker_positions": broker_positions,
        "summary": {
            "brokers": sorted({str(row.get("broker")) for row in accounts + broker_positions}),
            "broker_position_rows": len(broker_positions),
            "aggregated_positions": len(positions),
            "cross_broker_overlaps": [
                row["symbol"] for row in positions if row.get("cross_broker_overlap")
            ],
        },
        "safety_note": (
            "Read-only plugin account snapshot. It is research evidence only and must not be used "
            "to place, cancel, replace, or modify orders."
        ),
    }
    atomic_write_json(output, payload)
    return {
        "status": "success",
        "date": args.date,
        "output": str(output),
        "positions_count": len(positions),
        "brokers": payload["summary"]["brokers"],
        "cross_broker_overlaps": payload["summary"]["cross_broker_overlaps"],
        "source": payload["source"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize read-only IBKR and Longbridge plugin snapshots")
    parser.add_argument("--date", required=True)
    parser.add_argument("--ibkr-positions")
    parser.add_argument("--ibkr-account")
    parser.add_argument("--ibkr-balances")
    parser.add_argument("--longbridge-positions")
    parser.add_argument("--longbridge-account")
    parser.add_argument("--output")
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
