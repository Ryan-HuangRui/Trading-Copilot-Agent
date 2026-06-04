import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import paper_event_ledger


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def order_record(**overrides) -> dict:
    record = {
        "kind": "paper_order",
        "intent_id": "intent-1",
        "source_signal_id": "sig-1",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "side": "buy",
        "order_type": "LO",
        "quantity": 200,
        "limit_price": 100,
        "remark": "tca:intent-1",
        "broker_order_id": "entry-o-1",
        "submit_status": "submitted",
        "submitted_at": "2026-05-26T13:30:00+00:00",
    }
    record.update(overrides)
    return record


def stop_record(**overrides) -> dict:
    record = {
        "kind": "paper_stop_order",
        "intent_id": "intent-1",
        "source_signal_id": "sig-1",
        "entry_broker_order_id": "entry-o-1",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "side": "sell",
        "order_type": "MIT",
        "quantity": 200,
        "trigger_price": 95,
        "remark": "tca-stop:intent-1",
        "broker_order_id": "stop-o-1",
        "submit_status": "submitted",
        "submitted_at": "2026-05-26T14:00:00+00:00",
    }
    record.update(overrides)
    return record


def take_profit_record(**overrides) -> dict:
    record = {
        "kind": "paper_take_profit_order",
        "intent_id": "intent-1",
        "source_signal_id": "sig-1",
        "entry_broker_order_id": "entry-o-1",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "side": "sell",
        "order_type": "LO",
        "quantity": 100,
        "limit_price": 112,
        "remark": "tca-tp1:intent-1",
        "broker_order_id": "tp-o-1",
        "submit_status": "submitted",
        "submitted_at": "2026-05-26T14:05:00+00:00",
    }
    record.update(overrides)
    return record


def exit_record(**overrides) -> dict:
    record = {
        "kind": "paper_exit_order",
        "intent_id": "intent-1",
        "source_signal_id": "sig-1",
        "entry_broker_order_id": "entry-o-1",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "side": "sell",
        "order_type": "MO",
        "quantity": 100,
        "remark": "tca-exit:intent-1",
        "broker_order_id": "exit-o-1",
        "submit_status": "submitted",
        "submitted_at": "2026-05-26T15:50:00+00:00",
    }
    record.update(overrides)
    return record


def replace_record(**overrides) -> dict:
    record = {
        "kind": "paper_replace_order",
        "intent_id": "intent-1",
        "source_signal_id": "sig-1",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "broker_order_id": "entry-o-1",
        "previous_quantity": 200,
        "new_quantity": 100,
        "previous_limit_price": 100,
        "new_limit_price": 99.5,
        "decision_reason": "tighten pending entry after failed reclaim",
        "submitted_at": "2026-05-26T13:45:00+00:00",
        "raw_request": {"command": ["order", "replace", "entry-o-1", "--qty", "100", "--price", "99.5"]},
        "raw_response": {"order_id": "entry-o-1", "status": "replaced"},
    }
    record.update(overrides)
    return record


def execution_state() -> dict:
    return {
        "date": "2026-05-26",
        "orders": [
            {
                "intent_id": "intent-1",
                "source_signal_id": "sig-1",
                "symbol": "MU",
                "side": "buy",
                "quantity": 200,
                "broker_order_id": "entry-o-1",
                "status": "filled",
                "filled_quantity": 200,
                "avg_fill_price": 100.2,
            }
        ],
        "protective_stops": [
            {
                "kind": "paper_stop_order",
                "intent_id": "intent-1",
                "source_signal_id": "sig-1",
                "entry_broker_order_id": "entry-o-1",
                "symbol": "MU",
                "side": "sell",
                "quantity": 200,
                "broker_order_id": "stop-o-1",
                "status": "accepted",
                "filled_quantity": 0,
            }
        ],
        "take_profit_orders": [
            {
                "kind": "paper_take_profit_order",
                "intent_id": "intent-1",
                "source_signal_id": "sig-1",
                "entry_broker_order_id": "entry-o-1",
                "symbol": "MU",
                "side": "sell",
                "order_type": "LO",
                "quantity": 100,
                "limit_price": 112,
                "broker_order_id": "tp-o-1",
                "status": "filled",
                "filled_quantity": 100,
                "avg_fill_price": 112.1,
            }
        ],
        "exit_orders": [
            {
                "kind": "paper_exit_order",
                "intent_id": "intent-1",
                "source_signal_id": "sig-1",
                "entry_broker_order_id": "entry-o-1",
                "symbol": "MU",
                "side": "sell",
                "quantity": 100,
                "broker_order_id": "exit-o-1",
                "status": "filled",
                "filled_quantity": 100,
                "avg_fill_price": 94.8,
            }
        ],
    }


