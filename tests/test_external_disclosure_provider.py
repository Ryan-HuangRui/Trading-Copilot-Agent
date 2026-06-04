import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from external_disclosure_provider import run


OPEN_CABINET_FIXTURE = {
    "exportedAt": "2026-06-01T00:00:00Z",
    "officials": [
        {
            "name": "Trump, Donald J.",
            "slug": "trump-donald-j",
            "title": "President of the United States",
            "transactionCount": 2,
            "mostRecentFilingDate": "2026-05-14",
            "transactions": [
                {
                    "description": "Micron Technology Inc",
                    "ticker": "MU",
                    "type": "Purchase",
                    "date": "2026-05-10",
                    "amount": "$15,001-$50,000",
                    "lateFilingFlag": False,
                },
                {
                    "description": "AAR CORP",
                    "ticker": None,
                    "type": "Sale",
                    "date": "2026-05-10",
                    "amount": "$1,001-$15,000",
                    "lateFilingFlag": True,
                },
            ],
        }
    ],
}


class ExternalDisclosureProviderTest(unittest.TestCase):
    def test_run_writes_trump_disclosure_artifact_with_symbol_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "open-cabinet.json"
            output = root / "report" / "2026-06-04" / "external-disclosures" / "trump-trades.json"
            fixture.write_text(json.dumps(OPEN_CABINET_FIXTURE), encoding="utf-8")

            result = run(
                Namespace(
                    date="2026-06-04",
                    symbol=["MU", "NVDA"],
                    input=str(fixture),
                    output=str(output),
                    source_url="https://open-cabinet.org/data/full-dataset.json",
                    official_slug="trump-donald-j",
                    lookback_days=120,
                    max_transactions=50,
                    repo_root=str(root),
                )
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["summary"]["recent_transactions"], 2)
            self.assertEqual(result["summary"]["matched_transactions"], 1)
            self.assertEqual(result["summary"]["transactions_without_ticker"], 1)
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["official"]["slug"], "trump-donald-j")
            self.assertEqual(payload["evidence"][0]["symbol"], "MU")
            self.assertEqual(payload["evidence"][0]["source_type"], "news")
            self.assertEqual(payload["evidence"][0]["source_subtype"], "oge_disclosure")
            self.assertIn("not a trading signal", payload["safety_note"])


if __name__ == "__main__":
    unittest.main()
