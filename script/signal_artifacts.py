#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SIGNAL_STATUSES = {"planned", "observed", "triggered", "invalidated", "no_trade"}
SIGNAL_SESSIONS = {"pre-market", "post-market", "monitor"}
PLAN_TYPES = {"trade_plan", "watch_only", "no_trade"}
EXECUTION_STATUSES = {"conditional_executable", "waiting_trigger", "watch_only", "no_trade"}
REQUIRED_ACTIONABLE_FIELDS = ("trigger", "invalidation", "risk")
SESSION_SIGNAL_FILENAMES = {
    "pre-market": "pre-market-signals.json",
    "post-market": "post-market-signals.json",
    "monitor": "monitor-signals.json",
}


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected top-level JSON object")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_signals_path(repo_root: Path, date: str, session: str | None = None) -> Path:
    filename = SESSION_SIGNAL_FILENAMES.get(session or "", "signals.json")
    return repo_root / "report" / date / filename


def legacy_signals_path(repo_root: Path, date: str) -> Path:
    return repo_root / "report" / date / "signals.json"


def resolve_signals_path(
    repo_root: Path,
    date: str,
    explicit_path: str | None = None,
    session: str | None = None,
    *,
    legacy_fallback: bool = True,
) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    path = default_signals_path(repo_root, date, session)
    legacy = legacy_signals_path(repo_root, date)
    if legacy_fallback and session and not path.exists() and legacy.exists():
        return legacy
    return path


def sidecar_source(signals_path: Path, repo_root: Path) -> str:
    return str(signals_path.relative_to(repo_root)) if signals_path.is_relative_to(repo_root) else str(signals_path)


def stable_signal_id(date: str, session: str, symbol: str, source: str) -> str:
    raw = f"{date}|{session}|{symbol.upper()}|{source}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{date}:{session}:{symbol.upper()}:{digest}"


def field_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        text = value.get("text")
        price = value.get("price")
        kind = value.get("type")
        parts = []
        if kind:
            parts.append(str(kind))
        if price is not None:
            parts.append(str(price))
        if text:
            parts.append(str(text))
        return " | ".join(parts) or None
    return str(value)


def field_price(value: Any) -> float | None:
    if isinstance(value, dict) and value.get("price") is not None:
        try:
            return float(value["price"])
        except (TypeError, ValueError):
            return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def risk_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        if value.get("text"):
            return str(value["text"])
        if value.get("max_risk_pct") is not None:
            return f"单笔风险 <= {value['max_risk_pct']}%"
    return str(value)


def nested_float(value: Any, *keys: str) -> float | None:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    try:
        return float(current)
    except (TypeError, ValueError):
        return None


