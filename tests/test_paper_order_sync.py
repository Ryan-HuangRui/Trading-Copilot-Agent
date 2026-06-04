import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_order_sync


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def submitted_order(**overrides) -> dict:
    record = {
        "kind": "paper_order",
        "intent_id": "2026-05-26:pre-market:MU:abc123",
        "source_signal_id": "sig-1",
        "date": "2026-05-26",
        "session": "pre-market",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "side": "buy",
        "order_type": "LO",
        "quantity": 200,
        "limit_price": 100,
        "remark": "tca:2026-05-26:pre-market:MU:abc123",
        "broker_order_id": "paper-o-1",
        "submit_status": "submitted",
        "submitted_at": "2026-05-26T13:30:00+00:00",
    }
    record.update(overrides)
    return record


def stop_order(**overrides) -> dict:
    record = {
        "kind": "paper_stop_order",
        "intent_id": "2026-05-26:pre-market:MU:abc123",
        "source_signal_id": "sig-1",
        "entry_broker_order_id": "paper-o-1",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "side": "sell",
        "order_type": "MIT",
        "quantity": 200,
        "trigger_price": 95,
        "remark": "tca-stop:2026-05-26:pre-market:MU:abc123",
        "broker_order_id": "stop-o-1",
        "submitted_at": "2026-05-26T14:00:00+00:00",
    }
    record.update(overrides)
    return record


def take_profit_order(**overrides) -> dict:
    record = {
        "kind": "paper_take_profit_order",
        "intent_id": "2026-05-26:pre-market:MU:abc123",
        "source_signal_id": "sig-1",
        "entry_broker_order_id": "paper-o-1",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "side": "sell",
        "order_type": "LO",
        "quantity": 100,
        "limit_price": 112,
        "take_profit": 112,
        "exit_fraction": 0.5,
        "remark": "tca-tp1:2026-05-26:pre-market:MU:abc123",
        "broker_order_id": "tp-o-1",
        "submitted_at": "2026-05-26T14:05:00+00:00",
    }
    record.update(overrides)
    return record


def snapshot(**overrides) -> dict:
    payload = {
        "date": "2026-05-26",
        "account_channel": "lb_papertrading",
        "account": {"net_liquidation": 100000, "cash": 25000},
        "orders": [
            {
                "order_id": "paper-o-1",
                "symbol": "MU",
                "market": "US",
                "side": "buy",
                "quantity": 200,
                "price": 100,
                "status": "filled",
                "raw": {"order_id": "paper-o-1", "remark": "tca:2026-05-26:pre-market:MU:abc123"},
            },
            {
                "order_id": "stop-o-1",
                "symbol": "MU",
                "market": "US",
                "side": "sell",
                "quantity": 200,
                "trigger_price": 95,
                "status": "accepted",
                "raw": {"order_id": "stop-o-1", "remark": "tca-stop:2026-05-26:pre-market:MU:abc123"},
            },
            {
                "order_id": "tp-o-1",
                "symbol": "MU",
                "market": "US",
                "side": "sell",
                "quantity": 100,
                "price": 112,
                "status": "filled",
                "raw": {"order_id": "tp-o-1", "remark": "tca-tp1:2026-05-26:pre-market:MU:abc123"},
            }
        ],
        "executions": [
            {
                "order_id": "paper-o-1",
                "symbol": "MU",
                "market": "US",
                "side": "buy",
                "quantity": 200,
                "price": 100.2,
                "raw": {"order_id": "paper-o-1"},
            },
            {
                "order_id": "tp-o-1",
                "symbol": "MU",
                "market": "US",
                "side": "sell",
                "quantity": 100,
                "price": 112.1,
                "raw": {"order_id": "tp-o-1"},
            }
        ],
    }
    payload.update(overrides)
    return payload


