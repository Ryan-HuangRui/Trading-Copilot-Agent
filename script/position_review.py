#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_append import append_jsonl, journal_path
from journal_review import read_jsonl
from signal_artifacts import normalize_sidecar, read_json, resolve_signals_path


CLOSE_TO_INVALIDATION_PCT = 3.0
HIGH_CONCENTRATION_PCT = 25.0
DEFAULT_CONFIG = {
    "close_to_invalidation_pct": CLOSE_TO_INVALIDATION_PCT,
    "high_concentration_pct": HIGH_CONCENTRATION_PCT,
    "ignore_symbols": [],
    "core_holding_symbols": [],
    "positions": {},
    "require_trade_link": False,
}


def config_path(repo_root: Path, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "config" / "position_review.json"


def load_config(repo_root: Path, explicit_path: str | None) -> dict[str, Any]:
    path = config_path(repo_root, explicit_path)
    config = dict(DEFAULT_CONFIG)
    if not path.exists():
        return config
    raw = read_json(path)
    section = raw.get("position_review", raw)
    if not isinstance(section, dict):
        return config
    for key in ("close_to_invalidation_pct", "high_concentration_pct"):
        value = to_float(section.get(key))
        if value is not None:
            config[key] = value
    for key in ("ignore_symbols", "core_holding_symbols"):
        value = section.get(key)
        if isinstance(value, list):
            config[key] = [str(item).upper() for item in value if str(item).strip()]
    positions = section.get("positions")
    if isinstance(positions, dict):
        config["positions"] = {
            str(symbol).upper(): profile
            for symbol, profile in positions.items()
            if str(symbol).strip() and isinstance(profile, dict)
        }
    if isinstance(section.get("require_trade_link"), bool):
        config["require_trade_link"] = section["require_trade_link"]
    return config


def account_snapshot_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "runtime" / "account" / date / "account-snapshot.json"


def signals_path(repo_root: Path, date: str, session: str, explicit_path: str | None) -> Path:
    return resolve_signals_path(repo_root, date, explicit_path, session)


def snapshot_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / date / "daily-snapshot.json"


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


def normalized_symbol(value: Any) -> str:
    return str(value or "").split(".", 1)[0].upper()


def date_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    return str(record.get("date") or ""), str(record.get("created_at") or "")


def active_signals(repo_root: Path, path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = read_json(path)
    return {
        signal["symbol"]: signal
        for signal in normalize_sidecar(payload, path, repo_root)
        if signal.get("status") != "no_trade" and signal.get("symbol")
    }


def snapshot_latest_prices(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    payload = read_json(path)
    prices = {}
    for item in payload.get("symbols", []):
        if not isinstance(item, dict):
            continue
        symbol = normalized_symbol(item.get("symbol"))
        latest = item.get("latest")
        if not symbol or not isinstance(latest, dict):
            continue
        close = to_float(latest.get("close"))
        if close is not None:
            prices[symbol] = close
    return prices


def enrich_position_with_price(position: dict[str, Any], snapshot_prices: dict[str, float]) -> dict[str, Any]:
    enriched = dict(position)
    symbol = normalized_symbol(position.get("symbol"))
    quantity = to_float(position.get("quantity"))
    avg_cost = to_float(position.get("avg_cost"))
    last_price = to_float(position.get("last_price"))
    price_source = "account_snapshot" if last_price is not None else None
    if last_price is None and symbol in snapshot_prices:
        last_price = snapshot_prices[symbol]
        enriched["last_price"] = last_price
        price_source = "daily_snapshot"

    market_value = to_float(position.get("market_value"))
    if market_value is None and last_price is not None and quantity is not None:
        enriched["market_value"] = round(abs(quantity) * last_price, 3)

    unrealized_pnl = to_float(position.get("unrealized_pnl"))
    if unrealized_pnl is None and last_price is not None and avg_cost is not None and quantity is not None:
        unrealized_pnl = round((last_price - avg_cost) * quantity, 3)
        enriched["unrealized_pnl"] = unrealized_pnl

    if position.get("unrealized_pnl_pct") is None and last_price is not None and avg_cost not in (None, 0):
        enriched["unrealized_pnl_pct"] = round((last_price / avg_cost - 1) * 100, 3)

    if price_source:
        enriched["_price_source"] = price_source
    return enriched


def journal_signals_by_id(repo_root: Path, journal_dir: str) -> dict[str, dict[str, Any]]:
    records = read_jsonl(journal_path(repo_root, journal_dir, "signal"))
    return {
        str(record["signal_id"]): record
        for record in records
        if record.get("kind") == "signal" and isinstance(record.get("signal_id"), str)
    }


def journal_trades_by_symbol(repo_root: Path, journal_dir: str, review_date: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for record in read_jsonl(journal_path(repo_root, journal_dir, "trade")):
        if record.get("kind") != "trade":
            continue
        trade_date = record.get("date")
        if isinstance(trade_date, str) and trade_date > review_date:
            continue
        symbol = normalized_symbol(record.get("symbol"))
        if not symbol:
            continue
        result.setdefault(symbol, []).append(record)
    for records in result.values():
        records.sort(key=date_sort_key, reverse=True)
    return result


def latest_trade(symbol: str, trades_by_symbol: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    records = trades_by_symbol.get(symbol, [])
    return records[0] if records else None


def linked_signal_for_trade(trade: dict[str, Any] | None, signals_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not trade:
        return None
    signal_id = trade.get("source_signal_id")
    if not isinstance(signal_id, str):
        return None
    return signals_by_id.get(signal_id)


def trade_link_state(trade: dict[str, Any] | None) -> str:
    if not trade:
        return "no_trade_record"
    if trade.get("source_signal_id"):
        return "linked_to_source_signal"
    return "trade_missing_source_signal_id"


def estimate_r(last_price: float | None, trade: dict[str, Any] | None) -> float | None:
    if last_price is None or not trade:
        return None
    entry = to_float(trade.get("entry"))
    stop = to_float(trade.get("stop"))
    if entry is None or stop is None or entry == stop:
        return None
    risk = entry - stop
    if risk <= 0:
        return None
    return round((last_price - entry) / risk, 3)


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
    config: dict[str, Any],
    trade: dict[str, Any] | None,
    linked_signal: dict[str, Any] | None,
) -> dict[str, Any]:
    symbol = normalized_symbol(position.get("symbol"))
    last_price = to_float(position.get("last_price"))
    market_value = to_float(position.get("market_value"))
    effective_signal = signal or linked_signal
    invalidation = to_float(effective_signal.get("invalidation_price")) if effective_signal else None
    if invalidation is None and trade:
        invalidation = to_float(trade.get("stop"))
    concentration_pct = None
    if market_value is not None and net_liquidation and net_liquidation > 0:
        concentration_pct = round(market_value / net_liquidation * 100, 3)

    distance_pct = None
    if last_price is not None and invalidation is not None and last_price > 0:
        distance_pct = round((last_price - invalidation) / last_price * 100, 3)

    in_today_signals = signal is not None
    core_holding = symbol in set(config.get("core_holding_symbols", []))
    close_to_invalidation = distance_pct is not None and distance_pct <= float(config["close_to_invalidation_pct"])
    high_concentration = concentration_pct is not None and concentration_pct >= float(config["high_concentration_pct"])
    link_state = trade_link_state(trade)
    trade_link_missing = link_state != "linked_to_source_signal"
    position_profiles = config.get("positions", {})
    profile = position_profiles.get(symbol, {}) if isinstance(position_profiles, dict) else {}
    position_type = str(profile.get("type") or ("core" if core_holding else "trading"))
    review_mode = str(profile.get("review_mode") or ("risk_only" if core_holding else "must_have_plan"))
    core_holding = core_holding or position_type == "core"
    requires_plan = review_mode == "must_have_plan" or position_type == "trading"
    requires_trade_link = bool(config.get("require_trade_link")) and review_mode != "risk_only"
    review_required = (
        ((not in_today_signals) and requires_plan)
        or close_to_invalidation
        or high_concentration
        or (requires_trade_link and trade_link_missing and not core_holding)
    )
    if close_to_invalidation:
        risk_state = "close_to_invalidation"
    elif high_concentration:
        risk_state = "high_concentration"
    elif requires_trade_link and trade_link_missing and not core_holding:
        risk_state = "missing_trade_link"
    elif core_holding and not in_today_signals:
        risk_state = "core_holding_not_in_plan"
    elif not in_today_signals:
        risk_state = "not_in_plan"
    else:
        risk_state = "normal"

    source_signal_id = None
    if signal and signal.get("signal_id"):
        source_signal_id = signal.get("signal_id")
    elif trade and trade.get("source_signal_id"):
        source_signal_id = trade.get("source_signal_id")
    elif linked_signal and linked_signal.get("signal_id"):
        source_signal_id = linked_signal.get("signal_id")

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
        "core_holding": core_holding,
        "position_type": position_type,
        "review_mode": review_mode,
        "profile_notes": profile.get("notes") if isinstance(profile, dict) else None,
        "price_source": position.get("_price_source"),
        "setup": effective_signal.get("setup") if effective_signal else trade.get("planned_setup") if trade else None,
        "source_signal_id": source_signal_id,
        "linked_trade_date": trade.get("date") if trade else None,
        "linked_trade_status": trade.get("status") if trade else None,
        "linked_trade_planned_setup": trade.get("planned_setup") if trade else None,
        "trade_link_state": link_state,
        "trade_link_missing": trade_link_missing,
        "entry": to_float(trade.get("entry")) if trade else None,
        "stop": to_float(trade.get("stop")) if trade else None,
        "estimated_r": estimate_r(last_price, trade),
        "nearest_invalidation": invalidation,
        "distance_to_invalidation_pct": distance_pct,
        "concentration_pct": concentration_pct,
        "risk_state": risk_state,
        "review_required": review_required,
        "source_account_snapshot": str(account_snapshot),
        "source_signals": str(signals_file) if signals_file.exists() else None,
    }
    return {key: value for key, value in payload.items() if value is not None}


def build_markdown(date: str, records: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    review_required = [record for record in records if record.get("review_required")]
    cash_pct = summary.get("cash_pct")
    trade_link_state = summary.get("trade_link_state", {})
    lines = [
        f"# 持仓风险复核（{date}）",
        "",
        "## 总览",
        f"- 持仓数：{len(records)}",
        f"- 需要人工复核：{len(review_required)}",
        f"- 今日计划信号数：{summary.get('planned_signals', 0)}",
        f"- 现金比例：{cash_pct if cash_pct is not None else '未知'}%",
        f"- 交易关联状态：{json.dumps(trade_link_state, ensure_ascii=False, sort_keys=True)}",
        "",
    ]
    if not records:
        lines.extend(
            [
                "## 空仓状态",
                "- 当前无持仓。",
                f"- 今日计划信号数：{summary.get('planned_signals', 0)}",
                f"- 现金比例：{cash_pct if cash_pct is not None else '未知'}%",
                "",
                "## 边界",
                "- 本报告只做持仓和计划一致性复核，不构成交易建议。",
                "- 任何加仓、减仓、卖出、止损都必须由人工确认。",
            ]
        )
        return "\n".join(lines) + "\n"

    lines.append("## 逐持仓")
    for record in records:
        lines.extend(
            [
                f"### {record['symbol']}",
                f"- 是否在今日计划：{'是' if record.get('in_today_signals') else '否'}",
                f"- 风险状态：{record.get('risk_state')}",
                f"- 持仓类型：{record.get('position_type', '未知')} / {record.get('review_mode', '未知')}",
                f"- 交易关联：{record.get('trade_link_state', '未知')}",
                f"- 来源 signal：{record.get('source_signal_id', '无')}",
                f"- 估算 R：{record.get('estimated_r', '无')}",
                f"- 当前价：{record.get('last_price', '未知')}",
                f"- 价格来源：{record.get('price_source', '未知')}",
                f"- 市值：{record.get('market_value', '未知')}",
                f"- 浮盈亏：{record.get('unrealized_pnl', '未知')}（{record.get('unrealized_pnl_pct', '未知')}%）",
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
    signal_path = signals_path(repo_root, args.date, args.session, args.signals)
    daily_snapshot = snapshot_path(repo_root, args.date, args.snapshot)
    account = read_json(account_path)
    signals = active_signals(repo_root, signal_path)
    snapshot_prices = snapshot_latest_prices(daily_snapshot)
    config = load_config(repo_root, getattr(args, "config", None))
    ignored_symbols = set(config.get("ignore_symbols", []))
    signals_by_id = journal_signals_by_id(repo_root, args.journal_dir)
    trades_by_symbol = journal_trades_by_symbol(repo_root, args.journal_dir, args.date)
    net_liquidation = to_float((account.get("account") or {}).get("net_liquidation"))
    cash = to_float((account.get("account") or {}).get("cash"))
    cash_pct = round(cash / net_liquidation * 100, 3) if cash is not None and net_liquidation else None
    records = []
    for position in account.get("positions", []):
        if not isinstance(position, dict) or not position.get("symbol"):
            continue
        symbol = normalized_symbol(position.get("symbol"))
        if symbol in ignored_symbols:
            continue
        trade = latest_trade(symbol, trades_by_symbol)
        enriched_position = enrich_position_with_price(position, snapshot_prices)
        records.append(
            position_review_record(
                date=args.date,
                position=enriched_position,
                signal=signals.get(symbol),
                net_liquidation=net_liquidation,
                account_snapshot=account_path,
                signals_file=signal_path,
                config=config,
                trade=trade,
                linked_signal=linked_signal_for_trade(trade, signals_by_id),
            )
        )

    base = output_base(repo_root, args.date, args.output)
    markdown_path = base.with_suffix(".md")
    json_path = base.with_suffix(".json")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": args.date,
        "source_account_snapshot": str(account_path),
        "source_signals": str(signal_path) if signal_path.exists() else None,
        "source_snapshot": str(daily_snapshot) if daily_snapshot.exists() else None,
        "position_reviews": records,
        "summary": {},
    }
    summary = {
        "positions": len(records),
        "review_required": sum(1 for record in records if record.get("review_required")),
        "in_today_signals": sum(1 for record in records if record.get("in_today_signals")),
        "planned_signals": len(signals),
        "empty_position_state": len(records) == 0,
        "cash_pct": cash_pct,
        "trade_link_state": {
            state: sum(1 for record in records if record.get("trade_link_state") == state)
            for state in sorted({str(record.get("trade_link_state")) for record in records if record.get("trade_link_state")})
        },
        "trade_link_missing": sum(1 for record in records if record.get("trade_link_missing")),
    }
    payload["summary"] = summary
    markdown = build_markdown(args.date, records, summary)
    markdown_path.write_text(markdown, encoding="utf-8")
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
    parser.add_argument("--session", choices=["pre-market", "post-market"], default="pre-market")
    parser.add_argument("--snapshot", help="Daily snapshot for price fallback. Defaults to report/<DATE>/daily-snapshot.json.")
    parser.add_argument("--config", help="Position review config path. Defaults to config/position_review.json.")
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
