#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from longbridge_paper_trade_adapter import PAPER_ACCOUNT_CHANNEL, ensure_paper_account


PAPER_EXECUTION_ENV = "TRADING_COPILOT_PAPER_EXECUTION"


def format_decimal(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


class LongbridgePaperOrderAdapter:
    def __init__(self, cli: str | None = None) -> None:
        self.cli = cli or shutil.which("longbridge") or str(Path.home() / ".local" / "bin" / "longbridge")

    def run_json(self, args: list[str]) -> Any:
        if not Path(self.cli).exists() and shutil.which(self.cli) is None:
            raise RuntimeError(f"Longbridge CLI not found: {self.cli}")
        proc = subprocess.run([self.cli, *args], check=False, text=True, capture_output=True)
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

    def assert_paper_account(self) -> str:
        payload = self.run_json(["auth", "status", "--format", "json"])
        if not isinstance(payload, dict):
            raise RuntimeError("Longbridge auth status did not return an object")
        return ensure_paper_account(payload)

    def ensure_submit_allowed(self, *, execute: bool, env: Mapping[str, str] | None = None) -> None:
        if not execute:
            raise PermissionError("--execute is required to submit paper orders")
        values = env if env is not None else os.environ
        if values.get(PAPER_EXECUTION_ENV) != "enabled":
            raise PermissionError(f"{PAPER_EXECUTION_ENV}=enabled is required to submit paper orders")

    def ensure_write_allowed(self, *, execute: bool, action: str, env: Mapping[str, str] | None = None) -> None:
        if not execute:
            raise PermissionError(f"--execute is required to {action} paper orders")
        values = env if env is not None else os.environ
        if values.get(PAPER_EXECUTION_ENV) != "enabled":
            raise PermissionError(f"{PAPER_EXECUTION_ENV}=enabled is required to {action} paper orders")

    def validate_limit_buy_intent(self, intent: dict[str, Any]) -> None:
        if intent.get("side") != "buy":
            raise ValueError("only buy side is supported")
        if intent.get("order_type") != "LO":
            raise ValueError("only LO limit orders are supported")
        if int(intent.get("quantity") or 0) <= 0:
            raise ValueError("quantity must be > 0")
        if not intent.get("longbridge_symbol"):
            raise ValueError("longbridge_symbol is required")
        if float(intent.get("limit_price") or 0) <= 0:
            raise ValueError("limit_price must be > 0")

    def submit_limit_order(
        self,
        intent: dict[str, Any],
        *,
        execute: bool,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        self.validate_limit_buy_intent(intent)
        self.ensure_write_allowed(execute=execute, action="submit", env=env)
        account_channel = self.assert_paper_account()
        quantity = str(int(intent["quantity"]))
        price = format_decimal(float(intent["limit_price"]))
        remark = str(intent.get("remark") or f"tca:{intent['intent_id']}")
        command = [
            "order",
            "buy",
            str(intent["longbridge_symbol"]),
            quantity,
            "--price",
            price,
            "--order-type",
            "LO",
            "--tif",
            str(intent.get("tif") or "day"),
            "--remark",
            remark,
            "--format",
            "json",
            "-y",
        ]
        raw_response = self.run_json(command)
        broker_order_id = None
        if isinstance(raw_response, dict):
            broker_order_id = raw_response.get("order_id") or raw_response.get("id") or raw_response.get("broker_order_id")
        return {
            "broker": "longbridge",
            "account_channel": account_channel or PAPER_ACCOUNT_CHANNEL,
            "broker_order_id": str(broker_order_id) if broker_order_id else None,
            "raw_request": {
                "command": command,
                "intent_id": intent["intent_id"],
                "remark": remark,
            },
            "raw_response": raw_response if isinstance(raw_response, dict) else {"response": raw_response},
        }

    def cancel_order(
        self,
        broker_order_id: str,
        *,
        execute: bool,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        order_id = str(broker_order_id or "").strip()
        if not order_id:
            raise ValueError("broker_order_id is required")
        self.ensure_write_allowed(execute=execute, action="cancel", env=env)
        account_channel = self.assert_paper_account()
        command = ["order", "cancel", order_id, "--format", "json", "-y"]
        raw_response = self.run_json(command)
        response_order_id = order_id
        if isinstance(raw_response, dict):
            response_order_id = str(raw_response.get("order_id") or raw_response.get("id") or raw_response.get("broker_order_id") or order_id)
        return {
            "broker": "longbridge",
            "account_channel": account_channel or PAPER_ACCOUNT_CHANNEL,
            "broker_order_id": response_order_id,
            "raw_request": {
                "command": command,
                "broker_order_id": order_id,
            },
            "raw_response": raw_response if isinstance(raw_response, dict) else {"response": raw_response},
        }
