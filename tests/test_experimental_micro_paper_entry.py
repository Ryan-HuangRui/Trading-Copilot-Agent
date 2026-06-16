import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_trade_submit


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")


def formal_monitor_preview() -> dict:
    return {
        "date": "2026-06-16",
        "session": "monitor",
        "orders": [
            {
                "signal_id": "monitor-AMD",
                "symbol": "AMD",
                "longbridge_symbol": "AMD.US",
                "status": "blocked",
                "reasons": [
                    "execution_status is not conditional_executable",
                    "plan_type is not trade_plan",
                    "risk.max_account_risk_pct must be > 0",
                ],
            }
        ],
    }


def paper_snapshot() -> dict:
    return {
        "date": "2026-06-16",
        "account_channel": "lb_papertrading",
        "account": {"net_liquidation": 100000, "cash": 1000, "currency": "USD"},
        "positions": [],
        "orders": [],
        "executions": [],
    }


def seed_learning_inputs(root: Path) -> None:
    write_json(
        root / "runtime" / "intraday" / "2026-06-16" / "state.json",
        {
            "date": "2026-06-16",
            "symbols": {
                "AMD": {
                    "symbol": "AMD",
                    "state": "price_touched",
                    "price_observation_state": "price_touched",
                    "trade_candidate_state": None,
                    "state_layer": "price_observation",
                    "trigger_price": 50.0,
                    "invalidation_price": 47.5,
                    "level_source": "pre_market_plan",
                    "bar_timestamp": "2026-06-16 10:35:00",
                }
            },
        },
    )
    write_json(
        root / "report" / "2026-06-16" / "monitor-signals.json",
        {
            "date": "2026-06-16",
            "session": "monitor",
            "signals": [
                {
                    "signal_id": "monitor-AMD",
                    "symbol": "AMD",
                    "plan_type": "no_trade",
                    "execution_status": "no_trade",
                    "reason": "price touched plan trigger but Codex kept no_trade",
                }
            ],
        },
    )
    write_json(
        root / "report" / "2026-06-16" / "data-quality.json",
        {
            "date": "2026-06-16",
            "session_phase": "intraday",
            "stale_data": False,
            "actual_latest_bar_date": "2026-06-16",
            "expected_bar_date": "2026-06-16",
        },
    )
    write_json(root / "runtime" / "paper" / "2026-06-16" / "paper-account-snapshot.json", paper_snapshot())


def learning_config(root: Path, *, allow: bool = True) -> Path:
    path = root / "config" / "experimental_micro_paper.json"
    write_json(
        path,
        {
            "experimental_micro_paper": {
                "allow_experimental_micro_paper": allow,
                "max_notional": 100,
                "max_loss": 5,
                "max_daily_trades": 2,
                "max_per_symbol_per_day": 1,
                "time_stop": "same_day_close",
                "default_market": "US",
                "order_type": "LO",
                "tif": "day",
            }
        },
    )
    return path


