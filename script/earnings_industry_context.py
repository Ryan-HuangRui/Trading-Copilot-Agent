#!/usr/bin/env python3
"""Prepare manual industry, challenge and synthesis role contexts from registered evidence."""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
from typing import Any

from earnings_common import (ROOT, atomic_write_json, canonical_json, confined_path, configuration_path, emit, envelope, ensure_inside, load_config, parse_time,
                             read_json, relative_to_root, resolve_path, safe_segment, sha256_bytes, sha256_file, shanghai_date, stable_id, utc_now)
from earnings_state import EarningsState


def _industry(universe: dict[str, Any], industry_id: str) -> dict[str, Any]:
    for industry in universe.get("industries", []):
        if industry.get("industry_id") == industry_id:
            return industry
    raise ValueError(f"industry not found in universe: {industry_id}")


def _profile(config: dict[str, Any], role: str, mode: str) -> tuple[str, str, str]:
    name = "review" if role == "challenge" else ("quarterly" if mode == "quarterly" else "daily")
    row = config["profiles"][name]
    return name, row["model"], row["reasoning_effort"]


def _registered_report(state: EarningsState, root: Path, path_text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = ensure_inside(resolve_path(root, path_text), [root / "report" / "earnings"])
    if not path.exists():
        raise ValueError(f"predecessor report missing: {path_text}")
    digest = sha256_file(path)
    row = state.db.execute("SELECT * FROM report_artifacts WHERE path=? AND sha256=?", (relative_to_root(root, path), digest)).fetchone()
    if not row:
        raise ValueError(f"predecessor report is not registered or hash differs: {path_text}")
    return read_json(path), dict(row)


def _company_inputs(state: EarningsState, root: Path, issuer_ids: list[str], period_start: str, period_end: str,
                    cutoff: str, research_quarter: str | None = None,
                    accepted_reports: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    artifacts: list[dict[str, Any]] = []
    researched: list[str] = []
    for issuer_id in issuer_ids:
        if research_quarter:
            rows = state.db.execute("""SELECT a.* FROM report_artifacts a WHERE a.report_type='company' AND a.subject_id IN
                (SELECT event_id FROM earnings_events WHERE issuer_id=?) ORDER BY a.created_at DESC""", (issuer_id,)).fetchall()
        else:
            rows = state.db.execute("""SELECT a.* FROM report_artifacts a WHERE a.report_type='company' AND a.subject_id IN
                (SELECT event_id FROM earnings_events WHERE issuer_id=?) AND (a.period_end IS NULL OR a.period_end BETWEEN ? AND ?)
                ORDER BY a.created_at DESC""", (issuer_id, period_start, period_end)).fetchall()
        if rows:
            eligible = []
            for candidate in rows:
                if accepted_reports is not None and not any(
                        row.get("path") == candidate["path"] and row.get("sha256") == candidate["sha256"]
                        for row in accepted_reports):
                    continue
                candidate_path = ensure_inside(resolve_path(root, candidate["path"]), [root / "report" / "earnings"])
                candidate_report = read_json(candidate_path)
                if parse_time(candidate_report.get("cutoff")) > parse_time(cutoff): continue
                if research_quarter:
                    from earnings_period_review import resolve_report_period
                    try: mapped = resolve_report_period(candidate_report)
                    except ValueError: continue
                    if mapped["research_quarter"] != research_quarter: continue
                eligible.append((candidate, candidate_path, candidate_report))
            if not eligible: continue
            row, path, report = eligible[0]; row = dict(row)
            if not path.exists() or sha256_file(path) != row["sha256"]:
                raise ValueError(f"company artifact missing or hash mismatch: {row['path']}")
            scope = report.get("scope") or {}
            if scope.get("issuer_id") != issuer_id:
                raise ValueError(f"company artifact scope mismatch: {row['path']}")
            artifacts.append({"report_id": row["report_id"], "task_id": row["task_id"], "path": row["path"], "sha256": row["sha256"],
                              "completeness": row["completeness"], "thesis_state": report.get("thesis_state"),
                              "source_mode": row["source_mode"], "cutoff": report.get("cutoff"), "issuer_id": issuer_id})
            researched.append(issuer_id)
    return artifacts, researched


def _source_documents(state: EarningsState, root: Path, issuer_ids: list[str], cutoff: str,
                      period_start: str, period_end: str, research_quarter: str | None = None,
                      bound_document_keys: set[tuple[str, int, str]] | None = None) -> list[dict[str, Any]]:
    cutoff_dt = parse_time(cutoff)
    documents: list[dict[str, Any]] = []
    for issuer_id in issuer_ids:
        rows = state.db.execute("""SELECT d.* FROM documents d LEFT JOIN earnings_events e ON e.event_id=d.event_id
            WHERE d.issuer_id=? AND (? OR e.reporting_end BETWEEN ? AND ? OR e.reporting_end IS NULL)
            ORDER BY d.document_id,d.version""", (issuer_id, int(bool(research_quarter)), period_start, period_end)).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for raw in rows:
            row = dict(raw)
            if research_quarter:
                from earnings_period_review import map_fiscal_period
                mapped_quarter = None
                if row.get("reporting_start") and row.get("reporting_end"):
                    try: mapped_quarter = map_fiscal_period(row["reporting_start"], row["reporting_end"], form=row.get("form"))["research_quarter"]
                    except ValueError: pass
                binding = (str(row["document_id"]), int(row["version"]), str(row["content_sha256"]))
                if mapped_quarter is None and binding in (bound_document_keys or set()): mapped_quarter = research_quarter
                if mapped_quarter != research_quarter: continue
            public = row.get("accepted_at") or row.get("published_at")
            if not public or parse_time(public) > cutoff_dt:
                continue
            if row.get("public_time_precision") == "day" and parse_time(public).date() == cutoff_dt.date():
                continue
            path = ensure_inside(resolve_path(root, row["original_path"]), [root / "raw_data" / "earnings"])
            if not path.exists() or sha256_file(path) != row["content_sha256"]:
                raise ValueError(f"source document missing or hash mismatch: {row['original_path']}")
            if row["document_id"] not in latest or row["version"] > latest[row["document_id"]]["version"]:
                latest[row["document_id"]] = row
        for row in latest.values():
            documents.append({key: row.get(key) for key in (
                "document_id", "version", "issuer_id", "event_id", "form", "source_type", "source_url", "provider", "backend",
                "reporting_start", "reporting_end", "published_at", "accepted_at", "fetched_at", "public_time_precision", "original_path",
                "content_sha256", "source_mode", "supersedes")})
    return documents


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a manual earnings industry role context")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--config", default="config/earnings_research.json")
    parser.add_argument("--universe", default="config/earnings_universe.json")
    parser.add_argument("--state", default="runtime/earnings/state.sqlite")
    parser.add_argument("--industry", required=True)
    parser.add_argument("--role", choices=["industry", "challenge", "synthesis"], required=True)
    parser.add_argument("--mode", choices=["daily", "quarterly"], default="daily")
    parser.add_argument("--period-start", required=True)
    parser.add_argument("--period-end", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--predecessor-report", action="append", default=[])
    parser.add_argument("--critical-gap-status", choices=["resolved", "disclosed", "unresolved"], default="unresolved")
    parser.add_argument("--frozen-scope")
    parser.add_argument("--accepted-company-input")
    parser.add_argument("--run-id")
    parser.add_argument("--owner")
    parser.add_argument("--lease-seconds", type=int)
    parser.add_argument("--date")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = Path(args.repo_root).resolve()
    config_path = configuration_path(root, args.config)
    config, config_hash = load_config(root, str(config_path))
    universe_path = confined_path(root, args.universe, "config")
    universe = read_json(universe_path)
    frozen_scope = None
    if args.frozen_scope:
        frozen_path = ensure_inside(resolve_path(root, args.frozen_scope), [root / "runtime" / "earnings" / "quarterly-scopes"])
        frozen_scope = read_json(frozen_path)
        industry = frozen_scope["industry"]
        if industry.get("industry_id") != args.industry:
            raise ValueError("frozen quarterly scope industry mismatch")
        if sha256_bytes(canonical_json(industry)) != frozen_scope.get("frozen_universe_hash"):
            raise ValueError("frozen quarterly scope hash mismatch")
    else:
        industry = _industry(universe, args.industry)
    cutoff = parse_time(args.cutoff)
    if cutoff is None:
        raise ValueError("cutoff required")
    if date.fromisoformat(args.period_start) > date.fromisoformat(args.period_end):
        raise ValueError("period-start after period-end")
    state = EarningsState(confined_path(root, args.state, "runtime/earnings"))
    try:
        expected_symbols = [row["symbol"] for row in industry["issuers"]]
        issuer_by_symbol = {row["symbol"]: row["issuer_id"] for row in state.db.execute("SELECT symbol,issuer_id FROM issuers")}
        issuer_ids = [issuer_by_symbol[symbol] for symbol in expected_symbols if symbol in issuer_by_symbol]
        unresolved_symbols = [symbol for symbol in expected_symbols if symbol not in issuer_by_symbol]
        end = date.fromisoformat(args.period_end); research_quarter = f"{end.year}-Q{(end.month - 1)//3 + 1}" if args.mode == "quarterly" else None
        accepted_reports = None
        if args.accepted_company_input:
            accepted_path = ensure_inside(resolve_path(root, args.accepted_company_input),
                                          [root / "runtime" / "earnings" / "quarterly-scopes"])
            accepted_payload = read_json(accepted_path)
            accepted_reports = accepted_payload.get("reports") or []
        company_artifacts, researched_ids = _company_inputs(state, root, issuer_ids, args.period_start,
            args.period_end, cutoff.isoformat(), research_quarter, accepted_reports)
        predecessor_reports: list[dict[str, Any]] = []
        predecessor_rows: list[dict[str, Any]] = []
        for path in args.predecessor_report:
            report, row = _registered_report(state, root, path)
            scope = report.get("scope") or {}
            if scope.get("industry_id") != args.industry or scope.get("reporting_start") != args.period_start or scope.get("reporting_end") != args.period_end:
                raise ValueError("predecessor scope does not match requested industry period")
            if report.get("research_mode") != args.mode or parse_time(report.get("cutoff")) > cutoff:
                raise ValueError("predecessor mode/cutoff is incompatible")
            predecessor_reports.append(report); predecessor_rows.append(row)
        if args.role == "challenge" and not any(r.get("report_type") == "industry" for r in predecessor_reports):
            raise ValueError("challenge role requires a registered industry predecessor report")
        if args.role == "synthesis" and not {"industry", "challenge"}.issubset({r.get("report_type") for r in predecessor_reports}):
            raise ValueError("synthesis role requires registered industry and challenge reports")
        bound_document_keys: set[tuple[str, int, str]] = set()
        if research_quarter:
            for artifact in company_artifacts:
                report = read_json(root / artifact["path"])
                for evidence in report.get("evidence", []):
                    if evidence.get("document_id") and evidence.get("document_version") is not None and evidence.get("document_hash"):
                        bound_document_keys.add((str(evidence["document_id"]), int(evidence["document_version"]),
                                                 str(evidence["document_hash"])))
        sources = _source_documents(state, root, issuer_ids, cutoff.isoformat(), args.period_start, args.period_end,
                                    research_quarter, bound_document_keys)
        modes = {row.get("source_mode") for row in company_artifacts + predecessor_rows + sources if row.get("source_mode")}
        if len(modes) > 1:
            raise ValueError(f"mixed source modes are forbidden: {sorted(modes)}")
        if not sources and not company_artifacts and not predecessor_rows:
            payload = envelope("earnings-industry-context", args.date or shanghai_date(), model_execution_required=False)
            payload.update(status="skipped", skipped=True, reason="no eligible provable industry inputs; model must not be invoked")
            emit(payload)
        source_mode = next(iter(modes), "fixture")
        expected_ids = [issuer_by_symbol.get(symbol, f"unresolved:{symbol}") for symbol in expected_symbols]
        if research_quarter and source_mode == "live":
            from earnings_period_review import _period_member_audit
            disclosed_ids, fetched_ids, researched_ids_audit, _ = _period_member_audit(
                state, issuer_ids, research_quarter, cutoff=cutoff.isoformat(),
                accepted_report_hashes={row["sha256"] for row in (accepted_reports or company_artifacts)})
        else:
            if research_quarter:
                from earnings_period_review import map_fiscal_period
                disclosed_ids = set()
                for row in state.db.execute("SELECT issuer_id,reporting_start,reporting_end FROM earnings_events WHERE event_kind='earnings'"):
                    if row["issuer_id"] not in issuer_ids: continue
                    try: mapped = map_fiscal_period(row["reporting_start"], row["reporting_end"])
                    except ValueError: continue
                    if mapped["research_quarter"] == research_quarter: disclosed_ids.add(row["issuer_id"])
            else:
                disclosed_ids = {row["issuer_id"] for row in state.db.execute(
                    "SELECT DISTINCT issuer_id FROM earnings_events WHERE reporting_end BETWEEN ? AND ? AND event_kind='earnings'",
                    (args.period_start, args.period_end)) if row["issuer_id"] in issuer_ids}
            fetched_ids = {row["issuer_id"] for row in sources}
            researched_ids_audit = set(researched_ids)
        researched_set = set(researched_ids_audit)
        key_tokens = [issuer_by_symbol.get(symbol, f"unresolved:{symbol}") for symbol in industry.get("key_symbols", [])]
        key_missing = [issuer_id for issuer_id in key_tokens if issuer_id not in researched_set]
        expected_count = len(expected_ids)
        disclosed_count, fetched_count, researched_count = len(disclosed_ids), len(fetched_ids), len(researched_set)
        threshold = float(config["quarterly"].get("mature_coverage_ratio", 0.9))
        maturity = {
            "coverage_ratio": disclosed_count / expected_count if expected_count else 0.0,
            "threshold": threshold,
            "key_issuers_complete": not key_missing,
            "company_research_complete": researched_count >= disclosed_count,
            "critical_gap_status": args.critical_gap_status,
        }
        maturity["eligible_full"] = bool(maturity["coverage_ratio"] >= threshold and maturity["key_issuers_complete"]
                                          and maturity["company_research_complete"] and args.critical_gap_status in {"resolved", "disclosed"})
        coverage = {"expected_issuers": expected_count, "disclosed_issuers": disclosed_count,
                    "fetched_issuers": min(fetched_count, disclosed_count), "researched_issuers": min(researched_count, fetched_count, disclosed_count),
                    "key_missing_issuers": key_missing}
        dependency_ids = sorted({row["task_id"] for row in predecessor_rows} | {row["task_id"] for row in company_artifacts})
        input_basis = {
            "role": args.role, "mode": args.mode, "industry_id": args.industry, "period_start": args.period_start,
            "period_end": args.period_end, "cutoff": cutoff.isoformat(),
            "universe_hash": frozen_scope["frozen_universe_hash"] if frozen_scope else sha256_file(universe_path),
            "company_artifacts": [(row["report_id"], row["sha256"]) for row in company_artifacts],
            "predecessors": [(row["report_id"], row["sha256"]) for row in predecessor_rows],
            "documents": [(row["document_id"], row["version"], row["content_sha256"]) for row in sources],
            "configuration_hash": config_hash, "source_mode": source_mode, "coverage": coverage,
            "critical_gap_status": args.critical_gap_status,
        }
        frozen_task_input = {**input_basis, "company_artifacts": company_artifacts,
                             "previous_artifacts": [{"report_id": row["report_id"], "task_id": row["task_id"], "path": row["path"],
                                                      "sha256": row["sha256"], "report_type": row["report_type"], "source_mode": row["source_mode"]}
                                                    for row in predecessor_rows], "documents": sources, "maturity": maturity}
        input_hash = sha256_bytes(canonical_json(frozen_task_input))
        profile_name, model, effort = _profile(config, args.role, args.mode)
        task_id, created = state.enqueue_task(task_type=args.role, subject_id=args.industry, period_start=args.period_start,
            period_end=args.period_end, input_hash=input_hash, method_version="earnings-method-v1", source_mode=source_mode,
            profile=profile_name, model=model, effort=effort, max_attempts=int(config["budgets"].get("max_task_attempts", 2)), dependencies=dependency_ids)
        state.freeze_task_input(task_id, frozen_task_input, input_hash)
        run_id = safe_segment(args.run_id or stable_id("run", args.role, args.industry, args.period_end, input_hash), "run id")
        task = state.claim_task(task_id, owner=args.owner or f"manual:{os.getpid()}:{run_id}",
                                lease_seconds=args.lease_seconds or int(config["budgets"].get("task_timeout_seconds", 1800)))
        if task is None:
            existing = state.db.execute("SELECT state,output_manifest FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()
            payload = envelope("earnings-industry-context", args.date or shanghai_date(), task_id=task_id,
                               task_state=existing["state"], model_execution_required=False)
            payload.update(status="skipped", skipped=True,
                           reason="unchanged input already queued/running/completed or dependency is not complete; do not invoke model")
            emit(payload)
        safe_industry = safe_segment(args.industry, "industry id")
        safe_period = safe_segment(args.period_end, "period end")
        safe_task = safe_segment(task_id, "task id")
        attempt_segment = f"attempt-{int(task['attempts'])}"
        output_dir = confined_path(root, root / "report" / "earnings" / "industries" / safe_industry / safe_period / safe_task / attempt_segment, "report/earnings")
        output_json = output_dir / f"{args.role}_report.json"
        run_dir = confined_path(root, root / "runtime" / "earnings" / "runs" / run_id / safe_task / attempt_segment, "runtime/earnings/runs")
        predecessor_claim_ids = sorted({claim.get("claim_id") for report in predecessor_reports for claim in report.get("claims", []) if claim.get("claim_id")})
        material_findings = sorted({finding.get("finding_id") for report in predecessor_reports if report.get("report_type") == "challenge"
                                    for finding in report.get("findings", []) if finding.get("finding_id") and finding.get("materiality") in {"high", "material"}})
        missing_ids = [issuer_id for issuer_id in expected_ids if issuer_id not in researched_set]
        manifest = {
            "schema_version": 1, "manifest_type": "earnings-role-input", "task_id": task_id, "run_id": run_id,
            "lease": {"owner": task["lease_owner"], "expires_at": task["lease_expires_at"], "attempt": task["attempts"]},
            "assigned_role": args.role, "research_mode": args.mode, "source_mode": source_mode, "cutoff": cutoff.isoformat(),
            "created_at": utc_now(), "method_version": task["method_version"], "configuration_hash": config_hash,
            "input_hash": input_hash, "profile": {"name": profile_name, "model": model, "effort": effort, "usage": None},
            "scope": {"industry_id": args.industry, "reporting_start": args.period_start, "reporting_end": args.period_end,
                      "universe_version": frozen_scope["frozen_universe_hash"] if frozen_scope else sha256_file(universe_path), "expected_issuer_ids": expected_ids},
            "coverage_audit": {"expected_symbols": expected_symbols, "resolved_issuer_ids": issuer_ids, "researched_issuer_ids": researched_ids,
                               "missing_issuer_ids": missing_ids, "unresolved_symbols": unresolved_symbols,
                               "counts": coverage, "key_issuer_ids": key_tokens, "maturity": maturity,
                               "negative_or_flat_samples": [row for row in company_artifacts if row.get("thesis_state") in {"weakening", "invalidated", "insufficient_data"}],
                               "unselected_samples": missing_ids + [f"unresolved:{s}" for s in unresolved_symbols]},
            "documents": sources, "company_artifacts": company_artifacts,
            "previous_artifacts": [{"report_id": row["report_id"], "task_id": row["task_id"], "path": row["path"], "sha256": row["sha256"],
                                    "report_type": row["report_type"]} for row in predecessor_rows],
            "predecessor_claim_ids": predecessor_claim_ids, "material_challenge_finding_ids": material_findings,
            "permitted_outputs": {"json": relative_to_root(root, output_json), "markdown": relative_to_root(root, output_json.with_suffix(".md")),
                                  "completion": relative_to_root(root, run_dir / "completion.json")},
            "instructions": {"skill": ".codex/skills/tca-earnings-research/SKILL.md", "role_prompt": "ops/cc-connect/tca-earnings-role.prompt.md",
                             "semantic_research_required": True, "deterministic_placeholder_forbidden": True,
                             "original_first_for_challenger": args.role == "challenge", "quarterly_original_reread_required": args.mode == "quarterly",
                             "resolve_every_material_challenge": args.role == "synthesis", "notification_forbidden": True},
        }
        manifest["input_manifest_hash"] = sha256_bytes(canonical_json(manifest))
        manifest_path = run_dir / "input-manifest.json"
        if manifest_path.exists():
            expected_file_hash = sha256_bytes(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
            if sha256_file(manifest_path) != expected_file_hash:
                raise ValueError("immutable attempt manifest differs")
        else:
            atomic_write_json(manifest_path, manifest)
        state.register_attempt_manifest(task_id, int(task["attempts"]), relative_to_root(root, manifest_path), sha256_file(manifest_path))
        payload = envelope("earnings-industry-context", args.date or shanghai_date(), task_id=task_id, run_id=run_id,
                           role=args.role, research_mode=args.mode, model=model, effort=effort,
                           coverage=manifest["coverage_audit"], model_execution_required=True)
        payload["artifacts"] = [relative_to_root(root, manifest_path)]
        payload["next_agent_inputs"] = [".codex/skills/tca-earnings-research/SKILL.md", "ops/cc-connect/tca-earnings-role.prompt.md",
                                        relative_to_root(root, manifest_path)]
        payload["expected_agent_outputs"] = list(manifest["permitted_outputs"].values())[:2]
        emit(payload)
    finally:
        state.close()


if __name__ == "__main__":
    main()
