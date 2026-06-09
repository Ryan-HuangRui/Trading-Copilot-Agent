#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from longbridge_paper_order_adapter import LongbridgePaperOrderAdapter, format_decimal
from paper_execution_config import broker_capability_matrix, load_paper_execution_config, paper_execution_policy
from paper_order_models import (
    PRICE_REQUIRED_ORDER_TYPES,
    TRAILING_AMOUNT_REQUIRED_ORDER_TYPES,
    TRAILING_PERCENT_REQUIRED_ORDER_TYPES,
    TRIGGER_PRICE_REQUIRED_ORDER_TYPES,
    normalize_order_type,
    normalize_tif,
    validate_order_shape,
)
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


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


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
    order_type: str,
    limit_price: float | None,
    trigger_price: float | None,
    trailing_amount: float | None,
    trailing_percent: float | None,
    limit_offset: float | None,
    tif: str,
    expire_date: str | None,
    outside_rth: str | None,
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
    normalized_order_type = normalize_order_type(order_type)
    normalized_tif = normalize_tif(tif)
    resolved_limit_price = limit_price
    if resolved_limit_price is None and normalized_order_type in PRICE_REQUIRED_ORDER_TYPES:
        resolved_limit_price = new_stop_price
    resolved_trigger_price = trigger_price
    if resolved_trigger_price is None and normalized_order_type in TRIGGER_PRICE_REQUIRED_ORDER_TYPES:
        resolved_trigger_price = new_stop_price
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
        "side": "sell",
        "order_type": normalized_order_type,
        "limit_price": resolved_limit_price,
        "trigger_price": resolved_trigger_price,
        "trailing_amount": trailing_amount,
        "trailing_percent": trailing_percent,
        "limit_offset": limit_offset,
        "tif": normalized_tif,
        "expire_date": expire_date,
        "outside_rth": outside_rth,
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
    shape_errors = validate_order_shape(
        {
            "side": "sell",
            "longbridge_symbol": longbridge_symbol,
            "quantity": remaining_quantity,
            "order_type": normalized_order_type,
            "limit_price": resolved_limit_price,
            "trigger_price": resolved_trigger_price,
            "trailing_amount": trailing_amount,
            "trailing_percent": trailing_percent,
            "limit_offset": limit_offset,
            "tif": normalized_tif,
            "expire_date": expire_date,
            "outside_rth": outside_rth,
        }
    )
    if shape_errors:
        return "blocked", {**base, "reason": "; ".join(shape_errors)}
    submit_command = ["longbridge", "order", "sell", longbridge_symbol, str(remaining_quantity), "--order-type", normalized_order_type]
    if normalized_order_type in PRICE_REQUIRED_ORDER_TYPES:
        submit_command.extend(["--price", format_decimal(float(resolved_limit_price))])
    if normalized_order_type in TRIGGER_PRICE_REQUIRED_ORDER_TYPES:
        submit_command.extend(["--trigger-price", format_decimal(float(resolved_trigger_price))])
    if normalized_order_type in TRAILING_AMOUNT_REQUIRED_ORDER_TYPES:
        submit_command.extend(["--trailing-amount", format_decimal(float(trailing_amount))])
    if normalized_order_type in TRAILING_PERCENT_REQUIRED_ORDER_TYPES:
        submit_command.extend(["--trailing-percent", format_decimal(float(trailing_percent))])
    if normalized_order_type.startswith("TSLP") and limit_offset is not None:
        submit_command.extend(["--limit-offset", format_decimal(float(limit_offset))])
    submit_command.extend(["--tif", normalized_tif])
    if normalized_tif == "gtd":
        submit_command.extend(["--expire-date", str(expire_date)])
    if outside_rth:
        submit_command.extend(["--outside-rth", str(outside_rth)])
    submit_command.extend(["--remark", remark, "--format", "json"])
    preview_steps = [
        {
            "action": "cancel_existing_stop",
            "command": ["longbridge", "order", "cancel", existing_stop_order_id, "--format", "json"],
        },
        {
            "action": "submit_break_even_stop",
            "command": submit_command,
        },
    ]
    return (
        "candidate",
        {
            **base,
            "preview_steps": preview_steps,
            "reason": "TP1 fill allows stop movement to break-even",
        },
    )


