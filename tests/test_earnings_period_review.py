import sys
from datetime import date
from pathlib import Path
import json
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
    record_gap_review,
)
from earnings_industry_context import _company_inputs
from earnings_common import atomic_write_json, sha256_file
from earnings_state import EarningsState


class EarningsPeriodReviewTests(unittest.TestCase):
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
            root = Path(temp).resolve(); path = root / "report/earnings/company.json"
            atomic_write_json(path, {"cutoff": "2026-08-01T00:00:00Z",
                "scope": {"issuer_id": "issuer-a", "reporting_start": "2026-04-27", "reporting_end": "2026-07-26"},
                "thesis_state": "emerging"})
            row = {"report_id": "r", "task_id": "t", "path": str(path.relative_to(root)), "sha256": sha256_file(path),
                   "completeness": "partial", "source_mode": "live", "period_end": "2026-07-26"}
            class DB:
                def execute(self, *_args): return self
                def fetchall(self): return [row]
            class State: db = DB()
            artifacts, researched = _company_inputs(State(), root, ["issuer-a"], "2026-04-01", "2026-06-30",
                "2026-08-02T00:00:00Z", "2026-Q2")
            self.assertEqual(researched, ["issuer-a"])
            self.assertEqual(artifacts[0]["report_id"], "r")


if __name__ == "__main__":
    unittest.main()
