import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from earnings_financials import derive_standalone_facts, fourth_quarter, growth_transition, normalize_fact, standalone_quarter


class EarningsFinancialsTests(unittest.TestCase):
    def fact(self, value, start, end, *, unit="USD", basis="GAAP", segment=None):
        return {"metric": "Revenue", "value": value, "unit": unit, "start": start, "end": end,
                "accounting_basis": basis, "segment": segment, "source_evidence_ids": [f"e-{end}"]}

    def test_cumulative_to_standalone_quarter(self):
        result = standalone_quarter(self.fact(250, "2026-01-01", "2026-06-30"), self.fact(100, "2026-01-01", "2026-03-31"))
        self.assertEqual(result["value"], "150")
        self.assertEqual(result["period"]["start"], "2026-04-01")
        self.assertEqual(result["derivation"], "cumulative_difference")

    def test_annual_less_nine_months_is_q4(self):
        result = fourth_quarter(self.fact(500, "2026-01-01", "2026-12-31"), self.fact(360, "2026-01-01", "2026-09-30"))
        self.assertEqual(result["value"], "140")
        self.assertEqual(result["period"]["start"], "2026-10-01")

    def test_incomparable_currency_or_segment_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "different currency"):
            standalone_quarter(self.fact(250, "2026-01-01", "2026-06-30", unit="USD"),
                               self.fact(100, "2026-01-01", "2026-03-31", unit="EUR"))
        with self.assertRaises(ValueError):
            standalone_quarter(self.fact(250, "2026-01-01", "2026-06-30", segment="A"),
                               self.fact(100, "2026-01-01", "2026-03-31", segment="B"))

    def test_missing_and_loss_to_profit(self):
        self.assertIsNone(normalize_fact(self.fact(None, "2026-01-01", "2026-03-31"))["value"])
        self.assertEqual(growth_transition(2, -4), {"kind": "loss_to_profit", "growth_percent": None})

    def test_nonfinite_and_malformed_dates_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.assertRaisesRegex(ValueError, "nonfinite"):
                normalize_fact(self.fact(value, "2026-01-01", "2026-03-31"))
        with self.assertRaises(ValueError):
            normalize_fact({"metric": "Cash", "value": 1, "unit": "USD", "end": "bad-date"})

    def test_missing_subtraction_uses_derived_interval_and_rejects_nonquarter(self):
        result = standalone_quarter(self.fact(None, "2026-01-01", "2026-06-30"), self.fact(100, "2026-01-01", "2026-03-31"))
        self.assertIsNone(result["value"])
        self.assertEqual(result["period"]["start"], "2026-04-01")
        with self.assertRaisesRegex(ValueError, "not a standalone quarter"):
            standalone_quarter(self.fact(500, "2026-01-01", "2026-12-31"), self.fact(100, "2026-01-01", "2026-03-31"))

    def test_actual_pipeline_derives_q2_and_preserves_accession(self):
        q1 = normalize_fact(self.fact(100, "2026-01-01", "2026-03-31")); q1.update(fiscal_period="Q1", fiscal_year=2026, filed="2026-05-01", accession="a")
        q2 = normalize_fact(self.fact(250, "2026-01-01", "2026-06-30")); q2.update(fiscal_period="Q2", fiscal_year=2026, filed="2026-08-01", accession="b")
        derived = [row for row in derive_standalone_facts([q1, q2]) if row.get("derivation") == "cumulative_difference"]
        self.assertEqual(derived[0]["value"], "150")
        self.assertEqual(derived[0]["accession"], "b")
        self.assertEqual([row["accession"] for row in derived[0]["component_provenance"]], ["b", "a"])
        self.assertEqual(len(derived[0]["source_evidence_ids"]), 2)


if __name__ == "__main__":
    unittest.main()
