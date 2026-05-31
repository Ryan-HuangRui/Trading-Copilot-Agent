import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from validate_agent_decision import validate


def write_minimal_role_set(symbol_dir: Path) -> None:
    symbol_dir.mkdir(parents=True)
    (symbol_dir / "bull_report.json").write_text(
        json.dumps(
            {
                "role": "Bull Researcher",
                "symbol": "MU",
                "date": "2026-05-26",
                "supporting_evidence_ids": ["m1"],
                "opposing_evidence_ids": ["t1"],
                "thesis": "upside scenario",
                "limitations": [],
            }
        ),
        encoding="utf-8",
    )
    (symbol_dir / "bear_report.json").write_text(
        json.dumps(
            {
                "role": "Bear Researcher",
                "symbol": "MU",
                "date": "2026-05-26",
                "supporting_evidence_ids": ["t1"],
                "opposing_evidence_ids": ["m1"],
                "thesis": "downside scenario",
                "limitations": [],
            }
        ),
        encoding="utf-8",
    )
    (symbol_dir / "risk_report.json").write_text(
        json.dumps(
            {
                "role": "Risk Manager",
                "symbol": "MU",
                "date": "2026-05-26",
                "evidence_ids": ["m1", "t1"],
                "invalidation": "No trade if data is stale.",
                "liquidity_or_data_limits": ["fixture only"],
                "portfolio_constraints": ["max risk unchanged"],
                "risk_summary": {"status": "review_required"},
            }
        ),
        encoding="utf-8",
    )


def valid_decision() -> dict:
    return {
        "schema_version": 1,
        "decision_id": "2026-05-26:agent-decision:MU",
        "date": "2026-05-26",
        "symbol": "MU",
        "plan_type": "watch_only",
        "execution_status": "watch_only",
        "decision_label": "watch_only",
        "evidence_ids": ["m1", "t1"],
        "risk_summary": {"status": "review_required"},
        "limitations": [],
    }


class ValidateAgentDecisionTest(unittest.TestCase):
    def test_validate_rejects_placeholder_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            symbol_dir = Path(tmp) / "MU"
            write_minimal_role_set(symbol_dir)
            decision = valid_decision()
            decision["experimental"] = True
            decision["not_for_execution"] = True
            (symbol_dir / "decision.json").write_text(json.dumps(decision), encoding="utf-8")

            result = validate(Namespace(date="2026-05-26", symbol=["MU"], decision_dir=str(Path(tmp)), repo_root=str(ROOT)))

            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("placeholder" in error for error in result["errors"]))

    def test_validate_rejects_conditional_plan_without_trade_plan_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            symbol_dir = Path(tmp) / "MU"
            write_minimal_role_set(symbol_dir)
            decision = valid_decision()
            decision["plan_type"] = "trade_plan"
            decision["execution_status"] = "conditional_executable"
            (symbol_dir / "decision.json").write_text(json.dumps(decision), encoding="utf-8")

            result = validate(Namespace(date="2026-05-26", symbol=["MU"], decision_dir=str(Path(tmp)), repo_root=str(ROOT)))

            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("entry.trigger_price" in error for error in result["errors"]))

    def test_validate_rejects_forbidden_broker_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            symbol_dir = Path(tmp) / "MU"
            write_minimal_role_set(symbol_dir)
            decision = valid_decision()
            decision["broker_command"] = ["submit_order"]
            (symbol_dir / "decision.json").write_text(json.dumps(decision), encoding="utf-8")

            result = validate(Namespace(date="2026-05-26", symbol=["MU"], decision_dir=str(Path(tmp)), repo_root=str(ROOT)))

            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("forbidden" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
