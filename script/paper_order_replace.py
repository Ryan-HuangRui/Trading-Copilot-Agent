#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from longbridge_paper_order_adapter import LongbridgePaperOrderAdapter, format_decimal
from paper_execution_config import broker_capability_matrix, load_paper_execution_config, paper_execution_policy


REPLACEABLE_STATUSES = {"submitted", "accepted", "new", "open", "pending", "queued"}


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def default_state_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-execution-state.json")


def default_decisions_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-replace-decisions.json")


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-replace-plan.json")


def default_replace_journal_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-replace-orders.jsonl")


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.split(".", 1)[0] if "." in text else text


def load_replace_decisions(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path, {"decisions": []})
    decisions = payload.get("decisions")
    return [item for item in decisions if isinstance(item, dict)] if isinstance(decisions, list) else []


def decision_for_order(decisions: list[dict[str, Any]], order: dict[str, Any]) -> dict[str, Any] | None:
    intent_id = str(order.get("intent_id") or "")
    symbol = normalize_symbol(order.get("symbol"))
    for decision in decisions:
        if str(decision.get("intent_id") or "") == intent_id:
            return decision
        if not decision.get("intent_id") and normalize_symbol(decision.get("symbol")) == symbol:
            return decision
    return None


def submitted_replace_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    values: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("intent_id"):
            values.add(str(record["intent_id"]))
    return values


def validate_decision(decision: dict[str, Any] | None, order: dict[str, Any]) -> tuple[bool, str | None]:
    if not decision:
        return False, "replace decision is required"
    if decision.get("action") != "replace_pending":
        return False, "replace decision action must be replace_pending"
    if decision.get("execution_status") != "conditional_executable":
        return False, "replace decision is not conditional_executable"
    if not str(decision.get("reason") or "").strip():
        return False, "replace decision reason is required"
    new_quantity = as_int(decision.get("new_quantity"))
    if new_quantity <= 0:
        return False, "replace decision new_quantity must be > 0"
    filled_quantity = as_int(order.get("filled_quantity"))
    current_quantity = as_int(order.get("quantity"))
    if new_quantity < filled_quantity:
        return False, "replace decision new_quantity cannot be below filled quantity"
    if current_quantity > 0 and new_quantity > current_quantity:
        return False, "replace decision new_quantity cannot exceed current quantity"
    new_limit_price = as_float(decision.get("new_limit_price"))
    if decision.get("new_limit_price") is not None and (new_limit_price is None or new_limit_price <= 0):
        return False, "replace decision new_limit_price must be > 0 when supplied"
    return True, None


def preview_command(order_id: str, quantity: int, limit_price: float | None) -> list[str]:
    command = ["longbridge", "order", "replace", order_id, "--qty", str(quantity)]
    if limit_price is not None:
        command.extend(["--price", format_decimal(limit_price)])
    command.extend(["--format", "json"])
    return command


def replace_candidate_or_block(
    order: dict[str, Any],
    *,
    decisions: list[dict[str, Any]],
    submitted_replace_intent_ids: set[str],
) -> tuple[str, dict[str, Any]]:
    intent_id = str(order.get("intent_id") or "")
    broker_order_id = str(order.get("broker_order_id") or "").strip()
    status = str(order.get("status") or "").strip().lower()
    decision = decision_for_order(decisions, order)
    base = {
        "intent_id": intent_id,
        "source_signal_id": order.get("source_signal_id"),
        "symbol": normalize_symbol(order.get("symbol")),
        "longbridge_symbol": order.get("longbridge_symbol"),
        "broker_order_id": broker_order_id or None,
        "current_status": status,
        "current_quantity": order.get("quantity"),
        "filled_quantity": order.get("filled_quantity"),
        "current_limit_price": order.get("limit_price"),
        "decision_reason": decision.get("reason") if decision else None,
    }
    if intent_id in submitted_replace_intent_ids:
        return "blocked", {**base, "reason": "replace already submitted"}
    if not broker_order_id:
        return "blocked", {**base, "reason": "broker_order_id is required for replace"}
    if status not in REPLACEABLE_STATUSES:
        return "blocked", {**base, "reason": "order status is not replaceable"}
    if as_int(order.get("filled_quantity")) > 0:
        return "blocked", {**base, "reason": "partially filled orders are not replaceable by this workflow"}
    decision_ok, decision_reason = validate_decision(decision, order)
    if not decision_ok:
        return "blocked", {**base, "reason": decision_reason}
    new_quantity = as_int(decision.get("new_quantity"))
    new_limit_price = as_float(decision.get("new_limit_price"))
    return (
        "candidate",
        {
            **base,
            "new_quantity": new_quantity,
            "new_limit_price": new_limit_price,
            "preview_command": preview_command(broker_order_id, new_quantity, new_limit_price),
            "reason": "conditional replace decision for pending paper order",
        },
    )


