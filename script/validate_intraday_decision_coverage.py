#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def symbols_from_signals(payload: dict[str, Any]) -> list[str]:
    raw_signals = payload.get("signals")
    if not isinstance(raw_signals, list):
        return []
    symbols: list[str] = []
    for signal in raw_signals:
        if not isinstance(signal, dict):
            continue
        symbol = str(signal.get("symbol") or "").strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def required_symbols_from_context(payload: dict[str, Any]) -> list[str]:
    template = payload.get("sidecar_template")
    if not isinstance(template, dict):
        return []
    return symbols_from_signals(template)


def source_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def validate(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    context_path = resolve_path(
        repo_root,
        args.context,
        repo_root / "report" / args.date / "intraday-opportunity-context.json",
    )
    signals_path = resolve_path(
        repo_root,
        args.signals,
        repo_root / "report" / args.date / "monitor-signals.json",
    )
    context_payload = read_json(context_path)
    signals_payload = read_json(signals_path)
    required_symbols = required_symbols_from_context(context_payload)
    covered_symbols = symbols_from_signals(signals_payload)
    covered = set(covered_symbols)
    missing_symbols = [symbol for symbol in required_symbols if symbol not in covered]
    status = "pass" if not missing_symbols else "fail"
    return {
        "status": status,
        "date": args.date,
        "context": source_path(context_path, repo_root),
        "signals": source_path(signals_path, repo_root),
        "missing_symbols": missing_symbols,
        "summary": {
            "required_symbols": len(required_symbols),
            "covered_symbols": len([symbol for symbol in required_symbols if symbol in covered]),
            "extra_symbols": len([symbol for symbol in covered_symbols if symbol not in set(required_symbols)]),
        },
        "safety_note": "Coverage validation only. This workflow does not judge trade quality or call broker APIs.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Codex intraday sidecar coverage against the full observation universe")
    parser.add_argument("--date", required=True)
    parser.add_argument("--context")
    parser.add_argument("--signals")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        payload = validate(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
