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
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def evidence_for_symbol(payload: dict[str, Any], symbol: str, source_types: set[str]) -> list[dict[str, Any]]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        return []
    result = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        if str(item.get("symbol") or "").upper() != symbol:
            continue
        if str(item.get("source_type") or "") not in source_types:
            continue
        result.append(item)
    return result


def provider_fixture_evidence(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return {"fundamentals": [], "news": [], "sentiment": []}
    payload = read_json(path)
    result = {"fundamentals": [], "news": [], "sentiment": []}
    for item in payload.get("evidence", []):
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("source_type") or "")
        if source_type in result:
            result[source_type].append(item)
    return result


def build_report(
    *,
    report_type: str,
    date: str,
    symbol: str,
    evidence: list[dict[str, Any]],
    facts: list[Any],
    derived_metrics: dict[str, Any],
    scores: dict[str, Any],
    limitations: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "report_type": report_type,
        "date": date,
        "symbol": symbol,
        "generated_at": now_utc(),
        "evidence": evidence,
        "facts": facts,
        "derived_metrics": derived_metrics,
        "scores": scores,
        "limitations": limitations,
    }


def render_markdown(symbol_dir: Path, symbol: str, reports: list[dict[str, Any]]) -> Path:
    path = symbol_dir / "research_report.md"
    lines = [f"# Agent Research Report: {symbol}", ""]
    for report in reports:
        lines.extend([f"## {report['report_type']}", ""])
        if report["evidence"]:
            for item in report["evidence"]:
                lines.append(f"- {item.get('evidence_id')}: {item.get('summary')}")
        else:
            lines.append("- No fixture evidence available.")
        if report["limitations"]:
            lines.append("")
            lines.append("Limitations:")
            for limitation in report["limitations"]:
                lines.append(f"- {limitation}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    out_dir = resolve_path(repo_root, args.output_dir) if args.output_dir else repo_root / "report" / args.date / "agents"
    market_path = resolve_path(repo_root, args.market_data) if args.market_data else repo_root / "report" / args.date / "agents" / "market-data.json"
    technicals_path = resolve_path(repo_root, args.technicals) if args.technicals else repo_root / "report" / args.date / "agents" / "technicals.json"
    fixture_path = resolve_path(repo_root, args.provider_fixture) if args.provider_fixture else repo_root / "tests" / "fixtures" / "agent_research" / "provider_contract_fixture.json"

    market_payload = read_json(market_path)
    technicals_payload = read_json(technicals_path)
    fixture = provider_fixture_evidence(fixture_path)
    symbols = normalize_symbols(args.symbol)
    artifacts: list[str] = []
    for symbol in symbols:
        symbol_dir = out_dir / symbol
        market_item = (market_payload.get("market_data") or {}).get(symbol, {})
        technical_item = (technicals_payload.get("technicals") or {}).get(symbol, {})
        reports = [
            build_report(
                report_type="market",
                date=args.date,
                symbol=symbol,
                evidence=evidence_for_symbol(market_payload, symbol, {"market_data"}),
                facts=[market_item.get("latest")] if market_item else [],
                derived_metrics=market_item.get("metrics") if isinstance(market_item, dict) else {},
                scores={},
                limitations=market_payload.get("limitations", []),
            ),
            build_report(
                report_type="technicals",
                date=args.date,
                symbol=symbol,
                evidence=evidence_for_symbol(technicals_payload, symbol, {"technical_indicator"}),
                facts=[],
                derived_metrics=technical_item.get("metrics") if isinstance(technical_item, dict) else {},
                scores={},
                limitations=technicals_payload.get("limitations", []),
            ),
        ]
        for report_type in ("fundamentals", "news", "sentiment"):
            report_evidence = [
                item for item in fixture[report_type] if str(item.get("symbol") or "").upper() == symbol
            ]
            reports.append(
                build_report(
                    report_type=report_type,
                    date=args.date,
                    symbol=symbol,
                    evidence=report_evidence,
                    facts=[],
                    derived_metrics={},
                    scores={},
                    limitations=[] if report_evidence else [f"{report_type} provider is fixture-only or unavailable"],
                )
            )
        for report in reports:
            path = symbol_dir / f"{report['report_type']}_report.json"
            write_json(path, report)
            artifacts.append(str(path))
        if args.markdown:
            artifacts.append(str(render_markdown(symbol_dir, symbol, reports)))
    return {
        "status": "success",
        "date": args.date,
        "artifacts": artifacts,
        "summary": {
            "symbols": len(symbols),
            "json_reports": len([path for path in artifacts if path.endswith(".json")]),
            "markdown_reports": len([path for path in artifacts if path.endswith(".md")]),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate structured agent research reports")
    parser.add_argument("--date", required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--market-data")
    parser.add_argument("--technicals")
    parser.add_argument("--provider-fixture")
    parser.add_argument("--output-dir")
    parser.add_argument("--markdown", action="store_true")
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
