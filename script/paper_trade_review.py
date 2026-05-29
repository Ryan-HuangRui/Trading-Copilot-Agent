#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_append import append_jsonl
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
    return (str(execution.get("symbol") or "").upper(), str(execution.get("side") or "").lower())


def trade_record(date: str, order: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "trade",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": date,
        "symbol": order.get("symbol"),
        "status": "entered",
        "planned_setup": order.get("setup"),
        "entry": as_float(execution.get("price")) or as_float(order.get("entry_price")),
        "stop": as_float(order.get("stop_price")),
        "source_signal_id": order.get("signal_id"),
        "paper_order_id": execution.get("order_id"),
        "paper_quantity": as_float(execution.get("quantity")),
        "paper_side": execution.get("side"),
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
    preview = read_json(preview_path)
    snapshot = read_json(snapshot_path)
    orders = preview.get("orders")
    executions = snapshot.get("executions")
    if not isinstance(orders, list):
        raise ValueError(f"{preview_path}: orders must be an array")
    if not isinstance(executions, list):
        raise ValueError(f"{snapshot_path}: executions must be an array")

    execution_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for execution in executions:
        if isinstance(execution, dict):
            execution_by_key.setdefault(execution_key(execution), []).append(execution)

    reviews = []
    trade_records = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        if order.get("status") != "ready":
            reviews.append({"order": order, "paper_status": "preview_blocked", "execution": None})
            continue
        key = (str(order.get("longbridge_symbol") or "").upper(), str(order.get("side") or "").lower())
        matches = execution_by_key.get(key, [])
        if not matches:
            reviews.append({"order": order, "paper_status": "no_paper_execution", "execution": None})
            continue
        execution = matches[0]
        reviews.append({"order": order, "paper_status": "filled", "execution": execution})
        trade_records.append(trade_record(args.date, order, execution))

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
