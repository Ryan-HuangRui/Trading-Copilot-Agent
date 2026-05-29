import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from paper_order_models import build_order_intent, stable_intent_id


def ready_preview() -> dict:
    return {
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


class PaperOrderModelsTest(unittest.TestCase):
    def test_stable_intent_id_is_deterministic(self):
        first = stable_intent_id("2026-05-26", "pre-market", "sig-1", "MU")
        second = stable_intent_id("2026-05-26", "pre-market", "sig-1", "MU")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("2026-05-26:pre-market:MU:"))

    def test_build_order_intent_from_ready_preview(self):
        intent = build_order_intent(
            date="2026-05-26",
            session="pre-market",
            preview=ready_preview(),
        )

        self.assertEqual(intent["status"], "ready")
        self.assertEqual(intent["source_signal_id"], "sig-1")
        self.assertEqual(intent["symbol"], "MU")
        self.assertEqual(intent["side"], "buy")
        self.assertEqual(intent["order_type"], "LO")
        self.assertEqual(intent["quantity"], 200)
        self.assertEqual(intent["limit_price"], 100.0)
        self.assertEqual(intent["stop_price"], 95.0)
        self.assertEqual(intent["take_profit"], 112.0)
        self.assertEqual(intent["remark"], f"tca:{intent['intent_id']}")
        self.assertEqual(intent["idempotency_key"], intent["intent_id"])

    def test_build_order_intent_rejects_blocked_preview(self):
        preview = {**ready_preview(), "status": "blocked", "reasons": ["missing risk"]}

        with self.assertRaises(ValueError):
            build_order_intent(date="2026-05-26", session="pre-market", preview=preview)


if __name__ == "__main__":
    unittest.main()
