#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone
import hashlib
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


def load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def hash_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def hash_tree(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(str(child.relative_to(path)).encode("utf-8"))
        digest.update(child.read_bytes())
    return digest.hexdigest()


def git_output(*args: str) -> str | None:
    proc = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def git_dirty_files() -> List[str]:
    output = git_output("status", "--short")
    if not output:
        return []
    return [line for line in output.splitlines() if line.strip()]


def manifest_path(date: str, session: str, explicit: str | None = None) -> Path:
    if explicit:
        return resolve_repo_path(explicit)
    return ROOT / "report" / date / f"{session}-run-manifest.json"


def relative_artifact(path_text: str) -> str:
    path = Path(path_text)
    if not path.is_absolute():
        return path_text
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


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
    external_disclosures_path: str | None = None,
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
    if external_disclosures_path:
        reports_command.extend(["--external-disclosures", external_disclosures_path])
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


def run_external_disclosure_pipeline(
    *,
    date: str,
    symbols: List[str],
    input_path: str | None = None,
    lookback_days: int = 120,
) -> Dict[str, Any]:
    output = str(ROOT / "report" / date / "external-disclosures" / "trump-trades.json")
    command = [
        "script/external_disclosure_provider.py",
        "--date",
        date,
        "--output",
        output,
        "--lookback-days",
        str(lookback_days),
    ]
    if input_path:
        command.extend(["--input", input_path])
    for symbol in normalize_symbols(symbols):
        command.extend(["--symbol", symbol])
    proc = run_child(command)
    stdout = parse_json_output(proc.stdout) or {}
    artifacts = list(stdout.get("artifacts") or [])
    output_path = stdout.get("output")
    if output_path and output_path not in artifacts:
        artifacts.append(output_path)
    if proc.returncode != 0:
        return {
            "status": "failed",
            "artifacts": artifacts,
            "reason": proc.stderr.strip() or stdout.get("reason") or proc.stdout.strip(),
            "command": command,
            "stdout": stdout,
        }
    return {
        "status": "success",
        "artifacts": artifacts,
        "summary": stdout.get("summary", {}),
        "command": command,
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
        external_disclosures_path = None
        if getattr(args, "include_external_disclosures", False):
            disclosure_symbols = normalize_symbols(
                getattr(args, "external_disclosure_symbol", [])
                or getattr(args, "agent_symbol", [])
                or symbols_from_agent_source(context_path)
            )
            external_disclosures = run_external_disclosure_pipeline(
                date=str(stdout.get("report_date")),
                symbols=disclosure_symbols,
                input_path=getattr(args, "external_disclosure_input", None),
                lookback_days=int(getattr(args, "external_disclosure_lookback_days", 120)),
            )
            response["external_disclosures"] = external_disclosures
            response["artifacts"].extend(external_disclosures.get("artifacts", []))
            response["next_agent_inputs"].extend(external_disclosures.get("artifacts", []))
            if external_disclosures.get("artifacts"):
                external_disclosures_path = external_disclosures["artifacts"][0]
        if getattr(args, "include_agent_research", False):
            symbols = normalize_symbols(getattr(args, "agent_symbol", []) or symbols_from_agent_source(context_path))
            research = run_agent_research_pipeline(
                date=str(stdout.get("report_date")),
                symbols=symbols,
                context_path=context_path,
                external_disclosures_path=external_disclosures_path,
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
            f"report/{stdout.get('snapshot_date')}/intraday.md",
            f"runtime/intraday/{stdout.get('snapshot_date')}/state.json",
            f"runtime/intraday/{stdout.get('snapshot_date')}/events.jsonl",
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


def run_intraday_tracker(args: argparse.Namespace) -> None:
    command = [
        "script/intraday_tracker.py",
        "--manual-watchlist",
        args.manual_watchlist,
        "--monitor",
        args.monitor,
        "--top-n",
        str(args.top_n),
        "--timezone",
        args.timezone,
    ]
    if args.date:
        command.extend(["--date", args.date])
    if args.pre_market_signals:
        command.extend(["--pre-market-signals", args.pre_market_signals])
    if args.state:
        command.extend(["--state", args.state])
    if args.events:
        command.extend(["--events", args.events])
    if args.markdown:
        command.extend(["--markdown", args.markdown])
    if args.as_of:
        command.extend(["--as-of", args.as_of])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("intraday-tracker", command, proc), 1)

    response = base_response("intraday-tracker", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = (stdout or {}).get("artifacts", [])
    response["summary"] = (stdout or {}).get("summary", {})
    response["events"] = (stdout or {}).get("events", [])
    response["next_agent_inputs"] = [
        f"report/{response['date']}/intraday.md" if response.get("date") else "report/<DATE>/intraday.md",
        "knowledge/refined/",
    ]
    emit(response)


def run_intraday_dry_run(args: argparse.Namespace) -> None:
    extract_command: list[str] | None = None
    extract_stdout: dict[str, Any] | None = None
    if getattr(args, "signals", None):
        signals_path = args.signals
    else:
        extract_command = [
            "script/extract_monitor_signals.py",
            "--monitor",
            args.monitor,
            "--date",
            args.date,
            "--max-signals",
            str(args.max_signals),
            "--timezone",
            args.timezone,
        ]
        if args.signals_output:
            extract_command.extend(["--signals-output", args.signals_output])
        extract_proc = run_child(extract_command)
        extract_stdout = parse_json_output(extract_proc.stdout)
        if extract_proc.returncode != 0:
            emit(failed_response("intraday-dry-run", extract_command, extract_proc), 1)
        signals_path = (extract_stdout or {}).get("signals_path")
        if not signals_path:
            response = failed_response("intraday-dry-run", extract_command, extract_proc)
            response["reason"] = "extract-monitor-signals did not return signals_path"
            emit(response, 1)

    validate_command = [
        "script/validate_trade_plan.py",
        "--date",
        args.date,
        "--session",
        "monitor",
        "--signals",
        signals_path,
    ]
    validate_proc = run_child(validate_command)
    validate_stdout = parse_json_output(validate_proc.stdout)
    if validate_proc.returncode != 0:
        emit(failed_response("intraday-dry-run", validate_command, validate_proc), 1)

    preview_command = [
        "script/paper_trade_preview.py",
        "--date",
        args.date,
        "--session",
        "monitor",
        "--signals",
        signals_path,
        "--default-market",
        args.default_market,
        "--tif",
        args.tif,
        "--require-validation",
    ]
    if args.account_snapshot:
        preview_command.extend(["--account-snapshot", args.account_snapshot])
    if args.preview_output:
        preview_command.extend(["--output", args.preview_output])
    preview_proc = run_child(preview_command)
    preview_stdout = parse_json_output(preview_proc.stdout)
    if preview_proc.returncode != 0:
        emit(failed_response("intraday-dry-run", preview_command, preview_proc), 1)
    preview_path = (preview_stdout or {}).get("output") or args.preview_output

    submit_command = [
        "script/paper_trade_submit.py",
        "--date",
        args.date,
        "--session",
        "monitor",
        "--signals",
        signals_path,
        "--max-daily-risk-pct",
        str(args.max_daily_risk_pct),
        "--max-daily-orders",
        str(args.max_daily_orders),
        "--require-validation",
    ]
    if preview_path:
        submit_command.extend(["--preview", preview_path])
    if args.account_snapshot:
        submit_command.extend(["--account-snapshot", args.account_snapshot])
    if args.submit_output:
        submit_command.extend(["--output", args.submit_output])
    submit_proc = run_child(submit_command)
    submit_stdout = parse_json_output(submit_proc.stdout)
    if submit_proc.returncode != 0:
        emit(failed_response("intraday-dry-run", submit_command, submit_proc), 1)

    summary_command = [
        "script/feishu_summary.py",
        "--date",
        args.date,
        "--session",
        "monitor",
        "--signals",
        signals_path,
        "--learning-dir",
        args.learning_dir,
    ]
    if args.summary_output:
        summary_command.extend(["--output", args.summary_output])
    summary_proc = run_child(summary_command)
    summary_stdout = parse_json_output(summary_proc.stdout)
    if summary_proc.returncode != 0:
        emit(failed_response("intraday-dry-run", summary_command, summary_proc), 1)

    artifacts = [
        signals_path,
        (preview_stdout or {}).get("output"),
        (submit_stdout or {}).get("output"),
        (summary_stdout or {}).get("output"),
    ]
    response = base_response("intraday-dry-run", extract_command or validate_command, extract_stdout or {"status": "success"})
    response["date"] = args.date
    response["artifacts"] = [artifact for artifact in artifacts if artifact]
    response["dry_run"] = bool((submit_stdout or {}).get("dry_run", True))
    response["signals_path"] = signals_path
    response["validation"] = validate_stdout
    response["signals"] = (extract_stdout or {}).get("signals", [])
    response["preview_summary"] = (preview_stdout or {}).get("summary", {})
    response["submit_summary"] = (submit_stdout or {}).get("summary", {})
    response["feishu_summary"] = (summary_stdout or {}).get("summary", {})
    response["commands"] = {
        "extract": extract_command,
        "validate": validate_command,
        "preview": preview_command,
        "submit": submit_command,
        "feishu": summary_command,
    }
    emit(response)


def run_intraday_review_append(args: argparse.Namespace) -> None:
    command = [
        "script/intraday_review_append.py",
        "--date",
        args.date,
        "--timezone",
        args.timezone,
        "--max-notes-chars",
        str(args.max_notes_chars),
    ]
    if args.signals:
        command.extend(["--signals", args.signals])
    if args.submission:
        command.extend(["--submission", args.submission])
    if args.context:
        command.extend(["--context", args.context])
    if args.markdown:
        command.extend(["--markdown", args.markdown])
    if args.as_of:
        command.extend(["--as-of", args.as_of])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("intraday-review-append", command, proc), 1)
    response = base_response("intraday-review-append", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [(stdout or {}).get("markdown")] if (stdout or {}).get("markdown") else []
    response["signals"] = (stdout or {}).get("signals")
    response["summary"] = (stdout or {}).get("summary", {})
    response["skipped"] = (stdout or {}).get("status") == "skipped"
    response["reason"] = (stdout or {}).get("reason")
    emit(response)


def run_intraday_lifecycle_append(args: argparse.Namespace) -> None:
    command = [
        "script/intraday_lifecycle_append.py",
        "--date",
        args.date,
        "--timezone",
        args.timezone,
    ]
    if args.markdown:
        command.extend(["--markdown", args.markdown])
    if args.output:
        command.extend(["--output", args.output])
    if args.as_of:
        command.extend(["--as-of", args.as_of])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("intraday-lifecycle-append", command, proc), 1)
    response = base_response("intraday-lifecycle-append", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [
        artifact
        for artifact in [
            (stdout or {}).get("markdown"),
            (stdout or {}).get("output"),
        ]
        if artifact
    ]
    response["summary"] = (stdout or {}).get("summary", {})
    response["should_notify"] = bool((stdout or {}).get("should_notify"))
    response["skipped"] = (stdout or {}).get("status") == "skipped"
    response["reason"] = (stdout or {}).get("reason")
    emit(response)


def run_intraday_opportunity_context(args: argparse.Namespace) -> None:
    command = [
        "script/intraday_opportunity_context.py",
        "--date",
        args.date,
        "--monitor",
        args.monitor,
        "--max-candidates",
        str(args.max_candidates),
        "--markdown-chars",
        str(args.markdown_chars),
    ]
    if args.pre_market_signals:
        command.extend(["--pre-market-signals", args.pre_market_signals])
    if args.intraday_state:
        command.extend(["--intraday-state", args.intraday_state])
    if args.intraday_markdown:
        command.extend(["--intraday-markdown", args.intraday_markdown])
    if args.paper_state:
        command.extend(["--paper-state", args.paper_state])
    if args.output:
        command.extend(["--output", args.output])
    if args.signals_output:
        command.extend(["--signals-output", args.signals_output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("intraday-opportunity-context", command, proc), 1)
    response = base_response("intraday-opportunity-context", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [artifact for artifact in [(stdout or {}).get("output")] if artifact]
    response["signals_output"] = (stdout or {}).get("signals_output")
    response["summary"] = (stdout or {}).get("summary", {})
    emit(response)


def run_intraday_paper_entry(args: argparse.Namespace) -> None:
    command = [
        "script/intraday_paper_entry.py",
        "--date",
        args.date,
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
    if args.require_validation:
        command.append("--require-validation")
    if args.execute:
        command.append("--execute")
    if args.paper_execution_config:
        command.extend(["--paper-execution-config", args.paper_execution_config])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("intraday-paper-entry", command, proc), 1)

    response = base_response("intraday-paper-entry", command, stdout)
    response["date"] = (stdout or {}).get("date") or args.date
    response["artifacts"] = [(stdout or {}).get("output")] if (stdout or {}).get("output") else []
    response["dry_run"] = bool((stdout or {}).get("dry_run", not args.execute))
    response["summary"] = (stdout or {}).get("summary", {})
    response["safety_note"] = (stdout or {}).get("safety_note")
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
        if args.external_disclosures:
            command.extend(["--external-disclosures", args.external_disclosures])
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
    if getattr(args, "signals_output", None):
        command.extend(["--signals-output", args.signals_output])
    if args.require_validation:
        command.append("--require-validation")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("extract-report-signals", command, proc), 1)

    response = base_response("extract-report-signals", command, stdout)
    response["date"] = args.date
    artifacts = []
    if stdout and stdout.get("signals_path"):
        artifacts.append(stdout["signals_path"])
    if stdout and stdout.get("journal_path"):
        artifacts.append(stdout["journal_path"])
    response["artifacts"] = artifacts
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
    if getattr(args, "run_manifest", None):
        command.extend(["--run-manifest", args.run_manifest])
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


def run_focus_selection(args: argparse.Namespace) -> None:
    command = [
        "script/focus_selection.py",
        "--date",
        args.date,
        "--session",
        args.session,
    ]
    if args.signals:
        command.extend(["--signals", args.signals])
    if args.context:
        command.extend(["--context", args.context])
    if args.snapshot:
        command.extend(["--snapshot", args.snapshot])
    if args.agents_dir:
        command.extend(["--agents-dir", args.agents_dir])
    if args.output:
        command.extend(["--output", args.output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("focus-selection", command, proc), 1)

    response = base_response("focus-selection", command, stdout)
    response["date"] = args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["selected"] = (stdout or {}).get("selected")
    emit(response)


def run_inspect_pre_market_context(args: argparse.Namespace) -> None:
    command = ["script/inspect_pre_market_context.py", "--date", args.date]
    if args.context:
        command.extend(["--context", args.context])
    if args.output:
        command.extend(["--output", args.output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("inspect-pre-market-context", command, proc), 1)

    response = base_response("inspect-pre-market-context", command, stdout)
    response["date"] = args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
    response["summary"] = stdout
    emit(response)


def run_llm_generation_manifest(args: argparse.Namespace) -> None:
    command = [
        "script/llm_generation_manifest.py",
        "--date",
        args.date,
        "--session",
        args.session,
        "--model",
        args.model,
        "--runner",
        args.runner,
    ]
    if args.prompt:
        command.extend(["--prompt", args.prompt])
    for item in args.input or []:
        command.extend(["--input", item])
    for item in args.generated_output or []:
        command.extend(["--generated-output", item])
    if args.notes:
        command.extend(["--notes", args.notes])
    if args.output:
        command.extend(["--output", args.output])

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("llm-generation-manifest", command, proc), 1)

    response = base_response("llm-generation-manifest", command, stdout)
    response["date"] = args.date
    response["artifacts"] = [stdout["output"]] if stdout and stdout.get("output") else []
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


def symbols_from_signals(signals_path: Path) -> List[str]:
    if not signals_path.exists():
        return []
    payload = load_json(signals_path)
    symbols = []
    for item in payload.get("signals", []):
        if isinstance(item, dict) and item.get("symbol") and item.get("status") != "no_trade":
            symbols.append(str(item["symbol"]))
    return normalize_symbols(symbols)


def run_manifest_step(
    *,
    manifest: Dict[str, Any],
    name: str,
    command: List[str],
    allow_failure: bool = False,
) -> tuple[bool, Dict[str, Any]]:
    started_at = now_utc()
    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    finished_at = now_utc()
    status = "success" if proc.returncode == 0 else "failed"
    if isinstance(stdout, dict):
        if stdout.get("status") in {"failed", "fail"}:
            status = "failed"
        elif stdout.get("status") in {"success", "pass"} and proc.returncode == 0:
            status = "success"
    step = {
        "name": name,
        "command": [sys.executable, *command],
        "started_at": started_at,
        "finished_at": finished_at,
        "returncode": proc.returncode,
        "status": status,
        "artifacts": [
            relative_artifact(item)
            for item in sorted(
                dict.fromkeys(
                    [
                        *((stdout or {}).get("artifacts", []) or []),
                        *([stdout.get("output")] if isinstance(stdout, dict) and stdout.get("output") else []),
                    ]
                )
            )
        ],
        "stdout": stdout,
        "stderr": proc.stderr.strip(),
        "allow_failure": allow_failure,
    }
    manifest.setdefault("steps", []).append(step)
    return (status == "success" or allow_failure), step


def manifest_base(date: str, session: str, workflow: str, args: argparse.Namespace) -> Dict[str, Any]:
    context_path = ROOT / "report" / date / "pre-market-context.json"
    signals_path = resolve_repo_path(args.signals) if getattr(args, "signals", None) else ROOT / "report" / date / f"{session}-signals.json"
    snapshot_path = ROOT / "report" / date / "daily-snapshot.json"
    context = load_json(context_path) if context_path.exists() else {}
    llm_manifest = ROOT / "report" / date / f"{session}-llm-generation.json"
    return {
        "schema_version": 1,
        "workflow": workflow,
        "session": session,
        "date": date,
        "generated_at": now_utc(),
        "repo_root": str(ROOT),
        "git_sha": git_output("rev-parse", "HEAD"),
        "branch": git_output("branch", "--show-current"),
        "dirty_files": git_dirty_files(),
        "hashes": {
            "watchlist": hash_file(resolve_repo_path(getattr(args, "watchlist", "config/watchlist.json"))),
            "daily_prompt": hash_file(ROOT / "agent" / "daily_analysis_prompt.md"),
            "post_market_prompt": hash_file(ROOT / "agent" / "post_market_analysis_prompt.md"),
            "knowledge_refined": hash_tree(ROOT / "knowledge" / "refined"),
            "signals": hash_file(signals_path),
            "llm_generation": hash_file(llm_manifest),
        },
        "context": {
            "path": relative_artifact(str(context_path)),
            "source_snapshot_date": context.get("source_snapshot_date"),
            "source_snapshot_path": context.get("source_snapshot_path"),
            "snapshot_path": relative_artifact(str(snapshot_path)),
        },
        "artifacts": [],
        "steps": [],
    }


def write_manifest(manifest: Dict[str, Any], path: Path) -> None:
    artifacts = []
    for step in manifest.get("steps", []):
        if isinstance(step, dict):
            artifacts.extend(step.get("artifacts", []) or [])
    manifest["artifacts"] = sorted(dict.fromkeys(artifacts))
    write_json(path, manifest)


def run_pre_market_deliver(args: argparse.Namespace) -> None:
    date = args.date
    session = "pre-market"
    signals_path = resolve_repo_path(args.signals) if args.signals else ROOT / "report" / date / "pre-market-signals.json"
    manifest_file = manifest_path(date, session, args.manifest_output)
    manifest = manifest_base(date, session, "pre-market-deliver", args)
    focus = symbols_from_signals(signals_path)
    manifest["focused_symbols"] = focus

    if not signals_path.exists():
        manifest["status"] = "failed"
        manifest["reason"] = f"missing signals sidecar: {signals_path}"
        write_manifest(manifest, manifest_file)
        emit(
            {
                "status": "failed",
                "workflow": "pre-market-deliver",
                "date": date,
                "artifacts": [str(manifest_file)],
                "skipped": False,
                "reason": manifest["reason"],
            },
            1,
        )

    if focus and not args.skip_agent_validation:
        agents_dir = ROOT / "report" / date / "agents"
        if any((agents_dir / symbol / "decision.json").exists() for symbol in focus):
            validate_reports = ["script/validate_agent_reports.py", "--date", date]
            validate_decision = ["script/validate_agent_decision.py", "--date", date]
            for symbol in focus:
                validate_reports.extend(["--symbol", symbol])
                validate_decision.extend(["--symbol", symbol])
            ok, _ = run_manifest_step(manifest=manifest, name="validate-agent-reports", command=validate_reports)
            if not ok:
                manifest["status"] = "failed"
                manifest["reason"] = "validate-agent-reports failed"
                write_manifest(manifest, manifest_file)
                emit(manifest | {"artifacts": [str(manifest_file)]}, 1)
            ok, _ = run_manifest_step(manifest=manifest, name="validate-agent-decision", command=validate_decision)
            if not ok:
                manifest["status"] = "failed"
                manifest["reason"] = "validate-agent-decision failed"
                write_manifest(manifest, manifest_file)
                emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    validate_report = ["script/validate_report.py", "--date", date, "--session", session]
    validate_plan = ["script/validate_trade_plan.py", "--date", date, "--session", session]
    if args.report:
        validate_report.extend(["--report", args.report])
    if args.signals:
        validate_report.extend(["--signals", args.signals])
        validate_plan.extend(["--signals", args.signals])
    for name, command in (("validate-report", validate_report), ("validate-trade-plan", validate_plan)):
        ok, _ = run_manifest_step(manifest=manifest, name=name, command=command)
        if not ok:
            manifest["status"] = "failed"
            manifest["reason"] = f"{name} failed"
            write_manifest(manifest, manifest_file)
            emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    data_quality = ["script/data_quality.py", "--date", date, "--session", session]
    ok, dq_step = run_manifest_step(manifest=manifest, name="data-quality", command=data_quality)
    quality_status = ((dq_step.get("stdout") or {}).get("quality_status") if isinstance(dq_step.get("stdout"), dict) else None)
    if not ok or quality_status == "fail":
        manifest["status"] = "failed"
        manifest["reason"] = "data-quality failed"
        write_manifest(manifest, manifest_file)
        emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    focus_selection = ["script/focus_selection.py", "--date", date, "--session", session]
    if args.signals:
        focus_selection.extend(["--signals", args.signals])
    ok, focus_step = run_manifest_step(manifest=manifest, name="focus-selection", command=focus_selection)
    if not ok:
        manifest["status"] = "failed"
        manifest["reason"] = "focus-selection failed"
        write_manifest(manifest, manifest_file)
        emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    extract = [
        "script/extract_report_signals.py",
        "--date",
        date,
        "--session",
        session,
        "--journal-dir",
        args.journal_dir,
        "--require-validation",
    ]
    if args.signals:
        extract.extend(["--signals", args.signals])
    if args.report:
        extract.extend(["--report", args.report])
    if not args.no_append_journal:
        extract.append("--append")
    ok, _ = run_manifest_step(manifest=manifest, name="extract-report-signals", command=extract)
    if not ok:
        manifest["status"] = "failed"
        manifest["reason"] = "extract-report-signals failed"
        write_manifest(manifest, manifest_file)
        emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    account_snapshot_path = args.account_snapshot
    if not args.skip_account:
        account = ["script/longbridge_account_snapshot.py"]
        if args.date:
            account.extend(["--date", date])
        if args.longbridge_cli:
            account.extend(["--longbridge-cli", args.longbridge_cli])
        ok, account_step = run_manifest_step(manifest=manifest, name="account-snapshot", command=account, allow_failure=True)
        stdout = account_step.get("stdout") if isinstance(account_step.get("stdout"), dict) else {}
        if isinstance(stdout, dict):
            account_snapshot_path = stdout.get("output") or account_snapshot_path
        position = ["script/position_review.py", "--date", date, "--append", "--journal-dir", args.journal_dir]
        if account_snapshot_path:
            position.extend(["--account-snapshot", account_snapshot_path])
        if args.signals:
            position.extend(["--signals", args.signals])
        position.extend(["--session", session])
        if args.position_config:
            position.extend(["--config", args.position_config])
        run_manifest_step(manifest=manifest, name="position-review", command=position, allow_failure=True)

    if args.sync_longbridge:
        sync = [
            "script/sync_longbridge_watchlist.py",
            "--session",
            session,
            "--date",
            date,
            "--group-name",
            args.group_name,
            "--sync-mode",
            args.sync_mode,
            "--max-symbols",
            str(args.max_symbols),
            "--method",
            args.sync_method,
            "--no-create",
        ]
        if args.signals:
            sync.extend(["--signals", args.signals])
        if args.report:
            sync.extend(["--report", args.report])
        if args.execute_sync:
            sync.append("--execute")
        if args.longbridge_cli:
            sync.extend(["--longbridge-cli", args.longbridge_cli])
        ok, _ = run_manifest_step(manifest=manifest, name="sync-longbridge-watchlist", command=sync)
        if not ok:
            manifest["status"] = "failed"
            manifest["reason"] = "sync-longbridge-watchlist failed"
            write_manifest(manifest, manifest_file)
            emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    manifest["status"] = "success"
    manifest["reason"] = None
    write_manifest(manifest, manifest_file)

    feishu = ["script/feishu_summary.py", "--date", date, "--session", session, "--run-manifest", str(manifest_file), "--learning-dir", args.learning_dir]
    if args.signals:
        feishu.extend(["--signals", args.signals])
    if args.summary_output:
        feishu.extend(["--output", args.summary_output])
    ok, summary_step = run_manifest_step(manifest=manifest, name="feishu-summary", command=feishu)
    if not ok:
        manifest["status"] = "failed"
        manifest["reason"] = "feishu-summary failed"
        write_manifest(manifest, manifest_file)
        emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    if args.delivery_guard:
        guard = ["script/report_delivery_guard.py", "--kind", args.delivery_kind, "--date", date]
        if args.mark_sent:
            guard.append("--mark-sent")
        run_manifest_step(manifest=manifest, name="delivery-guard", command=guard, allow_failure=True)

    manifest["status"] = "success"
    manifest["reason"] = None
    write_manifest(manifest, manifest_file)
    response = base_response("pre-market-deliver", ["pre-market-deliver"], manifest)
    response["date"] = date
    response["artifacts"] = [str(manifest_file)]
    stdout = summary_step.get("stdout") if isinstance(summary_step.get("stdout"), dict) else {}
    if isinstance(stdout, dict) and stdout.get("output"):
        response["artifacts"].append(stdout["output"])
    response["manifest"] = str(manifest_file)
    response["focused_symbols"] = focus
    response["quality_status"] = quality_status
    stdout = focus_step.get("stdout") if isinstance(focus_step.get("stdout"), dict) else {}
    response["focus_selection"] = stdout.get("output") if isinstance(stdout, dict) else None
    emit(response)


def run_post_market_deliver(args: argparse.Namespace) -> None:
    date = args.date
    session = "post-market"
    signals_path = resolve_repo_path(args.signals) if args.signals else ROOT / "report" / date / "post-market-signals.json"
    manifest_file = manifest_path(date, session, args.manifest_output)
    manifest = manifest_base(date, session, "post-market-deliver", args)
    focus = symbols_from_signals(signals_path)
    manifest["focused_symbols"] = focus

    if not signals_path.exists():
        manifest["status"] = "failed"
        manifest["reason"] = f"missing signals sidecar: {signals_path}"
        write_manifest(manifest, manifest_file)
        emit(
            {
                "status": "failed",
                "workflow": "post-market-deliver",
                "date": date,
                "artifacts": [str(manifest_file)],
                "skipped": False,
                "reason": manifest["reason"],
            },
            1,
        )

    if focus and not args.skip_agent_validation:
        agents_dir = ROOT / "report" / date / "agents"
        if any((agents_dir / symbol / "decision.json").exists() for symbol in focus):
            validate_reports = ["script/validate_agent_reports.py", "--date", date]
            validate_decision = ["script/validate_agent_decision.py", "--date", date]
            for symbol in focus:
                validate_reports.extend(["--symbol", symbol])
                validate_decision.extend(["--symbol", symbol])
            ok, _ = run_manifest_step(manifest=manifest, name="validate-agent-reports", command=validate_reports)
            if not ok:
                manifest["status"] = "failed"
                manifest["reason"] = "validate-agent-reports failed"
                write_manifest(manifest, manifest_file)
                emit(manifest | {"artifacts": [str(manifest_file)]}, 1)
            ok, _ = run_manifest_step(manifest=manifest, name="validate-agent-decision", command=validate_decision)
            if not ok:
                manifest["status"] = "failed"
                manifest["reason"] = "validate-agent-decision failed"
                write_manifest(manifest, manifest_file)
                emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    validate_report = ["script/validate_report.py", "--date", date, "--session", session]
    validate_plan = ["script/validate_trade_plan.py", "--date", date, "--session", session]
    if args.report:
        validate_report.extend(["--report", args.report])
    if args.signals:
        validate_report.extend(["--signals", args.signals])
        validate_plan.extend(["--signals", args.signals])
    for name, command in (("validate-report", validate_report), ("validate-trade-plan", validate_plan)):
        ok, _ = run_manifest_step(manifest=manifest, name=name, command=command)
        if not ok:
            manifest["status"] = "failed"
            manifest["reason"] = f"{name} failed"
            write_manifest(manifest, manifest_file)
            emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    data_quality = ["script/data_quality.py", "--date", date, "--session", session]
    ok, dq_step = run_manifest_step(manifest=manifest, name="data-quality", command=data_quality)
    quality_status = ((dq_step.get("stdout") or {}).get("quality_status") if isinstance(dq_step.get("stdout"), dict) else None)
    if not ok or quality_status == "fail":
        manifest["status"] = "failed"
        manifest["reason"] = "data-quality failed"
        write_manifest(manifest, manifest_file)
        emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    if not args.skip_outcomes:
        outcomes = ["script/journal_review.py", "--date", date, "--journal-dir", args.journal_dir]
        if args.snapshot:
            outcomes.extend(["--snapshot", args.snapshot])
        if args.append_outcomes:
            outcomes.append("--append")
        ok, _ = run_manifest_step(manifest=manifest, name="backfill-signal-outcomes", command=outcomes)
        if not ok:
            manifest["status"] = "failed"
            manifest["reason"] = "backfill-signal-outcomes failed"
            write_manifest(manifest, manifest_file)
            emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    focus_selection = ["script/focus_selection.py", "--date", date, "--session", session]
    if args.signals:
        focus_selection.extend(["--signals", args.signals])
    if args.snapshot:
        focus_selection.extend(["--snapshot", args.snapshot])
    ok, focus_step = run_manifest_step(manifest=manifest, name="focus-selection", command=focus_selection)
    if not ok:
        manifest["status"] = "failed"
        manifest["reason"] = "focus-selection failed"
        write_manifest(manifest, manifest_file)
        emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    extract = [
        "script/extract_report_signals.py",
        "--date",
        date,
        "--session",
        session,
        "--journal-dir",
        args.journal_dir,
        "--require-validation",
    ]
    if args.signals:
        extract.extend(["--signals", args.signals])
    if args.report:
        extract.extend(["--report", args.report])
    if not args.no_append_journal:
        extract.append("--append")
    ok, _ = run_manifest_step(manifest=manifest, name="extract-report-signals", command=extract)
    if not ok:
        manifest["status"] = "failed"
        manifest["reason"] = "extract-report-signals failed"
        write_manifest(manifest, manifest_file)
        emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    account_snapshot_path = args.account_snapshot
    if not args.skip_account:
        account = ["script/longbridge_account_snapshot.py"]
        account.extend(["--date", date])
        if args.longbridge_cli:
            account.extend(["--longbridge-cli", args.longbridge_cli])
        ok, account_step = run_manifest_step(manifest=manifest, name="account-snapshot", command=account, allow_failure=True)
        stdout = account_step.get("stdout") if isinstance(account_step.get("stdout"), dict) else {}
        if isinstance(stdout, dict):
            account_snapshot_path = stdout.get("output") or account_snapshot_path
        position = ["script/position_review.py", "--date", date, "--append", "--journal-dir", args.journal_dir, "--session", session]
        if account_snapshot_path:
            position.extend(["--account-snapshot", account_snapshot_path])
        if args.signals:
            position.extend(["--signals", args.signals])
        if args.position_config:
            position.extend(["--config", args.position_config])
        run_manifest_step(manifest=manifest, name="position-review", command=position, allow_failure=True)

    if not args.skip_plan_review:
        plan_review = ["script/plan_review.py", "--date", date, "--journal-dir", args.journal_dir, "--learning-dir", args.learning_dir]
        if args.append_lessons:
            plan_review.append("--append-lessons")
        run_manifest_step(manifest=manifest, name="plan-review", command=plan_review, allow_failure=True)

    if not args.skip_learning_review:
        learning_review = [
            "script/learning_review.py",
            "--lookback-days",
            str(args.learning_lookback_days),
            "--learning-dir",
            args.learning_dir,
            "--journal-dir",
            args.journal_dir,
        ]
        run_manifest_step(manifest=manifest, name="learning-review", command=learning_review, allow_failure=True)

    if not args.skip_self_review:
        self_review = ["script/daily_self_review.py", "--date", date, "--journal-dir", args.journal_dir]
        if args.append_self_review:
            self_review.append("--append")
        run_manifest_step(manifest=manifest, name="daily-self-review", command=self_review, allow_failure=True)

    if args.sync_longbridge:
        sync = [
            "script/sync_longbridge_watchlist.py",
            "--session",
            session,
            "--date",
            date,
            "--group-name",
            args.group_name,
            "--sync-mode",
            args.sync_mode,
            "--max-symbols",
            str(args.max_symbols),
            "--method",
            args.sync_method,
            "--no-create",
        ]
        if args.signals:
            sync.extend(["--signals", args.signals])
        if args.report:
            sync.extend(["--report", args.report])
        if args.execute_sync:
            sync.append("--execute")
        if args.longbridge_cli:
            sync.extend(["--longbridge-cli", args.longbridge_cli])
        ok, _ = run_manifest_step(manifest=manifest, name="sync-longbridge-watchlist", command=sync)
        if not ok:
            manifest["status"] = "failed"
            manifest["reason"] = "sync-longbridge-watchlist failed"
            write_manifest(manifest, manifest_file)
            emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    manifest["status"] = "success"
    manifest["reason"] = None
    write_manifest(manifest, manifest_file)

    feishu = ["script/feishu_summary.py", "--date", date, "--session", session, "--run-manifest", str(manifest_file), "--learning-dir", args.learning_dir]
    if args.signals:
        feishu.extend(["--signals", args.signals])
    if args.summary_output:
        feishu.extend(["--output", args.summary_output])
    ok, summary_step = run_manifest_step(manifest=manifest, name="feishu-summary", command=feishu)
    if not ok:
        manifest["status"] = "failed"
        manifest["reason"] = "feishu-summary failed"
        write_manifest(manifest, manifest_file)
        emit(manifest | {"artifacts": [str(manifest_file)]}, 1)

    if args.delivery_guard:
        guard = ["script/report_delivery_guard.py", "--kind", "post-market", "--date", date]
        if args.mark_sent:
            guard.append("--mark-sent")
        run_manifest_step(manifest=manifest, name="delivery-guard", command=guard, allow_failure=True)

    manifest["status"] = "success"
    manifest["reason"] = None
    write_manifest(manifest, manifest_file)
    response = base_response("post-market-deliver", ["post-market-deliver"], manifest)
    response["date"] = date
    response["artifacts"] = [str(manifest_file)]
    stdout = summary_step.get("stdout") if isinstance(summary_step.get("stdout"), dict) else {}
    if isinstance(stdout, dict) and stdout.get("output"):
        response["artifacts"].append(stdout["output"])
    response["manifest"] = str(manifest_file)
    response["focused_symbols"] = focus
    response["quality_status"] = quality_status
    stdout = focus_step.get("stdout") if isinstance(focus_step.get("stdout"), dict) else {}
    response["focus_selection"] = stdout.get("output") if isinstance(stdout, dict) else None
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
    if args.signals_output:
        command.extend(["--signals-output", args.signals_output])

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
    if args.exits_journal:
        command.extend(["--exits-journal", args.exits_journal])
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
    if args.exits_journal:
        command.extend(["--exits-journal", args.exits_journal])
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


def run_lifecycle_child(
    command: list[str],
    *,
    workflow: str,
    steps: dict[str, Any],
    artifacts: list[str],
    commands: dict[str, list[str]],
) -> dict[str, Any]:
    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    commands[workflow] = command
    if proc.returncode != 0:
        raise RuntimeError(json.dumps(failed_response(workflow, command, proc), ensure_ascii=False))
    payload = stdout or {}
    steps[workflow] = payload
    for key in ("output", "markdown", "events_journal"):
        value = payload.get(key)
        if value and value not in artifacts:
            artifacts.append(value)
    return payload


def run_paper_lifecycle(args: argparse.Namespace) -> None:
    steps: dict[str, Any] = {}
    artifacts: list[str] = []
    commands: dict[str, list[str]] = {}

    def account_snapshot() -> dict[str, Any]:
        command = [
            "script/paper_account_snapshot.py",
            "--date",
            args.date,
            "--repo-root",
            args.repo_root,
        ]
        if args.paper_account_input:
            command.extend(["--input", args.paper_account_input])
        if args.longbridge_cli:
            command.extend(["--longbridge-cli", args.longbridge_cli])
        return run_lifecycle_child(command, workflow="paper_account_snapshot", steps=steps, artifacts=artifacts, commands=commands)

    def order_sync() -> dict[str, Any]:
        return run_lifecycle_child(
            [
                "script/paper_order_sync.py",
                "--date",
                args.date,
                "--repo-root",
                args.repo_root,
            ],
            workflow="paper_order_sync",
            steps=steps,
            artifacts=artifacts,
            commands=commands,
        )

    def exit_plan(
        *,
        workflow: str,
        script: str,
        execute: bool,
        extra: list[str],
    ) -> dict[str, Any]:
        command = [
            script,
            "--date",
            args.date,
            "--repo-root",
            args.repo_root,
            *extra,
        ]
        if args.longbridge_cli:
            command.extend(["--longbridge-cli", args.longbridge_cli])
        if args.paper_execution_config:
            command.extend(["--paper-execution-config", args.paper_execution_config])
        if execute:
            command.append("--execute")
        return run_lifecycle_child(command, workflow=workflow, steps=steps, artifacts=artifacts, commands=commands)

    try:
        account_snapshot()
        first_sync = order_sync()
        cancel = exit_plan(
            workflow="paper_order_cancel",
            script="script/paper_order_cancel.py",
            execute=bool(args.execute_cancel),
            extra=["--expire-after-minutes", str(args.expire_after_minutes)],
        )
        account_snapshot()
        second_sync = order_sync()
        stop = exit_plan(
            workflow="paper_protective_stop_plan",
            script="script/paper_protective_stop_plan.py",
            execute=bool(args.execute_protective_stop),
            extra=[
                "--order-type",
                getattr(args, "stop_order_type", "MIT"),
                "--tif",
                args.stop_tif,
                *(
                    ["--limit-price", str(args.stop_limit_price)]
                    if getattr(args, "stop_limit_price", None) is not None
                    else []
                ),
                *(
                    ["--trigger-price", str(args.stop_trigger_price)]
                    if getattr(args, "stop_trigger_price", None) is not None
                    else []
                ),
                *(
                    ["--trailing-amount", str(args.stop_trailing_amount)]
                    if getattr(args, "stop_trailing_amount", None) is not None
                    else []
                ),
                *(
                    ["--trailing-percent", str(args.stop_trailing_percent)]
                    if getattr(args, "stop_trailing_percent", None) is not None
                    else []
                ),
                *(
                    ["--limit-offset", str(args.stop_limit_offset)]
                    if getattr(args, "stop_limit_offset", None) is not None
                    else []
                ),
                *(["--expire-date", args.stop_expire_date] if getattr(args, "stop_expire_date", None) else []),
                *(["--outside-rth", args.stop_outside_rth] if getattr(args, "stop_outside_rth", None) else []),
            ],
        )
        take_profit = exit_plan(
            workflow="paper_take_profit_plan",
            script="script/paper_take_profit_plan.py",
            execute=bool(args.execute_take_profit),
            extra=[
                "--exit-fraction",
                str(args.exit_fraction),
                "--order-type",
                getattr(args, "take_profit_order_type", "LO"),
                "--tif",
                args.take_profit_tif,
                *(
                    ["--limit-price", str(args.take_profit_limit_price)]
                    if getattr(args, "take_profit_limit_price", None) is not None
                    else []
                ),
                *(
                    ["--trigger-price", str(args.take_profit_trigger_price)]
                    if getattr(args, "take_profit_trigger_price", None) is not None
                    else []
                ),
                *(
                    ["--trailing-amount", str(args.take_profit_trailing_amount)]
                    if getattr(args, "take_profit_trailing_amount", None) is not None
                    else []
                ),
                *(
                    ["--trailing-percent", str(args.take_profit_trailing_percent)]
                    if getattr(args, "take_profit_trailing_percent", None) is not None
                    else []
                ),
                *(
                    ["--limit-offset", str(args.take_profit_limit_offset)]
                    if getattr(args, "take_profit_limit_offset", None) is not None
                    else []
                ),
                *(["--expire-date", args.take_profit_expire_date] if getattr(args, "take_profit_expire_date", None) else []),
                *(["--outside-rth", args.take_profit_outside_rth] if getattr(args, "take_profit_outside_rth", None) else []),
                *(["--resize-stop-before-submit"] if args.resize_stop_before_take_profit else []),
            ],
        )
        exit_position = exit_plan(
            workflow="paper_exit_plan",
            script="script/paper_exit_plan.py",
            execute=bool(args.execute_exit),
            extra=[
                "--order-type",
                args.exit_order_type,
                "--tif",
                args.exit_tif,
                *(
                    ["--limit-price", str(args.exit_limit_price)]
                    if getattr(args, "exit_limit_price", None) is not None
                    else []
                ),
                *(
                    ["--trigger-price", str(args.exit_trigger_price)]
                    if getattr(args, "exit_trigger_price", None) is not None
                    else []
                ),
                *(
                    ["--trailing-amount", str(args.exit_trailing_amount)]
                    if getattr(args, "exit_trailing_amount", None) is not None
                    else []
                ),
                *(
                    ["--trailing-percent", str(args.exit_trailing_percent)]
                    if getattr(args, "exit_trailing_percent", None) is not None
                    else []
                ),
                *(
                    ["--limit-offset", str(args.exit_limit_offset)]
                    if getattr(args, "exit_limit_offset", None) is not None
                    else []
                ),
                *(["--expire-date", args.exit_expire_date] if getattr(args, "exit_expire_date", None) else []),
                *(["--outside-rth", args.exit_outside_rth] if getattr(args, "exit_outside_rth", None) else []),
                "--decisions",
                f"report/{args.date}/paper-exit-decisions.json",
            ],
        )
        break_even = exit_plan(
            workflow="paper_break_even_stop_plan",
            script="script/paper_break_even_stop_plan.py",
            execute=bool(args.execute_break_even_stop),
            extra=[
                "--order-type",
                getattr(args, "break_even_order_type", "MIT"),
                "--tif",
                args.break_even_tif,
                *(
                    ["--limit-price", str(args.break_even_limit_price)]
                    if getattr(args, "break_even_limit_price", None) is not None
                    else []
                ),
                *(
                    ["--trigger-price", str(args.break_even_trigger_price)]
                    if getattr(args, "break_even_trigger_price", None) is not None
                    else []
                ),
                *(
                    ["--trailing-amount", str(args.break_even_trailing_amount)]
                    if getattr(args, "break_even_trailing_amount", None) is not None
                    else []
                ),
                *(
                    ["--trailing-percent", str(args.break_even_trailing_percent)]
                    if getattr(args, "break_even_trailing_percent", None) is not None
                    else []
                ),
                *(
                    ["--limit-offset", str(args.break_even_limit_offset)]
                    if getattr(args, "break_even_limit_offset", None) is not None
                    else []
                ),
                *(["--expire-date", args.break_even_expire_date] if getattr(args, "break_even_expire_date", None) else []),
                *(["--outside-rth", args.break_even_outside_rth] if getattr(args, "break_even_outside_rth", None) else []),
            ],
        )
        account_snapshot()
        final_sync = order_sync()
        ledger = run_lifecycle_child(
            [
                "script/paper_event_ledger.py",
                "--date",
                args.date,
                "--repo-root",
                args.repo_root,
            ],
            workflow="paper_event_ledger",
            steps=steps,
            artifacts=artifacts,
            commands=commands,
        )
        review = run_lifecycle_child(
            [
                "script/paper_execution_review.py",
                "--date",
                args.date,
                "--repo-root",
                args.repo_root,
            ],
            workflow="paper_execution_review",
            steps=steps,
            artifacts=artifacts,
            commands=commands,
        )
        lessons = None
        if args.append_lessons:
            lessons_command = [
                "script/paper_learning_lessons.py",
                "--date",
                args.date,
                "--repo-root",
                args.repo_root,
                "--learning-dir",
                args.learning_dir,
                "--append",
            ]
            lessons = run_lifecycle_child(
                lessons_command,
                workflow="paper_learning_lessons",
                steps=steps,
                artifacts=artifacts,
                commands=commands,
            )
        strategy = None
        if args.strategy_review:
            strategy = run_lifecycle_child(
                [
                    "script/paper_strategy_review.py",
                    "--repo-root",
                    args.repo_root,
                ],
                workflow="paper_strategy_review",
                steps=steps,
                artifacts=artifacts,
                commands=commands,
            )
    except RuntimeError as exc:
        try:
            payload = json.loads(str(exc))
        except json.JSONDecodeError:
            payload = {"status": "failed", "workflow": "paper-lifecycle", "reason": str(exc)}
        emit(payload, 1)

    summary = {
        "paper_order_sync": (final_sync or second_sync or first_sync or {}).get("summary", {}),
        "paper_order_cancel": (cancel or {}).get("summary", {}),
        "paper_protective_stop_plan": (stop or {}).get("summary", {}),
        "paper_take_profit_plan": (take_profit or {}).get("summary", {}),
        "paper_exit_plan": (exit_position or {}).get("summary", {}),
        "paper_break_even_stop_plan": (break_even or {}).get("summary", {}),
        "paper_event_ledger": (ledger or {}).get("summary", {}),
        "paper_execution_review": (review or {}).get("summary", {}),
    }
    if lessons:
        summary["paper_learning_lessons"] = lessons.get("summary", {})
    if strategy:
        summary["paper_strategy_review"] = strategy.get("summary", {})

    response = {
        "status": "success",
        "workflow": "paper-lifecycle",
        "date": args.date,
        "artifacts": artifacts,
        "skipped": False,
        "reason": None,
        "dry_run": not any(
            [
                args.execute_cancel,
                args.execute_protective_stop,
                args.execute_take_profit,
                args.execute_exit,
                args.execute_break_even_stop,
            ]
        ),
        "execute_requested": {
            "cancel": bool(args.execute_cancel),
            "protective_stop": bool(args.execute_protective_stop),
            "take_profit": bool(args.execute_take_profit),
            "exit": bool(args.execute_exit),
            "break_even_stop": bool(args.execute_break_even_stop),
        },
        "steps": steps,
        "summary": summary,
        "commands": commands,
        "safety_note": "Paper lifecycle orchestration only; broker writes require per-action --execute flags and matching config gates.",
    }
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
        "--order-type",
        getattr(args, "order_type", "MIT"),
        "--tif",
        args.tif,
    ]
    if args.state:
        command.extend(["--state", args.state])
    if args.output:
        command.extend(["--output", args.output])
    if args.stops_journal:
        command.extend(["--stops-journal", args.stops_journal])
    optional_prices = [
        ("--limit-price", getattr(args, "limit_price", None)),
        ("--trigger-price", getattr(args, "trigger_price", None)),
        ("--trailing-amount", getattr(args, "trailing_amount", None)),
        ("--trailing-percent", getattr(args, "trailing_percent", None)),
        ("--limit-offset", getattr(args, "limit_offset", None)),
    ]
    for flag, value in optional_prices:
        if value is not None:
            command.extend([flag, str(value)])
    if getattr(args, "expire_date", None):
        command.extend(["--expire-date", args.expire_date])
    if getattr(args, "outside_rth", None):
        command.extend(["--outside-rth", args.outside_rth])
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
        "--order-type",
        getattr(args, "order_type", "LO"),
        "--tif",
        args.tif,
    ]
    if args.state:
        command.extend(["--state", args.state])
    if args.output:
        command.extend(["--output", args.output])
    if args.take_profit_journal:
        command.extend(["--take-profit-journal", args.take_profit_journal])
    if args.stops_journal:
        command.extend(["--stops-journal", args.stops_journal])
    optional_prices = [
        ("--limit-price", getattr(args, "limit_price", None)),
        ("--trigger-price", getattr(args, "trigger_price", None)),
        ("--trailing-amount", getattr(args, "trailing_amount", None)),
        ("--trailing-percent", getattr(args, "trailing_percent", None)),
        ("--limit-offset", getattr(args, "limit_offset", None)),
    ]
    for flag, value in optional_prices:
        if value is not None:
            command.extend([flag, str(value)])
    if getattr(args, "expire_date", None):
        command.extend(["--expire-date", args.expire_date])
    if getattr(args, "outside_rth", None):
        command.extend(["--outside-rth", args.outside_rth])
    if args.resize_stop_before_submit:
        command.append("--resize-stop-before-submit")
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


def run_paper_exit_plan(args: argparse.Namespace) -> None:
    command = [
        "script/paper_exit_plan.py",
        "--date",
        args.date,
        "--repo-root",
        args.repo_root,
        "--order-type",
        args.order_type,
        "--tif",
        args.tif,
    ]
    if args.state:
        command.extend(["--state", args.state])
    if args.intraday_state:
        command.extend(["--intraday-state", args.intraday_state])
    if args.decisions:
        command.extend(["--decisions", args.decisions])
    if args.output:
        command.extend(["--output", args.output])
    if args.exits_journal:
        command.extend(["--exits-journal", args.exits_journal])
    optional_prices = [
        ("--limit-price", args.limit_price),
        ("--trigger-price", args.trigger_price),
        ("--trailing-amount", args.trailing_amount),
        ("--trailing-percent", args.trailing_percent),
        ("--limit-offset", args.limit_offset),
    ]
    for flag, value in optional_prices:
        if value is not None:
            command.extend([flag, str(value)])
    if args.expire_date:
        command.extend(["--expire-date", args.expire_date])
    if args.outside_rth:
        command.extend(["--outside-rth", args.outside_rth])
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])
    if args.paper_execution_config:
        command.extend(["--paper-execution-config", args.paper_execution_config])
    if args.execute:
        command.append("--execute")

    proc = run_child(command)
    stdout = parse_json_output(proc.stdout)
    if proc.returncode != 0:
        emit(failed_response("paper-exit-plan", command, proc), 1)

    response = base_response("paper-exit-plan", command, stdout)
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
        "--order-type",
        args.order_type,
        "--tif",
        args.tif,
    ]
    if args.state:
        command.extend(["--state", args.state])
    if args.stops_journal:
        command.extend(["--stops-journal", args.stops_journal])
    if args.output:
        command.extend(["--output", args.output])
    optional_prices = [
        ("--limit-price", args.limit_price),
        ("--trigger-price", args.trigger_price),
        ("--trailing-amount", args.trailing_amount),
        ("--trailing-percent", args.trailing_percent),
        ("--limit-offset", args.limit_offset),
    ]
    for flag, value in optional_prices:
        if value is not None:
            command.extend([flag, str(value)])
    if args.expire_date:
        command.extend(["--expire-date", args.expire_date])
    if args.outside_rth:
        command.extend(["--outside-rth", args.outside_rth])
    if args.longbridge_cli:
        command.extend(["--longbridge-cli", args.longbridge_cli])
    if args.paper_execution_config:
        command.extend(["--paper-execution-config", args.paper_execution_config])
    if args.execute:
        command.append("--execute")

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
    pre.add_argument("--include-external-disclosures", dest="include_external_disclosures", action="store_true", default=True)
    pre.add_argument("--no-external-disclosures", dest="include_external_disclosures", action="store_false")
    pre.add_argument("--external-disclosure-symbol", action="append", default=[])
    pre.add_argument("--external-disclosure-input")
    pre.add_argument("--external-disclosure-lookback-days", type=int, default=120)
    pre.set_defaults(func=run_pre_market)

    pre_deliver = sub.add_parser("pre-market-deliver", help="Validate and deliver an existing pre-market report bundle")
    pre_deliver.add_argument("--date", required=True)
    pre_deliver.add_argument("--watchlist", default="config/watchlist.json")
    pre_deliver.add_argument("--report")
    pre_deliver.add_argument("--signals")
    pre_deliver.add_argument("--skip-agent-validation", action="store_true")
    pre_deliver.add_argument("--no-append-journal", action="store_true")
    pre_deliver.add_argument("--journal-dir", default="runtime/journal")
    pre_deliver.add_argument("--skip-account", action="store_true")
    pre_deliver.add_argument("--account-snapshot")
    pre_deliver.add_argument("--position-config", default="config/position_review.json")
    pre_deliver.add_argument("--sync-longbridge", action="store_true")
    pre_deliver.add_argument("--execute-sync", action="store_true")
    pre_deliver.add_argument("--group-name", default="今日关注")
    pre_deliver.add_argument("--sync-mode", choices=["add", "replace"], default="add")
    pre_deliver.add_argument("--sync-method", choices=["auto", "cli", "sdk"], default="auto")
    pre_deliver.add_argument("--max-symbols", type=int, default=3)
    pre_deliver.add_argument("--longbridge-cli")
    pre_deliver.add_argument("--learning-dir", default="runtime/learning")
    pre_deliver.add_argument("--manifest-output")
    pre_deliver.add_argument("--summary-output")
    pre_deliver.add_argument("--delivery-guard", action="store_true")
    pre_deliver.add_argument("--delivery-kind", choices=["pre-market", "exec-brief", "post-market"], default="exec-brief")
    pre_deliver.add_argument("--mark-sent", action="store_true")
    pre_deliver.set_defaults(func=run_pre_market_deliver)

    post_deliver = sub.add_parser("post-market-deliver", help="Validate and deliver an existing post-market report bundle")
    post_deliver.add_argument("--date", required=True)
    post_deliver.add_argument("--watchlist", default="config/watchlist.json")
    post_deliver.add_argument("--report")
    post_deliver.add_argument("--signals")
    post_deliver.add_argument("--snapshot")
    post_deliver.add_argument("--skip-agent-validation", action="store_true")
    post_deliver.add_argument("--skip-outcomes", action="store_true")
    post_deliver.add_argument("--append-outcomes", action="store_true")
    post_deliver.add_argument("--no-append-journal", action="store_true")
    post_deliver.add_argument("--journal-dir", default="runtime/journal")
    post_deliver.add_argument("--skip-account", action="store_true")
    post_deliver.add_argument("--account-snapshot")
    post_deliver.add_argument("--position-config", default="config/position_review.json")
    post_deliver.add_argument("--skip-plan-review", action="store_true")
    post_deliver.add_argument("--append-lessons", action="store_true")
    post_deliver.add_argument("--skip-learning-review", action="store_true")
    post_deliver.add_argument("--learning-lookback-days", type=int, default=20)
    post_deliver.add_argument("--skip-self-review", action="store_true")
    post_deliver.add_argument("--append-self-review", action="store_true")
    post_deliver.add_argument("--sync-longbridge", action="store_true")
    post_deliver.add_argument("--execute-sync", action="store_true")
    post_deliver.add_argument("--group-name", default="今日关注")
    post_deliver.add_argument("--sync-mode", choices=["add", "replace"], default="replace")
    post_deliver.add_argument("--sync-method", choices=["auto", "cli", "sdk"], default="auto")
    post_deliver.add_argument("--max-symbols", type=int, default=3)
    post_deliver.add_argument("--longbridge-cli")
    post_deliver.add_argument("--learning-dir", default="runtime/learning")
    post_deliver.add_argument("--manifest-output")
    post_deliver.add_argument("--summary-output")
    post_deliver.add_argument("--delivery-guard", action="store_true")
    post_deliver.add_argument("--mark-sent", action="store_true")
    post_deliver.set_defaults(func=run_post_market_deliver)

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

    intraday_tracker = sub.add_parser("intraday-tracker", help="Track pre-market plans against intraday monitor state")
    intraday_tracker.add_argument("--date")
    intraday_tracker.add_argument("--pre-market-signals")
    intraday_tracker.add_argument("--manual-watchlist", default="config/intraday_watchlist.json")
    intraday_tracker.add_argument("--monitor", default="report/latest-monitor.json")
    intraday_tracker.add_argument("--top-n", type=int, default=5)
    intraday_tracker.add_argument("--state")
    intraday_tracker.add_argument("--events")
    intraday_tracker.add_argument("--markdown")
    intraday_tracker.add_argument("--timezone", default="America/New_York")
    intraday_tracker.add_argument("--as-of")
    intraday_tracker.set_defaults(func=run_intraday_tracker)

    intraday_dry_run = sub.add_parser("intraday-dry-run", help="Run monitor candidates through dry-run paper checks")
    intraday_dry_run.add_argument("--date", required=True)
    intraday_dry_run.add_argument("--signals")
    intraday_dry_run.add_argument("--monitor", default="report/latest-monitor.json")
    intraday_dry_run.add_argument("--max-signals", type=int, default=5)
    intraday_dry_run.add_argument("--timezone", default="America/New_York")
    intraday_dry_run.add_argument("--signals-output")
    intraday_dry_run.add_argument("--account-snapshot")
    intraday_dry_run.add_argument("--preview-output")
    intraday_dry_run.add_argument("--submit-output")
    intraday_dry_run.add_argument("--summary-output")
    intraday_dry_run.add_argument("--default-market", default="US")
    intraday_dry_run.add_argument("--tif", default="day")
    intraday_dry_run.add_argument("--max-daily-risk-pct", type=float, default=3.0)
    intraday_dry_run.add_argument("--max-daily-orders", type=int, default=3)
    intraday_dry_run.add_argument("--learning-dir", default="runtime/learning")
    intraday_dry_run.set_defaults(func=run_intraday_dry_run)

    intraday_review = sub.add_parser("intraday-review-append", help="Append Codex intraday opportunity review into intraday.md")
    intraday_review.add_argument("--date", required=True)
    intraday_review.add_argument("--signals")
    intraday_review.add_argument("--submission")
    intraday_review.add_argument("--context")
    intraday_review.add_argument("--markdown")
    intraday_review.add_argument("--timezone", default="America/New_York")
    intraday_review.add_argument("--as-of")
    intraday_review.add_argument("--max-notes-chars", type=int, default=160)
    intraday_review.set_defaults(func=run_intraday_review_append)

    intraday_lifecycle = sub.add_parser("intraday-lifecycle-append", help="Append paper lifecycle status into intraday.md")
    intraday_lifecycle.add_argument("--date", required=True)
    intraday_lifecycle.add_argument("--markdown")
    intraday_lifecycle.add_argument("--output")
    intraday_lifecycle.add_argument("--timezone", default="America/New_York")
    intraday_lifecycle.add_argument("--as-of")
    intraday_lifecycle.set_defaults(func=run_intraday_lifecycle_append)

    intraday_context = sub.add_parser("intraday-opportunity-context", help="Build Codex review context for intraday opportunities")
    intraday_context.add_argument("--date", required=True)
    intraday_context.add_argument("--monitor", default="report/latest-monitor.json")
    intraday_context.add_argument("--pre-market-signals")
    intraday_context.add_argument("--intraday-state")
    intraday_context.add_argument("--intraday-markdown")
    intraday_context.add_argument("--paper-state")
    intraday_context.add_argument("--output")
    intraday_context.add_argument("--signals-output")
    intraday_context.add_argument("--max-candidates", type=int, default=3)
    intraday_context.add_argument("--markdown-chars", type=int, default=6000)
    intraday_context.set_defaults(func=run_intraday_opportunity_context)

    intraday_entry = sub.add_parser("intraday-paper-entry", help="Run standalone gated intraday paper entry")
    intraday_entry.add_argument("--date", required=True)
    intraday_entry.add_argument("--preview")
    intraday_entry.add_argument("--account-snapshot")
    intraday_entry.add_argument("--orders-journal")
    intraday_entry.add_argument("--signals")
    intraday_entry.add_argument("--output")
    intraday_entry.add_argument("--longbridge-cli")
    intraday_entry.add_argument("--require-validation", action="store_true")
    intraday_entry.add_argument("--execute", action="store_true")
    intraday_entry.add_argument("--max-daily-risk-pct", type=float, default=3.0)
    intraday_entry.add_argument("--max-daily-orders", type=int, default=1)
    intraday_entry.add_argument("--paper-execution-config")
    intraday_entry.set_defaults(func=run_intraday_paper_entry)

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
    agent_reports.add_argument("--external-disclosures")
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
    validate_plan.add_argument("--session", choices=["pre-market", "post-market", "monitor"], required=True)
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
    feishu_summary.add_argument("--session", choices=["pre-market", "post-market", "monitor"], required=True)
    feishu_summary.add_argument("--signals")
    feishu_summary.add_argument("--position-review")
    feishu_summary.add_argument("--plan-review")
    feishu_summary.add_argument("--run-manifest")
    feishu_summary.add_argument("--output")
    feishu_summary.add_argument("--learning-dir", default="runtime/learning")
    feishu_summary.set_defaults(func=run_feishu_summary)

    focus = sub.add_parser("focus-selection", help="Build an auditable focus-selection artifact")
    focus.add_argument("--date", required=True)
    focus.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    focus.add_argument("--signals")
    focus.add_argument("--context")
    focus.add_argument("--snapshot")
    focus.add_argument("--agents-dir")
    focus.add_argument("--output")
    focus.set_defaults(func=run_focus_selection)

    inspect_context = sub.add_parser("inspect-pre-market-context", help="Summarize pre-market context shape and data quality")
    inspect_context.add_argument("--date", required=True)
    inspect_context.add_argument("--context")
    inspect_context.add_argument("--output")
    inspect_context.set_defaults(func=run_inspect_pre_market_context)

    llm_manifest = sub.add_parser("llm-generation-manifest", help="Record LLM report-generation provenance")
    llm_manifest.add_argument("--date", required=True)
    llm_manifest.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    llm_manifest.add_argument("--model", required=True)
    llm_manifest.add_argument("--runner", default="codex")
    llm_manifest.add_argument("--prompt")
    llm_manifest.add_argument("--input", action="append", default=[])
    llm_manifest.add_argument("--generated-output", action="append", default=[])
    llm_manifest.add_argument("--notes")
    llm_manifest.add_argument("--output")
    llm_manifest.set_defaults(func=run_llm_generation_manifest)

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
    monitor_signals.add_argument("--signals-output")
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
    paper_preview.add_argument("--session", choices=["pre-market", "post-market", "monitor"], required=True)
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
    paper_submit.add_argument("--session", choices=["pre-market", "post-market", "monitor"], required=True)
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
    paper_sync.add_argument("--exits-journal")
    paper_sync.add_argument("--paper-snapshot")
    paper_sync.add_argument("--output")
    paper_sync.add_argument("--repo-root", default=str(ROOT))
    paper_sync.set_defaults(func=run_paper_order_sync)

    paper_events = sub.add_parser("paper-event-ledger", help="Project paper execution facts into the unified event ledger")
    paper_events.add_argument("--date", required=True)
    paper_events.add_argument("--orders-journal")
    paper_events.add_argument("--stops-journal")
    paper_events.add_argument("--take-profit-journal")
    paper_events.add_argument("--exits-journal")
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

    paper_lifecycle = sub.add_parser("paper-lifecycle", help="Run paper order lifecycle sync, exit planning, ledger, and review")
    paper_lifecycle.add_argument("--date", required=True)
    paper_lifecycle.add_argument("--paper-account-input")
    paper_lifecycle.add_argument("--paper-execution-config")
    paper_lifecycle.add_argument("--longbridge-cli")
    paper_lifecycle.add_argument("--execute-cancel", action="store_true")
    paper_lifecycle.add_argument("--execute-protective-stop", action="store_true")
    paper_lifecycle.add_argument("--execute-take-profit", action="store_true")
    paper_lifecycle.add_argument("--execute-exit", action="store_true")
    paper_lifecycle.add_argument("--execute-break-even-stop", action="store_true")
    paper_lifecycle.add_argument("--resize-stop-before-take-profit", action="store_true")
    paper_lifecycle.add_argument("--expire-after-minutes", type=int, default=90)
    paper_lifecycle.add_argument("--stop-tif", default="gtc")
    paper_lifecycle.add_argument("--stop-order-type", default="MIT")
    paper_lifecycle.add_argument("--stop-limit-price", type=float)
    paper_lifecycle.add_argument("--stop-trigger-price", type=float)
    paper_lifecycle.add_argument("--stop-trailing-amount", type=float)
    paper_lifecycle.add_argument("--stop-trailing-percent", type=float)
    paper_lifecycle.add_argument("--stop-limit-offset", type=float)
    paper_lifecycle.add_argument("--stop-expire-date")
    paper_lifecycle.add_argument("--stop-outside-rth")
    paper_lifecycle.add_argument("--take-profit-tif", default="gtc")
    paper_lifecycle.add_argument("--take-profit-order-type", default="LO")
    paper_lifecycle.add_argument("--take-profit-limit-price", type=float)
    paper_lifecycle.add_argument("--take-profit-trigger-price", type=float)
    paper_lifecycle.add_argument("--take-profit-trailing-amount", type=float)
    paper_lifecycle.add_argument("--take-profit-trailing-percent", type=float)
    paper_lifecycle.add_argument("--take-profit-limit-offset", type=float)
    paper_lifecycle.add_argument("--take-profit-expire-date")
    paper_lifecycle.add_argument("--take-profit-outside-rth")
    paper_lifecycle.add_argument("--exit-order-type", default="MO")
    paper_lifecycle.add_argument("--exit-tif", default="day")
    paper_lifecycle.add_argument("--exit-limit-price", type=float)
    paper_lifecycle.add_argument("--exit-trigger-price", type=float)
    paper_lifecycle.add_argument("--exit-trailing-amount", type=float)
    paper_lifecycle.add_argument("--exit-trailing-percent", type=float)
    paper_lifecycle.add_argument("--exit-limit-offset", type=float)
    paper_lifecycle.add_argument("--exit-expire-date")
    paper_lifecycle.add_argument("--exit-outside-rth")
    paper_lifecycle.add_argument("--break-even-tif", default="gtc")
    paper_lifecycle.add_argument("--break-even-order-type", default="MIT")
    paper_lifecycle.add_argument("--break-even-limit-price", type=float)
    paper_lifecycle.add_argument("--break-even-trigger-price", type=float)
    paper_lifecycle.add_argument("--break-even-trailing-amount", type=float)
    paper_lifecycle.add_argument("--break-even-trailing-percent", type=float)
    paper_lifecycle.add_argument("--break-even-limit-offset", type=float)
    paper_lifecycle.add_argument("--break-even-expire-date")
    paper_lifecycle.add_argument("--break-even-outside-rth")
    paper_lifecycle.add_argument("--exit-fraction", type=float, default=0.5)
    paper_lifecycle.add_argument("--append-lessons", action="store_true")
    paper_lifecycle.add_argument("--strategy-review", action="store_true")
    paper_lifecycle.add_argument("--learning-dir", default="runtime/learning")
    paper_lifecycle.add_argument("--repo-root", default=str(ROOT))
    paper_lifecycle.set_defaults(func=run_paper_lifecycle)

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
    paper_stop.add_argument("--order-type", default="MIT")
    paper_stop.add_argument("--limit-price", type=float)
    paper_stop.add_argument("--trigger-price", type=float)
    paper_stop.add_argument("--trailing-amount", type=float)
    paper_stop.add_argument("--trailing-percent", type=float)
    paper_stop.add_argument("--limit-offset", type=float)
    paper_stop.add_argument("--tif", default="gtc")
    paper_stop.add_argument("--expire-date")
    paper_stop.add_argument("--outside-rth")
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
    paper_tp.add_argument("--stops-journal")
    paper_tp.add_argument("--exit-fraction", type=float, default=0.5)
    paper_tp.add_argument("--order-type", default="LO")
    paper_tp.add_argument("--limit-price", type=float)
    paper_tp.add_argument("--trigger-price", type=float)
    paper_tp.add_argument("--trailing-amount", type=float)
    paper_tp.add_argument("--trailing-percent", type=float)
    paper_tp.add_argument("--limit-offset", type=float)
    paper_tp.add_argument("--tif", default="gtc")
    paper_tp.add_argument("--expire-date")
    paper_tp.add_argument("--outside-rth")
    paper_tp.add_argument("--resize-stop-before-submit", action="store_true")
    paper_tp.add_argument("--longbridge-cli")
    paper_tp.add_argument("--execute", action="store_true")
    paper_tp.add_argument("--paper-execution-config")
    paper_tp.add_argument("--repo-root", default=str(ROOT))
    paper_tp.set_defaults(func=run_paper_take_profit_plan)

    paper_exit = sub.add_parser("paper-exit-plan", help="Build or submit guarded paper exits for invalidated intraday plans")
    paper_exit.add_argument("--date", required=True)
    paper_exit.add_argument("--state")
    paper_exit.add_argument("--intraday-state")
    paper_exit.add_argument("--decisions")
    paper_exit.add_argument("--output")
    paper_exit.add_argument("--exits-journal")
    paper_exit.add_argument("--order-type", default="MO")
    paper_exit.add_argument("--limit-price", type=float)
    paper_exit.add_argument("--trigger-price", type=float)
    paper_exit.add_argument("--trailing-amount", type=float)
    paper_exit.add_argument("--trailing-percent", type=float)
    paper_exit.add_argument("--limit-offset", type=float)
    paper_exit.add_argument("--tif", default="day")
    paper_exit.add_argument("--expire-date")
    paper_exit.add_argument("--outside-rth")
    paper_exit.add_argument("--longbridge-cli")
    paper_exit.add_argument("--execute", action="store_true")
    paper_exit.add_argument("--paper-execution-config")
    paper_exit.add_argument("--repo-root", default=str(ROOT))
    paper_exit.set_defaults(func=run_paper_exit_plan)

    paper_be = sub.add_parser("paper-break-even-stop-plan", help="Build a dry-run break-even stop movement plan")
    paper_be.add_argument("--date", required=True)
    paper_be.add_argument("--state")
    paper_be.add_argument("--stops-journal")
    paper_be.add_argument("--output")
    paper_be.add_argument("--buffer-pct", type=float, default=0.0)
    paper_be.add_argument("--order-type", default="MIT")
    paper_be.add_argument("--limit-price", type=float)
    paper_be.add_argument("--trigger-price", type=float)
    paper_be.add_argument("--trailing-amount", type=float)
    paper_be.add_argument("--trailing-percent", type=float)
    paper_be.add_argument("--limit-offset", type=float)
    paper_be.add_argument("--tif", default="gtc")
    paper_be.add_argument("--expire-date")
    paper_be.add_argument("--outside-rth")
    paper_be.add_argument("--longbridge-cli")
    paper_be.add_argument("--execute", action="store_true")
    paper_be.add_argument("--paper-execution-config")
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
