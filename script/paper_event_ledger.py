#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_order_sync import load_order_records
from signal_artifacts import read_json


WORKFLOW = "paper-event-ledger"
ORDER_SHAPE_FIELDS = (
    "limit_price",
    "trigger_price",
    "trailing_amount",
    "trailing_percent",
    "limit_offset",
    "tif",
    "expire_date",
    "outside_rth",
)


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


def default_exits_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-exit-orders.jsonl")


def default_replace_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-replace-orders.jsonl")


def default_state_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-execution-state.json")


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-event-ledger.json")


def default_events_path(repo_root: Path, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "journal" / "events.jsonl")


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = read_json(path)
    return payload if isinstance(payload, dict) else None


def load_existing_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            events.append(record)
    return events


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records]
    path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def event_id(payload: dict[str, Any]) -> str:
    stable = {
        "date": payload.get("date"),
        "event_type": payload.get("event_type"),
        "entity_type": payload.get("entity_type"),
        "entity_id": payload.get("entity_id"),
        "status": payload.get("status"),
    }
    digest = hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:24]


def event(
    *,
    date: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    status: str | None = None,
    occurred_at: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "kind": "trading_event",
        "workflow": WORKFLOW,
        "date": date,
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "status": status,
        "occurred_at": occurred_at,
        "payload": payload or {},
    }
    record["event_id"] = event_id(record)
    return record


def broker_id(record: dict[str, Any]) -> str:
    return str(record.get("broker_order_id") or record.get("order_id") or "").strip()


def intent_id(record: dict[str, Any]) -> str:
    return str(record.get("intent_id") or "").strip()


