#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from journal_append import journal_path
from journal_review import read_jsonl


def resolve_dir(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def lessons_path(repo_root: Path, learning_dir: str) -> Path:
    return resolve_dir(repo_root, learning_dir) / "daily_lessons.jsonl"


def candidates_path(repo_root: Path, learning_dir: str) -> Path:
    return resolve_dir(repo_root, learning_dir) / "pattern_candidates.jsonl"


def report_paths(repo_root: Path, output: str | None) -> tuple[Path, Path]:
    if output:
        base = Path(output)
        if not base.is_absolute():
            base = repo_root / base
    else:
        base = repo_root / "report" / "learning" / "pattern-review"
    return base.with_suffix(".md"), base.with_suffix(".json")


def parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def slug(value: str) -> str:
    text = value.rsplit(".", 1)[0]
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return text or "unknown"


def pattern_id(problem: str, setup: str, lesson_type: str) -> str:
    if setup and setup != "NO VALID SETUP":
        return f"{slug(problem)}__{slug(setup)}"
    return f"{slug(problem)}__{slug(lesson_type)}"


def selected_lessons(records: list[dict[str, Any]], end_date: date, lookback_days: int) -> list[dict[str, Any]]:
    start_date = end_date - timedelta(days=max(lookback_days, 1) - 1)
    result = []
    for record in records:
        if record.get("kind") != "daily_lesson":
            continue
        lesson_date = parse_date(str(record.get("date") or ""))
        if lesson_date and start_date <= lesson_date <= end_date:
            result.append(record)
    return result


def selected_records(records: list[dict[str, Any]], end_date: date, lookback_days: int, date_key: str) -> list[dict[str, Any]]:
    start_date = end_date - timedelta(days=max(lookback_days, 1) - 1)
    result = []
    for record in records:
        record_date = parse_date(str(record.get(date_key) or ""))
        if record_date and start_date <= record_date <= end_date:
            result.append(record)
    return result


def choose_end_date(records: list[dict[str, Any]], explicit_end_date: str | None) -> date:
    if explicit_end_date:
        parsed = parse_date(explicit_end_date)
        if not parsed:
            raise ValueError(f"invalid --end-date: {explicit_end_date}")
        return parsed
    dates = [parse_date(str(record.get("date") or "")) for record in records]
    valid_dates = [item for item in dates if item is not None]
    return max(valid_dates) if valid_dates else datetime.now(timezone.utc).date()


def position_lesson(record: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(record.get("symbol") or "").upper()
    if not symbol:
        return None
    if not record.get("in_today_signals"):
        problem = "position_without_plan"
        suggested = "positions should either link to an active plan or be explicitly classified as core/watch"
    elif record.get("trade_link_state") == "trade_missing_source_signal_id":
        problem = "position_missing_source_signal_id"
        suggested = "trade records should include source_signal_id when they come from a plan"
    elif record.get("trade_link_state") == "no_trade_record":
        problem = "position_no_trade_record"
        suggested = "positions should have a matching trade record or explicit core/watch classification"
    elif record.get("risk_state") == "close_to_invalidation":
        problem = "position_close_to_invalidation"
        suggested = "positions near invalidation should have an explicit human review note"
    else:
        return None
    return {
        "kind": "daily_lesson",
        "date": record.get("date"),
        "lesson_type": "position_discipline",
        "symbol": symbol,
        "setup": str(record.get("setup") or "position_discipline"),
        "problem": problem,
        "evidence": [f"{symbol}: {problem}; risk_state={record.get('risk_state')}; trade_link_state={record.get('trade_link_state')}"],
        "suggested_constraint": suggested,
        "status": "candidate",
    }


def enrich_lessons_with_journal_context(
    lessons: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    outcomes_by_key = {
        (str(record.get("review_date") or ""), str(record.get("symbol") or "").upper()): record
        for record in outcomes
        if record.get("symbol")
    }
    trades_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        key = (str(trade.get("date") or ""), str(trade.get("symbol") or "").split(".", 1)[0].upper())
        if key[0] and key[1]:
            trades_by_key[key].append(trade)

    enriched = []
    for lesson in lessons:
        item = dict(lesson)
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence = [str(value) for value in evidence if value]
        key = (str(item.get("date") or ""), str(item.get("symbol") or "").upper())
        outcome = outcomes_by_key.get(key)
        if outcome:
            evidence.append(f"{key[1]} outcome={outcome.get('outcome')}")
        symbol_trades = trades_by_key.get(key, [])
        if symbol_trades:
            statuses = ",".join(sorted({str(trade.get("status") or "unknown") for trade in symbol_trades}))
            evidence.append(f"{key[1]} trade_status={statuses}")
        item["evidence"] = evidence
        enriched.append(item)
    return enriched


def most_common(values: list[str]) -> str | None:
    clean = [value for value in values if value]
    if not clean:
        return None
    return Counter(clean).most_common(1)[0][0]


def build_candidates(lessons: list[dict[str, Any]], min_count: int) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for lesson in lessons:
        problem = str(lesson.get("problem") or "unknown_problem")
        setup = str(lesson.get("setup") or "unknown_setup")
        lesson_type = str(lesson.get("lesson_type") or "general")
        groups[pattern_id(problem, setup, lesson_type)].append(lesson)

    candidates = []
    for pid, items in sorted(groups.items()):
        if len(items) < min_count:
            continue
        dates = sorted(str(item.get("date")) for item in items if item.get("date"))
        symbols = sorted({str(item.get("symbol")).upper() for item in items if item.get("symbol")})
        evidence: list[str] = []
        for item in items:
            raw = item.get("evidence")
            if isinstance(raw, list):
                evidence.extend(str(value) for value in raw if value)
            elif raw:
                evidence.append(str(raw))
        candidate = {
            "kind": "pattern_candidate",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pattern_id": pid,
            "lesson_type": most_common([str(item.get("lesson_type") or "") for item in items]),
            "problem": most_common([str(item.get("problem") or "") for item in items]),
            "setup": most_common([str(item.get("setup") or "") for item in items]),
            "seen_count": len(items),
            "first_seen": dates[0] if dates else None,
            "last_seen": dates[-1] if dates else None,
            "symbols": symbols,
            "evidence": evidence[:10],
            "suggested_constraint": most_common([str(item.get("suggested_constraint") or "") for item in items]),
            "promotion_status": "needs_human_review",
        }
        candidates.append({key: value for key, value in candidate.items() if value not in (None, [], "")})
    return candidates


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def build_markdown(end_date: date, lookback_days: int, candidates: list[dict[str, Any]]) -> str:
    lines = [
        f"# Learning Pattern Review（截至 {end_date.isoformat()}）",
        "",
        "## 总览",
        f"- 回看窗口：{lookback_days} 天",
        f"- 候选规律数：{len(candidates)}",
        "",
        "## 候选规律",
    ]
    if not candidates:
        lines.append("- 暂无达到重复阈值的候选规律。")
    for candidate in candidates:
        lines.extend(
            [
                f"### {candidate.get('pattern_id')}",
                f"- 问题：{candidate.get('problem')}",
                f"- setup：{candidate.get('setup')}",
                f"- 出现次数：{candidate.get('seen_count')}（{candidate.get('first_seen')} 至 {candidate.get('last_seen')}）",
                f"- 建议约束：{candidate.get('suggested_constraint')}",
                f"- 晋升状态：{candidate.get('promotion_status')}",
                "",
            ]
        )
    lines.extend(
        [
            "## 边界",
            "- 候选规律只来自运行期复盘，不自动修改 canonical rulebook。",
            "- 进入 validated_lessons.md 需要人工触发 promote-lesson。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    records = read_jsonl(lessons_path(repo_root, args.learning_dir))
    position_records_all = read_jsonl(journal_path(repo_root, args.journal_dir, "position_review"))
    outcome_records_all = read_jsonl(journal_path(repo_root, args.journal_dir, "outcome"))
    trade_records_all = read_jsonl(journal_path(repo_root, args.journal_dir, "trade"))
    end_date = choose_end_date(records + position_records_all, args.end_date)
    lessons = selected_lessons(records, end_date, args.lookback_days)
    position_records = selected_records(position_records_all, end_date, args.lookback_days, "date")
    outcome_records = selected_records(outcome_records_all, end_date, args.lookback_days, "review_date")
    trade_records = selected_records(trade_records_all, end_date, args.lookback_days, "date")
    lessons = enrich_lessons_with_journal_context(lessons, outcome_records, trade_records)
    synthetic_position_lessons = [lesson for lesson in (position_lesson(record) for record in position_records) if lesson]
    learning_events = lessons + synthetic_position_lessons
    candidates = build_candidates(learning_events, args.min_count)

    candidate_path = candidates_path(repo_root, args.learning_dir)
    write_jsonl(candidate_path, candidates)
    markdown_path, json_path = report_paths(repo_root, args.output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "end_date": end_date.isoformat(),
        "lookback_days": args.lookback_days,
        "summary": {
            "daily_lessons": len(lessons),
            "outcomes": len(outcome_records),
            "trades": len(trade_records),
            "position_reviews": len(position_records),
            "learning_events": len(learning_events),
            "pattern_candidates": len(candidates),
            "min_count": args.min_count,
        },
        "pattern_candidates": candidates,
        "artifacts": [str(markdown_path), str(json_path), str(candidate_path)],
    }
    markdown_path.write_text(build_markdown(end_date, args.lookback_days, candidates), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", **payload}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate daily learning lessons into pattern candidates")
    parser.add_argument("--lookback-days", type=int, default=20)
    parser.add_argument("--min-count", type=int, default=3)
    parser.add_argument("--end-date")
    parser.add_argument("--output", help="Output base path without extension")
    parser.add_argument("--learning-dir", default="runtime/learning")
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


if __name__ == "__main__":
    main()
