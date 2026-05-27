#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_append import append_jsonl, journal_path
from journal_review import planned_target_date, read_jsonl, summarize


def parse_week(week: str) -> tuple[dt.date, dt.date]:
    if "-W" not in week:
        raise ValueError("week must use YYYY-Www format")
    year_text, week_text = week.split("-W", 1)
    year = int(year_text)
    week_number = int(week_text)
    start = dt.date.fromisocalendar(year, week_number, 1)
    end = dt.date.fromisocalendar(year, week_number, 7)
    return start, end


def in_range(value: Any, start: dt.date, end: dt.date) -> bool:
    if not isinstance(value, str):
        return False
    try:
        current = dt.date.fromisoformat(value)
    except ValueError:
        return False
    return start <= current <= end


def review_id(week: str) -> str:
    return f"weekly:{week}"


def existing_review_ids(path: Path) -> set[str]:
    ids = set()
    for record in read_jsonl(path):
        value = record.get("review_id")
        if isinstance(value, str):
            ids.add(value)
    return ids


def output_path(repo_root: Path, week: str, explicit_output: str | None) -> Path:
    if explicit_output:
        path = Path(explicit_output)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / "weekly" / f"{week}.md"


def build_markdown(
    *,
    week: str,
    start: dt.date,
    end: dt.date,
    signals: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    position_reviews: list[dict[str, Any]],
) -> str:
    outcome_summary = summarize(outcomes)
    trade_status = Counter(str(trade.get("status") or "unknown") for trade in trades)
    trade_link_status = Counter(str(record.get("trade_link_state") or "unknown") for record in position_reviews)
    position_required = sum(1 for record in position_reviews if record.get("review_required"))
    result_r = [
        float(trade["result_r"])
        for trade in trades
        if isinstance(trade.get("result_r"), (int, float))
    ]
    total_r = round(sum(result_r), 3) if result_r else None

    lines = [
        f"# 周度交易系统复盘（{week}）",
        "",
        "## 范围",
        f"- 日期：{start.isoformat()} 至 {end.isoformat()}",
        f"- 计划/观察信号数：{len(signals)}",
        f"- outcome 数：{len(outcomes)}",
        f"- trades 数：{len(trades)}",
        f"- position review 数：{len(position_reviews)}",
        "",
        "## 信号表现",
        f"- outcome 汇总：{json.dumps(outcome_summary['by_outcome'], ensure_ascii=False, sort_keys=True)}",
    ]
    if outcome_summary["by_setup"]:
        lines.append("- setup 汇总：")
        for setup, counts in outcome_summary["by_setup"].items():
            lines.append(f"  - {setup}：{json.dumps(counts, ensure_ascii=False, sort_keys=True)}")
    if outcome_summary["by_symbol"]:
        lines.append("- symbol 汇总：")
        for symbol, counts in outcome_summary["by_symbol"].items():
            lines.append(f"  - {symbol}：{json.dumps(counts, ensure_ascii=False, sort_keys=True)}")

    lines.extend(
        [
            "",
            "## 实际执行",
            f"- trade 状态：{json.dumps(dict(trade_status), ensure_ascii=False, sort_keys=True)}",
            f"- 持仓交易关联：{json.dumps(dict(trade_link_status), ensure_ascii=False, sort_keys=True)}",
            f"- 合计 R：{total_r if total_r is not None else '暂无 result_r'}",
            f"- 持仓需人工复核：{position_required}",
            "",
            "## 本周纪律结论",
            "- 只把 outcomes 当作客观触达统计，不当作真实胜率。",
            "- 将真实执行与 signal 通过 source_signal_id 连接，避免复盘只停留在计划层。",
            "- 下周优先减少 not_evaluable 信号，所有 actionable signal 必须结构化价格与风险。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_review_record(week: str, start: dt.date, end: dt.date, path: Path, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize(outcomes)
    return {
        "kind": "review",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_id": review_id(week),
        "date": end.isoformat(),
        "scope": "weekly",
        "summary": f"{week} outcomes={summary['by_outcome']}",
        "outcome": summary["by_outcome"],
        "source_report": str(path),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    start, end = parse_week(args.week)
    signals_file = journal_path(repo_root, args.journal_dir, "signal")
    outcomes_file = journal_path(repo_root, args.journal_dir, "outcome")
    trades_file = journal_path(repo_root, args.journal_dir, "trade")
    reviews_file = journal_path(repo_root, args.journal_dir, "review")

    signals = [
        record
        for record in read_jsonl(signals_file)
        if record.get("kind") == "signal" and in_range(planned_target_date(record), start, end)
    ]
    outcomes = [
        record
        for record in read_jsonl(outcomes_file)
        if record.get("kind") == "outcome" and in_range(record.get("review_date"), start, end)
    ]
    trades = [
        record
        for record in read_jsonl(trades_file)
        if record.get("kind") == "trade" and in_range(record.get("date"), start, end)
    ]
    position_reviews_file = journal_path(repo_root, args.journal_dir, "position_review")
    position_reviews = [
        record
        for record in read_jsonl(position_reviews_file)
        if record.get("kind") == "position_review" and in_range(record.get("date"), start, end)
    ]

    output = output_path(repo_root, args.week, args.output)
    markdown = build_markdown(
        week=args.week,
        start=start,
        end=end,
        signals=signals,
        outcomes=outcomes,
        trades=trades,
        position_reviews=position_reviews,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")

    appended = []
    skipped_duplicates = []
    if args.append:
        rid = review_id(args.week)
        existing = existing_review_ids(reviews_file)
        if rid in existing:
            skipped_duplicates.append(rid)
        else:
            append_jsonl(reviews_file, build_review_record(args.week, start, end, output, outcomes))
            appended.append(rid)

    return {
        "status": "success",
        "week": args.week,
        "date": end.isoformat(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "output": str(output),
        "summary": summarize(outcomes),
        "signals_count": len(signals),
        "trades_count": len(trades),
        "position_reviews_count": len(position_reviews),
        "reviews_path": str(reviews_file) if args.append else None,
        "append": args.append,
        "appended": appended,
        "skipped_duplicates": skipped_duplicates,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a weekly Trading Copilot review")
    parser.add_argument("--week", required=True, help="ISO week in YYYY-Www format")
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--output")
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
