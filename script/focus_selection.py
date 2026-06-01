#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signal_artifacts import read_json, resolve_signals_path


def resolve_path(repo_root: Path, value: str | None, default: Path) -> Path:
    if value:
        path = Path(value)
        return path if path.is_absolute() else repo_root / path
    return default


def output_path(repo_root: Path, date: str, explicit: str | None) -> Path:
    return resolve_path(repo_root, explicit, repo_root / "report" / date / "focus-selection.json")


def relpath(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def snapshot_symbols(payload: dict[str, Any]) -> list[str]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
    result = []
    symbols = snapshot.get("symbols") if isinstance(snapshot, dict) else []
    for item in symbols if isinstance(symbols, list) else []:
        if isinstance(item, dict):
            symbol = normalize_symbol(item.get("symbol"))
            if symbol:
                result.append(symbol)
        elif isinstance(item, str):
            symbol = normalize_symbol(item)
            if symbol:
                result.append(symbol)
    return sorted(dict.fromkeys(result))


def load_optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}


def agent_decision(repo_root: Path, date: str, agents_dir: str | None, symbol: str) -> tuple[Path, dict[str, Any]]:
    base = resolve_path(repo_root, agents_dir, repo_root / "report" / date / "agents")
    path = base / symbol / "decision.json"
    return path, load_optional(path)


def compact_decision(decision: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "rank_score",
        "momentum_score",
        "risk_heat",
        "setup_match",
        "why_focus",
        "why_not_executable",
        "required_intraday_confirmation",
        "decision",
        "status",
    )
    return {field: decision.get(field) for field in fields if decision.get(field) not in (None, "", [])}


def focus_row(repo_root: Path, date: str, agents_dir: str | None, signal: dict[str, Any]) -> dict[str, Any]:
    symbol = normalize_symbol(signal.get("symbol"))
    decision_path, decision = agent_decision(repo_root, date, agents_dir, symbol)
    decision_fields = compact_decision(decision)
    row = {
        "symbol": symbol,
        "plan_type": signal.get("plan_type"),
        "execution_status": signal.get("execution_status"),
        "status": signal.get("status"),
        "setup": signal.get("setup"),
        "trigger": signal.get("trigger"),
        "invalidation": signal.get("invalidation"),
        "risk": signal.get("risk"),
        "notes": signal.get("notes"),
        **decision_fields,
    }
    if decision_path.exists():
        row["agent_decision_path"] = relpath(repo_root, decision_path)
    if not row.get("why_focus"):
        row["why_focus"] = signal.get("notes") or signal.get("setup") or "selected in structured report signals"
    if not row.get("why_not_executable") and row.get("execution_status") != "conditional_executable":
        row["why_not_executable"] = "not marked conditional_executable in validated signal sidecar"
    return {key: value for key, value in row.items() if value not in (None, "", [])}


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    signals_file = resolve_signals_path(repo_root, args.date, args.signals, args.session)
    signals_payload = read_json(signals_file)
    raw_signals = signals_payload.get("signals") if isinstance(signals_payload.get("signals"), list) else []
    selected = [focus_row(repo_root, args.date, args.agents_dir, signal) for signal in raw_signals]
    selected_symbols = [row["symbol"] for row in selected if row.get("symbol")]

    context_default = (
        repo_root / "report" / args.date / "pre-market-context.json"
        if args.session == "pre-market"
        else repo_root / "report" / args.date / "daily-snapshot.json"
    )
    context_file = resolve_path(repo_root, args.context or args.snapshot, context_default)
    context_payload = load_optional(context_file)
    universe_symbols = snapshot_symbols(context_payload)
    nonfocus = [
        {"symbol": symbol, "reason": "not selected in structured report signals"}
        for symbol in universe_symbols
        if symbol not in set(selected_symbols)
    ]

    output = output_path(repo_root, args.date, args.output)
    payload = {
        "schema_version": 1,
        "date": args.date,
        "session": args.session,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_signals": relpath(repo_root, signals_file),
        "source_context": relpath(repo_root, context_file) if context_file.exists() else None,
        "selected": selected,
        "rejected_or_nonfocus": nonfocus[: args.max_nonfocus],
        "summary": {
            "selected": len(selected),
            "conditional_executable": sum(
                1 for row in selected if row.get("execution_status") == "conditional_executable"
            ),
            "watch_only": sum(1 for row in selected if row.get("execution_status") == "watch_only"),
            "no_trade": sum(
                1
                for row in selected
                if row.get("execution_status") == "no_trade" or row.get("plan_type") == "no_trade"
            ),
            "nonfocus": len(nonfocus),
        },
    }
    payload = {key: value for key, value in payload.items() if value not in (None, "", [])}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "session": args.session, "output": str(output), **payload["summary"]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an auditable focus-selection artifact from report signals")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    parser.add_argument("--signals")
    parser.add_argument("--context")
    parser.add_argument("--snapshot")
    parser.add_argument("--agents-dir")
    parser.add_argument("--output")
    parser.add_argument("--max-nonfocus", type=int, default=20)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    try:
        payload = run(build_parser().parse_args())
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
