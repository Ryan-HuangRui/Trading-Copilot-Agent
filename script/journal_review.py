#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_append import append_jsonl, journal_path
from market_calendar import next_trading_day


PRICE_RE = re.compile(r"\d+(?:\.\d+)?")
TRIGGER_RE = re.compile(r"(?:突破|上攻|站上|重新上攻|突破并站稳)\s*(\d+(?:\.\d+)?)")
INVALID_RE = re.compile(r"(?:跌破|失守|深破|不深破)\s*(\d+(?:\.\d+)?)")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def snapshot_path(repo_root: Path, review_date: str, explicit_snapshot: str | None) -> Path:
    if explicit_snapshot:
        path = Path(explicit_snapshot)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / review_date / "daily-snapshot.json"


def load_snapshot_symbols(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for item in data.get("symbols", []):
        symbol = item.get("symbol")
        if isinstance(symbol, str):
            result[symbol.upper()] = item
    return result


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def latest_bar(symbol_snapshot: dict[str, Any]) -> dict[str, Any] | None:
    latest = symbol_snapshot.get("latest")
    return latest if isinstance(latest, dict) else None


def first_price(pattern: re.Pattern[str], text: str | None) -> float | None:
    if not text:
        return None
    match = pattern.search(text)
    if match:
        return float(match.group(1))
    numbers = PRICE_RE.findall(text)
    return float(numbers[0]) if numbers else None


def signal_price(signal: dict[str, Any], price_field: str, detail_field: str, text_field: str, pattern: re.Pattern[str]) -> float | None:
    value = signal.get(price_field)
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    detail = signal.get(detail_field)
    if isinstance(detail, dict) and detail.get("price") is not None:
        try:
            return float(detail["price"])
        except (TypeError, ValueError):
            pass
    return first_price(pattern, signal.get(text_field))


def planned_target_date(signal: dict[str, Any]) -> str | None:
    date_text = signal.get("date")
    if not isinstance(date_text, str):
        return None
    try:
        signal_date = dt.date.fromisoformat(date_text)
    except ValueError:
        return None
    if signal.get("session") == "post-market":
        return next_trading_day(signal_date).isoformat()
    return signal_date.isoformat()


def outcome_id(signal_id: str, review_date: str) -> str:
    return f"{signal_id}:outcome:{review_date}"


def existing_outcome_ids(path: Path) -> set[str]:
    ids = set()
    for record in read_jsonl(path):
        value = record.get("outcome_id")
        if isinstance(value, str):
            ids.add(value)
    return ids


def evaluate_signal(signal: dict[str, Any], review_date: str, symbols: dict[str, dict[str, Any]]) -> dict[str, Any]:
    symbol = str(signal.get("symbol", "")).upper()
    signal_id = str(signal.get("signal_id") or "")
    symbol_snapshot = symbols.get(symbol)
    base = {
        "kind": "outcome",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "outcome_id": outcome_id(signal_id, review_date),
        "signal_id": signal_id,
        "signal_date": signal.get("date"),
        "signal_session": signal.get("session"),
        "target_date": planned_target_date(signal),
        "review_date": review_date,
        "symbol": symbol,
        "setup": signal.get("setup"),
        "source_report": signal.get("source_report"),
    }

    if not symbol_snapshot:
        return {**base, "outcome": "no_data", "reason": "symbol missing from snapshot"}

    bar = latest_bar(symbol_snapshot)
    if not bar:
        return {**base, "outcome": "no_data", "reason": "latest bar missing from snapshot"}

    trigger_price = signal_price(signal, "trigger_price", "trigger_detail", "trigger", TRIGGER_RE)
    invalidation_price = signal_price(signal, "invalidation_price", "invalidation_detail", "invalidation", INVALID_RE)
    high = to_float(bar.get("high"))
    low = to_float(bar.get("low"))
    open_price = to_float(bar.get("open"))
    close = to_float(bar.get("close"))
    bar_date = bar.get("datetime")

    payload = {
        **base,
        "bar_date": bar_date,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "trigger_price": trigger_price,
        "invalidation_price": invalidation_price,
    }

    if trigger_price is None or invalidation_price is None or high is None or low is None:
        payload["outcome"] = "not_evaluable"
        payload["reason"] = "missing numeric trigger, invalidation, high, or low"
        return {key: value for key, value in payload.items() if value is not None}

    trigger_hit = high >= trigger_price
    invalidation_hit = low <= invalidation_price
    if trigger_hit and invalidation_hit:
        outcome = "triggered_and_invalidated"
        reason = "daily bar touched both trigger and invalidation; intraday order unknown"
    elif trigger_hit:
        outcome = "triggered"
        reason = "daily high touched or exceeded trigger"
    elif invalidation_hit:
        outcome = "invalidated"
        reason = "daily low touched or fell below invalidation"
    else:
        outcome = "not_triggered"
        reason = "daily range touched neither trigger nor invalidation"

    risk = trigger_price - invalidation_price
    if risk > 0:
        payload["max_favorable_r"] = round((high - trigger_price) / risk, 3)
        payload["max_adverse_r"] = round((low - trigger_price) / risk, 3)
    payload["trigger_hit"] = trigger_hit
    payload["invalidation_hit"] = invalidation_hit
    payload["outcome"] = outcome
    payload["reason"] = reason
    return {key: value for key, value in payload.items() if value is not None}


def summarize(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    by_outcome = Counter(record.get("outcome", "unknown") for record in outcomes)
    by_setup: dict[str, Counter[str]] = defaultdict(Counter)
    by_symbol: dict[str, Counter[str]] = defaultdict(Counter)
    for record in outcomes:
        outcome = str(record.get("outcome", "unknown"))
        setup = str(record.get("setup") or "unknown")
        symbol = str(record.get("symbol") or "unknown")
        by_setup[setup][outcome] += 1
        by_symbol[symbol][outcome] += 1
    return {
        "total": len(outcomes),
        "by_outcome": dict(by_outcome),
        "by_setup": {key: dict(value) for key, value in sorted(by_setup.items())},
        "by_symbol": {key: dict(value) for key, value in sorted(by_symbol.items())},
    }


def backfill(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    signals_file = journal_path(repo_root, args.journal_dir, "signal")
    outcomes_file = journal_path(repo_root, args.journal_dir, "outcome")
    snapshot = snapshot_path(repo_root, args.date, args.snapshot)
    if not snapshot.exists():
        raise FileNotFoundError(f"missing daily snapshot: {snapshot}")

    symbols = load_snapshot_symbols(snapshot)
    records = read_jsonl(signals_file)
    candidates = [
        record
        for record in records
        if record.get("kind") == "signal"
        and planned_target_date(record) == args.date
        and (not args.session or record.get("session") == args.session)
    ]
    outcomes = [evaluate_signal(record, args.date, symbols) for record in candidates]

    appended = []
    skipped_duplicates = []
    if args.append:
        existing = existing_outcome_ids(outcomes_file)
        for outcome in outcomes:
            oid = str(outcome.get("outcome_id") or "")
            if oid in existing:
                skipped_duplicates.append(oid)
                continue
            append_jsonl(outcomes_file, outcome)
            existing.add(oid)
            appended.append(oid)

    return {
        "status": "success",
        "date": args.date,
        "snapshot_path": str(snapshot),
        "signals_path": str(signals_file),
        "outcomes_path": str(outcomes_file) if args.append else None,
        "outcomes": outcomes,
        "summary": summarize(outcomes),
        "append": args.append,
        "appended": appended,
        "skipped_duplicates": skipped_duplicates,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill signal outcomes from a completed daily snapshot")
    parser.add_argument("--date", required=True, help="Completed market date to review in YYYY-MM-DD")
    parser.add_argument("--session", choices=["pre-market", "post-market", "monitor"])
    parser.add_argument("--snapshot", help="Explicit daily-snapshot.json path")
    parser.add_argument("--append", action="store_true", help="Append outcomes to runtime journal JSONL")
    parser.add_argument("--journal-dir", default="runtime/journal")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        payload = backfill(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
