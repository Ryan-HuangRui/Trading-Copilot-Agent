#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from longbridge_paper_order_adapter import LongbridgePaperOrderAdapter, format_decimal
from signal_artifacts import read_json


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def default_state_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-execution-state.json")


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-protective-stop-plan.json")


def default_stops_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-stop-orders.jsonl")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def load_submitted_stop_intent_ids(path: Path) -> set[str]:
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


def stop_candidate_or_block(order: dict[str, Any], *, tif: str) -> tuple[str, dict[str, Any]]:
    intent_id = str(order.get("intent_id") or "")
    filled_quantity = as_quantity(order.get("filled_quantity"))
    quantity = filled_quantity or as_quantity(order.get("quantity"))
    stop_price = as_float(order.get("stop_price"))
    longbridge_symbol = str(order.get("longbridge_symbol") or "")
    remark = f"tca-stop:{intent_id}"
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
        "stop_price": stop_price,
        "avg_fill_price": order.get("avg_fill_price"),
        "remark": remark,
    }
    if order.get("stop_order_id") or order.get("protective_stop_order_id"):
        return "blocked", {**base, "reason": "protective stop already exists"}
    if order.get("side") != "buy":
        return "blocked", {**base, "reason": "only long buy entries are supported"}
    if order.get("status") != "filled" or filled_quantity <= 0:
        return "blocked", {**base, "reason": "entry order is not fully filled"}
    if not longbridge_symbol:
        return "blocked", {**base, "reason": "longbridge_symbol is required"}
    if stop_price is None or stop_price <= 0:
        return "blocked", {**base, "reason": "stop_price is required"}
    if quantity <= 0:
        return "blocked", {**base, "reason": "quantity must be > 0"}
    command = [
        "longbridge",
        "order",
        "sell",
        longbridge_symbol,
        str(quantity),
        "--order-type",
        "MIT",
        "--trigger-price",
        format_decimal(stop_price),
        "--tif",
        tif,
        "--remark",
        remark,
        "--format",
        "json",
    ]
    return (
        "candidate",
        {
            **base,
            "side": "sell",
            "order_type": "MIT",
            "trigger_price": stop_price,
            "tif": tif,
            "preview_command": command,
            "reason": "filled long entry requires protective stop",
        },
    )


def stop_order_record(candidate: dict[str, Any], submit_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "paper_stop_order",
        "intent_id": candidate["intent_id"],
        "source_signal_id": candidate.get("source_signal_id"),
        "entry_broker_order_id": candidate.get("entry_broker_order_id"),
        "symbol": candidate.get("symbol"),
        "longbridge_symbol": candidate.get("longbridge_symbol"),
        "side": candidate.get("side"),
        "order_type": candidate.get("order_type"),
        "quantity": candidate.get("quantity"),
        "trigger_price": candidate.get("trigger_price"),
        "tif": candidate.get("tif"),
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
    repo_root = Path(args.repo_root).resolve()
    state_path = default_state_path(repo_root, args.date, args.state)
    output = default_output_path(repo_root, args.date, args.output)
    stops_path = default_stops_path(repo_root, args.date, args.stops_journal)
    submitted_stop_intent_ids = load_submitted_stop_intent_ids(stops_path)
    state = read_json(state_path)
    orders = state.get("orders")
    if not isinstance(orders, list):
        raise ValueError(f"{state_path}: orders must be an array")

    stop_candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    submitted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        bucket, record = stop_candidate_or_block(order, tif=args.tif)
        if bucket == "candidate":
            if record["intent_id"] in submitted_stop_intent_ids:
                blocked.append({**record, "reason": "protective stop already submitted"})
            else:
                stop_candidates.append(record)
        else:
            blocked.append(record)
    if args.execute and stop_candidates:
        adapter = LongbridgePaperOrderAdapter(cli=args.longbridge_cli)
        for candidate in stop_candidates:
            try:
                submit_result = adapter.submit_protective_stop_order(candidate, execute=True)
                record = stop_order_record(candidate, submit_result)
                append_jsonl(stops_path, record)
                submitted_stop_intent_ids.add(candidate["intent_id"])
                submitted.append(record)
            except Exception as exc:
                errors.append({**candidate, "error": str(exc)})

    summary = {
        "total": len([order for order in orders if isinstance(order, dict)]),
        "stop_candidates": len(stop_candidates),
        "blocked": len(blocked),
        "submitted": len(submitted),
        "errors": len(errors),
    }
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": not args.execute,
        "execute_requested": bool(args.execute),
        "source_execution_state": str(state_path),
        "stops_journal": str(stops_path),
        "stop_order_type": "MIT",
        "tif": args.tif,
        "stop_candidates": stop_candidates,
        "blocked": blocked,
        "submitted": submitted,
        "errors": errors,
        "summary": summary,
        "safety_note": (
            "Dry-run protective stop plan only. This workflow does not call broker write APIs."
            if not args.execute
            else "Executed protective stop submissions through guarded Longbridge paper adapter."
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
        "stops_journal": None,
        "tif": "gtc",
        "execute": False,
        "longbridge_cli": None,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or submit guarded protective stops for filled paper entries")
    parser.add_argument("--date", required=True)
    parser.add_argument("--state")
    parser.add_argument("--output")
    parser.add_argument("--stops-journal")
    parser.add_argument("--tif", default="gtc")
    parser.add_argument("--execute", action="store_true", help="Submit passing protective stop candidates through the guarded paper adapter")
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
