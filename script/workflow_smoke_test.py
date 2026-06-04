#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import daily_self_review
import extract_monitor_signals
import extract_report_signals
import feishu_summary
import journal_review
import learning_review
import longbridge_account_snapshot
import paper_account_snapshot
import paper_trade_preview
import paper_trade_review
import paper_trade_submit
import plan_review
import position_review
import validate_report
import validate_trade_plan
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

    plan_validation = validate_trade_plan.validate(validate_args)
    steps["validate-trade-plan"] = {"validation": plan_validation}
    if plan_validation["status"] != "pass":
        return {"status": "failed", "date": args.date, "steps": steps, "reason": "validate-trade-plan failed"}

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

    account_snapshot_path = None
    if args.account_input:
        account_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            timezone=args.timezone,
            input=args.account_input,
            output=args.account_output,
            longbridge_cli=None,
        )
        steps["account-snapshot"] = longbridge_account_snapshot.run(account_args)
        account_snapshot_path = steps["account-snapshot"].get("output")
    elif args.account_snapshot:
        account_snapshot_path = args.account_snapshot
    else:
        default_account = repo_root / "runtime" / "account" / args.date / "account-snapshot.json"
        if default_account.exists():
            account_snapshot_path = str(default_account)

    if account_snapshot_path:
        position_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            account_snapshot=account_snapshot_path,
            signals=None,
            session=args.session,
            snapshot=None,
            config=args.position_config,
            output=None,
            append=True,
            journal_dir=args.journal_dir,
        )
        steps["position-review"] = position_review.run(position_args)

    if args.paper_input:
        paper_account_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            timezone=args.timezone,
            input=args.paper_input,
            output=args.paper_output,
            longbridge_cli=None,
            paper_execution_config=None,
        )
        steps["paper-account-snapshot"] = paper_account_snapshot.run(paper_account_args)
        paper_preview_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            session=args.session,
            signals=None,
            account_snapshot=steps["paper-account-snapshot"].get("output"),
            output=None,
            require_validation=True,
            default_market="US",
            tif="day",
        )
        steps["paper-trade-preview"] = paper_trade_preview.run(paper_preview_args)
        paper_submit_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            session=args.session,
            preview=steps["paper-trade-preview"].get("output"),
            account_snapshot=steps["paper-account-snapshot"].get("output"),
            orders_journal=None,
            signals=None,
            output=None,
            require_validation=True,
            execute=False,
            longbridge_cli=None,
            max_daily_risk_pct=3.0,
            max_daily_orders=3,
        )
        steps["paper-trade-submit"] = paper_trade_submit.run(paper_submit_args)
        paper_review_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            session=args.session,
            preview=steps["paper-trade-preview"].get("output"),
            paper_snapshot=steps["paper-account-snapshot"].get("output"),
            orders_journal=None,
            output=None,
            append=True,
            journal_dir=args.journal_dir,
        )
        steps["paper-trade-review"] = paper_trade_review.run(paper_review_args)

    daily_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        append=True,
        output=None,
        journal_dir=args.journal_dir,
    )
    steps["daily-self-review"] = daily_self_review.run(daily_args)

    plan_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        append_lessons=True,
        output=None,
        journal_dir=args.journal_dir,
        learning_dir=args.learning_dir,
    )
    steps["plan-review"] = plan_review.run(plan_args)

    learning_args = argparse.Namespace(
        repo_root=str(repo_root),
        lookback_days=20,
        min_count=3,
        end_date=args.date,
        output=None,
        learning_dir=args.learning_dir,
        journal_dir=args.journal_dir,
    )
    steps["learning-review"] = learning_review.run(learning_args)

    feishu_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        session=args.session,
        signals=None,
        position_review=None,
        plan_review=None,
        output=None,
        learning_dir=args.learning_dir,
    )
    steps["feishu-summary"] = feishu_summary.run(feishu_args)

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
    parser.add_argument("--account-input", help="Fixture payload for account-snapshot smoke coverage")
    parser.add_argument("--account-output", help="Optional account snapshot output path")
    parser.add_argument("--account-snapshot", help="Existing account snapshot path for position-review smoke coverage")
    parser.add_argument("--paper-input", help="Fixture payload for paper account/submit/review smoke coverage")
    parser.add_argument("--paper-output", help="Optional paper account snapshot output path")
    parser.add_argument("--position-config", help="Optional position review config path")
    parser.add_argument("--journal-dir", default="runtime/journal")
    parser.add_argument("--learning-dir", default="runtime/learning")
    parser.add_argument("--timezone", default="America/New_York")
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
