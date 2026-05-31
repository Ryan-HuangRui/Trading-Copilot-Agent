#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone
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


def normalize_symbols(symbols: List[str] | None) -> List[str]:
    seen = set()
    normalized: List[str] = []
    for symbol in symbols or []:
        value = str(symbol or "").strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def resolve_repo_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def agent_output_dir(date: str, explicit_dir: str | None) -> Path:
    if explicit_dir:
        return resolve_repo_path(explicit_dir)
    return ROOT / "report" / date / "agents"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def symbols_from_agent_source(path_text: str | None) -> List[str]:
    if not path_text:
        return []
    path = resolve_repo_path(path_text)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
    symbols = []
    for item in snapshot.get("symbols", []):
        if isinstance(item, dict) and item.get("symbol"):
            symbols.append(str(item["symbol"]))
    return normalize_symbols(symbols)


def run_agent_research_pipeline(
    *,
    date: str,
    symbols: List[str],
    context_path: str | None = None,
    snapshot_path: str | None = None,
) -> Dict[str, Any]:
    normalized = normalize_symbols(symbols)
    if not normalized:
        return {
            "status": "skipped",
            "artifacts": [],
            "reason": "no symbols available for agent research",
        }
    agents_dir = str(ROOT / "report" / date / "agents")
    market_data_path = str(ROOT / "report" / date / "agents" / "market-data.json")
    technicals_path = str(ROOT / "report" / date / "agents" / "technicals.json")
    commands: List[List[str]] = []
    market_command = [
        "script/agent_market_data.py",
        "--date",
        date,
        "--output",
        market_data_path,
    ]
    if context_path:
        market_command.extend(["--context", context_path])
    if snapshot_path:
        market_command.extend(["--snapshot", snapshot_path])
    for symbol in normalized:
        market_command.extend(["--symbol", symbol])
    commands.append(market_command)

    technicals_command = [
        "script/agent_technicals.py",
        "--date",
        date,
        "--market-data",
        market_data_path,
        "--output",
        technicals_path,
    ]
    for symbol in normalized:
        technicals_command.extend(["--symbol", symbol])
    commands.append(technicals_command)

    reports_command = [
        "script/agent_research_reports.py",
        "--date",
        date,
        "--market-data",
        market_data_path,
        "--technicals",
        technicals_path,
        "--output-dir",
        agents_dir,
    ]
    validate_reports_command = ["script/validate_agent_reports.py", "--date", date, "--reports-dir", agents_dir]
    decision_command = ["script/agent_decision.py", "--date", date, "--reports-dir", agents_dir, "--output-dir", agents_dir]
    validate_decision_command = ["script/validate_agent_decision.py", "--date", date, "--decision-dir", agents_dir]
    for symbol in normalized:
        reports_command.extend(["--symbol", symbol])
        validate_reports_command.extend(["--symbol", symbol])
        decision_command.extend(["--symbol", symbol])
        validate_decision_command.extend(["--symbol", symbol])
    commands.extend([reports_command, validate_reports_command, decision_command, validate_decision_command])

    artifacts = [market_data_path, technicals_path]
    for command in commands:
        proc = run_child(command)
        stdout = parse_json_output(proc.stdout)
        if proc.returncode != 0:
            return {
                "status": "failed",
                "artifacts": artifacts,
                "reason": proc.stderr.strip() or proc.stdout.strip() or f"{command[0]} failed",
                "command": command,
                "stdout": stdout,
            }
        artifacts.extend((stdout or {}).get("artifacts", []))
        output = (stdout or {}).get("output")
        if output:
            artifacts.append(output)
    return {
        "status": "success",
        "artifacts": sorted(dict.fromkeys(artifacts)),
        "reason": None,
        "symbols": normalized,
    }


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
            f"report/{stdout.get('report_date')}/pre-market-signals.json",
        ]
        if getattr(args, "include_agent_research", False):
            symbols = normalize_symbols(getattr(args, "agent_symbol", []) or symbols_from_agent_source(context_path))
            research = run_agent_research_pipeline(
                date=str(stdout.get("report_date")),
                symbols=symbols,
                context_path=context_path,
            )
            response["agent_research"] = research
            if research["status"] == "failed":
                response["status"] = "failed"
                response["reason"] = research["reason"]
                emit(response, 1)
            response["artifacts"].extend(research.get("artifacts", []))
            response["next_agent_inputs"].extend(research.get("artifacts", []))
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
        "--market-data-source",
        args.market_data_source,
        "--fallback-market-data-source",
        args.fallback_market_data_source,
    ]
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])
    if args.longbridge_default_market:
        command.extend(["--longbridge-default-market", args.longbridge_default_market])
    if args.date:
        command.extend(["--date", args.date])
    if args.skip_non_trading_day:
        command.append("--skip-non-trading-day")
    if args.sp500_screen:
        command.append("--sp500-screen")
        command.extend(["--sp500-top", str(args.sp500_top)])
        command.extend(["--sp500-candidates", str(args.sp500_candidates)])
        command.extend(["--sp500-source", args.sp500_source])
    for symbol in getattr(args, "extra_symbol", []) or []:
        command.extend(["--extra-symbol", symbol])
    if getattr(args, "include_journal_signals", False):
        command.append("--include-journal-signals")
    if getattr(args, "include_position_symbols", False):
        command.append("--include-position-symbols")

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
            f"report/{stdout.get('snapshot_date')}/post-market-signals.json",
        ]
        if getattr(args, "include_agent_research", False):
            snapshot_path_text = stdout.get("snapshot_path")
            symbols = normalize_symbols(getattr(args, "agent_symbol", []) or symbols_from_agent_source(snapshot_path_text))
            research = run_agent_research_pipeline(
                date=str(stdout.get("snapshot_date")),
                symbols=symbols,
                snapshot_path=snapshot_path_text,
            )
            response["agent_research"] = research
            if research["status"] == "failed":
                response["status"] = "failed"
                response["reason"] = research["reason"]
                emit(response, 1)
            response["artifacts"].extend(research.get("artifacts", []))
            response["next_agent_inputs"].extend(research.get("artifacts", []))
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
        "--market-data-source",
        args.market_data_source,
        "--fallback-market-data-source",
        args.fallback_market_data_source,
    ]
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])
    if args.longbridge_default_market:
        command.extend(["--longbridge-default-market", args.longbridge_default_market])
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


