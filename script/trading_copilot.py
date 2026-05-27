#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]


def run_child(args: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_json_output(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def guard_reason(payload: Dict[str, Any]) -> Optional[str]:
    guard = payload.get("guard") or payload.get("trading_day")
    if not isinstance(guard, dict):
        return None
    return guard.get("holiday") or guard.get("reason")


def guard_date(payload: Dict[str, Any]) -> Optional[str]:
    guard = payload.get("guard") or payload.get("trading_day")
    if isinstance(guard, dict):
        date = guard.get("date")
        if isinstance(date, str):
            return date
    return None


def emit(payload: Dict[str, Any], exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


def base_response(workflow: str, command: List[str], stdout: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "status": "success",
        "workflow": workflow,
        "date": None,
        "artifacts": [],
        "skipped": False,
        "reason": None,
        "command": command,
        "stdout": stdout,
    }


def failed_response(workflow: str, command: List[str], proc: subprocess.CompletedProcess[str]) -> Dict[str, Any]:
    stdout = parse_json_output(proc.stdout)
    reason = proc.stderr.strip() or proc.stdout.strip() or f"command exited with {proc.returncode}"
    return {
        "status": "failed",
        "workflow": workflow,
        "date": guard_date(stdout or {}),
        "artifacts": [],
        "skipped": False,
        "reason": reason,
        "command": command,
        "stdout": stdout,
    }


def maybe_skipped(response: Dict[str, Any], stdout: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not stdout or not stdout.get("skipped"):
        return response
    response["status"] = "skipped"
    response["skipped"] = True
    response["date"] = guard_date(stdout)
    response["reason"] = guard_reason(stdout) or "workflow skipped"
    response["artifacts"] = []
    return response


def run_pre_market(args: argparse.Namespace) -> None:
    command = [
        "script/prepare_daily_context.py",
        "--watchlist",
        args.watchlist,
        "--interval",
        args.interval,
        "--timezone",
        args.timezone,
    ]
    if args.date:
        command.extend(["--date", args.date])
    if args.snapshot_date:
        command.extend(["--snapshot-date", args.snapshot_date])
    if args.skip_non_trading_day:
        command.append("--skip-non-trading-day")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("pre-market-plan", command, proc), 1)

    response = maybe_skipped(base_response("pre-market-plan", command, stdout), stdout)
    if response["status"] == "success" and stdout:
        response["date"] = stdout.get("report_date")
        context_path = stdout.get("context_path")
        response["artifacts"] = [context_path] if context_path else []
        response["next_agent_inputs"] = [
            "agent/daily_analysis_prompt.md",
            "knowledge/refined/",
            context_path,
        ]
        response["expected_agent_outputs"] = [
            f"report/{stdout.get('report_date')}/exec-brief.md",
            f"report/{stdout.get('report_date')}/pre-market.md",
            f"report/{stdout.get('report_date')}/signals.json",
        ]
    emit(response)


def run_post_market(args: argparse.Namespace) -> None:
    command = [
        "script/prepare_market_snapshot.py",
        "--watchlist",
        args.watchlist,
        "--interval",
        args.interval,
        "--outputsize",
        str(args.outputsize),
        "--timezone",
        args.timezone,
    ]
    if args.date:
        command.extend(["--date", args.date])
    if args.skip_non_trading_day:
        command.append("--skip-non-trading-day")
    if args.sp500_screen:
        command.append("--sp500-screen")
        command.extend(["--sp500-top", str(args.sp500_top)])
        command.extend(["--sp500-candidates", str(args.sp500_candidates)])
        command.extend(["--sp500-source", args.sp500_source])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("post-market-review", command, proc), 1)

    response = maybe_skipped(base_response("post-market-review", command, stdout), stdout)
    if response["status"] == "success" and stdout:
        artifacts = []
        if stdout.get("snapshot_path"):
            artifacts.append(stdout["snapshot_path"])
        if stdout.get("candidate_universe_path"):
            artifacts.append(stdout["candidate_universe_path"])
        response["date"] = stdout.get("snapshot_date")
        response["artifacts"] = artifacts
        response["next_agent_inputs"] = [
            "agent/post_market_analysis_prompt.md",
            "knowledge/refined/",
            stdout.get("snapshot_path"),
        ]
        response["expected_agent_outputs"] = [
            f"report/{stdout.get('snapshot_date')}/post-market.md",
            f"report/{stdout.get('snapshot_date')}/signals.json",
        ]
    emit(response)


def run_monitor(args: argparse.Namespace) -> None:
    command = [
        "script/monitor_scan.py",
        "--state",
        args.state,
        "--interval",
        args.interval,
        "--output",
        args.output,
    ]
    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("monitor-brief", command, proc), 1)

    response = base_response("monitor-brief", command, stdout)
    response["artifacts"] = [args.output]
    response["next_agent_inputs"] = [
        args.output,
        "knowledge/refined/",
    ]
    emit(response)


def run_trading_day_check(args: argparse.Namespace) -> None:
    command = [
        "script/trading_day_guard.py",
        "--format",
        "json",
        "--timezone",
        args.timezone,
    ]
    if args.date:
        command.extend(["--date", args.date])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("trading-day-check", command, proc), 1)

    response = base_response("trading-day-check", command, stdout)
    if stdout:
        response["date"] = stdout.get("date")
        response["is_trading_day"] = stdout.get("is_trading_day")
        response["reason"] = stdout.get("holiday") or stdout.get("reason")
    emit(response)


