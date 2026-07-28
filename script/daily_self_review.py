#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_append import append_jsonl, journal_path
from journal_review import planned_target_date, read_jsonl, summarize


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def review_id(date: str) -> str:
    return f"daily:{date}"


def existing_review_ids(path: Path) -> set[str]:
    ids = set()
    for record in read_jsonl(path):
        value = record.get("review_id")
        if isinstance(value, str):
            ids.add(value)
    return ids


def report_path(repo_root: Path, date: str) -> Path:
    return repo_root / "report" / date / "self-review.md"


def build_markdown(
    *,
    date: str,
    outcomes: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    position_reviews: list[dict[str, Any]],
    broker_executions: list[dict[str, Any]],
    broker_sources: dict[str, Any],
    post_market_exists: bool,
) -> str:
    outcome_summary = summarize(outcomes)
    by_status = Counter(str(signal.get("status") or "unknown") for signal in signals)
    by_trade = Counter(str(trade.get("status") or "unknown") for trade in trades)
    by_trade_link = Counter(str(record.get("trade_link_state") or "unknown") for record in position_reviews)
    position_review_required = sum(1 for record in position_reviews if record.get("review_required"))
    not_evaluable = [item for item in outcomes if item.get("outcome") in {"not_evaluable", "no_data"}]
    ambiguous = [item for item in outcomes if item.get("outcome") == "triggered_and_invalidated"]

    no_data = [item for item in outcomes if item.get("outcome") == "no_data"]
    not_evaluable_only = [item for item in outcomes if item.get("outcome") == "not_evaluable"]
    touched = [
        item
        for item in outcomes
        if item.get("outcome") in {"triggered", "invalidated", "triggered_and_invalidated", "not_triggered"}
    ]

    lines = [
        f"# 日度自我复盘（{date}）",
        "",
        "## 数据基础",
        f"- 盘后报告：{'已生成' if post_market_exists else '缺失'}",
        f"- 计划/观察信号数：{len(signals)}",
        f"- 已回填 outcome 数：{len(outcomes)}",
        f"- 人工交易记录数：{len(trades)}",
        f"- 券商只读成交证据数：{len(broker_executions)}",
        f"- 券商成交覆盖：{json.dumps(broker_sources, ensure_ascii=False, sort_keys=True)}",
        f"- 持仓复核记录数：{len(position_reviews)}",
        "",
        "## 计划质量复盘",
        f"- 信号状态：{json.dumps(dict(by_status), ensure_ascii=False, sort_keys=True)}",
        f"- 可回填信号数：{len(outcomes) - len(no_data) - len(not_evaluable_only)}",
        f"- 缺行情信号数：{len(no_data)}",
        f"- 结构不可评估信号数：{len(not_evaluable_only)}",
        "",
        "## 市场触达复盘",
        f"- outcome 汇总：{json.dumps(outcome_summary['by_outcome'], ensure_ascii=False, sort_keys=True)}",
        f"- 已进行价格触达判断：{len(touched)}",
        "- 价格触达只基于日线 high/low，不代表真实入场、成交或策略收益。",
        "",
        "## 真实执行复盘",
        f"- 交易记录状态：{json.dumps(dict(by_trade), ensure_ascii=False, sort_keys=True)}",
        f"- 人工交易记录数：{len(trades)}",
        f"- formal trade journal：{'暂无 trade record。' if not trades else '仅按 trades.jsonl 中的人工记录评价。'}",
        (
            f"- broker execution evidence：{len(broker_executions)} 笔只读成交；"
            "详细质量评价见 post-market.md 的「当日交易复盘」。"
            if broker_executions
            else "- broker execution evidence：未取得可核验成交；不能据此推断当日无交易。"
        ),
        f"- 持仓交易关联：{json.dumps(dict(by_trade_link), ensure_ascii=False, sort_keys=True)}",
        f"- 持仓需人工复核：{position_review_required}",
        "",
        "## 需要人工复核",
    ]
    if ambiguous:
        lines.append(f"- {len(ambiguous)} 个信号日线同时触及触发和失效，无法判断盘中先后。")
    if not_evaluable:
        symbols = ", ".join(sorted({str(item.get("symbol")) for item in not_evaluable if item.get("symbol")}))
        lines.append(f"- {len(not_evaluable)} 个信号不可评估或缺数据：{symbols or '无 symbol'}。")
    if not ambiguous and not not_evaluable:
        lines.append("- 暂无必须人工复核的 outcome。")

    lines.extend(
        [
            "",
            "## Setup 反馈",
        ]
    )
    if outcome_summary["by_setup"]:
        for setup, counts in outcome_summary["by_setup"].items():
            lines.append(f"- {setup}：{json.dumps(counts, ensure_ascii=False, sort_keys=True)}")
    else:
        lines.append("- 暂无 setup outcome。")

    lines.extend(
        [
            "",
            "## 今日纪律结论",
            "- 不把触发记录等同于真实入场结果；formal 统计只从 trades.jsonl 判断，券商只读成交证据单独披露。",
            "- 对不可评估信号补充结构化 trigger/invalidation 价格，减少后续回填噪音。",
            "- 若 outcome 显示触发后又失效，次日降低同类追突破场景的执行优先级。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_review_record(date: str, markdown_path: Path, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize(outcomes)
    return {
        "kind": "review",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "review_id": review_id(date),
        "date": date,
        "scope": "daily",
        "summary": f"outcomes={summary['by_outcome']}",
        "outcome": summary["by_outcome"],
        "source_report": str(markdown_path),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    signals_file = journal_path(repo_root, args.journal_dir, "signal")
    outcomes_file = journal_path(repo_root, args.journal_dir, "outcome")
    trades_file = journal_path(repo_root, args.journal_dir, "trade")
    reviews_file = journal_path(repo_root, args.journal_dir, "review")

    signals = [
        record
        for record in read_jsonl(signals_file)
        if record.get("kind") == "signal" and planned_target_date(record) == args.date
    ]
    outcomes = [
        record
        for record in read_jsonl(outcomes_file)
        if record.get("kind") == "outcome" and record.get("review_date") == args.date
    ]
    trades = [
        record
        for record in read_jsonl(trades_file)
        if record.get("kind") == "trade" and record.get("date") == args.date
    ]
    position_reviews_file = journal_path(repo_root, args.journal_dir, "position_review")
    position_reviews = [
        record
        for record in read_jsonl(position_reviews_file)
        if record.get("kind") == "position_review" and record.get("date") == args.date
    ]
    broker_trade_path = (
        Path(args.broker_trade_snapshot)
        if getattr(args, "broker_trade_snapshot", None)
        else repo_root / "runtime" / "account" / args.date / "plugin-trade-snapshot.json"
    )
    if not broker_trade_path.is_absolute():
        broker_trade_path = repo_root / broker_trade_path
    broker_payload = {}
    if broker_trade_path.exists():
        try:
            broker_payload = json.loads(broker_trade_path.read_text(encoding="utf-8"))
        except Exception:
            broker_payload = {}
    broker_executions = (
        broker_payload.get("executions")
        if isinstance(broker_payload.get("executions"), list)
        else []
    )
    broker_sources = broker_payload.get("sources") if isinstance(broker_payload.get("sources"), dict) else {}

    post_market = repo_root / "report" / args.date / "post-market.md"
    output = Path(args.output) if args.output else report_path(repo_root, args.date)
    if not output.is_absolute():
        output = repo_root / output
    markdown = build_markdown(
        date=args.date,
        outcomes=outcomes,
        signals=signals,
        trades=trades,
        position_reviews=position_reviews,
        broker_executions=broker_executions,
        broker_sources=broker_sources,
        post_market_exists=post_market.exists(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")

    appended = []
    skipped_duplicates = []
    if args.append:
        rid = review_id(args.date)
        existing = existing_review_ids(reviews_file)
        if rid in existing:
            skipped_duplicates.append(rid)
        else:
            append_jsonl(reviews_file, build_review_record(args.date, output, outcomes))
            appended.append(rid)

    return {
        "status": "success",
        "date": args.date,
        "output": str(output),
        "signals_path": str(signals_file),
        "outcomes_path": str(outcomes_file),
        "trades_path": str(trades_file),
        "position_reviews_path": str(position_reviews_file),
        "reviews_path": str(reviews_file) if args.append else None,
        "summary": summarize(outcomes),
        "signals_count": len(signals),
        "trades_count": len(trades),
        "broker_executions_count": len(broker_executions),
        "broker_trade_snapshot": str(broker_trade_path) if broker_trade_path.exists() else None,
        "position_reviews_count": len(position_reviews),
        "append": args.append,
        "appended": appended,
        "skipped_duplicates": skipped_duplicates,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a daily Trading Copilot self-review")
    parser.add_argument("--date", required=True)
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--journal-dir", default="runtime/journal")
    parser.add_argument("--broker-trade-snapshot")
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