def run_agent_research_context(args: argparse.Namespace) -> None:
    symbols = normalize_symbols(args.symbol)
    output = resolve_repo_path(args.output) if args.output else ROOT / "report" / args.date / "agents" / "research-context.json"
    payload: Dict[str, Any] = {
        "schema_version": 1,
        "workflow": "agent-research-context",
        "date": args.date,
        "session": args.session,
        "symbols": symbols,
        "generated_at": now_utc(),
        "experimental": True,
        "not_for_execution": True,
        "inputs": {
            "snapshot": args.snapshot,
            "context": args.context,
            "config": args.config,
        },
        "limitations": [
            "Phase 0 skeleton artifact; downstream report or execution workflows must not treat it as a trading signal."
        ],
    }
    write_json(output, payload)
    response = base_response("agent-research-context", ["agent-research-context"], payload)
    response["date"] = args.date
    response["artifacts"] = [str(output)]
    response["next_agent_inputs"] = [str(output), "knowledge/refined/"]
    emit(response)


def run_agent_research_reports(args: argparse.Namespace) -> None:
    if not getattr(args, "placeholder", False):
        command = [
            "script/agent_research_reports.py",
            "--date",
            args.date,
        ]
        for symbol in args.symbol or []:
            command.extend(["--symbol", symbol])
        if args.market_data:
            command.extend(["--market-data", args.market_data])
        if args.technicals:
            command.extend(["--technicals", args.technicals])
        if args.provider_fixture:
            command.extend(["--provider-fixture", args.provider_fixture])
        if args.output_dir:
            command.extend(["--output-dir", args.output_dir])
        if args.markdown:
            command.append("--markdown")
        proc = run_child(command)
        stdout = parse_json_output(proc.stdout)
        if proc.returncode != 0:
            emit(failed_response("agent-research-reports", command, proc), 1)
        response = base_response("agent-research-reports", command, stdout)
        response["date"] = args.date
        response["artifacts"] = (stdout or {}).get("artifacts", [])
        response["summary"] = (stdout or {}).get("summary")
        emit(response)

    symbols = normalize_symbols(args.symbol)
    out_dir = agent_output_dir(args.date, args.output_dir)
    report_types = ["market", "technicals", "fundamentals", "news", "sentiment"]
    artifacts: List[str] = []
    for symbol in symbols:
        symbol_dir = out_dir / symbol
        for report_type in report_types:
            path = symbol_dir / f"{report_type}_report.json"
            payload = {
                "schema_version": 1,
                "report_type": report_type,
                "date": args.date,
                "symbol": symbol,
                "generated_at": now_utc(),
                "experimental": True,
                "not_for_execution": True,
                "evidence": [],
                "facts": [],
                "derived_metrics": {},
                "scores": {},
                "limitations": [
                    "Phase 0 placeholder report. Replace with Phase 1/2 provider output before analysis."
                ],
            }
            write_json(path, payload)
            artifacts.append(str(path))
    response = base_response("agent-research-reports", ["agent-research-reports"], None)
    response["date"] = args.date
    response["artifacts"] = artifacts
    response["symbols"] = symbols
    emit(response)


def run_validate_agent_reports(args: argparse.Namespace) -> None:
    command = [
        "script/validate_agent_reports.py",
        "--date",
        args.date,
    ]
    for symbol in args.symbol or []:
        command.extend(["--symbol", symbol])
    if args.reports_dir:
        command.extend(["--reports-dir", args.reports_dir])
    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        response = failed_response("validate-agent-reports", command, proc)
        response["date"] = args.date
        response["validation"] = stdout
        emit(response, 1)
    response = base_response("validate-agent-reports", command, stdout)
    response["date"] = args.date
    response["validation"] = stdout
    response["artifacts"] = []
    emit(response)


