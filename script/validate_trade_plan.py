#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from paper_order_models import validate_order_shape
from signal_artifacts import read_json, resolve_signals_path, validate_sidecar_payload
from validate_report import refined_setup_files


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def entry_order_intent(signal: dict[str, Any]) -> dict[str, Any]:
    entry = signal.get("entry") if isinstance(signal.get("entry"), dict) else {}
    symbol = str(signal.get("symbol") or "").strip().upper()
    direction = str(signal.get("direction") or "long").strip().lower()
    trigger = signal.get("trigger") if isinstance(signal.get("trigger"), dict) else {}

    limit_price = as_float(entry.get("limit_price"))
    if limit_price is None:
        limit_price = as_float(entry.get("price"))
    if limit_price is None:
        limit_price = as_float(entry.get("trigger_price"))

    trigger_price = as_float(entry.get("trigger_price"))
    if trigger_price is None:
        trigger_price = as_float(trigger.get("price"))

    return {
        "side": "sell" if direction == "short" else "buy",
        "longbridge_symbol": symbol,
        "quantity": 1,
        "order_type": entry.get("order_type") or "LO",
        "limit_price": limit_price,
        "trigger_price": trigger_price,
        "trailing_amount": as_float(entry.get("trailing_amount")),
        "trailing_percent": as_float(entry.get("trailing_percent")),
        "limit_offset": as_float(entry.get("limit_offset")),
        "tif": entry.get("tif") or "day",
        "expire_date": entry.get("expire_date"),
        "outside_rth": entry.get("outside_rth"),
    }


def validate_conditional_entry_order_shapes(payload: dict[str, Any], path: Path) -> list[str]:
    signals = payload.get("signals")
    if not isinstance(signals, list):
        return []
    errors: list[str] = []
    for idx, signal in enumerate(signals):
        if not isinstance(signal, dict):
            continue
        if signal.get("plan_type") != "trade_plan" or signal.get("execution_status") != "conditional_executable":
            continue
        item = f"{path}: signals[{idx}]"
        for error in validate_order_shape(entry_order_intent(signal)):
            errors.append(f"{item}: entry order shape invalid: {error}")
    return errors


def validate(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    sidecar = resolve_signals_path(repo_root, args.date, args.signals, args.session)
    setup_files = refined_setup_files(repo_root)
    errors: list[str] = []
    warnings: list[str] = []

    if not setup_files:
        errors.append("missing refined setup directory or setup markdown files")
    if not sidecar.exists():
        errors.append(f"missing structured signal sidecar: {sidecar}")
        return {
            "status": "fail",
            "date": args.date,
            "session": args.session,
            "checked_artifacts": [],
            "checked_signals": None,
            "errors": errors,
            "warnings": warnings,
        }

    try:
        payload = read_json(sidecar)
    except Exception as exc:
        errors.append(f"{sidecar}: invalid JSON: {exc}")
        payload = None

    if payload is not None:
        sidecar_errors, sidecar_warnings = validate_sidecar_payload(
            payload=payload,
            path=sidecar,
            expected_date=args.date,
            expected_session=args.session,
            setup_files=setup_files,
        )
        errors.extend(sidecar_errors)
        warnings.extend(sidecar_warnings)
        errors.extend(validate_conditional_entry_order_shapes(payload, sidecar))

    return {
        "status": "fail" if errors else "pass",
        "date": args.date,
        "session": args.session,
        "checked_artifacts": [str(sidecar)],
        "checked_signals": str(sidecar),
        "errors": errors,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate structured Trading Copilot trade-plan sidecars")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market", "monitor"], required=True)
    parser.add_argument("--signals", help="Structured signal sidecar path")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = validate(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
