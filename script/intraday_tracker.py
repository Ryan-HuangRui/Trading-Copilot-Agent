#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


IMPORTANT_STATES = {"near_trigger", "triggered", "triggered_but_blocked", "invalidated", "missed_window"}


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def repo_path(repo_root: Path, path_text: str | None, default: Path) -> Path:
    if path_text:
        path = Path(path_text)
        return path if path.is_absolute() else repo_root / path
    return repo_root / default


def current_market_date(timezone_name: str) -> str:
    return dt.datetime.now(ZoneInfo(timezone_name)).date().isoformat()


def parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def display_time(value: datetime, timezone_name: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%H:%M %Z")


def normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def unique_append(symbols: list[str], sources: dict[str, list[str]], symbol: str, source: str) -> None:
    clean = normalize_symbol(symbol)
    if not clean:
        return
    if clean not in symbols:
        symbols.append(clean)
    sources.setdefault(clean, [])
    if source not in sources[clean]:
        sources[clean].append(source)


def load_pre_market_signals(path: Path, top_n: int) -> tuple[list[str], dict[str, dict[str, Any]]]:
    payload = read_json(path, {"signals": []})
    raw_signals = payload.get("signals")
    if not isinstance(raw_signals, list):
        raise ValueError(f"{path}: signals must be an array")
    symbols: list[str] = []
    plans: dict[str, dict[str, Any]] = {}
    for raw in raw_signals[:top_n]:
        if not isinstance(raw, dict):
            continue
        symbol = normalize_symbol(raw.get("symbol"))
        if not symbol:
            continue
        symbols.append(symbol)
        plans[symbol] = raw
    return symbols, plans


def load_manual_symbols(path: Path) -> list[str]:
    payload = read_json(path, {"symbols": []})
    raw_symbols = payload.get("symbols") or payload.get("watchlist") or []
    symbols: list[str] = []
    if isinstance(raw_symbols, list):
        for item in raw_symbols:
            if isinstance(item, dict):
                symbols.append(normalize_symbol(item.get("symbol")))
            else:
                symbols.append(normalize_symbol(item))
    return [symbol for symbol in symbols if symbol]


def scans_by_symbol(monitor_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scans: dict[str, dict[str, Any]] = {}
    raw_scans = monitor_payload.get("scans")
    if not isinstance(raw_scans, list):
        return scans
    for scan in raw_scans:
        if not isinstance(scan, dict):
            continue
        symbol = normalize_symbol(scan.get("symbol"))
        if symbol:
            scans[symbol] = scan
    return scans


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def detail_price(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, dict):
        return to_float(value.get("price"))
    return to_float(value)


def signal_price(plan: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(plan, dict):
        return None
    value = plan.get(key)
    if isinstance(value, dict):
        return to_float(value.get("price"))
    return to_float(value)


def evaluate_state(symbol: str, scan: dict[str, Any] | None, plan: dict[str, Any] | None) -> dict[str, Any]:
    if not scan:
        return {
            "symbol": symbol,
            "state": "no_data",
            "monitor_status": None,
            "reason": "monitor scan missing for tracked symbol",
            "bar_timestamp": None,
            "trigger_price": signal_price(plan, "trigger"),
            "invalidation_price": signal_price(plan, "invalidation"),
            "risk_quality": None,
        }

    status = str(scan.get("status") or "")
    risk_quality = str(scan.get("risk_quality") or "")
    last_price = to_float(scan.get("last") or scan.get("last_price") or scan.get("close"))
    invalidation_price = detail_price(scan, "invalidation_detail") or to_float(scan.get("stop")) or signal_price(plan, "invalidation")
    trigger_price = detail_price(scan, "trigger_detail") or to_float(scan.get("trigger")) or signal_price(plan, "trigger")

    if last_price is not None and invalidation_price is not None and last_price <= invalidation_price:
        state = "invalidated"
    elif status == "可执行":
        state = "triggered" if risk_quality in {"acceptable", "watch_only", ""} else "triggered_but_blocked"
    elif status == "临近触发":
        state = "near_trigger"
    elif status == "观察中":
        state = "waiting"
    elif status == "数据不足":
        state = "data_insufficient"
    else:
        state = "waiting"

    return {
        "symbol": symbol,
        "state": state,
        "monitor_status": status or None,
        "reason": scan.get("reason"),
        "bar_timestamp": scan.get("bar_timestamp"),
        "trigger_price": trigger_price,
        "invalidation_price": invalidation_price,
        "risk_quality": risk_quality or None,
    }


def event_id(date: str, symbol: str, state: str, bar_timestamp: Any) -> str:
    digest = hashlib.sha1(f"{date}|{symbol}|{state}|{bar_timestamp or ''}".encode("utf-8")).hexdigest()
    return digest[:16]


def load_existing_event_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("event_id"), str):
            ids.add(payload["event_id"])
    return ids


def build_markdown_section(as_of: datetime, timezone_name: str, symbols: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    lines = [
        f"## {display_time(as_of, timezone_name)}",
        f"- 关注池：{', '.join(item['symbol'] for item in symbols) if symbols else '无'}",
        f"- 重要变化：{len(events)}",
    ]
    for item in symbols:
        detail = item["state"]
        reason = item.get("reason")
        if reason:
            detail = f"{detail} ({reason})"
        lines.append(f"- {item['symbol']}: {detail}")
    lines.append("- 通知：仅重要状态变化写入事件流；本 Markdown 不构成交易指令。")
    return "\n".join(lines) + "\n"


def append_markdown(path: Path, date: str, section: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else f"# {date} Intraday Tracker\n\n"
    if previous and not previous.endswith("\n"):
        previous += "\n"
    path.write_text(previous + section + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    timezone_name = args.timezone
    date = args.date or current_market_date(timezone_name)
    as_of = parse_as_of(getattr(args, "as_of", None))
    pre_market_path = repo_path(repo_root, args.pre_market_signals, Path("report") / date / "pre-market-signals.json")
    manual_path = repo_path(repo_root, args.manual_watchlist, Path("config") / "intraday_watchlist.json")
    monitor_path = repo_path(repo_root, args.monitor, Path("report") / "latest-monitor.json")
    state_path = repo_path(repo_root, args.state, Path("runtime") / "intraday" / date / "state.json")
    events_path = repo_path(repo_root, args.events, Path("runtime") / "intraday" / date / "events.jsonl")
    markdown_path = repo_path(repo_root, args.markdown, Path("report") / date / "intraday.md")

    pre_symbols, plans = load_pre_market_signals(pre_market_path, args.top_n)
    manual_symbols = load_manual_symbols(manual_path)
    monitor_payload = read_json(monitor_path, {"scans": []})
    scans = scans_by_symbol(monitor_payload)
    previous_state = read_json(state_path, {"symbols": {}})
    previous_symbols = previous_state.get("symbols") if isinstance(previous_state.get("symbols"), dict) else {}

    focus_symbols: list[str] = []
    sources: dict[str, list[str]] = {}
    for symbol in pre_symbols:
        unique_append(focus_symbols, sources, symbol, "pre_market_top")
    for symbol in manual_symbols:
        unique_append(focus_symbols, sources, symbol, "manual_watchlist")

    current_symbols: dict[str, dict[str, Any]] = {}
    changed_events: list[dict[str, Any]] = []
    existing_event_ids = load_existing_event_ids(events_path)
    for symbol in focus_symbols:
        evaluated = evaluate_state(symbol, scans.get(symbol), plans.get(symbol))
        evaluated["sources"] = sources.get(symbol, [])
        prior = previous_symbols.get(symbol) if isinstance(previous_symbols.get(symbol), dict) else {}
        evaluated["previous_state"] = prior.get("state")
        current_symbols[symbol] = evaluated
        changed = prior.get("state") != evaluated["state"] or prior.get("bar_timestamp") != evaluated.get("bar_timestamp")
        if changed and evaluated["state"] in IMPORTANT_STATES:
            record = {
                "event_id": event_id(date, symbol, evaluated["state"], evaluated.get("bar_timestamp")),
                "date": date,
                "created_at": as_of.astimezone(timezone.utc).isoformat(timespec="seconds"),
                "symbol": symbol,
                "event_type": "intraday_state_change",
                "state": evaluated["state"],
                "previous_state": prior.get("state"),
                "bar_timestamp": evaluated.get("bar_timestamp"),
                "reason": evaluated.get("reason"),
                "notify": True,
                "source": "intraday-tracker",
            }
            if record["event_id"] not in existing_event_ids:
                changed_events.append(record)
                existing_event_ids.add(record["event_id"])

    state_payload = {
        "date": date,
        "generated_at": as_of.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "workflow": "intraday-tracker",
        "focus_symbols": focus_symbols,
        "sources": sources,
        "inputs": {
            "pre_market_signals": str(pre_market_path),
            "manual_watchlist": str(manual_path),
            "monitor": str(monitor_path),
            "previous_markdown": str(markdown_path),
        },
        "symbols": current_symbols,
        "safety_note": "Read-only intraday state tracker. Events are notification candidates, not broker instructions.",
    }
    write_json(state_path, state_payload)
    for record in changed_events:
        append_jsonl(events_path, record)
    section = build_markdown_section(as_of, timezone_name, list(current_symbols.values()), changed_events)
    append_markdown(markdown_path, date, section)

    return {
        "status": "success",
        "date": date,
        "artifacts": [str(markdown_path), str(state_path), str(events_path)],
        "summary": {
            "tracked": len(current_symbols),
            "events": len(changed_events),
            "notify": len([event for event in changed_events if event.get("notify")]),
        },
        "events": changed_events,
        "safety_note": state_payload["safety_note"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track pre-market plans and manual watchlist through intraday monitor scans")
    parser.add_argument("--date")
    parser.add_argument("--pre-market-signals")
    parser.add_argument("--manual-watchlist", default="config/intraday_watchlist.json")
    parser.add_argument("--monitor", default="report/latest-monitor.json")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--state")
    parser.add_argument("--events")
    parser.add_argument("--markdown")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--as-of")
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
