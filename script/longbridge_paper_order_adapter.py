#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from longbridge_paper_trade_adapter import PAPER_ACCOUNT_CHANNEL, ensure_paper_account
from paper_execution_config import ensure_paper_write_allowed, normalize_paper_execution_config
from paper_order_models import (
    PRICE_REQUIRED_ORDER_TYPES,
    TRAILING_AMOUNT_REQUIRED_ORDER_TYPES,
    TRAILING_PERCENT_REQUIRED_ORDER_TYPES,
    TRIGGER_PRICE_REQUIRED_ORDER_TYPES,
    normalize_order_type,
    normalize_tif,
    validate_order_shape,
)


def format_decimal(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def parse_json_output(output: str) -> Any:
    text = output.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                payload, end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if text[index + end :].strip():
                continue
            return payload
        raise


def broker_order_id_from_response(raw_response: Any) -> str | None:
    if not isinstance(raw_response, dict):
        return None
    broker_order_id = raw_response.get("order_id") or raw_response.get("id") or raw_response.get("broker_order_id")
    return str(broker_order_id) if broker_order_id else None


class LongbridgePaperOrderAdapter:
    def __init__(self, cli: str | None = None, paper_execution_config: dict[str, Any] | None = None) -> None:
        self.cli = cli or shutil.which("longbridge") or str(Path.home() / ".local" / "bin" / "longbridge")
        self.paper_execution_config = normalize_paper_execution_config(paper_execution_config)

    def run_text(self, args: list[str]) -> str:
        if not Path(self.cli).exists() and shutil.which(self.cli) is None:
            raise RuntimeError(f"Longbridge CLI not found: {self.cli}")
        proc = subprocess.run([self.cli, *args], check=False, text=True, capture_output=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(f"Longbridge CLI failed: {detail}")
        return proc.stdout.strip()

    def run_json(self, args: list[str]) -> Any:
        output = self.run_text(args)
        if not output:
            return None
        try:
            return parse_json_output(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Longbridge CLI did not return JSON: {output[:200]}") from exc

    def assert_paper_account(self) -> str:
        payload = self.run_json(["auth", "status", "--format", "json"])
        if not isinstance(payload, dict):
            raise RuntimeError("Longbridge auth status did not return an object")
        return ensure_paper_account(payload)

    def ensure_write_allowed(self, *, execute: bool, action: str) -> None:
        ensure_paper_write_allowed(self.paper_execution_config, execute=execute, action=action)

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

    def validate_order_intent(self, intent: dict[str, Any]) -> None:
        errors = validate_order_shape(intent)
        if errors:
            raise ValueError("; ".join(errors))

    def submit_order_command(self, intent: dict[str, Any]) -> list[str]:
        self.validate_order_intent(intent)
        order_type = normalize_order_type(intent.get("order_type"))
        tif = normalize_tif(intent.get("tif"))
        remark = str(intent.get("remark") or f"tca:{intent['intent_id']}")
        command = [
            "order",
            str(intent["side"]),
            str(intent["longbridge_symbol"]),
            str(int(intent["quantity"])),
            "--order-type",
            order_type,
        ]
        if order_type in PRICE_REQUIRED_ORDER_TYPES:
            command.extend(["--price", format_decimal(float(intent["limit_price"]))])
        if order_type in TRIGGER_PRICE_REQUIRED_ORDER_TYPES:
            command.extend(["--trigger-price", format_decimal(float(intent["trigger_price"]))])
        if order_type in TRAILING_AMOUNT_REQUIRED_ORDER_TYPES:
            command.extend(["--trailing-amount", format_decimal(float(intent["trailing_amount"]))])
        if order_type in TRAILING_PERCENT_REQUIRED_ORDER_TYPES:
            command.extend(["--trailing-percent", format_decimal(float(intent["trailing_percent"]))])
        if order_type.startswith("TSLP") and intent.get("limit_offset") is not None:
            command.extend(["--limit-offset", format_decimal(float(intent["limit_offset"]))])
        command.extend(["--tif", tif])
        if tif == "gtd":
            command.extend(["--expire-date", str(intent["expire_date"])])
        if intent.get("outside_rth"):
            command.extend(["--outside-rth", str(intent["outside_rth"])])
        command.extend(["--remark", remark, "--format", "json", "-y"])
        return command

    def submit_order(
        self,
        intent: dict[str, Any],
        *,
        execute: bool,
        action: str = "entry_submit",
    ) -> dict[str, Any]:
        self.ensure_write_allowed(execute=execute, action=action)
        account_channel = self.assert_paper_account()
        command = self.submit_order_command(intent)
        raw_response = self.run_json(command)
        broker_order_id = broker_order_id_from_response(raw_response)
        return {
            "broker": "longbridge",
            "account_channel": account_channel or PAPER_ACCOUNT_CHANNEL,
            "broker_order_id": broker_order_id,
            "raw_request": {
                "command": command,
                "intent_id": intent["intent_id"],
                "remark": str(intent.get("remark") or f"tca:{intent['intent_id']}"),
            },
            "raw_response": raw_response if isinstance(raw_response, dict) else {"response": raw_response},
        }

    def submit_limit_order(
        self,
        intent: dict[str, Any],
        *,
        execute: bool,
        action: str = "entry_submit",
    ) -> dict[str, Any]:
        if action not in {"entry_submit", "intraday_entry_submit"}:
            raise ValueError(f"unsupported limit order action: {action}")
        self.validate_limit_buy_intent(intent)
        return self.submit_order(intent, execute=execute, action=action)

    def cancel_order(
        self,
        broker_order_id: str,
        *,
        execute: bool,
        action: str = "cancel",
    ) -> dict[str, Any]:
        order_id = str(broker_order_id or "").strip()
        if not order_id:
            raise ValueError("broker_order_id is required")
        self.ensure_write_allowed(execute=execute, action=action)
        account_channel = self.assert_paper_account()
        command = ["order", "cancel", order_id, "--format", "json", "-y"]
        output = self.run_text(command)
        if output:
            try:
                raw_response = parse_json_output(output)
            except json.JSONDecodeError:
                if order_id not in output or "cancel" not in output.lower():
                    raise RuntimeError(f"Longbridge CLI did not return JSON: {output[:200]}")
                raw_response = {"order_id": order_id, "status": "cancelled", "response": output}
        else:
            raw_response = {"order_id": order_id, "status": "cancelled", "response": ""}
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

    def validate_protective_stop_intent(self, intent: dict[str, Any]) -> None:
        if intent.get("side") != "sell":
            raise ValueError("only sell side is supported for protective stops")
        errors = validate_order_shape(intent)
        if errors:
            raise ValueError("; ".join(errors))

    def submit_protective_stop_order(
        self,
        intent: dict[str, Any],
        *,
        execute: bool,
        action: str = "protective_stop",
    ) -> dict[str, Any]:
        self.validate_protective_stop_intent(intent)
        self.ensure_write_allowed(execute=execute, action=action)
        account_channel = self.assert_paper_account()
        remark = str(intent.get("remark") or f"tca-stop:{intent['intent_id']}")
        command = self.submit_order_command({**intent, "remark": remark})
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

    def validate_take_profit_intent(self, intent: dict[str, Any]) -> None:
        if intent.get("side") != "sell":
            raise ValueError("only sell side is supported for take-profit orders")
        errors = validate_order_shape(intent)
        if errors:
            raise ValueError("; ".join(errors))

    def submit_take_profit_order(
        self,
        intent: dict[str, Any],
        *,
        execute: bool,
    ) -> dict[str, Any]:
        self.validate_take_profit_intent(intent)
        self.ensure_write_allowed(execute=execute, action="take_profit")
        account_channel = self.assert_paper_account()
        remark = str(intent.get("remark") or f"tca-tp1:{intent['intent_id']}")
        command = self.submit_order_command({**intent, "remark": remark})
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
