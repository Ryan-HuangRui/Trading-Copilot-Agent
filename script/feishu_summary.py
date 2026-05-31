#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from journal_review import read_jsonl
from signal_artifacts import read_json, resolve_signals_path


def resolve_path(repo_root: Path, value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def output_path(repo_root: Path, date: str, explicit_output: str | None) -> Path:
    return resolve_path(repo_root, explicit_output, repo_root / "report" / date / "feishu-summary.md")


def artifact_json(repo_root: Path, path: str | None, default: Path) -> dict[str, Any]:
    target = resolve_path(repo_root, path, default)
    if not target.exists():
        return {}
    try:
        return read_json(target)
    except Exception:
        return {}


def session_title(session: str) -> str:
    if session == "monitor":
        return "盘中"
    return "今日" if session == "pre-market" else "明日"


def signal_symbol(signal: dict[str, Any]) -> str:
    return str(signal.get("symbol") or "").upper()


def compact_price(value: Any) -> str:
    if value in (None, ""):
        return "待定"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def signal_note(signal: dict[str, Any]) -> str:
    return str(signal.get("notes") or signal.get("setup") or "无补充")


def split_signals(signals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    executable = []
    watch = []
    no_trade = []
    for signal in signals:
        status = signal.get("execution_status")
        plan_type = signal.get("plan_type")
        if status == "conditional_executable" and plan_type == "trade_plan":
            executable.append(signal)
        elif status == "no_trade" or plan_type == "no_trade" or signal.get("status") == "no_trade":
            no_trade.append(signal)
        else:
            watch.append(signal)
    return executable, watch, no_trade


def lessons_for_date(repo_root: Path, date: str, learning_dir: str) -> list[dict[str, Any]]:
    path = Path(learning_dir)
    if not path.is_absolute():
        path = repo_root / path
    return [
        record
        for record in read_jsonl(path / "daily_lessons.jsonl")
        if record.get("kind") == "daily_lesson" and record.get("date") == date
    ]


def build_markdown(
    *,
    date: str,
    session: str,
    signals: list[dict[str, Any]],
    position_review: dict[str, Any],
    plan_review: dict[str, Any],
    data_quality: dict[str, Any],
    lessons: list[dict[str, Any]],
    signal_source_summary: dict[str, Any] | None = None,
) -> str:
    title_prefix = session_title(session)
    executable, watch, no_trade = split_signals(signals)
    position_summary = position_review.get("summary") if isinstance(position_review.get("summary"), dict) else {}
    plan_summary = plan_review.get("summary") if isinstance(plan_review.get("summary"), dict) else {}

    lines = [
        f"# 飞书执行摘要（{date} {session}）",
        "",
        f"【{title_prefix}可执行交易计划】",
    ]
    if not executable:
        lines.append("- 无。")
    for signal in executable:
        entry = compact_price((signal.get("entry") or {}).get("trigger_price"))
        stop = compact_price((signal.get("stop") or {}).get("initial_stop"))
        tp1 = compact_price((signal.get("take_profit") or {}).get("tp1"))
        risk = compact_price((signal.get("risk") or {}).get("max_account_risk_pct"))
        lines.append(f"- {signal_symbol(signal)}：入场 {entry}；止损 {stop}；TP1 {tp1}；风险 <= {risk}%；状态：等待条件触发。")

    lines.extend(["", "【观察候选】"])
    if not watch:
        lines.append("- 无。")
    for signal in watch:
        lines.append(f"- {signal_symbol(signal)}：{signal_note(signal)}")

    lines.extend(["", "【NO TRADE】"])
    if not no_trade:
        lines.append("- 无。")
    for signal in no_trade:
        lines.append(f"- {signal_symbol(signal)}：{signal_note(signal)}")

    focused_fallback = data_quality.get("focused_fallback_symbols")
    lines.extend(
        [
            "",
            "【数据质量】",
            f"- 状态：{data_quality.get('status', 'unknown')}",
            f"- stale_data：{data_quality.get('stale_data', 'unknown')}",
        ]
    )
    if isinstance(focused_fallback, list) and focused_fallback:
        for row in focused_fallback:
            if isinstance(row, dict):
                lines.append(
                    f"- {row.get('symbol')} 使用 {row.get('provider')} fallback；"
                    f"primary={row.get('fallback_from')}；原因：{row.get('primary_error')}"
                )
    else:
        lines.append("- 重点标的无 fallback 记录。")

    if session == "monitor":
        summary = signal_source_summary or {}
        lines.extend(
            [
                "",
                "【盘中候选状态】",
                f"- candidate：{summary.get('candidate', len(watch))}",
                f"- blocked：{summary.get('blocked', 0)}",
                f"- skipped：{summary.get('skipped', 0)}",
                "- monitor 候选仅用于 dry-run 和人工观察，不能自动执行。",
            ]
        )

    lines.extend(
        [
            "",
            "【持仓复核摘要】",
            f"- 持仓数：{position_summary.get('positions', 0)}",
            f"- 需人工复核：{position_summary.get('review_required', 0)}",
            f"- 缺交易关联：{position_summary.get('trade_link_missing', 0)}",
            "",
            "【昨日计划复盘】",
        ]
    )
    if plan_summary:
        lines.extend(
            [
                f"- 计划数：{plan_summary.get('plans', 0)}",
                f"- 计划质量：{json.dumps(plan_summary.get('quality', {}), ensure_ascii=False, sort_keys=True)}",
                f"- 价格触达：{json.dumps(plan_summary.get('outcomes', {}), ensure_ascii=False, sort_keys=True)}",
                f"- 真实执行：{json.dumps(plan_summary.get('trade_state', {}), ensure_ascii=False, sort_keys=True)}",
            ]
        )
    else:
        lines.append("- 暂无 plan-review 产物。")

    lines.extend(["", "【今日新增 lesson】"])
    if not lessons:
        lines.append("- 无。")
    for lesson in lessons[:5]:
        lines.append(f"- {lesson.get('problem')}：{lesson.get('suggested_constraint')}")

    lines.extend(
        [
            "",
            "【边界】",
            "- 本摘要只压缩展示条件化计划、复盘和风险提示，不构成投资建议。",
            "- 不自动下单；任何交易执行必须人工确认。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    signals_file = resolve_signals_path(repo_root, args.date, args.signals, args.session)
    payload = read_json(signals_file)
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    signal_source_summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    position_review = artifact_json(repo_root, args.position_review, repo_root / "report" / args.date / "position-review.json")
    plan_review = artifact_json(repo_root, args.plan_review, repo_root / "report" / args.date / "plan-review.json")
    data_quality = artifact_json(repo_root, None, repo_root / "report" / args.date / "data-quality.json")
    lessons = lessons_for_date(repo_root, args.date, args.learning_dir)
    output = output_path(repo_root, args.date, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_markdown(
            date=args.date,
            session=args.session,
            signals=signals,
            position_review=position_review,
            plan_review=plan_review,
            data_quality=data_quality,
            lessons=lessons,
            signal_source_summary=signal_source_summary,
        ),
        encoding="utf-8",
    )
    executable, watch, no_trade = split_signals(signals)
    return {
        "status": "success",
        "date": args.date,
        "session": args.session,
        "output": str(output),
        "source_signals": str(signals_file),
        "summary": {
            "conditional_executable": len(executable),
            "watch_only": len(watch),
            "no_trade": len(no_trade),
            "lessons": len(lessons),
            "data_quality_status": data_quality.get("status"),
            "focused_fallback_symbols": len(data_quality.get("focused_fallback_symbols", []))
            if isinstance(data_quality.get("focused_fallback_symbols"), list)
            else 0,
            "candidate": signal_source_summary.get("candidate"),
            "blocked": signal_source_summary.get("blocked"),
            "skipped": signal_source_summary.get("skipped"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a concise Feishu-ready execution summary")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market", "monitor"], required=True)
    parser.add_argument("--signals")
    parser.add_argument("--position-review")
    parser.add_argument("--plan-review")
    parser.add_argument("--output")
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
