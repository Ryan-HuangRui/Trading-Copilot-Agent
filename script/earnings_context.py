#!/usr/bin/env python3
"""Claim eligible company tasks and write immutable Codex role input manifests."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

from earnings_common import (ROOT, atomic_write_json, canonical_json, company_research_configuration_hash, confined_path, configuration_path, emit, envelope, ensure_inside, legacy_company_configuration_compatible, load_config,
                             parse_time, read_json, relative_to_root, resolve_path, sha256_bytes,
                             sha256_file, safe_segment, shanghai_date, stable_id, utc_now)
from earnings_financials import derive_standalone_facts, extract_sec_company_facts
from earnings_state import EarningsState


class NotYetPublic(RuntimeError):
    pass


class LegacyConfigurationPending(RuntimeError):
    pass


def _eligible_document(row: dict[str, Any], cutoff: datetime) -> tuple[bool, str | None]:
    public = parse_time(row.get("accepted_at") or row.get("published_at"))
    if public is None:
        return False, "public time unknown"
    if row.get("public_time_precision") == "day" and public.date() == cutoff.date():
        return False, "same-day public time is unprovable"
    if public > cutoff:
        return False, "published after cutoff"
    return True, None


def bounded_financial_history(facts: list[dict[str, Any]], anchor: date, limit: int = 500) -> tuple[list[dict[str, Any]], bool]:
    window_start = anchor - timedelta(days=8 * 93)
    selected = [fact for fact in facts if (fact.get("period") or {}).get("end")
                and window_start <= date.fromisoformat(fact["period"]["end"]) <= anchor]
    selected.sort(key=lambda fact: ((fact.get("period") or {}).get("end", ""), fact.get("metric", "")), reverse=True)
    return selected[:limit], len(selected) > limit


def _retry_feedback(root: Path, state: EarningsState, task: dict[str, Any]) -> dict[str, Any] | None:
    """Preserve actionable validator feedback and immutable prior draft references."""
    if int(task.get("attempts", 0)) <= 1:
        return None
    feedback: dict[str, Any] = {"reason": task.get("error"), "prior_attempts": []}
    rows = state.db.execute(
        """SELECT attempt,path,sha256 FROM task_attempt_manifests
        WHERE task_id=? AND attempt<? ORDER BY attempt DESC""",
        (task["task_id"], int(task["attempts"])),
    ).fetchall()
    for row in rows[:2]:
        manifest_path = resolve_path(root, row["path"])
        item: dict[str, Any] = {"attempt": row["attempt"], "manifest_path": row["path"],
                               "manifest_sha256": row["sha256"]}
        result_path = manifest_path.parent / "runner-result.json"
        if result_path.exists():
            result = read_json(result_path)
            item["result"] = {key: result.get(key) for key in ("status", "error", "validation_errors")}
        if manifest_path.exists():
            prior = read_json(manifest_path)
            for kind in ("json", "markdown"):
                value = (prior.get("permitted_outputs") or {}).get(kind)
                if value:
                    path = resolve_path(root, value)
                    if path.exists():
                        item[f"{kind}_path"] = value
                        item[f"{kind}_sha256"] = sha256_file(path)
        feedback["prior_attempts"].append(item)
    return feedback


def _manifest_for_task(root: Path, state: EarningsState, task: dict[str, Any], cutoff: datetime,
                       config_hash: str, universe_path: Path, run_id: str, config: dict[str, Any]) -> dict[str, Any]:
    frozen = state.task_input(task["task_id"])
    frozen_config_hash = frozen.get("configuration_hash")
    if not isinstance(frozen_config_hash, str) or not frozen_config_hash:
        raise ValueError("frozen task configuration hash is missing")
    frozen_basis = frozen.get("configuration_basis")
    if isinstance(frozen_basis, dict):
        if sha256_bytes(canonical_json(frozen_basis)) != frozen_config_hash:
            raise ValueError("frozen task semantic configuration hash mismatch")
    else:
        profile = (config.get("profiles") or {}).get("daily") or {}
        legacy_profile_matches = (task.get("model"), task.get("effort"), task.get("method_version")) == (
            profile.get("model"), profile.get("reasoning_effort"), "earnings-method-v1")
        if (not legacy_profile_matches
                or not legacy_company_configuration_compatible(root, frozen_config_hash, config)):
            raise LegacyConfigurationPending(
                "legacy task configuration/profile is not backed by a compatible registered raw snapshot")
    # Running tasks retain the configuration hash captured when they were created.
    # Current delivery/publication tuning cannot invalidate their evidence input.
    config_hash = frozen_config_hash
    event = frozen.get("event") or {}
    issuer = frozen.get("issuer") or {}
    if event.get("event_id") != task["subject_id"] or not issuer or event.get("issuer_id") != issuer.get("issuer_id"):
        raise ValueError("frozen event/issuer identity is missing or inconsistent")
    rows = list(frozen.get("documents") or [])
    documents: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for row in rows:
        eligible, reason = _eligible_document(row, cutoff)
        path = ensure_inside(resolve_path(root, row["original_path"]), [root / "raw_data" / "earnings"])
        if not eligible:
            missing.append({"document_id": row["document_id"], "reason": str(reason)})
            continue
        if not path.exists() or sha256_file(path) != row["content_sha256"]:
            raise ValueError(f"source missing or hash mismatch: {row['original_path']}")
        documents.append({key: row.get(key) for key in (
            "document_id", "version", "issuer_id", "form", "source_type", "source_url", "provider", "backend",
            "reporting_start", "reporting_end", "published_at", "accepted_at", "fetched_at",
            "public_time_precision", "original_path", "content_sha256", "source_mode", "supersedes")})
    if not documents:
        if rows and all(reason["reason"] == "published after cutoff" for reason in missing):
            raise NotYetPublic("all frozen inputs are after cutoff")
        raise ValueError("no eligible document with provable public availability; model execution forbidden")
    facts_documents = list(frozen.get("companyfacts") or [])
    normalized: list[dict[str, Any]] = []
    calculation_inputs: list[dict[str, Any]] = []
    for row in facts_documents:
        path = ensure_inside(resolve_path(root, row["original_path"]), [root / "raw_data" / "earnings"])
        if path.exists() and sha256_file(path) == row["content_sha256"]:
            try:
                reported = [fact for fact in extract_sec_company_facts(read_json(path), allowed_tags=None)
                            if fact.get("filed") and parse_time(fact["filed"]).date() < cutoff.date()]
                anchor = date.fromisoformat(event["reporting_end"]) if event.get("reporting_end") else cutoff.date()
                # Filter before period derivation: the endpoint contains decades of repeated facts.
                needed_metrics = {"us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                    "us-gaap:NetIncomeLoss", "us-gaap:OperatingIncomeLoss", "us-gaap:CashAndCashEquivalentsAtCarryingValue",
                    "us-gaap:NetCashProvidedByUsedInOperatingActivities"}
                reported = [fact for fact in reported if (fact.get("metric") in needed_metrics or fact.get("custom_tag"))
                    and anchor - timedelta(days=1100) <= date.fromisoformat(fact["period"]["end"]) <= anchor]
                normalized = derive_standalone_facts(reported)
                calculation_inputs.append({key: row.get(key) for key in (
                    "document_id", "version", "issuer_id", "source_url", "provider", "backend", "fetched_at",
                    "original_path", "content_sha256", "source_mode", "metadata_json")})
                calculation_inputs[-1]["limitations"] = [
                    "SEC Company Facts is a current aggregate endpoint, not a point-in-time archive; filed-date filtering alone does not prove historical availability",
                    "Historical conclusions require citation to the contemporaneous original filing in manifest.documents",
                ]
                missing.append({"document_id": row["document_id"],
                                "reason": "aggregate Company Facts is not point-in-time evidence; normalized values require original filing confirmation"})
                if any(fact.get("filed") and parse_time(fact["filed"]).date() == cutoff.date()
                       for fact in extract_sec_company_facts(read_json(path), allowed_tags=None)):
                    missing.append({"document_id": row["document_id"], "reason": "same-day date-only Company Facts values excluded: intraday public time unprovable"})
                normalized = [fact for fact in normalized if fact.get("metric") in {
                    "us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:NetIncomeLoss",
                    "us-gaap:OperatingIncomeLoss", "us-gaap:CashAndCashEquivalentsAtCarryingValue", "us-gaap:NetCashProvidedByUsedInOperatingActivities",
                } or fact.get("custom_tag")]
            except (ValueError, json.JSONDecodeError):
                missing.append({"document_id": row["document_id"], "reason": "company facts normalization failed"})
    # Bound model input to the event's operating history; the full aggregate stays archived.
    anchor = date.fromisoformat(event["reporting_end"]) if event.get("reporting_end") else cutoff.date()
    normalized, truncated = bounded_financial_history(normalized, anchor)
    if truncated:
        missing.append({"document_id": "companyfacts", "reason": "normalized context capped at 500 recent facts; full originals remain required for omitted metrics"})
    previous_artifacts: list[dict[str, Any]] = []
    for artifact in state.db.execute(
        """SELECT a.* FROM report_artifacts a JOIN research_tasks t ON t.task_id=a.task_id
        WHERE a.report_type='company' AND t.subject_id IN (SELECT event_id FROM earnings_events WHERE issuer_id=?)
        AND a.task_id!=? AND t.state='completed' AND NOT EXISTS(
          SELECT 1 FROM research_tasks n WHERE n.rowid>t.rowid AND n.task_type=t.task_type
          AND n.subject_id=t.subject_id AND n.period_start IS t.period_start AND n.period_end IS t.period_end
          AND n.source_mode=t.source_mode AND n.task_id!=? AND n.state IN ('queued','running','completed','retryable_failed'))
        ORDER BY a.period_end DESC,a.created_at DESC LIMIT 4""", (event["issuer_id"], task["task_id"], task["task_id"]),
    ):
        artifact = dict(artifact)
        artifact_path = ensure_inside(resolve_path(root, artifact["path"]), [root / "report" / "earnings"])
        if not artifact_path.exists() or sha256_file(artifact_path) != artifact["sha256"]:
            raise ValueError(f"previous company artifact hash mismatch: {artifact['path']}")
        prior = read_json(artifact_path)
        if parse_time(prior.get("cutoff")) > cutoff:
            continue
        previous_artifacts.append({"report_id": artifact["report_id"], "task_id": artifact["task_id"], "path": artifact["path"],
                                   "sha256": artifact["sha256"], "report_type": "company", "cutoff": prior.get("cutoff")})
    safe_run = safe_segment(run_id, "run id")
    safe_task = safe_segment(task["task_id"], "task id")
    safe_issuer = safe_segment(event["issuer_id"], "issuer id").replace(":", "-")
    period_segment = safe_segment(event["reporting_end"] or "unresolved", "reporting period")
    attempt_segment = f"attempt-{int(task['attempts'])}"
    output_dir = confined_path(root, root / "runtime" / "earnings" / "runs" / safe_run / safe_task / attempt_segment, "runtime/earnings/runs")
    output_json = confined_path(root, root / "report" / "earnings" / "companies" / safe_issuer / period_segment / safe_task / attempt_segment / "company_report.json", "report/earnings")
    output_md = output_json.with_suffix(".md")
    manifest = {
        "schema_version": 1,
        "manifest_type": "earnings-role-input",
        "task_id": task["task_id"],
        "run_id": run_id,
        "lease": {"owner": task["lease_owner"], "expires_at": task["lease_expires_at"], "attempt": task["attempts"]},
        "assigned_role": "company",
        "research_mode": "company",
        "source_mode": task["source_mode"],
        "cutoff": cutoff.isoformat(),
        "created_at": utc_now(),
        "scope": {"issuer_id": event["issuer_id"], "symbol": issuer["symbol"], "event_id": event["event_id"],
                  "reporting_start": event["reporting_start"], "reporting_end": event["reporting_end"],
                  "universe_version": sha256_file(universe_path)},
        "profile": {"name": task["profile"], "model": task["model"], "effort": task["effort"], "usage": None},
        "method_version": task["method_version"],
        "configuration_hash": config_hash,
        "input_hash": task["input_hash"],
        "documents": documents,
        "calculation_inputs": calculation_inputs,
        "normalized_financials": normalized,
        "missing_inputs": missing,
        "previous_artifacts": previous_artifacts,
        "retry_feedback": _retry_feedback(root, state, task),
        "permitted_outputs": {"json": relative_to_root(root, output_json), "markdown": relative_to_root(root, output_md),
                              "completion": relative_to_root(root, output_dir / "completion.json")},
        "instructions": {
            "skill": ".codex/skills/tca-earnings-research/SKILL.md",
            "role_prompt": "ops/cc-connect/tca-earnings-role.prompt.md",
            "semantic_research_required": True,
            "deterministic_placeholder_forbidden": True,
            "companyfacts_not_point_in_time": True,
            "original_filing_evidence_required_for_historical_facts": True,
            "notification_forbidden": True,
        },
    }
    manifest["input_manifest_hash"] = sha256_bytes(canonical_json(manifest))
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build immutable company earnings research role contexts")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--config", default="config/earnings_research.json")
    parser.add_argument("--universe", default="config/earnings_universe.json")
    parser.add_argument("--state", default="runtime/earnings/state.sqlite")
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--date")
    parser.add_argument("--run-id")
    parser.add_argument("--owner")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--lease-seconds", type=int)
    parser.add_argument("--company-tier", choices=["current", "history"])
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = Path(args.repo_root).resolve()
    config_path = configuration_path(root, args.config)
    config, _ = load_config(root, str(config_path))
    config_hash = company_research_configuration_hash(config)
    cutoff = parse_time(args.cutoff)
    if cutoff is None:
        raise ValueError("cutoff is required")
    universe = confined_path(root, args.universe, "config")
    if not universe.exists():
        raise ValueError(f"universe missing: {universe}")
    run_id = safe_segment(args.run_id or stable_id("run", "company", cutoff.isoformat(), utc_now()), "run id")
    owner = args.owner or f"manual:{os.getpid()}:{run_id}"
    limit = args.limit or int(config["budgets"].get("companies_per_batch", 5))
    lease = args.lease_seconds or int(config["budgets"].get("task_timeout_seconds", 1800))
    state = EarningsState(confined_path(root, args.state, "runtime/earnings"))
    manifests: list[str] = []
    failures: list[dict[str, Any]] = []
    try:
        universe_payload = read_json(universe)
        key_symbols = [symbol for industry in universe_payload.get("industries", [])
                       for symbol in industry.get("key_symbols", [])]
        tasks = state.claim_tasks(owner=owner, limit=limit, lease_seconds=lease, task_type="company",
                                  company_tier=args.company_tier, priority_symbols=key_symbols)
        for task in tasks:
            try:
                manifest = _manifest_for_task(root, state, task, cutoff, config_hash, universe, run_id, config)
                path = confined_path(root, resolve_path(root, manifest["permitted_outputs"]["completion"]).parent / "input-manifest.json", "runtime/earnings/runs")
                encoded_hash = sha256_bytes(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
                if path.exists():
                    if sha256_file(path) != encoded_hash:
                        raise ValueError(f"immutable manifest differs: {path}")
                else:
                    atomic_write_json(path, manifest)
                state.register_attempt_manifest(task["task_id"], int(task["attempts"]), relative_to_root(root, path), sha256_file(path))
                manifests.append(relative_to_root(root, path))
            except NotYetPublic as exc:
                state.release_task(task["task_id"], str(exc))
                failures.append({"task_id": task["task_id"], "error": str(exc), "pending_publication": True})
            except LegacyConfigurationPending as exc:
                state.release_task(task["task_id"], str(exc))
                failures.append({"task_id": task["task_id"], "error": str(exc),
                                 "pending_publication": False, "pending_configuration_migration": True})
            except Exception as exc:
                state.fail_task(task["task_id"], str(exc), retryable=False)
                failures.append({"task_id": task["task_id"], "error": str(exc), "pending_publication": False})
        payload = envelope("earnings-context", args.date or shanghai_date(), run_id=run_id,
                           cutoff=cutoff.isoformat(), claimed_tasks=len(tasks), manifests=manifests, failures=failures,
                           model_execution_required=bool(manifests))
        payload["artifacts"] = manifests
        if not tasks:
            payload.update(status="skipped", skipped=True, reason="no eligible changed company task; model must not be invoked")
        elif not manifests and failures and all(row.get("pending_publication") for row in failures):
            payload.update(status="skipped", skipped=True, reason="all frozen inputs are after cutoff; model must not be invoked")
        elif not manifests and failures and all(row.get("pending_configuration_migration") for row in failures):
            payload.update(status="skipped", skipped=True,
                           reason="legacy configuration snapshot registration is required; model must not be invoked")
        elif not manifests and failures:
            payload.update(status="failed", reason="all claimed tasks had invalid or unprovable frozen inputs; model must not be invoked")
            emit(payload, 1)
        elif failures:
            payload["reason"] = "no model execution for tasks with unavailable, unprovable, or invalid frozen inputs" if not manifests else "some task contexts failed in isolation"
        emit(payload)
    finally:
        state.close()


if __name__ == "__main__":
    main()
