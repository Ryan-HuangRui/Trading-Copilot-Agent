import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from longbridge_paper_order_adapter import LongbridgePaperOrderAdapter, parse_json_output
from paper_order_models import build_order_intent


def write_config(**overrides) -> dict:
    config = {
        "broker_writes_enabled": True,
        "allow_entry_submit": True,
        "allow_cancel": True,
        "allow_protective_stop": True,
        "allow_take_profit": True,
    }
    config.update(overrides)
    return config


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


def market_order_intent() -> dict:
    payload = order_intent()
    payload.update({"order_type": "MO", "limit_price": None, "estimated_notional": 20000})
    return payload


def lit_order_intent() -> dict:
    payload = order_intent()
    payload.update({"order_type": "LIT", "limit_price": 100, "trigger_price": 101, "outside_rth": "RTH_ONLY"})
    return payload


def trailing_percent_intent() -> dict:
    payload = order_intent()
    payload.update(
        {
            "order_type": "TSLPPCT",
            "limit_price": None,
            "trailing_percent": 2.5,
            "limit_offset": 0.3,
            "tif": "gtc",
        }
    )
    return payload


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


def take_profit_intent() -> dict:
    return {
        "intent_id": "intent-1",
        "longbridge_symbol": "MU.US",
        "side": "sell",
        "order_type": "LO",
        "quantity": 100,
        "limit_price": 112,
        "tif": "gtc",
        "remark": "tca-tp1:intent-1",
    }


