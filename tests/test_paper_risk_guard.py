import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from paper_order_models import build_order_intent
from paper_risk_guard import RiskGuardConfig, evaluate_order_intent, load_submitted_intent_ids


def intent(**overrides) -> dict:
    preview = {
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
    }
    payload = build_order_intent(date="2026-05-26", session="pre-market", preview=preview)
    payload.update(overrides)
    return payload


class PaperRiskGuardTest(unittest.TestCase):
    def test_ready_order_passes_guard(self):
        result = evaluate_order_intent(
            intent(),
            account_snapshot={"account_channel": "lb_papertrading", "account": {"net_liquidation": 100000, "cash": 25000}},
            submitted_intent_ids=set(),
            config=RiskGuardConfig(max_daily_risk_pct=3, max_daily_orders=3),
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["errors"], [])

    def test_non_paper_account_fails_guard(self):
        result = evaluate_order_intent(
            intent(),
            account_snapshot={"account_channel": "live", "account": {"net_liquidation": 100000, "cash": 25000}},
            submitted_intent_ids=set(),
            config=RiskGuardConfig(),
        )

        self.assertFalse(result["passed"])
        self.assertIn("account_channel must be lb_papertrading", result["errors"])

    def test_duplicate_intent_fails_guard(self):
        candidate = intent()
        result = evaluate_order_intent(
            candidate,
            account_snapshot={"account_channel": "lb_papertrading", "account": {"net_liquidation": 100000, "cash": 25000}},
            submitted_intent_ids={candidate["intent_id"]},
            config=RiskGuardConfig(),
        )

        self.assertFalse(result["passed"])
        self.assertIn("intent_id was already submitted", result["errors"])

    def test_cash_shortfall_fails_guard(self):
        result = evaluate_order_intent(
            intent(),
            account_snapshot={"account_channel": "lb_papertrading", "account": {"net_liquidation": 100000, "cash": 1000}},
            submitted_intent_ids=set(),
            config=RiskGuardConfig(),
        )

        self.assertFalse(result["passed"])
        self.assertIn("estimated_notional exceeds available cash", result["errors"])

    def test_unsupported_order_shape_fails_guard(self):
        result = evaluate_order_intent(
            intent(side="sell", order_type="MO"),
            account_snapshot={"account_channel": "lb_papertrading", "account": {"net_liquidation": 100000, "cash": 25000}},
            submitted_intent_ids=set(),
            config=RiskGuardConfig(),
        )

        self.assertFalse(result["passed"])
        self.assertIn("only buy side is supported", result["errors"])
        self.assertIn("only LO limit orders are supported", result["errors"])

    def test_load_submitted_intent_ids_reads_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper-orders.jsonl"
            path.write_text(
                json.dumps({"intent_id": "intent-1"}) + "\n" + json.dumps({"intent_id": "intent-2"}) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(load_submitted_intent_ids(path), {"intent-1", "intent-2"})


if __name__ == "__main__":
    unittest.main()