def submitted_events(date: str, records: list[dict[str, Any]], *, event_type: str, entity_type: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        current_intent_id = intent_id(record)
        if not current_intent_id:
            continue
        payload = {
            "intent_id": current_intent_id,
            "source_signal_id": record.get("source_signal_id"),
            "broker_order_id": broker_id(record) or None,
            "entry_broker_order_id": record.get("entry_broker_order_id"),
            "symbol": record.get("symbol"),
            "longbridge_symbol": record.get("longbridge_symbol"),
            "side": record.get("side"),
            "order_type": record.get("order_type"),
            "quantity": record.get("quantity"),
            "remark": record.get("remark"),
            "raw_request": record.get("raw_request") if isinstance(record.get("raw_request"), dict) else {},
            "raw_response": record.get("raw_response") if isinstance(record.get("raw_response"), dict) else {},
        }
        for field in ORDER_SHAPE_FIELDS:
            payload[field] = record.get(field)
        events.append(
            event(
                date=date,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=current_intent_id,
                status=str(record.get("submit_status") or "submitted"),
                occurred_at=record.get("submitted_at"),
                payload=payload,
            )
        )
    return events


def replace_events(date: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        current_intent_id = intent_id(record)
        if not current_intent_id:
            continue
        payload = {
            "intent_id": current_intent_id,
            "source_signal_id": record.get("source_signal_id"),
            "broker_order_id": broker_id(record) or None,
            "symbol": record.get("symbol"),
            "longbridge_symbol": record.get("longbridge_symbol"),
            "previous_quantity": record.get("previous_quantity"),
            "new_quantity": record.get("new_quantity"),
            "previous_limit_price": record.get("previous_limit_price"),
            "new_limit_price": record.get("new_limit_price"),
            "decision_reason": record.get("decision_reason"),
            "raw_request": record.get("raw_request") if isinstance(record.get("raw_request"), dict) else {},
            "raw_response": record.get("raw_response") if isinstance(record.get("raw_response"), dict) else {},
        }
        events.append(
            event(
                date=date,
                event_type="order_replaced",
                entity_type="paper_entry_order",
                entity_id=current_intent_id,
                status="replaced",
                occurred_at=record.get("submitted_at"),
                payload=payload,
            )
        )
    return events


def status_event_type(entity_type: str, status: str) -> str:
    if entity_type == "paper_entry_order":
        if status == "filled":
            return "order_filled"
        if status in {"rejected", "cancelled", "expired", "partially_filled", "accepted", "submitted"}:
            return f"order_{status}"
    if entity_type == "paper_stop_order":
        if status == "filled":
            return "stop_filled"
        if status in {"rejected", "cancelled", "expired", "partially_filled", "accepted", "submitted"}:
            return f"stop_{status}"
    if entity_type == "paper_take_profit_order":
        if status == "filled":
            return "take_profit_filled"
        if status in {"rejected", "cancelled", "expired", "partially_filled", "accepted", "submitted"}:
            return f"take_profit_{status}"
    if entity_type == "paper_exit_order":
        if status == "filled":
            return "exit_filled"
        if status in {"rejected", "cancelled", "expired", "partially_filled", "accepted", "submitted"}:
            return f"exit_{status}"
    return f"{entity_type}_{status}"


def execution_state_events(date: str, records: list[dict[str, Any]], *, entity_type: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        current_intent_id = intent_id(record)
        status = str(record.get("status") or "").strip()
        if not current_intent_id or not status:
            continue
        payload = {
            "intent_id": current_intent_id,
            "source_signal_id": record.get("source_signal_id"),
            "broker_order_id": broker_id(record) or None,
            "entry_broker_order_id": record.get("entry_broker_order_id"),
            "symbol": record.get("symbol"),
            "longbridge_symbol": record.get("longbridge_symbol"),
            "side": record.get("side"),
            "order_type": record.get("order_type"),
            "quantity": record.get("quantity"),
            "filled_quantity": record.get("filled_quantity"),
            "avg_fill_price": record.get("avg_fill_price"),
            "match": record.get("match") if isinstance(record.get("match"), dict) else {},
        }
        for field in ORDER_SHAPE_FIELDS:
            payload[field] = record.get(field)
        events.append(
            event(
                date=date,
                event_type=status_event_type(entity_type, status),
                entity_type=entity_type,
                entity_id=current_intent_id,
                status=status,
                occurred_at=record.get("submitted_at"),
                payload=payload,
            )
        )
    return events


def collect_events(
    *,
    date: str,
    orders: list[dict[str, Any]],
    stops: list[dict[str, Any]],
    take_profits: list[dict[str, Any]],
    exits: list[dict[str, Any]],
    replaces: list[dict[str, Any]],
    state: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    events.extend(submitted_events(date, orders, event_type="order_submitted", entity_type="paper_entry_order"))
    events.extend(submitted_events(date, stops, event_type="stop_submitted", entity_type="paper_stop_order"))
    events.extend(
        submitted_events(
            date,
            take_profits,
            event_type="take_profit_submitted",
            entity_type="paper_take_profit_order",
        )
    )
    events.extend(submitted_events(date, exits, event_type="exit_submitted", entity_type="paper_exit_order"))
    events.extend(replace_events(date, replaces))
    if state:
        state_orders = state.get("orders") if isinstance(state.get("orders"), list) else []
        state_stops = state.get("protective_stops") if isinstance(state.get("protective_stops"), list) else []
        state_take_profits = state.get("take_profit_orders") if isinstance(state.get("take_profit_orders"), list) else []
        state_exits = state.get("exit_orders") if isinstance(state.get("exit_orders"), list) else []
        events.extend(execution_state_events(date, state_orders, entity_type="paper_entry_order"))
        events.extend(execution_state_events(date, state_stops, entity_type="paper_stop_order"))
        events.extend(execution_state_events(date, state_take_profits, entity_type="paper_take_profit_order"))
        events.extend(execution_state_events(date, state_exits, entity_type="paper_exit_order"))

    deduped: dict[str, dict[str, Any]] = {}
    for item in events:
        deduped[str(item["event_id"])] = item
    return list(deduped.values())


def replace_date_events(existing: list[dict[str, Any]], *, date: str, new_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preserved = [
        record
        for record in existing
        if not (record.get("workflow") == WORKFLOW and record.get("date") == date)
    ]
    combined = [*preserved, *new_events]
    return sorted(combined, key=lambda item: (str(item.get("date") or ""), str(item.get("event_id") or "")))


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    orders_path = default_orders_path(repo_root, args.date, args.orders_journal)
    stops_path = default_stops_path(repo_root, args.date, args.stops_journal)
    take_profit_path = default_take_profit_path(repo_root, args.date, args.take_profit_journal)
    exits_path = default_exits_path(repo_root, args.date, args.exits_journal)
    replace_path = default_replace_path(repo_root, args.date, args.replace_journal)
    state_path = default_state_path(repo_root, args.date, args.execution_state)
    events_path = default_events_path(repo_root, args.events_journal)
    output = default_output_path(repo_root, args.date, args.output)

    orders = load_order_records(orders_path)
    stops = load_order_records(stops_path)
    take_profits = load_order_records(take_profit_path)
    exits = load_order_records(exits_path)
    replaces = load_order_records(replace_path)
    state = load_json_if_exists(state_path)
    new_events = collect_events(date=args.date, orders=orders, stops=stops, take_profits=take_profits, exits=exits, replaces=replaces, state=state)
    existing_events = load_existing_events(events_path)
    combined_events = replace_date_events(existing_events, date=args.date, new_events=new_events)
    write_jsonl(events_path, combined_events)

    type_counts: dict[str, int] = {}
    for item in new_events:
        event_type = str(item.get("event_type") or "unknown")
        type_counts[event_type] = type_counts.get(event_type, 0) + 1

    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_orders_journal": str(orders_path),
        "source_stops_journal": str(stops_path),
        "source_take_profit_journal": str(take_profit_path),
        "source_exits_journal": str(exits_path),
        "source_replace_journal": str(replace_path),
        "source_execution_state": str(state_path) if state_path.exists() else None,
        "events_journal": str(events_path),
        "summary": {
            "events_written_for_date": len(new_events),
            "events_total": len(combined_events),
            "event_types": type_counts,
        },
        "events": new_events,
        "safety_note": "Event projection only. This workflow reads paper journals/state and does not call broker APIs.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "events_journal": str(events_path), "summary": payload["summary"]}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "orders_journal": None,
        "stops_journal": None,
        "take_profit_journal": None,
        "exits_journal": None,
        "replace_journal": None,
        "execution_state": None,
        "events_journal": None,
        "output": None,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project paper execution facts into a unified event ledger")
    parser.add_argument("--date", required=True)
    parser.add_argument("--orders-journal")
    parser.add_argument("--stops-journal")
    parser.add_argument("--take-profit-journal")
    parser.add_argument("--exits-journal")
    parser.add_argument("--replace-journal")
    parser.add_argument("--execution-state")
    parser.add_argument("--events-journal")
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
