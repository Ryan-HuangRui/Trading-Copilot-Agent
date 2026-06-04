import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

import intraday_event_notify


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")


class IntradayEventNotifyTest(unittest.TestCase):
    def args(self, root: Path, **overrides):
        values = {
            "repo_root": str(root),
            "date": "2026-05-26",
            "events": None,
            "sent_state": None,
            "message_output": None,
            "max_events": 5,
            "mark_sent": False,
        }
        values.update(overrides)
        return Namespace(**values)

    def test_builds_message_for_unsent_notify_events_and_marks_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(
                root / "runtime" / "intraday" / "2026-05-26" / "events.jsonl",
                [
                    {
                        "event_id": "evt-1",
                        "date": "2026-05-26",
                        "symbol": "MU",
                        "state": "near_trigger",
                        "previous_state": "waiting",
                        "reason": "price is near trigger",
                        "bar_timestamp": "2026-05-26 10:30:00",
                        "notify": True,
                    },
                    {
                        "event_id": "evt-2",
                        "date": "2026-05-26",
                        "symbol": "ORCL",
                        "state": "waiting",
                        "notify": False,
                    },
                ],
            )

            result = intraday_event_notify.run(self.args(root, mark_sent=True))

            self.assertTrue(result["should_send"])
            self.assertEqual(result["summary"]["unsent_events"], 1)
            message = Path(result["message_output"]).read_text(encoding="utf-8")
            self.assertIn("盘中监控事件", message)
            self.assertIn("MU", message)
            sent = json.loads((root / "runtime" / "intraday" / "2026-05-26" / "sent-events.json").read_text(encoding="utf-8"))
            self.assertEqual(sent["sent_event_ids"], ["evt-1"])

    def test_skips_already_sent_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(
                root / "runtime" / "intraday" / "2026-05-26" / "events.jsonl",
                [{"event_id": "evt-1", "date": "2026-05-26", "symbol": "MU", "state": "near_trigger", "notify": True}],
            )
            state = root / "runtime" / "intraday" / "2026-05-26" / "sent-events.json"
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"sent_event_ids": ["evt-1"]}), encoding="utf-8")

            result = intraday_event_notify.run(self.args(root, mark_sent=True))

            self.assertFalse(result["should_send"])
            self.assertEqual(result["reason"], "no unsent notify events")


if __name__ == "__main__":
    unittest.main()
