import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from earnings_common import atomic_write_json, classify_model_failure, company_research_configuration_hash, sha256_file
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

    def test_p4_tables_are_added_without_replacing_p3_state(self):
        tables = {row[0] for row in self.state.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("research_tasks", tables)
        self.assertIn("publication_artifacts", tables)
        self.assertIn("publication_delivery", tables)

    def test_task_key_deduplicates_unchanged_input(self):
        first, created = self.state.enqueue_task(task_type="company", subject_id="event-a", period_start=None, period_end="2026-03-31",
            input_hash="same", method_version="v1", source_mode="fixture", profile="daily", model="gpt-5.6-sol", effort="medium")
        second, created_again = self.state.enqueue_task(task_type="company", subject_id="event-a", period_start=None, period_end="2026-03-31",
            input_hash="same", method_version="v1", source_mode="fixture", profile="daily", model="gpt-5.6-sol", effort="medium")
        self.assertEqual(first, second); self.assertTrue(created); self.assertFalse(created_again)

    def test_delivery_and_publication_tuning_does_not_change_company_research_hash(self):
        config = {"schema_version": 1, "sources": {"primary": ["sec"]},
                  "profiles": {"daily": {"model": "gpt-5.6-sol", "reasoning_effort": "medium"}},
                  "budgets": {"max_task_attempts": 2, "initialization_lookback_quarters": 8,
                              "publication_stage_timeout_seconds": 300},
                  "delivery": {"enabled": False}}
        first = company_research_configuration_hash(config)
        config["budgets"]["publication_stage_timeout_seconds"] = 900
        config["delivery"]["enabled"] = True
        self.assertEqual(first, company_research_configuration_hash(config))
        config["profiles"]["daily"]["reasoning_effort"] = "high"
        self.assertNotEqual(first, company_research_configuration_hash(config))

    def test_quota_and_capacity_are_distinct_and_timeout_is_neither(self):
        self.assertEqual(classify_model_failure("Codex usage limit has been reached"), "quota_exhausted")
        self.assertEqual(classify_model_failure("model_quota_exhausted: backend stopped"), "quota_exhausted")
        self.assertEqual(classify_model_failure("server capacity temporarily unavailable"), "capacity_unavailable")
        self.assertEqual(classify_model_failure("model_capacity_unavailable: retry"), "capacity_unavailable")
        self.assertIsNone(classify_model_failure("process timed out after 300 seconds"))

    def test_dependency_blocks_until_completed(self):
        parent = self.enqueue("parent")
        child = self.enqueue("child", [parent])
        self.assertEqual([row["task_id"] for row in self.state.claim_tasks(owner="x", limit=5, lease_seconds=60)], [parent])
        self.state.complete_task(parent, "completion.json")
        self.assertEqual(self.state.claim_task(child, owner="x", lease_seconds=60)["task_id"], child)

    def test_superseded_completed_dependency_blocks_child_before_model_claim(self):
        parent = self.enqueue("parent")
        child = self.enqueue("child", [parent])
        self.state.complete_task(parent, "completion.json")
        newer, _ = self.state.enqueue_task(task_type="company", subject_id="parent",
            period_start="2026-01-01", period_end="2026-03-31", input_hash="new-hash",
            method_version="v1", source_mode="fixture", profile="daily", model="gpt-5.6-sol", effort="medium")
        claimed = self.state.claim_tasks(owner="x", limit=5, lease_seconds=60)
        self.assertIn(newer, [row["task_id"] for row in claimed])
        self.assertNotIn(child, [row["task_id"] for row in claimed])
        self.assertIsNone(self.state.claim_task(child, owner="x", lease_seconds=60))

    def test_failed_dependency_reuse_requires_exact_frozen_semantics_and_is_audited(self):
        old, _ = self.state.enqueue_task(task_type="company", subject_id="amat", period_start="2026-04-01",
            period_end="2026-06-30", input_hash="same", method_version="v1", source_mode="live",
            profile="daily", model="gpt-5.6-sol", effort="medium")
        self.state.freeze_task_input(old, {"documents": [{"document_id": "d", "version": 1,
            "content_sha256": "a" * 64}], "configuration_hash": "config"}, "same")
        self.state.db.execute("UPDATE research_tasks SET state='completed',output_manifest='old-output.json' WHERE task_id=?", (old,))
        report = Path(self.temp.name) / "report/earnings/amat.json"
        atomic_write_json(report, {"report_id": "amat-report", "task_id": old})
        self.state.db.execute("INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("amat-report", old, "company", "amat", "2026-04-01", "2026-06-30",
             str(report.relative_to(Path(self.temp.name))), sha256_file(report), "manifest", "live", "partial", "now"))
        failed, _ = self.state.enqueue_task(task_type="company", subject_id="amat", period_start="2026-04-01",
            period_end="2026-06-30", input_hash="same-new-task", method_version="v1", source_mode="live",
            profile="daily", model="gpt-5.6-sol", effort="medium")
        self.state.freeze_task_input(failed, {"documents": [{"document_id": "d", "version": 1,
            "content_sha256": "a" * 64}], "configuration_hash": "config"}, "same-new-task")
        self.state.db.execute("UPDATE research_tasks SET state='terminal_failed',attempts=2,error='AMAT failed' WHERE task_id=?", (failed,))
        preview = self.state.preview_dependency_reuse(failed, old)
        self.assertTrue(preview["eligible"])
        applied = self.state.apply_dependency_reuse(failed, old, reason="source/hash/config/method equivalent")
        self.assertEqual(applied["status"], "superseded")
        row = self.state.db.execute("SELECT state,attempts,error FROM research_tasks WHERE task_id=?", (failed,)).fetchone()
        self.assertEqual((row["state"], row["attempts"]), ("superseded", 2))
        self.assertIn(old, row["error"])
        audit = self.state.db.execute("SELECT proof_json FROM dependency_reuse_audit").fetchone()
        proof = __import__("json").loads(audit[0])
        self.assertEqual(proof["failed_before"]["error"], "AMAT failed")

    def test_single_claim_accepts_dependency_after_audited_reuse(self):
        self.test_failed_dependency_reuse_requires_exact_frozen_semantics_and_is_audited()
        old = self.state.db.execute("SELECT reused_task_id FROM dependency_reuse_audit").fetchone()[0]
        child, _ = self.state.enqueue_task(task_type="industry", subject_id="semiconductors",
            period_start="2026-04-01", period_end="2026-06-30", input_hash="industry",
            method_version="v1", source_mode="live", profile="quarterly", model="gpt-6-astra",
            effort="high", dependencies=[old])
        claimed = self.state.claim_task(child, owner="review", lease_seconds=60)
        self.assertEqual(claimed["task_id"], child)

    def test_bulk_claim_ignores_only_audited_reuse_tombstone(self):
        self.test_failed_dependency_reuse_requires_exact_frozen_semantics_and_is_audited()
        old = self.state.db.execute("SELECT reused_task_id FROM dependency_reuse_audit").fetchone()[0]
        child, _ = self.state.enqueue_task(task_type="industry", subject_id="display",
            period_start="2026-04-01", period_end="2026-06-30", input_hash="display",
            method_version="v1", source_mode="live", profile="quarterly", model="gpt-6-astra",
            effort="high", dependencies=[old])
        self.assertIn(child, [row["task_id"] for row in self.state.claim_tasks(owner="bulk", limit=20, lease_seconds=60)])

    def test_failed_dependency_reuse_rejects_revised_source_hash(self):
        old = self.enqueue("amat-old")
        failed = self.enqueue("amat-failed")
        self.state.freeze_task_input(old, {"documents": [{"document_id": "d", "version": 1,
            "content_sha256": "a" * 64}], "configuration_hash": "config"}, "hash-amat-old")
        self.state.freeze_task_input(failed, {"documents": [{"document_id": "d", "version": 2,
            "content_sha256": "b" * 64}], "configuration_hash": "config"}, "hash-amat-failed")
        self.state.db.execute("UPDATE research_tasks SET state='completed',output_manifest='old-output.json' WHERE task_id=?", (old,))
        self.state.db.execute("UPDATE research_tasks SET state='terminal_failed' WHERE task_id=?", (failed,))
        preview = self.state.preview_dependency_reuse(failed, old)
        self.assertFalse(preview["eligible"])
        self.assertIn("subject", preview["differences"])
        self.assertIn("documents", preview["differences"])
        with self.assertRaisesRegex(ValueError, "not equivalent"):
            self.state.apply_dependency_reuse(failed, old, reason="must not fall back")

    def test_legacy_config_hashes_require_two_matching_immutable_semantic_proofs(self):
        root = Path(self.temp.name); history = root / "runtime/earnings/config-history"; history.mkdir(parents=True)
        base = {"schema_version": 1, "sources": {"primary": ["sec"]},
                "profiles": {"daily": {"model": "m", "reasoning_effort": "e"}},
                "budgets": {"max_task_attempts": 2, "initialization_lookback_quarters": 8}}
        paths = [history / "one.json", history / "two.json"]
        paths[0].write_text(__import__("json").dumps(base, sort_keys=True), encoding="utf-8")
        paths[1].write_text(__import__("json").dumps(base, sort_keys=True, indent=2), encoding="utf-8")
        hashes = [sha256_file(path) for path in paths]
        for path, digest in zip(paths, hashes): path.rename(history / f"{digest}.json")
        tasks = []
        for index, digest in enumerate(hashes):
            task, _ = self.state.enqueue_task(task_type="company", subject_id="legacy", period_start=None,
                period_end="2026-06-30", input_hash=f"legacy-{index}", method_version="v1", source_mode="live",
                profile="daily", model="m", effort="e")
            self.state.freeze_task_input(task, {"documents": [], "configuration_hash": digest}, f"legacy-{index}")
            tasks.append(task)
        self.state.db.execute("UPDATE research_tasks SET state='completed',output_manifest='old' WHERE task_id=?", (tasks[1],))
        report = root / "report/earnings/legacy.json"; atomic_write_json(report, {"report_id": "legacy"})
        self.state.db.execute("INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy", tasks[1], "company", "legacy", None, "2026-06-30", str(report.relative_to(root)),
             sha256_file(report), "m", "live", "partial", "now"))
        self.state.db.execute("UPDATE research_tasks SET state='terminal_failed' WHERE task_id=?", (tasks[0],))
        proof = self.state.preview_dependency_reuse(tasks[0], tasks[1])
        self.assertTrue(proof["eligible"]); self.assertTrue(proof["configuration_proofs"]["equivalent"])

    def test_explicit_dependency_exclusion_blocks_older_head_and_preserves_failure(self):
        old = self.enqueue("excluded")
        self.state.complete_task(old, "old.json")
        failed, _ = self.state.enqueue_task(task_type="company", subject_id="excluded", period_start="2026-01-01",
            period_end="2026-03-31", input_hash="new", method_version="v1", source_mode="fixture", profile="daily",
            model="gpt-5.6-sol", effort="medium")
        self.state.db.execute("UPDATE research_tasks SET state='terminal_failed',attempts=2,error='original' WHERE task_id=?", (failed,))
        child = self.enqueue("child-excluded", [old])
        self.assertIsNone(self.state.claim_task(child, owner="before", lease_seconds=60))
        applied = self.state.apply_dependency_exclusion(failed, reason="semantic config proof unavailable")
        self.assertEqual(applied["failed_before"]["error"], "original")
        self.assertIsNone(self.state.claim_task(child, owner="after", lease_seconds=60))
        self.assertEqual(self.state.task_blocked_by_exclusion(old)["failed_task_id"], failed)

    def test_company_queue_round_robins_issuers_before_deeper_history(self):
        for issuer in ("issuer-a", "issuer-b"):
            self.state.upsert_issuer(issuer_id=issuer, cik=None, symbol=issuer[-1].upper(),
                                     name=issuer, identity_status="resolved")
            for index, period in enumerate(("2026-06-30", "2026-03-31", "2025-12-31")):
                event = f"{issuer}-event-{index}"
                self.state.refresh_event(event, issuer, "earnings", None, period)
                self.state.enqueue_task(task_type="company", subject_id=event, period_start=None, period_end=period,
                    input_hash=f"{event}-hash", method_version="v1", source_mode="live", profile="daily",
                    model="gpt-5.6-sol", effort="medium")
        claimed = self.state.claim_tasks(owner="x", limit=4, lease_seconds=60)
        periods = [(row["subject_id"].split("-event-")[0], row["period_end"]) for row in claimed]
        self.assertEqual([period for _, period in periods],
                         ["2026-06-30", "2026-06-30", "2026-03-31", "2026-03-31"])
        self.assertEqual(len({issuer for issuer, period in periods[:2]}), 2)

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