class LongbridgePaperOrderAdapterTest(unittest.TestCase):
    def test_parse_json_output_accepts_cli_progress_prefix(self):
        payload = parse_json_output(
            'Submitting Buy order: 89 ORCL.US @ 205\n{"order_id": "order-1"}'
        )

        self.assertEqual(payload, {"order_id": "order-1"})

    def test_submit_requires_execute_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())

        with self.assertRaises(PermissionError):
            adapter.submit_limit_order(order_intent(), execute=False)

    def test_submit_requires_config_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(PermissionError):
            adapter.submit_limit_order(order_intent(), execute=True)

    def test_submit_rejects_non_paper_account(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())

        with patch.object(adapter, "run_json", return_value={"account": {"account_channel": "live"}, "token": {"status": "valid"}}):
            with self.assertRaises(ValueError):
                adapter.submit_limit_order(
                    order_intent(),
                    execute=True,
                )

    def test_submit_limit_order_builds_safe_command_and_records_raw_response(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())
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
                "--order-type",
                "LO",
                "--price",
                "100",
                "--tif",
                "day",
                "--remark",
                result["raw_request"]["remark"],
                "--format",
                "json",
                "-y",
            ],
        )

    def test_submit_order_builds_market_order_without_price(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[:2] == ["auth", "status"]:
                return {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}}
            if args[:2] == ["order", "buy"]:
                return {"order_id": "mo-1", "status": "submitted"}
            raise AssertionError(args)

        with patch.object(adapter, "run_json", side_effect=fake_run):
            result = adapter.submit_order(market_order_intent(), execute=True)

        self.assertEqual(result["broker_order_id"], "mo-1")
        self.assertNotIn("--price", calls[1])
        self.assertEqual(calls[1][0:5], ["order", "buy", "MU.US", "200", "--order-type"])
        self.assertEqual(calls[1][5], "MO")

    def test_submit_order_builds_lit_order_with_price_and_trigger(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[:2] == ["auth", "status"]:
                return {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}}
            if args[:2] == ["order", "buy"]:
                return {"order_id": "lit-1", "status": "submitted"}
            raise AssertionError(args)

        with patch.object(adapter, "run_json", side_effect=fake_run):
            result = adapter.submit_order(lit_order_intent(), execute=True)

        self.assertEqual(result["broker_order_id"], "lit-1")
        self.assertIn("--price", calls[1])
        self.assertIn("--trigger-price", calls[1])
        self.assertIn("--outside-rth", calls[1])
        self.assertEqual(calls[1][calls[1].index("--order-type") + 1], "LIT")
        self.assertEqual(calls[1][calls[1].index("--price") + 1], "100")
        self.assertEqual(calls[1][calls[1].index("--trigger-price") + 1], "101")
        self.assertEqual(calls[1][calls[1].index("--outside-rth") + 1], "RTH_ONLY")

    def test_submit_order_builds_trailing_percent_order(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[:2] == ["auth", "status"]:
                return {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}}
            if args[:2] == ["order", "buy"]:
                return {"order_id": "trail-1", "status": "submitted"}
            raise AssertionError(args)

        with patch.object(adapter, "run_json", side_effect=fake_run):
            result = adapter.submit_order(trailing_percent_intent(), execute=True)

        self.assertEqual(result["broker_order_id"], "trail-1")
        self.assertEqual(calls[1][calls[1].index("--order-type") + 1], "TSLPPCT")
        self.assertEqual(calls[1][calls[1].index("--trailing-percent") + 1], "2.5")
        self.assertEqual(calls[1][calls[1].index("--limit-offset") + 1], "0.3")
        self.assertNotIn("--price", calls[1])

    def test_submit_rejects_unsupported_order_shape(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())
        intent = {**order_intent(), "side": "sell"}

        with self.assertRaises(ValueError):
            adapter.submit_limit_order(
                intent,
                execute=True,
            )

    def test_cancel_requires_execute_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())

        with self.assertRaises(PermissionError):
            adapter.cancel_order("order-1", execute=False)

    def test_cancel_requires_config_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(PermissionError):
            adapter.cancel_order("order-1", execute=True)

    def test_cancel_rejects_missing_broker_order_id(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())

        with self.assertRaises(ValueError):
            adapter.cancel_order("", execute=True)

    def test_cancel_order_builds_safe_command_and_records_raw_response(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[:2] == ["auth", "status"]:
                return {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}}
            if args[:2] == ["order", "cancel"]:
                return {"order_id": "order-1", "status": "cancelled"}
            raise AssertionError(args)

        with patch.object(adapter, "run_json", side_effect=fake_run):
            with patch.object(adapter, "run_text", return_value='{"order_id": "order-1", "status": "cancelled"}') as run_text:
                result = adapter.cancel_order(
                    "order-1",
                    execute=True,
                )

        self.assertEqual(result["broker_order_id"], "order-1")
        self.assertEqual(result["raw_response"], {"order_id": "order-1", "status": "cancelled"})
        run_text.assert_called_once_with(["order", "cancel", "order-1", "--format", "json", "-y"])

    def test_cancel_order_accepts_text_success_response(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())

        with patch.object(adapter, "run_json", return_value={"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}}):
            with patch.object(adapter, "run_text", return_value="Order order-1 cancelled."):
                result = adapter.cancel_order("order-1", execute=True)

        self.assertEqual(result["broker_order_id"], "order-1")
        self.assertEqual(result["raw_response"]["order_id"], "order-1")
        self.assertEqual(result["raw_response"]["status"], "cancelled")
        self.assertEqual(result["raw_response"]["response"], "Order order-1 cancelled.")

    def test_protective_stop_requires_execute_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())

        with self.assertRaises(PermissionError):
            adapter.submit_protective_stop_order(
                stop_intent(),
                execute=False,
            )

    def test_protective_stop_requires_config_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(PermissionError):
            adapter.submit_protective_stop_order(stop_intent(), execute=True)

    def test_protective_stop_rejects_unsupported_shape(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())

        with self.assertRaises(ValueError):
            adapter.submit_protective_stop_order(
                {**stop_intent(), "order_type": "LO"},
                execute=True,
            )

    def test_protective_stop_builds_safe_command_and_records_raw_response(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())
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

    def test_take_profit_requires_execute_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())

        with self.assertRaises(PermissionError):
            adapter.submit_take_profit_order(
                take_profit_intent(),
                execute=False,
            )

    def test_take_profit_requires_config_flag(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge")

        with self.assertRaises(PermissionError):
            adapter.submit_take_profit_order(take_profit_intent(), execute=True)

    def test_take_profit_rejects_unsupported_shape(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())

        with self.assertRaises(ValueError):
            adapter.submit_take_profit_order(
                {**take_profit_intent(), "order_type": "MIT"},
                execute=True,
            )

    def test_take_profit_builds_safe_command_and_records_raw_response(self):
        adapter = LongbridgePaperOrderAdapter(cli="/bin/longbridge", paper_execution_config=write_config())
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[:2] == ["auth", "status"]:
                return {"account": {"account_channel": "lb_papertrading"}, "token": {"status": "valid"}}
            if args[:2] == ["order", "sell"]:
                return {"order_id": "tp-o-1", "status": "submitted"}
            raise AssertionError(args)

        with patch.object(adapter, "run_json", side_effect=fake_run):
            result = adapter.submit_take_profit_order(
                take_profit_intent(),
                execute=True,
            )

        self.assertEqual(result["broker_order_id"], "tp-o-1")
        self.assertEqual(result["raw_response"], {"order_id": "tp-o-1", "status": "submitted"})
        self.assertEqual(
            calls[1],
            [
                "order",
                "sell",
                "MU.US",
                "100",
                "--price",
                "112",
                "--order-type",
                "LO",
                "--tif",
                "gtc",
                "--remark",
                "tca-tp1:intent-1",
                "--format",
                "json",
                "-y",
            ],
        )


if __name__ == "__main__":
    unittest.main()