def nested_value(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def normalize_sidecar_signal(
    *,
    signal: dict[str, Any],
    date: str,
    session: str,
    source_report: str | None,
    source_signals: str,
) -> dict[str, Any]:
    symbol = str(signal.get("symbol") or "").upper()
    setup = str(signal.get("setup") or "NO VALID SETUP")
    setup_files = signal.get("setup_files")
    if not isinstance(setup_files, list):
        setup_files = [] if setup == "NO VALID SETUP" else [setup]

    trigger = signal.get("trigger")
    invalidation = signal.get("invalidation")
    risk = signal.get("risk")
    source = source_report or source_signals
    payload: dict[str, Any] = {
        "kind": "signal",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "signal_id": str(signal.get("signal_id") or stable_signal_id(date, session, symbol, source)),
        "date": date,
        "session": session,
        "symbol": symbol,
        "setup": setup,
        "setup_files": setup_files,
        "direction": signal.get("direction"),
        "regime": signal.get("regime"),
        "status": signal.get("status") or "planned",
        "source_report": source_report,
        "source_signals": source_signals,
        "trigger": field_text(trigger),
        "trigger_detail": trigger if isinstance(trigger, dict) else None,
        "trigger_price": field_price(trigger),
        "invalidation": field_text(invalidation),
        "invalidation_detail": invalidation if isinstance(invalidation, dict) else None,
        "invalidation_price": field_price(invalidation),
        "risk": risk_text(risk),
        "risk_detail": risk if isinstance(risk, dict) else None,
        "plan_type": signal.get("plan_type"),
        "execution_status": signal.get("execution_status"),
        "entry": signal.get("entry") if isinstance(signal.get("entry"), dict) else None,
        "stop": signal.get("stop") if isinstance(signal.get("stop"), dict) else None,
        "take_profit": signal.get("take_profit") if isinstance(signal.get("take_profit"), dict) else None,
        "execution_rules": signal.get("execution_rules") if isinstance(signal.get("execution_rules"), dict) else None,
        "notes": signal.get("notes"),
    }
    return {key: value for key, value in payload.items() if value not in (None, [], "")}


def validate_conditional_trade_plan(signal: dict[str, Any], item: str) -> list[str]:
    errors: list[str] = []
    entry_price = nested_float(signal, "entry", "trigger_price")
    stop_price = nested_float(signal, "stop", "initial_stop")
    tp1 = nested_float(signal, "take_profit", "tp1")
    account_risk = nested_float(signal, "risk", "max_account_risk_pct")
    risk_per_share = nested_float(signal, "risk", "risk_per_share")
    skip_conditions = nested_value(signal, "execution_rules", "skip_conditions")

    if entry_price is None:
        errors.append(f"{item}: conditional_executable trade_plan entry.trigger_price is required")
    if stop_price is None:
        errors.append(f"{item}: conditional_executable trade_plan stop.initial_stop is required")
    if tp1 is None:
        errors.append(f"{item}: conditional_executable trade_plan take_profit.tp1 is required")
    if account_risk is None:
        errors.append(f"{item}: conditional_executable trade_plan risk.max_account_risk_pct is required")
    if risk_per_share is None or risk_per_share <= 0:
        errors.append(f"{item}: conditional_executable trade_plan risk.risk_per_share must be > 0")
    if not isinstance(skip_conditions, list) or not skip_conditions:
        errors.append(f"{item}: conditional_executable trade_plan execution_rules.skip_conditions must contain at least one item")

    if entry_price is None or stop_price is None or tp1 is None:
        return errors

    direction = str(signal.get("direction") or "long").lower()
    if direction == "short":
        risk = stop_price - entry_price
        reward = entry_price - tp1
    else:
        risk = entry_price - stop_price
        reward = tp1 - entry_price
    if risk <= 0:
        errors.append(f"{item}: conditional_executable trade_plan entry/stop risk must be > 0")
        return errors
    reward_risk_ratio = reward / risk
    if reward_risk_ratio < 2:
        errors.append(f"{item}: conditional_executable tradePlan reward_risk_ratio must be >= 2")
    return errors


def normalize_sidecar(payload: dict[str, Any], signals_path: Path, repo_root: Path) -> list[dict[str, Any]]:
    date = str(payload.get("date") or "")
    session = str(payload.get("session") or "")
    source_report = payload.get("source_report")
    if source_report is not None:
        source_report = str(source_report)
    source_signals = sidecar_source(signals_path, repo_root)
    raw_signals = payload.get("signals")
    if not isinstance(raw_signals, list):
        raise ValueError(f"{signals_path}: signals must be an array")
    normalized = []
    for raw in raw_signals:
        if not isinstance(raw, dict):
            continue
        normalized.append(
            normalize_sidecar_signal(
                signal=raw,
                date=date,
                session=session,
                source_report=source_report,
                source_signals=source_signals,
            )
        )
    return normalized


def validate_sidecar_payload(
    *,
    payload: dict[str, Any],
    path: Path,
    expected_date: str,
    expected_session: str,
    setup_files: set[str],
    method_card_paths: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    label = str(path)

    date = payload.get("date")
    session = payload.get("session")
    if date != expected_date:
        errors.append(f"{label}: date must be {expected_date}")
    if session != expected_session:
        errors.append(f"{label}: session must be {expected_session}")
    if session not in SIGNAL_SESSIONS:
        errors.append(f"{label}: unsupported session: {session}")

    signals = payload.get("signals")
    if not isinstance(signals, list):
        errors.append(f"{label}: signals must be an array")
        return errors, warnings

    if len(signals) > 3:
        warnings.append(f"{label}: signals contains more than 3 focused candidates")

    seen_symbols: set[str] = set()
    method_card_paths = method_card_paths or set()
    for idx, signal in enumerate(signals):
        item = f"{label}: signals[{idx}]"
        if not isinstance(signal, dict):
            errors.append(f"{item}: must be an object")
            continue
        symbol = signal.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            errors.append(f"{item}: missing symbol")
        else:
            seen_symbols.add(symbol.upper())

        setup = signal.get("setup")
        if not isinstance(setup, str) or not setup.strip():
            errors.append(f"{item}: missing setup")
        elif setup != "NO VALID SETUP" and setup not in setup_files:
            errors.append(f"{item}: setup file is not approved by the canonical rulebook: {setup}")

        status = signal.get("status", "planned")
        if status not in SIGNAL_STATUSES:
            errors.append(f"{item}: unsupported status: {status}")
        plan_type = signal.get("plan_type")
        if plan_type is not None and plan_type not in PLAN_TYPES:
            errors.append(f"{item}: unsupported plan_type: {plan_type}")
        execution_status = signal.get("execution_status")
        if execution_status is not None and execution_status not in EXECUTION_STATUSES:
            errors.append(f"{item}: unsupported execution_status: {execution_status}")

        serialized_signal = json.dumps(signal, ensure_ascii=False).lower()
        for marker in ("raw/", ".srt", "youtube.com", "youtu.be", "bilibili.com"):
            if marker in serialized_signal:
                errors.append(f"{item}: runtime signal references compiler-only source: {marker}")

        method_context = signal.get("method_context")
        if method_context is not None:
            if not isinstance(method_context, list):
                errors.append(f"{item}: method_context must be an array")
            else:
                for method_idx, method in enumerate(method_context):
                    method_item = f"{item}: method_context[{method_idx}]"
                    if not isinstance(method, dict):
                        errors.append(f"{method_item}: must be an object")
                        continue
                    method_path = method.get("path")
                    if not isinstance(method_path, str) or not method_path.strip():
                        errors.append(f"{method_item}: missing path")
                    elif method_path not in method_card_paths:
                        errors.append(f"{method_item}: path is not an active method card: {method_path}")
                    if not isinstance(method.get("summary"), str) or not method["summary"].strip():
                        errors.append(f"{method_item}: missing summary")

        actionable = status != "no_trade" and setup != "NO VALID SETUP"
        if actionable:
            for field in REQUIRED_ACTIONABLE_FIELDS:
                if signal.get(field) in (None, "", []):
                    errors.append(f"{item}: actionable signal missing {field}")
            for field in ("trigger", "invalidation"):
                value = signal.get(field)
                if not isinstance(value, dict):
                    errors.append(f"{item}: actionable signal {field} must be an object")
                    continue
                if value.get("price") is None:
                    errors.append(f"{item}: {field}.price is required")
                    continue
                try:
                    float(value["price"])
                except (TypeError, ValueError):
                    errors.append(f"{item}: {field}.price must be numeric")
            risk = signal.get("risk")
            if isinstance(risk, dict):
                max_risk = risk.get("max_risk_pct")
                risk_text_value = risk.get("text")
                if max_risk is None and not risk_text_value:
                    errors.append(f"{item}: risk.max_risk_pct or risk.text is required")
                if max_risk is not None:
                    try:
                        float(max_risk)
                    except (TypeError, ValueError):
                        errors.append(f"{item}: risk.max_risk_pct must be numeric")

        for field in ("trigger", "invalidation"):
            value = signal.get(field)
            if isinstance(value, dict) and "price" in value and value["price"] is not None:
                try:
                    float(value["price"])
                except (TypeError, ValueError):
                    errors.append(f"{item}: {field}.price must be numeric")

        if plan_type == "trade_plan" and execution_status == "conditional_executable":
            errors.extend(validate_conditional_trade_plan(signal, item))

    if len(seen_symbols) != len([s for s in signals if isinstance(s, dict) and s.get("symbol")]):
        warnings.append(f"{label}: duplicate symbols in signals array")

    return errors, warnings
