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
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

from earnings_common import ROOT, atomic_write_json, load_config, parse_time, read_json, utc_now
from earnings_daily import round_progress
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


def _start_locked(root: Path, args: argparse.Namespace) -> dict:
    ledger = ContinuationLedger(root)
    try:
        deployed = destination(root, runtime_path(root, args.deployment))
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
        round_row = ledger.active_round() or ledger.create_round(datetime.now(timezone.utc))
        generation = ledger.db.execute("SELECT COALESCE(MAX(generation),0)+1 FROM workers WHERE round_id=?",
                                       (round_row["round_id"],)).fetchone()[0]
        pid = spawn_worker(root, args, round_row["round_id"], generation)
        worker_id = f"{round_row['round_id']}:g{generation}:{pid}"
        ledger.db.execute("INSERT OR IGNORE INTO workers VALUES(?,?,?,?,?,?,?,?,?)",
                          (worker_id, round_row["round_id"], generation, pid, "running", utc_now(), None, None, None))
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
    process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
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
            round_row_raw = ledger.db.execute("SELECT * FROM rounds WHERE round_id=?", (args.round_id,)).fetchone()
            if not round_row_raw:
                raise ValueError("continuation round does not exist")
            round_row = dict(round_row_raw)
            if round_row.get("owner_generation") is not None and int(round_row["owner_generation"]) != args.generation:
                return {"status": "skipped", "workflow": "earnings-continuation-worker",
                        "round_id": args.round_id, "generation": args.generation,
                        "state": "superseded", "reason": "newer worker generation owns this round"}
            if round_row["state"] in {"complete", "blocked"}:
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
            while stop_state is None and deadline - time.monotonic() >= final_reserve + 60:
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
                                 ("daily_industry", "quarterly_scopes", "publications"))
                company = int(before["pending"].get("current_company", 0))
                previous_focus = last_result.get("execution_focus")
                focus = ("downstream" if downstream and (not company or previous_focus != "downstream")
                         else "company" if company else "downstream")
                try:
                    result = _run_daily(root, args, round_row, window_id, available,
                                        resume_only=collection_complete, execution_focus=focus)
                    result["execution_focus"] = focus
                except Exception as exc:
                    result = {"status": "failed", "errors": [f"continuation-window:{type(exc).__name__}: {exc}"],
                              "progress": before, "execution_focus": focus,
                              "usage_summary": {"actual_model_calls": 1, "calls_with_usage_null": 1,
                                                "calls": [{"call_id": f"{window_id}:unreconciled", "status": "unknown", "usage": None}]}}
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
                    elif delivery_state == "unknown":
                        stop_state = "delivery_unknown"; stop_reason += "; automatic retry suppressed"
                    elif delivery_only:
                        stop_state = "complete"; stop_reason = "durable delivery retry completed"
                except Exception as exc:
                    stop_state = "delivery_pending"
                    stop_reason += f"; finalization failed and remains pending: {type(exc).__name__}: {exc}"
            completed_at = utc_now() if stop_state in {"complete", "blocked", "delivery_unknown"} else None
            ledger.db.execute("UPDATE rounds SET state=?,stop_reason=?,updated_at=?,completed_at=? WHERE round_id=?",
                              (stop_state, stop_reason, utc_now(), completed_at, args.round_id))
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
                    "round_usage_summary": cumulative}
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
