#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_RELATIVE_CONFIG = Path("config") / "paper_execution.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "broker_writes_enabled": False,
    "allow_entry_submit": False,
    "allow_cancel": False,
    "allow_protective_stop": False,
    "allow_take_profit": False,
    "allow_take_profit_stop_resize": False,
    "allow_intraday_entry_submit": False,
    "allow_exit_cancel_replace": False,
    "allow_exit_submit": False,
    "allow_break_even_stop_move": False,
}

ACTION_CONFIG_KEYS = {
    "entry_submit": "allow_entry_submit",
    "intraday_entry_submit": "allow_intraday_entry_submit",
    "cancel": "allow_cancel",
    "protective_stop": "allow_protective_stop",
    "take_profit": "allow_take_profit",
    "take_profit_stop_resize": "allow_take_profit_stop_resize",
    "exit_cancel_replace": "allow_exit_cancel_replace",
    "exit_submit": "allow_exit_submit",
    "break_even_stop_move": "allow_break_even_stop_move",
}

SUPPORTED_BROKER_ACTIONS: tuple[dict[str, Any], ...] = (
    {
        "action": "entry_submit",
        "label": "Entry order",
        "config_key": "allow_entry_submit",
        "order_type": "LO/ELO/MO/AO/ALO/ODD/SLO/LIT/MIT/TSLPAMT/TSLPPCT",
        "side": "buy",
        "workflow": "paper-trade-submit",
        "maturity": "initial_rollout",
    },
    {
        "action": "intraday_entry_submit",
        "label": "Intraday monitor entry order",
        "config_key": "allow_intraday_entry_submit",
        "order_type": "LO/ELO/MO/AO/ALO/ODD/SLO/LIT/MIT/TSLPAMT/TSLPPCT",
        "side": "buy",
        "workflow": "intraday-paper-entry",
        "maturity": "guarded_phase3",
    },
    {
        "action": "cancel",
        "label": "Expired entry cancel",
        "config_key": "allow_cancel",
        "order_type": "cancel",
        "side": None,
        "workflow": "paper-order-cancel",
        "maturity": "guarded",
    },
    {
        "action": "protective_stop",
        "label": "Protective stop",
        "config_key": "allow_protective_stop",
        "order_type": "MIT",
        "side": "sell",
        "workflow": "paper-protective-stop-plan",
        "maturity": "dry_run_first",
    },
    {
        "action": "take_profit",
        "label": "TP1 partial exit",
        "config_key": "allow_take_profit",
        "order_type": "LO/ELO/MO/AO/ALO/ODD/SLO/LIT/MIT/TSLPAMT/TSLPPCT",
        "side": "sell",
        "workflow": "paper-take-profit-plan",
        "maturity": "dry_run_first",
    },
    {
        "action": "take_profit_stop_resize",
        "label": "Resize protective stop before TP1",
        "config_key": "allow_take_profit_stop_resize",
        "order_type": "cancel + MIT",
        "side": "sell",
        "workflow": "paper-take-profit-plan",
        "maturity": "guarded_cancel_then_submit",
    },
    {
        "action": "break_even_stop_move",
        "label": "Break-even stop move",
        "config_key": "allow_break_even_stop_move",
        "order_type": "MIT",
        "side": "sell",
        "workflow": "paper-break-even-stop-plan",
        "maturity": "guarded_cancel_then_submit",
    },
    {
        "action": "exit_cancel_replace",
        "label": "Cancel open exit orders before full exit",
        "config_key": "allow_exit_cancel_replace",
        "order_type": "cancel",
        "side": None,
        "workflow": "paper-exit-plan",
        "maturity": "guarded_cancel_then_submit",
    },
    {
        "action": "exit_submit",
        "label": "Plan-invalidated exit order",
        "config_key": "allow_exit_submit",
        "order_type": "MO/LO/ELO/AO/ALO/ODD/SLO/LIT/MIT/TSLPAMT/TSLPPCT",
        "side": "sell",
        "workflow": "paper-exit-plan",
        "maturity": "guarded_exit",
    },
)

UNSUPPORTED_BROKER_ACTIONS: tuple[dict[str, Any], ...] = (
    {
        "action": "native_oco",
        "label": "Native OCO/bracket order",
        "reason": "broker capability and local cancel-replace safety are not contracted",
    },
    {
        "action": "short_entry",
        "label": "Short entry",
        "reason": "risk model and order lifecycle only support long paper entries",
    },
)


def resolve_config_path(repo_root: Path, explicit_path: str | None = None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / DEFAULT_RELATIVE_CONFIG


def normalize_paper_execution_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload or {}
    if isinstance(raw.get("paper_execution"), dict):
        raw = raw["paper_execution"]
    config = dict(DEFAULT_CONFIG)
    for key in DEFAULT_CONFIG:
        value = raw.get(key)
        if isinstance(value, bool):
            config[key] = value
    return config


def broker_capability_matrix(config: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = normalize_paper_execution_config(config)
    broker_writes_enabled = bool(normalized.get("broker_writes_enabled"))
    actions: list[dict[str, Any]] = []
    for capability in SUPPORTED_BROKER_ACTIONS:
        config_key = str(capability["config_key"])
        action_enabled = broker_writes_enabled and bool(normalized.get(config_key))
        actions.append(
            {
                **capability,
                "broker": "longbridge",
                "account_channel": "lb_papertrading",
                "broker_write": True,
                "requires_execute": True,
                "supported": True,
                "execution_status": "enabled" if action_enabled else "config_disabled",
            }
        )
    return {
        "broker": "longbridge",
        "account_channel": "lb_papertrading",
        "broker_writes_enabled": broker_writes_enabled,
        "actions": actions,
        "unsupported_actions": [dict(item) for item in UNSUPPORTED_BROKER_ACTIONS],
    }


def paper_execution_policy(config: dict[str, Any] | None = None) -> dict[str, Any]:
    matrix = broker_capability_matrix(config)
    allowed = [item["action"] for item in matrix["actions"] if item["execution_status"] == "enabled"]
    dry_run_only = [item["action"] for item in matrix["actions"] if item["execution_status"] != "enabled"]
    return {
        "broker": matrix["broker"],
        "account_channel": matrix["account_channel"],
        "broker_writes_enabled": matrix["broker_writes_enabled"],
        "allowed_broker_writes": allowed,
        "dry_run_only_actions": dry_run_only,
        "unsupported_actions": [item["action"] for item in matrix["unsupported_actions"]],
    }


def load_paper_execution_config(repo_root: Path, explicit_path: str | None = None) -> tuple[dict[str, Any], Path]:
    path = resolve_config_path(repo_root, explicit_path)
    if not path.exists():
        return normalize_paper_execution_config(None), path
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: paper execution config must be a JSON object")
    return normalize_paper_execution_config(payload), path


def ensure_paper_write_allowed(config: dict[str, Any], *, execute: bool, action: str) -> None:
    if not execute:
        raise PermissionError(f"--execute is required for paper {action}")
    if not config.get("broker_writes_enabled"):
        raise PermissionError("config paper_execution.broker_writes_enabled=true is required for paper broker writes")
    action_key = ACTION_CONFIG_KEYS.get(action)
    if not action_key:
        raise PermissionError(f"unsupported paper broker write action: {action}")
    if not config.get(action_key):
        raise PermissionError(f"config paper_execution.{action_key}=true is required for paper {action}")
