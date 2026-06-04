#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any


SUPPORTED_ORDER_TYPES = {
    "LO",
    "ELO",
    "MO",
    "AO",
    "ALO",
    "ODD",
    "SLO",
    "LIT",
    "MIT",
    "TSLPAMT",
    "TSLPPCT",
}
PRICE_REQUIRED_ORDER_TYPES = {"LO", "ELO", "ALO", "ODD", "SLO", "LIT"}
TRIGGER_PRICE_REQUIRED_ORDER_TYPES = {"MIT", "LIT"}
TRAILING_AMOUNT_REQUIRED_ORDER_TYPES = {"TSLPAMT"}
TRAILING_PERCENT_REQUIRED_ORDER_TYPES = {"TSLPPCT"}
VALID_TIFS = {"day", "gtc", "gtd"}
VALID_SIDES = {"buy", "sell"}
VALID_OUTSIDE_RTH = {"RTH_ONLY", "ANY_TIME", "OVERNIGHT"}


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalized_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def normalize_order_type(value: Any) -> str:
    return str(value or "LO").strip().upper()


def normalize_tif(value: Any) -> str:
    return str(value or "day").strip().lower()


def normalize_side(value: Any) -> str:
    return str(value or "").strip().lower()


def stable_intent_id(date: str, session: str, source_signal_id: str, symbol: str) -> str:
    normalized_symbol = symbol.upper()
    raw = f"{date}|{session}|{source_signal_id}|{normalized_symbol}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{date}:{session}:{normalized_symbol}:{digest}"


