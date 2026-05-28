import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import data_quality


class DataQualityTest(unittest.TestCase):
    def test_data_quality_flags_focused_fallback_and_price_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-05-27"
            report_dir.mkdir(parents=True)
            (report_dir / "daily-snapshot.json").write_text(
                json.dumps(
                    {
                        "snapshot_date": "2026-05-27",
                        "market_data_source": "longbridge_with_twelve_data_fallback",
                        "primary_market_data_source": "longbridge",
                        "fallback_market_data_source": "twelve",
                        "stale_data": False,
                        "latest_bar_dates": ["2026-05-27"],
                        "symbols": [
                            {
                                "symbol": "MU",
                                "meta": {
                                    "provider": "twelve_data",
                                    "fallback_from": "longbridge",
                                    "primary_error": "permission denied",
                                },
                                "latest": {"datetime": "2026-05-27", "close": "100"},
                                "metrics": {"close_delta_pct": 25},
                            },
                            {
                                "symbol": "NVDA",
                                "meta": {"provider": "longbridge"},
                                "latest": {"datetime": "2026-05-27", "close": "200"},
                                "metrics": {"close_delta_pct": 1},
                            },
                        ],
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )
            (report_dir / "post-market-signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "session": "post-market",
                        "signals": [{"symbol": "MU"}, {"symbol": "AMD"}],
                    }
                ),
                encoding="utf-8",
            )
            account_dir = root / "runtime" / "account" / "2026-05-27"
            account_dir.mkdir(parents=True)
            (account_dir / "account-snapshot.json").write_text(
                json.dumps({"positions": [{"symbol": "MU.US", "last_price": 110}]}),
                encoding="utf-8",
            )

            result = data_quality.run(
                Namespace(
                    repo_root=str(root),
                    date="2026-05-27",
                    session="post-market",
                    snapshot=None,
                    account_snapshot=None,
                    output_json=None,
                    output_md=None,
                    account_delta_threshold_pct=5.0,
                    abnormal_move_threshold_pct=20.0,
                )
            )

            self.assertEqual(result["quality_status"], "fail")
            self.assertEqual(result["focused_fallback_symbols"][0]["symbol"], "MU")
            self.assertEqual(result["missing_focused_symbols"], ["AMD"])
            self.assertEqual(result["account_price_deltas"][0]["delta_pct"], 10.0)
            self.assertTrue((report_dir / "data-quality.json").exists())
            self.assertTrue((report_dir / "data-quality.md").exists())

    def test_data_quality_reads_pre_market_context_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-05-28"
            report_dir.mkdir(parents=True)
            (report_dir / "pre-market-context.json").write_text(
                json.dumps(
                    {
                        "source_snapshot_path": "report/2026-05-27/daily-snapshot.json",
                        "snapshot": {
                            "snapshot_date": "2026-05-27",
                            "stale_data": False,
                            "latest_bar_dates": ["2026-05-27"],
                            "symbols": [
                                {
                                    "symbol": "MU",
                                    "meta": {"provider": "longbridge"},
                                    "latest": {"datetime": "2026-05-27", "close": "100"},
                                    "metrics": {"close_delta_pct": 1},
                                }
                            ],
                            "errors": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (report_dir / "pre-market-signals.json").write_text(
                json.dumps({"date": "2026-05-28", "session": "pre-market", "signals": [{"symbol": "MU"}]}),
                encoding="utf-8",
            )

            result = data_quality.run(
                Namespace(
                    repo_root=str(root),
                    date="2026-05-28",
                    session="pre-market",
                    snapshot=None,
                    account_snapshot=None,
                    output_json=None,
                    output_md=None,
                    account_delta_threshold_pct=5.0,
                    abnormal_move_threshold_pct=20.0,
                )
            )

            self.assertEqual(result["quality_status"], "pass")
            payload = json.loads((report_dir / "data-quality.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["snapshot_path"], "report/2026-05-27/daily-snapshot.json")


if __name__ == "__main__":
    unittest.main()
