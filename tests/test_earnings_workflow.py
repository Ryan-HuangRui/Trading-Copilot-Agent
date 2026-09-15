import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EarningsWorkflowTests(unittest.TestCase):
    def run_cli(self, root, *args, success=True, env=None):
        proc = subprocess.run([sys.executable, str(ROOT / "script/trading_copilot.py"), *args, "--repo-root", str(root)],
                              cwd=ROOT, text=True, capture_output=True, env=env)
        if success and proc.returncode:
            self.fail(proc.stderr or proc.stdout)
        return proc, json.loads(proc.stdout)

    def write_role_report(self, root, manifest_path, *, role, claim_id, disputed_claim_id=None, material_finding_id=None):
        manifest = json.loads(manifest_path.read_text())
        document = manifest["documents"][0]
        expected = list((manifest.get("scope") or {}).get("expected_issuer_ids") or ["fixture:acme"])
        report = {
            "schema_version": 1, "report_id": f"test-{role}-report", "report_type": role, "research_mode": manifest["research_mode"],
            "task_id": manifest["task_id"], "run_id": manifest["run_id"], "scope": {**manifest["scope"], "expected_issuer_ids": expected},
            "cutoff": manifest["cutoff"], "generated_at": "2026-09-14T02:10:00Z", "input_manifest_hash": manifest["input_manifest_hash"],
            "source_mode": manifest["source_mode"], "provenance": {"provider": "fixture", "model": manifest["profile"]["model"],
                "effort": manifest["profile"]["effort"], "method_version": manifest["method_version"],
                "configuration_hash": manifest["configuration_hash"], "usage": None,
                "input_document_hashes": [row["content_sha256"] for row in manifest["documents"]] +
                                          [row["content_sha256"] for row in manifest.get("calculation_inputs", [])],
                "predecessor_report_hashes": [row["sha256"] for row in manifest.get("previous_artifacts", [])]},
            "evidence": [{"schema_version": 1, "evidence_id": f"{role}-evidence", "issuer_id": "fixture:acme", "document_id": document["document_id"],
                "document_version": document["version"], "document_hash": document["content_sha256"], "source_locator": "fixture line 1",
                "short_quote": "Revenue was USD 120 million", "source_url": document["source_url"],
                "public_timestamp": document["accepted_at"], "reporting_start": "2026-04-01", "reporting_end": "2026-06-30",
                "evidence_kind": "fact", "industry_ids": [], "summary": "Test-only fixture fact", "numeric_facts": [], "limitations": ["test fixture"]}],
            "claims": [{"claim_id": claim_id, "statement": "Test-only mixed evidence", "kind": "inference",
                "evidence_ids": [f"{role}-evidence"], "direction": "mixed", "alternative_explanation": "fixture construction", "limitations": ["not live"]}],
            "limitations": ["test-only fixture; not substantive research"], "completeness": {"status": "partial", "missing_inputs": ["live original"]}}
        if role in {"company", "industry", "synthesis"}:
            report.update(thesis_state="insufficient_data", change_summary="Test-only", invalidation_conditions=["live review"], next_checks=["live review"],
                coverage={"expected_issuers": len(expected), "disclosed_issuers": 1, "fetched_issuers": 1,
                          "researched_issuers": 1, "key_missing_issuers": []})
        if role == "challenge":
            report["findings"] = [{"finding_id": material_finding_id, "disputed_claim_id": disputed_claim_id,
                "evidence_ids": ["challenge-evidence"], "competing_explanation": "test contradiction", "materiality": "material",
                "requested_check": "reread original"}]
        if role == "synthesis":
            report["challenge_dispositions"] = [{"finding_id": material_finding_id, "disposition": "unresolved",
                "rationale": "fixture cannot resolve", "supporting_evidence_ids": ["synthesis-evidence"]}]
        report_path = root / manifest["permitted_outputs"]["json"]
        report_path.parent.mkdir(parents=True, exist_ok=True); report_path.write_text(json.dumps(report))
        return report_path

    def test_company_record_then_industry_dependency_and_isolated_paths(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config/earnings_research.json"; config.parent.mkdir(parents=True)
            shutil.copy(ROOT / "config/earnings_research.json", config)
            fixture = root / "tests/fixtures/earnings/sample_bundle.json"; fixture.parent.mkdir(parents=True)
            shutil.copy(ROOT / "tests/fixtures/earnings/sample_bundle.json", fixture)
            universe = root / "config/universe.json"
            universe.write_text(json.dumps({"schema_version": 1, "universe_id": "test",
                "industries": [{"industry_id": "test-industry", "key_symbols": ["ACME"], "issuers": [{"symbol": "ACME"}]}]}))
            _, collected = self.run_cli(root, "earnings-collect", "--config", str(config), "--mode", "offline",
                "--input", str(fixture), "--cutoff", "2026-09-14T02:00:00Z")
            self.assertEqual(collected["summary"]["queued_tasks"], 1)
            # Context must use the issuer identity frozen with the task, not a later mutable DB row.
            db = sqlite3.connect(root / "runtime/earnings/state.sqlite")
            db.execute("UPDATE issuers SET symbol='LATER' WHERE issuer_id='fixture:acme'"); db.commit(); db.close()
            _, context = self.run_cli(root, "earnings-context", "--config", str(config), "--universe", str(universe),
                "--cutoff", "2026-09-14T02:00:00Z", "--run-id", "company-run", "--limit", "1")
            manifest_path = root / context["manifests"][0]
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["scope"]["symbol"], "ACME")
            db = sqlite3.connect(root / "runtime/earnings/state.sqlite")
            db.execute("UPDATE issuers SET symbol='ACME' WHERE issuer_id='fixture:acme'"); db.commit(); db.close()
            report_path = root / manifest["permitted_outputs"]["json"]
            report_path.parent.mkdir(parents=True)
            document = manifest["documents"][0]
            report = {
                "schema_version": 1, "report_id": "test-company-report", "report_type": "company", "research_mode": "company",
                "task_id": manifest["task_id"], "run_id": manifest["run_id"], "scope": {**manifest["scope"], "expected_issuer_ids": ["fixture:acme"]},
                "cutoff": manifest["cutoff"], "generated_at": "2026-09-14T02:10:00Z", "input_manifest_hash": manifest["input_manifest_hash"],
                "source_mode": "fixture", "provenance": {"provider": "fixture", "model": manifest["profile"]["model"],
                    "effort": manifest["profile"]["effort"], "method_version": manifest["method_version"],
                    "configuration_hash": manifest["configuration_hash"], "usage": None,
                    "input_document_hashes": [row["content_sha256"] for row in manifest["documents"]] +
                                             [row["content_sha256"] for row in manifest.get("calculation_inputs", [])],
                    "predecessor_report_hashes": []},
                "evidence": [{"schema_version": 1, "evidence_id": "test-evidence", "issuer_id": "fixture:acme", "document_id": document["document_id"],
                    "document_version": document["version"], "document_hash": document["content_sha256"], "source_locator": "fixture line 1",
                    "short_quote": "Revenue was USD 120 million", "source_url": document["source_url"],
                    "public_timestamp": document["accepted_at"], "reporting_start": "2026-04-01", "reporting_end": "2026-06-30",
                "evidence_kind": "fact", "industry_ids": [], "summary": "Test-only fixture fact", "numeric_facts": [], "limitations": ["test fixture"]}],
                "claims": [{"claim_id": "test-claim", "statement": "Test-only mixed evidence", "kind": "inference",
                    "evidence_ids": ["test-evidence"], "direction": "mixed", "alternative_explanation": "fixture construction", "limitations": ["not live"]}],
                "limitations": ["test-only fixture; not substantive research"], "completeness": {"status": "partial", "missing_inputs": ["live original"]},
                "thesis_state": "insufficient_data", "change_summary": "Test-only", "invalidation_conditions": ["live review"], "next_checks": ["live review"],
                "coverage": {"expected_issuers": 1, "disclosed_issuers": 1, "fetched_issuers": 1, "researched_issuers": 1, "key_missing_issuers": []}}
            report_path.write_text(json.dumps(report))
            db = sqlite3.connect(root / "runtime/earnings/state.sqlite")
            db.execute("UPDATE research_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?", (manifest["task_id"],)); db.commit(); db.close()
            expired_proc, expired_payload = self.run_cli(root, "earnings-record", "--report", str(report_path),
                                                         "--manifest", str(manifest_path), success=False)
            self.assertNotEqual(expired_proc.returncode, 0)
            self.assertIn("lease expired", expired_payload["reason"])
            db = sqlite3.connect(root / "runtime/earnings/state.sqlite")
            db.execute("UPDATE research_tasks SET lease_expires_at=? WHERE task_id=?", (manifest["lease"]["expires_at"], manifest["task_id"])); db.commit(); db.close()
            _, recorded = self.run_cli(root, "earnings-record", "--report", str(report_path), "--manifest", str(manifest_path))
            self.assertEqual(recorded["status"], "success")
            _, repeated = self.run_cli(root, "earnings-record", "--report", str(report_path), "--manifest", str(manifest_path))
            self.assertTrue(repeated["idempotent"])
            _, industry = self.run_cli(root, "earnings-industry-context", "--config", str(config), "--universe", str(universe),
                "--industry", "test-industry", "--role", "industry", "--mode", "quarterly",
                "--period-start", "2026-04-01", "--period-end", "2026-06-30", "--cutoff", "2026-09-14T02:00:00Z")
            industry_manifest_path = root / industry["artifacts"][0]
            industry_manifest = json.loads(industry_manifest_path.read_text())
            self.assertEqual(industry_manifest["coverage_audit"]["researched_issuer_ids"], ["fixture:acme"])
            self.assertEqual(industry_manifest["source_mode"], "fixture")
            self.assertEqual(industry_manifest["profile"]["model"], "gpt-6-astra")
            for path in industry_manifest["permitted_outputs"].values():
                resolved = (root / path).resolve()
                self.assertTrue(str(resolved).startswith(str(root.resolve())))
            industry_report = self.write_role_report(root, industry_manifest_path, role="industry", claim_id="industry-claim")
            self.run_cli(root, "earnings-record", "--report", str(industry_report), "--manifest", str(industry_manifest_path))
            _, challenge = self.run_cli(root, "earnings-industry-context", "--config", str(config), "--universe", str(universe),
                "--industry", "test-industry", "--role", "challenge", "--mode", "quarterly", "--period-start", "2026-04-01",
                "--period-end", "2026-06-30", "--cutoff", "2026-09-14T02:00:00Z", "--predecessor-report", str(industry_report))
            challenge_manifest_path = root / challenge["artifacts"][0]
            challenge_report = self.write_role_report(root, challenge_manifest_path, role="challenge", claim_id="challenge-claim",
                                                       disputed_claim_id="industry-claim", material_finding_id="finding-1")
            self.run_cli(root, "earnings-record", "--report", str(challenge_report), "--manifest", str(challenge_manifest_path))
            _, synthesis = self.run_cli(root, "earnings-industry-context", "--config", str(config), "--universe", str(universe),
                "--industry", "test-industry", "--role", "synthesis", "--mode", "quarterly", "--period-start", "2026-04-01",
                "--period-end", "2026-06-30", "--cutoff", "2026-09-14T02:00:00Z",
                "--predecessor-report", str(industry_report), "--predecessor-report", str(challenge_report))
            synthesis_manifest_path = root / synthesis["artifacts"][0]
            synthesis_manifest = json.loads(synthesis_manifest_path.read_text())
            self.assertEqual(synthesis_manifest["material_challenge_finding_ids"], ["finding-1"])
            synthesis_report = self.write_role_report(root, synthesis_manifest_path, role="synthesis", claim_id="synthesis-claim",
                                                       material_finding_id="finding-1")
            _, synthesis_record = self.run_cli(root, "earnings-record", "--report", str(synthesis_report), "--manifest", str(synthesis_manifest_path))
            self.assertEqual(synthesis_record["status"], "success")

    def test_unsafe_run_id_is_rejected_before_any_manifest_write(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config/earnings_research.json"; config.parent.mkdir(parents=True)
            shutil.copy(ROOT / "config/earnings_research.json", config)
            universe = root / "config/earnings_universe.json"; shutil.copy(ROOT / "config/earnings_universe.json", universe)
            proc, payload = self.run_cli(root, "earnings-context", "--config", str(config), "--universe", str(universe),
                "--cutoff", "2026-09-14T02:00:00Z", "--run-id", "../../escape", success=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("unsafe run id", payload["reason"])
            self.assertFalse((root.parent / "escape").exists())

    def test_live_mode_requires_real_sec_contact_and_never_falls_back(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            env = dict(os.environ); env.pop("TCA_SEC_USER_AGENT", None)
            config = root / "config/earnings_research.json"; config.parent.mkdir(parents=True)
            shutil.copy(ROOT / "config/earnings_research.json", config)
            proc, payload = self.run_cli(root, "earnings-collect", "--config", str(config),
                "--mode", "live", "--symbol", "NVDA", "--cutoff", "2026-09-14T02:00:00Z", success=False, env=env)
            # CI deliberately has no contact identity; a network or fixture fallback must not occur.
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(payload["status"], "failed")
            self.assertIn("TCA_SEC_USER_AGENT", payload["reason"])

    def test_same_second_superseding_input_rejects_old_running_completion(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config/earnings_research.json"; config.parent.mkdir(parents=True)
            shutil.copy(ROOT / "config/earnings_research.json", config)
            universe = root / "config/earnings_universe.json"; shutil.copy(ROOT / "config/earnings_universe.json", universe)
            fixture = root / "runtime/earnings/input.json"; fixture.parent.mkdir(parents=True)
            payload = json.loads((ROOT / "tests/fixtures/earnings/sample_bundle.json").read_text()); fixture.write_text(json.dumps(payload))
            self.run_cli(root, "earnings-collect", "--config", str(config), "--mode", "offline", "--input", str(fixture),
                         "--cutoff", "2026-09-14T02:00:00Z")
            _, context = self.run_cli(root, "earnings-context", "--config", str(config), "--universe", str(universe),
                                      "--cutoff", "2026-09-14T02:00:00Z", "--run-id", "old-running", "--limit", "1")
            old_manifest_path = root / context["manifests"][0]
            old_report = self.write_role_report(root, old_manifest_path, role="company", claim_id="old-claim")
            payload["documents"][0]["content"] += " amended"
            fixture.write_text(json.dumps(payload))
            self.run_cli(root, "earnings-collect", "--config", str(config), "--mode", "offline", "--input", str(fixture),
                         "--cutoff", "2026-09-14T02:00:00Z")
            db = sqlite3.connect(root / "runtime/earnings/state.sqlite")
            db.execute("UPDATE research_tasks SET created_at='2026-09-14T02:00:00+00:00'"); db.commit(); db.close()
            proc, result = self.run_cli(root, "earnings-record", "--report", str(old_report), "--manifest", str(old_manifest_path), success=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("superseded", result["reason"])

    def test_amended_company_task_can_register_with_legitimate_prior_comparison(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config/earnings_research.json"; config.parent.mkdir(parents=True)
            shutil.copy(ROOT / "config/earnings_research.json", config)
            universe = root / "config/earnings_universe.json"; shutil.copy(ROOT / "config/earnings_universe.json", universe)
            fixture = root / "runtime/earnings/input.json"; fixture.parent.mkdir(parents=True)
            payload = json.loads((ROOT / "tests/fixtures/earnings/sample_bundle.json").read_text()); fixture.write_text(json.dumps(payload))
            self.run_cli(root, "earnings-collect", "--config", str(config), "--mode", "offline", "--input", str(fixture),
                         "--cutoff", "2026-09-14T02:00:00Z")
            _, first_context = self.run_cli(root, "earnings-context", "--config", str(config), "--universe", str(universe),
                                            "--cutoff", "2026-09-14T02:00:00Z", "--run-id", "prior-run", "--limit", "1")
            first_manifest = root / first_context["manifests"][0]
            first_report = self.write_role_report(root, first_manifest, role="company", claim_id="prior-claim")
            self.run_cli(root, "earnings-record", "--report", str(first_report), "--manifest", str(first_manifest))
            payload["documents"][0]["content"] += " amended"
            fixture.write_text(json.dumps(payload))
            self.run_cli(root, "earnings-collect", "--config", str(config), "--mode", "offline", "--input", str(fixture),
                         "--cutoff", "2026-09-14T02:00:00Z")
            _, amended_context = self.run_cli(root, "earnings-context", "--config", str(config), "--universe", str(universe),
                                              "--cutoff", "2026-09-14T02:00:00Z", "--run-id", "amended-run", "--limit", "1")
            amended_manifest_path = root / amended_context["manifests"][0]
            amended_manifest = json.loads(amended_manifest_path.read_text())
            self.assertEqual([row["report_id"] for row in amended_manifest["previous_artifacts"]], ["test-company-report"])
            amended_report = self.write_role_report(root, amended_manifest_path, role="company", claim_id="amended-claim")
            amended_payload = json.loads(amended_report.read_text()); amended_payload["report_id"] = "test-company-report-amended"
            amended_report.write_text(json.dumps(amended_payload))
            _, result = self.run_cli(root, "earnings-record", "--report", str(amended_report), "--manifest", str(amended_manifest_path))
            self.assertEqual(result["status"], "success")


if __name__ == "__main__":
    unittest.main()
