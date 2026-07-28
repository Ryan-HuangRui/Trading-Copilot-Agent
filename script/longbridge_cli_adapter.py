#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


READ_ONLY_ROOTS = {
    "account",
    "asset",
    "assets",
    "position",
    "positions",
    "portfolio",
    "quote",
    "market-status",
    "kline",
    "intraday",
    "static",
}
READ_ONLY_ORDER_COMMANDS = {
    ("order",),
    ("order", "executions"),
}
WRITE_TOKENS = {
    "order",
    "submit",
    "place",
    "buy",
    "sell",
    "cancel",
    "replace",
    "modify",
    "update",
    "delete",
    "create",
    "trade",
    "watchlist",
}


def ensure_read_only_command(args: list[str]) -> None:
    if not args:
        raise ValueError("Longbridge command args are required")
    root = args[0].lower()
    if root == "order":
        command_args = [str(token).lower() for token in args]
        if "--format" in command_args:
            index = command_args.index("--format")
            if index + 1 >= len(command_args) or command_args[index + 1] != "json":
                raise ValueError(f"Longbridge order read command requires --format json: {args}")
            del command_args[index : index + 2]
        if tuple(command_args) in READ_ONLY_ORDER_COMMANDS:
            return
        raise ValueError(f"Longbridge order command is not in the read-only allowlist: {args}")
    tokens = {str(token).lower() for token in args}
    if root not in READ_ONLY_ROOTS:
        raise ValueError(f"Longbridge command is not in the read-only allowlist: {args}")
    blocked = sorted(tokens & WRITE_TOKENS)
    if blocked:
        raise ValueError(f"Longbridge command contains non-read-only tokens: {blocked}")


def longbridge_cli_path(explicit_path: str | None = None) -> str:
    if explicit_path:
        return explicit_path
    return shutil.which("longbridge") or str(Path.home() / ".local" / "bin" / "longbridge")


def run_read_only_json(cli: str, args: list[str]) -> Any:
    ensure_read_only_command(args)
    if not Path(cli).exists() and shutil.which(cli) is None:
        raise RuntimeError(f"Longbridge CLI not found: {cli}")
    proc = subprocess.run([cli, *args], check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"Longbridge read-only CLI failed: {detail}")
    output = proc.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Longbridge CLI did not return JSON: {output[:200]}") from exc


def fetch_account_snapshot(cli: str) -> dict[str, Any]:
    account = run_read_only_json(cli, ["assets", "--format", "json"])
    positions = run_read_only_json(cli, ["positions", "--format", "json"])
    return {"account": account, "positions": positions}


def fetch_trade_snapshot(cli: str) -> dict[str, Any]:
    return {
        "orders": run_read_only_json(cli, ["order", "--format", "json"]),
        "executions": run_read_only_json(cli, ["order", "executions", "--format", "json"]),
    }
