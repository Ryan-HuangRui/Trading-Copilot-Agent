#!/usr/bin/env python3
"""Build a true cross-industry quarterly synthesis context after all industry gates."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from earnings_common import (ROOT, atomic_write_json, canonical_json, ensure_inside, load_config, parse_time, read_json,
                             relative_to_root, safe_segment, sha256_bytes, sha256_file, stable_id, utc_now)
from earnings_state import EarningsState


def assess_market_dependencies(expected_industry_ids: list[str], rows: list[dict[str, Any]], *, requested_edition: str) -> dict[str, Any]:
    if requested_edition not in {"full", "stage"}: raise ValueError("invalid market edition")
    eligible = set()
    for row in rows:
        if row.get("industry_id") not in expected_industry_ids or row.get("checker_status") != "passed": continue
        if requested_edition == "stage" or row.get("edition") in {"full", "revision"}:
            eligible.add(row["industry_id"])
    missing = sorted(set(expected_industry_ids) - eligible)
    return {"expected_industries": len(expected_industry_ids), "eligible_industries": len(eligible),
            "missing_or_ineligible": missing, "requested_edition": requested_edition, "eligible": not missing}


def build_context(root: Path, *, source_paths: list[Path], period_start: str, period_end: str, cutoff: str,
                  edition: str, config_path: str, universe_path: str, run_id: str | None = None,
                  owner: str | None = None, lease_seconds: int | None = None,
                  frozen_scope_paths: list[Path] | None = None) -> dict[str, Any]:
    config, config_hash = load_config(root, config_path)
    frozen_scopes = [read_json(ensure_inside(path.resolve(), [root / "runtime/earnings/quarterly-scopes"]))
                     for path in (frozen_scope_paths or [])]
    if not frozen_scopes: raise ValueError("market context requires frozen quarterly scope registry")
    if any(sha256_bytes(canonical_json(row["industry"])) != row.get("frozen_universe_hash") for row in frozen_scopes):
        raise ValueError("frozen quarterly scope hash mismatch")
    state = EarningsState(root / config["paths"]["state"])
    try:
        if any(row["period_start"] != period_start or row["period_end"] != period_end for row in frozen_scopes):
            raise ValueError("frozen market scopes do not match requested period")
        expected_industries = [row["industry"]["industry_id"] for row in frozen_scopes]
        predecessors = []; reports = []
        seen_industries = set()
        for source in source_paths:
            path = ensure_inside(source.resolve(), [root / "report/earnings"]); digest = sha256_file(path); report = read_json(path)
            scope = report.get("scope") or {}; industry_id = scope.get("industry_id")
            if report.get("report_type") != "synthesis" or report.get("research_mode") != "quarterly":
                raise ValueError("market input must be a quarterly industry synthesis")
            if report.get("source_mode") != "live" or not parse_time(report.get("cutoff")) or parse_time(report["cutoff"]) > parse_time(cutoff):
                raise ValueError("market input must be accepted live research at or before the frozen cutoff")
            if scope.get("reporting_start") != period_start or scope.get("reporting_end") != period_end:
                raise ValueError("industry synthesis period differs from frozen market quarter")
            if industry_id in seen_industries: raise ValueError("duplicate industry synthesis dependency")
            seen_industries.add(industry_id)
            frozen_scope = next((item for item in frozen_scopes if item["industry"]["industry_id"] == industry_id), None)
            if frozen_scope is None or parse_time(report["cutoff"]) > parse_time(frozen_scope["cutoff"]):
                raise ValueError("industry synthesis exceeds its frozen scope cutoff")
            quarterly_db = root / "runtime/earnings/quarterly.sqlite"
            if quarterly_db.is_file():
                import sqlite3
                registry = sqlite3.connect(quarterly_db); registry.row_factory = sqlite3.Row
                try:
                    current = registry.execute("SELECT revision FROM quarterly_scopes WHERE scope_id=?",
                                               (frozen_scope["scope_id"],)).fetchone()
                    stage = registry.execute("SELECT state,artifact_path,artifact_sha256 FROM quarterly_stages WHERE scope_id=? AND stage='synthesis'",
                                             (frozen_scope["scope_id"],)).fetchone()
                finally: registry.close()
                if (not current or int(current["revision"]) != int(frozen_scope["revision"]) or not stage
                        or stage["state"] != "completed" or stage["artifact_path"] != relative_to_root(root, path)
                        or stage["artifact_sha256"] != digest):
                    raise ValueError("market input is not the completed synthesis for the current scope revision")
            row = state.db.execute("""SELECT a.* FROM report_artifacts a JOIN research_tasks t ON t.task_id=a.task_id
                WHERE a.path=? AND a.sha256=? AND a.source_mode='live' AND t.state='completed'""",
                (relative_to_root(root, path), digest)).fetchone()
            if not row: raise ValueError("market input is not a registered report artifact")
            if state.db.execute("""SELECT 1 FROM research_tasks WHERE task_type='synthesis' AND subject_id=?
                AND period_start=? AND period_end=? AND task_id!=? AND state IN ('queued','running','retryable_failed')""",
                (row["subject_id"], period_start, period_end, row["task_id"])).fetchone():
                raise ValueError("market input has a newer or pending industry research revision")
            publication = state.db.execute("SELECT * FROM publication_artifacts WHERE publication_type='industry' AND scope_id=? AND quarter_id=? ORDER BY version DESC LIMIT 1",
                                           (industry_id, frozen_scopes[0]["quarter_id"])).fetchone()
            checker_status = "missing"; report_edition = "stage"
            if publication:
                publication = dict(publication); publication_manifest = read_json(root / publication["manifest_path"])
                if sha256_file(root / publication["manifest_path"]) != publication["manifest_sha256"]:
                    raise ValueError("industry publication manifest changed after registration")
                source_hashes = {item["sha256"] for item in publication_manifest.get("sources", [])}
                pending = state.db.execute("SELECT 1 FROM publication_jobs WHERE series_key IN (SELECT series_key FROM publication_jobs WHERE publication_manifest_path=?) AND revision>(SELECT revision FROM publication_jobs WHERE publication_manifest_path=? LIMIT 1) AND state!='superseded'",
                                           (publication["manifest_path"], publication["manifest_path"])).fetchone()
                if (publication_manifest.get("publishable") is True and publication_manifest.get("checker", {}).get("status") == "passed"
                        and not publication_manifest.get("checker", {}).get("errors") and digest in source_hashes and not pending):
                    checker_status = "passed"; report_edition = publication["edition"]
            reports.append({"industry_id": industry_id, "edition": report_edition, "checker_status": checker_status})
            predecessors.append({"report_id": report["report_id"], "task_id": report["task_id"], "path": relative_to_root(root, path),
                                 "sha256": digest, "report_type": "synthesis", "industry_id": industry_id})
        gate = assess_market_dependencies(expected_industries, reports, requested_edition=edition)
        if not gate["eligible"]: return {"status": "skipped", "model_execution_required": False, "dependency_gate": gate,
                                         "reason": "all frozen industries have not passed the requested market-report gate"}
        expected_issuer_ids = []
        for frozen in frozen_scopes:
            for issuer in frozen["industry"]["issuers"]:
                row = state.db.execute("SELECT issuer_id FROM issuers WHERE symbol=?", (issuer["symbol"],)).fetchone()
                expected_issuer_ids.append(row[0] if row else f"unresolved:{issuer['symbol']}")
        document_keys = {(e.get("document_id"), e.get("document_version")) for report in [read_json(root / p["path"]) for p in predecessors]
                         for e in report.get("evidence", []) if e.get("document_id") and e.get("document_version")}
        documents = []
        for document_id, version in sorted(document_keys):
            row = state.db.execute("SELECT * FROM documents WHERE document_id=? AND version=?", (document_id, version)).fetchone()
            if not row: raise ValueError("industry evidence document is missing from state")
            if (dict(row).get("source_mode") != "live" or
                    (dict(row).get("accepted_at") and parse_time(dict(row)["accepted_at"]) > parse_time(cutoff))):
                raise ValueError("industry evidence provenance is stale, fixture, or post-cutoff")
            documents.append({key: dict(row).get(key) for key in ("document_id", "version", "issuer_id", "event_id", "form", "source_type",
                "source_url", "provider", "backend", "reporting_start", "reporting_end", "published_at", "accepted_at", "fetched_at",
                "public_time_precision", "original_path", "content_sha256", "source_mode", "supersedes")})
        input_basis = {"edition": edition, "period_start": period_start, "period_end": period_end, "cutoff": cutoff,
                       "industries": [(r["industry_id"], r["sha256"]) for r in predecessors],
                       "industry_cutoffs": sorted((row["industry"]["industry_id"], row["cutoff"]) for row in frozen_scopes),
                       "universe_hash": sha256_bytes(canonical_json([(row["scope_id"], row["frozen_universe_hash"]) for row in frozen_scopes]))}
        input_hash = sha256_bytes(canonical_json(input_basis)); profile = config["profiles"]["quarterly"]
        task_id, _ = state.enqueue_task(task_type="synthesis", subject_id=f"market:{period_end}", period_start=period_start,
            period_end=period_end, input_hash=input_hash, method_version="cross-industry-v1", source_mode="live", profile="quarterly",
            model=profile["model"], effort=profile["reasoning_effort"], max_attempts=int(config["budgets"]["max_task_attempts"]),
            dependencies=[r["task_id"] for r in predecessors])
        frozen = {**input_basis, "previous_artifacts": predecessors, "documents": documents}
        state.freeze_task_input(task_id, frozen, input_hash)
        safe_run = safe_segment(run_id or stable_id("market-run", task_id, input_hash), "run id")
        task = state.claim_task(task_id, owner=owner or f"market:{os.getpid()}:{safe_run}",
                                lease_seconds=lease_seconds or int(config["budgets"]["task_timeout_seconds"]))
        if task is None:
            current = state.db.execute("SELECT state FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()[0]
            return {"status": "skipped", "model_execution_required": False, "task_id": task_id, "task_state": current,
                    "reason": "unchanged cross-industry input is not claimable"}
        attempt = f"attempt-{task['attempts']}"; output = root / "report/earnings/market" / safe_segment(period_end, "period") / task_id / attempt
        run_dir = root / "runtime/earnings/runs" / safe_run / task_id / attempt
        coverage = {"expected_issuers": len(expected_issuer_ids), "disclosed_issuers": sum(read_json(root / r["path"])["coverage"]["disclosed_issuers"] for r in predecessors),
                    "fetched_issuers": sum(read_json(root / r["path"])["coverage"]["fetched_issuers"] for r in predecessors),
                    "researched_issuers": sum(read_json(root / r["path"])["coverage"]["researched_issuers"] for r in predecessors),
                    "key_missing_issuers": sorted({i for r in predecessors for i in read_json(root / r["path"])["coverage"].get("key_missing_issuers", [])})}
        manifest = {"schema_version": 1, "manifest_type": "earnings-role-input", "task_id": task_id, "run_id": safe_run,
            "lease": {"owner": task["lease_owner"], "expires_at": task["lease_expires_at"], "attempt": task["attempts"]},
            "assigned_role": "synthesis", "research_mode": "quarterly", "source_mode": "live", "cutoff": cutoff,
            "created_at": utc_now(), "method_version": "cross-industry-v1", "configuration_hash": config_hash, "input_hash": input_hash,
            "profile": {"name": "quarterly", "model": profile["model"], "effort": profile["reasoning_effort"], "usage": None},
            "scope": {"industry_id": "cross-industry", "market_label": "美股重点行业季度研究", "reporting_start": period_start,
                      "reporting_end": period_end, "universe_version": sha256_file(root / universe_path), "expected_issuer_ids": expected_issuer_ids,
                      "expected_industry_ids": expected_industries, "industry_cutoffs": dict(input_basis["industry_cutoffs"]),
                      "edition": edition},
            "coverage_audit": {"counts": coverage, "industry_dependency_gate": gate, "frozen_industry_ids": expected_industries,
                               "maturity": {"eligible_full": edition == "full" and gate["eligible"],
                                            "all_industries_accepted": gate["eligible"]}},
            "documents": documents, "previous_artifacts": predecessors, "company_artifacts": [],
            "predecessor_claim_ids": sorted({c["claim_id"] for r in predecessors for c in read_json(root / r["path"]).get("claims", [])}),
            "material_challenge_finding_ids": [],
            "permitted_outputs": {"json": relative_to_root(root, output / "synthesis_report.json"),
                                  "markdown": relative_to_root(root, output / "synthesis_report.md"),
                                  "completion": relative_to_root(root, run_dir / "completion.json")},
            "instructions": {"skill": ".codex/skills/tca-earnings-research/SKILL.md", "semantic_research_required": True,
              "cross_industry_independent_analysis": True, "compare_profit_transmission": True,
              "deduplicate_shared_customer_evidence": True, "notification_forbidden": True}}
        manifest["input_manifest_hash"] = sha256_bytes(canonical_json(manifest)); manifest_path = run_dir / "input-manifest.json"
        atomic_write_json(manifest_path, manifest); state.register_attempt_manifest(task_id, int(task["attempts"]), relative_to_root(root, manifest_path), sha256_file(manifest_path))
        return {"status": "success", "model_execution_required": True, "task_id": task_id,
                "artifacts": [relative_to_root(root, manifest_path)], "dependency_gate": gate}
    finally:
        state.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--config", default="config/earnings_research.json"); parser.add_argument("--universe", default="config/earnings_universe.json")
    parser.add_argument("--industry-report", action="append", required=True); parser.add_argument("--period-start", required=True)
    parser.add_argument("--period-end", required=True); parser.add_argument("--cutoff", required=True)
    parser.add_argument("--edition", choices=["full", "stage"], default="full"); parser.add_argument("--run-id"); parser.add_argument("--owner")
    parser.add_argument("--frozen-scope", action="append", required=True)
    parser.add_argument("--lease-seconds", type=int); parser.add_argument("--date")
    args = parser.parse_args(); root = Path(args.repo_root).resolve()
    try:
        result = build_context(root, source_paths=[root / p for p in args.industry_report], period_start=args.period_start,
            period_end=args.period_end, cutoff=args.cutoff, edition=args.edition, config_path=args.config, universe_path=args.universe,
            run_id=args.run_id, owner=args.owner, lease_seconds=args.lease_seconds,
            frozen_scope_paths=[root / p for p in args.frozen_scope])
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc: result = {"status": "failed", "reason": str(exc)}
    print(json.dumps({"schema_version": 1, "workflow": "earnings-market-context", "date": args.date, **result}, ensure_ascii=False))
    raise SystemExit(result["status"] == "failed")


if __name__ == "__main__": main()
