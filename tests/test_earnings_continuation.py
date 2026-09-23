import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import subprocess
import sqlite3
import fcntl
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from earnings_common import atomic_write_json
import earnings_continuation
from earnings_continuation import ContinuationLedger, _run_daily, start, worker
from earnings_daily import DailyLedger, _publication_job_within_cutoff, industry_work, round_progress
from earnings_period_review import QuarterlyReviewLedger
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
    def test_worker_bootstraps_legacy_quarterly_schema_before_first_progress_read(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            qpath = root / "runtime/earnings/quarterly.sqlite"
            legacy = sqlite3.connect(qpath)
            legacy.executescript("""CREATE TABLE quarterly_scopes(scope_id TEXT PRIMARY KEY,quarter_id TEXT NOT NULL,
              industry_id TEXT NOT NULL,period_start TEXT NOT NULL,period_end TEXT NOT NULL,
              frozen_universe_json TEXT NOT NULL,frozen_universe_hash TEXT NOT NULL,cutoff TEXT NOT NULL,
              edition TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,
              input_fingerprint TEXT,UNIQUE(quarter_id,industry_id,edition));
              CREATE TABLE quarterly_stages(scope_id TEXT NOT NULL,stage TEXT NOT NULL,state TEXT NOT NULL,input_hash TEXT,
              artifact_path TEXT,artifact_sha256 TEXT,attempts INTEGER NOT NULL DEFAULT 0,error TEXT,updated_at TEXT NOT NULL,
              PRIMARY KEY(scope_id,stage));""")
            legacy.commit(); legacy.close()
            empty = {"fingerprint": "empty", "actionable_count": 0, "waiting_count": 0,
                     "blocker_count": 0, "pending": {}, "blockers": {}}
            work = {"status": "success", "errors": [], "progress": empty, "collection_complete": True,
                    "usage_summary": {"actual_model_calls": 0, "calls": []}}
            final = {**work, "delivery": {"delivery": {"state": "suppressed"}}}
            with patch("earnings_continuation._run_daily", side_effect=[work, final]) as run_daily:
                result = worker(root, args)
            columns = {row[1] for row in sqlite3.connect(qpath).execute("PRAGMA table_info(quarterly_scopes)")}
            self.assertEqual(result["state"], "complete")
            self.assertEqual(run_daily.call_count, 2)
            self.assertIn("accepted_reports_json", columns)
            self.assertTrue(list((root / "runtime/earnings/migrations").glob("quarterly-pre-goal-driven-*.sqlite.bak")))

    def test_start_preserves_delivery_pending_and_retries_only_finalizer(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            ledger = ContinuationLedger(root)
            ledger.db.execute("UPDATE rounds SET state='delivery_pending',research_outcome='blocked' WHERE round_id=?",
                              (round_row["round_id"],))
            ledger.db.execute("UPDATE workers SET state='completed'"); ledger.db.commit(); ledger.close()
            with patch("earnings_continuation.spawn_worker", return_value=987654):
                started = start(root, args)
            ledger = ContinuationLedger(root)
            persisted = dict(ledger.db.execute("SELECT * FROM rounds WHERE round_id=?", (round_row["round_id"],)).fetchone())
            ledger.close()
            self.assertEqual((started["state"], persisted["state"], persisted["research_outcome"]),
                             ("started", "delivery_pending", "blocked"))
            args.generation = started["generation"]
            final = {"status": "success", "errors": [], "delivery": {"delivery": {"state": "sent"}},
                     "usage_summary": {"actual_model_calls": 0, "calls": []}}
            with patch("earnings_continuation._run_daily", return_value=final) as run_daily, \
                 patch("earnings_continuation.spawn_worker", return_value=888):
                result = worker(root, args)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(run_daily.call_count, 1)
            self.assertTrue(run_daily.call_args.kwargs["finalize_only"])

    def test_cross_day_delivery_retry_hands_off_to_real_collection_round(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            ledger = ContinuationLedger(root)
            ledger.db.execute("UPDATE rounds SET state='delivery_pending',research_outcome='waiting' WHERE round_id=?",
                              (round_row["round_id"],))
            ledger.db.execute("UPDATE workers SET started_at=?", (datetime.now(timezone.utc).isoformat(),))
            ledger.db.commit(); ledger.close()
            delivered = {"status": "success", "errors": [], "delivery": {"delivery": {"state": "sent"}},
                         "usage_summary": {"actual_model_calls": 0, "calls": []}}
            concurrent_start = {}
            def spawn_during_handoff(spawn_root, spawn_args, spawn_round_id, generation):
                with patch("earnings_continuation._pid_alive", return_value=True):
                    concurrent_start.update(start(spawn_root, spawn_args))
                return 222
            with patch("earnings_continuation._run_daily", return_value=delivered) as delivery_run, \
                 patch("earnings_continuation.spawn_worker", side_effect=spawn_during_handoff) as spawn:
                old_result = worker(root, args)
            self.assertEqual(old_result["state"], "waiting")
            self.assertEqual(delivery_run.call_count, 1)
            self.assertEqual((concurrent_start["state"], concurrent_start["round_id"]),
                             ("already_running", round_row["round_id"]))
            next_round = old_result["next_round"]
            self.assertNotEqual(next_round["round_id"], round_row["round_id"])
            self.assertGreater(next_round["cutoff"], round_row["cutoff"])
            spawn.assert_called_once_with(root, args, next_round["round_id"], 1)

            args.round_id = next_round["round_id"]; args.generation = 1
            empty = {"fingerprint": "empty", "actionable_count": 0, "waiting_count": 0,
                     "blocker_count": 0, "pending": {}, "blockers": {}}
            collected = {"status": "success", "errors": [], "progress": empty,
                         "collection_complete": True, "usage_summary": {"actual_model_calls": 0, "calls": []}}
            finalized = {**collected, "delivery": {"delivery": {"state": "suppressed"}}}
            with patch("earnings_continuation._run_daily", side_effect=[collected, finalized]) as collection_run:
                new_result = worker(root, args)
            self.assertEqual(new_result["state"], "complete")
            self.assertFalse(collection_run.call_args_list[0].kwargs.get("finalize_only", False))
            self.assertEqual(collection_run.call_args_list[0].args[2]["round_id"], next_round["round_id"])

    def test_cross_day_failed_or_unknown_delivery_keeps_debt_without_starving_collection(self):
        for delivery_state, expected_old_state in (("retryable_failed", "delivery_pending"),
                                                   ("unknown", "delivery_unknown")):
            with self.subTest(delivery_state=delivery_state), TemporaryDirectory() as temp:
                root = Path(temp).resolve(); round_row, args = self.prepare(root)
                ledger = ContinuationLedger(root)
                ledger.db.execute("UPDATE rounds SET state='delivery_pending',research_outcome='waiting' WHERE round_id=?",
                                  (round_row["round_id"],))
                ledger.db.commit(); ledger.close()
                finalized = {"status": "success", "errors": [],
                    "delivery": {"delivery": {"state": delivery_state}},
                    "usage_summary": {"actual_model_calls": 0, "calls": []}}
                with patch("earnings_continuation._run_daily", return_value=finalized), \
                     patch("earnings_continuation.spawn_worker", return_value=333) as spawn:
                    result = worker(root, args)
                ledger = ContinuationLedger(root)
                old = ledger.db.execute("SELECT state FROM rounds WHERE round_id=?", (round_row["round_id"],)).fetchone()
                current_day = ledger.db.execute("SELECT * FROM rounds WHERE revision=2").fetchone()
                ledger.close()
                self.assertEqual(old["state"], expected_old_state)
                self.assertIsNotNone(current_day)
                self.assertEqual(result["next_round"]["round_id"], current_day["round_id"])
                spawn.assert_called_once_with(root, args, current_day["round_id"], 1)
                if delivery_state == "retryable_failed":
                    with patch("earnings_continuation._run_daily", return_value=finalized), \
                         patch("earnings_continuation.spawn_worker") as duplicate_spawn:
                        repeated = worker(root, args)
                    self.assertEqual(repeated["next_round"]["round_id"], current_day["round_id"])
                    self.assertEqual(repeated["next_round"]["handoff_state"], "already_exists")
                    duplicate_spawn.assert_not_called()

    def test_completed_research_preserves_deferred_notification_for_next_day(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); _, args = self.prepare(root)
            empty = {"fingerprint": "empty", "actionable_count": 0, "blocker_count": 0,
                     "pending": {}, "blockers": {}}
            done = {"status": "success", "errors": [], "progress": empty, "collection_complete": True}
            deferred = {"status": "success", "delivery": {"delivery": {"state": "deferred"}}}
            with patch("earnings_continuation.round_progress", return_value=empty), \
                 patch("earnings_continuation._run_daily", side_effect=[done, deferred]):
                result = worker(root, args)
            self.assertEqual(result["state"], "delivery_pending")
            ledger = ContinuationLedger(root)
            row = ledger.db.execute("SELECT research_outcome FROM rounds WHERE round_id=?", (args.round_id,)).fetchone()
            self.assertEqual(row[0], "complete")
            ledger.close()
            with patch("earnings_continuation._run_daily", return_value=deferred) as run_daily, \
                 patch("earnings_continuation.spawn_worker", return_value=889):
                result = worker(root, args)
            self.assertEqual(result["state"], "delivery_pending")
            self.assertEqual(run_daily.call_count, 1)
            self.assertTrue(run_daily.call_args.kwargs["finalize_only"])

    def test_delivery_unknown_is_terminal_and_not_restarted(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            ledger = ContinuationLedger(root)
            ledger.db.execute("UPDATE rounds SET state='delivery_unknown',research_outcome='complete' WHERE round_id=?",
                              (round_row["round_id"],))
            ledger.db.execute("UPDATE workers SET state='completed'"); ledger.db.commit(); ledger.close()
            with patch("earnings_continuation.spawn_worker", return_value=7):
                started = start(root, args)
            self.assertNotEqual(started["round_id"], round_row["round_id"])

    def test_delivery_retry_restores_complete_or_paused_research_outcome(self):
        for outcome in ("complete", "paused_quota"):
            with self.subTest(outcome=outcome), TemporaryDirectory() as temp:
                root = Path(temp).resolve(); round_row, args = self.prepare(root)
                ledger = ContinuationLedger(root)
                ledger.db.execute("UPDATE rounds SET state='delivery_pending',research_outcome=? WHERE round_id=?",
                                  (outcome, round_row["round_id"]))
                ledger.db.execute("UPDATE workers SET state='completed'"); ledger.db.commit(); ledger.close()
                with patch("earnings_continuation.spawn_worker", return_value=42):
                    started = start(root, args)
                args.generation = started["generation"]
                final = {"status": "success", "errors": [], "delivery": {"delivery": {"state": "sent"}},
                         "usage_summary": {"actual_model_calls": 0, "calls": []}}
                with patch("earnings_continuation._run_daily", return_value=final) as run_daily, \
                     patch("earnings_continuation.spawn_worker", return_value=890):
                    result = worker(root, args)
                self.assertEqual(result["state"], outcome)
                self.assertEqual(run_daily.call_count, 1)

    def test_waiting_round_closes_and_next_start_freezes_new_cutoff(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            config = json.loads((root / args.config).read_text()); config["quarterly"]["automatic_trigger_enabled"] = True
            qledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
            qledger.freeze({"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"},
                           {"industry_id": "waiting", "issuers": [{"symbol": "MISSING"}], "key_symbols": ["MISSING"]},
                           round_row["cutoff"], edition="full"); qledger.close()
            state = EarningsState(root / config["paths"]["state"])
            waiting = round_progress(root, state, config, cutoff=round_row["cutoff"]); state.close()
            self.assertEqual((waiting["actionable_count"], waiting["waiting_count"],
                              waiting["pending"]["quarterly_waiting"]), (0, 1, 1))
            daily = {"status": "success", "errors": [], "progress": waiting,
                     "collection_complete": False, "usage_summary": {"actual_model_calls": 0, "calls": []}}
            final = {"status": "success", "errors": [], "progress": waiting,
                     "delivery": {"delivery": {"state": "suppressed"}},
                     "usage_summary": {"actual_model_calls": 0, "calls": []}}
            with patch("earnings_continuation.round_progress", return_value=waiting), \
                 patch("earnings_continuation._run_daily", side_effect=[daily, final]):
                result = worker(root, args)
            self.assertEqual(result["state"], "waiting")
            ledger = ContinuationLedger(root); ledger.db.execute("UPDATE workers SET state='completed'"); ledger.db.commit(); ledger.close()
            with patch("earnings_continuation.spawn_worker", return_value=8):
                next_round = start(root, args)
            self.assertNotEqual(next_round["round_id"], round_row["round_id"])

    def test_start_retires_yielded_round_when_only_below_trigger_work_remains(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            ledger = ContinuationLedger(root)
            ledger.db.execute("UPDATE rounds SET state='yielded',stop_reason='no durable progress across bounded windows' WHERE round_id=?",
                              (round_row['round_id'],))
            ledger.db.execute("UPDATE workers SET state='completed'"); ledger.db.commit(); ledger.close()
            waiting = {'fingerprint': 'below-trigger', 'actionable_count': 0, 'waiting_count': 1,
                       'blocker_count': 0, 'pending': {'quarterly_scopes': 0, 'quarterly_waiting': 1},
                       'blockers': {}}
            with patch('earnings_continuation.round_progress', return_value=waiting), \
                 patch('earnings_continuation.spawn_worker', return_value=9):
                started = start(root, args)
            self.assertNotEqual(started['round_id'], round_row['round_id'])
            self.assertGreater(started['cutoff'], round_row['cutoff'])
            ledger = ContinuationLedger(root)
            old = ledger.db.execute('SELECT state,stop_reason FROM rounds WHERE round_id=?',
                                    (round_row['round_id'],)).fetchone()
            self.assertEqual(old['state'], 'waiting')
            self.assertIn('no executable evidence', old['stop_reason'])
            ledger.close()

    def test_cleanup_kills_registered_detached_model_session(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); attempt = root / "runtime/earnings/runs/w/attempt"; attempt.mkdir(parents=True)
            marker = root / "detached-marker"
            lock_path = attempt / "model-process.lock"; lock_handle = lock_path.open("a")
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            proc = subprocess.Popen([sys.executable, "-c",
                f"import time,pathlib;time.sleep(.8);pathlib.Path(r'{marker}').write_text('late')"],
                start_new_session=True, pass_fds=(lock_handle.fileno(),)); lock_handle.close()
            identity = earnings_continuation._process_identity(proc.pid)
            atomic_write_json(attempt / "runner-request.json", {"call_id": "real-call", "started_at": "2026-09-21T00:00:00Z",
                "pid": proc.pid, "process_group": proc.pid, "process_identity": identity,
                "ownership_lock": str(lock_path.relative_to(root)),
                "execution_window_id": "window-1", "status": "running", "usage": None})
            killed = earnings_continuation._terminate_owned_model_groups(root, window_id="window-1")
            self.assertEqual(killed[0]["call_id"], "real-call")
            proc.wait(timeout=3); time.sleep(1)
            self.assertFalse(marker.exists())

    def test_cleanup_skips_completed_call_and_identity_mismatch(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); attempt = root / "runtime/earnings/runs/w/attempt"; attempt.mkdir(parents=True)
            lock_path = attempt / "model-process.lock"; lock_handle = lock_path.open("a")
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            proc = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(2)"], start_new_session=True,
                                    pass_fds=(lock_handle.fileno(),)); lock_handle.close()
            request = {"call_id": "done", "started_at": "2026-09-21T00:00:00Z", "pid": proc.pid,
                "process_group": proc.pid, "process_identity": earnings_continuation._process_identity(proc.pid),
                "ownership_lock": str(lock_path.relative_to(root)),
                "execution_window_id": "window-1", "status": "running", "usage": None}
            atomic_write_json(attempt / "runner-request.json", request)
            atomic_write_json(attempt / "runner-result.json", {"call_id": "done", "status": "completed"})
            self.assertEqual(earnings_continuation._terminate_owned_model_groups(root, window_id="window-1"), [])
            (attempt / "runner-result.json").unlink(); request["process_identity"] = "reused-pid"
            atomic_write_json(attempt / "runner-request.json", request)
            self.assertEqual(earnings_continuation._terminate_owned_model_groups(root, window_id="window-1"), [])
            self.assertIsNone(proc.poll()); proc.terminate(); proc.wait(timeout=3)

    def test_aborted_window_reconciles_progress_without_synthetic_model_call(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            before = {"fingerprint": "before", "actionable_count": 1, "waiting_count": 0,
                      "blocker_count": 0, "pending": {"current_company": 1}, "blockers": {}}
            after = {"fingerprint": "after", "actionable_count": 0, "waiting_count": 1,
                     "blocker_count": 0, "pending": {"quarterly_waiting": 1}, "blockers": {}}
            final = {"status": "success", "errors": [], "delivery": {"delivery": {"state": "suppressed"}},
                     "usage_summary": {"actual_model_calls": 0, "calls": []}}
            def daily(*_args, **kwargs):
                if kwargs.get("finalize_only"):
                    return final
                raise RuntimeError("outer daily disappeared")
            with patch("earnings_continuation.round_progress", side_effect=[before, after]), \
                 patch("earnings_continuation._run_daily", side_effect=daily):
                result = worker(root, args)
            checkpoint = next((root / "runtime/earnings/continuation" / round_row["round_id"]).glob("*-w1.json"))
            payload = json.loads(checkpoint.read_text())
            self.assertEqual(result["state"], "waiting")
            self.assertEqual(payload["progress"]["fingerprint"], "after")
            self.assertEqual(payload["usage_summary"]["actual_model_calls"], 0)
            self.assertFalse(payload["usage_summary"]["model_call_count_known"])
            self.assertEqual(payload["usage_summary"]["calls"], [])

    def test_outer_timeout_kills_daily_process_group(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "script").mkdir()
            marker = root / "late-grandchild.txt"
            (root / "script/earnings_daily.py").write_text(
                "import subprocess,sys,time\n"
                f"subprocess.Popen([sys.executable,'-c',\"import time,pathlib;time.sleep(.7);pathlib.Path(r'{marker}').write_text('late')\"])\n"
                "time.sleep(10)\n")
            args = argparse.Namespace(config="c", deployment="d", send=False, process_timeout_seconds=.2)
            with self.assertRaisesRegex(RuntimeError, "process group"):
                _run_daily(root, args, {"batch_date": "2026-09-20", "cutoff": "2026-09-20T00:00:00Z",
                    "round_id": "r"}, "w", 60, resume_only=False)
            time.sleep(1)
            self.assertFalse(marker.exists())

    def test_worker_routes_a_window_to_downstream_despite_company_backlog(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); _round, args = self.prepare(root)
            before = {"fingerprint": "a", "actionable_count": 2, "blocker_count": 0,
                      "pending": {"current_company": 5, "quarterly_scopes": 1}, "blockers": {}}
            after = {**before, "fingerprint": "b", "actionable_count": 0,
                     "pending": {"current_company": 0, "quarterly_scopes": 0}}
            work = {"status": "success", "errors": [], "progress": after,
                    "collection_complete": True, "usage_summary": {"actual_model_calls": 0, "calls": []}}
            final = {"status": "success", "errors": [], "progress": after,
                     "delivery": {"delivery": {"state": "suppressed"}},
                     "usage_summary": {"actual_model_calls": 0, "calls": []}}
            with patch("earnings_continuation.round_progress", return_value=before), \
                 patch("earnings_continuation._run_daily", side_effect=[work, final]) as run_daily:
                worker(root, args)
            self.assertEqual(run_daily.call_args_list[0].kwargs["execution_focus"], "downstream")

    def test_worker_continues_from_last_industry_to_market_and_verified_publication(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); _round, args = self.prepare(root)
            before = {"fingerprint": "industry", "actionable_count": 1, "waiting_count": 0,
                      "blocker_count": 0, "pending": {"quarterly_scopes": 1}, "blockers": {}}
            market = {"fingerprint": "market", "actionable_count": 1, "waiting_count": 0,
                      "blocker_count": 0, "pending": {"quarterly_scopes": 0, "quarterly_market": 1}, "blockers": {}}
            publication = {"fingerprint": "publication", "actionable_count": 1, "waiting_count": 0,
                           "blocker_count": 0, "pending": {"quarterly_market": 0, "publications": 1}, "blockers": {}}
            done = {"fingerprint": "verified", "actionable_count": 0, "waiting_count": 0,
                    "blocker_count": 0, "pending": {}, "blockers": {}}
            windows = [{"status": "success", "errors": [], "progress": progress, "collection_complete": True,
                        "usage_summary": {"actual_model_calls": 1, "calls": []}}
                       for progress in (market, publication, done)]
            final = {"status": "success", "errors": [], "progress": done,
                     "delivery": {"delivery": {"state": "suppressed"}},
                     "usage_summary": {"actual_model_calls": 0, "calls": []}}
            with patch("earnings_continuation.round_progress", side_effect=[before, market, publication]), \
                 patch("earnings_continuation._run_daily", side_effect=[*windows, final]) as run_daily:
                result = worker(root, args)
            self.assertEqual(result["state"], "complete")
            self.assertEqual(run_daily.call_count, 4)
            self.assertTrue(all(call.kwargs.get("execution_focus") == "downstream"
                                for call in run_daily.call_args_list[:3]))

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

    def test_short_worker_tail_hands_off_without_false_no_progress(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); round_row, args = self.prepare(root)
            deployment(root, continuation_worker_seconds=8400, continuation_window_seconds=1800)
            before = {"fingerprint": "before", "actionable_count": 28, "blocker_count": 5,
                      "pending": {"current_company": 11, "publications": 11}, "blockers": {}}
            after = {**before, "fingerprint": "after"}
            daily = {"status": "failed", "errors": ["terminal publications need review"],
                     "progress": after, "collection_complete": True,
                     "usage_summary": {"actual_model_calls": 5}}
            # Production stopped after 136m: ~68s were left after the finalizer
            # reserve, so the old >=60 loop ran two empty windows and yielded.
            with patch("earnings_continuation.round_progress", return_value=before), \
                 patch("earnings_continuation._run_daily", return_value=daily) as run_daily, \
                 patch("earnings_continuation.spawn_worker", return_value=999) as spawn, \
                 patch("earnings_continuation.time.monotonic", side_effect=[0, 0, 0, 8212]):
                result = worker(root, args)
            self.assertEqual(result["state"], "active")
            self.assertEqual(run_daily.call_count, 1)
            self.assertEqual(run_daily.call_args.args[4], 1800)
            spawn.assert_called_once_with(root, args, round_row["round_id"], 2)
            ledger = ContinuationLedger(root)
            saved = ledger.db.execute("SELECT * FROM rounds WHERE round_id=?", (args.round_id,)).fetchone()
            self.assertEqual(saved["no_progress_windows"], 0)
            self.assertEqual(saved["owner_generation"], 2)
            self.assertEqual(saved["cutoff"], round_row["cutoff"])
            self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM windows").fetchone()[0], 1)
            ledger.close()

    def test_worker_rejects_budget_that_cannot_fit_one_full_window(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); _, args = self.prepare(root)
            deployment(root, continuation_worker_seconds=300, continuation_window_seconds=1800)
            with patch("earnings_continuation.spawn_worker") as spawn:
                with self.assertRaisesRegex(ValueError, "full window"):
                    worker(root, args)
                spawn.assert_not_called()

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

    def test_delivery_failure_persists_and_next_run_retries_only_finalization(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); _round, args = self.prepare(root)
            empty = {"fingerprint": "empty", "actionable_count": 0, "blocker_count": 0,
                     "pending": {}, "blockers": {}}
            work = {"status": "success", "errors": [], "progress": empty,
                    "collection_complete": True, "usage_summary": {"actual_model_calls": 0, "calls": []}}
            failed_delivery = {"status": "failed", "errors": ["delivery"], "progress": empty,
                "delivery": {"delivery": {"state": "retryable_failed"}},
                "usage_summary": {"actual_model_calls": 0, "calls": []}}
            with patch("earnings_continuation.round_progress", return_value=empty), \
                 patch("earnings_continuation._run_daily", side_effect=[work, failed_delivery]):
                first = worker(root, args)
            self.assertEqual(first["state"], "delivery_pending")
            recovered = {"status": "success", "errors": [], "progress": empty,
                "delivery": {"delivery": {"state": "sent"}},
                "usage_summary": {"actual_model_calls": 0, "calls": []}}
            with patch("earnings_continuation._run_daily", return_value=recovered) as run_daily:
                with patch("earnings_continuation.spawn_worker", return_value=891):
                    second = worker(root, args)
            self.assertEqual(second["state"], "complete")
            self.assertEqual(run_daily.call_count, 1)
            self.assertTrue(run_daily.call_args.kwargs["finalize_only"])

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
