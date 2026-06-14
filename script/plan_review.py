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


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


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


def normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def signals_by_symbol(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    raw_signals = payload.get("signals")
    if not isinstance(raw_signals, list):
        return result
    for signal in raw_signals:
        if not isinstance(signal, dict):
            continue
        symbol = normalize_symbol(signal.get("symbol"))
        if symbol:
            result[symbol] = signal
    return result


def intraday_states_by_symbol(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    symbols = payload.get("symbols")
    if not isinstance(symbols, dict):
        return {}
    return {
        normalize_symbol(symbol): state
        for symbol, state in symbols.items()
        if normalize_symbol(symbol) and isinstance(state, dict)
    }


def submission_entry_identity(entry: dict[str, Any]) -> tuple[str | None, str | None]:
    intent = entry.get("intent") if isinstance(entry.get("intent"), dict) else {}
    preview = entry.get("preview") if isinstance(entry.get("preview"), dict) else {}
    source_signal_id = (
        intent.get("source_signal_id")
        or nested(intent, "source", "signal_id")
        or preview.get("source_signal_id")
        or nested(preview, "source", "signal_id")
    )
    symbol = normalize_symbol(intent.get("symbol") or preview.get("symbol"))
    return str(source_signal_id) if source_signal_id else None, symbol or None


def paper_submission_lookup(payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    by_signal_id: dict[str, str] = {}
    by_symbol: dict[str, str] = {}
    for state in ("submitted", "ready", "blocked", "skipped_duplicates", "errors"):
        entries = payload.get(state)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_signal_id, symbol = submission_entry_identity(entry)
            if source_signal_id:
                by_signal_id.setdefault(source_signal_id, state)
            if symbol:
                by_symbol.setdefault(symbol, state)
    return by_signal_id, by_symbol


def paper_state_for(signal: dict[str, Any], by_signal_id: dict[str, str], by_symbol: dict[str, str]) -> str:
    signal_id = str(signal.get("signal_id") or "")
    symbol = normalize_symbol(signal.get("symbol"))
    return by_signal_id.get(signal_id) or by_symbol.get(symbol) or "not_ready"


def price_triggered(outcome: str) -> bool:
    return outcome in {"triggered", "triggered_and_invalidated"}


def intraday_confirmed(state: str | None) -> bool:
    return state in {
        "triggered",
        "triggered_but_blocked",
        "triggered_but_failed_hold",
        "triggered_and_invalidated",
        "no_chase_gap",
    }


def codex_candidate(signal: dict[str, Any] | None) -> bool:
    if not isinstance(signal, dict):
        return False
    status = str(signal.get("execution_status") or signal.get("plan_type") or "")
    return status in {"conditional_executable", "watch_only"}


def build_execution_layers(
    *,
    outcome: str,
    intraday_state: str | None,
    monitor_signal: dict[str, Any] | None,
    paper_submission_state: str,
    trade_count: int,
) -> dict[str, bool]:
    return {
        "price_triggered": price_triggered(outcome),
        "intraday_confirmed": intraday_confirmed(intraday_state),
        "codex_candidate": codex_candidate(monitor_signal),
        "paper_ready": paper_submission_state in {"ready", "submitted"},
        "paper_submitted": paper_submission_state == "submitted",
        "trade_recorded": trade_count > 0,
    }


def position_discipline(position_reviews: list[dict[str, Any]], plan_reviews: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[str]]]:
    planned_without_trade_record = sorted(
        {
            str(review.get("symbol")).upper()
            for review in plan_reviews
            if review.get("symbol") and review.get("trade_state") == "no_trade_record"
        }
    )
    positions_without_plan = sorted(
        {
            str(record.get("symbol")).upper()
            for record in position_reviews
            if record.get("symbol") and not record.get("in_today_signals")
        }
    )
    missing_trade_link = sorted(
        {
            str(record.get("symbol")).upper()
            for record in position_reviews
            if record.get("symbol") and record.get("trade_link_state") != "linked_to_source_signal"
        }
    )
    missing_source_signal_id = sorted(
        {
            str(record.get("symbol")).upper()
            for record in position_reviews
            if record.get("symbol") and record.get("trade_link_state") == "trade_missing_source_signal_id"
        }
    )
    close_without_trade = sorted(
        {
            str(record.get("symbol")).upper()
            for record in position_reviews
            if record.get("symbol")
            and record.get("risk_state") == "close_to_invalidation"
            and record.get("trade_link_state") != "linked_to_source_signal"
        }
    )
    details = {
        "planned_without_trade_record": planned_without_trade_record,
        "positions_without_plan": positions_without_plan,
        "missing_trade_link": missing_trade_link,
        "missing_source_signal_id": missing_source_signal_id,
        "close_to_invalidation_without_trade_record": close_without_trade,
    }
    summary = {
        "position_reviews": len(position_reviews),
        "review_required": sum(1 for record in position_reviews if record.get("review_required")),
        "planned_without_trade_record": len(planned_without_trade_record),
        "positions_without_plan": len(positions_without_plan),
        "missing_trade_link": len(missing_trade_link),
        "missing_source_signal_id": len(missing_source_signal_id),
        "close_to_invalidation_without_trade_record": len(close_without_trade),
        "trade_link_state": dict(Counter(str(record.get("trade_link_state") or "unknown") for record in position_reviews)),
        "risk_state": dict(Counter(str(record.get("risk_state") or "unknown") for record in position_reviews)),
    }
    return summary, details


def build_plan_review(
    signal: dict[str, Any],
    outcome: dict[str, Any] | None,
    symbol_trades: list[dict[str, Any]],
    intraday_state: dict[str, Any] | None = None,
    monitor_signal: dict[str, Any] | None = None,
    paper_submission_state: str = "not_ready",
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

    outcome_state = outcome.get("outcome") if outcome else "missing_outcome"
    intraday_state_name = str(intraday_state.get("state")) if isinstance(intraday_state, dict) and intraday_state.get("state") else None
    layers = build_execution_layers(
        outcome=str(outcome_state),
        intraday_state=intraday_state_name,
        monitor_signal=monitor_signal,
        paper_submission_state=paper_submission_state,
        trade_count=len(symbol_trades),
    )

    return {
        "signal_id": signal.get("signal_id"),
        "session": signal.get("session"),
        "signal_date": signal.get("date"),
        "symbol": signal.get("symbol"),
        "setup": signal.get("setup"),
        "plan_type": plan_type,
        "execution_status": execution_status,
        "quality_state": quality_state,
        "missing_fields": missing_fields,
        "outcome": outcome_state,
        "intraday_state": intraday_state_name,
        "intraday_bar_timestamp": intraday_state.get("bar_timestamp") if isinstance(intraday_state, dict) else None,
        "codex_review_status": monitor_signal.get("execution_status") if isinstance(monitor_signal, dict) else None,
        "codex_review_plan_type": monitor_signal.get("plan_type") if isinstance(monitor_signal, dict) else None,
        "paper_submission_state": paper_submission_state,
        "execution_layers": layers,
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


def position_lesson(date: str, record: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(record.get("symbol") or "").upper()
    if not symbol:
        return None
    if not record.get("in_today_signals"):
        problem = "position_without_plan"
        suggested = "trading positions should link to an active plan or be classified as core/watch"
    elif record.get("trade_link_state") == "trade_missing_source_signal_id":
        problem = "position_missing_source_signal_id"
        suggested = "trade records should include source_signal_id when linked to a plan"
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
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": date,
        "lesson_type": "position_discipline",
        "symbol": symbol,
        "setup": str(record.get("setup") or "position_discipline"),
        "problem": problem,
        "evidence": [f"{symbol}: {problem}; risk_state={record.get('risk_state')}; trade_link_state={record.get('trade_link_state')}"],
        "suggested_constraint": suggested,
        "status": "candidate",
    }


def summarize(reviews: list[dict[str, Any]], position_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    layer_names = ("price_triggered", "intraday_confirmed", "codex_candidate", "paper_ready", "paper_submitted", "trade_recorded")
    return {
        "plans": len(reviews),
        "sessions": dict(Counter(str(item.get("session") or "unknown") for item in reviews)),
        "quality": dict(Counter(str(item.get("quality_state")) for item in reviews)),
        "outcomes": dict(Counter(str(item.get("outcome")) for item in reviews)),
        "trade_state": dict(Counter(str(item.get("trade_state")) for item in reviews)),
        "execution_layers": {
            name: sum(1 for item in reviews if (item.get("execution_layers") or {}).get(name))
            for name in layer_names
        },
        "position_discipline": position_summary or {
            "position_reviews": 0,
            "review_required": 0,
            "planned_without_trade_record": 0,
            "positions_without_plan": 0,
            "missing_trade_link": 0,
            "missing_source_signal_id": 0,
            "close_to_invalidation_without_trade_record": 0,
            "trade_link_state": {},
            "risk_state": {},
        },
    }


def csv_or_none(values: list[str]) -> str:
    return ", ".join(values) if values else "无"


def reviews_by_session(reviews: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        session = str(review.get("session") or "unknown")
        grouped.setdefault(session, []).append(review)
    return {session: grouped[session] for session in sorted(grouped)}


def session_groups(reviews: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        session: [str(review.get("symbol") or "") for review in items]
        for session, items in reviews_by_session(reviews).items()
    }


def build_markdown(date: str, reviews: list[dict[str, Any]], summary: dict[str, Any], position_details: dict[str, list[str]]) -> str:
    layers = summary.get("execution_layers", {})
    lines = [
        f"# 日度交易计划复盘（{date}）",
        "",
        "## 总览",
        f"- 计划数：{summary['plans']}",
        f"- session：{json.dumps(summary.get('sessions', {}), ensure_ascii=False, sort_keys=True)}",
        f"- 计划质量：{json.dumps(summary['quality'], ensure_ascii=False, sort_keys=True)}",
        f"- 触达结果：{json.dumps(summary['outcomes'], ensure_ascii=False, sort_keys=True)}",
        f"- 真实执行：{json.dumps(summary['trade_state'], ensure_ascii=False, sort_keys=True)}",
        "",
        "## 执行漏斗分层",
        (
            "- "
            f"价格触发={layers.get('price_triggered', 0)}；"
            f"盘中确认={layers.get('intraday_confirmed', 0)}；"
            f"Codex 候选={layers.get('codex_candidate', 0)}；"
            f"paper ready={layers.get('paper_ready', 0)}；"
            f"paper submitted={layers.get('paper_submitted', 0)}；"
            f"trade recorded={layers.get('trade_recorded', 0)}"
        ),
        "",
        "## 逐计划复盘（按 session 分组）",
    ]
    if not reviews:
        lines.append("- 暂无可复盘计划。")
    for session, session_reviews in reviews_by_session(reviews).items():
        lines.append(f"### {session}")
        for review in session_reviews:
            lines.extend(
                [
                    f"#### {review.get('symbol')}",
                    f"- session：{review.get('session') or 'unknown'}",
                    f"- 信号日期：{review.get('signal_date') or 'unknown'}",
                    f"- 类型：{review.get('plan_type')} / {review.get('execution_status')}",
                    f"- 质量：{review.get('quality_state')}",
                    f"- 缺失字段：{', '.join(review.get('missing_fields') or []) or '无'}",
                    f"- 价格触达：{review.get('outcome')}",
                    f"- 盘中状态：{review.get('intraday_state') or 'unknown'}",
                    f"- Codex 盘中评审：{review.get('codex_review_plan_type') or 'unknown'} / {review.get('codex_review_status') or 'unknown'}",
                    f"- paper 提交流程：{review.get('paper_submission_state')}",
                    f"- 真实执行：{review.get('trade_state')}",
                    "",
                ]
            )
    discipline = summary["position_discipline"]
    lines.extend(
        [
            "## 持仓纪律复盘",
            f"- 持仓复核记录数：{discipline['position_reviews']}",
            f"- 需人工复核：{discipline['review_required']}",
            f"- 有计划但无交易记录：{csv_or_none(position_details.get('planned_without_trade_record', []))}",
            f"- 有持仓但无计划：{csv_or_none(position_details.get('positions_without_plan', []))}",
            f"- 持仓缺 trade/source link：{csv_or_none(position_details.get('missing_trade_link', []))}",
            f"- 有持仓但缺 source_signal_id：{csv_or_none(position_details.get('missing_source_signal_id', []))}",
            f"- 接近失效位但无完整交易关联：{csv_or_none(position_details.get('close_to_invalidation_without_trade_record', []))}",
            f"- 持仓交易关联：{json.dumps(discipline.get('trade_link_state', {}), ensure_ascii=False, sort_keys=True)}",
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
    position_reviews = [
        record
        for record in read_jsonl(journal_path(repo_root, args.journal_dir, "position_review"))
        if record.get("kind") == "position_review" and record.get("date") == args.date
    ]
    trade_map = trades_by_symbol(trades)
    intraday_state_payload = read_json(repo_root / "runtime" / "intraday" / args.date / "state.json", {})
    monitor_signals_payload = read_json(repo_root / "report" / args.date / "monitor-signals.json", {})
    paper_submission_payload = read_json(repo_root / "report" / args.date / "paper-trade-submission.json", {})
    intraday_map = intraday_states_by_symbol(intraday_state_payload)
    monitor_map = signals_by_symbol(monitor_signals_payload)
    paper_by_signal_id, paper_by_symbol = paper_submission_lookup(paper_submission_payload)

    reviews = []
    for signal in signals:
        sid = str(signal.get("signal_id") or "")
        symbol = str(signal.get("symbol") or "").split(".", 1)[0].upper()
        reviews.append(
            build_plan_review(
                signal,
                outcomes_by_signal.get(sid),
                trade_map.get(symbol, []),
                intraday_state=intraday_map.get(symbol),
                monitor_signal=monitor_map.get(symbol),
                paper_submission_state=paper_state_for(signal, paper_by_signal_id, paper_by_symbol),
            )
        )

    position_summary, position_details = position_discipline(position_reviews, reviews)
    summary = summarize(reviews, position_summary)
    markdown_path, json_path = output_paths(repo_root, args.date, args.output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": args.date,
        "plan_reviews": reviews,
        "session_groups": session_groups(reviews),
        "position_discipline": position_details,
        "summary": summary,
    }
    markdown_path.write_text(build_markdown(args.date, reviews, summary, position_details), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lessons = [lesson for lesson in (lesson_for_review(args.date, review) for review in reviews) if lesson]
    lessons.extend(lesson for lesson in (position_lesson(args.date, record) for record in position_reviews) if lesson)
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
