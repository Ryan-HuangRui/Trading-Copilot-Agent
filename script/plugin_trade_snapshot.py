#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from longbridge_cli_adapter import fetch_trade_snapshot, longbridge_cli_path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_input(repo_root: Path, value: str | None) -> Any:
    if not value:
        return None
    path = Path(value)
    return read_json(path if path.is_absolute() else repo_root / path)


def clean_payload(raw: Any) -> Any:
    if isinstance(raw, dict) and isinstance(raw.get("structuredContent"), (dict, list)):
        return raw["structuredContent"]
    return raw


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_symbol(value: Any) -> tuple[str, str | None]:
    raw = str(value or "").strip().upper()
    if "." in raw:
        symbol, market = raw.split(".", 1)
        return symbol, market
    return raw, None


def market_date(value: Any, timezone_name: str) -> str | None:
    if not value:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def ibkr_trades(raw: Any, date: str, timezone_name: str) -> list[dict[str, Any]]:
    payload = clean_payload(raw)
    rows = payload.get("trades", []) if isinstance(payload, dict) else []
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol, market = normalize_symbol(row.get("symbol"))
        trade_time = row.get("trade_time")
        observed_date = market_date(trade_time, timezone_name)
        if observed_date and observed_date != date:
            continue
        if not symbol:
            continue
        result.append(
            {
                "broker": "ibkr",
                "trade_id": str(row.get("trade_id") or ""),
                "order_id": str(row.get("order_id") or ""),
                "symbol": symbol,
                "market": market,
                "asset_class": row.get("sec_type"),
                "side": str(row.get("side") or "").lower(),
                "quantity": to_float(row.get("size")),
                "price": to_float(row.get("price")),
                "currency": str(row.get("currency") or "USD"),
                "trade_time": trade_time,
                "market_date": observed_date or date,
                "commission": to_float(row.get("commission")),
                "realized_pnl": to_float(row.get("realized_pnl")),
                "order_type": row.get("order_type"),
                "exchange": row.get("exchange"),
            }
        )
    return result


def unwrap_rows(raw: Any, *keys: str) -> list[dict[str, Any]]:
    payload = clean_payload(raw)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = unwrap_rows(value, *keys)
            if nested:
                return nested
    return []


def longbridge_orders(raw: Any) -> list[dict[str, Any]]:
    result = []
    for row in unwrap_rows(raw, "orders", "list", "items", "data"):
        symbol, market = normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        result.append(
            {
                "broker": "longbridge",
                "order_id": str(row.get("order_id") or row.get("id") or ""),
                "symbol": symbol,
                "market": market,
                "side": str(row.get("side") or row.get("action") or "").lower(),
                "status": row.get("status"),
                "quantity": to_float(row.get("quantity")),
                "price": to_float(row.get("price")),
                "executed_quantity": to_float(row.get("executed_quantity")),
                "executed_price": to_float(row.get("executed_price")),
                "order_type": row.get("order_type"),
                "submitted_at": row.get("submitted_at"),
            }
        )
    return result


def longbridge_executions(raw: Any, date: str, timezone_name: str) -> list[dict[str, Any]]:
    result = []
    for row in unwrap_rows(raw, "executions", "trades", "list", "items", "data"):
        symbol, market = normalize_symbol(row.get("symbol"))
        trade_time = row.get("trade_done_at") or row.get("trade_time") or row.get("executed_at")
        observed_date = market_date(trade_time, timezone_name)
        if observed_date and observed_date != date:
            continue
        if not symbol:
            continue
        result.append(
            {
                "broker": "longbridge",
                "trade_id": str(row.get("trade_id") or row.get("execution_id") or ""),
                "order_id": str(row.get("order_id") or ""),
                "symbol": symbol,
                "market": market,
                "side": str(row.get("side") or "").lower(),
                "quantity": to_float(row.get("quantity")),
                "price": to_float(row.get("price")),
                "currency": row.get("currency"),
                "trade_time": trade_time,
                "market_date": observed_date or date,
            }
        )
    return result


def attach_order_context(executions: list[dict[str, Any]], orders: list[dict[str, Any]]) -> None:
    by_id = {row["order_id"]: row for row in orders if row.get("order_id")}
    for execution in executions:
        order = by_id.get(execution.get("order_id"))
        if not order:
            continue
        for key in ("order_type", "status", "submitted_at"):
            if order.get(key) not in (None, ""):
                execution[key] = order[key]


def trade_key(row: dict[str, Any]) -> tuple[Any, ...]:
    if row.get("trade_id"):
        return row.get("broker"), row.get("trade_id")
    return (
        row.get("broker"),
        row.get("order_id"),
        row.get("symbol"),
        row.get("side"),
        row.get("quantity"),
        row.get("price"),
        row.get("trade_time"),
    )