def build_order_intent(*, date: str, session: str, preview: dict[str, Any]) -> dict[str, Any]:
    if preview.get("status") != "ready":
        raise ValueError(f"paper preview is not ready: {preview.get('reasons') or preview.get('status')}")
    source_signal_id = str(preview.get("signal_id") or "")
    symbol = str(preview.get("symbol") or "").upper()
    if not source_signal_id:
        raise ValueError("paper preview missing signal_id")
    if not symbol:
        raise ValueError("paper preview missing symbol")

    intent_id = str(preview.get("intent_id") or stable_intent_id(date, session, source_signal_id, symbol))
    order_type = normalize_order_type(preview.get("order_type"))
    entry_price = as_float(preview.get("entry_price"))
    explicit_limit_price = as_float(preview.get("limit_price"))
    limit_price = explicit_limit_price
    if limit_price is None and order_type in PRICE_REQUIRED_ORDER_TYPES:
        limit_price = entry_price
    trigger_price = as_float(preview.get("trigger_price"))
    reference_price = as_float(preview.get("reference_price"))
    if reference_price is None:
        reference_price = entry_price or limit_price or trigger_price
    trailing_amount = as_float(preview.get("trailing_amount"))
    trailing_percent = as_float(preview.get("trailing_percent"))
    limit_offset = as_float(preview.get("limit_offset"))
    stop_price = as_float(preview.get("stop_price"))
    take_profit = as_float(preview.get("take_profit"))
    estimated_account_risk = as_float(preview.get("estimated_account_risk"))
    estimated_notional = as_float(preview.get("estimated_notional"))
    risk_per_share = as_float(preview.get("risk_per_share"))
    max_account_risk_pct = as_float(preview.get("max_account_risk_pct"))
    quantity = int(as_float(preview.get("quantity")) or 0)
    remark = f"tca:{intent_id}"

    return {
        "kind": "paper_order_intent",
        "intent_id": intent_id,
        "idempotency_key": intent_id,
        "source_signal_id": source_signal_id,
        "date": date,
        "session": session,
        "symbol": symbol,
        "longbridge_symbol": str(preview.get("longbridge_symbol") or "").upper(),
        "setup": preview.get("setup"),
        "side": normalize_side(preview.get("side")),
        "order_type": order_type,
        "quantity": quantity,
        "limit_price": limit_price,
        "reference_price": reference_price,
        "trigger_price": trigger_price,
        "trailing_amount": trailing_amount,
        "trailing_percent": trailing_percent,
        "limit_offset": limit_offset,
        "stop_price": stop_price,
        "take_profit": take_profit,
        "risk_per_share": risk_per_share,
        "max_account_risk_pct": max_account_risk_pct,
        "estimated_account_risk": estimated_account_risk,
        "estimated_notional": estimated_notional,
        "tif": normalize_tif(preview.get("tif")),
        "expire_date": normalized_text(preview.get("expire_date")),
        "outside_rth": normalized_text(preview.get("outside_rth")),
        "remark": remark,
        "client_order_id": None,
        "status": "ready",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def validate_order_shape(intent: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    side = normalize_side(intent.get("side"))
    order_type = normalize_order_type(intent.get("order_type"))
    tif = normalize_tif(intent.get("tif"))
    outside_rth = normalized_text(intent.get("outside_rth"))
    quantity = int(as_float(intent.get("quantity")) or 0)

    if side not in VALID_SIDES:
        errors.append("side must be buy or sell")
    if order_type not in SUPPORTED_ORDER_TYPES:
        errors.append(f"unsupported order_type: {order_type}")
    if quantity <= 0:
        errors.append("quantity must be > 0")
    if not intent.get("longbridge_symbol"):
        errors.append("longbridge_symbol is required")
    if tif not in VALID_TIFS:
        errors.append("tif must be day, gtc, or gtd")
    if tif == "gtd" and not normalized_text(intent.get("expire_date")):
        errors.append("expire_date is required when tif is gtd")
    if outside_rth and outside_rth not in VALID_OUTSIDE_RTH:
        errors.append("outside_rth must be RTH_ONLY, ANY_TIME, or OVERNIGHT")

    if order_type in PRICE_REQUIRED_ORDER_TYPES and float(intent.get("limit_price") or 0) <= 0:
        errors.append(f"limit_price must be > 0 for {order_type}")
    if order_type in TRIGGER_PRICE_REQUIRED_ORDER_TYPES and float(intent.get("trigger_price") or 0) <= 0:
        errors.append(f"trigger_price must be > 0 for {order_type}")
    if order_type in TRAILING_AMOUNT_REQUIRED_ORDER_TYPES and float(intent.get("trailing_amount") or 0) <= 0:
        errors.append(f"trailing_amount must be > 0 for {order_type}")
    if order_type in TRAILING_PERCENT_REQUIRED_ORDER_TYPES and float(intent.get("trailing_percent") or 0) <= 0:
        errors.append(f"trailing_percent must be > 0 for {order_type}")
    if order_type.startswith("TSLP") and intent.get("limit_offset") is not None and float(intent.get("limit_offset") or 0) <= 0:
        errors.append(f"limit_offset must be > 0 for {order_type}")
    return errors


def paper_order_record(
    *,
    intent: dict[str, Any],
    submit_status: str,
    broker_order_id: str | None = None,
    raw_request: dict[str, Any] | None = None,
    raw_response: dict[str, Any] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    return {
        "kind": "paper_order",
        "intent_id": intent["intent_id"],
        "idempotency_key": intent["idempotency_key"],
        "source_signal_id": intent["source_signal_id"],
        "date": intent["date"],
        "session": intent["session"],
        "symbol": intent["symbol"],
        "longbridge_symbol": intent["longbridge_symbol"],
        "side": intent["side"],
        "order_type": intent["order_type"],
        "quantity": intent["quantity"],
        "limit_price": intent["limit_price"],
        "reference_price": intent.get("reference_price"),
        "trigger_price": intent.get("trigger_price"),
        "trailing_amount": intent.get("trailing_amount"),
        "trailing_percent": intent.get("trailing_percent"),
        "limit_offset": intent.get("limit_offset"),
        "stop_price": intent["stop_price"],
        "take_profit": intent["take_profit"],
        "estimated_account_risk": intent["estimated_account_risk"],
        "estimated_notional": intent["estimated_notional"],
        "remark": intent["remark"],
        "client_order_id": intent.get("client_order_id"),
        "broker": "longbridge",
        "account_channel": "lb_papertrading",
        "broker_order_id": broker_order_id,
        "submit_status": submit_status,
        "dry_run": dry_run,
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raw_request": raw_request or {},
        "raw_response": raw_response or {},
    }