class ExperimentalMicroPaperEntryTest(unittest.TestCase):
    def test_formal_monitor_path_remains_not_ready_when_conditional_executable_is_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "report" / "2026-06-16" / "paper-trade-preview.json", formal_monitor_preview())
            write_json(root / "runtime" / "paper" / "2026-06-16" / "paper-account-snapshot.json", paper_snapshot())

            result = paper_trade_submit.run(
                Namespace(
                    repo_root=str(root),
                    date="2026-06-16",
                    session="monitor",
                    preview=None,
                    account_snapshot=None,
                    orders_journal=None,
                    signals=None,
                    output=None,
                    require_validation=False,
                    execute=False,
                    longbridge_cli=None,
                    max_daily_risk_pct=3.0,
                    max_daily_orders=3,
                    paper_execution_config=None,
                )
            )

            self.assertEqual(result["summary"]["ready"], 0)
            self.assertEqual(result["summary"]["blocked"], 1)

    def test_preview_builds_experimental_micro_paper_candidate_from_price_touched_no_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_learning_inputs(root)
            config = learning_config(root)
            import experimental_micro_paper_entry

            result = experimental_micro_paper_entry.run(
                Namespace(
                    repo_root=str(root),
                    date="2026-06-16",
                    config=str(config),
                    state=None,
                    signals=None,
                    data_quality=None,
                    output=None,
                    journal=None,
                    paper_execution_config=None,
                    longbridge_cli=None,
                    execute=False,
                )
            )

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["summary"]["ready"], 1)
            preview = json.loads((root / "report" / "2026-06-16" / "experimental-micro-paper-preview.json").read_text(encoding="utf-8"))
            candidate = preview["candidates"][0]
            self.assertEqual(candidate["mode"], "experimental_micro_paper")
            self.assertEqual(candidate["symbol"], "AMD")
            self.assertEqual(candidate["reason"], "price_touched_plan_trigger_but_codex_no_trade")
            self.assertEqual(candidate["entry_reference"], 50.0)
            self.assertEqual(candidate["invalidation_reference"], 47.5)
            self.assertEqual(candidate["level_source"], "pre_market_plan")
            self.assertEqual(candidate["data_quality"], "fresh")
            self.assertTrue(candidate["not_for_formal_stats"])
            self.assertLessEqual(candidate["estimated_notional"], 100)
            self.assertLessEqual(candidate["estimated_max_loss"], 5)
            self.assertFalse((root / "runtime" / "learning" / "2026-06-16" / "learning-trade-journal.jsonl").exists())

    def test_preview_is_blocked_when_learning_config_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_learning_inputs(root)
            config = learning_config(root, allow=False)
            import experimental_micro_paper_entry

            result = experimental_micro_paper_entry.run(
                Namespace(
                    repo_root=str(root),
                    date="2026-06-16",
                    config=str(config),
                    state=None,
                    signals=None,
                    data_quality=None,
                    output=None,
                    journal=None,
                    paper_execution_config=None,
                    longbridge_cli=None,
                    execute=False,
                )
            )

            self.assertEqual(result["summary"]["ready"], 0)
            self.assertIn("allow_experimental_micro_paper is false", {row["reason"] for row in result["blocked"]})

    def test_execute_writes_learning_journal_without_formal_trade_or_paper_order_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_learning_inputs(root)
            config = learning_config(root)
            paper_config = root / "config" / "paper_execution.local.json"
            write_json(
                paper_config,
                {
                    "paper_execution": {
                        "broker_writes_enabled": True,
                        "allow_experimental_micro_paper": True,
                        "allow_auth_status_unknown_paper_channel": True,
                    }
                },
            )
            import experimental_micro_paper_entry

            dry_run = experimental_micro_paper_entry.run(
                Namespace(
                    repo_root=str(root),
                    date="2026-06-16",
                    config=str(config),
                    state=None,
                    signals=None,
                    data_quality=None,
                    output=None,
                    journal=None,
                    paper_execution_config=str(paper_config),
                    longbridge_cli=None,
                    execute=False,
                )
            )
            self.assertEqual(dry_run["summary"]["ready"], 1)

            with patch.object(experimental_micro_paper_entry, "LongbridgePaperOrderAdapter") as adapter:
                adapter.return_value.submit_order.return_value = {
                    "broker_order_id": "learn-1",
                    "raw_request": {"command": ["order", "buy"]},
                    "raw_response": {"order_id": "learn-1"},
                    "account_channel": "lb_papertrading",
                }
                result = experimental_micro_paper_entry.run(
                    Namespace(
                        repo_root=str(root),
                        date="2026-06-16",
                        config=str(config),
                        state=None,
                        signals=None,
                        data_quality=None,
                        output=None,
                        journal=None,
                        paper_execution_config=str(paper_config),
                        longbridge_cli=None,
                        execute=True,
                    )
                )

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["summary"]["submitted"], 1)
            adapter.return_value.submit_order.assert_called_once()
            self.assertEqual(adapter.return_value.submit_order.call_args.kwargs["action"], "experimental_micro_paper")
            journal = root / "runtime" / "learning" / "2026-06-16" / "learning-trade-journal.jsonl"
            records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["mode"], "experimental_micro_paper")
            self.assertTrue(records[0]["not_for_formal_stats"])
            self.assertEqual(records[0]["post_review_label"], None)
            self.assertFalse((root / "runtime" / "journal" / "trades.jsonl").exists())
            self.assertFalse((root / "runtime" / "paper" / "2026-06-16" / "paper-orders.jsonl").exists())

    def test_execute_requires_existing_dry_run_preview_and_enabled_learning_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_learning_inputs(root)
            disabled = learning_config(root, allow=False)
            import experimental_micro_paper_entry

            with self.assertRaises(FileNotFoundError):
                experimental_micro_paper_entry.run(
                    Namespace(
                        repo_root=str(root),
                        date="2026-06-16",
                        config=str(disabled),
                        state=None,
                        signals=None,
                        data_quality=None,
                        output=None,
                        journal=None,
                        paper_execution_config=None,
                        longbridge_cli=None,
                        execute=True,
                    )
                )

            enabled = learning_config(root, allow=True)
            experimental_micro_paper_entry.run(
                Namespace(
                    repo_root=str(root),
                    date="2026-06-16",
                    config=str(enabled),
                    state=None,
                    signals=None,
                    data_quality=None,
                    output=None,
                    journal=None,
                    paper_execution_config=None,
                    longbridge_cli=None,
                    execute=False,
                )
            )
            learning_config(root, allow=False)
            with self.assertRaises(PermissionError):
                experimental_micro_paper_entry.run(
                    Namespace(
                        repo_root=str(root),
                        date="2026-06-16",
                        config=str(disabled),
                        state=None,
                        signals=None,
                        data_quality=None,
                        output=None,
                        journal=None,
                        paper_execution_config=None,
                        longbridge_cli=None,
                        execute=True,
                    )
                )

    def test_wrapper_exposes_experimental_micro_paper_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_learning_inputs(root)
            config = learning_config(root)
            proc = __import__("subprocess").run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "experimental-micro-paper-entry",
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-06-16",
                    "--config",
                    str(config),
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["workflow"], "experimental-micro-paper-entry")
            self.assertEqual(payload["summary"]["ready"], 1)


if __name__ == "__main__":
    unittest.main()
