#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def display_time(value: datetime, timezone_name: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%H:%M %Z")


def snapshot_name(value: datetime, timezone_name: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%H%M%S") + ".json"


def source_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def compact_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def price_from(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            nested = value.get("price")
            if nested is not None:
                return nested
        elif value is not None:
            return value
    return None


def nested_price(payload: dict[str, Any], container: str, key: str) -> Any:
    value = payload.get(container)
    if isinstance(value, dict):
        return value.get(key)
    return None


def count_by_status(signals: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"conditional_executable": 0, "watch_only": 0, "no_trade": 0}
    for signal in signals:
        status = str(signal.get("execution_status") or signal.get("plan_type") or "").strip()
        if status == "conditional_executable":
            counts["conditional_executable"] += 1
        elif status in {"watch_only", "observed"}:
            counts["watch_only"] += 1
        elif status in {"no_trade", "blocked"}:
            counts["no_trade"] += 1
    return counts


def build_signal_line(signal: dict[str, Any], max_notes_chars: int) -> str:
    symbol = str(signal.get("symbol") or "UNKNOWN")
    plan_type = str(signal.get("plan_type") or "unknown")
    execution_status = str(signal.get("execution_status") or "unknown")
    trigger = nested_price(signal, "entry", "trigger_price") or price_from(signal, "trigger")
    stop = nested_price(signal, "stop", "initial_stop") or price_from(signal, "invalidation")
    tp1 = nested_price(signal, "take_profit", "tp1")
    notes = compact_text(signal.get("notes") or (signal.get("risk") or {}).get("text"), max_notes_chars)
    pieces = [f"- {symbol}: {plan_type} / {execution_status}"]
    details = []
    if trigger is not None:
        details.append(f"trigger={trigger}")
    if stop is not None:
        details.append(f"stop={stop}")
    if tp1 is not None:
        details.append(f"tp1={tp1}")
    if details:
        pieces.append(f"({'；'.join(details)})")
    if notes:
        pieces.append(f"- {notes}")
    return " ".join(pieces)


def build_section(
    *,
    date: str,
    as_of: datetime,
    timezone_name: str,
    signals_path: Path,
    context_path: Path,
    submission_path: Path,
    signals_payload: dict[str, Any],
    context_payload: dict[str, Any],
    submission_payload: dict[str, Any],
    repo_root: Path,
    max_notes_chars: int,
) -> tuple[str, dict[str, Any]]:
    raw_signals = signals_payload.get("signals") if isinstance(signals_payload.get("signals"), list) else []
    signals = [signal for signal in raw_signals if isinstance(signal, dict)]
    counts = count_by_status(signals)
    context_summary = context_payload.get("summary") if isinstance(context_payload.get("summary"), dict) else {}
    submit_summary = submission_payload.get("summary") if isinstance(submission_payload.get("summary"), dict) else {}
    section_lines = [
        f"## {display_time(as_of, timezone_name)} Codex 机会评审",
        f"- 来源：{source_path(signals_path, repo_root)}；context={source_path(context_path, repo_root) if context_path.exists() else 'missing'}",
        (
            "- 汇总："
            f"signals={len(signals)}；"
            f"conditional_executable={counts['conditional_executable']}；"
            f"watch_only={counts['watch_only']}；"
            f"no_trade={counts['no_trade']}"
        ),
    ]
    if context_summary:
        observation_scans = context_summary.get("observation_scans")
        deterministic_candidates = context_summary.get("deterministic_candidate_scans", context_summary.get("candidate_scans"))
        template_signals = context_summary.get("template_signals", context_summary.get("sidecar_template_signals"))
        section_lines.append(
            "- Codex 输入："
            f"observation_scans={observation_scans if observation_scans is not None else 'unknown'}；"
            f"deterministic_candidate_scans={deterministic_candidates if deterministic_candidates is not None else 'unknown'}；"
            f"template_signals={template_signals if template_signals is not None else 'unknown'}"
        )
    if submit_summary:
        section_lines.append(
            "- dry-run："
            f"ready={submit_summary.get('ready', 0)}；"
            f"submitted={submit_summary.get('submitted', 0)}；"
            f"blocked={submit_summary.get('blocked', 0)}；"
            f"errors={submit_summary.get('errors', 0)}"
        )
    if not signals:
        section_lines.append("- 候选：无")
    else:
        for signal in signals:
            section_lines.append(build_signal_line(signal, max_notes_chars))
    section_lines.append("- 说明：本段为 Codex 盘中评审记录，不构成真实账户交易指令。")
    summary = {
        "signals": len(signals),
        **counts,
        "dry_run_ready": submit_summary.get("ready", 0),
        "dry_run_submitted": submit_summary.get("submitted", 0),
        "dry_run_blocked": submit_summary.get("blocked", 0),
        "dry_run_errors": submit_summary.get("errors", 0),
        "observation_scans": context_summary.get("observation_scans"),
        "deterministic_candidate_scans": context_summary.get("deterministic_candidate_scans", context_summary.get("candidate_scans")),
    }
    return "\n".join(section_lines) + "\n", summary


def append_markdown(path: Path, date: str, section: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else f"# {date} Intraday Tracker\n\n"
    if previous and not previous.endswith("\n"):
        previous += "\n"
    path.write_text(previous + section + "\n", encoding="utf-8")


def write_snapshot(
    *,
    path: Path,
    date: str,
    as_of: datetime,
    timezone_name: str,
    signals_path: Path,
    context_path: Path,
    submission_path: Path,
    signals_payload: dict[str, Any],
    context_payload: dict[str, Any],
    submission_payload: dict[str, Any],
    summary: dict[str, Any],
    repo_root: Path,
) -> None:
    payload = {
        "date": date,
        "created_at": as_of.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "local_time": display_time(as_of, timezone_name),
        "workflow": "intraday-review-append",
        "sources": {
            "signals": source_path(signals_path, repo_root),
            "context": source_path(context_path, repo_root) if context_path.exists() else None,
            "submission": source_path(submission_path, repo_root) if submission_path.exists() else None,
        },
        "summary": summary,
        "signals": signals_payload,
        "context_summary": context_payload.get("summary") if isinstance(context_payload.get("summary"), dict) else {},
        "submission": submission_payload,
        "safety_note": "Archived Codex intraday review snapshot. Not a broker instruction.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    signals_path = resolve_path(repo_root, args.signals, repo_root / "report" / args.date / "monitor-signals.json")
    submission_path = resolve_path(repo_root, args.submission, repo_root / "report" / args.date / "paper-trade-submission.json")
    context_path = resolve_path(repo_root, args.context, repo_root / "report" / args.date / "intraday-opportunity-context.json")
    markdown_path = resolve_path(repo_root, args.markdown, repo_root / "report" / args.date / "intraday.md")
    snapshot_dir = resolve_path(repo_root, getattr(args, "snapshot_dir", None), repo_root / "report" / args.date / "monitor-signals")
    if not signals_path.exists():
        return {
            "status": "skipped",
            "workflow": "intraday-review-append",
            "date": args.date,
            "reason": "signals file missing",
            "signals": str(signals_path),
            "markdown": str(markdown_path),
        }

    signals_payload = read_json(signals_path, {"signals": []})
    context_payload = read_json(context_path, {})
    submission_payload = read_json(submission_path, {})
    as_of = parse_as_of(args.as_of)
    section, summary = build_section(
        date=args.date,
        as_of=as_of,
        timezone_name=args.timezone,
        signals_path=signals_path,
        context_path=context_path,
        submission_path=submission_path,
        signals_payload=signals_payload,
        context_payload=context_payload,
        submission_payload=submission_payload,
        repo_root=repo_root,
        max_notes_chars=args.max_notes_chars,
    )
    append_markdown(markdown_path, args.date, section)
    snapshot_path = snapshot_dir / snapshot_name(as_of, args.timezone)
    write_snapshot(
        path=snapshot_path,
        date=args.date,
        as_of=as_of,
        timezone_name=args.timezone,
        signals_path=signals_path,
        context_path=context_path,
        submission_path=submission_path,
        signals_payload=signals_payload,
        context_payload=context_payload,
        submission_payload=submission_payload,
        summary=summary,
        repo_root=repo_root,
    )
    return {
        "status": "success",
        "workflow": "intraday-review-append",
        "date": args.date,
        "markdown": str(markdown_path),
        "snapshot": str(snapshot_path),
        "signals": str(signals_path),
        "submission": str(submission_path) if submission_path.exists() else None,
        "context": str(context_path) if context_path.exists() else None,
        "summary": summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append Codex intraday opportunity review into the daily intraday Markdown log")
    parser.add_argument("--date", required=True)
    parser.add_argument("--signals")
    parser.add_argument("--submission")
    parser.add_argument("--context")
    parser.add_argument("--markdown")
    parser.add_argument("--snapshot-dir")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--as-of")
    parser.add_argument("--max-notes-chars", type=int, default=160)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        payload = run(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "workflow": "intraday-review-append", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
