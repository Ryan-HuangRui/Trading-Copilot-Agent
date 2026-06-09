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


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-take-profit-plan.json")


def default_take_profit_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-take-profit-orders.jsonl")


def default_stops_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-stop-orders.jsonl")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def load_submitted_take_profit_intent_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    intent_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        intent_id = record.get("intent_id")
        if intent_id:
            intent_ids.add(str(intent_id))
    return intent_ids


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_quantity(value: Any) -> int:
    numeric = as_float(value)
    return int(numeric or 0)


def partial_quantity(filled_quantity: int, exit_fraction: float) -> int:
    if filled_quantity <= 0:
        return 0
    return max(1, min(filled_quantity, int(filled_quantity * exit_fraction)))


def active_status(value: Any) -> bool:
    return str(value or "").lower() in {"submitted", "accepted", "partially_filled"}


def active_stop_over_exit_risk(order: dict[str, Any], *, post_tp_remaining_quantity: int) -> bool:
    if not active_status(order.get("stop_status") or order.get("protective_stop_status")):
        return False
    if not (order.get("protective_stop_order_id") or order.get("stop_order_id")):
        return False
    stop_quantity = as_quantity(order.get("protective_stop_quantity") or order.get("stop_quantity"))
    if stop_quantity <= 0:
        stop_quantity = as_quantity(order.get("filled_quantity") or order.get("quantity"))
    return stop_quantity > post_tp_remaining_quantity


