#!/usr/bin/env python3
"""Persistent bounded-window supervisor for goal-driven earnings research."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import fcntl
import os
import signal
import shutil
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

from earnings_common import (ROOT, atomic_write_json, load_config, parse_time, process_identity,
                             read_json, sha256_file, utc_now)
from earnings_daily import round_progress, summarize_batch_usage
from earnings_delivery import destination, exclusive_lock, runtime_path
from earnings_state import EarningsState


@contextmanager
def _worker_lock(path: Path, timeout: float = 30.0):
    """Serialize worker generations; a successor may wait briefly for its parent handoff."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("continuation worker lock remained busy")
                    time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class ContinuationLedger:
    def __init__(self, root: Path):
        self.path = runtime_path(root, "runtime/earnings/continuation.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS rounds(
          round_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, batch_date TEXT NOT NULL,
          cutoff TEXT NOT NULL, state TEXT NOT NULL, stop_reason TEXT,
          last_fingerprint TEXT, no_progress_windows INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT);
        CREATE TABLE IF NOT EXISTS windows(
          window_id TEXT PRIMARY KEY, round_id TEXT NOT NULL, generation INTEGER NOT NULL,
          window_index INTEGER NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL,
          completed_at TEXT, result_path TEXT, result_status TEXT, model_calls INTEGER,
          fingerprint_before TEXT, fingerprint_after TEXT, stop_reason TEXT, usage_json TEXT);
        CREATE TABLE IF NOT EXISTS workers(
          worker_id TEXT PRIMARY KEY, round_id TEXT NOT NULL, generation INTEGER NOT NULL,
          pid INTEGER NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL,
          completed_at TEXT, successor_pid INTEGER, reason TEXT);
        """)
        window_columns = {row[1] for row in self.db.execute("PRAGMA table_info(windows)")}
        if "usage_json" not in window_columns:
            self.db.execute("ALTER TABLE windows ADD COLUMN usage_json TEXT")
        round_columns = {row[1] for row in self.db.execute("PRAGMA table_info(rounds)")}
        if "owner_generation" not in round_columns:
            self.db.execute("ALTER TABLE rounds ADD COLUMN owner_generation INTEGER")
        if "research_outcome" not in round_columns:
            self.db.execute("ALTER TABLE rounds ADD COLUMN research_outcome TEXT")
        self.db.execute("""UPDATE rounds SET research_outcome=CASE
          WHEN stop_reason LIKE 'frozen cutoff target completed%' THEN 'complete'
          WHEN stop_reason LIKE 'only terminal/manual blockers remain%' THEN 'blocked'
          WHEN stop_reason LIKE 'model quota exhausted%' THEN 'paused_quota'
          WHEN stop_reason LIKE 'model capacity unavailable%' THEN 'paused_capacity'
          WHEN stop_reason LIKE 'frozen cutoff has no executable evidence%' THEN 'waiting'
          WHEN stop_reason LIKE 'no durable progress%' OR stop_reason LIKE 'window errors remain%' THEN 'yielded'
          ELSE 'blocked' END
          WHERE research_outcome IS NULL AND state IN ('delivery_pending','delivery_unknown')""")
        self.db.commit()

    def active_round(self) -> dict | None:
        row = self.db.execute("""SELECT * FROM rounds WHERE state IN
          ('active','yielded','paused_quota','paused_capacity','delivery_pending') ORDER BY revision DESC LIMIT 1""").fetchone()
        return dict(row) if row else None

    def create_round(self, now: datetime) -> dict:
        revision = self.db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM rounds").fetchone()[0]
        batch_date = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        cutoff = now.astimezone(timezone.utc).isoformat()
        round_id = f"earnings-round-{revision}-{uuid.uuid4().hex[:12]}"
        stamp = utc_now()
        self.db.execute("""INSERT INTO rounds(round_id,revision,batch_date,cutoff,state,stop_reason,
                        last_fingerprint,no_progress_windows,created_at,updated_at,completed_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (round_id, revision, batch_date, cutoff, "active", None, None, 0,
                         stamp, stamp, None))
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM rounds WHERE round_id=?", (round_id,)).fetchone())

    def close(self) -> None:
        self.db.close()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _process_identity(pid: int) -> str | None:
    return process_identity(pid)


