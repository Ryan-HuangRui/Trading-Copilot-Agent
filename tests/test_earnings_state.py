import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from earnings_state import EarningsState


class EarningsStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.state = EarningsState(Path(self.temp.name) / "state.sqlite")

    def tearDown(self):
        self.state.close(); self.temp.cleanup()

    def enqueue(self, subject, dependencies=()):
        return self.state.enqueue_task(task_type="company", subject_id=subject, period_start="2026-01-01", period_end="2026-03-31",
            input_hash=f"hash-{subject}", method_version="v1", source_mode="fixture", profile="daily",
            model="gpt-5.6-sol", effort="medium", dependencies=dependencies)[0]

    def test_task_key_deduplicates_unchanged_input(self):
        first, created = self.state.enqueue_task(task_type="company", subject_id="event-a", period_start=None, period_end="2026-03-31",
            input_hash="same", method_version="v1", source_mode="fixture", profile="daily", model="gpt-5.6-sol", effort="medium")
        second, created_again = self.state.enqueue_task(task_type="company", subject_id="event-a", period_start=None, period_end="2026-03-31",
            input_hash="same", method_version="v1", source_mode="fixture", profile="daily", model="gpt-5.6-sol", effort="medium")
        self.assertEqual(first, second); self.assertTrue(created); self.assertFalse(created_again)

    def test_dependency_blocks_until_completed(self):
        parent = self.enqueue("parent")
        child = self.enqueue("child", [parent])
        self.assertEqual([row["task_id"] for row in self.state.claim_tasks(owner="x", limit=5, lease_seconds=60)], [parent])
        self.state.complete_task(parent, "completion.json")
        self.assertEqual(self.state.claim_task(child, owner="x", lease_seconds=60)["task_id"], child)

    def test_live_lease_not_reentered_but_expired_lease_recovers(self):
        task = self.enqueue("lease")
        claimed = self.state.claim_task(task, owner="worker-1", lease_seconds=60)
        self.assertIsNotNone(claimed)
        self.assertIsNone(self.state.claim_task(task, owner="worker-2", lease_seconds=60))
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
        self.state.db.execute("UPDATE research_tasks SET lease_expires_at=? WHERE task_id=?", (expired, task))
        recovered = self.state.claim_task(task, owner="worker-2", lease_seconds=60)
        self.assertEqual(recovered["lease_owner"], "worker-2")

    def test_source_watermarks_are_independent(self):
        self.state.set_watermark("sec", "NVDA", "a", "success")
        self.state.set_watermark("issuer_ir", "NVDA", None, "unavailable", "timeout")
        rows = self.state.status()["watermarks"]
        self.assertEqual({(row["source"], row["status"]) for row in rows}, {("sec", "success"), ("issuer_ir", "unavailable")})

    def test_source_failure_is_persisted_separately_from_empty_result(self):
        failure_id = self.state.record_failure("sec", "NVDA", "submissions", "timeout", True)
        self.assertTrue(failure_id.startswith("failure-"))
        self.assertEqual(self.state.status()["unresolved_source_failures"], 1)
        self.assertEqual(self.state.resolve_failures("sec", "NVDA", "submissions"), 1)
        self.assertEqual(self.state.status()["unresolved_source_failures"], 0)

    def test_source_discovery_persists_backlog_and_retry_state(self):
        self.assertTrue(self.state.discover_source_item("sec_filing", "NVDA", "accn-1", {"form": "10-Q"}))
        self.assertFalse(self.state.discover_source_item("sec_filing", "NVDA", "accn-1", {"form": "10-Q"}))
        self.assertEqual(self.state.pending_source_items("sec_filing", "NVDA", 10)[0]["item_key"], "accn-1")
        self.state.fail_source_item("sec_filing", "NVDA", "accn-1", "timeout", True, 3)
        self.state.db.execute("UPDATE source_items SET next_attempt_at=NULL WHERE item_key='accn-1'")
        self.assertEqual(len(self.state.pending_source_items("sec_filing", "NVDA", 10)), 1)
        self.state.finish_source_item("sec_filing", "NVDA", "accn-1", digest="abc")
        self.assertEqual(self.state.status()["source_items"], {"fetched": 1})

    def test_max_attempt_expired_lease_becomes_terminal(self):
        task = self.enqueue("max-attempt")
        self.state.db.execute("UPDATE research_tasks SET max_attempts=1 WHERE task_id=?", (task,))
        self.state.claim_task(task, owner="worker", lease_seconds=60)
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
        self.state.db.execute("UPDATE research_tasks SET lease_expires_at=? WHERE task_id=?", (expired, task))
        self.assertEqual(self.state.status()["queue"]["terminal_failed"], 1)
        self.assertIsNone(self.state.claim_task(task, owner="other", lease_seconds=60))

    def test_attempt_manifest_is_immutable(self):
        task = self.enqueue("manifest")
        self.state.register_attempt_manifest(task, 1, "runtime/earnings/runs/a/input.json", "hash")
        self.state.register_attempt_manifest(task, 1, "runtime/earnings/runs/a/input.json", "hash")
        with self.assertRaisesRegex(ValueError, "cannot be replaced"):
            self.state.register_attempt_manifest(task, 1, "runtime/earnings/runs/b/input.json", "other")

    def test_task_input_versions_are_frozen(self):
        task = self.enqueue("frozen")
        payload = {"documents": [{"document_id": "d", "version": 1, "content_sha256": "a" * 64}]}
        self.state.freeze_task_input(task, payload, "hash-frozen")
        self.assertEqual(self.state.task_input(task)["documents"][0]["version"], 1)
        with self.assertRaisesRegex(ValueError, "does not match task|cannot be replaced"):
            self.state.freeze_task_input(task, {"documents": [{"document_id": "d", "version": 2}]}, "hash-new")


if __name__ == "__main__":
    unittest.main()
