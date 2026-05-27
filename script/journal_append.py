#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOURNAL_FILES = {
    "signal": "signals.jsonl",
    "outcome": "outcomes.jsonl",
    "trade": "trades.jsonl",
    "review": "reviews.jsonl",
    "position_review": "position_reviews.jsonl",
}

SIGNAL_STATUSES = {"planned", "observed", "triggered", "invalidated", "no_trade"}
TRADE_STATUSES = {"entered", "missed", "no_trade", "win", "loss", "invalidated"}
REVIEW_SCOPES = {"daily", "weekly", "setup", "symbol"}


def clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def journal_path(repo_root: Path, journal_dir: str, kind: str) -> Path:
    base = Path(journal_dir)
    if not base.is_absolute():
        base = repo_root / base
    return base / JOURNAL_FILES[kind]


def base_record(kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def signal_record(args: argparse.Namespace) -> dict[str, Any]:
    if args.status not in SIGNAL_STATUSES:
        raise ValueError(f"unsupported signal status: {args.status}")
    payload = {
        **base_record("signal"),
        "date": args.date,
        "session": args.session,
        "symbol": args.symbol.upper(),
        "setup": args.setup,
        "status": args.status,
        "source_report": args.source_report,
        "regime": args.regime,
        "trigger": args.trigger,
        "invalidation": args.invalidation,
        "risk_r": args.risk_r,
        "notes": args.notes,
    }
    return clean_payload(payload)


def trade_record(args: argparse.Namespace) -> dict[str, Any]:
    if args.status not in TRADE_STATUSES:
        raise ValueError(f"unsupported trade status: {args.status}")
    payload = {
        **base_record("trade"),
        "date": args.date,
        "symbol": args.symbol.upper(),
        "status": args.status,
        "planned_setup": args.planned_setup,
        "entry": args.entry,
        "stop": args.stop,
        "exit": args.exit,
        "result_r": args.result_r,
        "lesson": args.lesson,
        "source_signal_id": args.source_signal_id,
    }
    return clean_payload(payload)


def review_record(args: argparse.Namespace) -> dict[str, Any]:
    if args.scope not in REVIEW_SCOPES:
        raise ValueError(f"unsupported review scope: {args.scope}")
    payload = {
        **base_record("review"),
        "date": args.date,
        "scope": args.scope,
        "summary": args.summary,
        "setup": args.setup,
        "symbol": args.symbol.upper() if args.symbol else None,
        "outcome": args.outcome,
        "lesson": args.lesson,
        "source_report": args.source_report,
    }
    return clean_payload(payload)


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--journal-dir", default="runtime/journal")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append Trading Copilot journal JSONL records")
    sub = parser.add_subparsers(dest="kind", required=True)

    signal = sub.add_parser("signal", help="Append a planned or observed setup candidate")
    add_common(signal)
    signal.add_argument("--date", required=True)
    signal.add_argument("--session", choices=["pre-market", "post-market", "monitor"], required=True)
    signal.add_argument("--symbol", required=True)
    signal.add_argument("--setup", required=True)
    signal.add_argument("--status", choices=sorted(SIGNAL_STATUSES), required=True)
    signal.add_argument("--source-report", required=True)
    signal.add_argument("--regime")
    signal.add_argument("--trigger")
    signal.add_argument("--invalidation")
    signal.add_argument("--risk-r", type=float)
    signal.add_argument("--notes")
    signal.set_defaults(factory=signal_record)

    trade = sub.add_parser("trade", help="Append an optional human-entered trade outcome")
    add_common(trade)
    trade.add_argument("--date", required=True)
    trade.add_argument("--symbol", required=True)
    trade.add_argument("--status", choices=sorted(TRADE_STATUSES), required=True)
    trade.add_argument("--planned-setup")
    trade.add_argument("--entry", type=float)
    trade.add_argument("--stop", type=float)
    trade.add_argument("--exit", type=float)
    trade.add_argument("--result-r", type=float)
    trade.add_argument("--lesson")
    trade.add_argument("--source-signal-id")
    trade.set_defaults(factory=trade_record)

    review = sub.add_parser("review", help="Append a daily, weekly, setup, or symbol review")
    add_common(review)
    review.add_argument("--date", required=True)
    review.add_argument("--scope", choices=sorted(REVIEW_SCOPES), required=True)
    review.add_argument("--summary", required=True)
    review.add_argument("--setup")
    review.add_argument("--symbol")
    review.add_argument("--outcome")
    review.add_argument("--lesson")
    review.add_argument("--source-report")
    review.set_defaults(factory=review_record)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve()
    payload = args.factory(args)
    path = journal_path(repo_root, args.journal_dir, args.kind)
    append_jsonl(path, payload)
    print(json.dumps({"status": "success", "path": str(path), "record": payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