def _ownership_lock_held(root: Path, relative: str | None) -> bool:
    if not relative:
        return False
    path = (root / relative).resolve()
    try:
        path.relative_to((root / "runtime/earnings").resolve())
        with path.open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except (OSError, ValueError):
        return False
    return False


def _bootstrap_quarterly_schema(root: Path) -> dict:
    """Back up and migrate the legacy quarterly DB before any progress query."""
    quarterly = runtime_path(root, "runtime/earnings/quarterly.sqlite")
    if not quarterly.exists():
        return {"status": "not_needed", "reason": "quarterly database absent"}
    state_path = runtime_path(root, "runtime/earnings/migrations/quarterly-goal-driven-state.json")
    with exclusive_lock(runtime_path(root, "runtime/earnings/quarterly-migration.lock")):
        db = sqlite3.connect(quarterly)
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(quarterly_scopes)")}
        finally:
            db.close()
        required = {"accepted_reports_json", "active_round_id", "active_input_path"}
        if required <= columns:
            return {"status": "ready", "migrated": False}
        digest = sha256_file(quarterly)
        backup = runtime_path(root, f"runtime/earnings/migrations/quarterly-pre-goal-driven-{digest[:16]}.sqlite.bak")
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(quarterly, backup)
        atomic_write_json(state_path, {"status": "running", "source_sha256": digest,
            "backup_path": str(backup.relative_to(root)), "started_at": utc_now()})
        try:
            from earnings_period_review import QuarterlyReviewLedger
            ledger = QuarterlyReviewLedger(quarterly); ledger.close()
            atomic_write_json(state_path, {"status": "completed", "source_sha256": digest,
                "backup_path": str(backup.relative_to(root)), "completed_at": utc_now()})
            return {"status": "completed", "migrated": True, "backup_path": str(backup.relative_to(root))}
        except Exception as exc:
            atomic_write_json(state_path, {"status": "failed", "source_sha256": digest,
                "backup_path": str(backup.relative_to(root)), "failed_at": utc_now(),
                "reason": f"{type(exc).__name__}: {exc}"})
            raise


