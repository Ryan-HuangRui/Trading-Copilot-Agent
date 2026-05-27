#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_append import append_jsonl, journal_path
from journal_review import read_jsonl
from signal_artifacts import normalize_sidecar, read_json


CLOSE_TO_INVALIDATION_PCT = 3.0
HIGH_CONCENTRATION_PCT = 25.0


def account_snapshot_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "runtime" / "account" / date / "account-snapshot.json"


def signals_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / date / "signals.json"


def output_base(repo_root: Path, date: str, explicit_output: str | None) -> Path:
    if explicit_output:
        path = Path(explicit_output)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / date / "position-review"


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def active_signals(repo_root: Path, path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = read_json(path)
    return {
        signal["symbol"]: signal
        for signal in normalize_sidecar(payload, path, repo_root)
        if signal.get("status") != "no_trade" and signal.get("symbol")
    }


def review_id(date: str, symbol: str) -> str:
    return f"position:{date}:{symbol}"


def existing_position_review_ids(path: Path) -> set[str]:
    ids = set()
    for record in read_jsonl(path):
        value = record.get("position_review_id")
        if isinstance(value, str):
            ids.add(value)
    return ids


def position_review_record(
    *,
    date: str,
    position: dict[str, Any],
    signal: dict[str, Any] | None,
    net_liquidation: float | None,
    account_snapshot: Path,
    signals_file: Path,
) -> dict[str, Any]:
    symbol = str(position.get("symbol") or "").upper()
    last_price = to_float(position.get("last_price"))
    market_value = to_float(position.get("market_value"))
    invalidation = to_float(signal.get("invalidation_price")) if signal else None
    concentration_pct = None
    if market_value is not None and net_liquidation and net_liquidation > 0:
        concentration_pct = round(market_value / net_liquidation * 100, 3)

    distance_pct = None
    if last_price is not None and invalidation is not None and last_price > 0:
        distance_pct = round((last_price - invalidation) / last_price * 100, 3)

    in_today_signals = signal is not None
    close_to_invalidation = distance_pct is not None and distance_pct <= CLOSE_TO_INVALIDATION_PCT
    high_concentration = concentration_pct is not None and concentration_pct >= HIGH_CONCENTRATION_PCT
    review_required = (not in_today_signals) or close_to_invalidation or high_concentration
    if close_to_invalidation:
        risk_state = "close_to_invalidation"
    elif high_concentration:
        risk_state = "high_concentration"
    elif not in_today_signals:
        risk_state = "not_in_plan"
    else:
        risk_state = "normal"

    payload = {
        "kind": "position_review",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "position_review_id": review_id(date, symbol),
        "date": date,
        "symbol": symbol,
        "market": position.get("market"),
        "quantity": position.get("quantity"),
        "market_value": market_value,
        "last_price": last_price,
        "avg_cost": position.get("avg_cost"),
        "unrealized_pnl": position.get("unrealized_pnl"),
        "unrealized_pnl_pct": position.get("unrealized_pnl_pct"),
        "in_today_signals": in_today_signals,
        "setup": signal.get("setup") if signal else None,
        "nearest_invalidation": invalidation,
        "distance_to_invalidation_pct": distance_pct,
        "concentration_pct": concentration_pct,
        "risk_state": risk_state,
        "review_required": review_required,
        "source_account_snapshot": str(account_snapshot),
        "source_signals": str(signals_file) if signals_file.exists() else None,
    }
    return {key: value for key, value in payload.items() if value is not None}


def build_markdown(date: str, records: list[dict[str, Any]]) -> str:
    review_required = [record for record in records if record.get("review_required")]
    lines = [
        f"# 持仓风险复核（{date}）",
        "",
        "## 总览",
        f"- 持仓数：{len(records)}",
        f"- 需要人工复核：{len(review_required)}",
        "",
        "## 逐持仓",
    ]
    for record in records:
        lines.extend(
            [
                f"### {record['symbol']}",
                f"- 是否在今日计划：{'是' if record.get('in_today_signals') else '否'}",
                f"- 风险状态：{record.get('risk_state')}",
                f"- 当前价：{record.get('last_price', '未知')}",
                f"- 失效位：{record.get('nearest_invalidation', '无')}",
                f"- 距离失效位：{record.get('distance_to_invalidation_pct', '无')}%",
                f"- 持仓集中度：{record.get('concentration_pct', '无')}%",
                f"- 人工复核：{'需要' if record.get('review_required') else '暂不需要'}",
                "",
            ]
        )
    lines.extend(
        [
            "## 边界",
            "- 本报告只做持仓和计划一致性复核，不构成交易建议。",
            "- 任何加仓、减仓、卖出、止损都必须由人工确认。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    account_path = account_snapshot_path(repo_root, args.date, args.account_snapshot)
    if not account_path.exists():
        raise FileNotFoundError(f"missing account snapshot: {account_path}")
    signal_path = signals_path(repo_root, args.date, args.signals)
    account = read_json(account_path)
    signals = active_signals(repo_root, signal_path)
    net_liquidation = to_float((account.get("account") or {}).get("net_liquidation"))
    records = [
        position_review_record(
            date=args.date,
            position=position,
            signal=signals.get(str(position.get("symbol") or "").upper()),
            net_liquidation=net_liquidation,
            account_snapshot=account_path,
            signals_file=signal_path,
        )
        for position in account.get("positions", [])
        if isinstance(position, dict) and position.get("symbol")
    ]

    base = output_base(repo_root, args.date, args.output)
    markdown_path = base.with_suffix(".md")
    json_path = base.with_suffix(".json")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": args.date,
        "source_account_snapshot": str(account_path),
        "source_signals": str(signal_path) if signal_path.exists() else None,
        "position_reviews": records,
        "summary": {
            "positions": len(records),
            "review_required": sum(1 for record in records if record.get("review_required")),
            "in_today_signals": sum(1 for record in records if record.get("in_today_signals")),
        },
    }
    markdown_path.write_text(build_markdown(args.date, records), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    appended = []
    skipped_duplicates = []
    journal_file = None
    if args.append:
        journal_file = journal_path(repo_root, args.journal_dir, "position_review")
        existing = existing_position_review_ids(journal_file)
        for record in records:
            rid = str(record.get("position_review_id") or "")
            if rid in existing:
                skipped_duplicates.append(rid)
                continue
            append_jsonl(journal_file, record)
            existing.add(rid)
            appended.append(rid)

    return {
        "status": "success",
        "date": args.date,
        "artifacts": [str(markdown_path), str(json_path)],
        "summary": payload["summary"],
        "journal_path": str(journal_file) if journal_file else None,
        "append": args.append,
        "appended": appended,
        "skipped_duplicates": skipped_duplicates,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review read-only account positions against structured signals")
    parser.add_argument("--date", required=True)
    parser.add_argument("--account-snapshot")
    parser.add_argument("--signals")
    parser.add_argument("--output", help="Output base path without extension")
    parser.add_argument("--append", action="store_true")
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