class PaperEventLedgerTest(unittest.TestCase):
    def seed(self, root: Path) -> None:
        base = root / "runtime" / "paper" / "2026-05-26"
        append_jsonl(base / "paper-orders.jsonl", order_record())
        append_jsonl(base / "paper-stop-orders.jsonl", stop_record())
        append_jsonl(base / "paper-take-profit-orders.jsonl", take_profit_record())
        append_jsonl(base / "paper-exit-orders.jsonl", exit_record())
        write_json(base / "paper-execution-state.json", execution_state())

    def test_event_ledger_projects_submitted_and_state_events_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            first = paper_event_ledger.run(paper_event_ledger.build_args(repo_root=str(root), date="2026-05-26"))
            second = paper_event_ledger.run(paper_event_ledger.build_args(repo_root=str(root), date="2026-05-26"))

            self.assertEqual(first["summary"]["events_written_for_date"], second["summary"]["events_written_for_date"])
            output = json.loads(Path(second["output"]).read_text(encoding="utf-8"))
            events = output["events"]
            event_types = {event["event_type"] for event in events}
            self.assertIn("order_submitted", event_types)
            self.assertIn("order_filled", event_types)
            self.assertIn("stop_submitted", event_types)
            self.assertIn("stop_accepted", event_types)
            self.assertIn("take_profit_submitted", event_types)
            self.assertIn("take_profit_filled", event_types)
            self.assertIn("exit_submitted", event_types)
            self.assertIn("exit_filled", event_types)
            journal = root / "runtime" / "journal" / "events.jsonl"
            lines = [line for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(lines), len(events))
            event_ids = [json.loads(line)["event_id"] for line in lines]
            self.assertEqual(len(event_ids), len(set(event_ids)))

    def test_event_payload_preserves_take_profit_order_shape_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "runtime" / "paper" / "2026-05-26"
            trailing_tp = take_profit_record(
                order_type="TSLPPCT",
                limit_price=None,
                trailing_percent=2.5,
                limit_offset=0.3,
                tif="gtc",
                outside_rth="RTH_ONLY",
            )
            append_jsonl(base / "paper-take-profit-orders.jsonl", trailing_tp)
            state = execution_state()
            state["orders"] = []
            state["protective_stops"] = []
            state["exit_orders"] = []
            state["take_profit_orders"][0].update(
                {
                    "order_type": "TSLPPCT",
                    "limit_price": None,
                    "trailing_percent": 2.5,
                    "limit_offset": 0.3,
                    "tif": "gtc",
                    "outside_rth": "RTH_ONLY",
                }
            )
            write_json(base / "paper-execution-state.json", state)

            result = paper_event_ledger.run(paper_event_ledger.build_args(repo_root=str(root), date="2026-05-26"))

            output = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            events_by_type = {event["event_type"]: event for event in output["events"]}
            submitted_payload = events_by_type["take_profit_submitted"]["payload"]
            filled_payload = events_by_type["take_profit_filled"]["payload"]
            self.assertEqual(submitted_payload["order_type"], "TSLPPCT")
            self.assertEqual(submitted_payload["trailing_percent"], 2.5)
            self.assertEqual(submitted_payload["limit_offset"], 0.3)
            self.assertEqual(submitted_payload["outside_rth"], "RTH_ONLY")
            self.assertEqual(filled_payload["order_type"], "TSLPPCT")
            self.assertEqual(filled_payload["trailing_percent"], 2.5)
            self.assertEqual(filled_payload["limit_offset"], 0.3)

    def test_event_ledger_projects_order_replace_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "runtime" / "paper" / "2026-05-26"
            append_jsonl(base / "paper-replace-orders.jsonl", replace_record())

            result = paper_event_ledger.run(paper_event_ledger.build_args(repo_root=str(root), date="2026-05-26"))

            output = json.loads(Path(result["output"]).read_text(encoding="utf-8"))
            replaced = [event for event in output["events"] if event["event_type"] == "order_replaced"]
            self.assertEqual(len(replaced), 1)
            payload = replaced[0]["payload"]
            self.assertEqual(replaced[0]["entity_type"], "paper_entry_order")
            self.assertEqual(payload["broker_order_id"], "entry-o-1")
            self.assertEqual(payload["previous_quantity"], 200)
            self.assertEqual(payload["new_quantity"], 100)
            self.assertEqual(payload["previous_limit_price"], 100)
            self.assertEqual(payload["new_limit_price"], 99.5)
            self.assertEqual(payload["decision_reason"], "tighten pending entry after failed reclaim")
            self.assertEqual(payload["raw_request"]["command"][1], "replace")
            self.assertEqual(output["summary"]["event_types"]["order_replaced"], 1)

    def test_wrapper_exposes_event_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.seed(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "script" / "trading_copilot.py"),
                    "paper-event-ledger",
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
            self.assertEqual(payload["workflow"], "paper-event-ledger")
            self.assertEqual(payload["summary"]["events_written_for_date"], 8)
            self.assertEqual(len(payload["artifacts"]), 2)


if __name__ == "__main__":
    unittest.main()