def _terminate_owned_model_groups(root: Path, *, window_id: str | None = None,
                                  started_after: datetime | None = None) -> list[dict]:
    """Terminate detached model sessions proven by durable runner ownership records."""
    records: list[tuple[Path, dict, dict | None]] = []
    for path in list((root / "runtime/earnings/runs").glob("**/runner-request.json")) + \
                list((root / "runtime/earnings/quarterly-scopes").glob("**/runner-request.json")):
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        records.append((path, payload, None))
    for path in (root / "runtime/earnings/publications/runs").glob("**/attempt-state.json"):
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for call in payload.get("calls", []):
            records.append((path, call, payload))
    killed = []
    for path, call, container in records:
        if window_id and call.get("execution_window_id") != window_id:
            continue
        started = parse_time(call.get("started_at"))
        if started_after and (not started or started < started_after):
            continue
        pid = call.get("process_group") or call.get("pid")
        if not isinstance(pid, int) or call.get("status") not in {"running", "reserved", "started"}:
            continue
        result_path = path.with_name("runner-result.json") if path.name == "runner-request.json" else None
        if result_path and result_path.exists():
            try:
                terminal = read_json(result_path)
            except (OSError, json.JSONDecodeError):
                terminal = {}
            if terminal.get("call_id") == call.get("call_id") and terminal.get("status") in {"completed", "failed", "success"}:
                continue
        if (not call.get("process_identity") or process_identity(pid) != call.get("process_identity") or
                not _ownership_lock_held(root, call.get("ownership_lock"))):
            continue
        try:
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + 2
            while _pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            if _pid_alive(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        except (ProcessLookupError, PermissionError):
            pass
        call.update(status="unknown", usage=None, terminated_at=utc_now(),
                    failure="outer execution window terminated owned model session")
        if container is None:
            atomic_write_json(path, call)
        else:
            atomic_write_json(path, container)
        killed.append({"call_id": call.get("call_id"), "pid": pid, "path": str(path.relative_to(root))})
    return killed


def _worker_argv(root: Path, args: argparse.Namespace, round_id: str, generation: int) -> list[str]:
    argv = [sys.executable, str(root / "script/earnings_continuation.py"), "--repo-root", str(root),
            "--config", args.config, "--deployment", args.deployment, "--worker",
            "--round-id", round_id, "--generation", str(generation)]
    if args.send:
        argv.append("--send")
    return argv


def spawn_worker(root: Path, args: argparse.Namespace, round_id: str, generation: int) -> int:
    logs = runtime_path(root, "runtime/earnings/logs")
    logs.mkdir(parents=True, exist_ok=True)
    stream = (logs / f"continuation-{round_id}-g{generation}-{uuid.uuid4().hex[:8]}.log").open("ab")
    process = subprocess.Popen(_worker_argv(root, args, round_id, generation), cwd=root,
                               stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                               start_new_session=True, close_fds=True)
    stream.close()
    return process.pid


def _handoff_cross_day_collection(root: Path, args: argparse.Namespace, ledger: ContinuationLedger,
                                  completed_round: dict) -> dict | None:
    """Start at most one current-day round after settling an older delivery debt."""
    now = datetime.now(timezone.utc)
    batch_date = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    if str(completed_round.get("batch_date") or "") >= batch_date:
        return None
    existing = ledger.db.execute(
        "SELECT * FROM rounds WHERE batch_date=? AND revision>? ORDER BY revision DESC LIMIT 1",
        (batch_date, completed_round["revision"])).fetchone()
    if existing:
        return {**dict(existing), "handoff_state": "already_exists"}
    next_round = ledger.create_round(now)
    generation = 1
    ledger.db.execute("UPDATE rounds SET owner_generation=? WHERE round_id=?",
                      (generation, next_round["round_id"]))
    ledger.db.commit()
    pid = spawn_worker(root, args, next_round["round_id"], generation)
    worker_id = f"{next_round['round_id']}:g{generation}:{pid}"
    ledger.db.execute("INSERT OR IGNORE INTO workers VALUES(?,?,?,?,?,?,?,?,?)",
                      (worker_id, next_round["round_id"], generation, pid, "running",
                       utc_now(), None, None, "cross-day delivery handoff"))
    ledger.db.commit()
    return {**next_round, "owner_generation": generation, "pid": pid, "handoff_state": "started"}


def _start_locked(root: Path, args: argparse.Namespace) -> dict:
    ledger = ContinuationLedger(root)
    try:
        deployed = destination(root, runtime_path(root, args.deployment))
        from earnings_quota_guard import refresh
        quota = refresh(root, deployed["codex_bin"])
        if quota.get("pause_required"):
            return {"status": "success", "workflow": "earnings-continuation-start",
                    "state": "paused_quota", "reason": "user quota reserve guard", "quota": quota}
        _bootstrap_quarterly_schema(root)
        active_worker = ledger.db.execute(
            "SELECT * FROM workers WHERE state='running' ORDER BY started_at DESC LIMIT 1").fetchone()
        worker_age = None
        if active_worker:
            started = parse_time(active_worker["started_at"])
            worker_age = ((datetime.now(timezone.utc) - started).total_seconds() if started else None)
        maximum_age = int(deployed.get("continuation_worker_seconds", 8400)) + 300
        if (active_worker and _pid_alive(int(active_worker["pid"]))
                and worker_age is not None and worker_age <= maximum_age):
            return {"status": "success", "workflow": "earnings-continuation-start",
                    "state": "already_running", "round_id": active_worker["round_id"],
                    "pid": active_worker["pid"]}
        if active_worker and _pid_alive(int(active_worker["pid"])):
            return {"status": "failed", "workflow": "earnings-continuation-start",
                    "state": "stale_running", "round_id": active_worker["round_id"],
                    "pid": active_worker["pid"], "reason": "live worker exceeded age bound; operator review required"}
        if active_worker:
            ledger.db.execute("UPDATE workers SET state='orphaned',completed_at=?,reason=? WHERE worker_id=?",
                              (utc_now(), "pid missing or worker heartbeat age exceeded", active_worker["worker_id"]))
        round_row = ledger.active_round()
        if round_row and round_row["state"] == "yielded":
            config, _ = load_config(root, args.config)
            state = EarningsState(root / config["paths"]["state"])
            try:
                progress = round_progress(root, state, config, cutoff=round_row["cutoff"])
            finally:
                state.close()
            last_window = ledger.db.execute("SELECT result_path FROM windows WHERE round_id=? AND result_path IS NOT NULL ORDER BY window_index DESC LIMIT 1", (round_row["round_id"],)).fetchone()
            collected = bool(last_window and read_json(root / last_window[0]).get("collection_complete") is True)
            if collected and not int(progress.get("blocker_count", 0)) and int(progress.get("actionable_count", 0)) == 0 and int(progress.get("waiting_count", 0)):
                ledger.db.execute("""UPDATE rounds SET state='waiting',research_outcome='waiting',stop_reason=?,
                  updated_at=?,completed_at=? WHERE round_id=? AND state='yielded'""",
                  ("frozen cutoff has no executable evidence; next trigger may admit new disclosures",
                   utc_now(), utc_now(), round_row["round_id"]))
                ledger.db.commit(); round_row = None
        round_row = round_row or ledger.create_round(datetime.now(timezone.utc))
        generation = ledger.db.execute("SELECT COALESCE(MAX(generation),0)+1 FROM workers WHERE round_id=?",
                                       (round_row["round_id"],)).fetchone()[0]
        pid = spawn_worker(root, args, round_row["round_id"], generation)
        worker_id = f"{round_row['round_id']}:g{generation}:{pid}"
        ledger.db.execute("INSERT OR IGNORE INTO workers VALUES(?,?,?,?,?,?,?,?,?)",
                          (worker_id, round_row["round_id"], generation, pid, "running", utc_now(), None, None, None))
        # Preserve delivery_pending and its independent research outcome. The spawned
        # worker decides whether this is a research or delivery-only generation.
        if round_row["state"] != "delivery_pending":
            ledger.db.execute("UPDATE rounds SET state='active',stop_reason=NULL,updated_at=? WHERE round_id=?",
                              (utc_now(), round_row["round_id"]))
        ledger.db.execute("UPDATE rounds SET owner_generation=? WHERE round_id=?", (generation, round_row["round_id"]))
        ledger.db.commit()
        return {"status": "success", "workflow": "earnings-continuation-start", "state": "started",
                "round_id": round_row["round_id"], "revision": round_row["revision"], "pid": pid,
                "generation": generation, "cutoff": round_row["cutoff"]}
    finally:
        ledger.close()


def start(root: Path, args: argparse.Namespace) -> dict:
    with exclusive_lock(runtime_path(root, "runtime/earnings/continuation-start.lock")):
        return _start_locked(root, args)


def _run_daily(root: Path, args: argparse.Namespace, round_row: dict, window_id: str,
               window_seconds: int, *, resume_only: bool, finalize_only: bool = False,
               execution_focus: str = "all", round_summary: Path | None = None) -> dict:
    command = [sys.executable, str(root / "script/earnings_daily.py"), "--repo-root", str(root),
        "--config", args.config, "--deployment", args.deployment,
        "--batch-date", round_row["batch_date"], "--cutoff", round_row["cutoff"],
        "--round-id", round_row["round_id"], "--execution-window-id", window_id,
        "--batch-timeout-seconds", str(max(60, window_seconds)), "--execution-focus", execution_focus]
    if finalize_only:
        command.append("--finalize-only")
        if args.send:
            command.append("--send")
        if round_summary:
            command.extend(["--round-summary", str(round_summary)])
    else:
        command.append("--defer-finalize")
        if resume_only:
            command.append("--resume-only")
    env = dict(os.environ); env["TCA_EARNINGS_WINDOW_ID"] = window_id
    process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True, env=env)
    try:
        process_timeout = getattr(args, "process_timeout_seconds", None) or max(90, window_seconds + 45)
        stdout, stderr = process.communicate(timeout=process_timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        raise RuntimeError(f"earnings daily window timed out; process group {process.pid} terminated")
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"earnings daily window returned no JSON (exit {completed.returncode})")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("earnings daily window returned invalid JSON") from exc
    result["process_returncode"] = completed.returncode
    if completed.stderr:
        result["stderr_tail"] = completed.stderr[-2000:]
    return result


