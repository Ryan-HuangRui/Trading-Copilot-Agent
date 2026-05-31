#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPORT_TYPES = ("market", "technicals", "fundamentals", "news", "sentiment")
REQUIRED_REPORT_FIELDS = {
    "schema_version",
    "report_type",
    "date",
    "symbol",
    "evidence",
    "facts",
    "derived_metrics",
    "scores",
    "limitations",
}
REQUIRED_EVIDENCE_FIELDS = {
    "evidence_id",
    "source",
    "source_type",
    "symbol",
    "summary",
    "confidence",
    "limitations",
}
FORBIDDEN_TEXT = (
    "submit_order",
    "broker_command",
    "cancel_order",
    "replace_order",
)


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected top-level object")
    return data


def resolve_path(repo_root: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root / candidate


def normalize_symbols(symbols: list[str] | None) -> list[str]:
    seen = set()
    result = []
    for symbol in symbols or []:
        value = str(symbol or "").strip().upper()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        return any(contains_forbidden(key) or contains_forbidden(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_forbidden(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(token in lowered for token in FORBIDDEN_TEXT)
    return False


def validate_evidence(path: Path, report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    evidence = report.get("evidence")
    if not isinstance(evidence, list):
        return [f"{path}: evidence must be an array"]
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"{path}: evidence[{index}] must be an object")
            continue
        missing = sorted(REQUIRED_EVIDENCE_FIELDS - set(item))
        if not (item.get("as_of") or item.get("published_at")):
            missing.append("as_of_or_published_at")
        if missing:
            errors.append(f"{path}: evidence[{index}] missing required fields: {', '.join(missing)}")
        if not isinstance(item.get("limitations"), list):
            errors.append(f"{path}: evidence[{index}].limitations must be an array")
        try:
            confidence = float(item.get("confidence"))
            if confidence < 0 or confidence > 1:
                errors.append(f"{path}: evidence[{index}].confidence must be between 0 and 1")
        except (TypeError, ValueError):
            errors.append(f"{path}: evidence[{index}].confidence must be numeric")
    return errors


def validate_report(path: Path, payload: dict[str, Any], date: str, symbol: str, report_type: str) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_REPORT_FIELDS - set(payload))
    if missing:
        errors.append(f"{path}: missing required fields: {', '.join(missing)}")
    if payload.get("date") != date:
        errors.append(f"{path}: date mismatch")
    if payload.get("symbol") != symbol:
        errors.append(f"{path}: symbol mismatch")
    if payload.get("report_type") != report_type:
        errors.append(f"{path}: report_type mismatch")
    errors.extend(validate_evidence(path, payload))
    if contains_forbidden(payload):
        errors.append(f"{path}: forbidden broker/order command text found")
    return errors


def validate(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    reports_dir = resolve_path(repo_root, args.reports_dir) if args.reports_dir else repo_root / "report" / args.date / "agents"
    symbols = normalize_symbols(args.symbol)
    errors: list[str] = []
    warnings: list[str] = []
    checked = 0
    for symbol in symbols:
        for report_type in REPORT_TYPES:
            path = reports_dir / symbol / f"{report_type}_report.json"
            if not path.exists():
                errors.append(f"missing report: {path}")
                continue
            try:
                payload = read_json(path)
            except Exception as exc:
                errors.append(f"{path}: invalid JSON: {exc}")
                continue
            checked += 1
            errors.extend(validate_report(path, payload, args.date, symbol, report_type))
            if not payload.get("evidence"):
                warnings.append(f"{path}: evidence is empty")
    return {
        "status": "fail" if errors else "pass",
        "date": args.date,
        "symbols": symbols,
        "checked_reports": checked,
        "errors": errors,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate structured agent research reports")
    parser.add_argument("--date", required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--reports-dir")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    payload = validate(build_parser().parse_args())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
