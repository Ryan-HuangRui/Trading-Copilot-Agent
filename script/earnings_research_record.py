#!/usr/bin/env python3
"""Validate and atomically register a completed semantic research role artifact."""

from __future__ import annotations

import argparse
from pathlib import Path

from earnings_common import ROOT, atomic_write_json, confined_path, emit, envelope, ensure_inside, parse_time, read_json, relative_to_root, resolve_path, sha256_file, utc_now
from earnings_state import EarningsState
from validate_earnings_research import validate_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register a validated earnings role report")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--state", default="runtime/earnings/state.sqlite")
    parser.add_argument("--report", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--date")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = Path(args.repo_root).resolve()
    report_path = ensure_inside(resolve_path(root, args.report), [root / "report" / "earnings"])
    manifest_path = ensure_inside(resolve_path(root, args.manifest), [root / "runtime" / "earnings" / "runs"])
    validation = validate_report(report_path, manifest_path, root)
    if not validation["valid"]:
        payload = envelope("earnings-record", args.date, validation=validation)
        payload.update(status="failed", reason="report rejected by validator")
        emit(payload, 1)
    report = read_json(report_path)
    manifest = read_json(manifest_path)
    permitted = resolve_path(root, manifest["permitted_outputs"]["json"]).resolve()
    if report_path.resolve() != permitted:
        payload = envelope("earnings-record", args.date)
        payload.update(status="failed", reason="report path is not the manifest-permitted JSON output")
        emit(payload, 1)
    state = EarningsState(confined_path(root, args.state, "runtime/earnings"))
    try:
        completion_path = resolve_path(root, manifest["permitted_outputs"]["completion"])
        ensure_inside(completion_path, [root / "runtime" / "earnings" / "runs"])
        report_digest = sha256_file(report_path)
        completion = {
            "schema_version": 1, "status": "completed", "task_id": report["task_id"], "run_id": report["run_id"],
            "report_id": report["report_id"], "report_type": report["report_type"], "report_path": relative_to_root(root, report_path),
            "report_sha256": report_digest, "input_manifest": relative_to_root(root, manifest_path),
            "input_manifest_hash": report["input_manifest_hash"], "source_mode": report["source_mode"],
            "model": report["provenance"]["model"], "effort": report["provenance"]["effort"],
            "usage": report["provenance"].get("usage"), "completed_at": utc_now(), "validation": validation,
        }
        with state.immediate() as db:
            task = db.execute("SELECT rowid AS task_revision,* FROM research_tasks WHERE task_id=?", (report["task_id"],)).fetchone()
            if not task:
                raise ValueError(f"unknown task: {report['task_id']}")
            existing = db.execute("SELECT sha256,path FROM report_artifacts WHERE task_id=?", (report["task_id"],)).fetchone()
            if existing and (existing["sha256"] != report_digest or existing["path"] != completion["report_path"]):
                raise ValueError("completed task report cannot be replaced")
            if task["state"] == "completed":
                if existing and existing["sha256"] == report_digest and existing["path"] == completion["report_path"]:
                    payload = envelope("earnings-record", args.date, task_id=report["task_id"], report_id=report["report_id"],
                                       validation=validation, completion=relative_to_root(root, completion_path), idempotent=True)
                    payload["artifacts"] = [relative_to_root(root, report_path), relative_to_root(root, completion_path)]
                    emit(payload)
                raise ValueError("completed task report cannot be replaced")
            lease = manifest.get("lease") or {}
            if task["state"] != "running" or task["lease_owner"] != lease.get("owner") or int(task["attempts"]) != lease.get("attempt"):
                raise ValueError("stale or superseded task lease")
            if not task["lease_expires_at"] or parse_time(task["lease_expires_at"]) <= parse_time(utc_now()):
                raise ValueError("task lease expired before completion")
            if task["input_hash"] != manifest["input_hash"]:
                raise ValueError("stale task input hash")
            attempt_manifest = db.execute("SELECT path,sha256 FROM task_attempt_manifests WHERE task_id=? AND attempt=?",
                                          (task["task_id"], task["attempts"])).fetchone()
            if not attempt_manifest or attempt_manifest["path"] != relative_to_root(root, manifest_path) or attempt_manifest["sha256"] != sha256_file(manifest_path):
                raise ValueError("current attempt manifest is missing or changed")
            blocked = db.execute(
                """SELECT COUNT(*) count FROM task_dependencies d JOIN research_tasks p ON p.task_id=d.dependency_task_id
                WHERE d.task_id=? AND p.state!='completed'""", (task["task_id"],)).fetchone()["count"]
            if blocked:
                raise ValueError("task dependency is no longer complete")
            newer = db.execute(
                """SELECT COUNT(*) count FROM research_tasks WHERE task_type=? AND subject_id=? AND period_start IS ? AND period_end IS ?
                AND method_version=? AND input_hash!=? AND rowid>? AND state IN ('queued','running','completed','retryable_failed')""",
                (task["task_type"], task["subject_id"], task["period_start"], task["period_end"], task["method_version"], task["input_hash"], task["task_revision"]),
            ).fetchone()["count"]
            if newer:
                raise ValueError("task input was superseded by a newer active input")
            for predecessor in manifest.get("previous_artifacts") or []:
                row = db.execute("SELECT path,sha256 FROM report_artifacts WHERE report_id=? AND task_id=?",
                                 (predecessor.get("report_id"), predecessor.get("task_id"))).fetchone()
                if not row or row["path"] != predecessor.get("path") or row["sha256"] != predecessor.get("sha256"):
                    raise ValueError("frozen predecessor is stale")
                predecessor_task = db.execute("SELECT rowid AS task_revision,* FROM research_tasks WHERE task_id=?", (predecessor.get("task_id"),)).fetchone()
                newer_predecessor = db.execute(
                    """SELECT COUNT(*) count FROM research_tasks WHERE task_type=? AND subject_id=? AND period_start IS ? AND period_end IS ?
                    AND input_hash!=? AND rowid>? AND task_id!=? AND state IN ('queued','running','completed','retryable_failed')""",
                    (predecessor_task["task_type"], predecessor_task["subject_id"], predecessor_task["period_start"],
                     predecessor_task["period_end"], predecessor_task["input_hash"], predecessor_task["task_revision"], task["task_id"]),
                ).fetchone()["count"] if predecessor_task else 1
                if newer_predecessor:
                    raise ValueError("predecessor dependency was superseded by newer evidence")
            for company in manifest.get("company_artifacts") or []:
                company_task = db.execute("SELECT rowid AS task_revision,* FROM research_tasks WHERE task_id=?", (company.get("task_id"),)).fetchone()
                artifact = db.execute("SELECT path,sha256 FROM report_artifacts WHERE report_id=? AND task_id=?",
                                      (company.get("report_id"), company.get("task_id"))).fetchone()
                if not company_task or not artifact or artifact["path"] != company.get("path") or artifact["sha256"] != company.get("sha256"):
                    raise ValueError("frozen company dependency is stale")
                newer_company = db.execute(
                    """SELECT COUNT(*) count FROM research_tasks WHERE task_type='company' AND subject_id=?
                    AND input_hash!=? AND rowid>? AND task_id!=? AND state IN ('queued','running','completed','retryable_failed')""",
                    (company_task["subject_id"], company_task["input_hash"], company_task["task_revision"], task["task_id"]),
                ).fetchone()["count"]
                if newer_company:
                    raise ValueError("company dependency was superseded by newer evidence")
            if not existing:
                scope = report.get("scope") or {}
                db.execute(
                    "INSERT INTO report_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (report["report_id"], report["task_id"], report["report_type"], task["subject_id"],
                     scope.get("reporting_start"), scope.get("reporting_end"), completion["report_path"], completion["report_sha256"],
                     report["input_manifest_hash"], report["source_mode"], report["completeness"]["status"], utc_now()),
                )
            atomic_write_json(completion_path, completion)
            db.execute(
                "UPDATE research_tasks SET state='completed',output_manifest=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE task_id=?",
                (relative_to_root(root, completion_path), utc_now(), report["task_id"]),
            )
        payload = envelope("earnings-record", args.date, task_id=report["task_id"], report_id=report["report_id"],
                           validation=validation, completion=relative_to_root(root, completion_path))
        payload["artifacts"] = [relative_to_root(root, report_path), relative_to_root(root, completion_path)]
        emit(payload)
    finally:
        state.close()


if __name__ == "__main__":
    main()
