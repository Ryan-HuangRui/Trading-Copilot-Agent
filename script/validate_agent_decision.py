#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from signal_artifacts import EXECUTION_STATUSES, PLAN_TYPES, validate_conditional_trade_plan


FORBIDDEN_FIELDS = {"order", "orders", "broker_command", "submit_order", "cancel_order", "replace_order"}
REQUIRED_DECISION_FIELDS = {
    "schema_version",
    "decision_id",
    "date",
    "symbol",
    "plan_type",
    "execution_status",
    "decision_label",
    "evidence_ids",
    "risk_summary",
    "limitations",
}


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
        for key, item in value.items():
            if str(key) in FORBIDDEN_FIELDS or contains_forbidden(item):
                return True
    if isinstance(value, list):
        return any(contains_forbidden(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(field in lowered for field in FORBIDDEN_FIELDS)
    return False


def validate_role_report(path: Path, payload: dict[str, Any], role: str) -> list[str]:
    errors: list[str] = []
    if payload.get("role") != role:
        errors.append(f"{path}: role must be {role}")
    if role in {"Bull Researcher", "Bear Researcher"}:
        if not payload.get("supporting_evidence_ids"):
            errors.append(f"{path}: supporting_evidence_ids is required")
        if not payload.get("opposing_evidence_ids"):
            errors.append(f"{path}: opposing_evidence_ids is required")
    if role == "Risk Manager":
        for field in ("invalidation", "liquidity_or_data_limits", "portfolio_constraints", "risk_summary"):
            if not payload.get(field):
                errors.append(f"{path}: {field} is required")
    return errors


def validate_decision(path: Path, payload: dict[str, Any], date: str, symbol: str) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_DECISION_FIELDS - set(payload))
    if missing:
        errors.append(f"{path}: missing required fields: {', '.join(missing)}")
    if payload.get("experimental") or payload.get("not_for_execution"):
        errors.append(f"{path}: placeholder decision is not valid for downstream use")
    if payload.get("date") != date:
        errors.append(f"{path}: date mismatch")
    if payload.get("symbol") != symbol:
        errors.append(f"{path}: symbol mismatch")
    if payload.get("plan_type") not in PLAN_TYPES:
        errors.append(f"{path}: unsupported plan_type: {payload.get('plan_type')}")
    if payload.get("execution_status") not in EXECUTION_STATUSES:
        errors.append(f"{path}: unsupported execution_status: {payload.get('execution_status')}")
    if not isinstance(payload.get("evidence_ids"), list):
        errors.append(f"{path}: evidence_ids must be an array")
    if contains_forbidden(payload):
        errors.append(f"{path}: forbidden broker/order command field or text found")
    if payload.get("plan_type") == "trade_plan" and payload.get("execution_status") == "conditional_executable":
        errors.extend(validate_conditional_trade_plan(payload, str(path)))
    return errors


def validate(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    decision_dir = resolve_path(repo_root, args.decision_dir) if args.decision_dir else repo_root / "report" / args.date / "agents"
    errors: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []
    for symbol in normalize_symbols(args.symbol):
        symbol_dir = decision_dir / symbol
        role_files = [
            ("bull_report.json", "Bull Researcher"),
            ("bear_report.json", "Bear Researcher"),
            ("risk_report.json", "Risk Manager"),
        ]
        for filename, role in role_files:
            path = symbol_dir / filename
            if not path.exists():
                errors.append(f"missing role report: {path}")
                continue
            try:
                payload = read_json(path)
            except Exception as exc:
                errors.append(f"{path}: invalid JSON: {exc}")
                continue
            checked.append(str(path))
            errors.extend(validate_role_report(path, payload, role))
        decision_path = symbol_dir / "decision.json"
        if not decision_path.exists():
            errors.append(f"missing decision: {decision_path}")
            continue
        try:
            decision = read_json(decision_path)
        except Exception as exc:
            errors.append(f"{decision_path}: invalid JSON: {exc}")
            continue
        checked.append(str(decision_path))
        errors.extend(validate_decision(decision_path, decision, args.date, symbol))
    return {
        "status": "fail" if errors else "pass",
        "date": args.date,
        "symbols": normalize_symbols(args.symbol),
        "checked_artifacts": checked,
        "errors": errors,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate agent role reports and decisions")
    parser.add_argument("--date", required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--decision-dir")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    payload = validate(build_parser().parse_args())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
