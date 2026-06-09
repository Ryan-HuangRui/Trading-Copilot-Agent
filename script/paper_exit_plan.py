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


OPEN_EXIT_STATUSES = {"submitted", "accepted", "partially_filled"}
EXIT_TRIGGER_STATES = {"invalidated"}


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def default_state_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-execution-state.json")


def default_intraday_state_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "intraday" / date / "state.json")


def default_decisions_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-exit-decisions.json")


def default_exits_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-exit-orders.jsonl")


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-exit-plan.json")


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_json(path)


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_quantity(value: Any) -> int:
    numeric = as_float(value)
    return int(numeric or 0)


def normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def submitted_exit_intent_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    for record in load_jsonl_records(path):
        intent_id = str(record.get("intent_id") or "")
        if intent_id:
            ids.add(intent_id)
    return ids


def intraday_symbol_state(intraday_state: dict[str, Any], symbol: str) -> dict[str, Any]:
    symbols = intraday_state.get("symbols")
    if not isinstance(symbols, dict):
        return {}
    direct = symbols.get(symbol)
    if isinstance(direct, dict):
        return direct
    normalized = normalize_symbol(symbol)
    for key, value in symbols.items():
        if normalize_symbol(key) == normalized and isinstance(value, dict):
            return value
    return {}


def load_exit_decisions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = read_json(path)
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError(f"{path}: decisions must be an array")
    return [item for item in raw_decisions if isinstance(item, dict)]


def exit_decision_for_order(decisions: list[dict[str, Any]], order: dict[str, Any]) -> dict[str, Any] | None:
    intent_id = str(order.get("intent_id") or "")
    symbol = normalize_symbol(order.get("symbol"))
    for decision in decisions:
        if intent_id and str(decision.get("intent_id") or "") == intent_id:
            return decision
    for decision in decisions:
        if symbol and normalize_symbol(decision.get("symbol")) == symbol:
            return decision
    return None


def validate_exit_decision(decision: dict[str, Any] | None, *, quantity: int) -> tuple[bool, str | None]:
    if not decision:
        return False, None
    if str(decision.get("action") or "") != "exit_remaining":
        return False, "exit decision action is not exit_remaining"
    if str(decision.get("execution_status") or "") != "conditional_executable":
        return False, "exit decision is not conditional_executable"
    reason = str(decision.get("reason") or "").strip()
    if not reason:
        return False, "exit decision reason is required"
    risk_check = decision.get("risk_check")
    if not isinstance(risk_check, dict):
        return False, "exit decision risk_check is required"
    if risk_check.get("cancel_open_exits_first") is not True:
        return False, "exit decision must require cancel_open_exits_first"
    decision_quantity = as_quantity(risk_check.get("remaining_quantity"))
    if decision_quantity <= 0 or decision_quantity != quantity:
        return False, "exit decision remaining_quantity must match open quantity"
    return True, None


