#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPORT_FILES = {
    "pre-market": ["pre-market.md", "exec-brief.md"],
    "post-market": ["post-market.md"],
}

SETUP_RE = re.compile(r"\b[a-z0-9][a-z0-9_-]+\.md\b")
SYMBOL_HEADING_RE = re.compile(r"^###\s+`?([A-Z][A-Z0-9.-]{0,9})`?\s*$", re.MULTILINE)
FORBIDDEN_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"必须\s*(买入|卖出|做多|做空|建仓|加仓)",
        r"一定\s*(上涨|下跌|突破|反弹|赚钱|盈利)",
        r"稳赚",
        r"无风险",
        r"确定性\s*(买点|卖点|机会|交易)",
        r"闭眼\s*(买|卖)",
    )
]


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def expected_reports(repo_root: Path, report_date: str, session: str, explicit_report: str | None) -> list[Path]:
    if explicit_report:
        return [Path(explicit_report) if Path(explicit_report).is_absolute() else repo_root / explicit_report]
    return [repo_root / "report" / report_date / name for name in REPORT_FILES[session]]


def refined_setup_files(repo_root: Path) -> set[str]:
    setup_dir = repo_root / "knowledge" / "refined" / "setups"
    if not setup_dir.exists():
        return set()
    return {path.name for path in setup_dir.glob("*.md")}


def split_symbol_sections(markdown: str) -> list[tuple[str, str]]:
    matches = list(SYMBOL_HEADING_RE.finditer(markdown))
    sections = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        sections.append((match.group(1), markdown[start:end]))
    return sections


def has_setup_reference(text: str) -> bool:
    return bool(SETUP_RE.search(text) or "NO VALID SETUP" in text)


def has_trigger(text: str) -> bool:
    return any(token in text for token in ("触发条件", "明日触发条件", "突破", "回踩", "站稳"))


def has_invalidation(text: str) -> bool:
    return any(token in text for token in ("失效", "放弃条件", "无效", "NO TRADE"))


def has_risk(text: str) -> bool:
    return "风险" in text and any(token in text for token in ("<=1%", "<= 1%", "单笔", "止损", "降仓", "放弃", "NO TRADE"))


def is_actionable_section(text: str) -> bool:
    return any(token in text for token in ("可执行候选", "重点候选", "重点观察", "值得明日重点观察"))


def mentions_stale_data_limit(text: str) -> bool:
    return any(token in text for token in ("stale_data", "数据限制", "数据滞后", "缓存", "非最新", "行情受限"))


def snapshot_payload(repo_root: Path, report_date: str, session: str) -> dict[str, Any] | None:
    report_dir = repo_root / "report" / report_date
    if session == "pre-market":
        context = load_json(report_dir / "pre-market-context.json")
        if context and isinstance(context.get("snapshot"), dict):
            return context["snapshot"]
        return None
    return load_json(report_dir / "daily-snapshot.json")


def validate_report_text(
    *,
    path: Path,
    text: str,
    session: str,
    setup_files: set[str],
    stale_data: bool,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    label = str(path)
    is_brief = path.name == "exec-brief.md"

    if not has_setup_reference(text):
        errors.append(f"{label}: missing setup file reference or explicit NO VALID SETUP")

    unknown_setups = sorted({name for name in SETUP_RE.findall(text) if name not in setup_files})
    for setup in unknown_setups:
        errors.append(f"{label}: setup file does not exist in knowledge/refined/setups: {setup}")

    for pattern in FORBIDDEN_PATTERNS:
        match = pattern.search(text)
        if match:
            errors.append(f"{label}: forbidden deterministic wording: {match.group(0)}")

    if stale_data and not mentions_stale_data_limit(text):
        errors.append(f"{label}: snapshot.stale_data=true but report does not disclose data limitation")

    if re.search(r"(动态候选|sp500_screen|S&P 500).{0,24}(推荐|建议买入|重点推荐)", text):
        warnings.append(f"{label}: dynamic universe wording may sound like a recommendation")

    if session == "pre-market" and not is_brief and "不是交易建议" not in text and "不是交易指令" not in text:
        warnings.append(f"{label}: pre-market report should explicitly say candidates are not trade instructions")

    if not is_brief and not has_trigger(text):
        errors.append(f"{label}: missing trigger condition wording")
    if not has_invalidation(text):
        errors.append(f"{label}: missing invalidation or abandonment condition wording")
    if not has_risk(text):
        errors.append(f"{label}: missing risk constraint wording")

    for symbol, section in split_symbol_sections(text):
        if not has_setup_reference(section):
            errors.append(f"{label}: {symbol} section missing setup file or NO VALID SETUP")
        if not has_invalidation(section):
            errors.append(f"{label}: {symbol} section missing invalidation or abandonment condition")
        if not is_brief and is_actionable_section(section) and not has_trigger(section):
            errors.append(f"{label}: {symbol} section missing trigger condition")
        if not is_brief and not has_risk(section):
            errors.append(f"{label}: {symbol} section missing risk constraint")

    return errors, warnings


def validate(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    reports = expected_reports(repo_root, args.date, args.session, args.report)
    setup_files = refined_setup_files(repo_root)
    snapshot = snapshot_payload(repo_root, args.date, args.session) or {}
    stale_data = bool(snapshot.get("stale_data"))

    errors: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []

    if not setup_files:
        errors.append("missing refined setup directory or setup markdown files")

    for report in reports:
        if not report.exists():
            errors.append(f"missing report file: {report}")
            continue
        text = report.read_text(encoding="utf-8")
        checked.append(str(report))
        report_errors, report_warnings = validate_report_text(
            path=report,
            text=text,
            session=args.session,
            setup_files=setup_files,
            stale_data=stale_data,
        )
        errors.extend(report_errors)
        warnings.extend(report_warnings)

    return {
        "status": "fail" if errors else "pass",
        "date": args.date,
        "session": args.session,
        "checked_reports": checked,
        "stale_data": stale_data,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Trading Copilot markdown report quality gates")
    parser.add_argument("--date", required=True, help="Report date in YYYY-MM-DD")
    parser.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    parser.add_argument("--report", help="Validate a single report path instead of the session defaults")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()

    payload = validate(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