def stop_resize_record(candidate: dict[str, Any], *, cancel_result: dict[str, Any], submit_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "paper_resized_stop_order",
        "intent_id": candidate["intent_id"],
        "source_signal_id": candidate.get("source_signal_id"),
        "entry_broker_order_id": candidate.get("entry_broker_order_id"),
        "replaces_broker_order_id": candidate.get("protective_stop_order_id"),
        "symbol": candidate.get("symbol"),
        "longbridge_symbol": candidate.get("longbridge_symbol"),
        "side": "sell",
        "order_type": "MIT",
        "quantity": candidate.get("post_tp_remaining_quantity"),
        "trigger_price": candidate.get("stop_resize_trigger_price"),
        "tif": candidate.get("tif"),
        "remark": f"tca-resize-stop:{candidate['intent_id']}",
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


def take_profit_candidate_or_block(
    order: dict[str, Any],
    *,
    exit_fraction: float,
    order_type: str,
    limit_price: float | None,
    trigger_price: float | None,
    trailing_amount: float | None,
    trailing_percent: float | None,
    limit_offset: float | None,
    tif: str,
    expire_date: str | None,
    outside_rth: str | None,
    resize_stop_before_submit: bool,
) -> tuple[str, dict[str, Any]]:
    intent_id = str(order.get("intent_id") or "")
    filled_quantity = as_quantity(order.get("filled_quantity"))
    quantity = partial_quantity(filled_quantity, exit_fraction)
    post_tp_remaining_quantity = max(0, filled_quantity - quantity)
    take_profit_price = as_float(order.get("take_profit"))
    stop_resize_trigger_price = as_float(order.get("current_stop_price") or order.get("stop_price"))
    longbridge_symbol = str(order.get("longbridge_symbol") or "")
    normalized_order_type = normalize_order_type(order_type)
    normalized_tif = normalize_tif(tif)
    resolved_limit_price = limit_price
    if resolved_limit_price is None and normalized_order_type in PRICE_REQUIRED_ORDER_TYPES:
        resolved_limit_price = take_profit_price
    resolved_trigger_price = trigger_price
    if resolved_trigger_price is None and normalized_order_type in TRIGGER_PRICE_REQUIRED_ORDER_TYPES:
        resolved_trigger_price = take_profit_price
    remark = f"tca-tp1:{intent_id}"
    base = {
        "intent_id": intent_id,
        "source_signal_id": order.get("source_signal_id"),
        "entry_broker_order_id": order.get("broker_order_id"),
        "symbol": order.get("symbol"),
        "longbridge_symbol": longbridge_symbol,
        "entry_status": order.get("status"),
        "entry_side": order.get("side"),
        "filled_quantity": filled_quantity,
        "quantity": quantity,
        "post_tp_remaining_quantity": post_tp_remaining_quantity,
        "protective_stop_order_id": order.get("protective_stop_order_id") or order.get("stop_order_id"),
        "stop_status": order.get("stop_status") or order.get("protective_stop_status"),
        "protective_stop_quantity": as_quantity(order.get("protective_stop_quantity") or order.get("stop_quantity")),
        "stop_resize_trigger_price": stop_resize_trigger_price,
        "take_profit": take_profit_price,
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
        "avg_fill_price": order.get("avg_fill_price"),
        "exit_fraction": exit_fraction,
        "remark": remark,
    }
    if order.get("take_profit_order_id") or order.get("tp1_order_id"):
        return "blocked", {**base, "reason": "take-profit order already exists"}
    if order.get("side") != "buy":
        return "blocked", {**base, "reason": "only long buy entries are supported"}
    if order.get("status") != "filled" or filled_quantity <= 0:
        return "blocked", {**base, "reason": "entry order is not fully filled"}
    if not longbridge_symbol:
        return "blocked", {**base, "reason": "longbridge_symbol is required"}
    if take_profit_price is None or take_profit_price <= 0:
        return "blocked", {**base, "reason": "take_profit is required"}
    if quantity <= 0:
        return "blocked", {**base, "reason": "quantity must be > 0"}
    shape_errors = validate_order_shape(
        {
            "side": "sell",
            "longbridge_symbol": longbridge_symbol,
            "quantity": quantity,
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
    stop_resize_required = active_stop_over_exit_risk(order, post_tp_remaining_quantity=post_tp_remaining_quantity)
    if stop_resize_required and not resize_stop_before_submit:
        return "blocked", {**base, "reason": "active protective stop quantity exceeds post-TP1 remaining quantity"}
    if stop_resize_required and (post_tp_remaining_quantity <= 0 or stop_resize_trigger_price is None or stop_resize_trigger_price <= 0):
        return "blocked", {**base, "reason": "valid resized stop quantity and trigger price are required"}
    resize_preview_steps = []
    if stop_resize_required:
        resize_preview_steps = [
            {
                "action": "cancel_existing_stop",
                "command": ["longbridge", "order", "cancel", str(base["protective_stop_order_id"]), "--format", "json"],
            },
            {
                "action": "submit_resized_stop",
                "command": [
                    "longbridge",
                    "order",
                    "sell",
                    longbridge_symbol,
                    str(post_tp_remaining_quantity),
                    "--order-type",
                    "MIT",
                    "--trigger-price",
                    format_decimal(stop_resize_trigger_price),
                    "--tif",
                    normalized_tif,
                    "--remark",
                    f"tca-resize-stop:{intent_id}",
                    "--format",
                    "json",
                ],
            },
        ]
    command = [
        "longbridge",
        "order",
        "sell",
        longbridge_symbol,
        str(quantity),
    ]
    if normalized_order_type in PRICE_REQUIRED_ORDER_TYPES:
        command.extend(["--price", format_decimal(float(resolved_limit_price))])
    command.extend(["--order-type", normalized_order_type])
    if normalized_order_type in TRIGGER_PRICE_REQUIRED_ORDER_TYPES:
        command.extend(["--trigger-price", format_decimal(float(resolved_trigger_price))])
    if normalized_order_type in TRAILING_AMOUNT_REQUIRED_ORDER_TYPES:
        command.extend(["--trailing-amount", format_decimal(float(trailing_amount))])
    if normalized_order_type in TRAILING_PERCENT_REQUIRED_ORDER_TYPES:
        command.extend(["--trailing-percent", format_decimal(float(trailing_percent))])
    if normalized_order_type.startswith("TSLP") and limit_offset is not None:
        command.extend(["--limit-offset", format_decimal(float(limit_offset))])
    command.extend(
        [
            "--tif",
            normalized_tif,
        ]
    )
    if normalized_tif == "gtd":
        command.extend(["--expire-date", str(expire_date)])
    if outside_rth:
        command.extend(["--outside-rth", str(outside_rth)])
    command.extend(
        [
        "--remark",
        remark,
        "--format",
        "json",
        ]
    )
    return (
        "candidate",
        {
            **base,
            "requires_stop_resize": stop_resize_required,
            "resize_preview_steps": resize_preview_steps,
            "preview_command": command,
            "reason": "filled long entry has TP1 partial exit",
        },
    )


def take_profit_order_record(candidate: dict[str, Any], submit_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "paper_take_profit_order",
        "intent_id": candidate["intent_id"],
        "source_signal_id": candidate.get("source_signal_id"),
        "entry_broker_order_id": candidate.get("entry_broker_order_id"),
        "symbol": candidate.get("symbol"),
        "longbridge_symbol": candidate.get("longbridge_symbol"),
        "side": candidate.get("side"),
        "order_type": candidate.get("order_type"),
        "quantity": candidate.get("quantity"),
        "limit_price": candidate.get("limit_price"),
        "trigger_price": candidate.get("trigger_price"),
        "trailing_amount": candidate.get("trailing_amount"),
        "trailing_percent": candidate.get("trailing_percent"),
        "limit_offset": candidate.get("limit_offset"),
        "take_profit": candidate.get("take_profit"),
        "exit_fraction": candidate.get("exit_fraction"),
        "tif": candidate.get("tif"),
        "expire_date": candidate.get("expire_date"),
        "outside_rth": candidate.get("outside_rth"),
        "remark": candidate.get("remark"),
        "broker": "longbridge",
        "account_channel": submit_result.get("account_channel"),
        "broker_order_id": submit_result.get("broker_order_id"),
        "submit_status": "submitted",
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raw_request": submit_result.get("raw_request") if isinstance(submit_result.get("raw_request"), dict) else {},
        "raw_response": submit_result.get("raw_response") if isinstance(submit_result.get("raw_response"), dict) else {},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.exit_fraction <= 0 or args.exit_fraction > 1:
        raise ValueError("--exit-fraction must be > 0 and <= 1")
    repo_root = Path(args.repo_root).resolve()
    state_path = default_state_path(repo_root, args.date, args.state)
    output = default_output_path(repo_root, args.date, args.output)
    take_profit_path = default_take_profit_path(repo_root, args.date, args.take_profit_journal)
    stops_path = default_stops_path(repo_root, args.date, args.stops_journal)
    paper_execution_config, paper_execution_config_path = load_paper_execution_config(
        repo_root,
        getattr(args, "paper_execution_config", None),
    )
    submitted_take_profit_intent_ids = load_submitted_take_profit_intent_ids(take_profit_path)
    state = read_json(state_path)
    orders = state.get("orders")
    if not isinstance(orders, list):
        raise ValueError(f"{state_path}: orders must be an array")

    take_profit_candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    resized_stops: list[dict[str, Any]] = []
    submitted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        bucket, record = take_profit_candidate_or_block(
            order,
            exit_fraction=args.exit_fraction,
            order_type=args.order_type,
            limit_price=args.limit_price,
            trigger_price=args.trigger_price,
            trailing_amount=args.trailing_amount,
            trailing_percent=args.trailing_percent,
            limit_offset=args.limit_offset,
            tif=args.tif,
            expire_date=args.expire_date,
            outside_rth=args.outside_rth,
            resize_stop_before_submit=args.resize_stop_before_submit,
        )
        if bucket == "candidate":
            if record["intent_id"] in submitted_take_profit_intent_ids:
                blocked.append({**record, "reason": "take-profit already submitted"})
            else:
                take_profit_candidates.append(record)
        else:
            blocked.append(record)

    if args.execute and take_profit_candidates:
        adapter = LongbridgePaperOrderAdapter(
            cli=args.longbridge_cli,
            paper_execution_config=paper_execution_config,
        )
        for candidate in take_profit_candidates:
            try:
                if candidate.get("requires_stop_resize"):
                    cancel_result = adapter.cancel_order(
                        str(candidate["protective_stop_order_id"]),
                        execute=True,
                        action="take_profit_stop_resize",
                    )
                    stop_intent = {
                        **candidate,
                        "quantity": candidate["post_tp_remaining_quantity"],
                        "order_type": "MIT",
                        "limit_price": None,
                        "trigger_price": candidate["stop_resize_trigger_price"],
                        "trailing_amount": None,
                        "trailing_percent": None,
                        "limit_offset": None,
                        "remark": f"tca-resize-stop:{candidate['intent_id']}",
                    }
                    stop_result = adapter.submit_protective_stop_order(
                        stop_intent,
                        execute=True,
                        action="take_profit_stop_resize",
                    )
                    resized_stop = stop_resize_record(candidate, cancel_result=cancel_result, submit_result=stop_result)
                    append_jsonl(stops_path, resized_stop)
                    resized_stops.append(resized_stop)
                submit_result = adapter.submit_take_profit_order(candidate, execute=True)
                record = take_profit_order_record(candidate, submit_result)
                append_jsonl(take_profit_path, record)
                submitted_take_profit_intent_ids.add(candidate["intent_id"])
                submitted.append(record)
            except Exception as exc:
                errors.append({**candidate, "error": str(exc)})

    summary = {
        "total": len([order for order in orders if isinstance(order, dict)]),
        "take_profit_candidates": len(take_profit_candidates),
        "blocked": len(blocked),
        "resized_stops": len(resized_stops),
        "submitted": len(submitted),
        "errors": len(errors),
    }
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": not args.execute,
        "execute_requested": bool(args.execute),
        "source_execution_state": str(state_path),
        "take_profit_journal": str(take_profit_path),
        "stops_journal": str(stops_path),
        "paper_execution_config": str(paper_execution_config_path),
        "execution_policy": paper_execution_policy(paper_execution_config),
        "broker_capabilities": broker_capability_matrix(paper_execution_config),
        "take_profit_order_type": normalize_order_type(args.order_type),
        "exit_fraction": args.exit_fraction,
        "tif": normalize_tif(args.tif),
        "expire_date": args.expire_date,
        "outside_rth": args.outside_rth,
        "take_profit_candidates": take_profit_candidates,
        "blocked": blocked,
        "resized_stops": resized_stops,
        "submitted": submitted,
        "errors": errors,
        "summary": summary,
        "safety_note": (
            "Dry-run TP1 partial exit plan only. This workflow does not call broker write APIs."
            if not args.execute
            else "Executed TP1 partial exit submissions through guarded Longbridge paper adapter."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "dry_run": payload["dry_run"], "summary": summary}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "state": None,
        "output": None,
        "take_profit_journal": None,
        "stops_journal": None,
        "exit_fraction": 0.5,
        "order_type": "LO",
        "limit_price": None,
        "trigger_price": None,
        "trailing_amount": None,
        "trailing_percent": None,
        "limit_offset": None,
        "tif": "gtc",
        "expire_date": None,
        "outside_rth": None,
        "resize_stop_before_submit": False,
        "execute": False,
        "longbridge_cli": None,
        "paper_execution_config": None,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or submit guarded TP1 partial exits for filled paper entries")
    parser.add_argument("--date", required=True)
    parser.add_argument("--state")
    parser.add_argument("--output")
    parser.add_argument("--take-profit-journal")
    parser.add_argument("--stops-journal")
    parser.add_argument("--exit-fraction", type=float, default=0.5)
    parser.add_argument("--order-type", default="LO")
    parser.add_argument("--limit-price", type=float)
    parser.add_argument("--trigger-price", type=float)
    parser.add_argument("--trailing-amount", type=float)
    parser.add_argument("--trailing-percent", type=float)
    parser.add_argument("--limit-offset", type=float)
    parser.add_argument("--tif", default="gtc")
    parser.add_argument("--expire-date")
    parser.add_argument("--outside-rth")
    parser.add_argument("--resize-stop-before-submit", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Submit passing TP1 candidates through the guarded paper adapter")
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
