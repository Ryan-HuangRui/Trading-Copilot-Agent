import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from longbridge_paper_order_adapter import LongbridgePaperOrderAdapter
from paper_order_models import build_order_intent


def order_intent() -> dict:
    return build_order_intent(
        date="2026-05-26",
        session="pre-market",
        preview={
            "signal_id": "sig-1",
            "symbol": "MU",
            "longbridge_symbol": "MU.US",
            "setup": "breakout_pullback_continuation.md",
            "side": "buy",
            "status": "ready",
            "quantity": 200,
            "entry_price": 100,
            "stop_price": 95,
            "take_profit": 112,
            "risk_per_share": 5,
            "max_account_risk_pct": 1,
            "estimated_account_risk": 1000,
            "estimated_notional": 20000,
            "order_type": "LO",
            "tif": "day",
        },
    )


def stop_intent() -> dict:
    return {
        "intent_id": "intent-1",
        "longbridge_symbol": "MU.US",
        "side": "sell",
        "order_type": "MIT",
        "quantity": 200,
        "trigger_price": 95,
        "tif": "gtc",
        "remark": "tca-stop:intent-1",
    }


class LongbridgePaperOrderAdapterTest(unittest.TestCase):
    def test_submit_requires_execute_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(PermissionError):
            adapter.submit_limit_order(order_intent(), execute=False, env={"TRADING_COPILOT_PAPER_EXECUTION": "enabled"})

    def test_submit_requires_env_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(PermissionError):
            adapter.submit_limit_order(order_intent(), execute=True, env={})

    def test_submit_rejects_non_paper_account(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with patch.object(adapter, "run_json", return_value={"account": {"account_channel": "live"}, "token": {"status": "valid"}}):
            with self.assertRaises(ValueError):
                adapter.submit_limit_order(
                    order_intent(),
                    execute=True,
                    env={"TRADING_COPILOT_PAPER_EXECUTION": "enabled"},
                )

    def test_submit_limit_order_builds_safe_command_and_records_raw_response(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[:2] == ["auth", "status"]:
                return {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}}
            if args[:2] == ["order", "buy"]:
                return {"order_id": "order-1", "status": "submitted"}
            raise AssertionError(args)

        with patch.object(adapter, "run_json", side_effect=fake_run):
            result = adapter.submit_limit_order(
                order_intent(),
                execute=True,
                env={"TRADING_COPILOT_PAPER_EXECUTION": "enabled"},
            )

        self.assertEqual(result["broker_order_id"], "order-1")
        self.assertEqual(result["raw_response"], {"order_id": "order-1", "status": "submitted"})
        self.assertEqual(
            calls[1],
            [
                "order",
                "buy",
                "MU.US",
                "200",
                "--price",
                "100",
                "--order-type",
                "LO",
                "--tif",
                "day",
                "--remark",
                result["raw_request"]["remark"],
                "--format",
                "json",
                "-y",
            ],
        )

    def test_submit_rejects_unsupported_order_shape(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")
        intent = {**order_intent(), "side": "sell"}

        with self.assertRaises(ValueError):
            adapter.submit_limit_order(
                intent,
                execute=True,
                env={"TRADING_COPILOT_PAPER_EXECUTION": "enabled"},
            )

    def test_cancel_requires_execute_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(PermissionError):
            adapter.cancel_order("order-1", execute=False, env={"TRADING_COPILOT_PAPER_EXECUTION": "enabled"})

    def test_cancel_requires_env_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(PermissionError):
            adapter.cancel_order("order-1", execute=True, env={})

    def test_cancel_rejects_missing_broker_order_id(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(ValueError):
            adapter.cancel_order("", execute=True, env={"TRADING_COPILOT_PAPER_EXECUTION": "enabled"})

    def test_cancel_order_builds_safe_command_and_records_raw_response(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[:2] == ["auth", "status"]:
                return {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}}
            if args[:2] == ["order", "cancel"]:
                return {"order_id": "order-1", "status": "cancelled"}
            raise AssertionError(args)

        with patch.object(adapter, "run_json", side_effect=fake_run):
            result = adapter.cancel_order(
                "order-1",
                execute=True,
                env={"TRADING_COPILOT_PAPER_EXECUTION": "enabled"},
            )

        self.assertEqual(result["broker_order_id"], "order-1")
        self.assertEqual(result["raw_response"], {"order_id": "order-1", "status": "cancelled"})
        self.assertEqual(calls[1], ["order", "cancel", "order-1", "--format", "json", "-y"])

    def test_protective_stop_requires_execute_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(PermissionError):
            adapter.submit_protective_stop_order(
                stop_intent(),
                execute=False,
                env={"TRADING_COPILOT_PAPER_EXECUTION": "enabled"},
            )

    def test_protective_stop_requires_env_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(PermissionError):
            adapter.submit_protective_stop_order(stop_intent(), execute=True, env={})

    def test_protective_stop_rejects_unsupported_shape(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(ValueError):
            adapter.submit_protective_stop_order(
                {**stop_intent(), "order_type": "LO"},
                execute=True,
                env={"TRADING_COPILOT_PAPER_EXECUTION": "enabled"},
            )

    def test_protective_stop_builds_safe_command_and_records_raw_response(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[:2] == ["auth", "status"]:
                return {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}}
            if args[:2] == ["order", "sell"]:
                return {"order_id": "stop-o-1", "status": "submitted"}
            raise AssertionError(args)

        with patch.object(adapter, "run_json", side_effect=fake_run):
            result = adapter.submit_protective_stop_order(
                stop_intent(),
                execute=True,
                env={"TRADING_COPILOT_PAPER_EXECUTION": "enabled"},
            )

        self.assertEqual(result["broker_order_id"], "stop-o-1")
        self.assertEqual(result["raw_response"], {"order_id": "stop-o-1", "status": "submitted"})
        self.assertEqual(
            calls[1],
            [
                "order",
                "sell",
                "MU.US",
                "200",
                "--order-type",
                "MIT",
                "--trigger-price",
                "95",
                "--tif",
                "gtc",
                "--remark",
                "tca-stop:intent-1",
                "--format",
                "json",
                "-y",
            ],
        )


if __name__ == "__main__":
    unittest.main()