def validate_report_command(args: argparse.Namespace) -> List[str]:
    command = [
        "script/validate_report.py",
        "--date",
        args.date,
        "--session",
        args.session,
    ]
    if getattr(args, "report", None):
        command.extend(["--report", args.report])
    if getattr(args, "signals", None):
        command.extend(["--signals", args.signals])
    return command


def run_validate_report(args: argparse.Namespace) -> None:
    command = validate_report_command(args)
    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("validate-report", command, proc), 1)

    response = base_response("validate-report", command, stdout)
    response["date"] = args.date
    response["artifacts"] = (stdout or {}).get("checked_reports", [])
    response["validation"] = stdout
    emit(response)


def run_extract_report_signals(args: argparse.Namespace) -> None:
    command = [
        "script/extract_report_signals.py",
        "--date",
        args.date,
        "--session",
        args.session,
        "--max-signals",
        str(args.max_signals),
        "--journal-dir",
        args.journal_dir,
    ]
    if args.report:
        command.extend(["--report", args.report])
    if args.signals:
        command.extend(["--signals", args.signals])
    if args.append:
        command.append("--append")
    if args.require_validation:
        command.append("--require-validation")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("extract-report-signals", command, proc), 1)

    response = base_response("extract-report-signals", command, stdout)
    response["date"] = args.date
    response["artifacts"] = [stdout["journal_path"]] if stdout and stdout.get("journal_path") else []
    response["signals"] = (stdout or {}).get("signals", [])
    response["appended"] = (stdout or {}).get("appended", [])
    response["skipped_duplicates"] = (stdout or {}).get("skipped_duplicates", [])
    emit(response)


def run_backfill_signal_outcomes(args: argparse.Namespace) -> None:
    command = [
        "script/journal_review.py",
        "--date",
        args.date,
        "--journal-dir",
        args.journal_dir,
    ]
    if args.session:
        command.extend(["--session", args.session])
    if args.snapshot:
        command.extend(["--snapshot", args.snapshot])
    if args.append:
        command.append("--append")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("backfill-signal-outcomes", command, proc), 1)

    response = base_response("backfill-signal-outcomes", command, stdout)
    response["date"] = args.date
    response["artifacts"] = [stdout["outcomes_path"]] if stdout and stdout.get("outcomes_path") else []
    response["summary"] = (stdout or {}).get("summary")
    response["outcomes"] = (stdout or {}).get("outcomes", [])
    response["appended"] = (stdout or {}).get("appended", [])
    response["skipped_duplicates"] = (stdout or {}).get("skipped_duplicates", [])
    emit(response)