def _checkpoint_path(root: Path, round_id: str, window_id: str) -> Path:
    return runtime_path(root, f"runtime/earnings/continuation/{round_id}/{window_id}.json")


def _failure_stop(result: dict) -> tuple[str | None, str | None]:
    text = "\n".join(str(value) for value in result.get("errors", []))
    if result.get("model_circuit_open") or "quota exhausted" in text.lower():
        return "paused_quota", "model quota exhausted; durable round retained"
    if "capacity_unavailable" in text.lower():
        return "paused_capacity", "model capacity unavailable; retry timing retained"
    return None, None


def worker(root: Path, args: argparse.Namespace) -> dict:
    config, _ = load_config(root, args.config)
    deployed = destination(root, runtime_path(root, args.deployment))
    session_seconds = int(deployed.get("continuation_worker_seconds", 8400))
    window_seconds = int(deployed.get("continuation_window_seconds", 1800))
    final_reserve = int(deployed.get("continuation_finalize_reserve_seconds", 120))
    no_progress_limit = int(deployed.get("continuation_no_progress_windows", 2))
    if not 300 <= session_seconds <= 43200 or not 60 <= window_seconds <= 7200:
        raise ValueError("invalid continuation worker/window bounds")
    if final_reserve < 0 or session_seconds <= window_seconds + final_reserve:
        raise ValueError("continuation worker must fit a full window and finalization reserve")
    ledger = ContinuationLedger(root)
    worker_row = ledger.db.execute("SELECT * FROM workers WHERE round_id=? AND generation=? ORDER BY started_at DESC LIMIT 1",
                                   (args.round_id, args.generation)).fetchone()
    worker_id = worker_row["worker_id"] if worker_row else f"{args.round_id}:g{args.generation}:{os.getpid()}"
    if not worker_row:
        ledger.db.execute("INSERT OR IGNORE INTO workers VALUES(?,?,?,?,?,?,?,?,?)",
                          (worker_id, args.round_id, args.generation, os.getpid(), "running", utc_now(), None, None, None))
        ledger.db.commit()
    try:
        with _worker_lock(runtime_path(root, "runtime/earnings/continuation-worker.lock")):
            _bootstrap_quarterly_schema(root)
            round_row_raw = ledger.db.execute("SELECT * FROM rounds WHERE round_id=?", (args.round_id,)).fetchone()
            if not round_row_raw:
                raise ValueError("continuation round does not exist")
            round_row = dict(round_row_raw)
            if round_row.get("owner_generation") is not None and int(round_row["owner_generation"]) != args.generation:
                return {"status": "skipped", "workflow": "earnings-continuation-worker",
                        "round_id": args.round_id, "generation": args.generation,
                        "state": "superseded", "reason": "newer worker generation owns this round"}
            if round_row["state"] in {"complete", "blocked", "waiting", "delivery_unknown"}:
                return {"status": "skipped", "workflow": "earnings-continuation-worker",
                        "round_id": args.round_id, "generation": args.generation,
                        "state": round_row["state"], "reason": "round is already terminal"}
            deadline = time.monotonic() + session_seconds
            collection_complete = False
            for row in ledger.db.execute("SELECT result_path FROM windows WHERE round_id=? AND state='completed' ORDER BY window_index",
                                         (args.round_id,)):
                try:
                    if read_json(root / row["result_path"]).get("collection_complete") is True:
                        collection_complete = True
                except (OSError, TypeError, json.JSONDecodeError):
                    continue
            no_progress = int(round_row["no_progress_windows"])
            last_result: dict = {}
            delivery_only = round_row["state"] == "delivery_pending"
            stop_state = "delivery_pending" if delivery_only else None
            stop_reason = "retrying durable delivery only" if delivery_only else None
            # A short tail cannot satisfy the daily runner's research/checker start
            # thresholds. Hand it off instead of counting empty windows as stalls.
            while stop_state is None and deadline - time.monotonic() >= final_reserve + window_seconds:
                from earnings_quota_guard import refresh
                quota = refresh(root, deployed["codex_bin"])
                if quota.get("pause_required"):
                    stop_state, stop_reason = "paused_quota", "user quota reserve guard: " + quota["reason"]
                    break
                index = ledger.db.execute("SELECT COALESCE(MAX(window_index),0)+1 FROM windows WHERE round_id=?",
                                          (args.round_id,)).fetchone()[0]
                window_id = f"{args.round_id}-g{args.generation}-w{index}"
                state = EarningsState(root / config["paths"]["state"])
                try:
                    before = round_progress(root, state, config, cutoff=round_row["cutoff"])
                finally:
                    state.close()
                ledger.db.execute("INSERT INTO windows(window_id,round_id,generation,window_index,state,started_at,fingerprint_before) VALUES(?,?,?,?,?,?,?)",
                                  (window_id, args.round_id, args.generation, index, "running", utc_now(), before["fingerprint"]))
                ledger.db.commit()
                available = min(window_seconds, max(60, int(deadline - time.monotonic() - final_reserve)))
                downstream = sum(int(before["pending"].get(key, 0)) for key in
                                 ("daily_industry", "quarterly_scopes", "quarterly_market", "publications"))
                company = int(before["pending"].get("current_company", 0))
                previous_focus = last_result.get("execution_focus")
                focus = ("downstream" if downstream and (not company or previous_focus != "downstream")
                         else "company" if company else "downstream")
                window_started = datetime.now(timezone.utc)
                try:
                    result = _run_daily(root, args, round_row, window_id, available,
                                        resume_only=collection_complete, execution_focus=focus)
                    result["execution_focus"] = focus
                except Exception as exc:
                    terminated = _terminate_owned_model_groups(root, window_id=window_id, started_after=window_started)
                    reconcile_state = EarningsState(root / config["paths"]["state"])
                    try:
                        reconciled_progress = round_progress(root, reconcile_state, config, cutoff=round_row["cutoff"])
                        recovered_reports = [dict(row) for row in reconcile_state.db.execute(
                            "SELECT task_id,path AS report_path,sha256 AS report_sha256,created_at FROM report_artifacts")
                            if (parse_time(row["created_at"]) or datetime.min.replace(tzinfo=timezone.utc)) >= window_started]
                    finally:
                        reconcile_state.close()
                    usage = summarize_batch_usage(root, round_row["batch_date"], recovered_reports, [], since=window_started)
                    usage["model_call_count_known"] = bool(usage.get("actual_model_calls") or terminated)
                    usage["unobserved_model_calls_possible"] = not usage["model_call_count_known"]
                    result = {"status": "failed", "errors": [f"continuation-window:{type(exc).__name__}: {exc}"],
                              "progress": reconciled_progress, "execution_focus": focus,
                              "reports": recovered_reports, "terminated_model_sessions": terminated,
                              "usage_summary": usage}
                checkpoint = _checkpoint_path(root, args.round_id, window_id)
                atomic_write_json(checkpoint, result)
                after = result.get("progress") or before
                changed = after.get("fingerprint") != before.get("fingerprint")
                calls = int((result.get("usage_summary") or {}).get("actual_model_calls") or 0)
                no_progress = 0 if changed else no_progress + 1
                usage = result.get("usage_summary") or {}
                ledger.db.execute("""UPDATE windows SET state='completed',completed_at=?,result_path=?,result_status=?,
                  model_calls=?,fingerprint_after=?,stop_reason=?,usage_json=? WHERE window_id=?""",
                  (utc_now(), str(checkpoint.relative_to(root)), result.get("status"), calls,
                   after.get("fingerprint"), None, json.dumps(usage, sort_keys=True), window_id))
                ledger.db.execute("UPDATE rounds SET last_fingerprint=?,no_progress_windows=?,updated_at=? WHERE round_id=?",
                                  (after.get("fingerprint"), no_progress, utc_now(), args.round_id))
                ledger.db.commit()
                collection_complete = collection_complete or result.get("collection_complete") is True
                last_result = result
                stop_state, stop_reason = _failure_stop(result)
                if stop_state:
                    break
                if int(after.get("actionable_count", 0)) == 0:
                    if not collection_complete:
                        if no_progress >= no_progress_limit:
                            stop_state, stop_reason = "yielded", "frozen-cutoff collection remains incomplete"
                            break
                        continue
                    if int(after.get("blocker_count", 0)):
                        stop_state, stop_reason = "blocked", "only terminal/manual blockers remain"
                    elif result.get("errors"):
                        stop_state, stop_reason = "yielded", "window errors remain despite no runnable task"
                    elif int(after.get("waiting_count", 0)):
                        stop_state, stop_reason = "waiting", "frozen cutoff has no executable evidence; next trigger may admit new disclosures"
                    else:
                        stop_state, stop_reason = "complete", "frozen cutoff target completed"
                    break
                if no_progress >= no_progress_limit:
                    stop_state, stop_reason = "yielded", "no durable progress across bounded windows"
                    break
            successor_pid = None
            if stop_state is None:
                # The worker itself is bounded. Continued progress hands off immediately to
                # another bounded generation, so the cron timeout is not a completion cap.
                ledger.db.execute("UPDATE rounds SET owner_generation=?,updated_at=? WHERE round_id=?",
                                  (args.generation + 1, utc_now(), args.round_id)); ledger.db.commit()
                successor_pid = spawn_worker(root, args, args.round_id, args.generation + 1)
                successor_id = f"{args.round_id}:g{args.generation + 1}:{successor_pid}"
                ledger.db.execute("INSERT OR IGNORE INTO workers VALUES(?,?,?,?,?,?,?,?,?)",
                                  (successor_id, args.round_id, args.generation + 1, successor_pid,
                                   "running", utc_now(), None, None, None))
                stop_state, stop_reason = "active", "bounded worker handed off to successor"
            else:
                research_outcome = round_row.get("research_outcome") if delivery_only else stop_state
                summary_reports = {}; summary_errors = []; summary_calls = {}
                for saved in ledger.db.execute("SELECT result_path FROM windows WHERE round_id=? AND result_path IS NOT NULL ORDER BY window_index",
                                               (args.round_id,)):
                    payload = read_json(root / saved["result_path"])
                    for report in payload.get("reports", []):
                        key = report.get("report_sha256") or report.get("task_id") or json.dumps(report, sort_keys=True)
                        summary_reports[key] = report
                    summary_errors.extend(payload.get("errors", []))
                    for call in (payload.get("usage_summary") or {}).get("calls", []):
                        summary_calls[call["call_id"]] = call
                current_state = EarningsState(root / config["paths"]["state"])
                try:
                    unresolved_errors = []
                    for error in dict.fromkeys(summary_errors):
                        parts = str(error).split(":")
                        if parts[0] == "role" and len(parts) > 1:
                            task = current_state.db.execute("SELECT state FROM research_tasks WHERE task_id=?", (parts[1],)).fetchone()
                            if task and task[0] == "completed": continue
                        if parts[0].startswith("publication") and len(parts) > 1:
                            job = current_state.db.execute("SELECT state FROM publication_jobs WHERE job_id=?", (parts[1],)).fetchone()
                            if job and job[0] in {"archived", "complete"}: continue
                        unresolved_errors.append(error)
                finally:
                    current_state.close()
                summary_path = runtime_path(root, f"runtime/earnings/continuation/{args.round_id}/round-summary.json")
                atomic_write_json(summary_path, {"schema_version": 1, "round_id": args.round_id,
                    "reports": list(summary_reports.values()), "errors": unresolved_errors,
                    "calls": list(summary_calls.values()), "created_at": utc_now()})
                final_id = f"{args.round_id}-finalize-g{args.generation}"
                try:
                    final_result = _run_daily(root, args, round_row, final_id, 60,
                                              resume_only=True, finalize_only=True, round_summary=summary_path)
                    atomic_write_json(_checkpoint_path(root, args.round_id, final_id), final_result)
                    delivery_state = ((final_result.get("delivery") or {}).get("delivery") or {}).get("state")
                    if final_result.get("status") == "failed" or delivery_state in {"retryable_failed", "failed"}:
                        stop_state = "delivery_pending"; stop_reason += "; delivery pending retry"
                    elif delivery_state == "deferred" and (delivery_only or stop_state in {"complete", "blocked", "waiting"}):
                        stop_state = "delivery_pending"; stop_reason += "; waiting for next notification slot"
                    elif delivery_state == "unknown":
                        stop_state = "delivery_unknown"; stop_reason += "; automatic retry suppressed"
                    elif delivery_only:
                        stop_state = research_outcome or "blocked"; stop_reason = "durable delivery retry completed"
                except Exception as exc:
                    stop_state = "delivery_pending"
                    stop_reason += f"; finalization failed and remains pending: {type(exc).__name__}: {exc}"
            completed_at = utc_now() if stop_state in {"complete", "blocked", "waiting", "delivery_unknown"} else None
            stored_outcome = research_outcome if 'research_outcome' in locals() else round_row.get("research_outcome")
            ledger.db.execute("UPDATE rounds SET state=?,research_outcome=?,stop_reason=?,updated_at=?,completed_at=? WHERE round_id=?",
                              (stop_state, stored_outcome, stop_reason, utc_now(), completed_at, args.round_id))
            ledger.db.commit()
            # Keep this worker durably running until the new worker row exists. A
            # concurrent starter therefore observes an owner throughout the handoff.
            next_round = (_handoff_cross_day_collection(root, args, ledger, round_row)
                          if delivery_only else None)
            ledger.db.execute("UPDATE workers SET state='completed',completed_at=?,successor_pid=?,reason=? WHERE worker_id=?",
                              (utc_now(), successor_pid, stop_reason, worker_id))
            ledger.db.commit()
            cumulative = {key: 0 for key in ("actual_model_calls", "calls_with_usage_null",
                "cached_results_reused", "input_tokens", "cached_input_tokens", "non_cached_input_tokens",
                "output_tokens", "reasoning_output_tokens")}
            for row in ledger.db.execute("SELECT usage_json FROM windows WHERE round_id=? AND usage_json IS NOT NULL",
                                         (args.round_id,)):
                usage = json.loads(row["usage_json"])
                for key in cumulative:
                    if isinstance(usage.get(key), int):
                        cumulative[key] += usage[key]
            return {"status": "success", "workflow": "earnings-continuation-worker",
                    "round_id": args.round_id, "generation": args.generation, "state": stop_state,
                    "reason": stop_reason, "successor_pid": successor_pid, "last_result": last_result,
                    "round_usage_summary": cumulative, "next_round": next_round}
    finally:
        ledger.close()


