import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from earnings_common import atomic_write_json, sha256_file
from earnings_recovery import recover
from earnings_period_review import QuarterlyReviewLedger
from earnings_state import EarningsState


class EarningsRecoveryTests(unittest.TestCase):
    def test_completed_publication_reconciliation_is_zero_model_and_idempotent(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'config').mkdir(parents=True)
            config = json.loads((ROOT / 'config/earnings_research.json').read_text())
            config['delivery']['lark_documents_enabled'] = True
            atomic_write_json(root / 'config/earnings_research.json', config)
            state = EarningsState(root / 'runtime/earnings/state.sqlite')
            now = '2026-09-21T00:00:00Z'
            state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
              scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ('job', 'series', 'source.json', 'sha', 'industry',
              'managed-care', '2026-Q2', 'stage', 1, 'complete', 'input.json', 'publication.json', None, 2, now, now))
            state.close(); QuarterlyReviewLedger(root / 'runtime/earnings/quarterly.sqlite').close()
            preview_value = {'eligible': True, 'planned_stages': ['publication', 'checker', 'cloud']}
            applied_value = {'eligible': True, 'planned_stages': ['publication', 'checker', 'cloud'],
                             'applied_stages': ['publication', 'checker', 'cloud'], 'finalized': True}
            with patch('earnings_recovery.reconcile_quarterly_publication', side_effect=[preview_value]) as reconcile:
                preview = recover(root, action='reconcile-publication', job_id='job')
            self.assertEqual((preview['model_calls'], preview['cloud_operations'], preview['executed']), (0, 0, False))
            with patch('earnings_recovery.reconcile_quarterly_publication', side_effect=[preview_value, applied_value]):
                result = recover(root, action='reconcile-publication', job_id='job', execute=True)
            self.assertEqual(result['reconciliation']['applied_stages'], ['publication', 'checker', 'cloud'])
            with patch('earnings_recovery.reconcile_quarterly_publication', side_effect=[
                    {'eligible': True, 'planned_stages': []},
                    {'eligible': True, 'planned_stages': [], 'applied_stages': [], 'finalized': True}]):
                repeated = recover(root, action='reconcile-publication', job_id='job', execute=True)
            self.assertEqual(repeated['reconciliation']['applied_stages'], [])
            state = EarningsState(root / 'runtime/earnings/state.sqlite')
            self.assertEqual(tuple(state.db.execute('SELECT state,attempts FROM publication_jobs').fetchone()),
                             ('complete', 2))
            state.close()

    def test_terminal_publication_can_be_deterministically_rechecked_without_resetting_attempts(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            manifest = root / "runtime/earnings/publications/runs/job/repair-attempt-1/input-manifest.json"
            atomic_write_json(manifest, {"input_manifest_hash": "frozen"})
            now = "2026-09-20T00:00:00Z"
            state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
              scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("managed-care", "series", "report/source.json", "hash", "industry",
              "managed-care", "2026-Q2", "stage", 1, "terminal_failed", str(manifest.relative_to(root)), None,
              "deterministic unit mismatch", 2, now, now))
            state.close()
            preview = recover(root, action="recheck-publication", job_id="managed-care")
            self.assertEqual(preview["planned"], {"state": "cloud_pending", "attempts": 2,
                "input_manifest_path": str(manifest.relative_to(root)), "model_calls": 0})
            with patch("earnings_recovery.recheck_publication", return_value={"status": "success",
                    "manifest_path": "report/earnings/publications/industry/managed-care/2026-Q2/v1/publication-manifest.json"}) as recheck:
                result = recover(root, action="recheck-publication", job_id="managed-care", execute=True)
            self.assertEqual(result["status"], "success")
            recheck.assert_called_once_with(root, manifest)
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            row = state.db.execute("SELECT state,attempts,publication_manifest_path FROM publication_jobs").fetchone()
            self.assertEqual(tuple(row), ("cloud_pending", 2,
                "report/earnings/publications/industry/managed-care/2026-Q2/v1/publication-manifest.json"))
            state.close()

    def test_dependency_reuse_is_preview_first_backed_up_and_one_shot(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            tasks = []
            for input_hash in ("old", "failed"):
                task, _ = state.enqueue_task(task_type="company", subject_id="amat", period_start="2026-04-01",
                    period_end="2026-06-30", input_hash=input_hash, method_version="v1", source_mode="live",
                    profile="daily", model="gpt-5.6-sol", effort="medium")
                state.freeze_task_input(task, {"documents": [{"document_id": "amat", "version": 1,
                    "content_sha256": "a" * 64}], "configuration_hash": "same"}, input_hash)
                tasks.append(task)
            state.db.execute("UPDATE research_tasks SET state='completed',output_manifest='old.json' WHERE task_id=?", (tasks[0],))
            report = root / "report/earnings/amat.json"
            atomic_write_json(report, {"report_id": "amat-report", "task_id": tasks[0]})
            state.db.execute("INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("amat-report", tasks[0], "company", "amat", "2026-04-01", "2026-06-30",
                 str(report.relative_to(root)), sha256_file(report), "manifest", "live", "partial", "now"))
            state.db.execute("UPDATE research_tasks SET state='terminal_failed',attempts=2 WHERE task_id=?", (tasks[1],))
            state.close()
            preview = recover(root, action="reuse-dependency", task_id=tasks[1], reuse_task_id=tasks[0],
                              reason="equivalent AMAT source and method")
            self.assertTrue(preview["planned"]["eligible"])
            result = recover(root, action="reuse-dependency", task_id=tasks[1], reuse_task_id=tasks[0],
                             reason="equivalent AMAT source and method", execute=True)
            self.assertEqual(result["status"], "success")
            self.assertTrue((root / result["backup_path"]).exists())
            with self.assertRaisesRegex(ValueError, "already executed"):
                recover(root, action="reuse-dependency", task_id=tasks[1], reuse_task_id=tasks[0],
                        reason="equivalent AMAT source and method", execute=True)

    def test_release_expired_task_mutates_only_selected_lease(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            task_ids = []
            for subject in ("one", "two"):
                task_id, _ = state.enqueue_task(task_type="company", subject_id=subject, period_start=None,
                    period_end="2026-06-30", input_hash=subject, method_version="v1", source_mode="live",
                    profile="daily", model="gpt-5.6-sol", effort="medium")
                state.claim_task(task_id, owner=subject, lease_seconds=60)
                task_ids.append(task_id)
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            state.db.execute("UPDATE research_tasks SET lease_expires_at=? WHERE task_id IN (?,?)",
                             (expired, *task_ids))
            state.close()
            preview = recover(root, action="release-expired-task", task_id=task_ids[0])
            self.assertEqual(preview["planned"]["state"], "retryable_failed")
            recover(root, action="release-expired-task", task_id=task_ids[0], execute=True)
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            rows = {row["task_id"]: row["state"] for row in state.db.execute("SELECT task_id,state FROM research_tasks")}
            self.assertEqual(rows[task_ids[0]], "retryable_failed")
            self.assertEqual(rows[task_ids[1]], "running")
            state.close()

    def test_dependency_exclusion_is_preview_first_backed_up_and_blocks_older_head(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            old, _ = state.enqueue_task(task_type="company", subject_id="amat", period_start=None, period_end="2026-07-26",
                input_hash="old", method_version="v1", source_mode="live", profile="daily", model="m", effort="e")
            state.db.execute("UPDATE research_tasks SET state='completed',output_manifest='old' WHERE task_id=?", (old,))
            failed, _ = state.enqueue_task(task_type="company", subject_id="amat", period_start=None, period_end="2026-07-26",
                input_hash="new", method_version="v1", source_mode="live", profile="daily", model="m", effort="e")
            state.db.execute("UPDATE research_tasks SET state='terminal_failed',attempts=2,error='original' WHERE task_id=?", (failed,))
            child, _ = state.enqueue_task(task_type="industry", subject_id="semi", period_start="2026-04-01",
                period_end="2026-06-30", input_hash="industry", method_version="v1", source_mode="live",
                profile="quarterly", model="q", effort="h", dependencies=[old])
            state.close()
            preview = recover(root, action="exclude-dependency", task_id=failed, reason="legacy proof unavailable")
            self.assertEqual((preview["status"], preview["planned"]["failed_before"]["error"]), ("preview", "original"))
            result = recover(root, action="exclude-dependency", task_id=failed,
                             reason="legacy proof unavailable", execute=True)
            self.assertTrue((root / result["backup_path"]).is_file())
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            self.assertEqual(state.db.execute("SELECT state FROM research_tasks WHERE task_id=?", (failed,)).fetchone()[0],
                             "excluded")
            self.assertIsNone(state.claim_task(child, owner="limited", lease_seconds=60))
            state.close()

    def test_dependency_exclusion_refuses_running_frozen_quarterly_scope(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            old, _ = state.enqueue_task(task_type="company", subject_id="event", period_start=None,
                period_end="2026-07-26", input_hash="old", method_version="v1", source_mode="live",
                profile="daily", model="m", effort="e")
            state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (old,))
            report = root / "report/earnings/old.json"; atomic_write_json(report, {"report_id": "old"})
            state.db.execute("INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("old", old, "company",
                "event", None, "2026-07-26", str(report.relative_to(root)), sha256_file(report), "m", "live",
                "partial", "2026-08-01T00:00:00Z"))
            failed, _ = state.enqueue_task(task_type="company", subject_id="event", period_start=None,
                period_end="2026-07-26", input_hash="new", method_version="v1", source_mode="live",
                profile="daily", model="m", effort="e")
            state.db.execute("UPDATE research_tasks SET state='terminal_failed' WHERE task_id=?", (failed,)); state.close()
            ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
            scope = ledger.freeze({"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"},
                {"industry_id": "semi", "issuers": [], "key_symbols": []}, "2026-08-01T00:00:00Z", edition="stage")
            ledger.begin_revision(scope["scope_id"], "old-boundary", scope["cutoff"], accepted_reports=[{
                "issuer_id": "issuer", "report_id": "old", "task_id": old,
                "path": str(report.relative_to(root)), "sha256": sha256_file(report)}])
            ledger.set_stage(scope["scope_id"], "industry", "running"); ledger.close()
            with self.assertRaisesRegex(ValueError, "running_quarterly_scope"):
                recover(root, action="exclude-dependency", task_id=failed, reason="cannot prove revision")
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            self.assertEqual(state.db.execute("SELECT state FROM research_tasks WHERE task_id=?", (failed,)).fetchone()[0],
                             "terminal_failed")
            state.close()

    def test_exact_checker_recovery_previews_then_backs_up_and_executes_once(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            manifest = root / "runtime/earnings/publications/runs/job/input-manifest.json"
            atomic_write_json(manifest, {"permitted_outputs": {"runner_result":
                "runtime/earnings/publications/runs/job/runner-result.json"}})
            atomic_write_json(manifest.parent / "writer-stage.json", {"status": "completed"})
            now = "2026-09-16T00:00:00Z"
            state.db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
              scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,attempts,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("job", "series", "report/source.json", "hash", "company",
              "TEST", "2026-Q2", "stage", 1, "terminal_failed", str(manifest.relative_to(root)), None,
              "legacy timeout", 2, now, now))
            state.close()
            preview = recover(root, action="resume-checker", job_id="job")
            self.assertEqual((preview["status"], preview["executed"], preview["model_calls"]), ("preview", False, 0))
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            self.assertEqual(state.db.execute("SELECT state FROM publication_jobs").fetchone()[0], "terminal_failed")
            state.close()
            result = recover(root, action="resume-checker", job_id="job", execute=True)
            self.assertEqual(result["status"], "success")
            self.assertTrue((root / result["backup_path"]).exists())
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            self.assertEqual(tuple(state.db.execute("SELECT state,attempts FROM publication_jobs").fetchone()),
                             ("checker_pending", 1))
            state.close()
            with self.assertRaisesRegex(ValueError, "already executed"):
                recover(root, action="resume-checker", job_id="job", execute=True)


if __name__ == "__main__":
    unittest.main()
