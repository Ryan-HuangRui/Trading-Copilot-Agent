#!/usr/bin/env python3
"""Deterministic fiscal-period mapping and frozen quarterly review readiness."""
from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from earnings_common import ROOT, atomic_write_json, canonical_json, ensure_inside, load_config, parse_time, read_json, sha256_bytes, sha256_file, stable_id, utc_now
from earnings_state import EarningsState


def _calendar_quarter(value: date) -> tuple[date, date, str]:
    month = ((value.month - 1) // 3) * 3 + 1
    start = date(value.year, month, 1)
    end_month = month + 2
    end = date(value.year, end_month, calendar.monthrange(value.year, end_month)[1])
    return start, end, f"{value.year}-Q{(month - 1) // 3 + 1}"


def _quarter_candidates(start: date, end: date) -> list[tuple[date, date, str]]:
    cursor, _, _ = _calendar_quarter(start)
    rows = []
    while cursor <= end:
        q_start, q_end, q_id = _calendar_quarter(cursor)
        rows.append((q_start, q_end, q_id))
        cursor = q_end + timedelta(days=1)
    return rows


def map_fiscal_period(start_text: str | None, end_text: str | None, *, form: str | None = None) -> dict[str, Any]:
    """Map a standalone operating quarter to the calendar quarter with greatest overlap.

    The actual issuer period remains authoritative. The mapping is only a deterministic
    cohort policy; annual and cumulative durations are rejected instead of being treated
    as a single quarter.
    """
    if not start_text or not end_text:
        raise ValueError("missing fiscal period boundary")
    start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
    if end < start:
        raise ValueError("fiscal period end precedes start")
    days = (end - start).days + 1
    normalized_form = (form or "").upper()
    if normalized_form in {"10-K", "20-F", "40-F"} or days >= 300:
        raise ValueError("annual disclosure cannot be mapped as a standalone quarter")
    if not 70 <= days <= 110:
        raise ValueError("period is not a standalone quarter")
    overlaps = []
    for q_start, q_end, q_id in _quarter_candidates(start, end):
        overlap = max(0, (min(end, q_end) - max(start, q_start)).days + 1)
        overlaps.append((overlap, q_id, q_start, q_end))
    overlap, q_id, q_start, q_end = max(overlaps, key=lambda row: (row[0], row[1]))
    return {
        "mapping_policy": "maximum-calendar-quarter-overlap-v1",
        "research_quarter": q_id,
        "calendar_period": {"start": q_start.isoformat(), "end": q_end.isoformat()},
        "actual_period": {"start": start_text, "end": end_text},
        "actual_duration_days": days,
        "overlap_days": overlap,
        "overlap_ratio": round(overlap / days, 6),
        "cross_period_difference": start != q_start or end != q_end,
    }


def resolve_report_period(report: dict[str, Any], *, form: str | None = None) -> dict[str, Any]:
    """Resolve a unique standalone period from verified report evidence without mutating the report."""
    scope = report.get("scope") or {}
    if scope.get("reporting_start") and scope.get("reporting_end"):
        mapped = map_fiscal_period(scope["reporting_start"], scope["reporting_end"], form=form or scope.get("form"))
        return {**mapped, "resolution": "report-scope", "evidence_ids": []}
    scope_end = scope.get("reporting_end")
    candidates: dict[tuple[str, str], set[str]] = {}
    for evidence in report.get("evidence", []):
        boundaries = []
        if evidence.get("reporting_start") and evidence.get("reporting_end"):
            boundaries.append((evidence["reporting_start"], evidence["reporting_end"]))
        for fact in evidence.get("numeric_facts", []):
            period = fact.get("period") or {}
            if period.get("kind") == "duration" and period.get("start") and period.get("end"):
                boundaries.append((period["start"], period["end"]))
        for start, end in boundaries:
            if scope_end and end != scope_end: continue
            try: map_fiscal_period(start, end, form=form or scope.get("form"))
            except ValueError: continue
            candidates.setdefault((start, end), set()).add(str(evidence.get("evidence_id")))
    if len(candidates) != 1:
        raise ValueError("standalone fiscal period requires one unique evidence-supported boundary")
    (start, end), evidence_ids = next(iter(candidates.items()))
    mapped = map_fiscal_period(start, end, form=form or scope.get("form"))
    return {**mapped, "resolution": "evidence-period-review-v1", "evidence_ids": sorted(evidence_ids)}


def review_quarter_for_day(day: date, seasons: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return the latest ended natural quarter and whether the date is in its tail window."""
    this_start, _, _ = _calendar_quarter(day)
    prior_end = this_start - timedelta(days=1)
    q_start, q_end, q_id = _calendar_quarter(prior_end)
    if seasons:
        windows = []
        for season in seasons:
            start_month, start_day = map(int, season["strong_end"].split("-"))
            end_month, end_day = map(int, season["tail_end"].split("-"))
            windows.append((start_month, start_day + 1, end_month, end_day))
    else:
        windows = [(3, 16, 3, 31), (5, 21, 5, 31), (8, 21, 8, 31), (11, 21, 11, 30)]
    tail = False; deadline_reached = False
    for first_month, first_day, last_month, last_day in windows:
        start = date(day.year, first_month, min(first_day, calendar.monthrange(day.year, first_month)[1]))
        end = date(day.year, last_month, min(last_day, calendar.monthrange(day.year, last_month)[1]))
        if start <= day <= end:
            tail = True; deadline_reached = day == end; break
    return {"quarter_id": q_id, "period_start": q_start.isoformat(), "period_end": q_end.isoformat(),
            "tail_window": tail, "deadline_reached": deadline_reached, "as_of": day.isoformat()}


def _quarter_deadline(quarter_id: str, seasons: list[dict[str, Any]]) -> date:
    year_text, number_text = quarter_id.split("-Q"); year, number = int(year_text), int(number_text)
    season_start_month = (number * 3) % 12 + 1
    season_year = year + int(number == 4)
    season = next(row for row in seasons if int(row["start"].split("-")[0]) == season_start_month)
    month, day = map(int, season["tail_end"].split("-"))
    return date(season_year, month, min(day, calendar.monthrange(season_year, month)[1]))


def assess_industry_maturity(*, expected_issuer_ids: Iterable[str], key_issuer_ids: Iterable[str],
                             disclosed_issuer_ids: Iterable[str], fetched_issuer_ids: Iterable[str],
                             researched_issuer_ids: Iterable[str], critical_gap_status: str,
                             threshold: float = 0.9) -> dict[str, Any]:
    expected = list(dict.fromkeys(expected_issuer_ids))
    key = set(key_issuer_ids); disclosed = set(disclosed_issuer_ids); fetched = set(fetched_issuer_ids)
    researched = set(researched_issuer_ids)
    expected_set = set(expected)
    disclosed &= expected_set; fetched &= disclosed; researched &= fetched
    key_missing = sorted(key - researched)
    ratio = len(disclosed) / len(expected) if expected else 0.0
    counts = {"expected_issuers": len(expected), "disclosed_issuers": len(disclosed),
              "fetched_issuers": len(fetched), "researched_issuers": len(researched),
              "key_missing_issuers": key_missing}
    return {"counts": counts, "coverage_ratio": ratio, "threshold": threshold,
            "key_issuers_complete": not key_missing,
            "company_research_complete": disclosed <= researched,
            "critical_gap_status": critical_gap_status,
            "eligible_full": bool(ratio >= threshold and not key_missing and disclosed <= researched
                                  and critical_gap_status in {"resolved", "disclosed"})}


class QuarterlyReviewLedger:
    """Incrementally migrated P4 state, separate from immutable research artifacts."""

    STAGES = ("coverage", "gap_review", "industry", "challenge", "synthesis", "publication", "checker", "cloud", "market")

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS quarterly_scopes(
          scope_id TEXT PRIMARY KEY, quarter_id TEXT NOT NULL, industry_id TEXT NOT NULL,
          period_start TEXT NOT NULL, period_end TEXT NOT NULL, frozen_universe_json TEXT NOT NULL,
          frozen_universe_hash TEXT NOT NULL, cutoff TEXT NOT NULL, edition TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(quarter_id,industry_id,edition));
        CREATE TABLE IF NOT EXISTS quarterly_stages(
          scope_id TEXT NOT NULL, stage TEXT NOT NULL, state TEXT NOT NULL,
          input_hash TEXT, artifact_path TEXT, artifact_sha256 TEXT, attempts INTEGER NOT NULL DEFAULT 0,
          error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(scope_id,stage));
        CREATE TABLE IF NOT EXISTS weekly_reviews(
          week_end TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, actionable INTEGER NOT NULL,
          artifact_path TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS quarterly_gap_reviews(
          scope_id TEXT NOT NULL, input_hash TEXT NOT NULL, cutoff TEXT NOT NULL,
          status TEXT NOT NULL, artifact_path TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
          created_at TEXT NOT NULL, PRIMARY KEY(scope_id,input_hash));
        CREATE TABLE IF NOT EXISTS quarterly_gap_attempts(
          scope_id TEXT NOT NULL, input_hash TEXT NOT NULL, state TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, lease_expires_at TEXT, manifest_path TEXT,
          result_path TEXT, error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(scope_id,input_hash));
        CREATE TABLE IF NOT EXISTS quarterly_stage_history(
          scope_id TEXT NOT NULL, revision INTEGER NOT NULL, stage TEXT NOT NULL, state TEXT NOT NULL,
          input_hash TEXT, artifact_path TEXT, artifact_sha256 TEXT, attempts INTEGER NOT NULL,
          error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(scope_id,revision,stage));
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(quarterly_scopes)")}
        if "revision" not in columns:
            self.db.execute("ALTER TABLE quarterly_scopes ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        if "input_fingerprint" not in columns:
            self.db.execute("ALTER TABLE quarterly_scopes ADD COLUMN input_fingerprint TEXT")
        for scope in self.db.execute("SELECT scope_id FROM quarterly_scopes").fetchall():
            for stage in self.STAGES:
                self.db.execute("INSERT OR IGNORE INTO quarterly_stages(scope_id,stage,state,updated_at) VALUES(?,?,?,?)",
                                (scope["scope_id"], stage, "pending", utc_now()))
        self.db.commit()

    def freeze(self, quarter: dict[str, Any], industry: dict[str, Any], cutoff: str, *, edition: str) -> dict[str, Any]:
        frozen = {"schema_version": 1, "industry_id": industry["industry_id"], "label": industry.get("label"),
                  "metric_template": industry.get("metric_template"), "key_symbols": list(industry.get("key_symbols", [])),
                  "issuers": list(industry.get("issuers", [])), "method_version": "earnings-method-v1"}
        payload = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        import hashlib
        digest = hashlib.sha256(payload.encode()).hexdigest()
        scope_id = stable_id("quarterly-scope", quarter["quarter_id"], industry["industry_id"])
        now = utc_now()
        self.db.execute("""INSERT OR IGNORE INTO quarterly_scopes(scope_id,quarter_id,industry_id,period_start,period_end,
                        frozen_universe_json,frozen_universe_hash,cutoff,edition,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (scope_id, quarter["quarter_id"], industry["industry_id"], quarter["period_start"],
                         quarter["period_end"], payload, digest, cutoff, edition, now, now))
        for stage in self.STAGES:
            self.db.execute("INSERT OR IGNORE INTO quarterly_stages(scope_id,stage,state,updated_at) VALUES(?,?,?,?)",
                            (scope_id, stage, "pending", now))
        self.db.commit()
        row = dict(self.db.execute("SELECT * FROM quarterly_scopes WHERE scope_id=?", (scope_id,)).fetchone())
        frozen_path = self.path.parent / "quarterly-scopes" / scope_id / "frozen-scope.json"
        if frozen_path.exists():
            if read_json(frozen_path).get("frozen_universe_hash") != row["frozen_universe_hash"]:
                raise ValueError("frozen quarterly scope file differs from registry")
        else:
            atomic_write_json(frozen_path, {"schema_version": 1, "scope_id": scope_id, "quarter_id": quarter["quarter_id"],
                "period_start": quarter["period_start"], "period_end": quarter["period_end"], "cutoff": row["cutoff"],
                "frozen_universe_hash": row["frozen_universe_hash"], "industry": json.loads(row["frozen_universe_json"])})
        revision_path = self.path.parent / "quarterly-scopes" / scope_id / "revisions" / f"v{row['revision']}" / "frozen-scope.json"
        if not revision_path.exists():
            atomic_write_json(revision_path, {"schema_version": 1, "scope_id": scope_id, "revision": row["revision"],
                "quarter_id": row["quarter_id"], "period_start": row["period_start"], "period_end": row["period_end"],
                "cutoff": row["cutoff"], "frozen_universe_hash": row["frozen_universe_hash"],
                "industry": json.loads(row["frozen_universe_json"])})
        row["frozen_scope_path"] = str(revision_path)
        return row

    def set_stage(self, scope_id: str, stage: str, state: str, *, input_hash: str | None = None,
                  artifact_path: str | None = None, artifact_sha256: str | None = None,
                  error: str | None = None) -> None:
        if stage not in self.STAGES or state not in {"pending", "running", "completed", "failed", "unknown", "blocked"}:
            raise ValueError("invalid quarterly stage transition")
        self.db.execute("""UPDATE quarterly_stages SET state=?,input_hash=COALESCE(?,input_hash),
          artifact_path=COALESCE(?,artifact_path),artifact_sha256=COALESCE(?,artifact_sha256),
          attempts=attempts+?,error=?,updated_at=? WHERE scope_id=? AND stage=?""",
          (state, input_hash, artifact_path, artifact_sha256, int(state == "running"), error, utc_now(), scope_id, stage))
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def begin_revision(self, scope_id: str, input_fingerprint: str, cutoff: str) -> bool:
        row = self.db.execute("SELECT * FROM quarterly_scopes WHERE scope_id=?", (scope_id,)).fetchone()
        if not row: raise ValueError("quarterly scope missing")
        if not row["input_fingerprint"]:
            self.db.execute("UPDATE quarterly_scopes SET input_fingerprint=?,updated_at=? WHERE scope_id=?",
                            (input_fingerprint, utc_now(), scope_id)); self.db.commit(); return False
        if row["input_fingerprint"] == input_fingerprint: return False
        # New accepted company evidence supersedes an incomplete stage edition too. An
        # industry revision cannot wait for the cross-industry market stage, which is
        # allowed to depend on other scopes. Single-process execution prevents overlap.
        revision = int(row["revision"])
        self.db.execute("""INSERT OR REPLACE INTO quarterly_stage_history
            SELECT scope_id,?,stage,state,input_hash,artifact_path,artifact_sha256,attempts,error,updated_at
            FROM quarterly_stages WHERE scope_id=?""", (revision, scope_id))
        new_revision = revision + 1
        self.db.execute("UPDATE quarterly_scopes SET revision=?,input_fingerprint=?,cutoff=?,edition='revision',updated_at=? WHERE scope_id=?",
                        (new_revision, input_fingerprint, cutoff, utc_now(), scope_id))
        self.db.execute("""UPDATE quarterly_stages SET state='pending',input_hash=NULL,artifact_path=NULL,
            artifact_sha256=NULL,attempts=0,error=NULL,updated_at=? WHERE scope_id=?""", (utc_now(), scope_id))
        revision_path = self.path.parent / "quarterly-scopes" / scope_id / "revisions" / f"v{new_revision}" / "frozen-scope.json"
        atomic_write_json(revision_path, {"schema_version": 1, "scope_id": scope_id, "revision": new_revision,
            "quarter_id": row["quarter_id"], "period_start": row["period_start"], "period_end": row["period_end"],
            "cutoff": cutoff, "frozen_universe_hash": row["frozen_universe_hash"],
            "industry": json.loads(row["frozen_universe_json"])})
        self.db.commit(); return True


def record_gap_review(root: Path, scope_id: str, result_path: Path) -> dict[str, Any]:
    """Accept an explicit evidence-gap disposition; Python never manufactures resolved status."""
    ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
    try:
        input_path = root / "runtime/earnings/quarterly-scopes" / scope_id / "gap-review-input.json"
        if not input_path.is_file(): raise ValueError("gap review input is missing")
        review_input = read_json(input_path)
        result_path = ensure_inside(result_path.resolve(), [root / "runtime/earnings"])
        result = read_json(result_path)
        if result.get("scope_id") != scope_id or result.get("input_hash") != review_input.get("input_hash"):
            raise ValueError("gap review result is not bound to current scope input")
        if result.get("cutoff") != review_input.get("cutoff") or result.get("status") not in {"resolved", "disclosed", "unresolved"}:
            raise ValueError("gap review cutoff or status is invalid")
        samples = result.get("omitted_and_negative_sample_review")
        if not isinstance(samples, list) or not samples or any(not isinstance(row, dict) or not row.get("finding") for row in samples):
            raise ValueError("gap review must address omitted and negative samples")
        required = [row["limitation_key"] for row in review_input.get("critical_limitations", [])]
        dispositions = result.get("limitation_dispositions")
        if not isinstance(dispositions, list): raise ValueError("gap review limitation dispositions are missing")
        by_key = {row.get("limitation_key"): row for row in dispositions if isinstance(row, dict)}
        if set(required) - set(by_key): raise ValueError("gap review omits critical limitations")
        allowed_evidence = {evidence_id for report in review_input.get("reports", [])
                            for evidence_id in report.get("evidence_ids", [])}
        for row in dispositions:
            if (not isinstance(row, dict) or row.get("disposition") not in
                    {"resolved", "disclosed", "not_material", "unresolved"} or not row.get("rationale")):
                raise ValueError("gap review disposition is incomplete")
            if set(row.get("evidence_ids") or []) - allowed_evidence:
                raise ValueError("gap review cites evidence outside the frozen input")
        for row in samples:
            if set(row.get("evidence_ids") or []) - allowed_evidence:
                raise ValueError("gap sample review cites evidence outside the frozen input")
        if result["status"] in {"resolved", "disclosed"}:
            limitation_by_key = {row["limitation_key"]: row for row in review_input.get("critical_limitations", [])}
            for key in required:
                row = by_key[key]
                if row.get("disposition") not in {"resolved", "disclosed", "not_material"} or not row.get("rationale"):
                    raise ValueError("resolved gap review lacks substantive limitation disposition")
                if limitation_by_key[key].get("kind") == "membership" and (row.get("disposition") != "resolved" or not row.get("evidence_ids")):
                    raise ValueError("membership gap requires resolved evidence")
        accepted = root / "runtime/earnings/quarterly-scopes" / scope_id / "gap-review.json"
        atomic_write_json(accepted, result)
        ledger.db.execute("INSERT OR REPLACE INTO quarterly_gap_reviews VALUES(?,?,?,?,?,?,?)",
            (scope_id, result["input_hash"], result["cutoff"], result["status"], str(accepted.relative_to(root)),
             sha256_file(accepted), utc_now()))
        ledger.db.commit()
        return {"status": "success", "scope_id": scope_id, "gap_status": result["status"],
                "artifact": str(accepted.relative_to(root))}
    finally: ledger.close()


def _period_members(state: EarningsState, issuer_ids: list[str], quarter_id: str,
                    cutoff: str | None = None) -> tuple[set[str], set[str], set[str]]:
    return _period_member_audit(state, issuer_ids, quarter_id, cutoff=cutoff)[0:3]


def _period_member_audit(state: EarningsState, issuer_ids: list[str], quarter_id: str,
                         cutoff: str | None = None) -> tuple[set[str], set[str], set[str], dict[str, list[str]]]:
    cutoff_dt = parse_time(cutoff or utc_now())
    root = state.path.resolve().parents[2]
    disclosed: set[str] = set(); fetched: set[str] = set(); researched: set[str] = set()
    reasons: dict[str, list[str]] = {issuer_id: [] for issuer_id in issuer_ids}
    for issuer_id in issuer_ids:
        events = state.db.execute("SELECT * FROM earnings_events WHERE issuer_id=? AND event_kind='earnings'", (issuer_id,)).fetchall()
        event_ids = [row["event_id"] for row in events]
        if not event_ids:
            reasons[issuer_id].append("no earnings event metadata")
            continue
        placeholders = ",".join("?" for _ in event_ids)
        # A validated accepted company report may resolve a standalone period from its
        # evidence/fact periods even when event/document reporting_start is null. The
        # original rows stay immutable; this binding is used only for the frozen audit.
        eligible_reports: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        evidence_document_keys: set[tuple[str, int, str]] = set()
        reports = state.db.execute(f"""SELECT a.*,t.state AS task_state FROM report_artifacts a
            JOIN research_tasks t ON t.task_id=a.task_id WHERE a.report_type='company'
            AND a.subject_id IN ({placeholders}) AND a.source_mode='live' AND t.state='completed'
            ORDER BY a.rowid DESC""", event_ids).fetchall()
        for raw in reports:
            row = dict(raw); report_path = root / row["path"]
            if not report_path.is_file() or sha256_file(report_path) != row["sha256"]:
                reasons[issuer_id].append(f"research file/hash invalid: {row['report_id']}"); continue
            report = read_json(report_path); report_cutoff = parse_time(report.get("cutoff"))
            if not report_cutoff or report_cutoff > cutoff_dt:
                reasons[issuer_id].append(f"research after cutoff: {row['report_id']}"); continue
            try: resolved_period = resolve_report_period(report)
            except ValueError:
                reasons[issuer_id].append(f"research period unresolved: {row['report_id']}"); continue
            if resolved_period["research_quarter"] != quarter_id: continue
            eligible_reports.append((row, report, resolved_period))
            for evidence in report.get("evidence", []):
                if evidence.get("document_id") and evidence.get("document_version") is not None and evidence.get("document_hash"):
                    evidence_document_keys.add((str(evidence["document_id"]), int(evidence["document_version"]),
                                                str(evidence["document_hash"])))
        documents = state.db.execute("""SELECT d.* FROM documents d WHERE issuer_id=? AND source_mode='live'
            AND version=(SELECT MAX(x.version) FROM documents x WHERE x.document_id=d.document_id)""", (issuer_id,)).fetchall()
        for raw in documents:
            row = dict(raw); public = row.get("accepted_at") or row.get("published_at")
            if not public or parse_time(public) > cutoff_dt:
                reasons[issuer_id].append(f"document unavailable at cutoff: {row['document_id']}"); continue
            mapped_quarter = None
            if row.get("reporting_start") and row.get("reporting_end"):
                try: mapped_quarter = map_fiscal_period(row["reporting_start"], row["reporting_end"], form=row.get("form"))["research_quarter"]
                except ValueError: pass
            binding = (str(row["document_id"]), int(row["version"]), str(row["content_sha256"]))
            if mapped_quarter is None and binding in evidence_document_keys:
                mapped_quarter = quarter_id
            if mapped_quarter != quarter_id:
                reasons[issuer_id].append(f"document period not bound to quarter: {row['document_id']}"); continue
            disclosed.add(issuer_id)
            original = root / row["original_path"]
            if not original.is_file() or sha256_file(original) != row["content_sha256"]:
                reasons[issuer_id].append(f"disclosed but document file/hash invalid: {row['document_id']}"); continue
            fetched.add(issuer_id)
        if issuer_id not in disclosed:
            reasons[issuer_id].append("no accepted live quarter document")
        if eligible_reports and issuer_id in fetched:
            researched.add(issuer_id)
        if issuer_id not in researched: reasons[issuer_id].append("no completed accepted live company research")
    return disclosed, fetched, researched, reasons


def _scope_input_fingerprint(state: EarningsState, root: Path, scope: sqlite3.Row, cutoff: str) -> str:
    industry = json.loads(scope["frozen_universe_json"])
    symbol_to_id = {row["symbol"]: row["issuer_id"] for row in state.db.execute("SELECT symbol,issuer_id FROM issuers")}
    versions = []
    for issuer in industry.get("issuers", []):
        issuer_id = symbol_to_id.get(issuer["symbol"])
        if not issuer_id:
            versions.append((issuer["symbol"], "unresolved")); continue
        for row in state.db.execute("""SELECT a.* FROM report_artifacts a JOIN research_tasks t ON t.task_id=a.task_id
            WHERE a.report_type='company' AND a.source_mode='live' AND t.state='completed' AND a.subject_id IN
            (SELECT event_id FROM earnings_events WHERE issuer_id=?) ORDER BY a.rowid DESC""", (issuer_id,)).fetchall():
            path = root / row["path"]
            if not path.is_file() or sha256_file(path) != row["sha256"]: continue
            report = read_json(path)
            report_cutoff = parse_time(report.get("cutoff"))
            if not report_cutoff or report_cutoff > parse_time(cutoff): continue
            try: period = resolve_report_period(report)
            except ValueError: continue
            if period["research_quarter"] == scope["quarter_id"]:
                versions.append((issuer_id, row["report_id"], row["sha256"])); break
    return sha256_bytes(canonical_json({"membership": scope["frozen_universe_hash"], "reports": versions}))


def inspect_due(root: Path, *, day: str, cutoff: str, config_path: str, universe_path: str,
                manual_quarter: str | None = None) -> dict[str, Any]:
    config, _ = load_config(root, config_path); universe = read_json(root / universe_path)
    selected = review_quarter_for_day(date.fromisoformat(day), config.get("seasons"))
    if manual_quarter:
        year, number = manual_quarter.split("-Q"); start_month = 1 + (int(number) - 1) * 3
        q_start = date(int(year), start_month, 1); q_end = _calendar_quarter(q_start)[1]
        if q_end >= date.fromisoformat(day): raise ValueError("manual quarter has not ended")
        selected = {"quarter_id": manual_quarter, "period_start": q_start.isoformat(), "period_end": q_end.isoformat(),
                    "tail_window": True, "deadline_reached": True, "as_of": day, "manual": True}
    ledger = QuarterlyReviewLedger(root / "runtime/earnings/quarterly.sqlite")
    state = EarningsState(root / config["paths"]["state"])
    try:
        existing_scope_count = ledger.db.execute("SELECT COUNT(*) FROM quarterly_scopes WHERE edition!='monitor'").fetchone()[0]
        selected["initialization_backfill"] = bool(not manual_quarter and not existing_scope_count
            and config["quarterly"].get("automatic_trigger_enabled") is True
            and int(config["quarterly"].get("initial_backfill_quarters", 0)) > 0)
        should_create = bool(manual_quarter or selected["tail_window"] or selected["initialization_backfill"])
        existing = ledger.db.execute("SELECT * FROM quarterly_scopes WHERE quarter_id=? ORDER BY created_at,industry_id",
                                     (selected["quarter_id"],)).fetchall()
        if should_create and not existing:
            for industry in universe["industries"]:
                ledger.freeze(selected, industry, cutoff, edition="full")
            existing = ledger.db.execute("SELECT * FROM quarterly_scopes WHERE quarter_id=? ORDER BY created_at,industry_id",
                                         (selected["quarter_id"],)).fetchall()
        for registered in ledger.db.execute("SELECT * FROM quarterly_scopes ORDER BY period_end,industry_id").fetchall():
            fingerprint = _scope_input_fingerprint(state, root, registered, cutoff)
            ledger.begin_revision(registered["scope_id"], fingerprint, cutoff)
        # Incomplete scopes remain due after their nominal window and across quarter rollovers.
        due = ledger.db.execute("""SELECT DISTINCT q.* FROM quarterly_scopes q JOIN quarterly_stages s ON s.scope_id=q.scope_id
            WHERE s.state!='completed' ORDER BY q.period_end,q.created_at,q.industry_id""").fetchall()
        scopes = []
        gap_artifacts = []
        for raw_scope in due:
            scope = dict(raw_scope)
            frozen_industry = json.loads(scope["frozen_universe_json"])
            symbol_to_id = {row["symbol"]: row["issuer_id"] for row in state.db.execute("SELECT symbol,issuer_id FROM issuers")}
            expected = [symbol_to_id.get(row["symbol"], f"unresolved:{row['symbol']}") for row in frozen_industry["issuers"]]
            keys = [symbol_to_id.get(symbol, f"unresolved:{symbol}") for symbol in frozen_industry.get("key_symbols", [])]
            resolved = [issuer_id for issuer_id in expected if not issuer_id.startswith("unresolved:")]
            disclosed, fetched, researched, member_reasons = _period_member_audit(
                state, resolved, scope["quarter_id"], cutoff=scope["cutoff"])
            for issuer_id in expected:
                if issuer_id.startswith("unresolved:"):
                    member_reasons[issuer_id] = ["issuer identity unresolved"]
            report_summaries = []
            for issuer_id in sorted(researched):
                rows = state.db.execute("""SELECT a.* FROM report_artifacts a JOIN research_tasks t ON t.task_id=a.task_id
                    WHERE a.report_type='company' AND a.source_mode='live' AND t.state='completed' AND a.subject_id IN
                    (SELECT event_id FROM earnings_events WHERE issuer_id=?) ORDER BY a.rowid DESC""", (issuer_id,)).fetchall()
                for row in rows:
                    report_path = root / row["path"]
                    if not report_path.is_file() or sha256_file(report_path) != row["sha256"]: continue
                    report = read_json(report_path)
                    report_cutoff = parse_time(report.get("cutoff"))
                    if not report_cutoff or report_cutoff > parse_time(scope["cutoff"]): continue
                    try: mapped = resolve_report_period(report)
                    except ValueError: continue
                    if mapped["research_quarter"] != scope["quarter_id"]: continue
                    report_summaries.append({"issuer_id": issuer_id, "report_id": row["report_id"], "path": row["path"],
                        "sha256": row["sha256"], "completeness": report.get("completeness"),
                        "thesis_state": report.get("thesis_state"), "change_summary": report.get("change_summary"),
                        "limitations": report.get("limitations", []), "claim_ids": [c.get("claim_id") for c in report.get("claims", [])],
                        "evidence_ids": [e.get("evidence_id") for e in report.get("evidence", [])]})
                    break
            critical_limitations = sorted([{"limitation_key": stable_id("gap-limitation", row["report_id"], limitation),
                "kind": "report", "report_id": row["report_id"], "statement": limitation} for row in report_summaries
                for limitation in row["limitations"]] +
                [{"limitation_key": stable_id("gap-member", issuer_id, reason), "kind": "membership",
                  "issuer_id": issuer_id, "statement": reason} for issuer_id, reasons in member_reasons.items() for reason in reasons],
                key=lambda row: row["limitation_key"])
            gap_basis = {"scope_id": scope["scope_id"], "quarter_id": scope["quarter_id"], "cutoff": scope["cutoff"],
                "frozen_universe_hash": scope["frozen_universe_hash"], "reports": [(r["report_id"], r["sha256"]) for r in report_summaries],
                "member_reasons": member_reasons, "critical_limitations": critical_limitations}
            gap_input_hash = sha256_bytes(canonical_json(gap_basis))
            gap_input = {"schema_version": 1, **gap_basis, "input_hash": gap_input_hash,
                "reports": report_summaries, "required_output": {"status": "resolved|disclosed|unresolved",
                "limitation_dispositions": [{"limitation_key": "...", "disposition": "...", "rationale": "...", "evidence_ids": []}],
                "omitted_and_negative_sample_review": []}, "created_at": utc_now()}
            immutable_input = root / "runtime/earnings/quarterly-scopes" / scope["scope_id"] / "gap-reviews" / gap_input_hash / "input.json"
            if not immutable_input.exists(): atomic_write_json(immutable_input, gap_input)
            current_input = root / "runtime/earnings/quarterly-scopes" / scope["scope_id"] / "gap-review-input.json"
            atomic_write_json(current_input, gap_input); gap_artifacts.append(str(immutable_input.relative_to(root)))
            accepted_gap = ledger.db.execute("SELECT * FROM quarterly_gap_reviews WHERE scope_id=? AND input_hash=?",
                                             (scope["scope_id"], gap_input_hash)).fetchone()
            gap_status = "unresolved"
            gap_output = root / "runtime/earnings/quarterly-scopes" / scope["scope_id"] / "gap-review.json"
            accepted_gap_valid = bool(accepted_gap and gap_output.is_file() and sha256_file(gap_output) == accepted_gap["artifact_sha256"])
            if accepted_gap_valid:
                gap_status = accepted_gap["status"]
            assessment = assess_industry_maturity(expected_issuer_ids=expected, key_issuer_ids=keys,
                disclosed_issuer_ids=disclosed, fetched_issuer_ids=fetched, researched_issuer_ids=researched,
                critical_gap_status=gap_status, threshold=float(config["quarterly"]["mature_coverage_ratio"]))
            frozen_path = ledger.path.parent / "quarterly-scopes" / scope["scope_id"] / "revisions" / f"v{scope['revision']}" / "frozen-scope.json"
            scopes.append({"scope_id": scope["scope_id"], "industry_id": scope["industry_id"],
                           "quarter_id": scope["quarter_id"], "revision": scope["revision"], "period_start": scope["period_start"],
                           "period_end": scope["period_end"], "cutoff": scope["cutoff"],
                           "input_fingerprint": scope["input_fingerprint"],
                           "frozen_universe_hash": scope["frozen_universe_hash"], "frozen_industry": frozen_industry,
                           "frozen_scope_path": str(frozen_path.relative_to(root)),
                           "gap_review_input_path": str(current_input.relative_to(root)),
                           "gap_review_immutable_input_path": str(immutable_input.relative_to(root)),
                           "gap_review_path": str(gap_output.relative_to(root)) if accepted_gap_valid else None,
                           "edition": "full" if assessment["eligible_full"] else "stage", "maturity": assessment,
                           "eligible_stage": bool(assessment["eligible_full"]),
                           "deadline_stage_allowed": bool(config["quarterly"].get("deadline_partial_allowed")
                               and date.fromisoformat(day) >= _quarter_deadline(scope["quarter_id"], config["seasons"])
                               and not assessment["eligible_full"])})
        sunday = date.fromisoformat(day).weekday() == 6
        weekly = {"due": sunday, "enabled": bool(config["quarterly"].get("weekly_review_enabled")),
                  "actionable": any(not row["maturity"]["eligible_full"] for row in scopes), "artifact": None,
                  "changed": False}
        if sunday and weekly["enabled"]:
            import hashlib
            basis = [{"scope_id": row["scope_id"], "maturity": row["maturity"]} for row in scopes]
            fingerprint = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
            previous = ledger.db.execute("SELECT fingerprint FROM weekly_reviews ORDER BY week_end DESC LIMIT 1").fetchone()
            weekly["changed"] = not previous or previous[0] != fingerprint
            if weekly["actionable"] and weekly["changed"]:
                output = root / "runtime/earnings/weekly" / f"{day}.json"
                atomic_write_json(output, {"schema_version": 1, "week_end": day, "quarter": selected,
                    "actionable": True, "scopes": basis, "created_at": utc_now()})
                weekly["artifact"] = str(output.relative_to(root))
            ledger.db.execute("INSERT OR REPLACE INTO weekly_reviews VALUES(?,?,?,?,?)",
                              (day, fingerprint, int(weekly["actionable"]), weekly["artifact"], utc_now()))
            ledger.db.commit()
        return {"schema_version": 1, "workflow": "earnings-review-context", "status": "success", "date": day,
                "quarter": selected, "weekly_review": weekly, "gap_review_artifacts": gap_artifacts, "scopes": scopes}
    finally:
        state.close(); ledger.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT)); parser.add_argument("--config", default="config/earnings_research.json")
    parser.add_argument("--universe", default="config/earnings_universe.json"); parser.add_argument("--date")
    parser.add_argument("--cutoff", default=None)
    parser.add_argument("--manual-quarter")
    parser.add_argument("--record-gap-review"); parser.add_argument("--scope-id")
    args = parser.parse_args()
    cutoff = args.cutoff or datetime.now(timezone.utc).isoformat()
    try:
        if args.record_gap_review:
            if not args.scope_id: raise ValueError("--scope-id is required with --record-gap-review")
            result = record_gap_review(Path(args.repo_root).resolve(), args.scope_id,
                                       Path(args.repo_root).resolve() / args.record_gap_review)
        else:
            if not args.date: raise ValueError("--date is required")
            result = inspect_due(Path(args.repo_root).resolve(), day=args.date, cutoff=cutoff, config_path=args.config,
                                 universe_path=args.universe, manual_quarter=args.manual_quarter)
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        result = {"schema_version": 1, "workflow": "earnings-review-context", "status": "failed", "date": args.date,
                  "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(result["status"] == "failed")


if __name__ == "__main__":
    main()
