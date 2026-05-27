#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SIGNAL_STATUSES = {"planned", "observed", "triggered", "invalidated", "no_trade"}
SIGNAL_SESSIONS = {"pre-market", "post-market", "monitor"}
REQUIRED_ACTIONABLE_FIELDS = ("trigger", "invalidation", "risk")


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected top-level JSON object")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_signals_path(repo_root: Path, date: str) -> Path:
    return repo_root / "report" / date / "signals.json"


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
        "notes": signal.get("notes"),
    }
    return {key: value for key, value in payload.items() if value not in (None, [], "")}


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
            errors.append(f"{item}: setup file does not exist in knowledge/refined/setups: {setup}")

        status = signal.get("status", "planned")
        if status not in SIGNAL_STATUSES:
            errors.append(f"{item}: unsupported status: {status}")

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

    if len(seen_symbols) != len([s for s in signals if isinstance(s, dict) and s.get("symbol")]):
        warnings.append(f"{label}: duplicate symbols in signals array")

    return errors, warnings
