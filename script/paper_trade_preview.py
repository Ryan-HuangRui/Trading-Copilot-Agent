#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_order_models import (
    PRICE_REQUIRED_ORDER_TYPES,
    TRAILING_AMOUNT_REQUIRED_ORDER_TYPES,
    TRAILING_PERCENT_REQUIRED_ORDER_TYPES,
    TRIGGER_PRICE_REQUIRED_ORDER_TYPES,
    normalize_order_type,
)
from signal_artifacts import read_json, resolve_signals_path, sidecar_source, stable_signal_id
from validate_trade_plan import validate as validate_trade_plan


def output_path(repo_root: Path, date: str, explicit_output: str | None) -> Path:
    if explicit_output:
        path = Path(explicit_output)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / date / "paper-trade-preview.json"


def default_account_snapshot(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "runtime" / "paper" / date / "paper-account-snapshot.json"


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_decimal(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def longbridge_symbol(symbol: str, default_market: str) -> str:
    clean = symbol.strip().upper()
    if "." in clean:
        return clean
    return f"{clean}.{default_market.upper()}"


def build_order_preview(
    *,
    signal: dict[str, Any],
    account: dict[str, Any],
    default_market: str,
    tif: str,
) -> dict[str, Any]:
    symbol = str(signal.get("symbol") or "").upper()
    direction = str(signal.get("direction") or "long").lower()
    entry = signal.get("entry") if isinstance(signal.get("entry"), dict) else {}
    order_type = normalize_order_type(entry.get("order_type"))
    trigger_price = as_float(entry.get("trigger_price"))
    entry_price = as_float(entry.get("limit_price"))
    if entry_price is None:
        entry_price = as_float(entry.get("price"))
    if entry_price is None and order_type in PRICE_REQUIRED_ORDER_TYPES:
        entry_price = trigger_price
    reference_price = entry_price if entry_price is not None else trigger_price
    trailing_amount = as_float(entry.get("trailing_amount"))
    trailing_percent = as_float(entry.get("trailing_percent"))
    limit_offset = as_float(entry.get("limit_offset"))
    stop_price = as_float((signal.get("stop") or {}).get("initial_stop"))
    tp1 = as_float((signal.get("take_profit") or {}).get("tp1"))
    risk = signal.get("risk") if isinstance(signal.get("risk"), dict) else {}
    risk_per_share = as_float(risk.get("risk_per_share"))
    max_account_risk_pct = as_float(risk.get("max_account_risk_pct") or risk.get("max_risk_pct"))
    net_liquidation = as_float(account.get("net_liquidation"))
    cash = as_float(account.get("cash"))
    side = "buy" if direction == "long" else "sell"

    reasons: list[str] = []
    if not symbol:
        reasons.append("missing symbol")
    if signal.get("execution_status") != "conditional_executable":
        reasons.append("execution_status is not conditional_executable")
    if signal.get("plan_type") != "trade_plan":
        reasons.append("plan_type is not trade_plan")
    if direction != "long":
        reasons.append("only long buy previews are supported in the first paper-trading adapter")
    if reference_price is None or reference_price <= 0:
        reasons.append("entry reference price must be > 0")
    if order_type in PRICE_REQUIRED_ORDER_TYPES and (entry_price is None or entry_price <= 0):
        reasons.append(f"entry limit_price must be > 0 for {order_type}")
    if order_type in TRIGGER_PRICE_REQUIRED_ORDER_TYPES and (trigger_price is None or trigger_price <= 0):
        reasons.append(f"entry trigger_price must be > 0 for {order_type}")
    if order_type in TRAILING_AMOUNT_REQUIRED_ORDER_TYPES and (trailing_amount is None or trailing_amount <= 0):
        reasons.append(f"entry trailing_amount must be > 0 for {order_type}")
    if order_type in TRAILING_PERCENT_REQUIRED_ORDER_TYPES and (trailing_percent is None or trailing_percent <= 0):
        reasons.append(f"entry trailing_percent must be > 0 for {order_type}")
    if stop_price is None:
        reasons.append("stop.initial_stop is required")
    if risk_per_share is None or risk_per_share <= 0:
        reasons.append("risk.risk_per_share must be > 0")
    if max_account_risk_pct is None or max_account_risk_pct <= 0:
        reasons.append("risk.max_account_risk_pct must be > 0")
    if net_liquidation is None or net_liquidation <= 0:
        reasons.append("account.net_liquidation must be > 0")

    quantity = 0
    risk_budget = 0.0
    if not reasons:
        risk_budget = float(net_liquidation) * float(max_account_risk_pct) / 100
        quantity = math.floor(risk_budget / float(risk_per_share))
        if cash is not None and reference_price:
            quantity = min(quantity, math.floor(float(cash) / float(reference_price)))
        if quantity <= 0:
            reasons.append("computed quantity is zero")

    lb_symbol = longbridge_symbol(symbol, default_market) if symbol else ""
    preview = {
        "signal_id": signal.get("signal_id"),
        "symbol": symbol,
        "longbridge_symbol": lb_symbol,
        "setup": signal.get("setup"),
        "side": side,
        "status": "blocked" if reasons else "ready",
        "reasons": reasons,
        "quantity": quantity,
        "entry_price": entry_price if entry_price is not None else reference_price,
        "reference_price": reference_price,
        "trigger_price": trigger_price,
        "trailing_amount": trailing_amount,
        "trailing_percent": trailing_percent,
        "limit_offset": limit_offset,
        "stop_price": stop_price,
        "take_profit": tp1,
        "risk_per_share": risk_per_share,
        "max_account_risk_pct": max_account_risk_pct,
        "estimated_account_risk": round(quantity * float(risk_per_share or 0), 4),
        "estimated_notional": round(quantity * float(reference_price or 0), 4),
        "order_type": order_type,
        "tif": tif,
    }
    if not reasons:
        command = [
            "longbridge",
            "order",
            side,
            lb_symbol,
            str(quantity),
            "--order-type",
            order_type,
        ]
        if order_type in PRICE_REQUIRED_ORDER_TYPES:
            command.extend(["--price", format_decimal(float(entry_price))])
        if order_type in TRIGGER_PRICE_REQUIRED_ORDER_TYPES:
            command.extend(["--trigger-price", format_decimal(float(trigger_price))])
        if order_type in TRAILING_AMOUNT_REQUIRED_ORDER_TYPES:
            command.extend(["--trailing-amount", format_decimal(float(trailing_amount))])
        if order_type in TRAILING_PERCENT_REQUIRED_ORDER_TYPES:
            command.extend(["--trailing-percent", format_decimal(float(trailing_percent))])
        if order_type.startswith("TSLP") and limit_offset is not None:
            command.extend(["--limit-offset", format_decimal(float(limit_offset))])
        command.extend(["--tif", tif, "--format", "json"])
        preview["preview_command"] = command
    return preview


def validation_result(repo_root: Path, date: str, session: str, signals: str | None) -> dict[str, Any]:
    args = argparse.Namespace(date=date, session=session, signals=signals, repo_root=str(repo_root))
    result = validate_trade_plan(args)
    if result["status"] != "pass":
        raise ValueError(f"trade plan validation failed: {result['errors']}")
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    signals_path = resolve_signals_path(repo_root, args.date, args.signals, args.session)
    account_path = default_account_snapshot(repo_root, args.date, args.account_snapshot)
    validation = validation_result(repo_root, args.date, args.session, args.signals) if args.require_validation else None
    signals_payload = read_json(signals_path)
    account_payload = read_json(account_path)
    account = account_payload.get("account")
    if not isinstance(account, dict):
        raise ValueError(f"{account_path}: missing account object")
    raw_signals = signals_payload.get("signals")
    if not isinstance(raw_signals, list):
        raise ValueError(f"{signals_path}: signals must be an array")
    source = sidecar_source(signals_path, repo_root)
    orders = []
    for signal in raw_signals:
        if not isinstance(signal, dict):
            continue
        if not signal.get("signal_id"):
            signal = {
                **signal,
                "signal_id": stable_signal_id(args.date, args.session, str(signal.get("symbol") or ""), source),
            }
        orders.append(
            build_order_preview(
                signal=signal,
                account=account,
                default_market=args.default_market,
                tif=args.tif,
            )
        )
    summary = {
        "total": len(orders),
        "ready": len([order for order in orders if order["status"] == "ready"]),
        "blocked": len([order for order in orders if order["status"] == "blocked"]),
    }
    payload = {
        "date": args.date,
        "session": args.session,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": True,
        "source_signals": str(signals_path),
        "source_account_snapshot": str(account_path),
        "orders": orders,
        "summary": summary,
        "validation": validation,
        "safety_note": "Preview only. This workflow does not submit orders.",
    }
    output = output_path(repo_root, args.date, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "summary": summary}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build paper-trading order previews from validated trade plans")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market", "monitor"], required=True)
    parser.add_argument("--signals")
    parser.add_argument("--account-snapshot")
    parser.add_argument("--output")
    parser.add_argument("--default-market", default="US")
    parser.add_argument("--tif", default="day")
    parser.add_argument("--require-validation", action="store_true")
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
