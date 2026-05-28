#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_append import append_jsonl, journal_path
from journal_review import planned_target_date, read_jsonl


def output_paths(repo_root: Path, date: str, explicit_output: str | None) -> tuple[Path, Path]:
    if explicit_output:
        base = Path(explicit_output)
        if not base.is_absolute():
            base = repo_root / base
    else:
        base = repo_root / "report" / date / "plan-review"
    return base.with_suffix(".md"), base.with_suffix(".json")


def learning_path(repo_root: Path, learning_dir: str) -> Path:
    base = Path(learning_dir)
    if not base.is_absolute():
        base = repo_root / base
    return base / "daily_lessons.jsonl"


def nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def has_trade_plan_card(signal: dict[str, Any]) -> bool:
    return all(
        item is not None
        for item in (
            nested(signal, "entry", "trigger_price"),
            nested(signal, "stop", "initial_stop"),
            nested(signal, "take_profit", "tp1"),
            nested(signal, "risk_detail", "max_account_risk_pct") or nested(signal, "risk", "max_account_risk_pct"),
            nested(signal, "risk_detail", "risk_per_share") or nested(signal, "risk", "risk_per_share"),
            nested(signal, "execution_rules", "skip_conditions"),
        )
    )


def missing_trade_plan_fields(signal: dict[str, Any]) -> list[str]:
    checks = {
        "entry.trigger_price": nested(signal, "entry", "trigger_price"),
        "stop.initial_stop": nested(signal, "stop", "initial_stop"),
        "take_profit.tp1": nested(signal, "take_profit", "tp1"),
        "risk.max_account_risk_pct": nested(signal, "risk_detail", "max_account_risk_pct") or nested(signal, "risk", "max_account_risk_pct"),
        "risk.risk_per_share": nested(signal, "risk_detail", "risk_per_share") or nested(signal, "risk", "risk_per_share"),
        "execution_rules.skip_conditions": nested(signal, "execution_rules", "skip_conditions"),
    }
    return [field for field, value in checks.items() if value in (None, [])]


