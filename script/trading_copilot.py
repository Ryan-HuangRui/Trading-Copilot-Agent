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
            f"report/{stdout.get('snapshot_date')}/post-market.md"
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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
