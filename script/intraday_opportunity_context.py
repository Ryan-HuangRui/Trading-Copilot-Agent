#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REVIEWABLE_STATUSES = {"可执行", "临近触发"}


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def source_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def refined_setups(repo_root: Path) -> list[str]:
    setup_dir = repo_root / "knowledge" / "refined" / "setups"
    if not setup_dir.exists():
        return []
    return sorted(path.name for path in setup_dir.glob("*.md"))


def premarket_by_symbol(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    signals = payload.get("signals")
    if not isinstance(signals, list):
        return indexed
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        symbol = str(signal.get("symbol") or "").upper()
        if symbol:
            indexed[symbol] = signal
    return indexed


def intraday_symbol_state(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    symbols = payload.get("symbols")
    if isinstance(symbols, dict):
        value = symbols.get(symbol)
        return value if isinstance(value, dict) else {}
    if isinstance(symbols, list):
        for item in symbols:
            if isinstance(item, dict) and str(item.get("symbol") or "").upper() == symbol:
                return item
    return {}


def paper_orders_by_symbol(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    orders = payload.get("orders")
    if not isinstance(orders, list):
        return indexed
    for order in orders:
        if not isinstance(order, dict):
            continue
        symbol = str(order.get("symbol") or "").upper()
        if symbol:
            indexed.setdefault(symbol, []).append(order)
    return indexed


def sidecar_template_signal(scan: dict[str, Any], *, date: str, source: str) -> dict[str, Any]:
    symbol = str(scan.get("symbol") or "").upper()
    setup = str(scan.get("setup") or "NO VALID SETUP")
    setup_files = scan.get("setup_files") if isinstance(scan.get("setup_files"), list) else ([setup] if setup != "NO VALID SETUP" else [])
    trigger = scan.get("trigger_detail") if isinstance(scan.get("trigger_detail"), dict) else None
    invalidation = scan.get("invalidation_detail") if isinstance(scan.get("invalidation_detail"), dict) else None
    trigger_price = as_float((trigger or {}).get("price")) if trigger else as_float(scan.get("trigger"))
    invalidation_price = as_float((invalidation or {}).get("price")) if invalidation else as_float(scan.get("stop"))
    target1 = as_float(scan.get("target1"))
    if trigger is None:
        trigger = {
            "type": "monitor_observation",
            "price": trigger_price,
            "text": str(scan.get("reason") or "monitor observation"),
        }
    if invalidation is None:
        invalidation = {
            "type": "monitor_invalidation",
            "price": invalidation_price,
            "text": str(scan.get("invalid") or "monitor invalidation"),
        }
    risk_per_share = None
    if trigger_price is not None and invalidation_price is not None:
        risk_per_share = round(trigger_price - invalidation_price, 4)
    signal_id = f"monitor-review:{date}:{symbol}:{str(scan.get('bar_timestamp') or '')}:{trigger_price}"
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "setup": setup,
        "setup_files": setup_files,
        "direction": "long",
        "trigger": trigger,
        "invalidation": invalidation,
        "risk": {
            "max_risk_pct": None,
            "max_account_risk_pct": None,
            "risk_per_share": risk_per_share,
            "text": "Codex 必须依据账户风险和 refined rules 决定是否补全为 conditional_executable",
        },
        "status": "observed",
        "plan_type": "watch_only",
        "execution_status": "watch_only",
        "entry": {
            "trigger_price": trigger_price,
            "order_type": "LO",
            "confirmation": "Codex review required before conditional_executable",
        },
        "stop": {"initial_stop": invalidation_price},
        "take_profit": {"tp1": target1},
        "execution_rules": {
            "skip_conditions": [
                "市场环境转为 risk-off",
                "价格未能站稳触发位",
                "实际成交滑点导致 RR < 2",
            ]
        },
        "notes": f"source={source}; monitor_status={scan.get('status')}; reason={scan.get('reason')}",
    }


def is_deterministic_candidate(scan: dict[str, Any]) -> bool:
    status = str(scan.get("status") or "")
    return status in REVIEWABLE_STATUSES and scan.get("journal_appendable") is not False


def observation_record(
    scan: dict[str, Any],
    *,
    premarket: dict[str, dict[str, Any]],
    intraday_state: dict[str, Any],
    paper_orders: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    symbol = str(scan.get("symbol") or "").upper()
    if not symbol:
        return None
    return {
        "symbol": symbol,
        "monitor_status": scan.get("status"),
        "deterministic_candidate": is_deterministic_candidate(scan),
        "setup": scan.get("setup"),
        "setup_files": scan.get("setup_files") if isinstance(scan.get("setup_files"), list) else [],
        "reason": scan.get("reason"),
        "trigger": scan.get("trigger"),
        "stop": scan.get("stop"),
        "target1": scan.get("target1"),
        "risk_quality": scan.get("risk_quality"),
        "bar_timestamp": scan.get("bar_timestamp"),
        "price_data_interval": scan.get("price_data_interval"),
        "latest_bar": scan.get("latest_bar") if isinstance(scan.get("latest_bar"), dict) else None,
        "recent_bars": scan.get("recent_bars") if isinstance(scan.get("recent_bars"), list) else [],
        "price_evidence": scan.get("price_evidence") if isinstance(scan.get("price_evidence"), dict) else {},
        "trigger_detail": scan.get("trigger_detail") if isinstance(scan.get("trigger_detail"), dict) else None,
        "invalidation_detail": scan.get("invalidation_detail") if isinstance(scan.get("invalidation_detail"), dict) else None,
        "premarket_plan": premarket.get(symbol),
        "intraday_state": intraday_symbol_state(intraday_state, symbol),
        "paper_orders": paper_orders.get(symbol, []),
    }


def build_context(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    monitor_path = resolve_path(repo_root, args.monitor, repo_root / "report" / "latest-monitor.json")
    premarket_path = resolve_path(repo_root, args.pre_market_signals, repo_root / "report" / args.date / "pre-market-signals.json")
    intraday_state_path = resolve_path(repo_root, args.intraday_state, repo_root / "runtime" / "intraday" / args.date / "state.json")
    intraday_markdown_path = resolve_path(repo_root, args.intraday_markdown, repo_root / "report" / args.date / "intraday.md")
    paper_state_path = resolve_path(repo_root, args.paper_state, repo_root / "runtime" / "paper" / args.date / "paper-execution-state.json")
    output = resolve_path(repo_root, args.output, repo_root / "report" / args.date / "intraday-opportunity-context.json")
    signals_output = resolve_path(repo_root, args.signals_output, repo_root / "report" / args.date / "monitor-signals.json")

    monitor_payload = read_json(monitor_path, {"scans": []})
    premarket_payload = read_json(premarket_path, {"signals": []})
    intraday_state_payload = read_json(intraday_state_path, {})
    paper_state_payload = read_json(paper_state_path, {"orders": []})
    premarket_index = premarket_by_symbol(premarket_payload)
    paper_index = paper_orders_by_symbol(paper_state_payload)

    source = source_path(monitor_path, repo_root)
    observations: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    scans = monitor_payload.get("scans") if isinstance(monitor_payload.get("scans"), list) else []
    for scan in scans:
        if not isinstance(scan, dict):
            continue
        record = observation_record(
            scan,
            premarket=premarket_index,
            intraday_state=intraday_state_payload,
            paper_orders=paper_index,
        )
        if record is None:
            continue
        if len(observations) >= args.max_observations:
            continue
        observations.append(record)
        templates.append(sidecar_template_signal(scan, date=args.date, source=source))
        if record["deterministic_candidate"] and len(candidates) < args.max_candidates:
            candidates.append(record)

    payload = {
        "schema_version": "intraday-opportunity-context/v1",
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {
            "monitor": source_path(monitor_path, repo_root),
            "pre_market_signals": source_path(premarket_path, repo_root) if premarket_path.exists() else None,
            "intraday_state": source_path(intraday_state_path, repo_root) if intraday_state_path.exists() else None,
            "intraday_markdown": source_path(intraday_markdown_path, repo_root) if intraday_markdown_path.exists() else None,
            "paper_state": source_path(paper_state_path, repo_root) if paper_state_path.exists() else None,
        },
        "llm_contract": {
            "output_signals": source_path(signals_output, repo_root),
            "decision_owner": "codex_llm",
            "primary_input": "observation_scans",
            "may_raise_to_conditional_executable": True,
            "must_review_all_observation_scans": True,
            "must_validate_with": f"python3 script/trading_copilot.py validate-trade-plan --session monitor --date {args.date}",
            "safety": [
                "真实账户禁止写操作",
                "未满足完整 Trade Plan Card 与 RR>=2 时必须保持 watch_only/no_trade",
                "模拟盘写操作只能走 intraday-paper-entry --execute 加 config gate",
            ],
        },
        "refined_setups": refined_setups(repo_root),
        "summary": {
            "observation_scans": len(observations),
            "deterministic_candidate_scans": len(candidates),
            "sidecar_template_signals": len(templates),
        },
        "observation_scans": observations,
        "candidate_scans": candidates,
        "intraday_markdown_excerpt": read_text(intraday_markdown_path)[-args.markdown_chars :] if intraday_markdown_path.exists() else "",
        "paper_summary": paper_state_payload.get("summary") if isinstance(paper_state_payload.get("summary"), dict) else {},
        "sidecar_template": {
            "date": args.date,
            "session": "monitor",
            "source_report": source_path(monitor_path, repo_root),
            "signals": templates,
            "summary": {
                "total": len(templates),
                "candidate": len(candidates),
                "blocked": 0,
                "skipped": 0,
            },
            "safety_note": "Template only. Codex must review before changing any signal to conditional_executable.",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "success",
        "date": args.date,
        "output": str(output),
        "signals_output": str(signals_output),
        "summary": {
            "observation_scans": len(observations),
            "candidate_scans": len(candidates),
            "template_signals": len(templates),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Codex review context for intraday monitor opportunities")
    parser.add_argument("--date", required=True)
    parser.add_argument("--monitor", default="report/latest-monitor.json")
    parser.add_argument("--pre-market-signals")
    parser.add_argument("--intraday-state")
    parser.add_argument("--intraday-markdown")
    parser.add_argument("--paper-state")
    parser.add_argument("--output")
    parser.add_argument("--signals-output")
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--max-observations", type=int, default=30)
    parser.add_argument("--markdown-chars", type=int, default=6000)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        payload = build_context(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
