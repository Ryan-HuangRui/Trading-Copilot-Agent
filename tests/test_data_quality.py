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
            self.assertEqual(payload["quality_status"], "pass")

    def test_intraday_data_quality_reports_phase_fallback_and_current_bar_freshness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-06-15"
            report_dir.mkdir(parents=True)
            (report_dir / "daily-snapshot.json").write_text(
                json.dumps(
                    {
                        "snapshot_date": "2026-06-15",
                        "market_data_source": "longbridge_with_twelve_data_fallback",
                        "primary_market_data_source": "longbridge",
                        "fallback_market_data_source": "twelve",
                        "stale_data": False,
                        "latest_bar_dates": ["2026-06-15"],
                        "symbols": [
                            {
                                "symbol": "AMD",
                                "meta": {
                                    "provider": "twelve_data",
                                    "fallback_from": "longbridge",
                                    "primary_error": "connection reset by peer",
                                },
                                "latest": {"datetime": "2026-06-15 10:35:00", "close": "123"},
                                "metrics": {"close_delta_pct": 1},
                            }
                        ],
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )
            (report_dir / "monitor-signals.json").write_text(
                json.dumps({"signals": [{"symbol": "AMD"}]}),
                encoding="utf-8",
            )

            result = data_quality.run(
                Namespace(
                    repo_root=str(root),
                    date="2026-06-15",
                    session="intraday",
                    snapshot=None,
                    account_snapshot=None,
                    output_json=None,
                    output_md=None,
                    account_delta_threshold_pct=5.0,
                    abnormal_move_threshold_pct=20.0,
                )
            )

            self.assertEqual(result["quality_status"], "warn")
            payload = json.loads((report_dir / "data-quality.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["session_phase"], "intraday")
            self.assertEqual(payload["expected_bar_date"], "2026-06-15")
            self.assertEqual(payload["actual_latest_bar_date"], "2026-06-15")
            self.assertEqual(payload["provider_source"], "twelve_data")
            self.assertEqual(payload["fallback_from"], "longbridge")
            self.assertEqual(payload["fallback_reason"], "connection_reset")
            self.assertFalse(payload["stale_data"])
            markdown = (report_dir / "data-quality.md").read_text(encoding="utf-8")
            self.assertIn("session_phase: intraday", markdown)
            self.assertIn("fallback_reason: connection_reset", markdown)

    def test_intraday_data_quality_flags_previous_day_bar_as_stale_even_when_snapshot_flag_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-06-15"
            report_dir.mkdir(parents=True)
            (report_dir / "daily-snapshot.json").write_text(
                json.dumps(
                    {
                        "snapshot_date": "2026-06-15",
                        "stale_data": False,
                        "latest_bar_dates": ["2026-06-12"],
                        "symbols": [
                            {
                                "symbol": "AMD",
                                "meta": {"provider": "longbridge"},
                                "latest": {"datetime": "2026-06-12 15:55:00", "close": "123"},
                            }
                        ],
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )

            data_quality.run(
                Namespace(
                    repo_root=str(root),
                    date="2026-06-15",
                    session="intraday",
                    snapshot=None,
                    account_snapshot=None,
                    output_json=None,
                    output_md=None,
                    account_delta_threshold_pct=5.0,
                    abnormal_move_threshold_pct=20.0,
                )
            )

            payload = json.loads((report_dir / "data-quality.json").read_text(encoding="utf-8"))
            self.assertTrue(payload["stale_data"])
            self.assertEqual(payload["stale_reason"], "intraday latest bar date 2026-06-12 != expected 2026-06-15")

    def test_data_quality_warns_for_focused_timeframe_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "report" / "2026-06-16"
            report_dir.mkdir(parents=True)
            (report_dir / "daily-snapshot.json").write_text(
                json.dumps(
                    {
                        "snapshot_date": "2026-06-16",
                        "stale_data": False,
                        "latest_bar_dates": ["2026-06-16"],
                        "symbols": [
                            {
                                "symbol": "AMD",
                                "meta": {"provider": "longbridge"},
                                "latest": {"datetime": "2026-06-16", "close": "123"},
                            }
                        ],
                        "errors": [],
                        "timeframe_errors": [
                            {"symbol": "AMD", "interval": "1h", "error": "timeout"},
                            {"symbol": "MU", "interval": "15min", "error": "timeout"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (report_dir / "post-market-signals.json").write_text(
                json.dumps({"signals": [{"symbol": "AMD"}]}),
                encoding="utf-8",
            )

            result = data_quality.run(
                Namespace(
                    repo_root=str(root),
                    date="2026-06-16",
                    session="post-market",
                    snapshot=None,
                    account_snapshot=None,
                    output_json=None,
                    output_md=None,
                    account_delta_threshold_pct=5.0,
                    abnormal_move_threshold_pct=20.0,
                )
            )

            self.assertEqual(result["quality_status"], "warn")
            self.assertEqual(result["focused_timeframe_errors"][0]["interval"], "1h")
            payload = json.loads((report_dir / "data-quality.json").read_text(encoding="utf-8"))
            self.assertEqual(len(payload["timeframe_errors"]), 2)
            self.assertEqual(len(payload["focused_timeframe_errors"]), 1)
            self.assertIn("Focused Timeframe Errors", (report_dir / "data-quality.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