def trades_by_symbol(trades: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        symbol = str(trade.get("symbol") or "").split(".", 1)[0].upper()
        if symbol:
            result.setdefault(symbol, []).append(trade)
    return result


def build_plan_review(
    signal: dict[str, Any],
    outcome: dict[str, Any] | None,
    symbol_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    plan_type = str(signal.get("plan_type") or "legacy_signal")
    execution_status = str(signal.get("execution_status") or signal.get("status") or "unknown")
    missing_fields = missing_trade_plan_fields(signal)
    if execution_status == "conditional_executable" and missing_fields:
        quality_state = "incomplete_trade_plan"
    elif execution_status == "conditional_executable":
        quality_state = "complete_trade_plan"
    elif execution_status == "watch_only":
        quality_state = "watch_only"
    elif execution_status == "no_trade":
        quality_state = "no_trade"
    else:
        quality_state = "legacy_or_unclassified"

    return {
        "signal_id": signal.get("signal_id"),
        "symbol": signal.get("symbol"),
        "setup": signal.get("setup"),
        "plan_type": plan_type,
        "execution_status": execution_status,
        "quality_state": quality_state,
        "missing_fields": missing_fields,
        "outcome": outcome.get("outcome") if outcome else "missing_outcome",
        "trade_state": "has_trade_record" if symbol_trades else "no_trade_record",
        "trade_count": len(symbol_trades),
    }


def lesson_for_review(date: str, review: dict[str, Any]) -> dict[str, Any] | None:
    missing = review.get("missing_fields") or []
    if "take_profit.tp1" in missing:
        problem = "missing_take_profit"
        suggested = "conditional_executable plans must include TP1 and minimum RR"
    elif missing:
        problem = "incomplete_trade_plan"
        suggested = "conditional_executable plans must include entry, stop, TP1, risk, and execution rules"
    elif review.get("outcome") == "missing_outcome":
        problem = "missing_outcome"
        suggested = "review snapshot coverage before judging plan quality"
    else:
        return None

    return {
        "kind": "daily_lesson",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": date,
        "lesson_type": "plan_quality",
        "symbol": review.get("symbol"),
        "setup": review.get("setup"),
        "problem": problem,
        "evidence": [f"{review.get('symbol')}: {problem}"],
        "suggested_constraint": suggested,
        "status": "candidate",
    }


def summarize(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "plans": len(reviews),
        "quality": dict(Counter(str(item.get("quality_state")) for item in reviews)),
        "outcomes": dict(Counter(str(item.get("outcome")) for item in reviews)),
        "trade_state": dict(Counter(str(item.get("trade_state")) for item in reviews)),
    }


def build_markdown(date: str, reviews: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        f"# 日度交易计划复盘（{date}）",
        "",
        "## 总览",
        f"- 计划数：{summary['plans']}",
        f"- 计划质量：{json.dumps(summary['quality'], ensure_ascii=False, sort_keys=True)}",
        f"- 触达结果：{json.dumps(summary['outcomes'], ensure_ascii=False, sort_keys=True)}",
        f"- 真实执行：{json.dumps(summary['trade_state'], ensure_ascii=False, sort_keys=True)}",
        "",
        "## 逐计划复盘",
    ]
    if not reviews:
        lines.append("- 暂无可复盘计划。")
    for review in reviews:
        lines.extend(
            [
                f"### {review.get('symbol')}",
                f"- 类型：{review.get('plan_type')} / {review.get('execution_status')}",
                f"- 质量：{review.get('quality_state')}",
                f"- 缺失字段：{', '.join(review.get('missing_fields') or []) or '无'}",
                f"- 价格触达：{review.get('outcome')}",
                f"- 真实执行：{review.get('trade_state')}",
                "",
            ]
        )
    lines.extend(
        [
            "## 边界",
            "- 本复盘评价交易计划质量和价格触达，不等同于真实交易收益。",
            "- 真实执行只从 trades.jsonl 的人工记录判断。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    signals = [
        record
        for record in read_jsonl(journal_path(repo_root, args.journal_dir, "signal"))
        if record.get("kind") == "signal" and planned_target_date(record) == args.date
    ]
    outcomes_by_signal = {
        str(record.get("signal_id")): record
        for record in read_jsonl(journal_path(repo_root, args.journal_dir, "outcome"))
        if record.get("kind") == "outcome" and record.get("review_date") == args.date
    }
    trades = [
        record
        for record in read_jsonl(journal_path(repo_root, args.journal_dir, "trade"))
        if record.get("kind") == "trade" and record.get("date") == args.date
    ]
    trade_map = trades_by_symbol(trades)

    reviews = []
    for signal in signals:
        sid = str(signal.get("signal_id") or "")
        symbol = str(signal.get("symbol") or "").split(".", 1)[0].upper()
        reviews.append(build_plan_review(signal, outcomes_by_signal.get(sid), trade_map.get(symbol, [])))

    summary = summarize(reviews)
    markdown_path, json_path = output_paths(repo_root, args.date, args.output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": args.date,
        "plan_reviews": reviews,
        "summary": summary,
    }
    markdown_path.write_text(build_markdown(args.date, reviews, summary), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lessons = [lesson for lesson in (lesson_for_review(args.date, review) for review in reviews) if lesson]
    lessons_path = learning_path(repo_root, args.learning_dir)
    if args.append_lessons:
        for lesson in lessons:
            append_jsonl(lessons_path, lesson)

    return {
        "status": "success",
        "date": args.date,
        "artifacts": [str(markdown_path), str(json_path)],
        "summary": summary,
        "lessons": lessons,
        "lessons_path": str(lessons_path) if args.append_lessons else None,
        "append_lessons": args.append_lessons,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review generated trade plans and record learning lessons")
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", help="Output base path without extension")
    parser.add_argument("--append-lessons", action="store_true")
    parser.add_argument("--journal-dir", default="runtime/journal")
    parser.add_argument("--learning-dir", default="runtime/learning")
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
