#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from longbridge_paper_order_adapter import format_decimal
from signal_artifacts import read_json


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def default_state_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-execution-state.json")


def default_stops_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-stop-orders.jsonl")


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-break-even-stop-plan.json")


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
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


def stop_records_by_intent(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for record in load_jsonl_records(path):
        intent_id = str(record.get("intent_id") or "")
        broker_order_id = str(record.get("broker_order_id") or "")
        if intent_id and broker_order_id:
            records[intent_id] = record
    return records


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_quantity(value: Any) -> int:
    numeric = as_float(value)
    return int(numeric or 0)


def first_float(*values: Any) -> float | None:
    for value in values:
        numeric = as_float(value)
        if numeric is not None:
            return numeric
    return None


def first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def tp1_is_filled(order: dict[str, Any]) -> bool:
    status = str(
        order.get("tp1_status")
        or order.get("take_profit_status")
        or order.get("take_profit_order_status")
        or ""
    ).lower()
    if status == "filled":
        return True
    return as_quantity(order.get("tp1_filled_quantity") or order.get("take_profit_filled_quantity")) > 0


def tp1_filled_quantity(order: dict[str, Any]) -> int:
    return as_quantity(order.get("tp1_filled_quantity") or order.get("take_profit_filled_quantity"))


def break_even_price(order: dict[str, Any], *, buffer_pct: float) -> float | None:
    base = first_float(order.get("avg_fill_price"), order.get("entry_price"), order.get("limit_price"))
    if base is None or base <= 0:
        return None
    return round(base * (1 + buffer_pct / 100), 4)


def break_even_candidate_or_block(
    order: dict[str, Any],
    *,
    stop_records: dict[str, dict[str, Any]],
    buffer_pct: float,
    tif: str,
) -> tuple[str, dict[str, Any]]:
    intent_id = str(order.get("intent_id") or "")
    stop_record = stop_records.get(intent_id, {})
    existing_stop_order_id = first_nonempty(
        order.get("break_even_stop_order_id"),
        order.get("protective_stop_order_id"),
        order.get("stop_order_id"),
        stop_record.get("broker_order_id"),
    )
    current_stop_price = first_float(order.get("current_stop_price"), order.get("stop_price"), stop_record.get("trigger_price"))
    filled_quantity = as_quantity(order.get("filled_quantity"))
    tp1_quantity = tp1_filled_quantity(order)
    remaining_quantity = as_quantity(order.get("remaining_quantity")) or max(0, filled_quantity - tp1_quantity)
    new_stop_price = break_even_price(order, buffer_pct=buffer_pct)
    longbridge_symbol = str(order.get("longbridge_symbol") or "")
    remark = f"tca-be-stop:{intent_id}"
    base = {
        "intent_id": intent_id,
        "source_signal_id": order.get("source_signal_id"),
        "entry_broker_order_id": order.get("broker_order_id"),
        "existing_stop_order_id": existing_stop_order_id or None,
        "symbol": order.get("symbol"),
        "longbridge_symbol": longbridge_symbol,
        "entry_status": order.get("status"),
        "entry_side": order.get("side"),
        "filled_quantity": filled_quantity,
        "tp1_filled_quantity": tp1_quantity,
        "remaining_quantity": remaining_quantity,
        "current_stop_price": current_stop_price,
        "new_stop_price": new_stop_price,
        "avg_fill_price": order.get("avg_fill_price"),
        "buffer_pct": buffer_pct,
        "remark": remark,
    }
    if order.get("side") != "buy":
        return "blocked", {**base, "reason": "only long buy entries are supported"}
    if order.get("status") != "filled" or filled_quantity <= 0:
        return "blocked", {**base, "reason": "entry order is not fully filled"}
    if not tp1_is_filled(order):
        return "blocked", {**base, "reason": "TP1 fill evidence is required"}
    if not existing_stop_order_id:
        return "blocked", {**base, "reason": "existing protective stop order is required"}
    if not longbridge_symbol:
        return "blocked", {**base, "reason": "longbridge_symbol is required"}
    if remaining_quantity <= 0:
        return "blocked", {**base, "reason": "remaining quantity must be > 0"}
    if new_stop_price is None or new_stop_price <= 0:
        return "blocked", {**base, "reason": "avg_fill_price or entry price is required"}
    if current_stop_price is not None and current_stop_price >= new_stop_price:
        return "blocked", {**base, "reason": "stop is already at or above break-even"}
    preview_steps = [
        {
            "action": "cancel_existing_stop",
            "command": ["longbridge", "order", "cancel", existing_stop_order_id, "--format", "json"],
        },
        {
            "action": "submit_break_even_stop",
            "command": [
                "longbridge",
                "order",
                "sell",
                longbridge_symbol,
                str(remaining_quantity),
                "--order-type",
                "MIT",
                "--trigger-price",
                format_decimal(new_stop_price),
                "--tif",
                tif,
                "--remark",
                remark,
                "--format",
                "json",
            ],
        },
    ]
    return (
        "candidate",
        {
            **base,
            "side": "sell",
            "order_type": "MIT",
            "trigger_price": new_stop_price,
            "tif": tif,
            "preview_steps": preview_steps,
            "reason": "TP1 fill allows stop movement to break-even",
        },
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.buffer_pct < 0:
        raise ValueError("--buffer-pct must be >= 0")
    repo_root = Path(args.repo_root).resolve()
    state_path = default_state_path(repo_root, args.date, args.state)
    stops_path = default_stops_path(repo_root, args.date, args.stops_journal)
    output = default_output_path(repo_root, args.date, args.output)
    state = read_json(state_path)
    orders = state.get("orders")
    if not isinstance(orders, list):
        raise ValueError(f"{state_path}: orders must be an array")
    stop_records = stop_records_by_intent(stops_path)

    move_candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        bucket, record = break_even_candidate_or_block(order, stop_records=stop_records, buffer_pct=args.buffer_pct, tif=args.tif)
        if bucket == "candidate":
            move_candidates.append(record)
        else:
            blocked.append(record)

    summary = {
        "total": len([order for order in orders if isinstance(order, dict)]),
        "move_candidates": len(move_candidates),
        "blocked": len(blocked),
    }
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": True,
        "execute_supported": False,
        "source_execution_state": str(state_path),
        "source_stops_journal": str(stops_path),
        "buffer_pct": args.buffer_pct,
        "tif": args.tif,
        "move_candidates": move_candidates,
        "blocked": blocked,
        "summary": summary,
        "safety_note": "Dry-run break-even stop movement plan only. This workflow does not cancel, replace, or submit broker orders.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "dry_run": True, "summary": summary}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "state": None,
        "stops_journal": None,
        "output": None,
        "buffer_pct": 0.0,
        "tif": "gtc",
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a dry-run break-even stop movement plan for paper entries")
    parser.add_argument("--date", required=True)
    parser.add_argument("--state")
    parser.add_argument("--stops-journal")
    parser.add_argument("--output")
    parser.add_argument("--buffer-pct", type=float, default=0.0)
    parser.add_argument("--tif", default="gtc")
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
