import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from earnings_common import atomic_write_json
from earnings_recovery import recover
from earnings_state import EarningsState


class EarningsRecoveryTests(unittest.TestCase):
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
