#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import daily_self_review
import extract_monitor_signals
import extract_report_signals
import journal_review
import validate_report
import weekly_review


def run(args: argparse.Namespace) -> dict:
    repo_root = Path(args.repo_root).resolve()
    steps = {}

    validate_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        session=args.session,
        report=None,
        signals=None,
    )
    validation = validate_report.validate(validate_args)
    steps["validate-report"] = {"validation": validation}
    if validation["status"] != "pass":
        return {"status": "failed", "date": args.date, "steps": steps, "reason": "validate-report failed"}

    extract_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        session=args.session,
        report=None,
        signals=None,
        max_signals=3,
        append=True,
        journal_dir=args.journal_dir,
        require_validation=False,
    )
    steps["extract-report-signals"] = extract_report_signals.extract(extract_args)

    backfill_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        session=None,
        snapshot=None,
        append=True,
        journal_dir=args.journal_dir,
    )
    steps["backfill-signal-outcomes"] = journal_review.backfill(backfill_args)

    daily_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        append=True,
        output=None,
        journal_dir=args.journal_dir,
    )
    steps["daily-self-review"] = daily_self_review.run(daily_args)

    weekly_args = argparse.Namespace(
        repo_root=str(repo_root),
        week=args.week,
        append=True,
        output=None,
        journal_dir=args.journal_dir,
    )
    steps["weekly-review"] = weekly_review.run(weekly_args)

    monitor_args = argparse.Namespace(
        repo_root=str(repo_root),
        monitor=args.monitor,
        date=args.date,
        timezone="America/New_York",
        max_signals=5,
        append=True,
        journal_dir=args.journal_dir,
    )
    steps["extract-monitor-signals"] = extract_monitor_signals.extract(monitor_args)

    return {"status": "success", "date": args.date, "week": args.week, "steps": steps}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fixture-based workflow smoke test without external data calls")
    parser.add_argument("--date", required=True)
    parser.add_argument("--week", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market"], default="pre-market")
    parser.add_argument("--monitor", default="report/latest-monitor.json")
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
    raise SystemExit(0 if payload["status"] == "success" else 1)


if __name__ == "__main__":
    main()
