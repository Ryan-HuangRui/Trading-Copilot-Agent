#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected top-level object")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_symbols(symbols: list[str] | None) -> list[str]:
    seen = set()
    result: list[str] = []
    for symbol in symbols or []:
        value = str(symbol or "").strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def default_snapshot_path(repo_root: Path, date: str) -> Path:
    return repo_root / "report" / date / "daily-snapshot.json"


def default_output_path(repo_root: Path, date: str) -> Path:
    return repo_root / "report" / date / "agents" / "market-data.json"


def resolve_path(repo_root: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root / candidate


def load_source_payload(repo_root: Path, args: argparse.Namespace) -> tuple[dict[str, Any], Path, str]:
    if args.context:
        context_path = resolve_path(repo_root, args.context)
        context = read_json(context_path)
        snapshot = context.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError(f"{context_path}: missing snapshot object")
        return snapshot, context_path, "pre_market_context"
    snapshot_path = resolve_path(repo_root, args.snapshot) if args.snapshot else default_snapshot_path(repo_root, args.date)
    return read_json(snapshot_path), snapshot_path, "daily_snapshot"


def symbol_index(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in snapshot.get("symbols", []):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol:
            result[symbol] = item
    return result


def provider_confidence(symbol_payload: dict[str, Any]) -> float:
    meta = symbol_payload.get("meta") if isinstance(symbol_payload.get("meta"), dict) else {}
    provider = str(meta.get("provider") or "").lower()
    if meta.get("fallback_from"):
        return 0.8
    if provider == "longbridge":
        return 0.95
    if provider in {"twelve_data", "twelve"}:
        return 0.85
    return 0.7


def evidence_for_symbol(
    *,
    date: str,
    source_path: Path,
    source_kind: str,
    symbol: str,
    symbol_payload: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    latest = symbol_payload.get("latest") if isinstance(symbol_payload.get("latest"), dict) else {}
    metrics = symbol_payload.get("metrics") if isinstance(symbol_payload.get("metrics"), dict) else {}
    meta = symbol_payload.get("meta") if isinstance(symbol_payload.get("meta"), dict) else {}
    latest_date = str(latest.get("datetime") or snapshot.get("snapshot_date") or date)
    latest_close = latest.get("close")
    limitations: list[str] = []
    if snapshot.get("stale_data"):
        limitations.append(str(snapshot.get("stale_reason") or "snapshot marked stale"))
    if meta.get("fallback_from"):
        limitations.append(f"fallback_from={meta.get('fallback_from')}")
    if not latest:
        limitations.append("missing latest bar")
    return {
        "evidence_id": f"{date}:market_data:{symbol}:{latest_date}",
        "source": str(source_path),
        "source_type": "market_data",
        "as_of": latest_date,
        "symbol": symbol,
        "summary": f"{symbol} latest close {latest_close}; close_delta_pct={metrics.get('close_delta_pct')}",
        "confidence": provider_confidence(symbol_payload),
        "limitations": limitations,
        "metadata": {
            "source_kind": source_kind,
            "provider": meta.get("provider"),
            "fallback_from": meta.get("fallback_from"),
            "sources": symbol_payload.get("sources", []),
        },
    }


def build_market_data_payload(
    *,
    date: str,
    symbols: list[str],
    source_payload: dict[str, Any],
    source_path: Path,
    source_kind: str,
) -> dict[str, Any]:
    normalized = normalize_symbols(symbols)
    indexed = symbol_index(source_payload)
    selected: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    missing: list[str] = []
    for symbol in normalized:
        item = indexed.get(symbol)
        if item is None:
            missing.append(symbol)
            continue
        selected[symbol] = item
        evidence.append(
            evidence_for_symbol(
                date=date,
                source_path=source_path,
                source_kind=source_kind,
                symbol=symbol,
                symbol_payload=item,
                snapshot=source_payload,
            )
        )
    return {
        "schema_version": 1,
        "tool": "agent_market_data",
        "date": date,
        "generated_at": now_utc(),
        "source_kind": source_kind,
        "source_path": str(source_path),
        "symbols": normalized,
        "missing_symbols": missing,
        "provider_metadata": {
            "market_data_source": source_payload.get("market_data_source"),
            "primary_market_data_source": source_payload.get("primary_market_data_source"),
            "fallback_market_data_source": source_payload.get("fallback_market_data_source"),
            "errors": source_payload.get("errors", []),
            "stale_data": source_payload.get("stale_data", False),
        },
        "market_data": selected,
        "evidence": evidence,
        "limitations": [f"missing symbol: {symbol}" for symbol in missing],
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    source_payload, source_path, source_kind = load_source_payload(repo_root, args)
    output = resolve_path(repo_root, args.output) if args.output else default_output_path(repo_root, args.date)
    payload = build_market_data_payload(
        date=args.date,
        symbols=args.symbol,
        source_payload=source_payload,
        source_path=source_path,
        source_kind=source_kind,
    )
    write_json(output, payload)
    return {
        "status": "success",
        "date": args.date,
        "output": str(output),
        "symbols": payload["symbols"],
        "missing_symbols": payload["missing_symbols"],
        "evidence_count": len(payload["evidence"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build deterministic agent market-data evidence from existing snapshots")
    parser.add_argument("--date", required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--snapshot")
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
