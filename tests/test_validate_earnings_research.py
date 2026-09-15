import copy
import hashlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from validate_earnings_research import validate_report, quote_in_original
from earnings_common import canonical_json, sha256_bytes


class ValidateEarningsResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.root = Path(self.temp.name)
        source = self.root / "raw_data/earnings/fixture/doc/v1/original.txt"
        source.parent.mkdir(parents=True); source.write_text("Revenue rose, while backlog declined.")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.manifest = {
            "schema_version": 1, "task_id": "task-1", "run_id": "run-1", "assigned_role": "company", "research_mode": "company",
            "source_mode": "fixture", "cutoff": "2026-09-14T02:00:00+00:00",
            "configuration_hash": "config-hash", "method_version": "earnings-method-v1", "input_hash": "event-hash",
            "scope": {"issuer_id": "fixture:x", "reporting_start": "2026-04-01", "reporting_end": "2026-06-30",
                      "expected_issuer_ids": ["fixture:x"]},
            "profile": {"model": "gpt-5.6-sol", "effort": "medium"},
            "documents": [{"document_id": "doc-1", "version": 1, "issuer_id": "fixture:x", "source_url": "fixture://doc-1",
                           "source_type": "fixture_filing", "original_path": str(source.relative_to(self.root)),
                           "content_sha256": digest, "source_mode": "fixture", "published_at": "2026-08-01", "accepted_at": "2026-08-01T20:00:00Z"}],
            "permitted_outputs": {"json": "report/earnings/companies/x/q/task-1/company_report.json",
                                  "markdown": "report/earnings/companies/x/q/task-1/company_report.md",
                                  "completion": "runtime/earnings/runs/run-1/task-1/completion.json"},
        }
        self.manifest["input_manifest_hash"] = sha256_bytes(canonical_json(self.manifest))
        self.report = {
            "schema_version": 1, "report_id": "report-1", "report_type": "company", "research_mode": "company", "task_id": "task-1", "run_id": "run-1",
            "scope": {"issuer_id": "fixture:x", "reporting_start": "2026-04-01", "reporting_end": "2026-06-30",
                      "expected_issuer_ids": ["fixture:x"]},
            "cutoff": self.manifest["cutoff"], "generated_at": "2026-09-14T02:10:00Z", "input_manifest_hash": self.manifest["input_manifest_hash"],
            "source_mode": "fixture", "provenance": {"provider": "fixture", "model": "gpt-5.6-sol", "effort": "medium",
                "method_version": "earnings-method-v1", "configuration_hash": "config-hash", "usage": None,
                "input_document_hashes": [digest], "predecessor_report_hashes": []},
            "evidence": [{"schema_version": 1, "evidence_id": "e-1", "issuer_id": "fixture:x", "document_id": "doc-1", "document_version": 1,
                "document_hash": digest, "source_locator": "line 1", "short_quote": "Revenue rose", "source_url": "fixture://doc-1",
                "public_timestamp": "2026-08-01T20:00:00Z", "reporting_start": "2026-04-01", "reporting_end": "2026-06-30",
                "evidence_kind": "fact", "industry_ids": [], "summary": "Revenue rose", "numeric_facts": [], "limitations": []}],
            "claims": [{"claim_id": "c-1", "statement": "Operations improved with a backlog counter-signal", "kind": "inference",
                "evidence_ids": ["e-1"], "direction": "mixed", "alternative_explanation": "mix or acquisition", "limitations": []}],
            "limitations": ["fixture only"], "completeness": {"status": "partial", "missing_inputs": ["call transcript"]},
            "thesis_state": "insufficient_data", "change_summary": "Mixed", "invalidation_conditions": ["revenue reverses"],
            "next_checks": ["backlog conversion"], "coverage": {"expected_issuers": 1, "disclosed_issuers": 1,
                "fetched_issuers": 1, "researched_issuers": 1, "key_missing_issuers": []},
        }

    def tearDown(self): self.temp.cleanup()

    def validate(self, report=None, manifest=None):
        report_path = self.root / "report/earnings/report.json"; manifest_path = self.root / "runtime/earnings/runs/run-1/manifest.json"
        report_path.parent.mkdir(parents=True, exist_ok=True); manifest_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report or self.report)); manifest_path.write_text(json.dumps(manifest or self.manifest))
        return validate_report(report_path, manifest_path, self.root)

    def rehash(self, manifest):
        manifest.pop("input_manifest_hash", None)
        manifest["input_manifest_hash"] = sha256_bytes(canonical_json(manifest))
        return manifest

    def test_valid_report_preserves_fixture_and_warning(self):
        result = self.validate()
        self.assertTrue(result["valid"], result["errors"])
        self.assertIn("optional issuer call transcript unavailable", result["warnings"])

    def test_html_quote_matches_inline_markup_entities_and_whitespace_only(self):
        html = '<p>Revenue was <span>$96.2</span>&nbsp;billion.</p><p>Demand increased.</p>'
        self.assertTrue(quote_in_original('Revenue was $96.2 billion.', html, '.htm'))
        self.assertFalse(quote_in_original('Revenue was $96.2 billion and demand increased.', html, '.htm'))
        self.assertFalse(quote_in_original('Revenue was $96.2 billion.', html, '.txt'))
        self.assertTrue(quote_in_original('represented 16 % of revenue', 'represented <span>16</span>% of revenue', '.htm'))
        self.assertTrue(quote_in_original('total of $ 105 billion', 'total of $<span>105</span> billion', '.htm'))
        self.assertFalse(quote_in_original('total of $ 106 billion', 'total of $<span>105</span> billion', '.htm'))

    def test_missing_citation_and_hash_are_errors(self):
        report = copy.deepcopy(self.report)
        report["claims"][0]["evidence_ids"] = ["not-found"]
        report["evidence"][0]["document_hash"] = "bad"
        result = self.validate(report)
        self.assertFalse(result["valid"])
        self.assertTrue(any("unresolved evidence" in item for item in result["errors"]))
        self.assertTrue(any("document hash mismatch" in item for item in result["errors"]))

    def test_invalid_instant_date_is_rejected_without_start_date(self):
        report = copy.deepcopy(self.report)
        report['evidence'][0]['reporting_start'] = None
        report['evidence'][0]['reporting_end'] = 'some day'
        self.assertTrue(any('invalid ISO date' in error for error in self.validate(report)['errors']))

    def test_challenge_requires_classified_unique_findings(self):
        manifest = copy.deepcopy(self.manifest); manifest['assigned_role'] = 'challenge'; self.rehash(manifest)
        report = copy.deepcopy(self.report); report.update(report_type='challenge', input_manifest_hash=manifest['input_manifest_hash'])
        report['findings'] = [{'finding_id': 'f1', 'materiality': 'critical', 'evidence_ids': ['e-1']}]
        errors = self.validate(report, manifest)['errors']
        self.assertTrue(any('invalid materiality' in error for error in errors))
        self.assertTrue(any('missing requested_check' in error for error in errors))

    def test_evidence_issuer_must_match_frozen_source_identity(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["documents"][0]["issuer_id"] = "fixture:other"
        self.rehash(manifest)
        report = copy.deepcopy(self.report); report["input_manifest_hash"] = manifest["input_manifest_hash"]
        result = self.validate(report=report, manifest=manifest)
        self.assertTrue(any("document issuer is outside" in item or "issuer does not match" in item for item in result["errors"]))

    def test_post_cutoff_document_is_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["documents"][0]["accepted_at"] = "2026-09-15T00:00:00Z"
        self.rehash(manifest)
        report = copy.deepcopy(self.report); report["input_manifest_hash"] = manifest["input_manifest_hash"]
        result = self.validate(report=report, manifest=manifest)
        self.assertTrue(any("post-cutoff" in item for item in result["errors"]))

    def test_fixed_coverage_denominator_and_key_missing_full(self):
        report = copy.deepcopy(self.report)
        report["scope"]["expected_issuer_ids"] = ["a", "b"]
        report["completeness"]["status"] = "full"
        report["coverage"]["key_missing_issuers"] = ["b"]
        result = self.validate(report)
        self.assertTrue(any("denominator" in item for item in result["errors"]))
        self.assertTrue(any("key missing" in item for item in result["errors"]))

    def test_output_path_traversal_in_manifest_source_is_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["documents"][0]["original_path"] = "../../etc/passwd"
        self.rehash(manifest)
        report = copy.deepcopy(self.report); report["input_manifest_hash"] = manifest["input_manifest_hash"]
        result = self.validate(report=report, manifest=manifest)
        self.assertTrue(any("outside allowed roots" in item for item in result["errors"]))

    def test_wrong_types_return_errors_instead_of_crashing(self):
        report = copy.deepcopy(self.report); report["scope"] = []; report["provenance"] = "bad"; report["evidence"] = {"bad": True}
        result = self.validate(report=report)
        self.assertFalse(result["valid"])
        self.assertIsInstance(result["errors"], list)

    def test_unknown_public_time_quote_mismatch_and_nonfinite_numeric_fail(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["documents"][0]["accepted_at"] = None; manifest["documents"][0]["published_at"] = None
        self.rehash(manifest)
        report = copy.deepcopy(self.report); report["input_manifest_hash"] = manifest["input_manifest_hash"]
        report["evidence"][0]["public_timestamp"] = None
        report["evidence"][0]["short_quote"] = "not in source"
        report["evidence"][0]["numeric_facts"] = [{"metric": "Revenue", "value": "NaN", "unit": "USD", "currency": "USD",
            "accounting_basis": "GAAP", "period": {"kind": "duration", "start": "2026-04-01", "end": "2026-06-30"},
            "derivation": "reported", "source_evidence_ids": ["e-1"]}]
        result = self.validate(report=report, manifest=manifest)
        self.assertTrue(any("unknown public timestamp" in item for item in result["errors"]))
        self.assertTrue(any("quote not found" in item for item in result["errors"]))
        self.assertTrue(any("must be finite" in item for item in result["errors"]))

    def test_full_quarter_requires_frozen_maturity(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["assigned_role"] = "industry"; manifest["research_mode"] = "quarterly"
        manifest["scope"] = {"industry_id": "test", "reporting_start": "2026-04-01", "reporting_end": "2026-06-30",
                             "universe_version": "u", "expected_issuer_ids": ["fixture:x"]}
        counts = {"expected_issuers": 1, "disclosed_issuers": 1, "fetched_issuers": 1,
                  "researched_issuers": 1, "key_missing_issuers": []}
        manifest["coverage_audit"] = {"counts": counts, "maturity": {"eligible_full": False}}
        self.rehash(manifest)
        report = copy.deepcopy(self.report); report.update(report_type="industry", research_mode="quarterly",
            input_manifest_hash=manifest["input_manifest_hash"], scope=copy.deepcopy(manifest["scope"]), coverage=counts,
            completeness={"status": "full", "missing_inputs": []})
        result = self.validate(report=report, manifest=manifest)
        self.assertTrue(any("maturity" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
