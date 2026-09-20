import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from earnings_common import atomic_write_json
from earnings_continuation import ContinuationLedger, worker
from earnings_daily import DailyLedger, _publication_job_within_cutoff, industry_work
from earnings_state import EarningsState


def deployment(root: Path, **overrides) -> None:
    payload = {"schema_version": 1, "verified_repo": str(root), "project": "test", "session": "test",
        "verified_at": "test", "verified_from_cron_id": "test", "cc_connect_bin": "/bin/false",
        "codex_bin": "/bin/false", "delivery_enabled": False,
        "continuation_worker_seconds": 300, "continuation_window_seconds": 60,
        "continuation_finalize_reserve_seconds": 120, "continuation_no_progress_windows": 2}
    payload.update(overrides)
    atomic_write_json(root / "runtime/earnings/deployment.json", payload)


class EarningsContinuationTests(unittest.TestCase):
    def prepare(self, root: Path) -> tuple[dict, argparse.Namespace]:
        (root / "config").mkdir(parents=True)
        config = json.loads((ROOT / "config/earnings_research.json").read_text())
        atomic_write_json(root / "config/earnings_research.json", config)
        atomic_write_json(root / "config/earnings_universe.json", {"industries": []})
        deployment(root)
        ledger = ContinuationLedger(root)
        round_row = ledger.create_round(datetime(2026, 9, 20, 2, tzinfo=timezone.utc))
        ledger.db.execute("INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?)",
            (f"{round_row['round_id']}:g1:123", round_row["round_id"], 1, 123, "running",
             "2026-09-20T02:00:00Z", None, None, None))
        ledger.db.commit(); ledger.close()
        args = argparse.Namespace(config="config/earnings_research.json",
            deployment="runtime/earnings/deployment.json", send=False, round_id=round_row["round_id"], generation=1)
        return round_row, args

    def test_soft_quotas_reset_for_each_execution_window(self):
        with TemporaryDirectory() as temp:
            ledger = DailyLedger(Path(temp).resolve())
            ledger.bind_quota_scope("round-1-window-1")
            self.assertTrue(ledger.reserve("2026-09-20", "company-a", "company", 1))
            self.assertFalse(ledger.reserve("2026-09-20", "company-b", "company", 1))
            ledger.bind_quota_scope("round-1-window-2")
            self.assertTrue(ledger.reserve("2026-09-20", "company-b", "company", 1))
            self.assertEqual(ledger.used("2026-09-20", "company"), 1)
            ledger.db.close()

    def test_more_current_disclosures_than_old_daily_cap_advance_in_same_round(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            ledger = DailyLedger(root); cutoff = "2026-09-20T02:00:00Z"
            for index in range(12):
                issuer = f"issuer-{index}"; event = f"event-{index}"
                state.upsert_issuer(issuer_id=issuer, cik=str(index), symbol=f"T{index:02d}",
                                    name=issuer, identity_status="resolved")
                state.refresh_event(event, issuer, "earnings", None, "2026-09-01")
                original = root / f"raw_data/earnings/{event}.txt"; original.parent.mkdir(parents=True, exist_ok=True)
                original.write_text(event)
                state.register_document({"document_id": event, "issuer_id": issuer, "event_id": event,
                    "form": "10-Q", "source_type": "sec_filing", "source_url": f"https://example.com/{event}",
                    "provider": "sec", "backend": "test", "reporting_start": "2026-06-01",
                    "reporting_end": "2026-09-01", "published_at": "2026-09-10T00:00:00Z",
                    "accepted_at": "2026-09-10T00:00:00Z", "fetched_at": "2026-09-10T00:00:00Z",
                    "public_time_precision": "second", "original_path": str(original.relative_to(root)),
                    "content_sha256": __import__("hashlib").sha256(event.encode()).hexdigest(),
                    "source_mode": "live", "metadata_json": "{}"})
                state.enqueue_task(task_type="company", subject_id=event, period_start=None,
                    period_end="2026-09-01", input_hash=event, method_version="v1", source_mode="live",
                    profile="daily", model="gpt-5.6-sol", effort="medium")
            ledger.bind_quota_scope("round-window-1")
            first = state.claim_tasks(owner="w1", limit=10, lease_seconds=60, task_type="company",
                                      company_tier="current", cutoff=cutoff)
            for task in first:
                self.assertTrue(ledger.reserve(cutoff[:10], task["task_id"], "company", 10))
                state.db.execute("UPDATE research_tasks SET state='completed',lease_owner=NULL,lease_expires_at=NULL WHERE task_id=?",
                                 (task["task_id"],))
            state.db.commit(); ledger.bind_quota_scope("round-window-2")
            second = state.claim_tasks(owner="w2", limit=10, lease_seconds=60, task_type="company",
                                       company_tier="current", cutoff=cutoff)
            self.assertEqual((len(first), len(second)), (10, 2))
            self.assertEqual(state.db.execute("SELECT MAX(attempts) FROM research_tasks").fetchone()[0], 1)
            state.close(); ledger.db.close()

    def test_progressing_worker_hands_off_beyond_one_outer_window(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            before = {"fingerprint": "before", "actionable_count": 2, "blocker_count": 0,
                      "pending": {"current_company": 2}, "blockers": {}}
            after = {**before, "fingerprint": "after", "actionable_count": 1}
            daily = {"status": "success", "errors": [], "progress": after,
                     "usage_summary": {"actual_model_calls": 1}}
            # One bounded generation runs a window, then exhausts its own session and
            # starts a successor instead of treating the cron duration as completion.
            with patch("earnings_continuation.round_progress", return_value=before), \
                 patch("earnings_continuation._run_daily", return_value=daily) as run_daily, \
                 patch("earnings_continuation.spawn_worker", return_value=999) as spawn, \
                 patch("earnings_continuation.time.monotonic", side_effect=[0, 0, 0, 241]):
                result = worker(root, args)
            self.assertEqual((result["state"], result["successor_pid"]), ("active", 999))
            self.assertEqual(run_daily.call_count, 1); spawn.assert_called_once()
            ledger = ContinuationLedger(root)
            row = ledger.db.execute("SELECT state,stop_reason FROM rounds WHERE round_id=?",
                                    (round_row["round_id"],)).fetchone()
            self.assertEqual(row["state"], "active")
            self.assertIn("handed off", row["stop_reason"])
            ledger.close()

    def test_no_progress_yields_without_spawning_or_repeating_models(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            deployment(root, continuation_no_progress_windows=1)
            snapshot = {"fingerprint": "same", "actionable_count": 3, "blocker_count": 0,
                        "pending": {"current_company": 3}, "blockers": {}}
            daily = {"status": "success", "errors": [], "progress": snapshot,
                     "usage_summary": {"actual_model_calls": 0}}
            final = {"status": "success", "errors": [], "progress": snapshot,
                     "usage_summary": {"actual_model_calls": 0}}
            with patch("earnings_continuation.round_progress", return_value=snapshot), \
                 patch("earnings_continuation._run_daily", side_effect=[daily, final]) as run_daily, \
                 patch("earnings_continuation.spawn_worker") as spawn:
                result = worker(root, args)
            self.assertEqual(result["state"], "yielded")
            self.assertEqual(run_daily.call_count, 2)  # one work window plus finalizer only
            spawn.assert_not_called()

    def test_quota_pauses_round_without_marking_target_complete(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            snapshot = {"fingerprint": "same", "actionable_count": 4, "blocker_count": 0,
                        "pending": {"current_company": 4}, "blockers": {}}
            quota = {"status": "failed", "model_circuit_open": True,
                     "errors": ["model-circuit:quota exhausted"], "progress": snapshot,
                     "usage_summary": {"actual_model_calls": 1}}
            final = {"status": "success", "errors": [], "progress": snapshot,
                     "usage_summary": {"actual_model_calls": 0}}
            with patch("earnings_continuation.round_progress", return_value=snapshot), \
                 patch("earnings_continuation._run_daily", side_effect=[quota, final]), \
                 patch("earnings_continuation.spawn_worker") as spawn:
                result = worker(root, args)
            self.assertEqual(result["state"], "paused_quota"); spawn.assert_not_called()
            ledger = ContinuationLedger(root)
            active = ledger.active_round()
            self.assertEqual((active["round_id"], active["state"]),
                             (round_row["round_id"], "paused_quota"))
            ledger.close()

    def test_empty_frozen_target_completes_and_finalizes_once(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); _round, args = self.prepare(root)
            empty = {"fingerprint": "empty", "actionable_count": 0, "blocker_count": 0,
                     "pending": {}, "blockers": {}}
            work = {"status": "success", "errors": [], "progress": empty,
                    "collection_complete": True, "usage_summary": {"actual_model_calls": 0}}
            final = {"status": "success", "errors": [], "progress": empty,
                     "usage_summary": {"actual_model_calls": 0}}
            with patch("earnings_continuation.round_progress", return_value=empty), \
                 patch("earnings_continuation._run_daily", side_effect=[work, final]) as run_daily, \
                 patch("earnings_continuation.spawn_worker") as spawn:
                result = worker(root, args)
            self.assertEqual(result["state"], "complete")
            self.assertEqual(run_daily.call_count, 2); spawn.assert_not_called()

    def test_cutoff_claim_ignores_late_disclosure_and_keeps_attempts(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            state.upsert_issuer(issuer_id="issuer", cik="1", symbol="TEST", name="Test", identity_status="resolved")
            for event, end, public in (("old", "2026-06-30", "2026-08-01T00:00:00+00:00"),
                                       ("late", "2026-09-30", "2026-09-21T00:00:00+00:00")):
                state.refresh_event(event, "issuer", "earnings", None, end)
                original = root / f"raw_data/earnings/{event}.txt"; original.parent.mkdir(parents=True, exist_ok=True)
                original.write_text(event)
                state.register_document({"document_id": event, "issuer_id": "issuer", "event_id": event,
                    "form": "10-Q", "source_type": "sec_filing", "source_url": f"https://example.com/{event}",
                    "provider": "sec", "backend": "test", "reporting_start": "2026-04-01",
                    "reporting_end": end, "published_at": public, "accepted_at": public, "fetched_at": public,
                    "public_time_precision": "second", "original_path": str(original.relative_to(root)),
                    "content_sha256": __import__("hashlib").sha256(event.encode()).hexdigest(),
                    "source_mode": "live", "metadata_json": "{}"})
                state.enqueue_task(task_type="company", subject_id=event, period_start=None, period_end=end,
                    input_hash=event, method_version="v1", source_mode="live", profile="daily",
                    model="gpt-5.6-sol", effort="medium")
            claimed = state.claim_tasks(owner="round", limit=2, lease_seconds=60, task_type="company",
                company_tier="current", cutoff="2026-09-20T02:00:00+00:00")
            self.assertEqual([row["subject_id"] for row in claimed], ["old"])
            late = state.db.execute("SELECT state,attempts FROM research_tasks WHERE subject_id='late'").fetchone()
            self.assertEqual(tuple(late), ("queued", 0))
            state.close()

    def test_industry_and_publication_heads_stay_inside_frozen_cutoff(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            ledger = DailyLedger(root)
            state.upsert_issuer(issuer_id="issuer", cik="1", symbol="TEST", name="Test", identity_status="resolved")
            universe = {"industries": [{"industry_id": "industry", "issuers": [{"symbol": "TEST"}]}]}
            paths = []
            for index, (event, end, cutoff) in enumerate((("old", "2026-06-30", "2026-09-19T00:00:00Z"),
                                                          ("late", "2026-09-30", "2026-09-21T00:00:00Z")), 1):
                state.refresh_event(event, "issuer", "earnings", None, end)
                task, _ = state.enqueue_task(task_type="company", subject_id=event, period_start=None,
                    period_end=end, input_hash=event, method_version="v1", source_mode="live", profile="daily",
                    model="gpt-5.6-sol", effort="medium")
                state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,))
                path = root / f"report/earnings/{event}.json"
                atomic_write_json(path, {"report_type": "company", "cutoff": cutoff,
                    "scope": {"issuer_id": "issuer", "symbol": "TEST", "reporting_end": end}})
                digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
                state.db.execute("INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (event, task, "company", event, None, end, str(path.relative_to(root)), digest,
                     "manifest", "live", "full", cutoff))
                paths.append((path, digest))
            state.db.commit()
            frozen = industry_work(root, state, universe, ledger, cutoff="2026-09-20T02:00:00Z")
            later = industry_work(root, state, universe, ledger, cutoff="2026-09-22T02:00:00Z")
            self.assertEqual((frozen[0]["end"], later[0]["end"]), ("2026-06-30", "2026-09-30"))
            self.assertTrue(_publication_job_within_cutoff(root, {"source_path": str(paths[0][0].relative_to(root))},
                                                           "2026-09-20T02:00:00Z"))
            self.assertFalse(_publication_job_within_cutoff(root, {"source_path": str(paths[1][0].relative_to(root))},
                                                            "2026-09-20T02:00:00Z"))
            state.close(); ledger.db.close()

    def test_terminal_latest_period_cannot_be_bypassed_by_claiming_history_as_current(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            state.upsert_issuer(issuer_id="issuer", cik="1", symbol="TEST", name="Test", identity_status="resolved")
            tasks = []
            for event, end in (("old", "2026-06-30"), ("latest", "2026-09-30")):
                state.refresh_event(event, "issuer", "earnings", None, end)
                original = root / f"raw_data/earnings/{event}.txt"; original.parent.mkdir(parents=True, exist_ok=True)
                original.write_text(event)
                state.register_document({"document_id": event, "issuer_id": "issuer", "event_id": event,
                    "form": "10-Q", "source_type": "sec_filing", "source_url": f"https://example.com/{event}",
                    "provider": "sec", "backend": "test", "reporting_start": "2026-04-01",
                    "reporting_end": end, "published_at": "2026-09-01T00:00:00Z",
                    "accepted_at": "2026-09-01T00:00:00Z", "fetched_at": "2026-09-01T00:00:00Z",
                    "public_time_precision": "second", "original_path": str(original.relative_to(root)),
                    "content_sha256": __import__("hashlib").sha256(event.encode()).hexdigest(),
                    "source_mode": "live", "metadata_json": "{}"})
                task, _ = state.enqueue_task(task_type="company", subject_id=event, period_start=None,
                    period_end=end, input_hash=event, method_version="v1", source_mode="live", profile="daily",
                    model="gpt-5.6-sol", effort="medium")
                tasks.append(task)
            state.db.execute("UPDATE research_tasks SET state='terminal_failed',attempts=max_attempts WHERE task_id=?",
                             (tasks[-1],)); state.db.commit()
            claimed = state.claim_tasks(owner="round", limit=1, lease_seconds=60, task_type="company",
                company_tier="current", cutoff="2026-09-20T02:00:00Z")
            self.assertEqual(claimed, [])
            state.close()


if __name__ == "__main__":
    unittest.main()