def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for row in sorted(rows, key=lambda item: str(item.get("trade_time") or "")):
        key = trade_key(row)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def notional_summary(executions: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in executions:
        quantity = to_float(row.get("quantity"))
        price = to_float(row.get("price"))
        if quantity is None or price is None:
            continue
        currency = str(row.get("currency") or "UNKNOWN")
        side = str(row.get("side") or "unknown").lower()
        key = f"{currency}:{side}"
        totals[key] = round(totals.get(key, 0.0) + abs(quantity) * price, 6)
    return totals


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
    sources: dict[str, dict[str, Any]] = {}
    executions = []

    ibkr_raw = resolve_input(repo_root, args.ibkr_trades)
    if ibkr_raw is not None:
        ibkr_rows = ibkr_trades(ibkr_raw, args.date, args.timezone)
        executions.extend(ibkr_rows)
        sources["ibkr"] = {"status": "success", "backend": "codex-app-plugin", "executions": len(ibkr_rows)}

    lb_execution_raw = resolve_input(repo_root, args.longbridge_executions)
    lb_order_raw = resolve_input(repo_root, args.longbridge_orders)
    if lb_execution_raw is None and args.longbridge_cli_fallback:
        try:
            raw = fetch_trade_snapshot(longbridge_cli_path(args.longbridge_cli))
            lb_execution_raw = raw.get("executions")
            lb_order_raw = raw.get("orders")
            sources["longbridge"] = {"status": "success", "backend": "longbridge-cli"}
        except Exception as exc:
            sources["longbridge"] = {
                "status": "failed",
                "backend": "longbridge-cli",
                "reason": str(exc),
            }
    elif lb_execution_raw is not None or lb_order_raw is not None:
        sources["longbridge"] = {"status": "success", "backend": "codex-app-plugin"}

    orders = longbridge_orders(lb_order_raw)
    lb_rows = longbridge_executions(lb_execution_raw, args.date, args.timezone)
    attach_order_context(lb_rows, orders)
    executions.extend(lb_rows)
    if sources.get("longbridge", {}).get("status") == "success":
        sources["longbridge"]["executions"] = len(lb_rows)
        sources["longbridge"]["orders"] = len(orders)

    if not sources:
        raise ValueError("at least one IBKR or Longbridge trade input is required")
    executions = dedupe(executions)
    brokers = sorted({str(row.get("broker")) for row in executions})
    output = (
        Path(args.output)
        if args.output
        else repo_root / "runtime" / "account" / args.date / "plugin-trade-snapshot.json"
    )
    if not output.is_absolute():
        output = repo_root / output
    payload = {
        "schema_version": 1,
        "date": args.date,
        "source": "read-only-broker-trade-activity",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timezone": args.timezone,
        "sources": sources,
        "executions": executions,
        "orders": orders,
        "summary": {
            "brokers_with_executions": brokers,
            "executions": len(executions),
            "buy_executions": sum(1 for row in executions if row.get("side") == "buy"),
            "sell_executions": sum(1 for row in executions if row.get("side") == "sell"),
            "symbols": sorted({str(row.get("symbol")) for row in executions if row.get("symbol")}),
            "notional_by_currency_side": notional_summary(executions),
            "realized_pnl_by_currency": {
                currency: round(
                    sum(
                        float(row["realized_pnl"])
                        for row in executions
                        if row.get("currency") == currency and row.get("realized_pnl") is not None
                    ),
                    6,
                )
                for currency in sorted(
                    {
                        str(row.get("currency"))
                        for row in executions
                        if row.get("currency") and row.get("realized_pnl") is not None
                    }
                )
            },
        },
        "limitations": [
            "Orders are context only; only executions are treated as completed trades.",
            "No automatic write is made to runtime/journal/trades.jsonl.",
            "Trade review must preserve broker provenance and disclose unavailable broker scopes.",
        ],
        "safety_note": "Read-only trade evidence. This artifact never submits, cancels, or modifies orders.",
    }
    atomic_write_json(output, payload)
    return {
        "status": "success",
        "date": args.date,
        "output": str(output),
        "executions_count": len(executions),
        "sources": sources,
        "symbols": payload["summary"]["symbols"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize read-only IBKR and Longbridge trade activity")
    parser.add_argument("--date", required=True)
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--ibkr-trades")
    parser.add_argument("--longbridge-executions")
    parser.add_argument("--longbridge-orders")
    parser.add_argument("--longbridge-cli-fallback", action="store_true")
    parser.add_argument("--longbridge-cli")
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
