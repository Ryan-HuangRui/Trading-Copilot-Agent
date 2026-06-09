#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


PAPER_ACCOUNT_CHANNEL = "lb_papertrading"
PAPER_READ_COMMANDS = {
    ("auth", "status"),
    ("assets",),
    ("positions",),
    ("order",),
    ("order", "executions"),
}


def ensure_paper_read_command(args: list[str]) -> None:
    if not args:
        raise ValueError("Longbridge command args are required")
    root = args[0].lower()
    command = (root,)
    if root in {"auth", "order"} and len(args) > 1 and not args[1].startswith("-"):
        command = (root, args[1].lower())
    if command not in PAPER_READ_COMMANDS:
        raise ValueError(f"Longbridge paper command is not read-only: {args}")


def longbridge_cli_path(explicit_path: str | None = None) -> str:
    if explicit_path:
        return explicit_path
    return shutil.which("longbridge") or str(Path.home() / ".local" / "bin" / "longbridge")


def run_json(cli: str, args: list[str]) -> Any:
    ensure_paper_read_command(args)
    if not Path(cli).exists() and shutil.which(cli) is None:
        raise RuntimeError(f"Longbridge CLI not found: {cli}")
    proc = subprocess.run([cli, *args], check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"Longbridge CLI failed: {detail}")
    output = proc.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Longbridge CLI did not return JSON: {output[:200]}") from exc


def ensure_paper_account(auth_status: dict[str, Any], paper_execution_config: dict[str, Any] | None = None) -> str:
    account = auth_status.get("account")
    if not isinstance(account, dict):
        raise ValueError("Longbridge auth status has no account object")
    token = auth_status.get("token")
    if isinstance(token, dict) and token.get("status") != "valid":
        raise ValueError(f"Longbridge token is not valid: {token.get('status')}")
    channel = str(account.get("account_channel") or "")
    if not channel and (paper_execution_config or {}).get("allow_auth_status_unknown_paper_channel"):
        return PAPER_ACCOUNT_CHANNEL
    if channel != PAPER_ACCOUNT_CHANNEL:
        raise ValueError(f"Longbridge account is not paper trading: {channel or 'unknown'}")
    return channel


def fetch_paper_snapshot(cli: str, paper_execution_config: dict[str, Any] | None = None) -> dict[str, Any]:
    auth = run_json(cli, ["auth", "status", "--format", "json"])
    if not isinstance(auth, dict):
        raise RuntimeError("Longbridge auth status did not return an object")
    ensure_paper_account(auth, paper_execution_config)
    return {
        "auth": auth,
        "account": run_json(cli, ["assets", "--format", "json"]),
        "positions": run_json(cli, ["positions", "--format", "json"]),
        "orders": run_json(cli, ["order", "--format", "json"]),
        "executions": run_json(cli, ["order", "executions", "--format", "json"]),
    }
