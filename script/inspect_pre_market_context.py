#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def resolve_path(repo_root: Path, value: str | None, default: Path) -> Path:
    if value:
        path = Path(value)
        return path if path.is_absolute() else repo_root / path
    return default


def relpath(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def symbol_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
    symbols = snapshot.get("symbols") if isinstance(snapshot, dict) else []
    return [item for item in symbols if isinstance(item, dict)]


def provider_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        provider = str(row.get("provider") or row.get("source") or "unknown")
        counts[provider] = counts.get(provider, 0) + 1
    return counts


def latest_dates(rows: list[dict[str, Any]]) -> dict[str, str]:
    result = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        latest = row.get("latest_date") or row.get("last_date") or row.get("as_of")
        bars = row.get("bars")
        if latest is None and isinstance(bars, list) and bars:
            last = bars[-1]
            if isinstance(last, dict):
                latest = last.get("datetime") or last.get("date")
        if symbol and latest:
            result[symbol] = str(latest)
    return result


def focused_symbols(repo_root: Path, date: str) -> list[str]:
    path = repo_root / "report" / date / "pre-market-signals.json"
    if not path.exists():
        return []
    try:
        payload = read_json(path)
    except Exception:
        return []
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    return [str(row.get("symbol") or "").upper() for row in signals if isinstance(row, dict) and row.get("symbol")]


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    context = resolve_path(repo_root, args.context, repo_root / "report" / args.date / "pre-market-context.json")
    payload = read_json(context)
    rows = symbol_rows(payload)
    missing = [str(row.get("symbol") or "").upper() for row in rows if row.get("missing") or row.get("error")]
    fallback = [
        str(row.get("symbol") or "").upper()
        for row in rows
        if row.get("fallback") or row.get("fallback_from") or row.get("primary_error")
    ]
    summary = {
        "status": "success",
        "date": args.date,
        "context": relpath(repo_root, context),
        "report_date": payload.get("report_date") or payload.get("date"),
        "source_snapshot_date": payload.get("source_snapshot_date"),
        "source_snapshot_path": payload.get("source_snapshot_path"),
        "symbol_count": len(rows),
        "symbols": [str(row.get("symbol") or "").upper() for row in rows if row.get("symbol")],
        "focused_symbols": focused_symbols(repo_root, args.date),
        "provider_summary": provider_summary(rows),
        "latest_bar_dates": latest_dates(rows),
        "missing_symbols": [symbol for symbol in missing if symbol],
        "fallback_symbols": [symbol for symbol in fallback if symbol],
        "stale_data": payload.get("stale_data"),
        "errors": payload.get("errors", []),
    }
    if args.output:
        output = resolve_path(repo_root, args.output, repo_root / "report" / args.date / "pre-market-context-inspection.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["output"] = str(output)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect a pre-market context artifact without ad-hoc jq")
    parser.add_argument("--date", required=True)
    parser.add_argument("--context")
    parser.add_argument("--output")
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