def run_agent_decision(args: argparse.Namespace) -> None:
    if not getattr(args, "placeholder", False):
        command = [
            "script/agent_decision.py",
            "--date",
            args.date,
        ]
        for symbol in args.symbol or []:
            command.extend(["--symbol", symbol])
        if args.reports_dir:
            command.extend(["--reports-dir", args.reports_dir])
        if args.output_dir:
            command.extend(["--output-dir", args.output_dir])
        if args.memory:
            command.extend(["--memory", args.memory])
        proc = run_child(command)
        stdout = parse_json_output(proc.stdout)
        if proc.returncode != 0:
            emit(failed_response("agent-decision", command, proc), 1)
        response = base_response("agent-decision", command, stdout)
        response["date"] = args.date
        response["artifacts"] = (stdout or {}).get("artifacts", [])
        emit(response)

    symbols = normalize_symbols(args.symbol)
    out_dir = agent_output_dir(args.date, args.output_dir)
    artifacts: List[str] = []
    for symbol in symbols:
        symbol_dir = out_dir / symbol
        decision_id = f"{args.date}:agent-decision:{symbol}:placeholder"
        decision = {
            "schema_version": 1,
            "decision_id": decision_id,
            "date": args.date,
            "symbol": symbol,
            "generated_at": now_utc(),
            "experimental": True,
            "not_for_execution": True,
            "plan_type": "no_trade",
            "execution_status": "no_trade",
            "decision_label": "placeholder",
            "evidence_ids": [],
            "risk_summary": {
                "status": "not_evaluated",
                "limitations": ["Phase 0 skeleton decision has no current evidence."]
            },
            "limitations": [
                "Placeholder decision for contract testing only.",
                "Not valid for report delivery, journal append, paper preview, or broker submission.",
            ],
        }
        json_path = symbol_dir / "decision.json"
        md_path = symbol_dir / "decision.md"
        write_json(json_path, decision)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(
            "\n".join(
                [
                    f"# Agent Decision Placeholder: {symbol}",
                    "",
                    f"- date: {args.date}",
                    "- status: not_for_execution",
                    "- note: Phase 0 skeleton only; do not use for trading workflows.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        artifacts.extend([str(json_path), str(md_path)])
    response = base_response("agent-decision", ["agent-decision"], None)
    response["date"] = args.date
    response["artifacts"] = artifacts
    response["symbols"] = symbols
    emit(response)


def run_validate_agent_decision(args: argparse.Namespace) -> None:
    command = [
        "script/validate_agent_decision.py",
        "--date",
        args.date,
    ]
    for symbol in args.symbol or []:
        command.extend(["--symbol", symbol])
    if args.decision_dir:
        command.extend(["--decision-dir", args.decision_dir])
    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        response = failed_response("validate-agent-decision", command, proc)
        response["date"] = args.date
        response["validation"] = stdout
        emit(response, 1)
    response = base_response("validate-agent-decision", command, stdout)
    response["date"] = args.date
    response["validation"] = stdout
    response["artifacts"] = (stdout or {}).get("checked_artifacts", [])
    emit(response)


def run_agent_memory_review(args: argparse.Namespace) -> None:
    command = ["script/agent_memory.py", "review", "--memory-path", args.memory_path]
    if args.date:
        command.extend(["--date", args.date])
    for symbol in args.symbol or []:
        command.extend(["--symbol", symbol])
    if args.output:
        command.extend(["--output", args.output])
    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("agent-memory-review", command, proc), 1)
    response = base_response("agent-memory-review", command, stdout)
    response["date"] = args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["summary"] = (stdout or {}).get("summary")
    response["memory_matches"] = (stdout or {}).get("memory_matches", [])
    emit(response)


def run_agent_memory_append(args: argparse.Namespace) -> None:
    command = [
        "script/agent_memory.py",
        "append",
        "--decision",
        args.decision,
        "--memory-path",
        args.memory_path,
        "--outcome-status",
        args.outcome_status,
        "--reflection",
        args.reflection,
    ]
    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("agent-memory-append", command, proc), 1)
    response = base_response("agent-memory-append", command, stdout)
    response["date"] = (stdout or {}).get("date")
    response["artifacts"] = [stdout["memory_path"]] if stdout and stdout.get("memory_path") else []
    response["appended"] = (stdout or {}).get("appended")
    response["decision_id"] = (stdout or {}).get("decision_id")
    emit(response)


def run_agent_memory_export(args: argparse.Namespace) -> None:
    command = [
        "script/agent_memory.py",
        "export",
        "--memory-path",
        args.memory_path,
        "--sqlite-output",
        args.sqlite_output,
    ]
    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("agent-memory-export", command, proc), 1)
    response = base_response("agent-memory-export", command, stdout)
    response["artifacts"] = [stdout["sqlite_output"]] if stdout and stdout.get("sqlite_output") else []
    response["rows"] = (stdout or {}).get("rows")
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


def validate_trade_plan_command(args: argparse.Namespace) -> List[str]:
    command = [
        "script/validate_trade_plan.py",
        "--date",
        args.date,
        "--session",
        args.session,
    ]
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


def run_validate_trade_plan(args: argparse.Namespace) -> None:
    command = validate_trade_plan_command(args)

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("validate-trade-plan", command, proc), 1)

    response = base_response("validate-trade-plan", command, stdout)
    response["date"] = args.date
    response["artifacts"] = (stdout or {}).get("checked_artifacts", [])
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


def run_plan_review(args: argparse.Namespace) -> None:
    command = [
        "script/plan_review.py",
        "--date",
        args.date,
        "--journal-dir",
        args.journal_dir,
        "--learning-dir",
        args.learning_dir,
    ]
    if args.output:
        command.extend(["--output", args.output])
    if args.append_lessons:
        command.append("--append-lessons")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("plan-review", command, proc), 1)

    response = base_response("plan-review", command, stdout)
    response["date"] = args.date
    response["artifacts"] = (stdout or {}).get("artifacts", [])
    response["summary"] = (stdout or {}).get("summary")
    response["lessons"] = (stdout or {}).get("lessons", [])
    response["lessons_path"] = (stdout or {}).get("lessons_path")
    emit(response)


def run_learning_review(args: argparse.Namespace) -> None:
    command = [
        "script/learning_review.py",
        "--lookback-days",
        str(args.lookback_days),
        "--min-count",
        str(args.min_count),
        "--learning-dir",
        args.learning_dir,
        "--journal-dir",
        args.journal_dir,
    ]
    if args.end_date:
        command.extend(["--end-date", args.end_date])
    if args.output:
        command.extend(["--output", args.output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("learning-review", command, proc), 1)

    response = base_response("learning-review", command, stdout)
    response["date"] = (stdout or {}).get("end_date")
    response["artifacts"] = (stdout or {}).get("artifacts", [])
    response["summary"] = (stdout or {}).get("summary")
    response["pattern_candidates"] = (stdout or {}).get("pattern_candidates", [])
    emit(response)


def run_feishu_summary(args: argparse.Namespace) -> None:
    command = [
        "script/feishu_summary.py",
        "--date",
        args.date,
        "--session",
        args.session,
        "--learning-dir",
        args.learning_dir,
    ]
    if args.signals:
        command.extend(["--signals", args.signals])
    if args.position_review:
        command.extend(["--position-review", args.position_review])
    if args.plan_review:
        command.extend(["--plan-review", args.plan_review])
    if args.output:
        command.extend(["--output", args.output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("feishu-summary", command, proc), 1)

    response = base_response("feishu-summary", command, stdout)
    response["date"] = args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_data_quality(args: argparse.Namespace) -> None:
    command = [
        "script/data_quality.py",
        "--date",
        args.date,
        "--session",
        args.session,
        "--account-delta-threshold-pct",
        str(args.account_delta_threshold_pct),
        "--abnormal-move-threshold-pct",
        str(args.abnormal_move_threshold_pct),
    ]
    if args.snapshot:
        command.extend(["--snapshot", args.snapshot])
    if args.account_snapshot:
        command.extend(["--account-snapshot", args.account_snapshot])
    if args.output_json:
        command.extend(["--output-json", args.output_json])
    if args.output_md:
        command.extend(["--output-md", args.output_md])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("data-quality", command, proc), 1)

    response = base_response("data-quality", command, stdout)
    response["date"] = args.date
    response["artifacts"] = (stdout or {}).get("artifacts", [])
    response["quality_status"] = (stdout or {}).get("quality_status")
    response["focused_fallback_symbols"] = (stdout or {}).get("focused_fallback_symbols", [])
    response["missing_focused_symbols"] = (stdout or {}).get("missing_focused_symbols", [])
    emit(response)


def run_promote_lesson(args: argparse.Namespace) -> None:
    command = [
        "script/promote_lesson.py",
        "--pattern-id",
        args.pattern_id,
        "--learning-dir",
        args.learning_dir,
    ]
    if args.candidates:
        command.extend(["--candidates", args.candidates])
    if args.output:
        command.extend(["--output", args.output])
    if args.apply:
        command.append("--apply")
    else:
        command.append("--dry-run")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("promote-lesson", command, proc), 1)

    response = base_response("promote-lesson", command, stdout)
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["pattern_id"] = args.pattern_id
    response["applied"] = bool((stdout or {}).get("applied"))
    response["markdown_block"] = (stdout or {}).get("markdown_block")
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


