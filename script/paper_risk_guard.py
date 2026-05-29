#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PAPER_ACCOUNT_CHANNEL = "lb_papertrading"


@dataclass(frozen=True)
class RiskGuardConfig:
    max_daily_risk_pct: float = 3.0
    max_daily_orders: int = 3


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_submitted_intent_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    intent_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        intent_id = record.get("intent_id")
        if intent_id:
            intent_ids.add(str(intent_id))
    return intent_ids


def evaluate_order_intent(
    order_intent: dict[str, Any],
    *,
    account_snapshot: dict[str, Any],
    submitted_intent_ids: set[str],
    config: RiskGuardConfig,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    account = account_snapshot.get("account") if isinstance(account_snapshot.get("account"), dict) else {}
    net_liquidation = as_float(account.get("net_liquidation"))
    cash = as_float(account.get("cash"))
    estimated_risk = as_float(order_intent.get("estimated_account_risk")) or 0.0
    estimated_notional = as_float(order_intent.get("estimated_notional")) or 0.0
    max_account_risk_pct = as_float(order_intent.get("max_account_risk_pct")) or 0.0

    if account_snapshot.get("account_channel") != PAPER_ACCOUNT_CHANNEL:
        errors.append("account_channel must be lb_papertrading")
    if order_intent.get("status") != "ready":
        errors.append("intent status must be ready")
    if order_intent.get("side") != "buy":
        errors.append("only buy side is supported")
    if order_intent.get("order_type") != "LO":
        errors.append("only LO limit orders are supported")
    if int(order_intent.get("quantity") or 0) <= 0:
        errors.append("quantity must be > 0")
    if order_intent.get("intent_id") in submitted_intent_ids:
        errors.append("intent_id was already submitted")
    if len(submitted_intent_ids) >= config.max_daily_orders:
        errors.append("daily paper order limit reached")
    if cash is None or estimated_notional > cash:
        errors.append("estimated_notional exceeds available cash")
    if net_liquidation is None or net_liquidation <= 0:
        errors.append("account.net_liquidation must be > 0")
    else:
        if estimated_risk > net_liquidation * max_account_risk_pct / 100:
            errors.append("estimated risk exceeds intent max_account_risk_pct")
        if estimated_risk > net_liquidation * config.max_daily_risk_pct / 100:
            errors.append("estimated risk exceeds daily max risk")

    if not order_intent.get("remark"):
        warnings.append("intent has no remark for broker-side traceability")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "account_channel": account_snapshot.get("account_channel"),
            "estimated_account_risk": estimated_risk,
            "estimated_notional": estimated_notional,
            "cash": cash,
            "net_liquidation": net_liquidation,
            "submitted_count": len(submitted_intent_ids),
        },
    }
