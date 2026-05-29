#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_append import append_jsonl
from paper_order_sync import broker_order_id, contains_trace, load_order_records, match_by_symbol_side_quantity
from signal_artifacts import read_json


def default_preview_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / date / "paper-trade-preview.json"


def default_snapshot_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "runtime" / "paper" / date / "paper-account-snapshot.json"


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / date / "paper-trade-review.json"


def default_orders_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "runtime" / "paper" / date / "paper-orders.jsonl"


def journal_path(repo_root: Path, journal_dir: str) -> Path:
    base = Path(journal_dir)
    if not base.is_absolute():
        base = repo_root / base
    return base / "trades.jsonl"


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def execution_key(execution: dict[str, Any]) -> tuple[str, str]:
    return (symbol_key(execution), str(execution.get("side") or "").lower())


def symbol_key(payload: dict[str, Any]) -> str:
    value = str(payload.get("longbridge_symbol") or payload.get("symbol") or "").upper()
    if "." in value:
        return value.split(".", 1)[0]
    return value


def submitted_order_for_preview(order: dict[str, Any], submitted_orders: list[dict[str, Any]]) -> dict[str, Any] | None:
    signal_id = str(order.get("signal_id") or "")
    for submitted in submitted_orders:
        if str(submitted.get("source_signal_id") or "") == signal_id:
            return submitted
    return None


