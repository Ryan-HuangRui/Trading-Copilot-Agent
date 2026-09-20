#!/usr/bin/env python3
"""Daily, budgeted earnings orchestration. No polling or exchange-day skipping."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

from earnings_common import ROOT, atomic_write_json, load_config, parse_time, read_json, sha256_file, utc_now
from earnings_delivery import destination, deliver, exclusive_lock, prepare_notification, runtime_path
from earnings_gap_review_runner import run_gap_review
from earnings_lark import LarkDocumentPublisher
from earnings_period_review import QuarterlyReviewLedger, inspect_due, map_fiscal_period, resolve_report_period
from earnings_publication_runner import (prepare_input as prepare_publication_input,
                                         prepare_repair_input, run_publication)
from earnings_role_runner import run_role
from earnings_state import EarningsState


class DailyLedger:
    def __init__(self, root: Path):
        runtime_path(root, "runtime/earnings").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(runtime_path(root, "runtime/earnings/daily.sqlite"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS reservations(day TEXT, task_id TEXT, role TEXT, created_at TEXT,
          PRIMARY KEY(day,task_id,created_at));
        CREATE TABLE IF NOT EXISTS initialized(symbol TEXT PRIMARY KEY, at TEXT);
        CREATE TABLE IF NOT EXISTS industry_inputs(scope TEXT PRIMARY KEY, fingerprint TEXT);
        CREATE TABLE IF NOT EXISTS health(day TEXT PRIMARY KEY, failed INTEGER);
        CREATE TABLE IF NOT EXISTS industry_requests(scope TEXT PRIMARY KEY, fingerprint TEXT, cutoff TEXT);
        CREATE TABLE IF NOT EXISTS publication_inputs(fingerprint TEXT PRIMARY KEY, manifest_path TEXT, completed_at TEXT);
        CREATE TABLE IF NOT EXISTS quarterly_requests(scope TEXT, stage TEXT, input_signature TEXT, cutoff TEXT,
          PRIMARY KEY(scope,stage));
        """)
        self.db.commit()

    def used(self, day: str, role: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM reservations WHERE day=? AND role=?", (day, role)).fetchone()[0]

    def reserve(self, day: str, task: str, role: str, limit: int) -> bool:
        if self.used(day, role) >= limit:
            return False
        self.db.execute("INSERT INTO reservations VALUES(?,?,?,?)", (day, task, role, datetime.now(timezone.utc).isoformat(timespec="microseconds")))
        self.db.commit()
        return True

    def release(self, day: str, task: str, role: str) -> None:
        self.db.execute("DELETE FROM reservations WHERE day=? AND task_id=? AND role=?", (day, task, role))
        self.db.commit()


def command(root: Path, script: str, args: list[str], logs: Path, timeout: int) -> dict:
    result = subprocess.run([sys.executable, str(root / "script" / script), "--repo-root", str(root), *args],
                            capture_output=True, text=True, timeout=timeout, check=False)
    log = logs / f"{script}-{uuid.uuid4().hex[:8]}.log"
    log.write_text(result.stdout + "\n" + result.stderr)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{script} returned no valid envelope; see {log.name}") from exc
    if result.returncode or data.get("status") == "failed":
        raise RuntimeError(f"{script}: {data.get('reason') or 'failed'}")
    return data


def season_limit(config: dict, day: str) -> int:
    month_day = date.fromisoformat(day).strftime("%m-%d")
    strong = any(s["start"] <= month_day <= s["strong_end"] for s in config["seasons"])
    key = "strong_season_company_limit" if strong else "daily_company_limit"
    return int(config["budgets"][key])


def industry_work(root: Path, state: EarningsState, universe: dict, ledger: DailyLedger) -> list[dict]:
    due = []
    for industry in universe["industries"]:
        symbols = [row["symbol"] for row in industry["issuers"]]
        marks = ",".join("?" for _ in symbols)
        rows = state.db.execute(f"""SELECT a.path,a.sha256,a.period_end FROM report_artifacts a
          JOIN earnings_events e ON a.subject_id=e.event_id JOIN issuers i ON e.issuer_id=i.issuer_id
          WHERE a.report_type='company' AND a.source_mode='live' AND i.symbol IN ({marks})
          ORDER BY a.period_end DESC,a.created_at DESC""", symbols).fetchall()
        if not rows or not rows[0]["period_end"]:
            continue
        # Daily cohort is explicitly based on period ends. Source reports preserve fiscal periods.
        end = date.fromisoformat(rows[0]["period_end"])
        month = ((end.month - 1) // 3) * 3 + 1
        start = date(end.year, month, 1)
        next_q = date(end.year + (month == 10), 1 if month == 10 else month + 3, 1)
        from datetime import timedelta
        finish = next_q - timedelta(days=1)
        versions = sorted((r["path"], r["sha256"]) for r in rows if r["period_end"] and start.isoformat() <= r["period_end"] <= finish.isoformat())
        signature = hashlib.sha256(json.dumps(versions).encode()).hexdigest()
        scope = f"{industry['industry_id']}:{start}:{finish}"
        existing = ledger.db.execute("SELECT fingerprint FROM industry_inputs WHERE scope=?", (scope,)).fetchone()
        if not existing or existing[0] != signature:
            due.append({"industry": industry["industry_id"], "start": str(start), "end": str(finish),
                        "scope": scope, "fingerprint": signature})
    return due


def fail_owned_attempt(state: EarningsState, manifest: dict, error: str) -> None:
    """An outer failure must never revert a committed report or another owner's lease."""
    lease = manifest["lease"]
    if _quota_exhausted(error):
        registered = state.db.execute("""SELECT path,sha256 FROM task_attempt_manifests
          WHERE task_id=? AND attempt=?""", (manifest["task_id"], lease["attempt"])).fetchone()
        if registered:
            state.defer_attempt(manifest["task_id"], int(lease["attempt"]), str(lease["owner"]),
                                registered["path"], registered["sha256"], error)
        return
    with state.immediate() as db:
        next_attempt = ((datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")
                        if "model_capacity_unavailable" in error.lower() else None)
        db.execute("""UPDATE research_tasks SET state=CASE WHEN attempts<max_attempts
          THEN 'retryable_failed' ELSE 'terminal_failed' END,error=?,next_attempt_at=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?
          WHERE task_id=? AND state='running' AND lease_owner=? AND attempts=?""",
          (error, next_attempt, utc_now(), manifest["task_id"], lease["owner"], lease["attempt"]))


def notification_material(report: dict, prior: dict | None) -> bool:
    if report.get("completeness", {}).get("status") == "insufficient" or not report.get("evidence"):
        return False
    current_state = report.get("thesis_state")
    if current_state in {None, "insufficient_data"}:
        return False
    # Only an evidenced thesis-state transition is auto-pushed in P3. All other reports remain archived.
    return prior is None or prior.get("thesis_state") != current_state


def _latest_company_publication_heads(root: Path, state: EarningsState) -> dict[tuple[str, str], dict]:
    """Return the newest accepted company/IPO report for each reader-publication scope."""
    heads: dict[tuple[str, str], dict] = {}
    rows = state.db.execute("""SELECT a.*,e.event_kind FROM report_artifacts a
      LEFT JOIN earnings_events e ON e.event_id=a.subject_id
      WHERE a.source_mode='live' AND a.report_type='company'
      ORDER BY a.period_end DESC,a.rowid DESC""").fetchall()
    for row in rows:
        try:
            report = read_json(root / row["path"]); scope = report.get("scope") or {}
        except Exception:
            continue
        publication_type = "ipo" if scope.get("event_kind") == "ipo" or row["event_kind"] == "ipo" else "company"
        scope_id = scope.get("symbol") or scope.get("issuer_id")
        if not scope_id:
            continue
        key = (publication_type, str(scope_id))
        if key not in heads:
            heads[key] = {"sha256": row["sha256"], "path": row["path"],
                          "reporting_end": scope.get("reporting_end") or row["period_end"]}
    return heads


def _publication_job_sort_key(root: Path, job: dict, heads: dict[tuple[str, str], dict]) -> tuple:
    """Prioritize current accepted reports before formal reports and historical backfill."""
    head = heads.get((job["publication_type"], str(job["scope_id"])))
    is_backfill = job["publication_type"] in {"company", "ipo"} and head is not None \
        and job["source_sha256"] != head["sha256"]
    if is_backfill:
        priority = 2
    elif job["publication_type"] in {"industry", "market"}:
        priority = 1
    else:
        priority = 0
    # Resume a checker before rerunning its writer, but only inside the same business priority.
    checker_ready = job["state"] == "checker_pending"
    if job["state"] == "retryable_failed" and job.get("input_manifest_path"):
        checker_ready = (root / job["input_manifest_path"]).parent.joinpath("writer-stage.json").exists()
    stage_priority = 0 if checker_ready else 1
    reporting_end = ""
    try:
        report = read_json(root / job["source_path"])
        reporting_end = str((report.get("scope") or {}).get("reporting_end") or "")
    except Exception:
        pass
    try:
        period_priority = -date.fromisoformat(reporting_end).toordinal()
    except ValueError:
        period_priority = 0
    return priority, stage_priority, period_priority, job["updated_at"], job["job_id"]


def _publication_job_is_backfill(job: dict, heads: dict[tuple[str, str], dict]) -> bool:
    head = heads.get((job["publication_type"], str(job["scope_id"])))
    return job["publication_type"] in {"company", "ipo"} and head is not None \
        and job["source_sha256"] != head["sha256"]


def _publication_matches_current_head(publication: dict, heads: dict[tuple[str, str], dict]) -> bool:
    head = heads.get((publication["publication_type"], str(publication["scope_id"])))
    source_hashes = {source.get("sha256") for source in publication.get("sources", [])}
    return head is not None and head["sha256"] in source_hashes


def _publication_failure_detail(result: dict) -> str:
    """Persist the real checker/validator failure without leaking a traceback into notifications."""
    detail = {"reason": result.get("reason"), "semantic_errors": result.get("semantic_errors") or [],
              "deterministic_errors": result.get("deterministic_errors") or []}
    return json.dumps(detail, ensure_ascii=False, sort_keys=True)


def _cached_publication_result(root: Path, manifest_path: Path) -> dict | None:
    manifest = read_json(manifest_path)
    result_path = root / manifest["permitted_outputs"]["runner_result"]
    return read_json(result_path) if result_path.exists() else None


def _quota_exhausted(value: object) -> bool:
    return "model_quota_exhausted" in str(value).lower()


def research_window_available(config: dict, deadline: float, *, prepare_context: bool = False) -> bool:
    """Leave enough execution time before claiming a role or reserving a model call."""
    required = int(config["budgets"].get("research_start_threshold_seconds", 600))
    # Context subprocesses have a 120-second timeout. Reserve it before claiming,
    # so preparing inputs cannot eat the minimum window promised to the model.
    return deadline - time.monotonic() >= required + (120 if prepare_context else 0)


def unresolved_terminal_count(state: EarningsState) -> int:
    """Keep exhausted current work visible; superseded attempts are historical."""
    return state.db.execute("""SELECT COUNT(*) FROM research_tasks t
      WHERE t.state='terminal_failed' AND t.source_mode='live' AND NOT EXISTS (
        SELECT 1 FROM research_tasks n WHERE n.rowid>t.rowid AND n.source_mode=t.source_mode
      AND n.task_type=t.task_type AND n.subject_id=t.subject_id
        AND n.period_start IS t.period_start AND n.period_end IS t.period_end)""").fetchone()[0]


def _latest_role_artifact(root: Path, state: EarningsState, subject: str, report_type: str,
                          period_start: str, period_end: str) -> dict | None:
    rows = state.db.execute("""SELECT * FROM report_artifacts WHERE subject_id=? AND report_type=?
      AND period_start=? AND period_end=? ORDER BY rowid DESC""", (subject, report_type, period_start, period_end)).fetchall()
    for row in rows:
        report = read_json(root / row["path"])
        if report.get("research_mode") == "quarterly":
            return {**dict(row), "report": report}
    return None


def _quarterly_company_signature(root: Path, state: EarningsState, industry: dict, quarter_id: str) -> str:
    issuer_ids = {row["issuer_id"] for row in state.db.execute("SELECT issuer_id,symbol FROM issuers")
                  if row["symbol"] in {item["symbol"] for item in industry["issuers"]}}
    versions = []
    for issuer_id in issuer_ids:
        rows = state.db.execute("""SELECT a.path,a.sha256 FROM report_artifacts a JOIN earnings_events e ON e.event_id=a.subject_id
          WHERE a.report_type='company' AND e.issuer_id=? ORDER BY a.rowid""", (issuer_id,)).fetchall()
        for row in rows:
            report = read_json(root / row["path"]); scope = report.get("scope") or {}
            try: mapped = map_fiscal_period(scope.get("reporting_start"), scope.get("reporting_end"))
            except ValueError: continue
            if mapped["research_quarter"] == quarter_id: versions.append((row["path"], row["sha256"]))
    return hashlib.sha256(json.dumps(sorted(versions)).encode()).hexdigest()


def run_gap_review_step(root: Path, config: dict, ledger: DailyLedger, qledger: QuarterlyReviewLedger,
                        deployed: dict, day: str, scope: dict, deadline: float) -> dict:
    """Claim and execute one durable review-profile gap audit for an immutable input."""
    input_path = root / scope.get("gap_review_immutable_input_path", scope["gap_review_input_path"])
    gap_input = read_json(input_path)
    input_hash = gap_input["input_hash"]
    row = qledger.db.execute("SELECT * FROM quarterly_gap_attempts WHERE scope_id=? AND input_hash=?",
                             (scope["scope_id"], input_hash)).fetchone()
    if row and row["state"] in {"completed", "terminal_failed"}:
        return {"status": row["state"], "scope_id": scope["scope_id"], "stage": "gap_review",
                "attempts": row["attempts"], "reason": row["error"]}
    now = datetime.now(timezone.utc)
    if row and row["state"] == "running" and row["lease_expires_at"]:
        lease = datetime.fromisoformat(row["lease_expires_at"].replace("Z", "+00:00"))
        if lease > now:
            return {"status": "running", "scope_id": scope["scope_id"], "stage": "gap_review",
                    "attempts": row["attempts"]}
    cap = int(config["budgets"].get("review_tasks_per_day", 0))
    max_attempts = int(config["budgets"].get("max_task_attempts", 2))
    attempts = int(row["attempts"]) if row else 0
    if attempts >= max_attempts:
        qledger.db.execute("UPDATE quarterly_gap_attempts SET state='terminal_failed',error=COALESCE(error,?),updated_at=? WHERE scope_id=? AND input_hash=?",
                           ("maximum gap-review attempts exhausted", utc_now(), scope["scope_id"], input_hash))
        qledger.db.commit(); qledger.set_stage(scope["scope_id"], "gap_review", "blocked", input_hash=input_hash,
                                               error="maximum gap-review attempts exhausted")
        return {"status": "terminal_failed", "scope_id": scope["scope_id"], "stage": "gap_review",
                "attempts": attempts, "reason": "maximum gap-review attempts exhausted"}
    task_key = f"{scope['scope_id']}:gap-review:{input_hash}:attempt-{attempts + 1}"
    if not research_window_available(config, deadline) or not ledger.reserve(day, task_key, "review", cap):
        return {"status": "queued", "scope_id": scope["scope_id"], "stage": "gap_review",
                "attempts": attempts, "reason": "review budget or batch deadline exhausted"}
    attempt = attempts + 1
    attempt_name = f"attempt-{attempt}"
    if row and _quota_exhausted(row["error"]):
        attempt_name += f"-quota-retry-{uuid.uuid4().hex[:8]}"
    attempt_dir = root / "runtime/earnings/quarterly-scopes" / scope["scope_id"] / "gap-reviews" / input_hash / "attempts" / attempt_name
    timeout = min(int(config["budgets"]["task_timeout_seconds"]), max(1, int(deadline - time.monotonic())))
    lease = (now + timedelta(seconds=timeout + 60)).isoformat()
    qledger.db.execute("""INSERT INTO quarterly_gap_attempts(scope_id,input_hash,state,attempts,lease_expires_at,
      manifest_path,result_path,error,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(scope_id,input_hash) DO UPDATE SET
      state='running',attempts=excluded.attempts,lease_expires_at=excluded.lease_expires_at,manifest_path=NULL,
      result_path=NULL,error=NULL,updated_at=excluded.updated_at""",
      (scope["scope_id"], input_hash, "running", attempt, lease, None, None, None, utc_now()))
    qledger.db.commit(); qledger.set_stage(scope["scope_id"], "gap_review", "running", input_hash=input_hash)
    try:
        result = run_gap_review(root, input_path, binary=deployed["codex_bin"], profile=config["profiles"]["review"],
                                timeout=timeout, attempt_dir=attempt_dir)
        qledger.db.execute("""UPDATE quarterly_gap_attempts SET state='completed',lease_expires_at=NULL,
          manifest_path=?,result_path=?,error=NULL,updated_at=? WHERE scope_id=? AND input_hash=?""",
          (result["attempt_manifest"], result["artifact"], utc_now(), scope["scope_id"], input_hash))
        qledger.db.commit(); qledger.set_stage(scope["scope_id"], "gap_review", "completed", input_hash=input_hash,
            artifact_path=result["artifact"], artifact_sha256=sha256_file(root / result["artifact"]))
        return {**result, "stage": "gap_review", "attempts": attempt}
    except Exception as exc:
        failure_is_quota = _quota_exhausted(exc)
        if failure_is_quota:
            ledger.release(day, task_key, "review")
            attempt = attempts
        terminal = attempt >= max_attempts
        failure_state = "queued" if failure_is_quota else ("terminal_failed" if terminal else "retryable_failed")
        manifest_path = attempt_dir / "input-manifest.json"
        qledger.db.execute("""UPDATE quarterly_gap_attempts SET state=?,attempts=?,lease_expires_at=NULL,error=?,updated_at=?
          ,manifest_path=COALESCE(?,manifest_path) WHERE scope_id=? AND input_hash=?""",
          (failure_state, attempt, str(exc), utc_now(), str(manifest_path.relative_to(root)) if manifest_path.exists() else None,
           scope["scope_id"], input_hash))
        qledger.db.commit(); qledger.set_stage(scope["scope_id"], "gap_review", "blocked" if terminal else "failed",
                                               input_hash=input_hash, error=str(exc))
        return {"status": failure_state, "scope_id": scope["scope_id"], "stage": "gap_review",
                "attempts": attempt, "reason": str(exc)}


def run_quarterly_step(root: Path, config: dict, universe: dict, state: EarningsState, ledger: DailyLedger,
                       deployed: dict, day: str, cutoff: str, run_id: str, logs: Path, deadline: float,
                       config_path: str = "config/earnings_research.json", manual_quarter: str | None = None) -> list[dict]:
    """Advance at most the configured number of durable quarterly model stages."""
    if config["quarterly"].get("automatic_trigger_enabled") is not True and not manual_quarter:
        return [{"status": "disabled", "reason": "quarterly.automatic_trigger_enabled is false"}]
    review = inspect_due(root, day=day, cutoff=cutoff, config_path=config_path,
                         universe_path=config["paths"]["universe"], manual_quarter=manual_quarter)
    qledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
    outcomes = []
    quota_open = False
    cap = int(config["budgets"]["quarterly_tasks_per_day"])
    try:
        for scope in review["scopes"]:
            if not research_window_available(config, deadline, prepare_context=True):
                break
            if not scope["maturity"]["counts"]["researched_issuers"]:
                outcomes.append({"status": "skipped", "scope_id": scope["scope_id"],
                                 "reason": "waiting for accepted company evidence before quarterly model work"})
                continue
            qledger.set_stage(scope["scope_id"], "coverage", "completed",
                              input_hash=hashlib.sha256(json.dumps(scope["maturity"], sort_keys=True).encode()).hexdigest())
            if scope["maturity"]["critical_gap_status"] == "unresolved":
                gap = run_gap_review_step(root, config, ledger, qledger, deployed, day, scope, deadline)
                outcomes.append(gap)
                if _quota_exhausted(gap.get("reason")):
                    quota_open = True
                    break
                if gap["status"] == "success":
                    refreshed = inspect_due(root, day=day, cutoff=cutoff, config_path=config_path,
                                            universe_path=config["paths"]["universe"], manual_quarter=manual_quarter)
                    scope = next(row for row in refreshed["scopes"] if row["scope_id"] == scope["scope_id"])
                elif gap["status"] not in {"completed"}:
                    continue
            if ledger.used(day, "quarterly") >= cap:
                continue
            if not (scope["eligible_stage"] or scope["deadline_stage_allowed"]):
                continue
            role = None; predecessors = []
            industry_config = scope["frozen_industry"]
            company_signature = scope["input_fingerprint"]
            stage_row = qledger.db.execute("SELECT state,input_hash FROM quarterly_stages WHERE scope_id=? AND stage='industry'",
                                           (scope["scope_id"],)).fetchone()
            industry = _latest_role_artifact(root, state, scope["industry_id"], "industry", scope["period_start"], scope["period_end"])
            challenge = _latest_role_artifact(root, state, scope["industry_id"], "challenge", scope["period_start"], scope["period_end"])
            synthesis = _latest_role_artifact(root, state, scope["industry_id"], "synthesis", scope["period_start"], scope["period_end"])
            challenge_predecessors = set((challenge or {}).get("report", {}).get("provenance", {}).get("predecessor_report_hashes", []))
            synthesis_predecessors = set((synthesis or {}).get("report", {}).get("provenance", {}).get("predecessor_report_hashes", []))
            if not industry or stage_row["state"] != "completed" or stage_row["input_hash"] != company_signature: role = "industry"
            elif not challenge or industry["sha256"] not in challenge_predecessors: role = "challenge"; predecessors = [industry["path"]]
            elif not synthesis or not {industry["sha256"], challenge["sha256"]}.issubset(synthesis_predecessors):
                role = "synthesis"; predecessors = [industry["path"], challenge["path"]]
            if role is None:
                for stage, artifact in (("industry", industry), ("challenge", challenge), ("synthesis", synthesis)):
                    qledger.set_stage(scope["scope_id"], stage, "completed", artifact_path=artifact["path"], artifact_sha256=artifact["sha256"])
                continue
            role_signature = company_signature if role == "industry" else hashlib.sha256(json.dumps(sorted(predecessors)).encode()).hexdigest()
            request_cutoff = scope["cutoff"]
            ledger.db.execute("INSERT OR REPLACE INTO quarterly_requests VALUES(?,?,?,?)",
                              (scope["scope_id"], role, role_signature, request_cutoff)); ledger.db.commit()
            args = ["--config", config_path, "--universe", config["paths"]["universe"], "--date", day,
                    "--cutoff", request_cutoff, "--industry", scope["industry_id"], "--role", role, "--mode", "quarterly",
                    "--period-start", scope["period_start"], "--period-end", scope["period_end"],
                    "--critical-gap-status", scope["maturity"]["critical_gap_status"],
                    "--frozen-scope", scope["frozen_scope_path"],
                    "--run-id", f"{run_id}-quarterly-{uuid.uuid4().hex[:8]}",
                    "--lease-seconds", str(int(config["budgets"]["task_timeout_seconds"]) + 180)]
            for path in predecessors: args.extend(["--predecessor-report", path])
            if not research_window_available(config, deadline, prepare_context=True):
                break
            manifest = None
            try:
                context = command(root, "earnings_industry_context.py", args, logs, 120)
                if context.get("model_execution_required"):
                    if not ledger.reserve(day, f"{scope['scope_id']}:{role}", "quarterly", cap):
                        outcomes.append({"status": "queued", "scope_id": scope["scope_id"], "stage": role,
                                         "reason": "quarterly model budget exhausted"})
                        continue
                    qledger.set_stage(scope["scope_id"], role, "running", input_hash=role_signature)
                    manifest = read_json(root / context["artifacts"][0])
                    result = run_role(root, root / context["artifacts"][0], binary=deployed["codex_bin"],
                        timeout=min(int(config["budgets"]["task_timeout_seconds"]), max(1, int(deadline-time.monotonic()))))
                    qledger.set_stage(scope["scope_id"], role, "completed", input_hash=company_signature if role == "industry" else None,
                                      artifact_path=result["report_path"],
                                      artifact_sha256=result["report_sha256"]); outcomes.append(result)
                else:
                    outcomes.append(context)
            except Exception as exc:
                if manifest is not None:
                    fail_owned_attempt(state, manifest, str(exc))
                qledger.set_stage(scope["scope_id"], role, "failed", error=str(exc))
                outcomes.append({"status": "failed", "scope_id": scope["scope_id"], "stage": role, "reason": str(exc)})
                if "preflight rejected before model invocation" in str(exc):
                    ledger.release(day, f"{scope['scope_id']}:{role}", "quarterly")
                if _quota_exhausted(exc):
                    ledger.release(day, f"{scope['scope_id']}:{role}", "quarterly")
                    quota_open = True
                    break
                continue
        # Formal market synthesis waits for every frozen industry synthesis in the same quarter.
        if (not quota_open and ledger.used(day, "quarterly") < cap and review["scopes"]
                and research_window_available(config, deadline, prepare_context=True)):
            first = review["scopes"][0]; reports = []
            quarter_scopes = [{"scope_id": row["scope_id"], "industry_id": row["industry_id"],
                "quarter_id": row["quarter_id"], "revision": row["revision"],
                "cutoff": row["cutoff"],
                "frozen_scope_path": str((root / "runtime/earnings/quarterly-scopes" /
                row["scope_id"] / "revisions" / f"v{row['revision']}" / "frozen-scope.json").relative_to(root))}
                for row in qledger.db.execute("SELECT * FROM quarterly_scopes WHERE quarter_id=? ORDER BY industry_id",
                                              (first["quarter_id"],)).fetchall()]
            for frozen_scope in quarter_scopes:
                artifact = _latest_role_artifact(root, state, frozen_scope["industry_id"], "synthesis", first["period_start"], first["period_end"])
                stage = qledger.db.execute("SELECT state,artifact_path,artifact_sha256 FROM quarterly_stages WHERE scope_id=? AND stage='synthesis'",
                                           (frozen_scope["scope_id"],)).fetchone()
                if (artifact and stage and stage["state"] == "completed" and stage["artifact_path"] == artifact["path"]
                        and stage["artifact_sha256"] == artifact["sha256"]):
                    reports.append(artifact["path"])
            if len(reports) == len(quarter_scopes):
                market_key = f"market:{first['quarter_id']}"
                # Formality is decided by authoritative accepted publication gates in market_context,
                # never by an LLM-authored completeness string.
                market_edition = "full"
                if ledger.reserve(day, market_key, "quarterly", cap):
                    market_signature = hashlib.sha256(json.dumps(sorted(reports)).encode()).hexdigest()
                    request = ledger.db.execute("SELECT input_signature,cutoff FROM quarterly_requests WHERE scope=? AND stage='market'",
                                                (market_key,)).fetchone()
                    # Freeze the market batch at the latest legal per-industry cutoff;
                    # each input remains bound to its own frozen scope cutoff.
                    market_cutoff = max(parse_time(row["cutoff"]) for row in quarter_scopes).isoformat()
                    ledger.db.execute("INSERT OR REPLACE INTO quarterly_requests VALUES(?,?,?,?)",
                                      (market_key, "market", market_signature, market_cutoff)); ledger.db.commit()
                    args = ["--config", config_path, "--universe", config["paths"]["universe"],
                            "--date", day, "--cutoff", market_cutoff, "--period-start", first["period_start"],
                            "--period-end", first["period_end"], "--edition", market_edition,
                            "--run-id", f"{run_id}-market-{uuid.uuid4().hex[:8]}"]
                    for report in reports: args.extend(["--industry-report", report])
                    for frozen_scope in quarter_scopes: args.extend(["--frozen-scope", frozen_scope["frozen_scope_path"]])
                    context = command(root, "earnings_market_context.py", args, logs, 120)
                    if context.get("model_execution_required"):
                        try:
                            market_result = run_role(root, root / context["artifacts"][0], binary=deployed["codex_bin"],
                                timeout=min(int(config["budgets"]["task_timeout_seconds"]), max(1, int(deadline-time.monotonic()))))
                        except Exception as exc:
                            try:
                                market_manifest = read_json(root / context["artifacts"][0])
                                fail_owned_attempt(state, market_manifest, str(exc))
                            except (OSError, KeyError, json.JSONDecodeError, ValueError):
                                pass
                            if "preflight rejected before model invocation" in str(exc):
                                ledger.release(day, market_key, "quarterly")
                            if _quota_exhausted(exc):
                                ledger.release(day, market_key, "quarterly")
                            raise
                        outcomes.append(market_result)
                        for frozen_scope in quarter_scopes: qledger.set_stage(frozen_scope["scope_id"], "market", "completed")
                    else: outcomes.append(context)
        return outcomes or [{"status": "idle", "quarter": review["quarter"]}]
    finally:
        qledger.close()


def run_publication_work(root: Path, config: dict, state: EarningsState, ledger: DailyLedger, deployed: dict,
                         day: str, deadline: float, *, discover: bool = True,
                         config_path: str = "config/earnings_research.json") -> list[dict]:
    """Persist and drain publication work; cloud recovery is independent of discovery/writer budgets."""
    outcomes: list[dict] = []
    writer_cap = int(config["budgets"].get("publication_writers_per_day", 0))
    checker_cap = int(config["budgets"].get("publication_checkers_per_day", 0))
    repair_cap = int(config["budgets"].get("publication_repairs_per_day", 1))
    cloud_cap = int(config["budgets"].get("publication_cloud_operations_per_day", 0))
    backfill_cap = int(config["budgets"].get("publication_backfill_limit", 1))
    rows = state.db.execute("SELECT * FROM report_artifacts WHERE source_mode='live' ORDER BY rowid DESC").fetchall() if discover else []
    seen_series: set[str] = set()
    seen_subjects: set[tuple[str, str, str]] = set()
    for row in rows:
        try:
            report = read_json(root / row["path"]); scope = report.get("scope") or {}
        except Exception as exc:
            outcomes.append({"status": "failed", "source_path": row["path"], "reason": str(exc)})
            continue
        publication_type = None; scope_id = None
        if report["report_type"] == "company":
            event = state.db.execute("SELECT event_kind FROM earnings_events WHERE event_id=?", (row["subject_id"],)).fetchone()
            publication_type = "ipo" if scope.get("event_kind") == "ipo" or (event and event[0] == "ipo") else "company"
            scope_id = scope.get("symbol") or scope.get("issuer_id")
        elif report["report_type"] == "synthesis" and report.get("research_mode") == "quarterly":
            publication_type = "market" if scope.get("industry_id") == "cross-industry" else "industry"
            scope_id = scope.get("market_label") or scope.get("industry_id")
        if not publication_type or not scope_id: continue
        subject_key = (publication_type, str(scope_id), str(row["subject_id"]))
        if subject_key in seen_subjects: continue
        seen_subjects.add(subject_key)
        if publication_type == "ipo" and (not scope.get("reporting_start") or not scope.get("reporting_end")):
            cutoff_day = date.fromisoformat(str(report["cutoff"])[:10]); quarter = f"IPO-{cutoff_day.year}-Q{(cutoff_day.month - 1)//3 + 1}"
        elif scope.get("reporting_end"):
            try: quarter = resolve_report_period(report, form=scope.get("form"))["research_quarter"]
            except ValueError as exc:
                if report["report_type"] == "company":
                    state.db.execute("""INSERT OR REPLACE INTO publication_gaps
                        VALUES(?,?,?,?,?,'actionable',?)""", (row["path"], row["sha256"], publication_type,
                        str(scope_id), str(exc), utc_now()))
                    # Annual/cumulative/undetermined company research is never relabeled as one quarter.
                    continue
                # Quarterly cohort reports already use exact natural-quarter boundaries.
                end = date.fromisoformat(scope["reporting_end"]); quarter = f"{end.year}-Q{(end.month - 1)//3 + 1}"
        else:
            state.db.execute("""INSERT OR REPLACE INTO publication_gaps VALUES(?,?,?,?,?,'actionable',?)""",
                (row["path"], row["sha256"], publication_type, str(scope_id),
                 "reporting period end is undetermined", utc_now()))
            continue
        edition = "full" if report.get("completeness", {}).get("status") == "full" else "stage"
        state.db.execute("UPDATE publication_gaps SET state='resolved',updated_at=? WHERE publication_type=? AND scope_id=?",
                         (utc_now(), publication_type, str(scope_id)))
        series_key = hashlib.sha256(f"{publication_type}:{scope_id}:{quarter}".encode()).hexdigest()
        if series_key in seen_series: continue
        seen_series.add(series_key)
        existing = state.db.execute("SELECT COALESCE(MAX(revision),0) FROM publication_jobs WHERE series_key=?", (series_key,)).fetchone()[0]
        if state.db.execute("SELECT 1 FROM publication_jobs WHERE source_sha256=? AND publication_type=? AND quarter_id=?",
                            (row["sha256"], publication_type, quarter)).fetchone():
            continue
        revision = int(existing) + 1; job_id = hashlib.sha256(f"{series_key}:{revision}".encode()).hexdigest()
        now = utc_now()
        with state.immediate() as db:
            db.execute("UPDATE publication_jobs SET state='superseded',updated_at=? WHERE series_key=? AND state IN ('local_pending','checker_pending','cloud_pending','retryable_failed')",
                       (now, series_key))
            db.execute("""INSERT INTO publication_jobs(job_id,series_key,source_path,source_sha256,publication_type,
                scope_id,quarter_id,edition,revision,state,input_manifest_path,publication_manifest_path,error,
                attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, series_key, row["path"], row["sha256"], publication_type, str(scope_id), quarter, edition,
                 revision, "local_pending", None, None, None, 0, now, now))

    jobs = [dict(row) for row in state.db.execute("""SELECT * FROM publication_jobs
      WHERE state IN ('checker_pending','local_pending')
      OR (state='retryable_failed' AND publication_manifest_path IS NULL)""").fetchall()]
    publication_heads = _latest_company_publication_heads(root, state)
    jobs.sort(key=lambda job: _publication_job_sort_key(root, job, publication_heads))
    for job in jobs:
        if time.monotonic() >= deadline: break
        is_backfill = _publication_job_is_backfill(job, publication_heads)
        if is_backfill and ledger.used(day, "publication_backfill") >= backfill_cap:
            continue
        has_writer = False
        is_repair = False
        try:
            if job.get("input_manifest_path"):
                manifest_path = root / job["input_manifest_path"]
                has_writer = (manifest_path.parent / "writer-stage.json").exists()
            else:
                manifest_path = prepare_publication_input(root, publication_type=job["publication_type"], scope_id=job["scope_id"],
                    quarter_id=job["quarter_id"], source_paths=[root / job["source_path"]], edition=job["edition"],
                    config_path=config_path)
                state.db.execute("UPDATE publication_jobs SET input_manifest_path=?,updated_at=? WHERE job_id=?",
                                 (str(manifest_path.relative_to(root)), utc_now(), job["job_id"]))
            cached = _cached_publication_result(root, manifest_path)
            if cached and cached.get("status") == "success":
                state.db.execute("UPDATE publication_jobs SET state='cloud_pending',publication_manifest_path=?,error=NULL,updated_at=? WHERE job_id=?",
                                 (cached["manifest_path"], utc_now(), job["job_id"]))
                outcomes.append({**cached, "cached": True, "job_id": job["job_id"]})
                continue
            if cached and cached.get("status") == "failed":
                original = read_json(manifest_path)
                if original.get("repair"):
                    state.db.execute("UPDATE publication_jobs SET state='terminal_failed',error=?,updated_at=? WHERE job_id=?",
                                     (_publication_failure_detail(cached), utc_now(), job["job_id"]))
                    outcomes.append({"status": "failed", "job_id": job["job_id"], "reason": cached.get("reason"),
                                     "cached": True, "failure": _publication_failure_detail(cached)})
                    continue
                manifest_path = prepare_repair_input(root, manifest_path)
                state.db.execute("UPDATE publication_jobs SET state='retryable_failed',input_manifest_path=?,error=?,updated_at=? WHERE job_id=?",
                                 (str(manifest_path.relative_to(root)), _publication_failure_detail(cached), utc_now(), job["job_id"]))
                has_writer = False
            is_repair = bool(read_json(manifest_path).get("repair"))
        except Exception as exc:
            exhausted = int(job.get("attempts", 0)) + 1 >= int(config["budgets"].get("max_task_attempts", 2))
            state.db.execute("UPDATE publication_jobs SET state=?,error=?,attempts=attempts+1,updated_at=? WHERE job_id=?",
                             ("terminal_failed" if exhausted else "retryable_failed", str(exc), utc_now(), job["job_id"]))
            outcomes.append({"status": "failed", "job_id": job["job_id"], "reason": str(exc)})
            continue
        remaining = int(deadline - time.monotonic())
        checker_start = int(config["budgets"].get("publication_checker_start_threshold_seconds", 480))
        full_start = int(config["budgets"].get("publication_full_start_threshold_seconds", 900))
        if remaining < (checker_start if has_writer else full_start):
            continue
        if is_repair and ledger.used(day, "publication_repair") >= repair_cap:
            continue
        if has_writer:
            if ledger.used(day, "publication_checker") >= checker_cap:
                continue
            if is_backfill and not ledger.reserve(day, job["job_id"] + ":backfill", "publication_backfill", backfill_cap):
                continue
            if not ledger.reserve(day, job["job_id"] + ":checker", "publication_checker", checker_cap):
                continue
        else:
            if ledger.used(day, "publication_writer") >= writer_cap or ledger.used(day, "publication_checker") >= checker_cap:
                continue
            if is_backfill and not ledger.reserve(day, job["job_id"] + ":backfill", "publication_backfill", backfill_cap):
                continue
            if not ledger.reserve(day, job["job_id"] + ":writer", "publication_writer", writer_cap): continue
            if not ledger.reserve(day, job["job_id"] + ":checker", "publication_checker", checker_cap):
                continue
        if is_repair and not ledger.reserve(day, job["job_id"] + ":repair", "publication_repair", repair_cap):
            continue
        try:
            stage_timeout = int(config["budgets"].get("publication_stage_timeout_seconds",
                                                       config["budgets"]["task_timeout_seconds"]))
            result = run_publication(root, manifest_path, binary=deployed["codex_bin"],
                timeout=min(stage_timeout, max(1, int(deadline-time.monotonic()))))
            if result.get("status") != "success" or not result.get("manifest_path"):
                detail = _publication_failure_detail(result)
                if not is_repair:
                    repair_path = prepare_repair_input(root, manifest_path)
                    state.db.execute("UPDATE publication_jobs SET state='retryable_failed',input_manifest_path=?,error=?,attempts=attempts+1,updated_at=? WHERE job_id=?",
                                     (str(repair_path.relative_to(root)), detail, utc_now(), job["job_id"]))
                else:
                    state.db.execute("UPDATE publication_jobs SET state='terminal_failed',error=?,attempts=attempts+1,updated_at=? WHERE job_id=?",
                                     (detail, utc_now(), job["job_id"]))
                outcomes.append({"status": "failed", "job_id": job["job_id"], "reason": result.get("reason"),
                                 "failure": detail, "repair_scheduled": not is_repair})
                continue
            state.db.execute("UPDATE publication_jobs SET state='cloud_pending',publication_manifest_path=?,error=NULL,attempts=attempts+1,updated_at=? WHERE job_id=?",
                             (result["manifest_path"], utc_now(), job["job_id"])); outcomes.append(result)
            if job["publication_type"] == "industry":
                qledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
                try:
                    qscope = qledger.db.execute("SELECT scope_id FROM quarterly_scopes WHERE industry_id=? AND quarter_id=?",
                                                (job["scope_id"], job["quarter_id"])).fetchone()
                    if qscope:
                        qledger.set_stage(qscope[0], "publication", "completed", artifact_path=result["manifest_path"])
                        qledger.set_stage(qscope[0], "checker", "completed", artifact_path=result["manifest_path"])
                finally: qledger.close()
        except Exception as exc:
            if _quota_exhausted(exc):
                for suffix, role_name in ((":writer", "publication_writer"), (":checker", "publication_checker"),
                                          (":repair", "publication_repair"), (":backfill", "publication_backfill")):
                    ledger.release(day, job["job_id"] + suffix, role_name)
                next_state = "checker_pending" if (manifest_path.parent / "writer-stage.json").exists() else "retryable_failed"
                state.db.execute("UPDATE publication_jobs SET state=?,error=?,updated_at=? WHERE job_id=?",
                                 (next_state, str(exc), utc_now(), job["job_id"]))
                attempt_path = manifest_path.parent / "attempt-state.json"
                attempt = read_json(attempt_path) if attempt_path.exists() else None
                outcomes.append({"status": "failed", "job_id": job["job_id"], "reason": str(exc), "attempt": attempt})
                break
            exhausted = int(job.get("attempts", 0)) + 1 >= int(config["budgets"].get("max_task_attempts", 2))
            next_state = "terminal_failed" if exhausted else ("checker_pending" if (manifest_path.parent / "writer-stage.json").exists() else "retryable_failed")
            state.db.execute("UPDATE publication_jobs SET state=?,error=?,attempts=attempts+1,updated_at=? WHERE job_id=?",
                             (next_state, str(exc), utc_now(), job["job_id"]))
            attempt_path = manifest_path.parent / "attempt-state.json"
            attempt = read_json(attempt_path) if attempt_path.exists() else None
            outcomes.append({"status": "failed", "job_id": job["job_id"], "reason": str(exc), "attempt": attempt})
            if "model_capacity_unavailable" in str(exc).lower():
                break

    cloud_states = "'cloud_pending','auth_failed','readback_failed'" + (",'archived'" if config["delivery"].get("lark_documents_enabled") is True else "")
    cloud_jobs = state.db.execute(f"SELECT * FROM publication_jobs WHERE state IN ({cloud_states}) OR (state='retryable_failed' AND publication_manifest_path IS NOT NULL) ORDER BY revision DESC,updated_at").fetchall()
    for raw in cloud_jobs:
        if time.monotonic() >= deadline: break
        job = dict(raw)
        newer = state.db.execute("SELECT 1 FROM publication_jobs WHERE series_key=? AND revision>? AND state!='superseded'",
                                 (job["series_key"], job["revision"])).fetchone()
        if newer:
            state.db.execute("UPDATE publication_jobs SET state='superseded',updated_at=? WHERE job_id=?", (utc_now(), job["job_id"])); continue
        if config["delivery"].get("lark_documents_enabled") is not True:
            state.db.execute("UPDATE publication_jobs SET state='archived',updated_at=? WHERE job_id=?", (utc_now(), job["job_id"]))
            if job["publication_type"] == "industry":
                qledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
                try:
                    qscope = qledger.db.execute("SELECT scope_id FROM quarterly_scopes WHERE industry_id=? AND quarter_id=?",
                                                (job["scope_id"], job["quarter_id"])).fetchone()
                    if qscope: qledger.set_stage(qscope[0], "cloud", "completed")
                finally: qledger.close()
            continue
        if not ledger.reserve(day, job["job_id"] + ":cloud", "publication_cloud", cloud_cap): break
        try:
            pub = read_json(root / job["publication_manifest_path"]); artifact = pub["artifacts"]["markdown"]
            cloud_timeout = max(1, min(120, int(deadline - time.monotonic())))
            cloud = LarkDocumentPublisher(root, deployed["lark_documents"], timeout=cloud_timeout).publish(pub["publication_id"], pub["title"],
                root / artifact["path"], expected_sha256=artifact["sha256"], series_id=pub.get("series_id"),
                publication_manifest=root / job["publication_manifest_path"])
            final_state = "complete" if cloud["state"] == "verified" else cloud["state"]
            state.db.execute("UPDATE publication_jobs SET state=?,error=?,updated_at=? WHERE job_id=?",
                             (final_state, cloud.get("reason"), utc_now(), job["job_id"]))
            if final_state == "complete" and job["publication_type"] == "industry":
                qledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
                try:
                    qscope = qledger.db.execute("SELECT scope_id FROM quarterly_scopes WHERE industry_id=? AND quarter_id=?",
                                                (job["scope_id"], job["quarter_id"])).fetchone()
                    if qscope: qledger.set_stage(qscope[0], "cloud", "completed")
                finally: qledger.close()
            outcomes.append({"status": "success" if final_state == "complete" else "failed", "job_id": job["job_id"], "cloud": cloud})
        except Exception as exc:
            state.db.execute("UPDATE publication_jobs SET state='retryable_failed',error=?,updated_at=? WHERE job_id=?", (str(exc), utc_now(), job["job_id"]))
            outcomes.append({"status": "failed", "job_id": job["job_id"], "reason": str(exc)})
    state.db.commit()
    return outcomes


def render_publication_entries(rows: list[tuple[dict, dict, dict]]) -> list[str]:
    """Never truncate checked cloud report entries; notification size is gated later as a whole."""
    return [f"- {publication['title']}（{publication['edition']} v{publication['version']}）：{cloud['url']}"
            for publication, cloud, _version in rows]


def operational_issue_summary(errors: list[str]) -> str:
    categories: dict[str, int] = {}
    for error in errors:
        category = str(error).split(":", 1)[0].replace("model-circuit", "模型额度")
        categories[category] = categories.get(category, 0) + 1
    return "；".join(f"{name} {count} 项" for name, count in sorted(categories.items()))


def summarize_batch_usage(root: Path, day: str, reports: list[dict], publications: list[dict]) -> dict:
    """Count durable model call identities once, including failed and gap-review calls."""
    calls: dict[str, dict] = {}
    zone = ZoneInfo("Asia/Shanghai")
    result_paths = list((root / "runtime/earnings/runs").glob("**/runner-result.json"))
    result_paths += list((root / "runtime/earnings/quarterly-scopes").glob("**/runner-result.json"))
    for result_path in result_paths:
        try:
            result = read_json(result_path)
        except (OSError, json.JSONDecodeError):
            continue
        at = parse_time(result.get("completed_at") or result.get("failed_at"))
        if not at or at.astimezone(zone).date().isoformat() != day:
            continue
        call_id = result.get("call_id") or f"legacy-result:{result_path.relative_to(root)}"
        calls[call_id] = {"role": result.get("model"), "usage": result.get("usage"),
                          "status": result.get("status")}
    for state_path in (root / "runtime/earnings/publications/runs").glob("**/attempt-state.json"):
        try:
            state = read_json(state_path)
        except (OSError, json.JSONDecodeError):
            continue
        for call in state.get("calls", []):
            started = parse_time(call.get("started_at"))
            if started and started.astimezone(zone).date().isoformat() == day:
                call_id = call.get("call_id") or f"legacy-publication:{state_path.relative_to(root)}:{call.get('role')}:{call.get('attempt')}"
                calls[call_id] = {"role": call.get("role"), "usage": call.get("usage"),
                                  "status": call.get("status")}
    totals = {key: 0 for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")}
    missing = 0
    for call in calls.values():
        usage = call.get("usage")
        if not isinstance(usage, dict):
            missing += 1
            continue
        for key in totals:
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
    totals["non_cached_input_tokens"] = max(0, totals["input_tokens"] - totals["cached_input_tokens"])
    cached_jobs = {str(row.get("job_id") or row.get("publication_id")) for row in publications if row.get("cached")}
    return {"actual_model_calls": len(calls), "calls_with_usage_null": missing,
            "cached_results_reused": len(cached_jobs), **totals}


def finalize(root: Path, deployed: dict, deployed_path: Path, day: str, reports: list[dict], errors: list[str],
             state: EarningsState, *, send: bool) -> dict:
    material = []
    handled = set()
    for decision_path in runtime_path(root, "runtime/earnings/outbox").glob("*/decision.json"):
        prior_decision = read_json(decision_path)
        if prior_decision.get("state") in {"sent", "unknown", "sending", "suppressed"}:
            handled.update(row["sha256"] for row in prior_decision.get("report_versions", []))
    # Recover registered reports even if a previous outer process died before finalization.
    candidates = []
    rows = state.db.execute("""SELECT a.*,a.rowid AS revision,e.issuer_id FROM report_artifacts a
      LEFT JOIN earnings_events e ON a.subject_id=e.event_id WHERE a.source_mode='live'
      ORDER BY a.period_end DESC,a.rowid DESC""").fetchall()
    latest = set()
    for row in rows:
        scope_key = (row["issuer_id"] or row["subject_id"], row["report_type"])
        if scope_key in latest:
            continue
        latest.add(scope_key)
        if row["sha256"] not in handled:
            candidates.append({"report_path": row["path"], "report_sha256": row["sha256"], "task_id": row["task_id"], "revision": row["revision"]})
    for result in candidates:
        report = read_json(root / result["report_path"])
        task = state.db.execute("SELECT subject_id,period_start,period_end FROM research_tasks WHERE task_id=?", (report["task_id"],)).fetchone()
        previous = state.db.execute("""SELECT path FROM report_artifacts WHERE subject_id=? AND report_type=? AND rowid<?
          AND period_start IS ? AND period_end IS ? AND source_mode='live' ORDER BY rowid DESC LIMIT 1""",
          (task[0], report["report_type"], result["revision"], task[1], task[2])).fetchone()
        prior = read_json(root / previous[0]) if previous else None
        if notification_material(report, prior):
            material.append((report, result))
    # A checked formal quarterly publication is independently deliverable even when thesis_state is unchanged.
    publication_material = []
    key_symbols = {symbol for industry in read_json(root / "config/earnings_universe.json")["industries"]
                   for symbol in industry.get("key_symbols", [])}
    publication_heads = _latest_company_publication_heads(root, state)
    cloud_by_id = {}
    for cloud_path in runtime_path(root, "runtime/earnings/publications/cloud").glob("*/state.json"):
        cloud = read_json(cloud_path); cloud_by_id[cloud.get("publication_id")] = cloud
    for manifest_path in sorted((root / "report/earnings/publications").glob("*/*/*/v*/publication-manifest.json")):
        digest = sha256_file(manifest_path)
        if digest in handled: continue
        publication = read_json(manifest_path); cloud = cloud_by_id.get(publication["publication_id"], {})
        formal = publication["publication_type"] in {"industry", "market"} and publication["edition"] in {"full", "revision"}
        priority_company = publication["publication_type"] in {"company", "ipo"} and publication["scope_id"] in key_symbols
        current_company = priority_company and _publication_matches_current_head(publication, publication_heads)
        if publication.get("checker", {}).get("status") == "passed" and cloud.get("state") == "verified" and (formal or current_company):
            publication_material.append((publication, cloud, {"path": str(manifest_path.relative_to(root)), "sha256": digest}))
    lines = [f"财报研究 · {day}", ""]
    versions = [{"path": r["report_path"], "sha256": r["report_sha256"]} for r in candidates]
    for report, result in material[:5]:
        title = report["scope"].get("symbol") or report["scope"].get("industry_id") or report["scope"].get("issuer_id")
        lines.append(f"{title}（期间结束：{report['scope'].get('reporting_end') or '未确定'}）：{str(report.get('change_summary', ''))[:650]}")
        lines.append(f"状态：{report['thesis_state']}；完整度：{report['completeness']['status']}。")
        for item in report["evidence"][:2]:
            lines.append(f"来源：{item['source_url']}")
        checks = report.get("next_checks", [])
        if checks:
            lines.append(f"后续核验：{str(checks[0])[:200]}")

    if len(material) > 5:
        lines.append(f"另有 {len(material)-5} 份状态变化报告已归档，本条合并通知。")
    if publication_material:
        lines.extend(["", "本批次已核对全文："])
        lines.extend(render_publication_entries(publication_material))
        for publication, cloud, version in publication_material:
            versions.append(version)
    else:
        lines.extend(["", "今日新增并回读全文：0 份。"])
    if errors:
        lines.append(f"待处理问题：{operational_issue_summary(errors)}；详细诊断已保留在 NAS 本地审计记录。")
    delivery_audit = (f"交付审计：今日新增全文 {len(publication_material)} 份"
                      + ("，以上链接均已由显式用户身份写入并回读；" if publication_material else "；")
                      + "消息仅由仓库绑定的 cc-connect 发送。")
    lines.extend(["", "数据质量：仅采用已归档的 SEC/发行人披露，覆盖缺口随报告保留。",
                  f"运行校验：本批次完成 {len(reports)} 个角色，失败步骤 {len(errors)} 个。",
                  delivery_audit])
    decision = prepare_notification(root, deployed, day=day, body="\n".join(lines), report_versions=versions,
        kind="daily", rationale="evidenced thesis-state change or checked formal publication" if material or publication_material else "no material thesis-state change or deliverable publication",
        should_send=bool(material or publication_material))
    outcome = deliver(root, decision, deployed_path, execute=send)
    return {"decision": str(decision.relative_to(root)), "delivery": outcome}


def run(args: argparse.Namespace) -> dict:
    root = Path(args.repo_root).resolve()
    runtime_path(root, "runtime/earnings").mkdir(parents=True, exist_ok=True)
    config, _ = load_config(root, args.config)
    deployed_path = runtime_path(root, args.deployment)
    deployed = destination(root, deployed_path)
    if config["paths"]["state"] != "runtime/earnings/state.sqlite":
        raise ValueError("earnings daily runner requires canonical earnings state path")
    if config["budgets"].get("concurrency") != 1:
        raise ValueError("earnings daily runner requires concurrency=1")
    if config["budgets"].get("max_tokens_per_day") is not None or config["budgets"].get("currency_budget_per_day") is not None:
        raise ValueError("hard token/currency budget unavailable for auth backend; configure observable task limits")
    batch_seconds = int(deployed.get("batch_timeout_seconds", 7200))
    if not 60 <= batch_seconds <= 14400:
        raise ValueError("batch_timeout_seconds must be between 60 and 14400")
    if not 60 <= int(config["budgets"]["task_timeout_seconds"]) <= 7200:
        raise ValueError("task_timeout_seconds must be between 60 and 7200")
    now = datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    cutoff = now.isoformat()
    run_id = f"daily-{day}-{uuid.uuid4().hex[:12]}"
    logs = runtime_path(root, f"runtime/earnings/runs/{run_id}")
    logs.mkdir(parents=True)
    with exclusive_lock(runtime_path(root, "runtime/earnings/daily.lock")):
        ledger = DailyLedger(root)
        state = EarningsState(root / "runtime/earnings/state.sqlite")
        reports, errors = [], []
        model_circuit_open = False
        deadline = time.monotonic() + batch_seconds
        phase_reserve = min(int(config["budgets"].get("phase_reserve_seconds", 300)), max(0, batch_seconds // 4))
        early_deadline = deadline - phase_reserve
        limit = season_limit(config, day)
        config_args = ["--config", args.config]
        common = [*config_args, "--date", day, "--cutoff", cutoff]
        context_common = [*common, "--universe", config["paths"]["universe"]]
        universe = read_json(root / config["paths"]["universe"])
        try:
            if not args.resume_only:
                symbols = sorted({issuer["symbol"] for industry in universe["industries"] for issuer in industry["issuers"]})
                initialized = {r[0] for r in ledger.db.execute("SELECT symbol FROM initialized")}
                already = ledger.used(day, "initialization")
                pending = [s for s in symbols if s not in initialized][:max(0, int(config["budgets"]["initialization_company_limit"]) - already)]
                for symbol in symbols:
                    if time.monotonic() >= early_deadline:
                        errors.append("collection budget deadline reached; remaining issuers deferred")
                        break
                    initialization = symbol in pending
                    if initialization:
                        ledger.reserve(day, symbol, "initialization", int(config["budgets"]["initialization_company_limit"]))
                    try:
                        collected = command(root, "earnings_collect.py", [*common, "--mode", "live", "--symbol", symbol,
                            "--collection-kind", "initialization" if initialization else "incremental"], logs, min(600, max(1, int(early_deadline-time.monotonic()))))
                        failures = collected.get("summary", {}).get("failures", [])
                        if failures:
                            errors.append(f"source:{symbol}:incomplete")
                        elif initialization and collected.get("summary", {}).get("initialization_coverage", {}).get(symbol, {}).get("complete") is True:
                            ledger.db.execute("INSERT OR REPLACE INTO initialized VALUES(?,?)", (symbol, utc_now())); ledger.db.commit()
                    except (RuntimeError, subprocess.TimeoutExpired) as exc:
                        errors.append(str(exc))
                        if "TCA_SEC_USER_AGENT" in str(exc):
                            break
            if not args.collect_only:
                publications = []
                if config.get("publication", {}).get("enabled") is True:
                    try:
                        # Drain recoverable checker/cloud work before new research can consume the batch clock.
                        recovery_deadline = min(deadline, time.monotonic() + max(1, phase_reserve))
                        resumed_publications = run_publication_work(root, config, state, ledger, deployed, day, recovery_deadline,
                                                                   discover=False, config_path=args.config)
                        publications.extend(resumed_publications)
                        errors.extend(f"publication-resume:{row.get('job_id', 'unknown')}:{row.get('reason', 'failed')}"
                                      for row in resumed_publications if row.get("status") == "failed")
                        model_circuit_open = any(_quota_exhausted(row.get("reason")) for row in resumed_publications)
                    except Exception as exc:
                        publications.append({"status": "failed", "reason": str(exc)})
                        errors.append(f"publication-resume:{exc}")
                context_attempts = 0
                history_limit = min(limit, max(0, int(config["budgets"].get("company_history_limit", 2))))
                current_limit = max(0, limit - history_limit)
                current_exhausted = current_limit == 0
                current_used = history_used = 0
                while (not model_circuit_open and ledger.used(day, "company") < limit
                       and research_window_available(config, early_deadline, prepare_context=True)
                       and context_attempts < limit * 2):
                    context_attempts += 1
                    tier = "history" if current_exhausted else "current"
                    if tier == "history" and history_used >= history_limit:
                        break
                    try:
                        context = command(root, "earnings_context.py", [*context_common, "--limit", "1", "--company-tier", tier,
                            "--run-id", f"{run_id}-{uuid.uuid4().hex[:8]}",
                            "--lease-seconds", str(int(config["budgets"]["task_timeout_seconds"]) + 180)], logs, 120)
                    except (RuntimeError, subprocess.TimeoutExpired) as exc:
                        errors.append(f"company-context:{exc}")
                        continue
                    manifests = context.get("manifests", [])
                    if not manifests:
                        if tier == "current":
                            current_exhausted = True
                            continue
                        break
                    for path in manifests:
                        manifest = read_json(root / path)
                        if not ledger.reserve(day, manifest["task_id"], "company", limit):
                            break
                        if tier == "history": history_used += 1
                        else: current_used += 1
                        try:
                            reports.append(run_role(root, root / path, binary=deployed["codex_bin"],
                                timeout=min(int(config["budgets"]["task_timeout_seconds"]), max(1, int(early_deadline-time.monotonic())))))
                        except Exception as exc:
                            fail_owned_attempt(state, manifest, str(exc))
                            if "preflight rejected before model invocation" in str(exc):
                                ledger.release(day, manifest["task_id"], "company")
                                if tier == "history": history_used -= 1
                                else: current_used -= 1
                            if _quota_exhausted(exc):
                                ledger.release(day, manifest["task_id"], "company")
                            errors.append(f"role:{manifest['task_id']}:{exc}")
                            if _quota_exhausted(exc):
                                model_circuit_open = True
                                errors.append("model-circuit:quota exhausted; remaining model work deferred")
                                break
                    if tier == "current" and current_used >= current_limit:
                        current_exhausted = True
                if config.get("publication", {}).get("enabled") is True and not model_circuit_open:
                    try:
                        # Publish accepted current-company research before downstream work can consume
                        # the remaining batch clock. The final pass discovers later formal reports.
                        current_publications = run_publication_work(root, config, state, ledger, deployed, day,
                                                                   early_deadline, config_path=args.config)
                        publications.extend(current_publications)
                        errors.extend(f"publication-current:{row.get('job_id', 'unknown')}:{row.get('reason', 'failed')}"
                                      for row in current_publications if row.get("status") == "failed")
                        model_circuit_open = any(_quota_exhausted(row.get("reason")) for row in current_publications)
                    except Exception as exc:
                        publications.append({"status": "failed", "reason": str(exc)})
                        errors.append(f"publication-current:{exc}")
                for work in ([] if model_circuit_open else industry_work(root, state, universe, ledger)):
                    cap = int(config["budgets"]["daily_industry_limit"])
                    if (ledger.used(day, "industry") >= cap
                            or not research_window_available(config, early_deadline, prepare_context=True)):
                        break
                    try:
                        request = ledger.db.execute("SELECT fingerprint,cutoff FROM industry_requests WHERE scope=?", (work["scope"],)).fetchone()
                        request_cutoff = request["cutoff"] if request and request["fingerprint"] == work["fingerprint"] else cutoff
                        ledger.db.execute("INSERT OR REPLACE INTO industry_requests VALUES(?,?,?)", (work["scope"], work["fingerprint"], request_cutoff)); ledger.db.commit()
                        industry_common = [*config_args, "--universe", config["paths"]["universe"], "--date", day, "--cutoff", request_cutoff]
                        context = command(root, "earnings_industry_context.py", [*industry_common, "--industry", work["industry"],
                            "--role", "industry", "--mode", "daily", "--period-start", work["start"], "--period-end", work["end"],
                            "--run-id", f"{run_id}-{uuid.uuid4().hex[:8]}", "--lease-seconds", str(int(config["budgets"]["task_timeout_seconds"]) + 180)], logs, 120)
                        if not context.get("model_execution_required"):
                            if context.get("task_state") in {"completed", "terminal_failed"}:
                                ledger.db.execute("INSERT OR REPLACE INTO industry_inputs VALUES(?,?)", (work["scope"], work["fingerprint"])); ledger.db.commit()
                            if context.get("task_state") == "terminal_failed":
                                errors.append(f"industry:{work['industry']}:maximum attempts exhausted for unchanged inputs")
                            continue
                        path = context["artifacts"][0]
                        manifest = read_json(root / path)
                        ledger.reserve(day, manifest["task_id"], "industry", cap)
                        try:
                            reports.append(run_role(root, root / path, binary=deployed["codex_bin"],
                                timeout=min(int(config["budgets"]["task_timeout_seconds"]), max(1, int(early_deadline-time.monotonic())))))
                        except Exception as exc:
                            fail_owned_attempt(state, manifest, str(exc))
                            if "preflight rejected before model invocation" in str(exc):
                                ledger.release(day, manifest["task_id"], "industry")
                            raise
                        ledger.db.execute("INSERT OR REPLACE INTO industry_inputs VALUES(?,?)", (work["scope"], work["fingerprint"])); ledger.db.commit()
                    except Exception as exc:
                        errors.append(f"industry:{work['industry']}:{exc}")
                        if _quota_exhausted(exc):
                            model_circuit_open = True
                            errors.append("model-circuit:quota exhausted; remaining model work deferred")
                            break
                try:
                    if model_circuit_open:
                        quarterly = [{"status": "queued", "reason": "model quota circuit open; deferred"}]
                    else:
                        quarterly = run_quarterly_step(root, config, universe, state, ledger, deployed, day, cutoff,
                                                       run_id, logs, deadline, args.config, getattr(args, "manual_quarter", None))
                    reports.extend(row for row in quarterly if row.get("report_path"))
                    if any(_quota_exhausted(row.get("reason")) for row in quarterly):
                        model_circuit_open = True
                        errors.append("model-circuit:quota exhausted in quarterly work; publication deferred")
                    errors.extend(f"quarterly:{row.get('scope_id', 'market')}:{row.get('stage', 'unknown')}:{row.get('reason', row['status'])}"
                                  for row in quarterly if row.get("status") in {"retryable_failed", "terminal_failed", "failed"})
                except Exception as exc:
                    quarterly = [{"status": "failed", "reason": str(exc)}]
                    errors.append(f"quarterly:{exc}")
                    if _quota_exhausted(exc):
                        model_circuit_open = True
                        errors.append("model-circuit:quota exhausted in quarterly work; publication deferred")
                if config.get("publication", {}).get("enabled") is True and not model_circuit_open:
                    try:
                        new_publications = run_publication_work(root, config, state, ledger, deployed, day, deadline,
                                                               config_path=args.config)
                        publications.extend(new_publications)
                        errors.extend(f"publication:{row.get('job_id', 'unknown')}:{row.get('reason', 'failed')}"
                                      for row in new_publications if row.get("status") == "failed")
                    except Exception as exc:
                        publications.append({"status": "failed", "reason": str(exc)})
                        errors.append(f"publication:{exc}")
                else:
                    publications = [{"status": "skipped", "reason": "publication is explicitly disabled"}]
            else:
                quarterly = [{"status": "skipped", "reason": "collect-only batch"}]
                publications = []
            exhausted = unresolved_terminal_count(state)
            if exhausted:
                errors.append(f"terminal_tasks:{exhausted} current research tasks require operator review")
            publication_exhausted = state.db.execute("""SELECT COUNT(*) FROM publication_jobs p
              WHERE p.state='terminal_failed' AND NOT EXISTS(SELECT 1 FROM publication_jobs n
                WHERE n.series_key=p.series_key AND n.revision>p.revision)""").fetchone()[0]
            if publication_exhausted:
                errors.append(f"terminal_publications:{publication_exhausted} publication jobs require operator review")
            publication_gaps = state.db.execute("SELECT COUNT(*) FROM publication_gaps WHERE state='actionable'").fetchone()[0]
            if publication_gaps:
                errors.append(f"publication_gaps:{publication_gaps} reports require fiscal-period review")
            delivery = finalize(root, deployed, deployed_path, day, reports, errors, state, send=args.send)
            ledger.db.execute("INSERT OR REPLACE INTO health VALUES(?,?)", (day, int(bool(errors))))
            ledger.db.commit()
            health = ledger.db.execute("SELECT failed FROM health ORDER BY day DESC LIMIT 2").fetchall()
            normal_notified = delivery.get("delivery", {}).get("state") in {"sent", "sending", "unknown"}
            if errors and not normal_notified and len(health) == 2 and all(row[0] for row in health):
                # Stable category text deduplicates the same persistent event across days.
                body = ("财报研究连续两个日批次仍有未完成步骤。已保留可恢复队列，未自动扩大模型预算。\n"
                        + operational_issue_summary(errors))
                failure = prepare_notification(root, deployed, day=day, body=body, report_versions=[], kind="failure",
                    rationale="two distinct failed daily batches", should_send=True)
                delivery["failure_delivery"] = deliver(root, failure, deployed_path, execute=args.send)
            result = {"schema_version": 1, "workflow": "earnings-daily", "status": "success" if not errors else "failed",
                "run_id": run_id, "date": day, "cutoff": cutoff, "reports": reports, "errors": errors,
                "publications": publications, "quarterly": quarterly, "delivery": delivery, "completed_at": utc_now(),
                "usage_summary": summarize_batch_usage(root, day, reports, publications),
                "quarterly_automatic_trigger": bool(config["quarterly"].get("automatic_trigger_enabled"))}
            atomic_write_json(logs / "daily-result.json", result)
            return result
        finally:
            state.close(); ledger.db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--config", default="config/earnings_research.json")
    parser.add_argument("--deployment", default="runtime/earnings/deployment.json")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--resume-only", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--manual-quarter", help="Explicitly advance one ended YYYY-QN scope using the same maturity gates")
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise InterruptedError(f"batch interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        result = run(args)
    except BlockingIOError:
        result = {"status": "skipped", "reason": "another earnings process holds the lock"}
    except Exception as exc:
        result = {"status": "failed", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result["status"] == "failed" else 0)


if __name__ == "__main__":
    main()
