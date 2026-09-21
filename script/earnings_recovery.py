#!/usr/bin/env python3
"""Preview or apply one bounded recovery to an explicitly selected earnings task."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from earnings_common import ROOT, atomic_write_json, read_json, safe_segment, utc_now
from earnings_publication_runner import prepare_repair_input, recheck_publication
from earnings_delivery import exclusive_lock
from earnings_state import EarningsState


def _backup(state: EarningsState, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(target)
    try:
        state.db.backup(destination)
    finally:
        destination.close()


def recover(root: Path, *, action: str, job_id: str | None = None, task_id: str | None = None,
            reuse_task_id: str | None = None, reason: str | None = None, execute: bool = False) -> dict:
    root = root.resolve()
    target_id = safe_segment(job_id or task_id or "", "recovery target")
    audit_dir = root / "runtime/earnings/recovery"
    audit_path = audit_dir / f"{action}-{target_id}.json"
    stack = ExitStack()
    if execute:
        stack.enter_context(exclusive_lock(root / "runtime/earnings/daily.lock"))
    state = EarningsState(root / "runtime/earnings/state.sqlite")
    try:
        if audit_path.exists() and read_json(audit_path).get("executed"):
            raise ValueError("this exact bounded recovery was already executed")
        before: dict
        mutation: dict
        if action in {"resume-checker", "schedule-repair", "recheck-publication"}:
            if not job_id or task_id:
                raise ValueError("publication recovery requires exactly --job-id")
            row = state.db.execute("SELECT * FROM publication_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise ValueError("unknown publication job")
            before = dict(row)
            if row["state"] not in {"checker_pending", "retryable_failed", "terminal_failed"}:
                raise ValueError("publication job is not in a recoverable failed/checker state")
            if not row["input_manifest_path"]:
                raise ValueError("publication job has no frozen input manifest")
            manifest_path = root / row["input_manifest_path"]
            manifest = read_json(manifest_path)
            if action == "recheck-publication":
                if row["state"] != "terminal_failed":
                    raise ValueError("deterministic recheck requires an exact terminal publication job")
                mutation = {"state": "cloud_pending", "attempts": int(row["attempts"]),
                            "input_manifest_path": row["input_manifest_path"], "model_calls": 0}
            elif action == "resume-checker":
                if not (manifest_path.parent / "writer-stage.json").exists():
                    raise ValueError("resume-checker requires a completed frozen writer stage")
                result_path = root / manifest["permitted_outputs"]["runner_result"]
                if result_path.exists():
                    raise ValueError("completed runner result requires schedule-repair or normal cached handling")
                mutation = {"state": "checker_pending", "attempts": 1,
                            "input_manifest_path": row["input_manifest_path"]}
            else:
                result_path = root / manifest["permitted_outputs"]["runner_result"]
                if not result_path.exists() or read_json(result_path).get("status") != "failed":
                    raise ValueError("schedule-repair requires a persisted failed runner result")
                mutation = {"state": "retryable_failed", "attempts": 1,
                            "input_manifest_path": row["input_manifest_path"], "create_repair": True}
        elif action == "reuse-dependency":
            if not task_id or not reuse_task_id or job_id:
                raise ValueError("dependency reuse requires --task-id and --reuse-task-id")
            before_row = state.db.execute("SELECT * FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()
            if not before_row:
                raise ValueError("unknown failed dependency task")
            before = dict(before_row)
            mutation = state.preview_dependency_reuse(task_id, reuse_task_id)
            if not mutation["eligible"]:
                raise ValueError(f"dependency tasks are not equivalent: {', '.join(mutation['differences'])}")
            if not (reason or "").strip():
                raise ValueError("dependency reuse requires --reason")
        elif action == "exclude-dependency":
            if not task_id or reuse_task_id or job_id:
                raise ValueError("dependency exclusion requires exactly --task-id")
            before_row = state.db.execute("SELECT * FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()
            if not before_row: raise ValueError("unknown failed dependency task")
            before = dict(before_row); mutation = state.preview_dependency_exclusion(task_id)
            if not mutation["eligible"]:
                raise ValueError(f"dependency task is not excludable: {', '.join(mutation['differences'])}")
            if not (reason or "").strip(): raise ValueError("dependency exclusion requires --reason")
        elif action == "release-expired-task":
            if not task_id or job_id:
                raise ValueError("task recovery requires exactly --task-id")
            row = state.db.execute("SELECT * FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("unknown research task")
            before = dict(row)
            if row["state"] != "running" or not row["lease_expires_at"]:
                raise ValueError("research task is not an expired running lease")
            lease = datetime.fromisoformat(row["lease_expires_at"].replace("Z", "+00:00"))
            if lease >= datetime.now(timezone.utc):
                raise ValueError("research task lease is still active")
            newer = state.db.execute("""SELECT 1 FROM research_tasks n JOIN research_tasks t ON t.task_id=?
              WHERE n.rowid>t.rowid AND n.task_type=t.task_type AND n.subject_id=t.subject_id
              AND n.period_start IS t.period_start AND n.period_end IS t.period_end AND n.source_mode=t.source_mode""",
              (task_id,)).fetchone()
            if newer:
                mutation = {"state": "terminal_failed", "error": "superseded while lease was active"}
            elif row["attempts"] >= row["max_attempts"]:
                mutation = {"state": "terminal_failed", "error": "lease expired at maximum attempts"}
            else:
                mutation = {"state": "retryable_failed", "error": "lease expired; eligible for bounded recovery"}
        else:
            raise ValueError("unsupported recovery action")
        result = {"schema_version": 1, "workflow": "earnings-recovery", "status": "preview",
                  "action": action, "target_id": target_id, "before": before, "planned": mutation,
                  "executed": False, "model_calls": 0, "cloud_operations": 0, "notifications": 0}
        if not execute:
            return result
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = audit_dir / "backups" / f"state-{timestamp}-{target_id}.sqlite"
        _backup(state, backup)
        if action == "recheck-publication":
            rechecked = recheck_publication(root, root / before["input_manifest_path"])
            if rechecked.get("status") != "success" or not rechecked.get("manifest_path"):
                raise ValueError(f"deterministic publication recheck failed: {rechecked.get('reason') or 'unknown'}")
            cursor = state.db.execute("""UPDATE publication_jobs SET state='cloud_pending',publication_manifest_path=?,
              error=?,updated_at=? WHERE job_id=? AND state=? AND attempts=? AND input_manifest_path=?""",
              (rechecked["manifest_path"], "operator deterministic recheck passed", utc_now(), job_id,
               before["state"], before["attempts"], before["input_manifest_path"]))
            if cursor.rowcount != 1:
                raise ValueError("publication job changed after preview; no recheck recovery applied")
            result["recheck"] = rechecked
        elif action in {"resume-checker", "schedule-repair"}:
            if action == "schedule-repair":
                repair = prepare_repair_input(root, root / before["input_manifest_path"])
                mutation["input_manifest_path"] = str(repair.relative_to(root))
            state.db.execute("""UPDATE publication_jobs SET state=?,attempts=?,input_manifest_path=?,
              publication_manifest_path=NULL,error=?,updated_at=? WHERE job_id=?""",
              (mutation["state"], mutation["attempts"], mutation["input_manifest_path"],
               f"operator bounded recovery: {action}", utc_now(), job_id))
        elif action == "reuse-dependency":
            state.apply_dependency_reuse(task_id, reuse_task_id, reason=reason or "")
        elif action == "exclude-dependency":
            state.apply_dependency_exclusion(task_id, reason=reason or "")
        else:
            cursor = state.db.execute("""UPDATE research_tasks SET state=?,error=?,lease_owner=NULL,
              lease_expires_at=NULL,updated_at=? WHERE task_id=? AND state='running'
              AND lease_expires_at=? AND attempts=? AND max_attempts=?""",
              (mutation["state"], mutation["error"], utc_now(), task_id, before["lease_expires_at"],
               before["attempts"], before["max_attempts"]))
            if cursor.rowcount != 1:
                raise ValueError("research task changed after preview; no recovery applied")
        result.update(status="success", executed=True, backup_path=str(backup.relative_to(root)), executed_at=utc_now())
        audit_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(audit_path, result)
        return result
    finally:
        state.close()
        stack.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--action", required=True,
                        choices=["resume-checker", "schedule-repair", "recheck-publication",
                                 "release-expired-task", "reuse-dependency", "exclude-dependency"])
    parser.add_argument("--job-id")
    parser.add_argument("--task-id")
    parser.add_argument("--reuse-task-id")
    parser.add_argument("--reason")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = recover(Path(args.repo_root), action=args.action, job_id=args.job_id,
                         task_id=args.task_id, reuse_task_id=args.reuse_task_id,
                         reason=args.reason, execute=args.execute)
    except (ValueError, OSError, sqlite3.Error, KeyError, json.JSONDecodeError) as exc:
        result = {"workflow": "earnings-recovery", "status": "failed", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result["status"] == "failed" else 0)


if __name__ == "__main__":
    main()