def replace_record(candidate: dict[str, Any], replace_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "paper_replace_order",
        "intent_id": candidate["intent_id"],
        "source_signal_id": candidate.get("source_signal_id"),
        "symbol": candidate.get("symbol"),
        "longbridge_symbol": candidate.get("longbridge_symbol"),
        "broker": "longbridge",
        "account_channel": replace_result.get("account_channel"),
        "broker_order_id": replace_result.get("broker_order_id") or candidate.get("broker_order_id"),
        "previous_quantity": candidate.get("current_quantity"),
        "new_quantity": candidate.get("new_quantity"),
        "previous_limit_price": candidate.get("current_limit_price"),
        "new_limit_price": candidate.get("new_limit_price"),
        "decision_reason": candidate.get("decision_reason"),
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raw_request": replace_result.get("raw_request") if isinstance(replace_result.get("raw_request"), dict) else {},
        "raw_response": replace_result.get("raw_response") if isinstance(replace_result.get("raw_response"), dict) else {},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    state_path = default_state_path(repo_root, args.date, args.state)
    decisions_path = default_decisions_path(repo_root, args.date, args.decisions)
    output = default_output_path(repo_root, args.date, args.output)
    replace_journal = default_replace_journal_path(repo_root, args.date, args.replace_journal)
    paper_execution_config, paper_execution_config_path = load_paper_execution_config(
        repo_root,
        getattr(args, "paper_execution_config", None),
    )
    state = read_json(state_path)
    orders = state.get("orders")
    if not isinstance(orders, list):
        raise ValueError(f"{state_path}: orders must be an array")
    decisions = load_replace_decisions(decisions_path)
    submitted_ids = submitted_replace_ids(replace_journal)
    replace_candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    replaced: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        bucket, record = replace_candidate_or_block(order, decisions=decisions, submitted_replace_intent_ids=submitted_ids)
        if bucket == "candidate":
            replace_candidates.append(record)
        else:
            blocked.append(record)

    if args.execute and replace_candidates:
        adapter = LongbridgePaperOrderAdapter(cli=args.longbridge_cli, paper_execution_config=paper_execution_config)
        for candidate in replace_candidates:
            try:
                result = adapter.replace_order(
                    str(candidate["broker_order_id"]),
                    quantity=int(candidate["new_quantity"]),
                    limit_price=candidate.get("new_limit_price"),
                    execute=True,
                )
                record = replace_record(candidate, result)
                append_jsonl(replace_journal, record)
                replaced.append(record)
            except Exception as exc:
                errors.append({**candidate, "error": str(exc)})

    summary = {
        "total": len([order for order in orders if isinstance(order, dict)]),
        "replace_candidates": len(replace_candidates),
        "blocked": len(blocked),
        "replaced": len(replaced),
        "errors": len(errors),
    }
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": not args.execute,
        "execute_requested": bool(args.execute),
        "source_execution_state": str(state_path),
        "source_decisions": str(decisions_path) if decisions_path.exists() else None,
        "replace_journal": str(replace_journal),
        "paper_execution_config": str(paper_execution_config_path),
        "execution_policy": paper_execution_policy(paper_execution_config),
        "broker_capabilities": broker_capability_matrix(paper_execution_config),
        "replace_candidates": replace_candidates,
        "blocked": blocked,
        "replaced": replaced,
        "errors": errors,
        "summary": summary,
        "safety_note": (
            "Dry-run pending paper order replace only. This workflow does not call broker write APIs."
            if not args.execute
            else "Executed pending paper order replace through guarded Longbridge paper adapter."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "dry_run": payload["dry_run"], "summary": summary}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "state": None,
        "decisions": None,
        "output": None,
        "replace_journal": None,
        "execute": False,
        "longbridge_cli": None,
        "paper_execution_config": None,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or execute a guarded replace plan for pending paper orders")
    parser.add_argument("--date", required=True)
    parser.add_argument("--state")
    parser.add_argument("--decisions")
    parser.add_argument("--output")
    parser.add_argument("--replace-journal")
    parser.add_argument("--longbridge-cli")
    parser.add_argument("--paper-execution-config")
    parser.add_argument("--execute", action="store_true")
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
