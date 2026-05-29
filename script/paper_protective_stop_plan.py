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


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-protective-stop-plan.json")


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


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.execute:
        raise PermissionError("paper-protective-stop-plan --execute is not implemented until a guarded stop adapter exists")
    repo_root = Path(args.repo_root).resolve()
    state_path = default_state_path(repo_root, args.date, args.state)
    output = default_output_path(repo_root, args.date, args.output)
    state = read_json(state_path)
    orders = state.get("orders")
    if not isinstance(orders, list):
        raise ValueError(f"{state_path}: orders must be an array")

    stop_candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        bucket, record = stop_candidate_or_block(order, tif=args.tif)
        if bucket == "candidate":
            stop_candidates.append(record)
        else:
            blocked.append(record)

    summary = {
        "total": len([order for order in orders if isinstance(order, dict)]),
        "stop_candidates": len(stop_candidates),
        "blocked": len(blocked),
        "submitted": 0,
        "errors": 0,
    }
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": True,
        "execute_requested": False,
        "source_execution_state": str(state_path),
        "stop_order_type": "MIT",
        "tif": args.tif,
        "stop_candidates": stop_candidates,
        "blocked": blocked,
        "submitted": [],
        "errors": [],
        "summary": summary,
        "safety_note": "Dry-run protective stop plan only. This workflow does not call broker write APIs.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "dry_run": True, "summary": summary}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "state": None,
        "output": None,
        "tif": "gtc",
        "execute": False,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a dry-run protective stop plan for filled paper entries")
    parser.add_argument("--date", required=True)
    parser.add_argument("--state")
    parser.add_argument("--output")
    parser.add_argument("--tif", default="gtc")
    parser.add_argument("--execute", action="store_true", help="Reserved; rejected until a guarded paper stop adapter exists")
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