class PaperOrderSyncTest(unittest.TestCase):
    def seed(self, root: Path, order: dict | None = None, paper_snapshot: dict | None = None) -> None:
        append_jsonl(root / "runtime" / "paper" / "2026-05-26" / "paper-orders.jsonl", order or submitted_order())
        write_json(
            root / "runtime" / "paper" / "2026-05-26" / "paper-account-snapshot.json",
            paper_snapshot or snapshot(),
        )

    def seed_exit_orders(self, root: Path, stop: dict | None = None, take_profit: dict | None = None) -> None:
        append_jsonl(root / "runtime" / "paper" / "2026-05-26" / "paper-stop-orders.jsonl", stop or stop_order())
        append_jsonl(
            root / "runtime" / "paper" / "2026-05-26" / "paper-take-profit-orders.jsonl",
            take_profit or take_profit_order(),
        )

    def test_sync_matches_by_broker_order_id_and_marks_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            result = paper_order_sync.run(paper_order_sync.build_args(repo_root=str(root), date="2026-05-26"))

            self.assertEqual(result["summary"]["filled"], 1)
            state = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            synced = state["orders"][0]
            self.assertEqual(synced["status"], "filled")
            self.assertEqual(synced["match"]["method"], "broker_order_id")
            self.assertEqual(synced["filled_quantity"], 200)
            self.assertEqual(synced["avg_fill_price"], 100.2)

    def test_sync_exit_orders_and_enriches_entry_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)
            self.seed_exit_orders(root)

            result = paper_order_sync.run(paper_order_sync.build_args(repo_root=str(root), date="2026-05-26"))

            self.assertEqual(result["summary"]["filled"], 1)
            state = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            self.assertEqual(state["exit_summary"]["protective_stops"]["accepted"], 1)
            self.assertEqual(state["exit_summary"]["take_profit_orders"]["filled"], 1)
            self.assertEqual(state["protective_stops"][0]["status"], "accepted")
            self.assertEqual(state["take_profit_orders"][0]["status"], "filled")
            self.assertEqual(state["take_profit_orders"][0]["filled_quantity"], 100)
            entry = state["orders"][0]
            self.assertEqual(entry["protective_stop_order_id"], "stop-o-1")
            self.assertEqual(entry["stop_status"], "accepted")
            self.assertEqual(entry["take_profit_order_id"], "tp-o-1")
            self.assertEqual(entry["tp1_status"], "filled")
            self.assertEqual(entry["tp1_filled_quantity"], 100)
            self.assertEqual(entry["remaining_quantity"], 100)

    def test_state_v2_includes_capabilities_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)
            self.seed_exit_orders(root)

            result = paper_order_sync.run(paper_order_sync.build_args(repo_root=str(root), date="2026-05-26"))

            state = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            entry = state["orders"][0]
            self.assertEqual(state["schema_version"], "paper-execution-state/v2")
            self.assertEqual(state["broker_capabilities"]["broker"], "longbridge")
            self.assertEqual(entry["lifecycle"]["entry_status"], "filled")
            self.assertEqual(entry["lifecycle"]["protection_status"], "protected")
            self.assertEqual(entry["lifecycle"]["take_profit_status"], "tp1_filled")
            self.assertEqual(entry["lifecycle"]["remaining_quantity"], 100)
            self.assertEqual(entry["lifecycle"]["overall_status"], "partially_exited")

    def test_sync_uses_latest_stop_record_for_same_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(
                root,
                paper_snapshot=snapshot(
                    orders=[
                        snapshot()["orders"][0],
                        {
                            "order_id": "stop-o-1",
                            "symbol": "MU",
                            "market": "US",
                            "side": "sell",
                            "quantity": 200,
                            "trigger_price": 95,
                            "status": "cancelled",
                            "raw": {"order_id": "stop-o-1"},
                        },
                        {
                            "order_id": "stop-o-2",
                            "symbol": "MU",
                            "market": "US",
                            "side": "sell",
                            "quantity": 100,
                            "trigger_price": 100.2,
                            "status": "accepted",
                            "raw": {"order_id": "stop-o-2"},
                        },
                    ]
                ),
            )
            stop_path = root / "runtime" / "paper" / "2026-05-26" / "paper-stop-orders.jsonl"
            append_jsonl(stop_path, stop_order(broker_order_id="stop-o-1", trigger_price=95, quantity=200))
            append_jsonl(
                stop_path,
                stop_order(
                    kind="paper_break_even_stop_order",
                    broker_order_id="stop-o-2",
                    trigger_price=100.2,
                    quantity=100,
                    remark="tca-be-stop:2026-05-26:pre-market:MU:abc123",
                ),
            )

            result = paper_order_sync.run(paper_order_sync.build_args(repo_root=str(root), date="2026-05-26"))

            state = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            entry = state["orders"][0]
            self.assertEqual(entry["protective_stop_order_id"], "stop-o-2")
            self.assertEqual(entry["current_stop_price"], 100.2)
            self.assertEqual(entry["stop_status"], "accepted")

    def test_sync_matches_by_remark_when_broker_order_id_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            order = submitted_order(broker_order_id=None)
            self.seed(root, order=order)

            result = paper_order_sync.run(paper_order_sync.build_args(repo_root=str(root), date="2026-05-26"))

            state = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            synced = state["orders"][0]
            self.assertEqual(synced["status"], "filled")
            self.assertEqual(synced["match"]["method"], "remark")
            self.assertEqual(synced["broker_order_id"], "paper-o-1")

    def test_sync_falls_back_to_symbol_side_quantity_for_open_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            order = submitted_order(broker_order_id=None, remark="")
            paper_snapshot = snapshot(
                orders=[
                    {
                        "order_id": "paper-o-2",
                        "symbol": "MU",
                        "market": "US",
                        "side": "buy",
                        "quantity": 200,
                        "price": 100,
                        "status": "submitted",
                        "raw": {},
                    }
                ],
                executions=[],
            )
            self.seed(root, order=order, paper_snapshot=paper_snapshot)

            result = paper_order_sync.run(paper_order_sync.build_args(repo_root=str(root), date="2026-05-26"))

            state = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            synced = state["orders"][0]
            self.assertEqual(synced["status"], "accepted")
            self.assertEqual(synced["match"]["method"], "symbol_side_quantity")

    def test_wrapper_exposes_paper_order_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-order-sync",
                    "--repo-root",
                    str(root),
                    "--date",
                    "2026-05-26",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["workflow"], "paper-order-sync")
            self.assertEqual(payload["summary"]["filled"], 1)


if __name__ == "__main__":
    unittest.main()
