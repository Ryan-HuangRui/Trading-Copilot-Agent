import sys
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "script"))
from earnings_market_context import assess_market_dependencies, build_context
from earnings_common import atomic_write_json, sha256_file
from earnings_period_review import QuarterlyReviewLedger
from earnings_state import EarningsState


class EarningsMarketContextTests(unittest.TestCase):
    def test_full_market_requires_every_frozen_industry(self):
        rows = [{"industry_id": "a", "edition": "full", "checker_status": "passed",
                 "finalization_state": "finalized_full"},
                {"industry_id": "b", "edition": "stage", "checker_status": "passed",
                 "finalization_state": "finalized_stage_with_gaps"}]
        result = assess_market_dependencies(["a", "b"], rows, requested_edition="full")
        self.assertFalse(result["eligible"]); self.assertEqual(result["missing_or_ineligible"], ["b"])
        self.assertTrue(assess_market_dependencies(["a", "b"], rows, requested_edition="stage")["eligible"])

    def test_unsealed_stage_snapshot_cannot_enter_stage_market(self):
        rows = [{"industry_id": "a", "edition": "stage", "checker_status": "passed",
                 "finalization_state": "open"}]
        self.assertFalse(assess_market_dependencies(["a"], rows, requested_edition="stage")["eligible"])

    def test_duplicate_industry_does_not_satisfy_missing_dependency(self):
        rows = [{"industry_id": "a", "edition": "full", "checker_status": "passed"}] * 2
        self.assertEqual(assess_market_dependencies(["a", "b"], rows, requested_edition="full")["missing_or_ineligible"], ["b"])

    def test_partial_revision_cannot_enter_full_market(self):
        rows = [{"industry_id": "a", "edition": "revision", "checker_status": "passed",
                 "completeness_status": "partial", "version_kind": "revision"}]
        self.assertFalse(assess_market_dependencies(["a"], rows, requested_edition="full")["eligible"])

    def test_five_scope_fixture_chain_uses_full_registry_and_checked_publications(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / "config").mkdir()
            source_root = Path(__file__).resolve().parents[1]
            config = json.loads((source_root / "config/earnings_research.json").read_text())
            atomic_write_json(root / "config/earnings_research.json", config)
            industries = [{"industry_id": f"i{index}", "label": f"I{index}", "metric_template": "fixture",
                "key_symbols": [], "issuers": []} for index in range(5)]
            atomic_write_json(root / "config/earnings_universe.json", {"schema_version": 1, "industries": industries})
            state = EarningsState(root / "runtime/earnings/state.sqlite")
            ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
            reports, frozen_paths = [], []
            quarter = {"quarter_id": "2026-Q2", "period_start": "2026-04-01", "period_end": "2026-06-30"}
            for index, industry in enumerate(industries):
                industry_cutoff = f"2026-09-0{index + 1}T00:00:00Z"
                frozen = ledger.freeze(quarter, industry, industry_cutoff, edition="full")
                frozen_paths.append(Path(frozen["frozen_scope_path"]))
                dependency = None
                for role in ("industry", "challenge", "synthesis"):
                    task, _ = state.enqueue_task(task_type=role, subject_id=industry["industry_id"], period_start=quarter["period_start"],
                        period_end=quarter["period_end"], input_hash=f"{role}-{index}", method_version="fixture-chain-v1",
                        source_mode="live", profile="quarterly", model="gpt-6-astra", effort="high",
                        dependencies=[dependency] if dependency else [])
                    state.db.execute("UPDATE research_tasks SET state='completed' WHERE task_id=?", (task,)); dependency = task
                report_path = root / f"report/earnings/industries/{industry['industry_id']}/synthesis.json"
                report = {"schema_version": 1, "report_id": f"report-{index}", "report_type": "synthesis", "task_id": dependency,
                    "research_mode": "quarterly", "source_mode": "live", "cutoff": industry_cutoff,
                    "scope": {"industry_id": industry["industry_id"], "reporting_start": quarter["period_start"],
                              "reporting_end": quarter["period_end"]}, "coverage": {"expected_issuers": 0,
                    "disclosed_issuers": 0, "fetched_issuers": 0, "researched_issuers": 0, "key_missing_issuers": []},
                    "claims": [], "evidence": [], "limitations": [], "completeness": {"status": "full", "missing_inputs": []}}
                atomic_write_json(report_path, report); digest = sha256_file(report_path); reports.append(report_path)
                state.db.execute("INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (f"report-{index}", dependency,
                    "synthesis", industry["industry_id"], quarter["period_start"], quarter["period_end"],
                    str(report_path.relative_to(root)), digest, "fixture-input", "live", "full", "2026-08-31T00:00:00Z"))
                ledger.set_stage(frozen["scope_id"], "synthesis", "completed", artifact_path=str(report_path.relative_to(root)),
                                 artifact_sha256=digest)
                manifest_path = root / f"report/earnings/publications/industry/{industry['industry_id']}/2026-Q2/v1/publication-manifest.json"
                atomic_write_json(manifest_path, {"schema_version": 2, "publication_id": f"publication-{index}",
                    "series_id": f"series-{index}", "publication_type": "industry", "scope_id": industry["industry_id"],
                    "quarter_id": "2026-Q2", "edition": "full", "version": 1, "publishable": True,
                    "checker": {"status": "passed", "errors": []}, "sources": [{"path": str(report_path.relative_to(root)), "sha256": digest}]})
                state.db.execute("INSERT INTO publication_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (f"publication-{index}",
                    f"series-{index}", "industry", industry["industry_id"], "2026-Q2", "full", 1,
                    str(manifest_path.relative_to(root)), sha256_file(manifest_path), f"content-{index}", "passed", "2026-08-31T00:00:00Z"))
            result = build_context(root, source_paths=reports, period_start="2026-04-01", period_end="2026-06-30",
                cutoff="2026-09-05T00:00:00Z", edition="full", config_path="config/earnings_research.json",
                universe_path="config/earnings_universe.json", frozen_scope_paths=frozen_paths)
            self.assertTrue(result["model_execution_required"]); self.assertTrue(result["dependency_gate"]["eligible"])
            self.assertEqual(result["dependency_gate"]["expected_industries"], 5)
            manifest = json.loads((root / result["artifacts"][0]).read_text())
            self.assertEqual(manifest["cutoff"], "2026-09-05T00:00:00Z")
            self.assertEqual(manifest["scope"]["industry_cutoffs"]["i0"], "2026-09-01T00:00:00Z")
            self.assertEqual(manifest["scope"]["industry_cutoffs"]["i4"], "2026-09-05T00:00:00Z")
            first_scope = json.loads(frozen_paths[0].read_text())["scope_id"]
            for stage in ("gap_review", "industry", "challenge", "publication", "checker", "cloud"):
                ledger.set_stage(first_scope, stage, "completed")
            ledger.begin_revision(first_scope, "baseline", "2026-09-05T00:00:00Z")
            self.assertTrue(ledger.begin_revision(first_scope, "late-evidence", "2026-09-06T00:00:00Z"))
            with self.assertRaisesRegex(ValueError, "current scope revision"):
                build_context(root, source_paths=reports, period_start="2026-04-01", period_end="2026-06-30",
                    cutoff="2026-09-06T00:00:00Z", edition="full", config_path="config/earnings_research.json",
                    universe_path="config/earnings_universe.json", frozen_scope_paths=frozen_paths)
            state.close(); ledger.close()


if __name__ == "__main__":
    unittest.main()
