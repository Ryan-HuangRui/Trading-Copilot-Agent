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
}

ACTION_CONFIG_KEYS = {
    "entry_submit": "allow_entry_submit",
    "cancel": "allow_cancel",
    "protective_stop": "allow_protective_stop",
    "take_profit": "allow_take_profit",
}


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
