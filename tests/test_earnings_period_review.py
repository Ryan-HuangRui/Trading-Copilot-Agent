import sys
from datetime import date
from pathlib import Path
import json
import sqlite3
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "script"))

from earnings_period_review import (
    assess_industry_maturity,
    map_fiscal_period,
    review_quarter_for_day,
    QuarterlyReviewLedger,
    inspect_due,
    _period_members,
    _period_member_audit,
    record_gap_review,
)
from earnings_industry_context import _company_inputs
from earnings_common import atomic_write_json, sha256_file
from earnings_state import EarningsState


class EarningsPeriodReviewTests(unittest.TestCase):
    def _register_disclosure(self, state, root, symbol, index, start, end, public_at):
        issuer = f"issuer-{symbol.lower()}"; event = f"event-{symbol.lower()}"
        state.upsert_issuer(issuer_id=issuer, cik=str(index + 1), symbol=symbol, name=symbol,
                            identity_status="resolved")
        state.refresh_event(event, issuer, "earnings", start, end)
        original = root / f"raw_data/earnings/{symbol}.txt"; original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text(f"{symbol} disclosure")
        state.register_document({"document_id": f"doc-{symbol}", "issuer_id": issuer, "event_id": event,
            "form": "10-Q", "source_type": "sec_filing", "source_url": f"https://example.com/{symbol}",
            "provider": "sec", "backend": "test", "reporting_start": start, "reporting_end": end,
            "published_at": public_at, "accepted_at": public_at, "fetched_at": public_at,
            "public_time_precision": "second", "original_path": str(original.relative_to(root)),
            "content_sha256": sha256_file(original), "source_mode": "live", "metadata_json": "{}"})

    def test_same_round_expansion_uses_new_content_address_without_rewriting_old_boundary(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
            scope = ledger.freeze({"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"},
                {"industry_id": "a", "issuers": [], "key_symbols": []}, "2026-09-20T02:00:00Z", edition="full")
            one = [{"issuer_id": "a", "report_id": "A", "task_id": "ta", "path": "report/A.json", "sha256": "A"}]
            two = one + [{"issuer_id": "b", "report_id": "B", "task_id": "tb", "path": "report/B.json", "sha256": "B"}]
            ledger.begin_revision(scope["scope_id"], "fingerprint-A", scope["cutoff"], round_id="round", accepted_reports=one)
            first_path = root / ledger.db.execute("SELECT active_input_path FROM quarterly_scopes").fetchone()[0]
            first_bytes = first_path.read_bytes()
            ledger.begin_revision(scope["scope_id"], "fingerprint-A-B", scope["cutoff"], round_id="round", accepted_reports=two)
            row = dict(ledger.db.execute("SELECT * FROM quarterly_scopes").fetchone()); second_path = root / row["active_input_path"]
            second = json.loads(second_path.read_text())
            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first_path.read_bytes(), first_bytes)
            self.assertEqual((second["input_fingerprint"], second["reports"]), (row["input_fingerprint"], two))
            self.assertEqual(row["research_cutoff"], scope["cutoff"])
            ledger.close()

    def test_same_round_expansion_advances_artifact_boundary_not_public_boundary(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
            scope = ledger.freeze({"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"},
                {"industry_id": "a", "issuers": [], "key_symbols": []}, "2026-09-16T02:00:00Z", edition="stage")
            ledger.begin_revision(scope["scope_id"], "A", "2026-09-16T02:00:00Z", round_id="round", accepted_reports=[])
            ledger.begin_revision(scope["scope_id"], "A-B", "2026-09-20T16:37:31Z", round_id="round",
                                  accepted_reports=[{"sha256": "B"}])
            row = ledger.db.execute("SELECT public_cutoff,research_cutoff FROM quarterly_scopes").fetchone()
            self.assertEqual(tuple(row), ("2026-09-16T02:00:00Z", "2026-09-20T16:37:31Z"))
            ledger.close()

    def test_report_timestamp_cannot_admit_evidence_after_public_boundary(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            self._register_disclosure(state, root, "LATE", 1, "2026-04-01", "2026-06-30", "2026-09-19T00:00:00Z")
            task, _ = state.enqueue_task(task_type="company", subject_id="event-late", period_start="2026-04-01",
                period_end="2026-06-30", input_hash="late", method_version="v1", source_mode="live", profile="daily",
                model="m", effort="e")
            state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,))
            report = root / "report/earnings/late.json"
            atomic_write_json(report, {"cutoff": "2026-09-20T00:00:00Z",
                "scope": {"issuer_id": "issuer-late", "reporting_start": "2026-04-01", "reporting_end": "2026-06-30"},
                "evidence": [{"evidence_id": "e", "document_id": "doc-LATE", "document_version": 1,
                    "document_hash": sha256_file(root / "raw_data/earnings/LATE.txt")} ]})
            state.db.execute("INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("r", task, "company",
                "event-late", "2026-04-01", "2026-06-30", str(report.relative_to(root)), sha256_file(report), "m",
                "live", "partial", "2026-09-20T00:00:00Z"))
            _, _, researched, reasons = _period_member_audit(state, ["issuer-late"], "2026-Q2",
                public_cutoff="2026-09-16T00:00:00Z", research_cutoff="2026-09-20T00:00:00Z")
            self.assertEqual(researched, set())
            self.assertTrue(any("after public cutoff" in reason for reason in reasons["issuer-late"]))
            state.close()

    def test_terminal_delivery_closes_old_dag_for_pending_revision(self):
        with TemporaryDirectory() as temp:
            ledger = QuarterlyReviewLedger(Path(temp).resolve() / "runtime/earnings/quarterly.sqlite")
            scope = ledger.freeze({"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"},
                {"industry_id": "a", "issuers": [], "key_symbols": []}, "2026-09-01T00:00:00Z", edition="stage")
            ledger.begin_revision(scope["scope_id"], "A", scope["cutoff"], round_id="r1")
            for stage in ("gap_review", "industry", "challenge", "synthesis"):
                ledger.set_stage(scope["scope_id"], stage, "completed")
            for stage in ("publication", "checker", "cloud"):
                ledger.set_stage(scope["scope_id"], stage, "blocked", error="bounded repair exhausted")
            self.assertTrue(ledger.begin_revision(scope["scope_id"], "B", "2026-09-02T00:00:00Z", round_id="r2"))
            self.assertEqual(ledger.db.execute("SELECT revision,input_fingerprint FROM quarterly_scopes").fetchone()[:], (2, "B"))
            ledger.close()

    def test_same_round_pending_input_starts_new_revision_after_inflight_dag_finishes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
            scope = ledger.freeze({"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"},
                {"industry_id": "a", "issuers": [], "key_symbols": []}, "2026-09-20T02:00:00Z", edition="stage")
            first = [{"issuer_id": "a", "report_id": "A", "task_id": "ta", "path": "report/A.json", "sha256": "A"}]
            later = first + [{"issuer_id": "b", "report_id": "B", "task_id": "tb", "path": "report/B.json", "sha256": "B"}]
            ledger.begin_revision(scope["scope_id"], "A", scope["cutoff"], round_id="round", accepted_reports=first)
            ledger.set_stage(scope["scope_id"], "industry", "running", input_hash="A")
            self.assertFalse(ledger.begin_revision(scope["scope_id"], "A-B", scope["cutoff"],
                                                   round_id="round", accepted_reports=later))
            self.assertEqual(ledger.db.execute("SELECT revision,pending_fingerprint FROM quarterly_scopes").fetchone()[:],
                             (1, "A-B"))
            for stage in ("gap_review", "industry", "challenge", "synthesis", "publication", "checker", "cloud"):
                ledger.set_stage(scope["scope_id"], stage, "completed")
            self.assertTrue(ledger.begin_revision(scope["scope_id"], "A-B", scope["cutoff"],
                                                  round_id="round", accepted_reports=later))
            current = ledger.db.execute("SELECT revision,input_fingerprint,pending_fingerprint FROM quarterly_scopes").fetchone()
            self.assertEqual(tuple(current), (2, "A-B", None))
            self.assertEqual(json.loads(ledger.db.execute("SELECT accepted_reports_json FROM quarterly_scopes").fetchone()[0]), later)
            ledger.close()

    def test_new_cutoff_does_not_reset_inflight_dag_and_is_audited_as_pending(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
            scope = ledger.freeze({"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"},
                {"industry_id": "a", "issuers": [], "key_symbols": []}, "2026-09-20T02:00:00Z", edition="stage")
            ledger.begin_revision(scope["scope_id"], "A", scope["cutoff"], round_id="round-1", accepted_reports=[])
            ledger.set_stage(scope["scope_id"], "industry", "running", input_hash="A")
            self.assertFalse(ledger.begin_revision(scope["scope_id"], "A-B", "2026-09-21T02:00:00Z",
                                                   round_id="round-2", accepted_reports=[]))
            pending = ledger.db.execute("SELECT revision,pending_fingerprint,pending_round_id,pending_cutoff FROM quarterly_scopes").fetchone()
            self.assertEqual(tuple(pending), (1, "A-B", "round-2", "2026-09-21T02:00:00Z"))
            self.assertEqual(ledger.db.execute("SELECT state FROM quarterly_stages WHERE stage='industry'").fetchone()[0], "running")
            for stage in ("gap_review", "industry", "challenge", "synthesis", "publication", "checker", "cloud"):
                ledger.set_stage(scope["scope_id"], stage, "completed")
            self.assertTrue(ledger.begin_revision(scope["scope_id"], "A-B", "2026-09-21T02:00:00Z",
                                                  round_id="round-2", accepted_reports=[]))
            current = ledger.db.execute("SELECT revision,public_cutoff,pending_round_id,pending_cutoff FROM quarterly_scopes").fetchone()
            self.assertEqual(tuple(current), (2, "2026-09-21T02:00:00Z", None, None))
            ledger.close()

    def test_legacy_fingerprint_migration_binds_reports_without_resetting_stage(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); path = root / "runtime/earnings/quarterly.sqlite"; path.parent.mkdir(parents=True)
            legacy = sqlite3.connect(path)
            legacy.executescript("""CREATE TABLE quarterly_scopes(scope_id TEXT PRIMARY KEY,quarter_id TEXT NOT NULL,
              industry_id TEXT NOT NULL,period_start TEXT NOT NULL,period_end TEXT NOT NULL,
              frozen_universe_json TEXT NOT NULL,frozen_universe_hash TEXT NOT NULL,cutoff TEXT NOT NULL,
              edition TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,
              input_fingerprint TEXT,UNIQUE(quarter_id,industry_id,edition));
              CREATE TABLE quarterly_stages(scope_id TEXT NOT NULL,stage TEXT NOT NULL,state TEXT NOT NULL,input_hash TEXT,
              artifact_path TEXT,artifact_sha256 TEXT,attempts INTEGER NOT NULL DEFAULT 0,error TEXT,updated_at TEXT NOT NULL,
              PRIMARY KEY(scope_id,stage));""")
            frozen = json.dumps({"industry_id": "a", "issuers": [], "key_symbols": []})
            legacy.execute("INSERT INTO quarterly_scopes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("legacy-scope", "2026-Q2", "a", "2026-04-01", "2026-06-30", frozen, "universe-sha",
                 "2026-09-20T02:00:00Z", "full", "created", "updated", 1, "same"))
            legacy.execute("INSERT INTO quarterly_stages VALUES(?,?,?,?,?,?,?,?,?)",
                ("legacy-scope", "industry", "completed", "same", "old.json", "old-sha", 1, None, "updated"))
            legacy.commit(); legacy.close()
            old_frozen = root / "runtime/earnings/quarterly-scopes/legacy-scope/revisions/v1/frozen-scope.json"
            atomic_write_json(old_frozen, {"scope_id": "legacy-scope", "revision": 1,
                              "cutoff": "2026-09-20T02:00:00Z", "frozen_universe_hash": "universe-sha"})
            ledger = QuarterlyReviewLedger(path); scope = {"scope_id": "legacy-scope"}
            before = dict(ledger.db.execute("SELECT * FROM quarterly_stages WHERE scope_id=? AND stage='industry'",
                                            (scope["scope_id"],)).fetchone())
            reports = [{"issuer_id": "issuer", "report_id": "report", "task_id": "task",
                        "path": "report/earnings/company.json", "sha256": "digest"}]
            revised = ledger.begin_revision(scope["scope_id"], "same", "2026-09-21T02:00:00Z",
                                            round_id="round-2", accepted_reports=reports)
            after = dict(ledger.db.execute("SELECT * FROM quarterly_stages WHERE scope_id=? AND stage='industry'",
                                           (scope["scope_id"],)).fetchone())
            row = dict(ledger.db.execute("SELECT * FROM quarterly_scopes WHERE scope_id=?", (scope["scope_id"],)).fetchone())
            accepted_path = root / row["active_input_path"]
            self.assertFalse(revised)
            self.assertEqual((after["state"], after["attempts"], after["artifact_sha256"]),
                             (before["state"], before["attempts"], before["artifact_sha256"]))
            self.assertEqual(json.loads(row["accepted_reports_json"]), reports)
            self.assertEqual(json.loads(accepted_path.read_text())["round_id"], "round-2")
            self.assertEqual(json.loads(accepted_path.read_text())["cutoff"], "2026-09-21T02:00:00Z")
            frozen = root / "runtime/earnings/quarterly-scopes" / scope["scope_id"] / "revisions/v1/frozen-scope.json"
            self.assertEqual(json.loads(frozen.read_text())["cutoff"], "2026-09-20T02:00:00Z")
            ledger.close()

    def test_fiscal_quarter_maps_by_overlap_and_preserves_actual_period(self):
        mapped = map_fiscal_period("2026-04-27", "2026-07-26", form="10-Q")
        self.assertEqual(mapped["research_quarter"], "2026-Q2")
        self.assertEqual(mapped["actual_period"], {"start": "2026-04-27", "end": "2026-07-26"})
        self.assertGreater(mapped["overlap_days"], 0)
        self.assertTrue(mapped["cross_period_difference"])

    def test_cumulative_and_annual_periods_cannot_masquerade_as_quarter(self):
        with self.assertRaisesRegex(ValueError, "standalone quarter"):
            map_fiscal_period("2026-01-01", "2026-06-30", form="10-Q")
        with self.assertRaisesRegex(ValueError, "annual"):
            map_fiscal_period("2026-01-01", "2026-12-31", form="10-K")
        with self.assertRaisesRegex(ValueError, "missing"):
            map_fiscal_period(None, "2026-06-30", form="10-Q")

    def test_future_quarter_is_not_reviewed_and_tail_cutoff_is_explicit(self):
        self.assertEqual(review_quarter_for_day(date(2026, 8, 25))["quarter_id"], "2026-Q2")
        self.assertTrue(review_quarter_for_day(date(2026, 8, 25))["tail_window"])
        self.assertFalse(review_quarter_for_day(date(2026, 8, 25))["deadline_reached"])
        self.assertTrue(review_quarter_for_day(date(2026, 8, 31))["deadline_reached"])
        self.assertFalse(review_quarter_for_day(date(2026, 7, 1))["tail_window"])

    def test_fixed_denominator_and_key_gap_override_ninety_percent(self):
        assessment = assess_industry_maturity(
            expected_issuer_ids=[f"i{i}" for i in range(10)],
            key_issuer_ids=["i9"],
            disclosed_issuer_ids=[f"i{i}" for i in range(9)],
            fetched_issuer_ids=[f"i{i}" for i in range(9)],
            researched_issuer_ids=[f"i{i}" for i in range(9)],
            critical_gap_status="resolved",
            threshold=0.9,
        )
        self.assertEqual(assessment["counts"]["expected_issuers"], 10)
        self.assertEqual(assessment["coverage_ratio"], 0.9)
        self.assertFalse(assessment["eligible_full"])
        self.assertEqual(assessment["counts"]["key_missing_issuers"], ["i9"])

    def test_stage_trigger_uses_disclosures_not_completed_research(self):
        expected = [f"i{i}" for i in range(6)]
        triggered = assess_industry_maturity(
            expected_issuer_ids=expected, key_issuer_ids=["i0"],
            disclosed_issuer_ids=expected[:4], fetched_issuer_ids=expected[:3],
            researched_issuer_ids=expected[:1], critical_gap_status="unresolved",
            threshold=0.9, stage_threshold=0.6,
        )
        self.assertTrue(triggered["eligible_stage"])
        self.assertEqual(triggered["stage_trigger"], "disclosure_ratio")
        self.assertFalse(triggered["eligible_full"])

    def test_key_disclosure_triggers_limited_stage_but_two_non_keys_do_not(self):
        expected = [f"i{i}" for i in range(6)]
        key = assess_industry_maturity(
            expected_issuer_ids=expected, key_issuer_ids=["i0"],
            disclosed_issuer_ids=["i0"], fetched_issuer_ids=["i0"],
            researched_issuer_ids=["i0"], critical_gap_status="unresolved",
            threshold=0.9, stage_threshold=0.6,
        )
        sparse = assess_industry_maturity(
            expected_issuer_ids=expected, key_issuer_ids=["i0"],
            disclosed_issuer_ids=["i1", "i2"], fetched_issuer_ids=["i1", "i2"],
            researched_issuer_ids=["i1", "i2"], critical_gap_status="unresolved",
            threshold=0.9, stage_threshold=0.6,
        )
        self.assertEqual((key["eligible_stage"], key["stage_trigger"]), (True, "key_disclosure"))
        self.assertEqual((sparse["eligible_stage"], sparse["stage_trigger"]), (False, None))

    def test_non_tail_four_of_six_disclosures_create_stage_scope_before_research(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "config").mkdir()
            config = json.loads((Path(__file__).resolve().parents[1] / "config/earnings_research.json").read_text())
            config["quarterly"]["automatic_trigger_enabled"] = True
            config["quarterly"]["initial_backfill_quarters"] = 0
            atomic_write_json(root / "config/earnings_research.json", config)
            symbols = [f"S{i}" for i in range(6)]
            atomic_write_json(root / "config/earnings_universe.json", {"industries": [{"industry_id": "sample",
                "metric_template": "m", "issuers": [{"symbol": symbol} for symbol in symbols],
                "key_symbols": ["S0"]}]})
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            for index, symbol in enumerate(symbols[:4]):
                self._register_disclosure(state, root, symbol, index, "2026-04-01", "2026-06-30",
                                          "2026-08-01T00:00:00Z")
            state.close()
            review = inspect_due(root, day="2026-08-10", cutoff="2026-08-10T00:00:00Z",
                config_path="config/earnings_research.json", universe_path="config/earnings_universe.json")
            self.assertFalse(review["quarter"]["tail_window"])
            self.assertEqual(len(review["scopes"]), 1)
            scope = review["scopes"][0]
            self.assertEqual(scope["maturity"]["counts"]["disclosed_issuers"], 4)
            self.assertTrue(scope["eligible_stage"])
            self.assertEqual(scope["maturity"]["stage_trigger"], "disclosure_ratio")
            self.assertEqual(scope["finalization"]["state"], "open")

    def test_current_natural_quarter_scope_can_start_from_mapped_key_disclosure(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "config").mkdir()
            config = json.loads((Path(__file__).resolve().parents[1] / "config/earnings_research.json").read_text())
            config["quarterly"]["automatic_trigger_enabled"] = True
            config["quarterly"]["initial_backfill_quarters"] = 0
            atomic_write_json(root / "config/earnings_research.json", config)
            atomic_write_json(root / "config/earnings_universe.json", {"industries": [{"industry_id": "off-cycle",
                "metric_template": "m", "issuers": [{"symbol": "KEY"}, {"symbol": "PEER"}],
                "key_symbols": ["KEY"]}]})
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            self._register_disclosure(state, root, "KEY", 0, "2026-07-01", "2026-09-15",
                                      "2026-09-18T00:00:00Z")
            state.close()
            review = inspect_due(root, day="2026-09-21", cutoff="2026-09-21T00:00:00Z",
                config_path="config/earnings_research.json", universe_path="config/earnings_universe.json")
            scope = next(row for row in review["scopes"] if row["quarter_id"] == "2026-Q3")
            self.assertTrue(scope["eligible_stage"])
            self.assertEqual(scope["maturity"]["stage_trigger"], "key_disclosure")
            self.assertEqual(scope["edition"], "stage")

    def test_legacy_scope_migrates_public_and_research_cutoffs_separately(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); path = root / "runtime/earnings/quarterly.sqlite"; path.parent.mkdir(parents=True)
            legacy = sqlite3.connect(path)
            legacy.executescript("""CREATE TABLE quarterly_scopes(scope_id TEXT PRIMARY KEY,quarter_id TEXT NOT NULL,
              industry_id TEXT NOT NULL,period_start TEXT NOT NULL,period_end TEXT NOT NULL,
              frozen_universe_json TEXT NOT NULL,frozen_universe_hash TEXT NOT NULL,cutoff TEXT NOT NULL,
              edition TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
              UNIQUE(quarter_id,industry_id,edition));""")
            legacy.execute("INSERT INTO quarterly_scopes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("consumer", "2026-Q2", "consumer-retail", "2026-04-01", "2026-06-30",
                 json.dumps({"industry_id": "consumer-retail", "issuers": [], "key_symbols": []}),
                 "universe", "2026-09-16T02:00:00.189002Z", "stage", "created", "updated"))
            legacy.commit(); legacy.close()
            ledger = QuarterlyReviewLedger(path)
            row = ledger.db.execute("SELECT cutoff,public_cutoff,research_cutoff FROM quarterly_scopes").fetchone()
            self.assertEqual(tuple(row), ("2026-09-16T02:00:00.189002Z",) * 3)
            ledger.begin_revision("consumer", "accepted-five", "2026-09-20T16:37:31.718967Z",
                                  round_id="round-later", accepted_reports=[])
            row = ledger.db.execute("SELECT public_cutoff,research_cutoff FROM quarterly_scopes").fetchone()
            self.assertEqual(tuple(row), ("2026-09-16T02:00:00.189002Z", "2026-09-20T16:37:31.718967Z"))
            ledger.close()

    def test_scope_freeze_does_not_accept_later_universe_growth(self):
        with TemporaryDirectory() as temp:
            ledger = QuarterlyReviewLedger(Path(temp) / "quarterly.sqlite")
            quarter = {"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"}
            original = {"industry_id": "a", "metric_template": "m1", "issuers": [{"symbol": "A"}], "key_symbols": ["A"]}
            first = ledger.freeze(quarter, original, "2026-08-01T00:00:00Z", edition="full")
            changed = {**original, "metric_template": "m2", "issuers": [{"symbol": "A"}, {"symbol": "B"}]}
            second = ledger.freeze(quarter, changed, "2026-08-02T00:00:00Z", edition="full")
            self.assertEqual(json.loads(first["frozen_universe_json"]), json.loads(second["frozen_universe_json"]))
            self.assertEqual(len(json.loads(second["frozen_universe_json"])["issuers"]), 1)
            self.assertEqual(json.loads(second["frozen_universe_json"])["metric_template"], "m1")
            ledger.close()

    def test_due_scope_survives_rollover_and_writes_evidence_gap_review(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "config").mkdir()
            config = json.loads((Path(__file__).resolve().parents[1] / "config/earnings_research.json").read_text())
            config["quarterly"]["automatic_trigger_enabled"] = True
            atomic_write_json(root / "config/earnings_research.json", config)
            universe_path = root / "config/earnings_universe.json"
            atomic_write_json(universe_path, {"industries": [{"industry_id": "a", "metric_template": "m1",
                "issuers": [{"symbol": "A"}], "key_symbols": ["A"]}]})
            first = inspect_due(root, day="2026-08-25", cutoff="2026-08-25T00:00:00Z",
                config_path="config/earnings_research.json", universe_path="config/earnings_universe.json")
            self.assertEqual(first["scopes"][0]["frozen_industry"]["metric_template"], "m1")
            self.assertIsNone(first["scopes"][0]["gap_review_path"])
            self.assertTrue((root / first["scopes"][0]["gap_review_input_path"]).exists())
            self.assertEqual(first["scopes"][0]["maturity"]["critical_gap_status"], "unresolved")
            self.assertFalse(first["scopes"][0]["deadline_stage_allowed"])
            deadline = inspect_due(root, day="2026-08-31", cutoff="2026-08-31T00:00:00Z",
                config_path="config/earnings_research.json", universe_path="config/earnings_universe.json")
            self.assertTrue(deadline["scopes"][0]["deadline_stage_allowed"])
            atomic_write_json(universe_path, {"industries": [{"industry_id": "b", "metric_template": "m2",
                "issuers": [{"symbol": "B"}], "key_symbols": ["B"]}]})
            later = inspect_due(root, day="2026-12-01", cutoff="2026-12-01T00:00:00Z",
                config_path="config/earnings_research.json", universe_path="config/earnings_universe.json")
            old = next(row for row in later["scopes"] if row["scope_id"] == first["scopes"][0]["scope_id"])
            self.assertEqual(old["frozen_industry"]["industry_id"], "a")
            self.assertEqual(old["frozen_industry"]["issuers"], [{"symbol": "A"}])

    def test_gap_review_cannot_auto_resolve_missing_membership_and_is_hash_bound(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "config").mkdir()
            config = json.loads((Path(__file__).resolve().parents[1] / "config/earnings_research.json").read_text())
            config["quarterly"]["automatic_trigger_enabled"] = True
            atomic_write_json(root / "config/earnings_research.json", config)
            atomic_write_json(root / "config/earnings_universe.json", {"industries": [{"industry_id": "a",
                "metric_template": "m", "issuers": [{"symbol": "MISSING"}], "key_symbols": ["MISSING"]}]})
            review = inspect_due(root, day="2026-08-31", cutoff="2026-08-31T00:00:00Z",
                config_path="config/earnings_research.json", universe_path="config/earnings_universe.json")
            scope = review["scopes"][0]; review_input = json.loads((root / scope["gap_review_input_path"]).read_text())
            limitation = review_input["critical_limitations"][0]
            result_path = root / "runtime/earnings/gap-result.json"
            atomic_write_json(result_path, {"scope_id": scope["scope_id"], "input_hash": review_input["input_hash"],
                "cutoff": review_input["cutoff"], "status": "resolved",
                "omitted_and_negative_sample_review": [{"sample": "missing member", "finding": "unresolved", "evidence_ids": []}],
                "limitation_dispositions": [{"limitation_key": limitation["limitation_key"], "disposition": "not_material",
                    "rationale": "ignore", "evidence_ids": []}]})
            with self.assertRaisesRegex(ValueError, "membership gap"):
                record_gap_review(root, scope["scope_id"], result_path)

    def test_coverage_resolves_null_metadata_period_from_accepted_report_evidence(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            state.upsert_issuer(issuer_id="issuer", cik="1", symbol="TEST", name="Test", identity_status="resolved")
            state.refresh_event("event", "issuer", "earnings", None, "2026-07-26")
            original = root / "raw_data/earnings/doc.txt"; original.parent.mkdir(parents=True); original.write_text("accepted filing")
            state.register_document({"document_id": "doc", "issuer_id": "issuer", "event_id": "event", "form": "10-Q",
                "source_type": "sec_filing", "source_url": "https://example.com/doc", "provider": "sec", "backend": "fixture-test",
                "reporting_start": None, "reporting_end": None, "published_at": "2026-08-01T00:00:00Z",
                "accepted_at": "2026-08-01T00:00:00Z", "fetched_at": "2026-08-01T00:00:00Z",
                "public_time_precision": "second", "original_path": str(original.relative_to(root)),
                "content_sha256": sha256_file(original), "source_mode": "live", "metadata_json": "{}"})
            task, _ = state.enqueue_task(task_type="company", subject_id="event", period_start=None, period_end="2026-07-26",
                input_hash="input", method_version="v1", source_mode="live", profile="daily", model="gpt-5.6-sol", effort="medium")
            state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,))
            report = root / "report/earnings/company.json"
            atomic_write_json(report, {"report_id": "report", "report_type": "company", "task_id": task,
                "source_mode": "live", "cutoff": "2026-08-02T00:00:00Z",
                "scope": {"issuer_id": "issuer", "reporting_start": None, "reporting_end": "2026-07-26"},
                "claims": [], "limitations": [], "evidence": [{"evidence_id": "e1", "document_id": "doc",
                    "document_version": 1, "document_hash": sha256_file(original),
                    "numeric_facts": [{"metric": "revenue", "value": "100.25", "unit": "USD",
                        "period": {"kind": "duration", "start": "2026-04-27", "end": "2026-07-26"}}]}]})
            state.db.execute("INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("report", task, "company", "event", None, "2026-07-26", str(report.relative_to(root)), sha256_file(report),
                 "manifest", "live", "partial", "2026-08-02T00:00:00Z"))
            self.assertEqual(_period_members(state, ["issuer"], "2026-Q2", "2026-08-03T00:00:00Z"),
                             ({"issuer"}, {"issuer"}, {"issuer"}))
            original.unlink()
            self.assertEqual(_period_members(state, ["issuer"], "2026-Q2", "2026-08-03T00:00:00Z"),
                             ({"issuer"}, set(), set()))
            state.close()

    def test_manual_future_quarter_is_rejected(self):
        # The pure day selector also never returns the current unfinished quarter.
        self.assertNotEqual(review_quarter_for_day(date(2026, 8, 1))["quarter_id"], "2026-Q3")

    def test_quarterly_stage_state_survives_process_restart(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "quarterly.sqlite"
            ledger = QuarterlyReviewLedger(path)
            quarter = {"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"}
            scope = ledger.freeze(quarter, {"industry_id": "a", "issuers": [], "key_symbols": []}, "cutoff", edition="stage")
            ledger.set_stage(scope["scope_id"], "industry", "completed", input_hash="input-a", artifact_path="report.json", artifact_sha256="sha")
            ledger.close()
            reopened = QuarterlyReviewLedger(path)
            row = reopened.db.execute("SELECT * FROM quarterly_stages WHERE scope_id=? AND stage='industry'", (scope["scope_id"],)).fetchone()
            self.assertEqual((row["state"], row["input_hash"], row["artifact_sha256"]), ("completed", "input-a", "sha"))
            reopened.close()

    def test_completed_scope_starts_versioned_revision_without_losing_stage_history(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
            quarter = {"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"}
            scope = ledger.freeze(quarter, {"industry_id": "a", "metric_template": "m1", "issuers": [], "key_symbols": []},
                                  "2026-08-20T00:00:00Z", edition="full")
            ledger.begin_revision(scope["scope_id"], "fingerprint-1", "2026-08-20T00:00:00Z")
            for stage in ledger.STAGES:
                if stage != "market": ledger.set_stage(scope["scope_id"], stage, "completed", artifact_path=f"{stage}.json")
            self.assertTrue(ledger.begin_revision(scope["scope_id"], "fingerprint-2", "2026-09-02T00:00:00Z"))
            current = ledger.db.execute("SELECT revision,cutoff,frozen_universe_json FROM quarterly_scopes").fetchone()
            self.assertEqual((current["revision"], current["cutoff"]), (2, "2026-09-02T00:00:00Z"))
            self.assertEqual(json.loads(current["frozen_universe_json"])["metric_template"], "m1")
            self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM quarterly_stage_history WHERE revision=1").fetchone()[0], len(ledger.STAGES))
            self.assertEqual(ledger.db.execute("SELECT state FROM quarterly_stage_history WHERE revision=1 AND stage='market'").fetchone()[0], "pending")
            self.assertEqual({row[0] for row in ledger.db.execute("SELECT state FROM quarterly_stages")}, {"pending"})
            revision_file = root / "runtime/earnings/quarterly-scopes" / scope["scope_id"] / "revisions/v2/frozen-scope.json"
            self.assertEqual(json.loads(revision_file.read_text())["cutoff"], "2026-09-02T00:00:00Z")
            ledger.close()

    def test_finalization_seals_delivered_revision_and_late_input_opens_new_revision(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
            scope = ledger.freeze({"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"},
                {"industry_id": "a", "issuers": [], "key_symbols": []}, "2026-08-31T00:00:00Z", edition="stage")
            ledger.begin_revision(scope["scope_id"], "first", scope["cutoff"], round_id="round-1")
            ledger.db.execute("UPDATE quarterly_scopes SET finalization_state='ready_stage_with_gaps' WHERE scope_id=?",
                              (scope["scope_id"],))
            for stage in ("gap_review", "industry", "challenge", "synthesis", "publication", "checker", "cloud"):
                ledger.set_stage(scope["scope_id"], stage, "completed", artifact_path=f"{stage}.json")
            self.assertTrue(ledger.finalize_if_ready(scope["scope_id"]))
            self.assertFalse(ledger.finalize_if_ready(scope["scope_id"]))
            sealed = ledger.db.execute("SELECT finalization_state,finalized_revision FROM quarterly_scopes").fetchone()
            self.assertEqual(tuple(sealed), ("finalized_stage_with_gaps", 1))
            self.assertTrue(ledger.begin_revision(scope["scope_id"], "late-correction", "2026-09-05T00:00:00Z",
                                                  round_id="round-2"))
            reopened = ledger.db.execute("SELECT revision,edition,finalization_state,finalized_revision FROM quarterly_scopes").fetchone()
            self.assertEqual(tuple(reopened), (2, "revision", "open", None))
            self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM quarterly_stage_history WHERE revision=1").fetchone()[0],
                             len(ledger.STAGES))
            ledger.close()

    def test_deadline_scope_is_superseded_by_late_accepted_evidence_without_market_completion(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "config").mkdir()
            config = json.loads((Path(__file__).resolve().parents[1] / "config/earnings_research.json").read_text())
            config["quarterly"]["automatic_trigger_enabled"] = True
            atomic_write_json(root / "config/earnings_research.json", config)
            atomic_write_json(root / "config/earnings_universe.json", {"industries": [{"industry_id": "a",
                "metric_template": "m", "issuers": [{"symbol": "LATE"}], "key_symbols": ["LATE"]}]})
            first = inspect_due(root, day="2026-08-31", cutoff="2026-08-31T00:00:00Z",
                config_path="config/earnings_research.json", universe_path="config/earnings_universe.json")
            self.assertTrue(first["scopes"][0]["deadline_stage_allowed"])
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            state.upsert_issuer(issuer_id="late", cik="1", symbol="LATE", name="Late", identity_status="resolved")
            state.refresh_event("late-event", "late", "earnings", None, "2026-07-26")
            original = root / "raw_data/earnings/late.txt"; original.parent.mkdir(parents=True); original.write_text("late evidence")
            state.register_document({"document_id": "late-doc", "issuer_id": "late", "event_id": "late-event", "form": "10-Q",
                "source_type": "sec_filing", "source_url": "https://example.com/late", "provider": "sec", "backend": "test",
                "reporting_start": None, "reporting_end": None, "published_at": "2026-09-01T00:00:00Z",
                "accepted_at": "2026-09-01T00:00:00Z", "fetched_at": "2026-09-01T00:00:00Z",
                "public_time_precision": "second", "original_path": str(original.relative_to(root)),
                "content_sha256": sha256_file(original), "source_mode": "live", "metadata_json": "{}"})
            task, _ = state.enqueue_task(task_type="company", subject_id="late-event", period_start=None, period_end="2026-07-26",
                input_hash="late", method_version="v1", source_mode="live", profile="daily", model="gpt-5.6-sol", effort="medium")
            state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,))
            report = root / "report/earnings/late.json"
            atomic_write_json(report, {"report_id": "late-report", "report_type": "company", "task_id": task,
                "source_mode": "live", "cutoff": "2026-09-01T00:00:00Z",
                "scope": {"issuer_id": "late", "reporting_start": None, "reporting_end": "2026-07-26"},
                "claims": [], "limitations": [], "evidence": [{"evidence_id": "late-evidence", "document_id": "late-doc",
                    "document_version": 1, "document_hash": sha256_file(original), "numeric_facts": [{"metric": "revenue",
                    "value": "9.5", "unit": "USD", "period": {"kind": "duration", "start": "2026-04-27", "end": "2026-07-26"}}]}]})
            state.db.execute("INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("late-report", task, "company",
                "late-event", None, "2026-07-26", str(report.relative_to(root)), sha256_file(report), "manifest", "live",
                "partial", "2026-09-01T00:00:00Z")); state.close()
            second = inspect_due(root, day="2026-09-02", cutoff="2026-09-02T00:00:00Z",
                config_path="config/earnings_research.json", universe_path="config/earnings_universe.json")
            revised = next(row for row in second["scopes"] if row["scope_id"] == first["scopes"][0]["scope_id"])
            self.assertEqual((revised["revision"], revised["cutoff"]), (2, "2026-09-02T00:00:00Z"))
            self.assertEqual(revised["maturity"]["counts"], {"expected_issuers": 1, "disclosed_issuers": 1,
                "fetched_issuers": 1, "researched_issuers": 1, "key_missing_issuers": []})
            ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
            self.assertEqual(ledger.db.execute("SELECT state FROM quarterly_stage_history WHERE revision=1 AND stage='market'").fetchone()[0],
                             "pending")
            ledger.close()

    def test_quarterly_context_selects_fiscal_period_by_mapping_not_end_date_range(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); state = EarningsState(root / "runtime/earnings/state.sqlite")
            self._register_disclosure(state, root, "A", 1, "2026-04-27", "2026-07-26", "2026-08-01T00:00:00Z")
            path = root / "report/earnings/company.json"
            atomic_write_json(path, {"cutoff": "2026-08-01T00:00:00Z",
                "scope": {"issuer_id": "issuer-a", "reporting_start": "2026-04-27", "reporting_end": "2026-07-26"},
                "thesis_state": "emerging", "evidence": [{"evidence_id": "e", "document_id": "doc-A",
                    "document_version": 1, "document_hash": sha256_file(root / "raw_data/earnings/A.txt")} ]})
            task, _ = state.enqueue_task(task_type="company", subject_id="event-a", period_start="2026-04-27",
                period_end="2026-07-26", input_hash="i", method_version="v1", source_mode="live", profile="daily",
                model="m", effort="e")
            state.db.execute("INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("r", task, "company",
                "event-a", "2026-04-27", "2026-07-26", str(path.relative_to(root)), sha256_file(path), "m", "live",
                "partial", "2026-08-01T00:00:00Z"))
            artifacts, researched = _company_inputs(state, root, ["issuer-a"], "2026-04-01", "2026-06-30",
                "2026-08-02T00:00:00Z", "2026-Q2")
            self.assertEqual(researched, ["issuer-a"])
            self.assertEqual(artifacts[0]["report_id"], "r")
            state.close()


if __name__ == "__main__":
    unittest.main()