def run_paper_account_snapshot(args: argparse.Namespace) -> None:
    command = [
        "script/paper_account_snapshot.py",
        "--timezone",
        args.timezone,
        "--repo-root",
        args.repo_root,
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
        emit(failed_response("paper-account-snapshot", command, proc), 1)

    response = base_response("paper-account-snapshot", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["positions_count"] = (stdout or {}).get("positions_count")
    response["orders_count"] = (stdout or {}).get("orders_count")
    response["executions_count"] = (stdout or {}).get("executions_count")
    response["account_channel"] = (stdout or {}).get("account_channel")
    emit(response)


def run_paper_trade_preview(args: argparse.Namespace) -> None:
    command = [
        "script/paper_trade_preview.py",
        "--date",
        args.date,
        "--session",
        args.session,
        "--repo-root",
        args.repo_root,
        "--default-market",
        args.default_market,
        "--tif",
        args.tif,
    ]
    if args.signals:
        command.extend(["--signals", args.signals])
    if args.account_snapshot:
        command.extend(["--account-snapshot", args.account_snapshot])
    if args.output:
        command.extend(["--output", args.output])
    if args.require_validation:
        command.append("--require-validation")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-trade-preview", command, proc), 1)

    response = base_response("paper-trade-preview", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_paper_trade_review(args: argparse.Namespace) -> None:
    command = [
        "script/paper_trade_review.py",
        "--date",
        args.date,
        "--session",
        args.session,
        "--repo-root",
        args.repo_root,
        "--journal-dir",
        args.journal_dir,
    ]
    if args.preview:
        command.extend(["--preview", args.preview])
    if args.paper_snapshot:
        command.extend(["--paper-snapshot", args.paper_snapshot])
    if args.orders_journal:
        command.extend(["--orders-journal", args.orders_journal])
    if args.output:
        command.extend(["--output", args.output])
    if args.append:
        command.append("--append")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-trade-review", command, proc), 1)

    response = base_response("paper-trade-review", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["summary"] = (stdout or {}).get("summary")
    response["appended"] = (stdout or {}).get("appended", [])
    response["skipped_duplicates"] = (stdout or {}).get("skipped_duplicates", [])
    emit(response)


def run_paper_trade_submit(args: argparse.Namespace) -> None:
    command = [
        "script/paper_trade_submit.py",
        "--date",
        args.date,
        "--session",
        args.session,
        "--repo-root",
        args.repo_root,
        "--max-daily-risk-pct",
        str(args.max_daily_risk_pct),
        "--max-daily-orders",
        str(args.max_daily_orders),
    ]
    if args.preview:
        command.extend(["--preview", args.preview])
    if args.account_snapshot:
        command.extend(["--account-snapshot", args.account_snapshot])
    if args.orders_journal:
        command.extend(["--orders-journal", args.orders_journal])
    if args.signals:
        command.extend(["--signals", args.signals])
    if args.output:
        command.extend(["--output", args.output])
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])
    if args.paper_execution_config:
        command.extend(["--paper-execution-config", args.paper_execution_config])
    if args.require_validation:
        command.append("--require-validation")
    if args.execute:
        command.append("--execute")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-trade-submit", command, proc), 1)

    response = base_response("paper-trade-submit", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["dry_run"] = (stdout or {}).get("dry_run")
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_paper_order_recover(args: argparse.Namespace) -> None:
    command = [
        "script/paper_order_recover.py",
        "--date",
        args.date,
        "--session",
        args.session,
        "--broker-order-id",
        args.broker_order_id,
        "--repo-root",
        args.repo_root,
    ]
    if args.preview:
        command.extend(["--preview", args.preview])
    if args.paper_snapshot:
        command.extend(["--paper-snapshot", args.paper_snapshot])
    if args.orders_journal:
        command.extend(["--orders-journal", args.orders_journal])
    if args.order_detail:
        command.extend(["--order-detail", args.order_detail])
    if args.symbol:
        command.extend(["--symbol", args.symbol])
    if args.intent_id:
        command.extend(["--intent-id", args.intent_id])
    if args.output:
        command.extend(["--output", args.output])
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])
    if args.append:
        command.append("--append")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-order-recover", command, proc), 1)

    response = base_response("paper-order-recover", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["summary"] = (stdout or {}).get("summary")
    response["appended"] = (stdout or {}).get("appended")
    emit(response)


def run_paper_order_sync(args: argparse.Namespace) -> None:
    command = [
        "script/paper_order_sync.py",
        "--date",
        args.date,
        "--repo-root",
        args.repo_root,
    ]
    if args.orders_journal:
        command.extend(["--orders-journal", args.orders_journal])
    if args.stops_journal:
        command.extend(["--stops-journal", args.stops_journal])
    if args.take_profit_journal:
        command.extend(["--take-profit-journal", args.take_profit_journal])
    if args.paper_snapshot:
        command.extend(["--paper-snapshot", args.paper_snapshot])
    if args.output:
        command.extend(["--output", args.output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-order-sync", command, proc), 1)

    response = base_response("paper-order-sync", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_paper_event_ledger(args: argparse.Namespace) -> None:
    command = [
        "script/paper_event_ledger.py",
        "--date",
        args.date,
        "--repo-root",
        args.repo_root,
    ]
    if args.orders_journal:
        command.extend(["--orders-journal", args.orders_journal])
    if args.stops_journal:
        command.extend(["--stops-journal", args.stops_journal])
    if args.take_profit_journal:
        command.extend(["--take-profit-journal", args.take_profit_journal])
    if args.execution_state:
        command.extend(["--execution-state", args.execution_state])
    if args.events_journal:
        command.extend(["--events-journal", args.events_journal])
    if args.output:
        command.extend(["--output", args.output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-event-ledger", command, proc), 1)

    response = base_response("paper-event-ledger", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    artifacts = []
    if stdout and stdout.get("output"):
        artifacts.append(stdout["output"])
    if stdout and stdout.get("events_journal"):
        artifacts.append(stdout["events_journal"])
    response["artifacts"] = artifacts
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_paper_execution_review(args: argparse.Namespace) -> None:
    command = [
        "script/paper_execution_review.py",
        "--date",
        args.date,
        "--repo-root",
        args.repo_root,
    ]
    if args.preview:
        command.extend(["--preview", args.preview])
    if args.execution_state:
        command.extend(["--execution-state", args.execution_state])
    if args.output:
        command.extend(["--output", args.output])
    if args.markdown_output:
        command.extend(["--markdown-output", args.markdown_output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-execution-review", command, proc), 1)

    response = base_response("paper-execution-review", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    artifacts = []
    if stdout and stdout.get("output"):
        artifacts.append(stdout["output"])
    if stdout and stdout.get("markdown"):
        artifacts.append(stdout["markdown"])
    response["artifacts"] = artifacts
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_paper_strategy_review(args: argparse.Namespace) -> None:
    command = [
        "script/paper_strategy_review.py",
        "--repo-root",
        args.repo_root,
    ]
    for review in args.review or []:
        command.extend(["--review", review])
    if args.output:
        command.extend(["--output", args.output])
    if args.markdown_output:
        command.extend(["--markdown-output", args.markdown_output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-strategy-review", command, proc), 1)

    response = base_response("paper-strategy-review", command, stdout)
    artifacts = []
    if stdout and stdout.get("output"):
        artifacts.append(stdout["output"])
    if stdout and stdout.get("markdown"):
        artifacts.append(stdout["markdown"])
    response["artifacts"] = artifacts
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_paper_learning_lessons(args: argparse.Namespace) -> None:
    command = [
        "script/paper_learning_lessons.py",
        "--date",
        args.date,
        "--repo-root",
        args.repo_root,
        "--learning-dir",
        args.learning_dir,
    ]
    if args.review:
        command.extend(["--review", args.review])
    if args.output:
        command.extend(["--output", args.output])
    if args.append:
        command.append("--append")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-learning-lessons", command, proc), 1)

    response = base_response("paper-learning-lessons", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_paper_order_cancel(args: argparse.Namespace) -> None:
    command = [
        "script/paper_order_cancel.py",
        "--date",
        args.date,
        "--repo-root",
        args.repo_root,
        "--expire-after-minutes",
        str(args.expire_after_minutes),
    ]
    if args.state:
        command.extend(["--state", args.state])
    if args.output:
        command.extend(["--output", args.output])
    if args.now:
        command.extend(["--now", args.now])
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])
    if args.paper_execution_config:
        command.extend(["--paper-execution-config", args.paper_execution_config])
    if args.execute:
        command.append("--execute")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-order-cancel", command, proc), 1)

    response = base_response("paper-order-cancel", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["dry_run"] = (stdout or {}).get("dry_run")
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_paper_protective_stop_plan(args: argparse.Namespace) -> None:
    command = [
        "script/paper_protective_stop_plan.py",
        "--date",
        args.date,
        "--repo-root",
        args.repo_root,
        "--tif",
        args.tif,
    ]
    if args.state:
        command.extend(["--state", args.state])
    if args.output:
        command.extend(["--output", args.output])
    if args.stops_journal:
        command.extend(["--stops-journal", args.stops_journal])
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])
    if args.paper_execution_config:
        command.extend(["--paper-execution-config", args.paper_execution_config])
    if args.execute:
        command.append("--execute")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-protective-stop-plan", command, proc), 1)

    response = base_response("paper-protective-stop-plan", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["dry_run"] = (stdout or {}).get("dry_run")
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_paper_take_profit_plan(args: argparse.Namespace) -> None:
    command = [
        "script/paper_take_profit_plan.py",
        "--date",
        args.date,
        "--repo-root",
        args.repo_root,
        "--exit-fraction",
        str(args.exit_fraction),
        "--tif",
        args.tif,
    ]
    if args.state:
        command.extend(["--state", args.state])
    if args.output:
        command.extend(["--output", args.output])
    if args.take_profit_journal:
        command.extend(["--take-profit-journal", args.take_profit_journal])
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])
    if args.paper_execution_config:
        command.extend(["--paper-execution-config", args.paper_execution_config])
    if args.execute:
        command.append("--execute")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-take-profit-plan", command, proc), 1)

    response = base_response("paper-take-profit-plan", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["dry_run"] = (stdout or {}).get("dry_run")
    response["summary"] = (stdout or {}).get("summary")
    emit(response)


def run_paper_break_even_stop_plan(args: argparse.Namespace) -> None:
    command = [
        "script/paper_break_even_stop_plan.py",
        "--date",
        args.date,
        "--repo-root",
        args.repo_root,
        "--buffer-pct",
        str(args.buffer_pct),
        "--tif",
        args.tif,
    ]
    if args.state:
        command.extend(["--state", args.state])
    if args.stops_journal:
        command.extend(["--stops-journal", args.stops_journal])
    if args.output:
        command.extend(["--output", args.output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-break-even-stop-plan", command, proc), 1)

    response = base_response("paper-break-even-stop-plan", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["dry_run"] = (stdout or {}).get("dry_run")
    response["summary"] = (stdout or {}).get("summary")
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
    if args.session:
        command.extend(["--session", args.session])
    if args.snapshot:
        command.extend(["--snapshot", args.snapshot])
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
    trade_plan_validation = None
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
        trade_plan_command = validate_trade_plan_command(args)
        trade_plan_proc = run_child(trade_plan_command)
        trade_plan_validation = parse_json_output(trade_plan_proc.stdout)
        if trade_plan_proc.returncode != 0:
            response = failed_response("sync-longbridge-watchlist", trade_plan_command, trade_plan_proc)
            response["date"] = args.date
            response["validation"] = validation
            response["trade_plan_validation"] = trade_plan_validation
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
    response["trade_plan_validation"] = trade_plan_validation
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
    pre.add_argument("--include-agent-research", action="store_true")
    pre.add_argument("--agent-symbol", action="append", default=[])
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
    post.add_argument("--extra-symbol", action="append", default=[])
    post.add_argument("--include-journal-signals", action="store_true")
    post.add_argument("--include-position-symbols", action="store_true")
    post.add_argument("--market-data-source", choices=["longbridge", "twelve"], default="longbridge")
    post.add_argument("--fallback-market-data-source", choices=["twelve", "longbridge", "none"], default="twelve")
    post.add_argument("--longbridge-cli")
    post.add_argument("--longbridge-default-market", default="US")
    post.add_argument("--include-agent-research", action="store_true")
    post.add_argument("--agent-symbol", action="append", default=[])
    post.set_defaults(func=run_post_market)

    monitor = sub.add_parser("monitor-brief", help="Run intraday monitor scan")
    monitor.add_argument("--state", default="config/monitor_state.json")
    monitor.add_argument("--interval", default="5min")
    monitor.add_argument("--output", default="report/latest-monitor.json")
    monitor.add_argument("--market-data-source", choices=["longbridge", "twelve"], default="longbridge")
    monitor.add_argument("--fallback-market-data-source", choices=["twelve", "longbridge", "none"], default="twelve")
    monitor.add_argument("--longbridge-cli")
    monitor.add_argument("--longbridge-default-market", default="US")
    monitor.set_defaults(func=run_monitor)

    agent_context = sub.add_parser("agent-research-context", help="Write a Phase 0 agent research context skeleton")
    agent_context.add_argument("--date", required=True)
    agent_context.add_argument("--session", choices=["pre-market", "post-market", "monitor", "research"], default="research")
    agent_context.add_argument("--symbol", action="append", required=True)
    agent_context.add_argument("--snapshot")
    agent_context.add_argument("--context")
    agent_context.add_argument("--config", default="config/agent_research.json")
    agent_context.add_argument("--output")
    agent_context.set_defaults(func=run_agent_research_context)

    agent_reports = sub.add_parser("agent-research-reports", help="Write Phase 0 placeholder agent research reports")
    agent_reports.add_argument("--date", required=True)
    agent_reports.add_argument("--symbol", action="append", required=True)
    agent_reports.add_argument("--market-data")
    agent_reports.add_argument("--technicals")
    agent_reports.add_argument("--provider-fixture")
    agent_reports.add_argument("--output-dir")
    agent_reports.add_argument("--markdown", action="store_true")
    agent_reports.add_argument("--placeholder", action="store_true", help="Write Phase 0 placeholder reports instead of generated reports")
    agent_reports.set_defaults(func=run_agent_research_reports)

    validate_agent_reports = sub.add_parser("validate-agent-reports", help="Validate structured agent research reports")
    validate_agent_reports.add_argument("--date", required=True)
    validate_agent_reports.add_argument("--symbol", action="append", required=True)
    validate_agent_reports.add_argument("--reports-dir")
    validate_agent_reports.set_defaults(func=run_validate_agent_reports)

    agent_decision = sub.add_parser("agent-decision", help="Write a Phase 0 placeholder agent decision")
    agent_decision.add_argument("--date", required=True)
    agent_decision.add_argument("--symbol", action="append", required=True)
    agent_decision.add_argument("--reports-dir")
    agent_decision.add_argument("--output-dir")
    agent_decision.add_argument("--memory")
    agent_decision.add_argument("--placeholder", action="store_true", help="Write a Phase 0 placeholder decision instead of role synthesis")
    agent_decision.set_defaults(func=run_agent_decision)

    validate_agent_decision = sub.add_parser("validate-agent-decision", help="Validate agent role reports and decisions")
    validate_agent_decision.add_argument("--date", required=True)
    validate_agent_decision.add_argument("--symbol", action="append", required=True)
    validate_agent_decision.add_argument("--decision-dir")
    validate_agent_decision.set_defaults(func=run_validate_agent_decision)

    agent_memory = sub.add_parser("agent-memory-review", help="Read-only Phase 0 memory review skeleton")
    agent_memory.add_argument("--date")
    agent_memory.add_argument("--symbol", action="append", default=[])
    agent_memory.add_argument("--memory-path", default="runtime/memory/trading_memory.md")
    agent_memory.add_argument("--output")
    agent_memory.set_defaults(func=run_agent_memory_review)

    agent_memory_append = sub.add_parser("agent-memory-append", help="Append an agent decision outcome to trading memory")
    agent_memory_append.add_argument("--decision", required=True)
    agent_memory_append.add_argument("--memory-path", default="runtime/memory/trading_memory.md")
    agent_memory_append.add_argument("--outcome-status", default="unknown")
    agent_memory_append.add_argument("--reflection", default="")
    agent_memory_append.set_defaults(func=run_agent_memory_append)

    agent_memory_export = sub.add_parser("agent-memory-export", help="Export append-only trading memory to SQLite")
    agent_memory_export.add_argument("--memory-path", default="runtime/memory/trading_memory.md")
    agent_memory_export.add_argument("--sqlite-output", default="runtime/memory/trading_memory.sqlite")
    agent_memory_export.set_defaults(func=run_agent_memory_export)

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

    validate_plan = sub.add_parser("validate-trade-plan", help="Validate structured trade-plan sidecar quality gates")
    validate_plan.add_argument("--date", required=True)
    validate_plan.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    validate_plan.add_argument("--signals")
    validate_plan.set_defaults(func=run_validate_trade_plan)

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

    plan_review = sub.add_parser("plan-review", help="Review generated trade plans and record learning lessons")
    plan_review.add_argument("--date", required=True)
    plan_review.add_argument("--output")
    plan_review.add_argument("--append-lessons", action="store_true")
    plan_review.add_argument("--journal-dir", default="runtime/journal")
    plan_review.add_argument("--learning-dir", default="runtime/learning")
    plan_review.set_defaults(func=run_plan_review)

    learning_review = sub.add_parser("learning-review", help="Aggregate daily lessons into repeated pattern candidates")
    learning_review.add_argument("--lookback-days", type=int, default=20)
    learning_review.add_argument("--min-count", type=int, default=3)
    learning_review.add_argument("--end-date")
    learning_review.add_argument("--output")
    learning_review.add_argument("--learning-dir", default="runtime/learning")
    learning_review.add_argument("--journal-dir", default="runtime/journal")
    learning_review.set_defaults(func=run_learning_review)

    feishu_summary = sub.add_parser("feishu-summary", help="Build a concise Feishu-ready execution summary")
    feishu_summary.add_argument("--date", required=True)
    feishu_summary.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    feishu_summary.add_argument("--signals")
    feishu_summary.add_argument("--position-review")
    feishu_summary.add_argument("--plan-review")
    feishu_summary.add_argument("--output")
    feishu_summary.add_argument("--learning-dir", default="runtime/learning")
    feishu_summary.set_defaults(func=run_feishu_summary)

    data_quality = sub.add_parser("data-quality", help="Generate market-data quality artifacts")
    data_quality.add_argument("--date", required=True)
    data_quality.add_argument("--session", choices=["pre-market", "post-market", "all"], default="all")
    data_quality.add_argument("--snapshot")
    data_quality.add_argument("--account-snapshot")
    data_quality.add_argument("--output-json")
    data_quality.add_argument("--output-md")
    data_quality.add_argument("--account-delta-threshold-pct", type=float, default=5.0)
    data_quality.add_argument("--abnormal-move-threshold-pct", type=float, default=20.0)
    data_quality.set_defaults(func=run_data_quality)

    promote_lesson = sub.add_parser("promote-lesson", help="Promote a pattern candidate into validated lessons")
    promote_lesson.add_argument("--pattern-id", required=True)
    promote_lesson.add_argument("--dry-run", action="store_true")
    promote_lesson.add_argument("--apply", action="store_true")
    promote_lesson.add_argument("--candidates")
    promote_lesson.add_argument("--learning-dir", default="runtime/learning")
    promote_lesson.add_argument("--output")
    promote_lesson.set_defaults(func=run_promote_lesson)

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

    paper_account = sub.add_parser("paper-account-snapshot", help="Write a read-only Longbridge paper account snapshot")
    paper_account.add_argument("--date")
    paper_account.add_argument("--timezone", default="America/New_York")
    paper_account.add_argument("--input")
    paper_account.add_argument("--output")
    paper_account.add_argument("--longbridge-cli")
    paper_account.add_argument("--repo-root", default=str(ROOT))
    paper_account.set_defaults(func=run_paper_account_snapshot)

    paper_preview = sub.add_parser("paper-trade-preview", help="Build paper-trading order previews from trade plans")
    paper_preview.add_argument("--date", required=True)
    paper_preview.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    paper_preview.add_argument("--signals")
    paper_preview.add_argument("--account-snapshot")
    paper_preview.add_argument("--output")
    paper_preview.add_argument("--default-market", default="US")
    paper_preview.add_argument("--tif", default="day")
    paper_preview.add_argument("--require-validation", action="store_true")
    paper_preview.add_argument("--repo-root", default=str(ROOT))
    paper_preview.set_defaults(func=run_paper_trade_preview)

    paper_review = sub.add_parser("paper-trade-review", help="Review paper executions against order previews")
    paper_review.add_argument("--date", required=True)
    paper_review.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    paper_review.add_argument("--preview")
    paper_review.add_argument("--paper-snapshot")
    paper_review.add_argument("--orders-journal")
    paper_review.add_argument("--output")
    paper_review.add_argument("--append", action="store_true")
    paper_review.add_argument("--journal-dir", default="runtime/journal")
    paper_review.add_argument("--repo-root", default=str(ROOT))
    paper_review.set_defaults(func=run_paper_trade_review)

    paper_submit = sub.add_parser("paper-trade-submit", help="Prepare controlled paper order submissions")
    paper_submit.add_argument("--date", required=True)
    paper_submit.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    paper_submit.add_argument("--preview")
    paper_submit.add_argument("--account-snapshot")
    paper_submit.add_argument("--orders-journal")
    paper_submit.add_argument("--signals")
    paper_submit.add_argument("--output")
    paper_submit.add_argument("--longbridge-cli")
    paper_submit.add_argument("--require-validation", action="store_true")
    paper_submit.add_argument("--execute", action="store_true")
    paper_submit.add_argument("--max-daily-risk-pct", type=float, default=3.0)
    paper_submit.add_argument("--max-daily-orders", type=int, default=3)
    paper_submit.add_argument("--paper-execution-config")
    paper_submit.add_argument("--repo-root", default=str(ROOT))
    paper_submit.set_defaults(func=run_paper_trade_submit)

    paper_recover = sub.add_parser("paper-order-recover", help="Recover a submitted paper order into the local journal")
    paper_recover.add_argument("--date", required=True)
    paper_recover.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    paper_recover.add_argument("--broker-order-id", required=True)
    paper_recover.add_argument("--preview")
    paper_recover.add_argument("--paper-snapshot")
    paper_recover.add_argument("--orders-journal")
    paper_recover.add_argument("--order-detail")
    paper_recover.add_argument("--symbol")
    paper_recover.add_argument("--intent-id")
    paper_recover.add_argument("--output")
    paper_recover.add_argument("--append", action="store_true")
    paper_recover.add_argument("--longbridge-cli")
    paper_recover.add_argument("--repo-root", default=str(ROOT))
    paper_recover.set_defaults(func=run_paper_order_recover)

    paper_sync = sub.add_parser("paper-order-sync", help="Sync submitted paper order state from a paper account snapshot")
    paper_sync.add_argument("--date", required=True)
    paper_sync.add_argument("--orders-journal")
    paper_sync.add_argument("--stops-journal")
    paper_sync.add_argument("--take-profit-journal")
    paper_sync.add_argument("--paper-snapshot")
    paper_sync.add_argument("--output")
    paper_sync.add_argument("--repo-root", default=str(ROOT))
    paper_sync.set_defaults(func=run_paper_order_sync)

    paper_events = sub.add_parser("paper-event-ledger", help="Project paper execution facts into the unified event ledger")
    paper_events.add_argument("--date", required=True)
    paper_events.add_argument("--orders-journal")
    paper_events.add_argument("--stops-journal")
    paper_events.add_argument("--take-profit-journal")
    paper_events.add_argument("--execution-state")
    paper_events.add_argument("--events-journal")
    paper_events.add_argument("--output")
    paper_events.add_argument("--repo-root", default=str(ROOT))
    paper_events.set_defaults(func=run_paper_event_ledger)

    paper_exec_review = sub.add_parser("paper-execution-review", help="Review synced paper execution quality")
    paper_exec_review.add_argument("--date", required=True)
    paper_exec_review.add_argument("--preview")
    paper_exec_review.add_argument("--execution-state")
    paper_exec_review.add_argument("--output")
    paper_exec_review.add_argument("--markdown-output")
    paper_exec_review.add_argument("--repo-root", default=str(ROOT))
    paper_exec_review.set_defaults(func=run_paper_execution_review)

    paper_strategy_review = sub.add_parser("paper-strategy-review", help="Aggregate paper execution reviews by setup and symbol")
    paper_strategy_review.add_argument("--review", action="append", default=[])
    paper_strategy_review.add_argument("--output")
    paper_strategy_review.add_argument("--markdown-output")
    paper_strategy_review.add_argument("--repo-root", default=str(ROOT))
    paper_strategy_review.set_defaults(func=run_paper_strategy_review)

    paper_lessons = sub.add_parser("paper-learning-lessons", help="Extract paper execution candidate lessons")
    paper_lessons.add_argument("--date", required=True)
    paper_lessons.add_argument("--review")
    paper_lessons.add_argument("--learning-dir", default="runtime/learning")
    paper_lessons.add_argument("--output")
    paper_lessons.add_argument("--append", action="store_true")
    paper_lessons.add_argument("--repo-root", default=str(ROOT))
    paper_lessons.set_defaults(func=run_paper_learning_lessons)

    paper_cancel = sub.add_parser("paper-order-cancel", help="Build a dry-run cancel plan for expired paper entry orders")
    paper_cancel.add_argument("--date", required=True)
    paper_cancel.add_argument("--state")
    paper_cancel.add_argument("--output")
    paper_cancel.add_argument("--expire-after-minutes", type=int, default=60)
    paper_cancel.add_argument("--now")
    paper_cancel.add_argument("--longbridge-cli")
    paper_cancel.add_argument("--execute", action="store_true")
    paper_cancel.add_argument("--paper-execution-config")
    paper_cancel.add_argument("--repo-root", default=str(ROOT))
    paper_cancel.set_defaults(func=run_paper_order_cancel)

    paper_stop = sub.add_parser("paper-protective-stop-plan", help="Build or submit guarded protective stops for filled paper entries")
    paper_stop.add_argument("--date", required=True)
    paper_stop.add_argument("--state")
    paper_stop.add_argument("--output")
    paper_stop.add_argument("--stops-journal")
    paper_stop.add_argument("--tif", default="gtc")
    paper_stop.add_argument("--longbridge-cli")
    paper_stop.add_argument("--execute", action="store_true")
    paper_stop.add_argument("--paper-execution-config")
    paper_stop.add_argument("--repo-root", default=str(ROOT))
    paper_stop.set_defaults(func=run_paper_protective_stop_plan)

    paper_tp = sub.add_parser("paper-take-profit-plan", help="Build or submit guarded TP1 partial exits for filled paper entries")
    paper_tp.add_argument("--date", required=True)
    paper_tp.add_argument("--state")
    paper_tp.add_argument("--output")
    paper_tp.add_argument("--take-profit-journal")
    paper_tp.add_argument("--exit-fraction", type=float, default=0.5)
    paper_tp.add_argument("--tif", default="gtc")
    paper_tp.add_argument("--longbridge-cli")
    paper_tp.add_argument("--execute", action="store_true")
    paper_tp.add_argument("--paper-execution-config")
    paper_tp.add_argument("--repo-root", default=str(ROOT))
    paper_tp.set_defaults(func=run_paper_take_profit_plan)

    paper_be = sub.add_parser("paper-break-even-stop-plan", help="Build a dry-run break-even stop movement plan")
    paper_be.add_argument("--date", required=True)
    paper_be.add_argument("--state")
    paper_be.add_argument("--stops-journal")
    paper_be.add_argument("--output")
    paper_be.add_argument("--buffer-pct", type=float, default=0.0)
    paper_be.add_argument("--tif", default="gtc")
    paper_be.add_argument("--repo-root", default=str(ROOT))
    paper_be.set_defaults(func=run_paper_break_even_stop_plan)

    position = sub.add_parser("position-review", help="Review read-only positions against structured signals")
    position.add_argument("--date", required=True)
    position.add_argument("--account-snapshot")
    position.add_argument("--signals")
    position.add_argument("--session", choices=["pre-market", "post-market"], default="pre-market")
    position.add_argument("--snapshot")
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
    sync.add_argument(
        "--require-validation",
        action="store_true",
        help="Validate the generated report and trade-plan sidecar before any Longbridge sync.",
    )
    sync.set_defaults(func=run_sync_longbridge_watchlist)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
