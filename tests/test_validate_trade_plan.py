import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from validate_trade_plan import validate


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def trade_plan_signal(entry: dict | None = None) -> dict:
    return {
        "symbol": "MU",
        "setup": "breakout_pullback_continuation.md",
        "direction": "long",
        "trigger": {"type": "break_above", "price": 100, "text": "突破 100"},
        "invalidation": {"type": "break_below", "price": 95, "text": "跌破 95"},
        "risk": {
            "max_risk_pct": 1,
            "max_account_risk_pct": 1,
            "risk_per_share": 5,
        },
        "status": "planned",
        "plan_type": "trade_plan",
        "execution_status": "conditional_executable",
        "entry": entry
        or {
            "trigger_price": 100,
            "order_type": "LO",
            "confirmation": "pullback holds",
        },
        "stop": {"initial_stop": 95},
        "take_profit": {"tp1": 112},
        "execution_rules": {"skip_conditions": ["market turns risk-off"]},
    }


class ValidateTradePlanOrderShapeTest(unittest.TestCase):
    def make_repo(self, signal: dict) -> tuple[tempfile.TemporaryDirectory, Path, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        setup_dir = root / "knowledge" / "refined" / "setups"
        setup_dir.mkdir(parents=True)
        (setup_dir / "breakout_pullback_continuation.md").write_text("# setup\n", encoding="utf-8")
        sidecar = root / "report" / "2026-05-26" / "monitor-signals.json"
        write_json(
            sidecar,
            {
                "date": "2026-05-26",
                "session": "monitor",
                "signals": [signal],
            },
        )
        return temp, root, sidecar

    def validate_signal(self, signal: dict) -> dict:
        temp, root, sidecar = self.make_repo(signal)
        with temp:
            return validate(
                argparse.Namespace(
                    repo_root=str(root),
                    date="2026-05-26",
                    session="monitor",
                    signals=str(sidecar),
                )
            )

    def test_conditional_executable_rejects_gtd_without_expire_date(self):
        payload = self.validate_signal(
            trade_plan_signal(
                {
                    "limit_price": 100,
                    "trigger_price": 100,
                    "order_type": "LO",
                    "tif": "gtd",
                    "confirmation": "pullback holds",
                }
            )
        )

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("expire_date is required when tif is gtd" in error for error in payload["errors"]))

    def test_conditional_executable_rejects_trailing_percent_without_percent(self):
        payload = self.validate_signal(
            trade_plan_signal(
                {
                    "trigger_price": 100,
                    "order_type": "TSLPPCT",
                    "confirmation": "breakout holds",
                }
            )
        )

        self.assertEqual(payload["status"], "fail")
        self.assertTrue(any("trailing_percent must be > 0 for TSLPPCT" in error for error in payload["errors"]))

    def test_conditional_executable_accepts_market_order_shape(self):
        payload = self.validate_signal(
            trade_plan_signal(
                {
                    "trigger_price": 100,
                    "order_type": "MO",
                    "confirmation": "breakout holds",
                }
            )
        )

        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["errors"], [])


if __name__ == "__main__":
    unittest.main()