def run_daily_self_review(args: argparse.Namespace) -> None:
    command = [
        "script/daily_self_review.py",
        "--date",
        args.date,
        "--journal-dir",
        args.journal_dir,
    ]
    if args.output:
        command.extend(["--output", args.output])
    if args.append:
        command.append("--append")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("daily-self-review", command, proc), 1)

    response = base_response("daily-self-review", command, stdout)
    response["date"] = args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["summary"] = (stdout or {}).get("summary")
    response["appended"] = (stdout or {}).get("appended", [])
    response["skipped_duplicates"] = (stdout or {}).get("skipped_duplicates", [])
    emit(response)


def run_weekly_review(args: argparse.Namespace) -> None:
    command = [
        "script/weekly_review.py",
        "--week",
        args.week,
        "--journal-dir",
        args.journal_dir,
    ]
    if args.output:
        command.extend(["--output", args.output])
    if args.append:
        command.append("--append")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("weekly-review", command, proc), 1)

    response = base_response("weekly-review", command, stdout)
    response["date"] = (stdout or {}).get("date")
    response["week"] = args.week
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["summary"] = (stdout or {}).get("summary")
    response["appended"] = (stdout or {}).get("appended", [])
    response["skipped_duplicates"] = (stdout or {}).get("skipped_duplicates", [])
    emit(response)


def run_extract_monitor_signals(args: argparse.Namespace) -> None:
    command = [
        "script/extract_monitor_signals.py",
        "--monitor",
        args.monitor,
        "--max-signals",
        str(args.max_signals),
        "--journal-dir",
        args.journal_dir,
        "--timezone",
        args.timezone,
    ]
    if args.date:
        command.extend(["--date", args.date])
    if args.append:
        command.append("--append")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("extract-monitor-signals", command, proc), 1)

    response = base_response("extract-monitor-signals", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["journal_path"]] if stdout and stdout.get("journal_path") else []
    response["signals"] = (stdout or {}).get("signals", [])
    response["appended"] = (stdout or {}).get("appended", [])
    response["skipped_duplicates"] = (stdout or {}).get("skipped_duplicates", [])
    emit(response)


def run_account_snapshot(args: argparse.Namespace) -> None:
    command = [
        "script/longbridge_account_snapshot.py",
        "--timezone",
        args.timezone,
    ]
    if args.date:
        command.extend(["--date", args.date])
    if args.input:
        command.extend(["--input", args.input])
    if args.output:
        command.extend(["--output", args.output])
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("account-snapshot", command, proc), 1)

    response = base_response("account-snapshot", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["positions_count"] = (stdout or {}).get("positions_count")
    emit(response)


def run_position_review(args: argparse.Namespace) -> None:
    command = [
        "script/position_review.py",
        "--date",
        args.date,
        "--journal-dir",
        args.journal_dir,
    ]
    if args.account_snapshot:
        command.extend(["--account-snapshot", args.account_snapshot])
    if args.signals:
        command.extend(["--signals", args.signals])
    if args.config:
        command.extend(["--config", args.config])
    if args.output:
        command.extend(["--output", args.output])
    if args.append:
        command.append("--append")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("position-review", command, proc), 1)

    response = base_response("position-review", command, stdout)
    response["date"] = args.date
    response["artifacts"] = (stdout or {}).get("artifacts", [])
    response["summary"] = (stdout or {}).get("summary")
    response["appended"] = (stdout or {}).get("appended", [])
    response["skipped_duplicates"] = (stdout or {}).get("skipped_duplicates", [])
    emit(response)


