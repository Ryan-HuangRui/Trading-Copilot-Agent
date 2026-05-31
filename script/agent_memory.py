#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENTRY_PREFIX = "<!-- trading-memory-entry "
ENTRY_SUFFIX = " -->"


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected top-level object")
    return data


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else candidate.resolve()


def load_entries(memory_path: Path) -> list[dict[str, Any]]:
    if not memory_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in memory_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith(ENTRY_PREFIX) or not line.endswith(ENTRY_SUFFIX):
            continue
        raw = line[len(ENTRY_PREFIX) : -len(ENTRY_SUFFIX)]
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def entry_from_decision(decision: dict[str, Any], outcome_status: str, reflection: str) -> dict[str, Any]:
    return {
        "date": decision.get("date"),
        "symbol": decision.get("symbol"),
        "decision_id": decision.get("decision_id"),
        "evidence_ids": decision.get("evidence_ids", []),
        "decision_label": decision.get("decision_label"),
        "plan_type": decision.get("plan_type"),
        "execution_status": decision.get("execution_status"),
        "outcome_status": outcome_status,
        "reflection": reflection,
        "created_at": now_utc(),
        "memory_policy": "can_lower_confidence_or_trigger_review_only",
    }


def append_block(entry: dict[str, Any]) -> str:
    raw = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    return "\n".join(
        [
            f"{ENTRY_PREFIX}{raw}{ENTRY_SUFFIX}",
            f"## {entry.get('date')} {entry.get('symbol')} {entry.get('decision_label')}",
            "",
            f"- decision_id: {entry.get('decision_id')}",
            f"- evidence_ids: {', '.join(entry.get('evidence_ids') or [])}",
            f"- outcome_status: {entry.get('outcome_status')}",
            f"- reflection: {entry.get('reflection')}",
            "- policy: memory can lower confidence or trigger review only; it cannot raise execution status.",
            "",
        ]
    )


def append_memory(args: argparse.Namespace) -> dict[str, Any]:
    decision = read_json(resolve_path(args.decision))
    memory_path = resolve_path(args.memory_path)
    entry = entry_from_decision(decision, args.outcome_status, args.reflection)
    decision_id = str(entry.get("decision_id") or "")
    if not decision_id:
        raise ValueError("decision.decision_id is required")
    existing = {str(item.get("decision_id")) for item in load_entries(memory_path)}
    if decision_id in existing:
        return {
            "status": "success",
            "memory_path": str(memory_path),
            "decision_id": decision_id,
            "appended": False,
            "reason": "duplicate_decision_id",
        }
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    header = "# Trading Memory\n\n" if not memory_path.exists() else ""
    with memory_path.open("a", encoding="utf-8") as handle:
        if header:
            handle.write(header)
        handle.write(append_block(entry))
    return {
        "status": "success",
        "memory_path": str(memory_path),
        "decision_id": decision_id,
        "appended": True,
        "reason": None,
    }


def normalize_symbols(symbols: list[str] | None) -> set[str]:
    return {str(symbol).strip().upper() for symbol in symbols or [] if str(symbol).strip()}


def review_memory(args: argparse.Namespace) -> dict[str, Any]:
    memory_path = resolve_path(args.memory_path)
    symbols = normalize_symbols(args.symbol)
    matches = []
    for entry in load_entries(memory_path):
        if args.date and entry.get("date") != args.date:
            continue
        if symbols and str(entry.get("symbol") or "").upper() not in symbols:
            continue
        matches.append(entry)
    payload = {
        "status": "success",
        "date": args.date,
        "symbols": sorted(symbols),
        "memory_path": str(memory_path),
        "read_only": True,
        "execution_status_effect": "cannot_raise",
        "memory_matches": matches,
        "summary": {"matches": len(matches)},
    }
    if args.output:
        output = resolve_path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["output"] = str(output)
    return payload


def export_sqlite(args: argparse.Namespace) -> dict[str, Any]:
    memory_path = resolve_path(args.memory_path)
    sqlite_path = resolve_path(args.sqlite_output)
    entries = load_entries(memory_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(sqlite_path) as conn:
        conn.execute(
            """
            create table if not exists memories (
                decision_id text primary key,
                date text,
                symbol text,
                decision_label text,
                outcome_status text,
                execution_status text,
                reflection text,
                raw_json text not null
            )
            """
        )
        conn.execute("delete from memories")
        for entry in entries:
            conn.execute(
                """
                insert into memories (
                    decision_id, date, symbol, decision_label, outcome_status,
                    execution_status, reflection, raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.get("decision_id"),
                    entry.get("date"),
                    entry.get("symbol"),
                    entry.get("decision_label"),
                    entry.get("outcome_status"),
                    entry.get("execution_status"),
                    entry.get("reflection"),
                    json.dumps(entry, ensure_ascii=False, sort_keys=True),
                ),
            )
    return {
        "status": "success",
        "memory_path": str(memory_path),
        "sqlite_output": str(sqlite_path),
        "rows": len(entries),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append, review, and export agent trading memory")
    sub = parser.add_subparsers(dest="command", required=True)

    append = sub.add_parser("append")
    append.add_argument("--decision", required=True)
    append.add_argument("--memory-path", default="runtime/memory/trading_memory.md")
    append.add_argument("--outcome-status", default="unknown")
    append.add_argument("--reflection", default="")
    append.set_defaults(func=append_memory)

    review = sub.add_parser("review")
    review.add_argument("--memory-path", default="runtime/memory/trading_memory.md")
    review.add_argument("--date")
    review.add_argument("--symbol", action="append", default=[])
    review.add_argument("--output")
    review.set_defaults(func=review_memory)

    export = sub.add_parser("export")
    export.add_argument("--memory-path", default="runtime/memory/trading_memory.md")
    export.add_argument("--sqlite-output", default="runtime/memory/trading_memory.sqlite")
    export.set_defaults(func=export_sqlite)
    return parser


def main() -> None:
    try:
        payload = build_parser().parse_args()
        result = payload.func(payload)
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