def open_exit_orders(order: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    stop_id = str(order.get("protective_stop_order_id") or order.get("stop_order_id") or "").strip()
    stop_status = str(order.get("stop_status") or order.get("protective_stop_status") or "").lower()
    if stop_id and stop_status in OPEN_EXIT_STATUSES:
        items.append({"kind": "protective_stop", "broker_order_id": stop_id})
    tp_id = str(order.get("take_profit_order_id") or order.get("tp1_order_id") or "").strip()
    tp_status = str(order.get("take_profit_status") or order.get("tp1_status") or "").lower()
    if tp_id and tp_status in OPEN_EXIT_STATUSES:
        items.append({"kind": "take_profit", "broker_order_id": tp_id})
    return items


def remaining_quantity(order: dict[str, Any]) -> int:
    lifecycle = order.get("lifecycle") if isinstance(order.get("lifecycle"), dict) else {}
    value = lifecycle.get("remaining_quantity") or order.get("remaining_quantity")
    if value is not None:
        return as_quantity(value)
    filled = as_quantity(order.get("filled_quantity"))
    tp1 = as_quantity(order.get("tp1_filled_quantity") or order.get("take_profit_filled_quantity"))
    return max(0, filled - tp1)


def build_exit_intent(order: dict[str, Any], *, args: argparse.Namespace, quantity: int) -> dict[str, Any]:
    order_type = normalize_order_type(args.order_type)
    intent_id = str(order.get("intent_id") or "")
    intent = {
        "kind": "paper_exit_order_intent",
        "intent_id": intent_id,
        "idempotency_key": f"{intent_id}:exit",
        "source_signal_id": order.get("source_signal_id"),
        "date": args.date,
        "session": order.get("session") or "paper-lifecycle",
        "symbol": normalize_symbol(order.get("symbol")),
        "longbridge_symbol": str(order.get("longbridge_symbol") or "").upper(),
        "side": "sell",
        "order_type": order_type,
        "quantity": quantity,
        "limit_price": args.limit_price,
        "trigger_price": args.trigger_price,
        "trailing_amount": args.trailing_amount,
        "trailing_percent": args.trailing_percent,
        "limit_offset": args.limit_offset,
        "tif": normalize_tif(args.tif),
        "expire_date": args.expire_date,
        "outside_rth": args.outside_rth,
        "remark": f"tca-exit:{intent_id}",
    }
    errors = validate_order_shape(intent)
    if errors:
        raise ValueError("; ".join(errors))
    return intent


def submit_order_command(intent: dict[str, Any]) -> list[str]:
    order_type = normalize_order_type(intent.get("order_type"))
    tif = normalize_tif(intent.get("tif"))
    command = [
        "longbridge",
        "order",
        str(intent["side"]),
        str(intent["longbridge_symbol"]),
        str(int(intent["quantity"])),
        "--order-type",
        order_type,
    ]
    if order_type in PRICE_REQUIRED_ORDER_TYPES:
        command.extend(["--price", format_decimal(float(intent["limit_price"]))])
    if order_type in TRIGGER_PRICE_REQUIRED_ORDER_TYPES:
        command.extend(["--trigger-price", format_decimal(float(intent["trigger_price"]))])
    if order_type in TRAILING_AMOUNT_REQUIRED_ORDER_TYPES:
        command.extend(["--trailing-amount", format_decimal(float(intent["trailing_amount"]))])
    if order_type in TRAILING_PERCENT_REQUIRED_ORDER_TYPES:
        command.extend(["--trailing-percent", format_decimal(float(intent["trailing_percent"]))])
    if order_type.startswith("TSLP") and intent.get("limit_offset") is not None:
        command.extend(["--limit-offset", format_decimal(float(intent["limit_offset"]))])
    command.extend(["--tif", tif])
    if tif == "gtd":
        command.extend(["--expire-date", str(intent["expire_date"])])
    if intent.get("outside_rth"):
        command.extend(["--outside-rth", str(intent["outside_rth"])])
    command.extend(["--remark", str(intent["remark"]), "--format", "json"])
    return command


def exit_candidate_or_block(
    order: dict[str, Any],
    *,
    intraday_state: dict[str, Any],
    decisions: list[dict[str, Any]],
    submitted_exit_ids: set[str],
    args: argparse.Namespace,
) -> tuple[str, dict[str, Any]]:
    intent_id = str(order.get("intent_id") or "")
    symbol = normalize_symbol(order.get("symbol"))
    state = intraday_symbol_state(intraday_state, symbol)
    intraday_status = str(state.get("state") or "").strip()
    lifecycle = order.get("lifecycle") if isinstance(order.get("lifecycle"), dict) else {}
    overall_status = str(lifecycle.get("overall_status") or "")
    quantity = remaining_quantity(order)
    decision = exit_decision_for_order(decisions, order)
    decision_valid, decision_block_reason = validate_exit_decision(decision, quantity=quantity)
    base = {
        "intent_id": intent_id,
        "source_signal_id": order.get("source_signal_id"),
        "entry_broker_order_id": order.get("broker_order_id"),
        "symbol": symbol,
        "longbridge_symbol": order.get("longbridge_symbol"),
        "entry_status": order.get("status"),
        "entry_side": order.get("side"),
        "lifecycle_status": overall_status,
        "remaining_quantity": quantity,
        "intraday_state": intraday_status or None,
        "intraday_reason": state.get("reason"),
        "bar_timestamp": state.get("bar_timestamp"),
        "decision_action": decision.get("action") if decision else None,
        "decision_execution_status": decision.get("execution_status") if decision else None,
        "decision_reason": decision.get("reason") if decision else None,
    }
    if order.get("side") != "buy":
        return "blocked", {**base, "reason": "only long buy entries are supported"}
    if order.get("status") != "filled":
        return "blocked", {**base, "reason": "entry order is not filled"}
    if overall_status not in {"open_protected", "open_unprotected", "partially_exited"}:
        return "blocked", {**base, "reason": "position lifecycle is not open"}
    if quantity <= 0:
        return "blocked", {**base, "reason": "remaining quantity must be > 0"}
    if intent_id in submitted_exit_ids:
        return "blocked", {**base, "reason": "exit already submitted"}
    trigger_source = None
    if intraday_status in EXIT_TRIGGER_STATES:
        trigger_source = "intraday_plan_invalidated"
    elif decision_valid:
        trigger_source = "llm_exit_decision"
    elif decision_block_reason:
        return "blocked", {**base, "reason": decision_block_reason}
    if not trigger_source:
        return "blocked", {**base, "reason": "intraday state is not an exit trigger"}
    try:
        intent = build_exit_intent(order, args=args, quantity=quantity)
    except ValueError as exc:
        return "blocked", {**base, "reason": str(exc)}
    exits_to_cancel = open_exit_orders(order)
    preview_steps = [
        {
            "action": "cancel_existing_stop" if item["kind"] == "protective_stop" else "cancel_existing_take_profit",
            "command": ["longbridge", "order", "cancel", item["broker_order_id"], "--format", "json"],
        }
        for item in exits_to_cancel
    ]
    preview_steps.append({"action": "submit_exit_order", "command": submit_order_command(intent)})
    return (
        "candidate",
        {
            **base,
            "exit_reason": trigger_source,
            "side": intent["side"],
            "order_type": intent["order_type"],
            "quantity": intent["quantity"],
            "limit_price": intent.get("limit_price"),
            "trigger_price": intent.get("trigger_price"),
            "trailing_amount": intent.get("trailing_amount"),
            "trailing_percent": intent.get("trailing_percent"),
            "limit_offset": intent.get("limit_offset"),
            "tif": intent.get("tif"),
            "remark": intent.get("remark"),
            "open_exit_orders": exits_to_cancel,
            "intent": intent,
            "preview_steps": preview_steps,
            "reason": "intraday tracker invalidated the plan while paper position remains open",
        },
    )


def exit_order_record(
    candidate: dict[str, Any],
    *,
    cancel_results: list[dict[str, Any]],
    submit_result: dict[str, Any],
) -> dict[str, Any]:
    intent = candidate["intent"]
    return {
        "kind": "paper_exit_order",
        "intent_id": candidate["intent_id"],
        "idempotency_key": intent["idempotency_key"],
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
        "tif": candidate.get("tif"),
        "remark": candidate.get("remark"),
        "exit_reason": candidate.get("exit_reason"),
        "broker": "longbridge",
        "account_channel": submit_result.get("account_channel"),
        "broker_order_id": submit_result.get("broker_order_id"),
        "submit_status": "submitted",
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cancel_results": cancel_results,
        "raw_request": submit_result.get("raw_request") if isinstance(submit_result.get("raw_request"), dict) else {},
        "raw_response": submit_result.get("raw_response") if isinstance(submit_result.get("raw_response"), dict) else {},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    state_path = default_state_path(repo_root, args.date, args.state)
    intraday_state_path = default_intraday_state_path(repo_root, args.date, args.intraday_state)
    decisions_path = default_decisions_path(repo_root, args.date, args.decisions)
    exits_path = default_exits_path(repo_root, args.date, args.exits_journal)
    output = default_output_path(repo_root, args.date, args.output)
    paper_execution_config, paper_execution_config_path = load_paper_execution_config(
        repo_root,
        getattr(args, "paper_execution_config", None),
    )
    state = read_json(state_path)
    intraday_state = read_json_if_exists(intraday_state_path)
    decisions = load_exit_decisions(decisions_path)
    orders = state.get("orders")
    if not isinstance(orders, list):
        raise ValueError(f"{state_path}: orders must be an array")
    existing_exit_ids = submitted_exit_intent_ids(exits_path)

    exit_candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    submitted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        bucket, record = exit_candidate_or_block(
            order,
            intraday_state=intraday_state,
            decisions=decisions,
            submitted_exit_ids=existing_exit_ids,
            args=args,
        )
        if bucket == "candidate":
            exit_candidates.append(record)
        else:
            blocked.append(record)

    if args.execute and exit_candidates:
        adapter = LongbridgePaperOrderAdapter(
            cli=args.longbridge_cli,
            paper_execution_config=paper_execution_config,
        )
        for candidate in exit_candidates:
            try:
                cancel_results = []
                for item in candidate.get("open_exit_orders") or []:
                    cancel_results.append(
                        adapter.cancel_order(str(item["broker_order_id"]), execute=True, action="exit_cancel_replace")
                    )
                submit_result = adapter.submit_order(candidate["intent"], execute=True, action="exit_submit")
                record = exit_order_record(candidate, cancel_results=cancel_results, submit_result=submit_result)
                append_jsonl(exits_path, record)
                submitted.append(record)
            except Exception as exc:
                errors.append({**candidate, "error": str(exc)})

    summary = {
        "total": len([order for order in orders if isinstance(order, dict)]),
        "exit_candidates": len(exit_candidates),
        "blocked": len(blocked),
        "submitted": len(submitted),
        "errors": len(errors),
    }
    sanitized_candidates = [{key: value for key, value in item.items() if key != "intent"} for item in exit_candidates]
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": not args.execute,
        "execute_requested": bool(args.execute),
        "source_execution_state": str(state_path),
        "source_intraday_state": str(intraday_state_path),
        "source_decisions": str(decisions_path) if decisions_path.exists() else None,
        "exits_journal": str(exits_path),
        "paper_execution_config": str(paper_execution_config_path),
        "execution_policy": paper_execution_policy(paper_execution_config),
        "broker_capabilities": broker_capability_matrix(paper_execution_config),
        "order_type": normalize_order_type(args.order_type),
        "exit_candidates": sanitized_candidates,
        "blocked": blocked,
        "submitted": submitted,
        "errors": errors,
        "summary": summary,
        "safety_note": (
            "Dry-run plan-invalidated exit only. This workflow does not call broker write APIs."
            if not args.execute
            else "Executed plan-invalidated paper exit through guarded cancel + exit order submission."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "dry_run": payload["dry_run"], "summary": summary}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "state": None,
        "intraday_state": None,
        "decisions": None,
        "output": None,
        "exits_journal": None,
        "order_type": "MO",
        "limit_price": None,
        "trigger_price": None,
        "trailing_amount": None,
        "trailing_percent": None,
        "limit_offset": None,
        "tif": "day",
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
    parser = argparse.ArgumentParser(description="Build or execute a guarded paper exit plan for invalidated intraday plans")
    parser.add_argument("--date", required=True)
    parser.add_argument("--state")
    parser.add_argument("--intraday-state")
    parser.add_argument("--decisions")
    parser.add_argument("--output")
    parser.add_argument("--exits-journal")
    parser.add_argument("--order-type", default="MO")
    parser.add_argument("--limit-price", type=float)
    parser.add_argument("--trigger-price", type=float)
    parser.add_argument("--trailing-amount", type=float)
    parser.add_argument("--trailing-percent", type=float)
    parser.add_argument("--limit-offset", type=float)
    parser.add_argument("--tif", default="day")
    parser.add_argument("--expire-date")
    parser.add_argument("--outside-rth")
    parser.add_argument("--execute", action="store_true", help="Submit passing exit candidates through the guarded paper adapter")
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