def break_even_stop_record(
    candidate: dict[str, Any],
    *,
    cancel_result: dict[str, Any],
    submit_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "paper_break_even_stop_order",
        "intent_id": candidate["intent_id"],
        "source_signal_id": candidate.get("source_signal_id"),
        "entry_broker_order_id": candidate.get("entry_broker_order_id"),
        "replaces_broker_order_id": candidate.get("existing_stop_order_id"),
        "symbol": candidate.get("symbol"),
        "longbridge_symbol": candidate.get("longbridge_symbol"),
        "side": candidate.get("side"),
        "order_type": candidate.get("order_type"),
        "quantity": candidate.get("remaining_quantity"),
        "limit_price": candidate.get("limit_price"),
        "trigger_price": candidate.get("trigger_price"),
        "trailing_amount": candidate.get("trailing_amount"),
        "trailing_percent": candidate.get("trailing_percent"),
        "limit_offset": candidate.get("limit_offset"),
        "tif": candidate.get("tif"),
        "expire_date": candidate.get("expire_date"),
        "outside_rth": candidate.get("outside_rth"),
        "buffer_pct": candidate.get("buffer_pct"),
        "remark": candidate.get("remark"),
        "broker": "longbridge",
        "account_channel": submit_result.get("account_channel") or cancel_result.get("account_channel"),
        "broker_order_id": submit_result.get("broker_order_id"),
        "submit_status": "submitted",
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cancel_result": {
            "broker_order_id": cancel_result.get("broker_order_id"),
            "raw_request": cancel_result.get("raw_request") if isinstance(cancel_result.get("raw_request"), dict) else {},
            "raw_response": cancel_result.get("raw_response") if isinstance(cancel_result.get("raw_response"), dict) else {},
        },
        "raw_request": submit_result.get("raw_request") if isinstance(submit_result.get("raw_request"), dict) else {},
        "raw_response": submit_result.get("raw_response") if isinstance(submit_result.get("raw_response"), dict) else {},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.buffer_pct < 0:
        raise ValueError("--buffer-pct must be >= 0")
    repo_root = Path(args.repo_root).resolve()
    state_path = default_state_path(repo_root, args.date, args.state)
    stops_path = default_stops_path(repo_root, args.date, args.stops_journal)
    output = default_output_path(repo_root, args.date, args.output)
    paper_execution_config, paper_execution_config_path = load_paper_execution_config(
        repo_root,
        getattr(args, "paper_execution_config", None),
    )
    state = read_json(state_path)
    orders = state.get("orders")
    if not isinstance(orders, list):
        raise ValueError(f"{state_path}: orders must be an array")
    stop_records = stop_records_by_intent(stops_path)

    move_candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        bucket, record = break_even_candidate_or_block(
            order,
            stop_records=stop_records,
            buffer_pct=args.buffer_pct,
            order_type=args.order_type,
            limit_price=args.limit_price,
            trigger_price=args.trigger_price,
            trailing_amount=args.trailing_amount,
            trailing_percent=args.trailing_percent,
            limit_offset=args.limit_offset,
            tif=args.tif,
            expire_date=args.expire_date,
            outside_rth=args.outside_rth,
        )
        if bucket == "candidate":
            move_candidates.append(record)
        else:
            blocked.append(record)

    if args.execute and move_candidates:
        adapter = LongbridgePaperOrderAdapter(
            cli=args.longbridge_cli,
            paper_execution_config=paper_execution_config,
        )
        for candidate in move_candidates:
            try:
                cancel_result = adapter.cancel_order(
                    str(candidate["existing_stop_order_id"]),
                    execute=True,
                    action="break_even_stop_move",
                )
                submit_result = adapter.submit_protective_stop_order(
                    {
                        **candidate,
                        "quantity": candidate["remaining_quantity"],
                    },
                    execute=True,
                    action="break_even_stop_move",
                )
                record = break_even_stop_record(candidate, cancel_result=cancel_result, submit_result=submit_result)
                append_jsonl(stops_path, record)
                moved.append(record)
            except Exception as exc:
                errors.append({**candidate, "error": str(exc)})

    summary = {
        "total": len([order for order in orders if isinstance(order, dict)]),
        "move_candidates": len(move_candidates),
        "blocked": len(blocked),
        "moved": len(moved),
        "errors": len(errors),
    }
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": not args.execute,
        "execute_requested": bool(args.execute),
        "execute_supported": True,
        "source_execution_state": str(state_path),
        "source_stops_journal": str(stops_path),
        "paper_execution_config": str(paper_execution_config_path),
        "execution_policy": paper_execution_policy(paper_execution_config),
        "broker_capabilities": broker_capability_matrix(paper_execution_config),
        "buffer_pct": args.buffer_pct,
        "stop_order_type": normalize_order_type(args.order_type),
        "tif": normalize_tif(args.tif),
        "expire_date": args.expire_date,
        "outside_rth": args.outside_rth,
        "move_candidates": move_candidates,
        "blocked": blocked,
        "moved": moved,
        "errors": errors,
        "summary": summary,
        "safety_note": (
            "Dry-run break-even stop movement plan only. This workflow does not call broker write APIs."
            if not args.execute
            else "Executed break-even stop movement through guarded cancel + new stop submission."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "dry_run": payload["dry_run"], "summary": summary}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "state": None,
        "stops_journal": None,
        "output": None,
        "buffer_pct": 0.0,
        "order_type": "MIT",
        "limit_price": None,
        "trigger_price": None,
        "trailing_amount": None,
        "trailing_percent": None,
        "limit_offset": None,
        "tif": "gtc",
        "expire_date": None,
        "outside_rth": None,
        "execute": False,
        "longbridge_cli": None,
        "paper_execution_config": None,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or execute a guarded break-even stop movement plan for paper entries")
    parser.add_argument("--date", required=True)
    parser.add_argument("--state")
    parser.add_argument("--stops-journal")
    parser.add_argument("--output")
    parser.add_argument("--buffer-pct", type=float, default=0.0)
    parser.add_argument("--order-type", default="MIT")
    parser.add_argument("--limit-price", type=float)
    parser.add_argument("--trigger-price", type=float)
    parser.add_argument("--trailing-amount", type=float)
    parser.add_argument("--trailing-percent", type=float)
    parser.add_argument("--limit-offset", type=float)
    parser.add_argument("--tif", default="gtc")
    parser.add_argument("--expire-date")
    parser.add_argument("--outside-rth")
    parser.add_argument("--execute", action="store_true", help="Cancel the old stop and submit a new guarded break-even stop")
    parser.add_argument("--longbridge-cli")
    parser.add_argument("--paper-execution-config")
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
