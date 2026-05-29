#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from longbridge_paper_order_adapter import LongbridgePaperOrderAdapter
from signal_artifacts import read_json


CANCELLABLE_STATUSES = {"submitted", "accepted"}


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def default_state_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-execution-state.json")


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-order-cancel-plan.json")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def now_utc(explicit_now: str | None) -> datetime:
    parsed = parse_timestamp(explicit_now)
    if parsed:
        return parsed
    return datetime.now(timezone.utc)


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def open_minutes(order: dict[str, Any], now: datetime) -> int | None:
    submitted_at = parse_timestamp(str(order.get("submitted_at") or ""))
    if not submitted_at:
        return None
    delta = now - submitted_at
    return int(delta.total_seconds() // 60)


def cancel_candidate_or_block(order: dict[str, Any], *, now: datetime, expire_after_minutes: int) -> tuple[str, dict[str, Any]]:
    intent_id = str(order.get("intent_id") or "")
    broker_order_id = str(order.get("broker_order_id") or "")
    status = str(order.get("status") or "").lower()
    filled_quantity = as_float(order.get("filled_quantity"))
    age_minutes = open_minutes(order, now)
    base = {
        "intent_id": intent_id,
        "source_signal_id": order.get("source_signal_id"),
        "symbol": order.get("symbol"),
        "longbridge_symbol": order.get("longbridge_symbol"),
        "side": order.get("side"),
        "order_type": order.get("order_type"),
        "quantity": order.get("quantity"),
        "broker_order_id": broker_order_id or None,
        "status": status,
        "filled_quantity": filled_quantity,
        "submitted_at": order.get("submitted_at"),
        "open_minutes": age_minutes,
    }
    if status == "filled":
        return "blocked", {**base, "reason": "order status is not cancellable"}
    if filled_quantity > 0:
        return "blocked", {**base, "reason": "order has fills"}
    if status not in CANCELLABLE_STATUSES:
        return "blocked", {**base, "reason": "order status is not cancellable"}
    if not broker_order_id:
        return "blocked", {**base, "reason": "broker_order_id is required for cancel"}
    if age_minutes is None:
        return "blocked", {**base, "reason": "submitted_at is required for cancel expiry check"}
    if age_minutes < expire_after_minutes:
        return "blocked", {**base, "reason": "entry order has not exceeded max open duration"}
    return "candidate", {**base, "reason": "entry order exceeded max open duration", "cancel_action": "cancel_entry_order"}


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    state_path = default_state_path(repo_root, args.date, args.state)
    output = default_output_path(repo_root, args.date, args.output)
    state = read_json(state_path)
    orders = state.get("orders")
    if not isinstance(orders, list):
        raise ValueError(f"{state_path}: orders must be an array")
    current_time = now_utc(args.now)
    cancel_candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    executed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        bucket, record = cancel_candidate_or_block(
            order,
            now=current_time,
            expire_after_minutes=args.expire_after_minutes,
        )
        if bucket == "candidate":
            cancel_candidates.append(record)
        else:
            blocked.append(record)
    if args.execute and cancel_candidates:
        adapter = LongbridgePaperOrderAdapter(cli=args.longbridge_cli)
        for candidate in cancel_candidates:
            try:
                cancel_result = adapter.cancel_order(str(candidate["broker_order_id"]), execute=True)
                executed.append(
                    {
                        **candidate,
                        "cancel_status": "cancelled",
                        "account_channel": cancel_result.get("account_channel"),
                        "raw_request": cancel_result.get("raw_request") if isinstance(cancel_result.get("raw_request"), dict) else {},
                        "raw_response": cancel_result.get("raw_response") if isinstance(cancel_result.get("raw_response"), dict) else {},
                    }
                )
            except Exception as exc:
                errors.append({**candidate, "error": str(exc)})
    summary = {
        "total": len([order for order in orders if isinstance(order, dict)]),
        "cancel_candidates": len(cancel_candidates),
        "blocked": len(blocked),
        "executed": len(executed),
        "errors": len(errors),
    }
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": not args.execute,
        "execute_requested": bool(args.execute),
        "source_execution_state": str(state_path),
        "expire_after_minutes": args.expire_after_minutes,
        "now": current_time.isoformat(timespec="seconds"),
        "cancel_candidates": cancel_candidates,
        "blocked": blocked,
        "executed": executed,
        "errors": errors,
        "summary": summary,
        "safety_note": (
            "Dry-run cancel plan only. This workflow does not call broker cancel APIs."
            if not args.execute
            else "Executed paper cancellations through guarded Longbridge paper adapter."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "success",
        "date": args.date,
        "output": str(output),
        "dry_run": payload["dry_run"],
        "summary": summary,
    }


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "state": None,
        "output": None,
        "expire_after_minutes": 60,
        "now": None,
        "execute": False,
        "longbridge_cli": None,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a dry-run cancel plan for expired unfilled paper entry orders")
    parser.add_argument("--date", required=True)
    parser.add_argument("--state")
    parser.add_argument("--output")
    parser.add_argument("--expire-after-minutes", type=int, default=60)
    parser.add_argument("--now")
    parser.add_argument("--execute", action="store_true", help="Cancel passing candidates through the guarded paper adapter")
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
