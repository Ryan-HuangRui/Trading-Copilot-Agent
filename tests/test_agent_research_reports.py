import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from agent_research_reports import run as run_reports


class AgentResearchReportsTest(unittest.TestCase):
    def run_wrapper(self, *args):
        proc = subprocess.run(
            [sys.executable, "script/trading_copilot.py", *args],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
        return json.loads(proc.stdout)

    def write_inputs(self, root: Path) -> tuple[Path, Path]:
        market_data = root / "market-data.json"
        technicals = root / "technicals.json"
        market_data.write_text(
            json.dumps(
                {
                    "date": "2026-05-26",
                    "market_data": {"MU": {"latest": {"close": "108"}, "metrics": {"close_delta_pct": 8}}},
                    "evidence": [
                        {
                            "evidence_id": "m1",
                            "source": "market-data.json",
                            "source_type": "market_data",
                            "as_of": "2026-05-26",
                            "symbol": "MU",
                            "summary": "MU latest close 108",
                            "confidence": 0.95,
                            "limitations": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        technicals.write_text(
            json.dumps(
                {
                    "date": "2026-05-26",
                    "technicals": {"MU": {"metrics": {"sma_20": 100, "rsi_14": 60}}},
                    "evidence": [
                        {
                            "evidence_id": "t1",
                            "source": "technicals.json",
                            "source_type": "technical_indicator",
                            "as_of": "2026-05-26",
                            "symbol": "MU",
                            "summary": "MU technicals",
                            "confidence": 0.85,
                            "limitations": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return market_data, technicals

    def test_run_generates_five_structured_reports_for_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "agents"
            market_data, technicals = self.write_inputs(root)
            external_disclosures = root / "trump-trades.json"
            external_disclosures.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "date": "2026-05-26",
                        "evidence": [
                            {
                                "evidence_id": "trump-disclosure-MU-2026-05-10-0",
                                "source": "report/2026-05-26/external-disclosures/trump-trades.json",
                                "source_type": "news",
                                "source_subtype": "oge_disclosure",
                                "published_at": "2026-05-14",
                                "symbol": "MU",
                                "summary": "Trump disclosed Purchase of Micron Technology Inc in $15,001-$50,000 range.",
                                "confidence": 0.72,
                                "limitations": ["public disclosure is delayed and amount is a range"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_reports(
                Namespace(
                    date="2026-05-26",
                    symbol=["MU"],
                    market_data=str(market_data),
                    technicals=str(technicals),
                    provider_fixture=str(ROOT / "tests" / "fixtures" / "agent_research" / "provider_contract_fixture.json"),
                    external_disclosures=str(external_disclosures),
                    output_dir=str(output_dir),
                    markdown=True,
                    repo_root=str(ROOT),
                )
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["summary"]["symbols"], 1)
            self.assertEqual(result["summary"]["json_reports"], 5)
            self.assertTrue((output_dir / "MU" / "market_report.json").exists())
            self.assertTrue((output_dir / "MU" / "research_report.md").exists())
            news = json.loads((output_dir / "MU" / "news_report.json").read_text(encoding="utf-8"))
            self.assertEqual(news["report_type"], "news")
            self.assertEqual(news["evidence"][0]["source_type"], "news")
            self.assertTrue(
                any(item.get("source_subtype") == "oge_disclosure" for item in news["evidence"])
            )

    def test_wrapper_generates_and_validates_agent_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "agents"
            market_data, technicals = self.write_inputs(root)

            generated = self.run_wrapper(
                "agent-research-reports",
                "--date",
                "2026-05-26",
                "--symbol",
                "MU",
                "--market-data",
                str(market_data),
                "--technicals",
                str(technicals),
                "--output-dir",
                str(output_dir),
            )
            self.assertEqual(generated["summary"]["json_reports"], 5)

            validated = self.run_wrapper(
                "validate-agent-reports",
                "--date",
                "2026-05-26",
                "--symbol",
                "MU",
                "--reports-dir",
                str(output_dir),
            )

            self.assertEqual(validated["validation"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
