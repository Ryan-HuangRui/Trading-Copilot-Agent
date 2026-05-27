#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


READ_ONLY_ROOTS = {"account", "asset", "assets", "position", "positions", "portfolio", "quote", "market"}
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
    account = run_read_only_json(cli, ["account", "balance", "--format", "json"])
    positions = run_read_only_json(cli, ["account", "positions", "--format", "json"])
    return {"account": account, "positions": positions}
