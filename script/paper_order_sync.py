#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signal_artifacts import read_json


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def default_orders_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-orders.jsonl")


def default_stops_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-stop-orders.jsonl")


def default_take_profit_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-take-profit-orders.jsonl")


def default_snapshot_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-account-snapshot.json")


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-execution-state.json")


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def symbol_key(payload: dict[str, Any]) -> str:
    value = str(payload.get("longbridge_symbol") or payload.get("symbol") or "").upper()
    if "." in value:
        return value.split(".", 1)[0]
    return value


def quantity_key(value: Any) -> float | None:
    numeric = as_float(value)
    if numeric is None:
        return None
    return float(int(numeric)) if numeric == int(numeric) else numeric


def load_order_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def contains_trace(payload: dict[str, Any], trace: str) -> bool:
    if not trace:
        return False
    if trace in str(payload.get("remark") or ""):
        return True
    raw = payload.get("raw")
    if isinstance(raw, dict):
        return trace in json.dumps(raw, ensure_ascii=False, sort_keys=True)
    return False


def broker_order_id(payload: dict[str, Any]) -> str:
    return str(payload.get("order_id") or payload.get("broker_order_id") or "").strip()


def match_by_symbol_side_quantity(intent: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    target = (
        symbol_key(intent),
        str(intent.get("side") or "").lower(),
        quantity_key(intent.get("quantity")),
    )
    for candidate in candidates:
        current = (
            symbol_key(candidate),
            str(candidate.get("side") or "").lower(),
            quantity_key(candidate.get("quantity")),
        )
        if current == target:
            return candidate
    return None


def find_broker_order(intent: dict[str, Any], broker_orders: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    intent_broker_id = str(intent.get("broker_order_id") or "").strip()
    if intent_broker_id:
        for order in broker_orders:
            if broker_order_id(order) == intent_broker_id:
                return order, "broker_order_id"

    remark = str(intent.get("remark") or "")
    if remark:
        for order in broker_orders:
            if contains_trace(order, remark):
                return order, "remark"

    intent_id = str(intent.get("intent_id") or "")
    if intent_id:
        for order in broker_orders:
            if contains_trace(order, intent_id):
                return order, "intent_id"

    fallback = match_by_symbol_side_quantity(intent, broker_orders)
    if fallback:
        return fallback, "symbol_side_quantity"
    return None, "none"


def find_executions(intent: dict[str, Any], broker_order: dict[str, Any] | None, executions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    matched: list[dict[str, Any]] = []
    order_id = broker_order_id(broker_order or {}) or str(intent.get("broker_order_id") or "")
    if order_id:
        matched = [execution for execution in executions if broker_order_id(execution) == order_id]
        if matched:
            return matched, "broker_order_id"

    remark = str(intent.get("remark") or "")
    if remark:
        matched = [execution for execution in executions if contains_trace(execution, remark)]
        if matched:
            return matched, "remark"

    intent_id = str(intent.get("intent_id") or "")
    if intent_id:
        matched = [execution for execution in executions if contains_trace(execution, intent_id)]
        if matched:
            return matched, "intent_id"

    fallback = match_by_symbol_side_quantity(intent, executions)
    if fallback:
        return [fallback], "symbol_side_quantity"
    return [], "none"


def weighted_average_price(executions: list[dict[str, Any]]) -> float | None:
    total_quantity = 0.0
    total_notional = 0.0
    for execution in executions:
        quantity = as_float(execution.get("quantity")) or 0.0
        price = as_float(execution.get("price"))
        if price is None or quantity <= 0:
            continue
        total_quantity += quantity
        total_notional += quantity * price
    if total_quantity <= 0:
        return None
    return round(total_notional / total_quantity, 4)


def normalize_status(raw_status: Any) -> str:
    status = str(raw_status or "").strip().lower()
    if not status:
        return "submitted"
    if "partial" in status:
        return "partially_filled"
    if "fill" in status or status in {"done", "executed"}:
        return "filled"
    if "cancel" in status:
        return "cancelled"
    if "reject" in status:
        return "rejected"
    if "expire" in status:
        return "expired"
    if status in {"submitted", "accepted", "new", "open", "pending", "queued"}:
        return "accepted"
    return status


def synced_order(intent: dict[str, Any], broker_orders: list[dict[str, Any]], executions: list[dict[str, Any]]) -> dict[str, Any]:
    broker_order, order_match_method = find_broker_order(intent, broker_orders)
    matched_executions, execution_match_method = find_executions(intent, broker_order, executions)
    requested_quantity = as_float(intent.get("quantity")) or 0.0
    filled_quantity = sum(as_float(execution.get("quantity")) or 0.0 for execution in matched_executions)
    if filled_quantity > 0 and requested_quantity > 0 and filled_quantity >= requested_quantity:
        status = "filled"
    elif filled_quantity > 0:
        status = "partially_filled"
    elif broker_order:
        status = normalize_status(broker_order.get("status"))
    else:
        status = "submitted"

    resolved_broker_order_id = str(intent.get("broker_order_id") or "") or broker_order_id(broker_order or {})
    return {
        "intent_id": intent.get("intent_id"),
        "source_signal_id": intent.get("source_signal_id"),
        "date": intent.get("date"),
        "session": intent.get("session"),
        "symbol": intent.get("symbol"),
        "longbridge_symbol": intent.get("longbridge_symbol"),
        "side": intent.get("side"),
        "order_type": intent.get("order_type"),
        "quantity": intent.get("quantity"),
        "limit_price": intent.get("limit_price"),
        "remark": intent.get("remark"),
        "submitted_at": intent.get("submitted_at"),
        "broker_order_id": resolved_broker_order_id or None,
        "status": status,
        "filled_quantity": filled_quantity,
        "avg_fill_price": weighted_average_price(matched_executions),
        "match": {
            "method": order_match_method if order_match_method != "none" else execution_match_method,
            "order_method": order_match_method,
            "execution_method": execution_match_method,
        },
        "broker_order": broker_order,
        "executions": matched_executions,
    }


def synced_exit_order(intent: dict[str, Any], broker_orders: list[dict[str, Any]], executions: list[dict[str, Any]]) -> dict[str, Any]:
    synced = synced_order(intent, broker_orders, executions)
    synced["kind"] = intent.get("kind")
    synced["entry_broker_order_id"] = intent.get("entry_broker_order_id")
    synced["trigger_price"] = intent.get("trigger_price")
    synced["take_profit"] = intent.get("take_profit")
    synced["exit_fraction"] = intent.get("exit_fraction")
    return synced


def status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def status_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = status_counts(items)
    return {
        "total": len(items),
        "submitted": counts.get("submitted", 0),
        "accepted": counts.get("accepted", 0),
        "partially_filled": counts.get("partially_filled", 0),
        "filled": counts.get("filled", 0),
        "cancelled": counts.get("cancelled", 0),
        "rejected": counts.get("rejected", 0),
        "expired": counts.get("expired", 0),
        "unknown": counts.get("unknown", 0),
    }


def first_by_intent(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        intent_id = str(item.get("intent_id") or "")
        if intent_id and intent_id not in indexed:
            indexed[intent_id] = item
    return indexed


def enrich_entry_with_exits(
    entry: dict[str, Any],
    *,
    protective_stop: dict[str, Any] | None,
    take_profit: dict[str, Any] | None,
) -> dict[str, Any]:
    enriched = dict(entry)
    if protective_stop:
        enriched["protective_stop_order_id"] = protective_stop.get("broker_order_id")
        enriched["stop_order_id"] = protective_stop.get("broker_order_id")
        enriched["stop_status"] = protective_stop.get("status")
        enriched["current_stop_price"] = protective_stop.get("trigger_price")
        enriched["stop_filled_quantity"] = protective_stop.get("filled_quantity")
    if take_profit:
        filled_quantity = as_float(take_profit.get("filled_quantity")) or 0.0
        entry_filled_quantity = as_float(entry.get("filled_quantity")) or 0.0
        remaining_quantity = max(0.0, entry_filled_quantity - filled_quantity)
        enriched["take_profit_order_id"] = take_profit.get("broker_order_id")
        enriched["tp1_order_id"] = take_profit.get("broker_order_id")
        enriched["take_profit_status"] = take_profit.get("status")
        enriched["tp1_status"] = take_profit.get("status")
        enriched["take_profit_filled_quantity"] = filled_quantity
        enriched["tp1_filled_quantity"] = filled_quantity
        enriched["remaining_quantity"] = int(remaining_quantity) if remaining_quantity == int(remaining_quantity) else remaining_quantity
    return enriched


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    orders_path = default_orders_path(repo_root, args.date, args.orders_journal)
    stops_path = default_stops_path(repo_root, args.date, args.stops_journal)
    take_profit_path = default_take_profit_path(repo_root, args.date, args.take_profit_journal)
    snapshot_path = default_snapshot_path(repo_root, args.date, args.paper_snapshot)
    output = default_output_path(repo_root, args.date, args.output)
    submitted_orders = load_order_records(orders_path)
    submitted_stops = load_order_records(stops_path)
    submitted_take_profits = load_order_records(take_profit_path)
    snapshot = read_json(snapshot_path)
    broker_orders = snapshot.get("orders") if isinstance(snapshot.get("orders"), list) else []
    executions = snapshot.get("executions") if isinstance(snapshot.get("executions"), list) else []
    synced = [
        synced_order(order, broker_orders, executions)
        for order in submitted_orders
        if isinstance(order, dict) and order.get("intent_id")
    ]
    synced_stops = [
        synced_exit_order(order, broker_orders, executions)
        for order in submitted_stops
        if isinstance(order, dict) and order.get("intent_id")
    ]
    synced_take_profits = [
        synced_exit_order(order, broker_orders, executions)
        for order in submitted_take_profits
        if isinstance(order, dict) and order.get("intent_id")
    ]
    stops_by_intent = first_by_intent(synced_stops)
    take_profits_by_intent = first_by_intent(synced_take_profits)
    enriched_orders = [
        enrich_entry_with_exits(
            order,
            protective_stop=stops_by_intent.get(str(order.get("intent_id") or "")),
            take_profit=take_profits_by_intent.get(str(order.get("intent_id") or "")),
        )
        for order in synced
    ]
    summary = status_summary(enriched_orders)
    exit_summary = {
        "protective_stops": status_summary(synced_stops),
        "take_profit_orders": status_summary(synced_take_profits),
    }
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_orders_journal": str(orders_path),
        "source_stops_journal": str(stops_path),
        "source_take_profit_journal": str(take_profit_path),
        "source_paper_snapshot": str(snapshot_path),
        "summary": summary,
        "exit_summary": exit_summary,
        "orders": enriched_orders,
        "protective_stops": synced_stops,
        "take_profit_orders": synced_take_profits,
        "safety_note": "Read-only paper order sync. This workflow does not submit, cancel, or replace orders.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "summary": summary}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "orders_journal": None,
        "stops_journal": None,
        "take_profit_journal": None,
        "paper_snapshot": None,
        "output": None,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync submitted paper orders with a paper account snapshot")
    parser.add_argument("--date", required=True)
    parser.add_argument("--orders-journal")
    parser.add_argument("--stops-journal")
    parser.add_argument("--take-profit-journal")
    parser.add_argument("--paper-snapshot")
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