def find_execution_for_order(
    order: dict[str, Any],
    submitted_order: dict[str, Any] | None,
    executions: list[dict[str, Any]],
    used_execution_indexes: set[int],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    available = [(index, execution) for index, execution in enumerate(executions) if index not in used_execution_indexes]
    if submitted_order:
        submitted_broker_order_id = str(submitted_order.get("broker_order_id") or "").strip()
        if submitted_broker_order_id:
            for index, execution in available:
                if broker_order_id(execution) == submitted_broker_order_id:
                    used_execution_indexes.add(index)
                    return execution, {"method": "broker_order_id"}

        remark = str(submitted_order.get("remark") or "")
        if remark:
            for index, execution in available:
                if contains_trace(execution, remark):
                    used_execution_indexes.add(index)
                    return execution, {"method": "remark"}

        intent_id = str(submitted_order.get("intent_id") or "")
        if intent_id:
            for index, execution in available:
                if contains_trace(execution, intent_id):
                    used_execution_indexes.add(index)
                    return execution, {"method": "intent_id"}

        fallback = match_by_symbol_side_quantity(submitted_order, [execution for _, execution in available])
        if fallback:
            for index, execution in available:
                if execution is fallback:
                    used_execution_indexes.add(index)
                    return execution, {"method": "symbol_side_quantity"}

    key = (symbol_key(order), str(order.get("side") or "").lower())
    for index, execution in available:
        if execution_key(execution) == key:
            used_execution_indexes.add(index)
            return execution, {"method": "symbol_side"}
    return None, {"method": "none"}


def slippage_pct(planned_entry: float | None, entry: float | None) -> float | None:
    if planned_entry is None or planned_entry == 0 or entry is None:
        return None
    return round((entry - planned_entry) / planned_entry * 100, 4)


def trade_record(
    date: str,
    order: dict[str, Any],
    execution: dict[str, Any],
    submitted_order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    planned_entry = as_float(order.get("entry_price")) or as_float((submitted_order or {}).get("limit_price"))
    entry = as_float(execution.get("price")) or planned_entry
    source_signal_id = (submitted_order or {}).get("source_signal_id") or order.get("signal_id")
    order_id = broker_order_id(execution) or (submitted_order or {}).get("broker_order_id")
    return {
        "kind": "trade",
        "mode": "paper",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": date,
        "symbol": order.get("symbol"),
        "status": "entered",
        "planned_setup": order.get("setup"),
        "entry": entry,
        "planned_entry": planned_entry,
        "stop": as_float(order.get("stop_price")) or as_float((submitted_order or {}).get("stop_price")),
        "take_profit": as_float(order.get("take_profit")) or as_float((submitted_order or {}).get("take_profit")),
        "source_signal_id": source_signal_id,
        "intent_id": (submitted_order or {}).get("intent_id"),
        "broker_order_id": order_id,
        "paper_order_id": order_id,
        "paper_quantity": as_float(execution.get("quantity")),
        "paper_side": execution.get("side"),
        "slippage_pct": slippage_pct(planned_entry, entry),
    }


def existing_trade_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    keys: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        keys.add((str(record.get("source_signal_id") or ""), str(record.get("paper_order_id") or "")))
    return keys


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    preview_path = default_preview_path(repo_root, args.date, args.preview)
    snapshot_path = default_snapshot_path(repo_root, args.date, args.paper_snapshot)
    orders_path = default_orders_path(repo_root, args.date, args.orders_journal)
    preview = read_json(preview_path)
    snapshot = read_json(snapshot_path)
    submitted_orders = load_order_records(orders_path)
    orders = preview.get("orders")
    executions = snapshot.get("executions")
    if not isinstance(orders, list):
        raise ValueError(f"{preview_path}: orders must be an array")
    if not isinstance(executions, list):
        raise ValueError(f"{snapshot_path}: executions must be an array")

    reviews = []
    trade_records = []
    used_execution_indexes: set[int] = set()
    for order in orders:
        if not isinstance(order, dict):
            continue
        if order.get("status") != "ready":
            reviews.append({"order": order, "paper_status": "preview_blocked", "execution": None})
            continue
        submitted_order = submitted_order_for_preview(order, submitted_orders)
        if submitted_orders and not submitted_order:
            reviews.append(
                {
                    "order": order,
                    "submitted_order": None,
                    "paper_status": "no_paper_execution",
                    "execution": None,
                    "match": {"method": "none"},
                }
            )
            continue
        execution, match = find_execution_for_order(order, submitted_order, executions, used_execution_indexes)
        if not execution:
            reviews.append({"order": order, "submitted_order": submitted_order, "paper_status": "no_paper_execution", "execution": None, "match": match})
            continue
        reviews.append({"order": order, "submitted_order": submitted_order, "paper_status": "filled", "execution": execution, "match": match})
        trade_records.append(trade_record(args.date, order, execution, submitted_order))

    output = default_output_path(repo_root, args.date, args.output)
    summary = {
        "total": len(reviews),
        "filled": len([item for item in reviews if item["paper_status"] == "filled"]),
        "no_paper_execution": len([item for item in reviews if item["paper_status"] == "no_paper_execution"]),
        "preview_blocked": len([item for item in reviews if item["paper_status"] == "preview_blocked"]),
    }
    appended: list[dict[str, Any]] = []
    skipped_duplicates: list[dict[str, Any]] = []
    if args.append:
        path = journal_path(repo_root, args.journal_dir)
        existing = existing_trade_keys(path)
        for record in trade_records:
            key = (str(record.get("source_signal_id") or ""), str(record.get("paper_order_id") or ""))
            if key in existing:
                skipped_duplicates.append(record)
                continue
            append_jsonl(path, record)
            existing.add(key)
            appended.append(record)

    payload = {
        "date": args.date,
        "session": args.session,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_preview": str(preview_path),
        "source_paper_snapshot": str(snapshot_path),
        "source_orders_journal": str(orders_path) if orders_path.exists() else None,
        "summary": summary,
        "reviews": reviews,
        "appended": appended,
        "skipped_duplicates": skipped_duplicates,
        "safety_note": "Review only. This workflow records observed paper executions and does not submit orders.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "success",
        "date": args.date,
        "output": str(output),
        "summary": summary,
        "appended": appended,
        "skipped_duplicates": skipped_duplicates,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review paper-trading executions against order previews")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    parser.add_argument("--preview")
    parser.add_argument("--paper-snapshot")
    parser.add_argument("--orders-journal")
    parser.add_argument("--output")
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--journal-dir", default="runtime/journal")
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
