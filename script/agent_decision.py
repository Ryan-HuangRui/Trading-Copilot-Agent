#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_TYPES = ("market", "technicals", "fundamentals", "news", "sentiment")


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected top-level object")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(repo_root: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root / candidate


def normalize_symbols(symbols: list[str] | None) -> list[str]:
    seen = set()
    result: list[str] = []
    for symbol in symbols or []:
        value = str(symbol or "").strip().upper()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def load_reports(reports_dir: Path, symbol: str) -> dict[str, dict[str, Any]]:
    reports = {}
    for report_type in REPORT_TYPES:
        reports[report_type] = read_json(reports_dir / symbol / f"{report_type}_report.json")
    return reports


def evidence_ids(reports: dict[str, dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for report in reports.values():
        for item in report.get("evidence", []):
            if isinstance(item, dict) and item.get("evidence_id"):
                ids.append(str(item["evidence_id"]))
    return ids


def first_ids(ids: list[str]) -> tuple[list[str], list[str]]:
    supporting = ids[::2] or ids[:1]
    opposing = ids[1::2] or ids[-1:]
    return supporting, opposing


def role_report(role: str, symbol: str, date: str, supporting: list[str], opposing: list[str], thesis: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "role": role,
        "symbol": symbol,
        "date": date,
        "generated_at": now_utc(),
        "supporting_evidence_ids": supporting,
        "opposing_evidence_ids": opposing,
        "thesis": thesis,
        "limitations": ["Deterministic Phase 3 role summary; review before using in reports."],
    }


def risk_report(symbol: str, date: str, ids: list[str], memory_path: str | None) -> dict[str, Any]:
    limits = ["Agent research cannot by itself raise execution status without a complete Trade Plan Card."]
    if memory_path:
        limits.append(f"Memory context reviewed from {memory_path}; memory can only lower confidence or trigger review.")
    return {
        "schema_version": 1,
        "role": "Risk Manager",
        "symbol": symbol,
        "date": date,
        "generated_at": now_utc(),
        "evidence_ids": ids,
        "invalidation": "No trade if data is stale, evidence is incomplete, or refined setup rules are not satisfied.",
        "liquidity_or_data_limits": limits,
        "portfolio_constraints": ["Respect existing max account risk and paper execution gates."],
        "risk_summary": {
            "status": "review_required",
            "can_raise_execution_status": False,
        },
    }


def metric_float(report: dict[str, Any], *names: str) -> float | None:
    sources = [report.get("derived_metrics"), report.get("scores"), report.get("facts")]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for name in names:
            value = source.get(name)
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def structured_scores(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    technicals = reports.get("technicals", {})
    market = reports.get("market", {})
    rsi = metric_float(technicals, "rsi_14", "rsi14")
    close_change = metric_float(market, "close_delta_pct", "change_pct")
    trend_score = metric_float(technicals, "trend_score", "trend")
    momentum_score = max(0.0, min(1.0, (rsi or 50.0) / 100.0))
    if close_change is not None:
        momentum_score = max(0.0, min(1.0, momentum_score + min(max(close_change, -10.0), 10.0) / 40.0))
    if trend_score is not None:
        momentum_score = round((momentum_score + max(0.0, min(1.0, trend_score))) / 2.0, 3)
    else:
        momentum_score = round(momentum_score, 3)
    if rsi is None:
        risk_heat = "unknown"
    elif rsi >= 75:
        risk_heat = "hot"
    elif rsi >= 65:
        risk_heat = "elevated"
    elif rsi <= 35:
        risk_heat = "weak"
    else:
        risk_heat = "normal"
    setup_match = "review_required"
    if momentum_score >= 0.7:
        setup_match = "trend_or_breakout_watch"
    elif momentum_score <= 0.35:
        setup_match = "no_clear_long_setup"
    rank_score = round(momentum_score - (0.15 if risk_heat == "hot" else 0.0), 3)
    return {
        "rank_score": rank_score,
        "momentum_score": momentum_score,
        "risk_heat": risk_heat,
        "setup_match": setup_match,
        "why_focus": [
            "Structured evidence is sufficient for observation ranking.",
            "Market and technical evidence should be reviewed against refined setup rules.",
        ],
        "why_not_executable": [
            "Deterministic role synthesis cannot create a complete Trade Plan Card.",
            "Execution upgrade requires trigger, invalidation, risk, TP1, and intraday confirmation.",
        ],
        "required_intraday_confirmation": [
            "Clean trigger-follow-through or breakout-pullback-confirmation on lower timeframe.",
            "Risk per share must be compressible to the configured account-risk limit.",
        ],
    }


def decision_payload(symbol: str, date: str, ids: list[str], risk: dict[str, Any], reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    scores = structured_scores(reports)
    return {
        "schema_version": 1,
        "decision_id": f"{date}:agent-decision:{symbol}",
        "date": date,
        "symbol": symbol,
        "generated_at": now_utc(),
        "plan_type": "watch_only",
        "execution_status": "watch_only",
        "decision_label": "watch_only",
        "evidence_ids": ids,
        "rank_score": scores["rank_score"],
        "momentum_score": scores["momentum_score"],
        "risk_heat": scores["risk_heat"],
        "setup_match": scores["setup_match"],
        "why_focus": scores["why_focus"],
        "why_not_executable": scores["why_not_executable"],
        "required_intraday_confirmation": scores["required_intraday_confirmation"],
        "risk_summary": risk.get("risk_summary", {}),
        "limitations": [
            "Phase 3 deterministic role synthesis does not create executable trade plans.",
            "Report-generation LLM remains responsible for final session sidecar status when it can write a complete Trade Plan Card.",
            "Use existing validate-report and validate-trade-plan gates before downstream workflow use.",
        ],
    }


def render_decision_md(path: Path, decision: dict[str, Any]) -> None:
    lines = [
        f"# Agent Decision: {decision['symbol']}",
        "",
        f"- date: {decision['date']}",
        f"- plan_type: {decision['plan_type']}",
        f"- execution_status: {decision['execution_status']}",
        f"- evidence_ids: {', '.join(decision.get('evidence_ids', []))}",
        "",
        "This artifact is evidence for human review and does not contain broker commands.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    reports_dir = resolve_path(repo_root, args.reports_dir) if args.reports_dir else repo_root / "report" / args.date / "agents"
    output_dir = resolve_path(repo_root, args.output_dir) if args.output_dir else reports_dir
    artifacts: list[str] = []
    for symbol in normalize_symbols(args.symbol):
        reports = load_reports(reports_dir, symbol)
        ids = evidence_ids(reports)
        supporting, opposing = first_ids(ids)
        bull = role_report("Bull Researcher", symbol, args.date, supporting, opposing, "Upside scenario requires current evidence and valid refined setup rules.")
        bear = role_report("Bear Researcher", symbol, args.date, opposing, supporting, "Downside scenario focuses on failed triggers, stale data, and risk limits.")
        risk = risk_report(symbol, args.date, ids, args.memory)
        decision = decision_payload(symbol, args.date, ids, risk, reports)
        symbol_dir = output_dir / symbol
        for filename, payload in [
            ("bull_report.json", bull),
            ("bear_report.json", bear),
            ("risk_report.json", risk),
            ("decision.json", decision),
        ]:
            path = symbol_dir / filename
            write_json(path, payload)
            artifacts.append(str(path))
        md_path = symbol_dir / "decision.md"
        render_decision_md(md_path, decision)
        artifacts.append(str(md_path))
    return {"status": "success", "date": args.date, "artifacts": artifacts}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate deterministic role reports and agent decisions")
    parser.add_argument("--date", required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--reports-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--memory")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    try:
        payload = run(build_parser().parse_args())
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
