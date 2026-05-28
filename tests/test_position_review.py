import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from longbridge_account_snapshot import normalize_account
from longbridge_cli_adapter import ensure_read_only_command, fetch_account_snapshot
from position_review import load_config


class PositionReviewTest(unittest.TestCase):
    def test_longbridge_adapter_rejects_write_commands(self):
        with self.assertRaises(ValueError):
            ensure_read_only_command(["order", "submit", "--symbol", "MU"])
        with self.assertRaises(ValueError):
            ensure_read_only_command(["watchlist", "update", "group-1"])
        with self.assertRaises(ValueError):
            ensure_read_only_command(["trading", "days", "US", "--format", "json"])
        self.assertEqual(ensure_read_only_command(["account", "positions", "--format", "json"]), None)
        self.assertEqual(ensure_read_only_command(["kline", "MU.US", "--period", "day", "--format", "json"]), None)

    def test_fetch_account_snapshot_uses_supported_read_only_cli_commands(self):
        calls = []

        def fake_run(cli, args):
            calls.append((cli, args))
            return []

        with patch("longbridge_cli_adapter.run_read_only_json", side_effect=fake_run):
            self.assertEqual(fetch_account_snapshot("/bin/longbridge"), {"account": [], "positions": []})

        self.assertEqual(
            calls,
            [
                ("/bin/longbridge", ["assets", "--format", "json"]),
                ("/bin/longbridge", ["positions", "--format", "json"]),
            ],
        )

    def test_normalize_account_accepts_longbridge_assets_payload(self):
        account = normalize_account(
            [
                {
                    "net_assets": "100000.50",
                    "total_cash": "20000.25",
                    "currency": "USD",
                }
            ]
        )

        self.assertEqual(account["net_liquidation"], 100000.50)
        self.assertEqual(account["cash"], 20000.25)
        self.assertEqual(account["currency"], "USD")

    def test_account_snapshot_from_fixture_writes_normalized_runtime_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture.json"
            fixture.write_text(
                json.dumps(
                    {
                        "account": {"net_liquidation": 100000, "cash": 20000, "currency": "USD"},
                        "positions": [
                            {
                                "symbol": "MU.US",
                                "quantity": 100,
                                "avg_cost": 90.5,
                                "last_price": 96.2,
                                "market_value": 9620,
                                "unrealized_pnl": 570,
                                "unrealized_pnl_pct": 6.3,
                                "currency": "USD",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(ROOT / "script" / "longbridge_account_snapshot.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-27",
                "--input",
                str(fixture),
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            output = Path(payload["output"])
            self.assertTrue(output.exists())
            snapshot = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["positions"][0]["symbol"], "MU")
            self.assertEqual(snapshot["positions"][0]["market"], "US")
            self.assertEqual(snapshot["account"]["net_liquidation"], 100000.0)

    def test_position_review_flags_plan_and_invalidation_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account_dir = root / "runtime" / "account" / "2026-05-27"
            account_dir.mkdir(parents=True)
            (account_dir / "account-snapshot.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "account": {"net_liquidation": 100000, "cash": 20000, "currency": "USD"},
                        "positions": [
                            {
                                "symbol": "MU",
                                "market": "US",
                                "quantity": 100,
                                "avg_cost": 90,
                                "last_price": 96,
                                "market_value": 9600,
                                "unrealized_pnl": 600,
                                "unrealized_pnl_pct": 6.67,
                            },
                            {
                                "symbol": "OLD",
                                "market": "US",
                                "quantity": 10,
                                "last_price": 20,
                                "market_value": 200,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report_dir = root / "report" / "2026-05-27"
            report_dir.mkdir(parents=True)
            (report_dir / "signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "trigger": {"price": 100, "text": "突破 100"},
                                "invalidation": {"price": 95, "text": "跌破 95"},
                                "risk": {"max_risk_pct": 1},
                                "status": "planned",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(ROOT / "script" / "position_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-27",
                "--append",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["positions"], 2)
            review = json.loads((report_dir / "position-review.json").read_text(encoding="utf-8"))
            by_symbol = {item["symbol"]: item for item in review["position_reviews"]}
            self.assertTrue(by_symbol["MU"]["in_today_signals"])
            self.assertEqual(by_symbol["MU"]["risk_state"], "close_to_invalidation")
            self.assertFalse(by_symbol["OLD"]["in_today_signals"])
            self.assertTrue(by_symbol["OLD"]["review_required"])
            lines = (root / "runtime" / "journal" / "position_reviews.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)

    def test_position_review_uses_config_thresholds_and_ignore_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "position_review.json").write_text(
                json.dumps(
                    {
                        "position_review": {
                            "close_to_invalidation_pct": 1,
                            "high_concentration_pct": 50,
                            "ignore_symbols": ["OLD"],
                            "core_holding_symbols": ["CORE"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            account_dir = root / "runtime" / "account" / "2026-05-27"
            account_dir.mkdir(parents=True)
            (account_dir / "account-snapshot.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "account": {"net_liquidation": 100000, "cash": 20000, "currency": "USD"},
                        "positions": [
                            {"symbol": "MU", "last_price": 96, "market_value": 9600},
                            {"symbol": "OLD", "last_price": 20, "market_value": 200},
                            {"symbol": "CORE", "last_price": 50, "market_value": 1000},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report_dir = root / "report" / "2026-05-27"
            report_dir.mkdir(parents=True)
            (report_dir / "signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "trigger": {"price": 100},
                                "invalidation": {"price": 95},
                                "risk": {"max_risk_pct": 1},
                                "status": "planned",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(ROOT / "script" / "position_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-27",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            review = json.loads((report_dir / "position-review.json").read_text(encoding="utf-8"))
            by_symbol = {item["symbol"]: item for item in review["position_reviews"]}
            self.assertEqual(set(by_symbol), {"MU", "CORE"})
            self.assertEqual(by_symbol["MU"]["risk_state"], "normal")
            self.assertFalse(by_symbol["MU"]["review_required"])
            self.assertEqual(by_symbol["CORE"]["risk_state"], "core_holding_not_in_plan")
            self.assertFalse(by_symbol["CORE"]["review_required"])

    def test_position_review_empty_state_reports_cash_and_planned_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account_dir = root / "runtime" / "account" / "2026-05-27"
            account_dir.mkdir(parents=True)
            (account_dir / "account-snapshot.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "account": {"net_liquidation": 100000, "cash": 100000, "currency": "USD"},
                        "positions": [],
                    }
                ),
                encoding="utf-8",
            )
            report_dir = root / "report" / "2026-05-27"
            report_dir.mkdir(parents=True)
            (report_dir / "signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "trigger": {"price": 100},
                                "invalidation": {"price": 95},
                                "risk": {"max_risk_pct": 1},
                                "status": "planned",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "position_review.py"),
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-27",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            review = json.loads((report_dir / "position-review.json").read_text(encoding="utf-8"))
            self.assertTrue(review["summary"]["empty_position_state"])
            self.assertEqual(review["summary"]["planned_signals"], 1)
            self.assertEqual(review["summary"]["cash_pct"], 100.0)
            markdown = (report_dir / "position-review.md").read_text(encoding="utf-8")
            self.assertIn("空仓状态", markdown)
            self.assertIn("今日计划信号数：1", markdown)

    def test_position_review_uses_daily_snapshot_price_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account_dir = root / "runtime" / "account" / "2026-05-27"
            account_dir.mkdir(parents=True)
            (account_dir / "account-snapshot.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "account": {"net_liquidation": 100000, "cash": 80000, "currency": "USD"},
                        "positions": [{"symbol": "NVDA", "quantity": 10, "avg_cost": 200}],
                    }
                ),
                encoding="utf-8",
            )
            report_dir = root / "report" / "2026-05-27"
            report_dir.mkdir(parents=True)
            (report_dir / "pre-market-signals.json").write_text(
                json.dumps({"date": "2026-05-27", "session": "pre-market", "signals": []}),
                encoding="utf-8",
            )
            (report_dir / "daily-snapshot.json").write_text(
                json.dumps(
                    {
                        "snapshot_date": "2026-05-27",
                        "symbols": [{"symbol": "NVDA", "latest": {"close": "212.6"}}],
                    }
                ),
                encoding="utf-8",
            )

            command = [
                sys.executable,
                str(ROOT / "script" / "position_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-27",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            review = json.loads((report_dir / "position-review.json").read_text(encoding="utf-8"))
            record = review["position_reviews"][0]
            self.assertEqual(record["last_price"], 212.6)
            self.assertEqual(record["market_value"], 2126.0)
            self.assertEqual(record["unrealized_pnl"], 126.0)
            self.assertEqual(record["price_source"], "daily_snapshot")

    def test_position_review_links_trade_source_signal_and_estimated_r(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account_dir = root / "runtime" / "account" / "2026-05-27"
            account_dir.mkdir(parents=True)
            (account_dir / "account-snapshot.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "account": {"net_liquidation": 100000, "cash": 90000, "currency": "USD"},
                        "positions": [
                            {
                                "symbol": "MU",
                                "last_price": 105,
                                "market_value": 1050,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            journal = root / "runtime" / "journal"
            journal.mkdir(parents=True)
            (journal / "signals.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "signal",
                        "signal_id": "sig-1",
                        "date": "2026-05-26",
                        "session": "pre-market",
                        "symbol": "MU",
                        "setup": "breakout_pullback_continuation.md",
                        "invalidation_price": 95,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (journal / "trades.jsonl").write_text(
                json.dumps(
                    {
                        "kind": "trade",
                        "date": "2026-05-26",
                        "symbol": "MU",
                        "status": "entered",
                        "planned_setup": "breakout_pullback_continuation.md",
                        "entry": 100,
                        "stop": 95,
                        "source_signal_id": "sig-1",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            command = [
                sys.executable,
                str(ROOT / "script" / "position_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-27",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            review = json.loads((root / "report" / "2026-05-27" / "position-review.json").read_text(encoding="utf-8"))
            record = review["position_reviews"][0]
            self.assertEqual(record["trade_link_state"], "linked_to_source_signal")
            self.assertEqual(record["source_signal_id"], "sig-1")
            self.assertEqual(record["setup"], "breakout_pullback_continuation.md")
            self.assertEqual(record["nearest_invalidation"], 95.0)
            self.assertEqual(record["estimated_r"], 1.0)
            self.assertEqual(review["summary"]["trade_link_state"], {"linked_to_source_signal": 1})
            markdown = (root / "report" / "2026-05-27" / "position-review.md").read_text(encoding="utf-8")
            self.assertIn("交易关联：linked_to_source_signal", markdown)

    def test_position_review_can_require_trade_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "position_review.json").write_text(
                json.dumps(
                    {
                        "position_review": {
                            "require_trade_link": True,
                        }
                    }
                ),
                encoding="utf-8",
            )
            account_dir = root / "runtime" / "account" / "2026-05-27"
            account_dir.mkdir(parents=True)
            (account_dir / "account-snapshot.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "account": {"net_liquidation": 100000, "cash": 90000, "currency": "USD"},
                        "positions": [{"symbol": "MU", "last_price": 110, "market_value": 1100}],
                    }
                ),
                encoding="utf-8",
            )
            report_dir = root / "report" / "2026-05-27"
            report_dir.mkdir(parents=True)
            (report_dir / "signals.json").write_text(
                json.dumps(
                    {
                        "date": "2026-05-27",
                        "session": "pre-market",
                        "signals": [
                            {
                                "symbol": "MU",
                                "setup": "breakout_pullback_continuation.md",
                                "trigger": {"price": 120},
                                "invalidation": {"price": 95},
                                "risk": {"max_risk_pct": 1},
                                "status": "planned",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            command = [
                sys.executable,
                str(ROOT / "script" / "position_review.py"),
                "--repo-root",
                str(root),
                "--date",
                "2026-05-27",
            ]
            proc = subprocess.run(command, check=False, text=True, capture_output=True)

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            review = json.loads((report_dir / "position-review.json").read_text(encoding="utf-8"))
            record = review["position_reviews"][0]
            self.assertEqual(record["trade_link_state"], "no_trade_record")
            self.assertEqual(record["risk_state"], "missing_trade_link")
            self.assertTrue(record["review_required"])
            self.assertEqual(review["summary"]["trade_link_missing"], 1)

    def test_load_config_uses_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp), None)

        self.assertEqual(config["close_to_invalidation_pct"], 3.0)
        self.assertEqual(config["high_concentration_pct"], 25.0)
        self.assertFalse(config["require_trade_link"])


if __name__ == "__main__":
    unittest.main()