def run_sync_longbridge_watchlist(args: argparse.Namespace) -> None:
    validation = None
    if args.require_validation:
        if not args.date:
            emit(
                {
                    "status": "failed",
                    "workflow": "sync-longbridge-watchlist",
                    "date": None,
                    "artifacts": [],
                    "skipped": False,
                    "reason": "--date is required with --require-validation",
                    "command": [],
                    "stdout": None,
                },
                1,
            )
        validation_command = validate_report_command(args)
        validation_proc = run_child(validation_command)
        validation = parse_json_output(validation_proc.stdout)
        if validation_proc.returncode != 0:
            response = failed_response("sync-longbridge-watchlist", validation_command, validation_proc)
            response["date"] = args.date
            response["validation"] = validation
            emit(response, 1)

    command = [
        "script/sync_longbridge_watchlist.py",
        "--session",
        args.session,
        "--sync-mode",
        args.sync_mode,
        "--max-symbols",
        str(args.max_symbols),
    ]
    if args.date:
        command.extend(["--date", args.date])
    if args.report:
        command.extend(["--report", args.report])
    if args.signals:
        command.extend(["--signals", args.signals])
    if args.group_name:
        command.extend(["--group-name", args.group_name])
    if args.default_market:
        command.extend(["--default-market", args.default_market])
    for symbol in args.symbol or []:
        command.extend(["--symbol", symbol])
    if args.execute:
        command.append("--execute")
    if args.no_create:
        command.append("--no-create")
    command.extend(["--method", args.method])
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("sync-longbridge-watchlist", command, proc), 1)

    response = base_response("sync-longbridge-watchlist", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = []
    response["group_name"] = (stdout or {}).get("group_name")
    response["sync_mode"] = (stdout or {}).get("sync_mode")
    response["dry_run"] = (stdout or {}).get("dry_run")
    response["symbols"] = (stdout or {}).get("symbols", [])
    response["longbridge"] = (stdout or {}).get("longbridge")
    response["validation"] = validation
    emit(response)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified Trading Copilot workflow wrapper for agent callers"
    )
    sub = parser.add_subparsers(dest="workflow", required=True)

    pre = sub.add_parser("pre-market-plan", help="Prepare pre-market context for agent reporting")
    pre.add_argument("--watchlist", default="config/watchlist.json")
    pre.add_argument("--interval", default="1day")
    pre.add_argument("--date")
    pre.add_argument("--snapshot-date")
    pre.add_argument("--timezone", default="America/New_York")
    pre.add_argument("--skip-non-trading-day", action="store_true")
    pre.set_defaults(func=run_pre_market)

    post = sub.add_parser("post-market-review", help="Prepare post-market snapshot for agent review")
    post.add_argument("--watchlist", default="config/watchlist.json")
    post.add_argument("--interval", default="1day")
    post.add_argument("--outputsize", type=int, default=200)
    post.add_argument("--date")
    post.add_argument("--timezone", default="America/New_York")
    post.add_argument("--skip-non-trading-day", action="store_true")
    post.add_argument("--sp500-screen", action="store_true")
    post.add_argument("--sp500-top", type=int, default=100)
    post.add_argument("--sp500-candidates", type=int, default=15)
    post.add_argument("--sp500-source", choices=["ishares_ivv", "slickcharts"], default="ishares_ivv")
    post.set_defaults(func=run_post_market)

    monitor = sub.add_parser("monitor-brief", help="Run intraday monitor scan")
    monitor.add_argument("--state", default="config/monitor_state.json")
    monitor.add_argument("--interval", default="5min")
    monitor.add_argument("--output", default="report/latest-monitor.json")
    monitor.set_defaults(func=run_monitor)

    day = sub.add_parser("trading-day-check", help="Check regular US market trading-day status")
    day.add_argument("--date")
    day.add_argument("--timezone", default="America/New_York")
    day.set_defaults(func=run_trading_day_check)

    validate = sub.add_parser("validate-report", help="Validate generated report quality before delivery or sync")
    validate.add_argument("--date", required=True)
    validate.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    validate.add_argument("--report")
    validate.add_argument("--signals")
    validate.set_defaults(func=run_validate_report)

    signals = sub.add_parser("extract-report-signals", help="Extract focused report candidates into journal signal records")
    signals.add_argument("--date", required=True)
    signals.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    signals.add_argument("--report")
    signals.add_argument("--signals")
    signals.add_argument("--max-signals", type=int, default=3)
    signals.add_argument("--append", action="store_true")
    signals.add_argument("--journal-dir", default="runtime/journal")
    signals.add_argument("--require-validation", action="store_true")
    signals.set_defaults(func=run_extract_report_signals)

    outcomes = sub.add_parser("backfill-signal-outcomes", help="Backfill journal signal outcomes from a completed daily snapshot")
    outcomes.add_argument("--date", required=True)
    outcomes.add_argument("--session", choices=["pre-market", "post-market", "monitor"])
    outcomes.add_argument("--snapshot")
    outcomes.add_argument("--append", action="store_true")
    outcomes.add_argument("--journal-dir", default="runtime/journal")
    outcomes.set_defaults(func=run_backfill_signal_outcomes)

    daily_review = sub.add_parser("daily-self-review", help="Generate a daily self-review from journal outcomes")
    daily_review.add_argument("--date", required=True)
    daily_review.add_argument("--append", action="store_true")
    daily_review.add_argument("--output")
    daily_review.add_argument("--journal-dir", default="runtime/journal")
    daily_review.set_defaults(func=run_daily_self_review)

    weekly_review = sub.add_parser("weekly-review", help="Generate a weekly review from journal outcomes and trades")
    weekly_review.add_argument("--week", required=True)
    weekly_review.add_argument("--append", action="store_true")
    weekly_review.add_argument("--output")
    weekly_review.add_argument("--journal-dir", default="runtime/journal")
    weekly_review.set_defaults(func=run_weekly_review)

    monitor_signals = sub.add_parser("extract-monitor-signals", help="Extract monitor scan observations into the journal")
    monitor_signals.add_argument("--monitor", default="report/latest-monitor.json")
    monitor_signals.add_argument("--date")
    monitor_signals.add_argument("--timezone", default="America/New_York")
    monitor_signals.add_argument("--max-signals", type=int, default=5)
    monitor_signals.add_argument("--append", action="store_true")
    monitor_signals.add_argument("--journal-dir", default="runtime/journal")
    monitor_signals.set_defaults(func=run_extract_monitor_signals)

    account = sub.add_parser("account-snapshot", help="Write a read-only Longbridge account snapshot")
    account.add_argument("--date")
    account.add_argument("--timezone", default="America/New_York")
    account.add_argument("--input")
    account.add_argument("--output")
    account.add_argument("--longbridge-cli")
    account.set_defaults(func=run_account_snapshot)

    position = sub.add_parser("position-review", help="Review read-only positions against structured signals")
    position.add_argument("--date", required=True)
    position.add_argument("--account-snapshot")
    position.add_argument("--signals")
    position.add_argument("--config")
    position.add_argument("--output")
    position.add_argument("--append", action="store_true")
    position.add_argument("--journal-dir", default="runtime/journal")
    position.set_defaults(func=run_position_review)

    sync = sub.add_parser(
        "sync-longbridge-watchlist",
        help="Sync extracted daily focus symbols to a Longbridge watchlist group",
    )
    sync.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    sync.add_argument("--date")
    sync.add_argument("--report")
    sync.add_argument("--signals")
    sync.add_argument("--group-name")
    sync.add_argument("--default-market", default="US")
    sync.add_argument("--max-symbols", type=int, default=3)
    sync.add_argument("--symbol", action="append")
    sync.add_argument("--execute", action="store_true")
    sync.add_argument("--no-create", action="store_true")
    sync.add_argument("--sync-mode", choices=["auto", "add", "replace"], default="auto")
    sync.add_argument("--method", choices=["auto", "cli", "sdk"], default="auto")
    sync.add_argument("--longbridge-cli")
    sync.add_argument("--require-validation", action="store_true", help="Validate the generated report before any Longbridge sync.")
    sync.set_defaults(func=run_sync_longbridge_watchlist)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
