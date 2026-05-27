#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from journal_append import append_jsonl, journal_path
from signal_artifacts import stable_signal_id


APPENDABLE_STATUSES = {"可执行", "临近触发"}


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected object")
    return data


def current_market_date(timezone: str) -> str:
    return dt.datetime.now(ZoneInfo(timezone)).date().isoformat()


def existing_signal_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        signal_id = payload.get("signal_id")
        if isinstance(signal_id, str):
            ids.add(signal_id)
    return ids


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def source_path(path: Path, repo_root: Path) -> str:
    return str(path.relative_to(repo_root)) if path.is_relative_to(repo_root) else str(path)


def scan_to_signal(scan: dict[str, Any], date: str, source: str, risk_pct: Any) -> dict[str, Any] | None:
    status_text = str(scan.get("status") or "")
    if status_text not in APPENDABLE_STATUSES:
        return None
    symbol = str(scan.get("symbol") or "").upper()
    if not symbol:
        return None
    trigger_price = to_float(scan.get("trigger"))
    invalidation_price = to_float(scan.get("stop"))
    setup = "strong_breakout_trend_following.md" if status_text == "可执行" else "breakout_pullback_continuation.md"
    signal_id = stable_signal_id(date, "monitor", symbol, f"{source}|{status_text}")
    return {
        "kind": "signal",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "signal_id": signal_id,
        "date": date,
        "session": "monitor",
        "symbol": symbol,
        "setup": setup,
        "setup_files": [setup],
        "status": "observed",
        "source_report": source,
        "trigger": f"{status_text}：{scan.get('reason')}; trigger={scan.get('trigger')}",
        "trigger_price": trigger_price,
        "invalidation": scan.get("invalid") or f"stop={scan.get('stop')}",
        "invalidation_price": invalidation_price,
        "risk": f"监控配置单笔风险 {risk_pct}%",
        "notes": scan.get("reason"),
    }


def extract(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    monitor_path = Path(args.monitor)
    if not monitor_path.is_absolute():
        monitor_path = repo_root / monitor_path
    if not monitor_path.exists():
        raise FileNotFoundError(f"missing monitor artifact: {monitor_path}")
    payload = read_json(monitor_path)
    date = args.date or current_market_date(args.timezone)
    source = source_path(monitor_path, repo_root)
    signals = [
        signal
        for signal in (
            scan_to_signal(scan, date, source, payload.get("risk_per_trade_pct"))
            for scan in payload.get("scans", [])
            if isinstance(scan, dict)
        )
        if signal is not None
    ][: args.max_signals]

    appended = []
    skipped_duplicates = []
    journal_file = None
    if args.append:
        journal_file = journal_path(repo_root, args.journal_dir, "signal")
        existing = existing_signal_ids(journal_file)
        for signal in signals:
            signal_id = str(signal.get("signal_id") or "")
            if signal_id in existing:
                skipped_duplicates.append(signal_id)
                continue
            append_jsonl(journal_file, signal)
            existing.add(signal_id)
            appended.append(signal_id)

    return {
        "status": "success",
        "date": date,
        "source_report": source,
        "signals": signals,
        "journal_path": str(journal_file) if journal_file else None,
        "append": args.append,
        "appended": appended,
        "skipped_duplicates": skipped_duplicates,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract monitor scan observations into signal journal records")
    parser.add_argument("--monitor", default="report/latest-monitor.json")
    parser.add_argument("--date")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--max-signals", type=int, default=5)
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--journal-dir", default="runtime/journal")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        payload = extract(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
