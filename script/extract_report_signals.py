#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_append import append_jsonl, journal_path
from signal_artifacts import normalize_sidecar, read_json, resolve_signals_path


REPORT_FILES = {
    "pre-market": "exec-brief.md",
    "post-market": "post-market.md",
}

SETUP_RE = re.compile(r"\b[a-z0-9][a-z0-9_-]+\.md\b")
SYMBOL_RE = re.compile(r"\b[A-Z][A-Z0-9.-]{0,9}\b")
SYMBOL_HEADING_RE = re.compile(r"^###\s+`?([A-Z][A-Z0-9.-]{0,9})`?\s*$", re.MULTILINE)
SKIP_TOKENS = {
    "AI",
    "API",
    "BOS",
    "CLI",
    "ETF",
    "MA20",
    "MA50",
    "NO",
    "R",
    "S",
    "US",
}


def ordered_unique(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def report_path(repo_root: Path, report_date: str, session: str, explicit_report: str | None) -> Path:
    if explicit_report:
        path = Path(explicit_report)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / report_date / REPORT_FILES[session]


def signals_path(repo_root: Path, report_date: str, session: str, explicit_signals: str | None) -> Path:
    return resolve_signals_path(repo_root, report_date, explicit_signals, session)


def split_symbol_sections(markdown: str) -> dict[str, str]:
    matches = list(SYMBOL_HEADING_RE.finditer(markdown))
    major_heading_re = re.compile(r"^##\s+", re.MULTILINE)
    sections: dict[str, str] = {}
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        next_major = major_heading_re.search(markdown, start, end)
        if next_major:
            end = next_major.start()
        sections[match.group(1)] = markdown[start:end].strip()
    return sections


def bullet_value(section: str, labels: tuple[str, ...]) -> str | None:
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        body = stripped[1:].strip()
        for label in labels:
            for sep in ("：", ":"):
                prefix = f"{label}{sep}"
                if body.startswith(prefix):
                    return body[len(prefix) :].strip()
    return None


def inline_value(section: str, labels: tuple[str, ...]) -> str | None:
    normalized = section.replace("\n", " ")
    for label in labels:
        pattern = re.compile(rf"{re.escape(label)}\s*(?:是|为|：|:)?\s*(.*?)(?:；|。|$)")
        match = pattern.search(normalized)
        if match:
            return match.group(1).strip()
    return None


def field_value(section: str, labels: tuple[str, ...]) -> str | None:
    return bullet_value(section, labels) or inline_value(section, labels)


def focus_symbols(markdown: str, session: str) -> list[str]:
    labels = (
        ("今日最多3个重点标的",)
        if session == "pre-market"
        else ("明日最多3个重点观察标的",)
    )
    for line in markdown.splitlines():
        if any(label in line for label in labels):
            text = line.split("：", 1)[-1].split(":", 1)[-1]
            symbols = [token for token in SYMBOL_RE.findall(text.upper()) if token not in SKIP_TOKENS]
            return ordered_unique(symbols)
    return []


def post_market_observation_lines(markdown: str) -> list[tuple[str, str]]:
    lines = markdown.splitlines()
    in_section = False
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = "明日观察清单" in stripped
            continue
        if not in_section or not stripped.startswith("-"):
            continue
        match = re.match(r"^-\s+`?([A-Z][A-Z0-9.-]{0,9})`?\s*[：:]\s*(.+)$", stripped)
        if match:
            symbol = match.group(1).upper()
            if symbol not in SKIP_TOKENS:
                result.append((symbol, match.group(2).strip()))
    return result


def setup_files(text: str) -> list[str]:
    if "NO VALID SETUP" in text:
        return ["NO VALID SETUP"]
    return ordered_unique(SETUP_RE.findall(text))


def infer_regime(text: str) -> str | None:
    for token in ("上行趋势", "下行趋势", "震荡", "过渡", "Barb Wire", "弱势结构修复", "强突破趋势"):
        if token in text:
            return token
    return None


def signal_id(date: str, session: str, symbol: str, source_report: str) -> str:
    raw = f"{date}|{session}|{symbol}|{source_report}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{date}:{session}:{symbol}:{digest}"


def make_signal(
    *,
    date: str,
    session: str,
    symbol: str,
    section: str,
    source_report: str,
    default_status: str,
) -> dict[str, Any]:
    setups = setup_files(section)
    trigger = field_value(section, ("触发条件", "明日触发条件", "主场景", "明日主观察"))
    invalidation = field_value(section, ("失效条件", "失效/放弃条件", "放弃条件"))
    risk = field_value(section, ("风险约束", "风险提醒", "单笔风险"))
    notes = field_value(section, ("执行要点（1行）", "执行要点", "备选场景", "明日备选路径"))
    status = "no_trade" if "NO TRADE" in section and not trigger else default_status
    primary_setup = setups[0] if setups else "NO VALID SETUP"

    payload: dict[str, Any] = {
        "kind": "signal",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "signal_id": signal_id(date, session, symbol, source_report),
        "date": date,
        "session": session,
        "symbol": symbol,
        "setup": primary_setup,
        "setup_files": setups,
        "status": status,
        "source_report": source_report,
        "regime": infer_regime(section),
        "trigger": trigger,
        "invalidation": invalidation,
        "risk": risk,
        "notes": notes,
    }
    return {key: value for key, value in payload.items() if value not in (None, [], "")}


def extract_pre_market(markdown: str, date: str, source_report: str, max_signals: int) -> list[dict[str, Any]]:
    sections = split_symbol_sections(markdown)
    symbols = focus_symbols(markdown, "pre-market") or list(sections)
    signals = []
    for symbol in symbols[:max_signals]:
        section = sections.get(symbol)
        if section:
            signals.append(
                make_signal(
                    date=date,
                    session="pre-market",
                    symbol=symbol,
                    section=section,
                    source_report=source_report,
                    default_status="planned",
                )
            )
    return signals


def extract_post_market(markdown: str, date: str, source_report: str, max_signals: int) -> list[dict[str, Any]]:
    observation_lines = post_market_observation_lines(markdown)
    if observation_lines:
        return [
            make_signal(
                date=date,
                session="post-market",
                symbol=symbol,
                section=text,
                source_report=source_report,
                default_status="planned",
            )
            for symbol, text in observation_lines[:max_signals]
        ]

    sections = split_symbol_sections(markdown)
    symbols = focus_symbols(markdown, "post-market") or list(sections)
    signals = []
    for symbol in symbols[:max_signals]:
        section = sections.get(symbol)
        if section:
            signals.append(
                make_signal(
                    date=date,
                    session="post-market",
                    symbol=symbol,
                    section=section,
                    source_report=source_report,
                    default_status="planned",
                )
            )
    return signals


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
        signal_id_value = payload.get("signal_id")
        if isinstance(signal_id_value, str):
            ids.add(signal_id_value)
    return ids


def validate_report(repo_root: Path, date: str, session: str, report: Path | None, signals: Path | None) -> dict[str, Any]:
    command = [
        sys.executable,
        "script/validate_report.py",
        "--date",
        date,
        "--session",
        session,
    ]
    if report:
        command.extend(["--report", str(report)])
    if signals:
        command.extend(["--signals", str(signals)])
    proc = subprocess.run(command, cwd=repo_root, check=False, text=True, capture_output=True)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"status": "fail", "errors": [proc.stderr or proc.stdout or "validation failed"]}
    if proc.returncode != 0:
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload


def extract(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    report = report_path(repo_root, args.date, args.session, args.report)
    sidecar = signals_path(repo_root, args.date, args.session, args.signals)
    if args.require_validation:
        validate_report(repo_root, args.date, args.session, report if args.report else None, sidecar if args.signals else None)

    source_report = str(report.relative_to(repo_root)) if report.exists() and report.is_relative_to(repo_root) else str(report)
    source_signals = None
    if sidecar.exists():
        payload = read_json(sidecar)
        if payload.get("date") != args.date:
            raise ValueError(f"{sidecar}: date must be {args.date}")
        if payload.get("session") != args.session:
            raise ValueError(f"{sidecar}: session must be {args.session}")
        extracted_signals = normalize_sidecar(payload, sidecar, repo_root)[: args.max_signals]
        signals = extracted_signals
        source_signals = str(sidecar.relative_to(repo_root)) if sidecar.is_relative_to(repo_root) else str(sidecar)
    else:
        if not report.exists():
            raise FileNotFoundError(f"missing report file: {report}")
        markdown = report.read_text(encoding="utf-8")
        if args.session == "pre-market":
            signals = extract_pre_market(markdown, args.date, source_report, args.max_signals)
        else:
            signals = extract_post_market(markdown, args.date, source_report, args.max_signals)

    appended = []
    skipped_duplicates = []
    journal_file = None
    if args.append:
        journal_file = journal_path(repo_root, args.journal_dir, "signal")
        existing_ids = existing_signal_ids(journal_file)
        for signal in signals:
            if signal["signal_id"] in existing_ids:
                skipped_duplicates.append(signal["signal_id"])
                continue
            append_jsonl(journal_file, signal)
            existing_ids.add(signal["signal_id"])
            appended.append(signal["signal_id"])

    return {
        "status": "success",
        "date": args.date,
        "session": args.session,
        "source_report": source_report,
        "source_signals": source_signals,
        "signals": signals,
        "append": args.append,
        "journal_path": str(journal_file) if journal_file else None,
        "appended": appended,
        "skipped_duplicates": skipped_duplicates,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract focused report candidates into signal JSON records")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    parser.add_argument("--report")
    parser.add_argument("--signals", help="Structured signal sidecar path. Defaults to report/<DATE>/<SESSION>-signals.json.")
    parser.add_argument("--max-signals", type=int, default=3)
    parser.add_argument("--append", action="store_true", help="Append extracted signals to runtime journal JSONL.")
    parser.add_argument("--journal-dir", default="runtime/journal")
    parser.add_argument("--require-validation", action="store_true", help="Run validate_report.py before extracting signals.")
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
