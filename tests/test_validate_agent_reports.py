import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from validate_agent_reports import validate


def report_payload(report_type: str) -> dict:
    source_type = "technical_indicator" if report_type == "technicals" else report_type
    if report_type == "market":
        source_type = "market_data"
    return {
        "schema_version": 1,
        "report_type": report_type,
        "date": "2026-05-26",
        "symbol": "MU",
        "evidence": [
            {
                "evidence_id": f"{report_type}-1",
                "source": "fixture",
                "source_type": source_type,
                "as_of": "2026-05-26",
                "symbol": "MU",
                "summary": "fixture evidence",
                "confidence": 0.8,
                "limitations": [],
            }
        ],
        "facts": [],
        "derived_metrics": {},
        "scores": {},
        "limitations": [],
    }


class ValidateAgentReportsTest(unittest.TestCase):
    def write_reports(self, root: Path, mutate=None):
        symbol_dir = root / "MU"
        symbol_dir.mkdir(parents=True)
        for report_type in ("market", "technicals", "fundamentals", "news", "sentiment"):
            payload = report_payload(report_type)
            if mutate:
                mutate(report_type, payload)
            (symbol_dir / f"{report_type}_report.json").write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

    def test_validate_agent_reports_passes_complete_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_reports(root)

            result = validate(Namespace(date="2026-05-26", symbol=["MU"], reports_dir=str(root), repo_root=str(ROOT)))

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["checked_reports"], 5)
            self.assertEqual(result["errors"], [])

    def test_validate_agent_reports_fails_missing_evidence_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def mutate(report_type, payload):
                if report_type == "market":
                    payload["evidence"][0].pop("confidence")

            self.write_reports(root, mutate=mutate)

            result = validate(Namespace(date="2026-05-26", symbol=["MU"], reports_dir=str(root), repo_root=str(ROOT)))

            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("confidence" in error for error in result["errors"]))

    def test_validate_agent_reports_rejects_order_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def mutate(report_type, payload):
                if report_type == "market":
                    payload["facts"] = ["submit_order now"]

            self.write_reports(root, mutate=mutate)

            result = validate(Namespace(date="2026-05-26", symbol=["MU"], reports_dir=str(root), repo_root=str(ROOT)))

            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("forbidden" in error for error in result["errors"]))

    def test_validate_agent_reports_requires_market_and_technical_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def mutate(report_type, payload):
                if report_type in {"market", "technicals", "news"}:
                    payload["evidence"] = []

            self.write_reports(root, mutate=mutate)

            result = validate(Namespace(date="2026-05-26", symbol=["MU"], reports_dir=str(root), repo_root=str(ROOT)))

            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("market" in error and "evidence is required" in error for error in result["errors"]))
            self.assertTrue(any("technicals" in error and "evidence is required" in error for error in result["errors"]))
            self.assertTrue(any("news" in warning and "evidence is empty" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