def status(root: Path) -> dict:
    ledger = ContinuationLedger(root)
    try:
        active = ledger.active_round()
        recent = [dict(row) for row in ledger.db.execute(
            "SELECT * FROM rounds ORDER BY revision DESC LIMIT 5").fetchall()]
        windows = []
        workers = []
        if active:
            windows = [dict(row) for row in ledger.db.execute(
                "SELECT * FROM windows WHERE round_id=? ORDER BY window_index DESC LIMIT 10",
                (active["round_id"],)).fetchall()]
            workers = [dict(row) for row in ledger.db.execute(
                "SELECT * FROM workers WHERE round_id=? ORDER BY generation DESC LIMIT 10",
                (active["round_id"],)).fetchall()]
        return {"status": "success", "workflow": "earnings-continuation-status",
                "active_round": active, "recent_rounds": recent,
                "active_windows": windows, "active_workers": workers}
    finally:
        ledger.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--config", default="config/earnings_research.json")
    parser.add_argument("--deployment", default="runtime/earnings/deployment.json")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--round-id")
    parser.add_argument("--generation", type=int, default=1)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(); root = Path(args.repo_root).resolve()
    try:
        if args.status:
            result = status(root)
        elif args.worker:
            if not args.round_id:
                raise ValueError("--worker requires --round-id")
            result = worker(root, args)
        else:
            result = start(root, args)
    except BlockingIOError:
        result = {"status": "success", "workflow": "earnings-continuation", "state": "already_running"}
    except Exception as exc:
        result = {"status": "failed", "workflow": "earnings-continuation", "reason": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result["status"] == "failed" else 0)


if __name__ == "__main__":
    main()
