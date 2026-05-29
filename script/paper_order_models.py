#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    limit_price = as_float(preview.get("entry_price"))
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
        "side": str(preview.get("side") or "").lower(),
        "order_type": str(preview.get("order_type") or "").upper(),
        "quantity": quantity,
        "limit_price": limit_price,
        "stop_price": stop_price,
        "take_profit": take_profit,
        "risk_per_share": risk_per_share,
        "max_account_risk_pct": max_account_risk_pct,
        "estimated_account_risk": estimated_account_risk,
        "estimated_notional": estimated_notional,
        "tif": preview.get("tif"),
        "remark": remark,
        "client_order_id": None,
        "status": "ready",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


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
