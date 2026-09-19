#!/usr/bin/env python3
"""Shared last-moment validity gate for immutable earnings role manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from earnings_common import ensure_inside, parse_time, read_json, relative_to_root, resolve_path, sha256_file, utc_now
from earnings_state import EarningsState


def _newer_active(db: Any, task: Any, *, exclude_task_id: str | None = None) -> bool:
    params: list[Any] = [task["task_revision"], task["task_type"], task["subject_id"],
                         task["period_start"], task["period_end"], task["source_mode"]]
    exclude = ""
    if exclude_task_id:
        exclude = " AND n.task_id!=?"
        params.append(exclude_task_id)
    return bool(db.execute(
        """SELECT 1 FROM research_tasks n WHERE n.rowid>? AND n.task_type=? AND n.subject_id=?
        AND n.period_start IS ? AND n.period_end IS ? AND n.source_mode=?
        AND n.state IN ('queued','running','completed','retryable_failed')""" + exclude + " LIMIT 1", params,
    ).fetchone())


def validate_manifest(root: Path, state: EarningsState, manifest_path: Path) -> tuple[dict[str, Any], list[str]]:
    """Validate the exact lease, frozen input and every artifact immediately before a model call."""
    manifest_path = ensure_inside(manifest_path.resolve(), [root / "runtime" / "earnings" / "runs"])
    manifest = read_json(manifest_path)
    errors: list[str] = []
    task = state.db.execute("SELECT rowid AS task_revision,* FROM research_tasks WHERE task_id=?",
                            (manifest.get("task_id"),)).fetchone()
    lease = manifest.get("lease") or {}
    if not task:
        errors.append("task missing")
    else:
        if task["state"] != "running" or task["lease_owner"] != lease.get("owner") or int(task["attempts"]) != lease.get("attempt"):
            errors.append("stale task lease")
        if not task["lease_expires_at"] or parse_time(task["lease_expires_at"]) <= parse_time(utc_now()):
            errors.append("task lease expired")
        if task["input_hash"] != manifest.get("input_hash"):
            errors.append("task input changed")
        if _newer_active(state.db, task):
            errors.append("task input superseded")
        registered = state.db.execute(
            "SELECT path,sha256 FROM task_attempt_manifests WHERE task_id=? AND attempt=?",
            (task["task_id"], task["attempts"]),
        ).fetchone()
        if not registered or registered["path"] != relative_to_root(root, manifest_path) or registered["sha256"] != sha256_file(manifest_path):
            errors.append("attempt manifest missing or changed")
    for label, rows in (("predecessor", manifest.get("previous_artifacts") or []),
                        ("company", manifest.get("company_artifacts") or [])):
        for item in rows:
            artifact = state.db.execute("SELECT path,sha256 FROM report_artifacts WHERE report_id=? AND task_id=?",
                                        (item.get("report_id"), item.get("task_id"))).fetchone()
            source = state.db.execute("SELECT rowid AS task_revision,* FROM research_tasks WHERE task_id=?",
                                      (item.get("task_id"),)).fetchone()
            if not artifact or not source or source["state"] != "completed" or artifact["path"] != item.get("path") or artifact["sha256"] != item.get("sha256"):
                errors.append(f"{label} artifact stale: {item.get('task_id')}")
                continue
            path = resolve_path(root, item["path"])
            if not path.exists() or sha256_file(path) != item["sha256"]:
                errors.append(f"{label} artifact file changed: {item.get('task_id')}")
            if _newer_active(state.db, source, exclude_task_id=manifest.get("task_id")):
                errors.append(f"{label} artifact superseded: {item.get('task_id')}")
    return manifest, errors


def preflight_or_defer(root: Path, manifest_path: Path) -> dict[str, Any]:
    state = EarningsState(root / "runtime" / "earnings" / "state.sqlite")
    try:
        manifest, errors = validate_manifest(root, state, manifest_path)
        if not errors:
            return manifest
        reason = "preflight rejected before model invocation: " + "; ".join(errors)
        lease = manifest.get("lease") or {}
        state.defer_attempt(manifest["task_id"], int(lease.get("attempt", 0)), str(lease.get("owner") or ""),
                            relative_to_root(root, manifest_path), sha256_file(manifest_path), reason,
                            superseded="task input superseded" in errors)
        raise ValueError(reason)
    finally:
        state.close()
