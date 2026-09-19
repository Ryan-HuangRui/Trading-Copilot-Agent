#!/usr/bin/env python3
"""SQLite state, queue leases, dependencies and watermarks for earnings research."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Sequence

from earnings_common import ROOT, confined_path, emit, envelope, parse_time, stable_id, utc_now


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS issuers (
  issuer_id TEXT PRIMARY KEY, cik TEXT UNIQUE, symbol TEXT, name TEXT NOT NULL,
  identity_status TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT NOT NULL, version INTEGER NOT NULL, issuer_id TEXT NOT NULL,
  event_id TEXT, form TEXT, source_type TEXT NOT NULL, source_url TEXT NOT NULL,
  provider TEXT NOT NULL, backend TEXT NOT NULL, reporting_start TEXT, reporting_end TEXT,
  published_at TEXT, accepted_at TEXT, fetched_at TEXT NOT NULL, public_time_precision TEXT NOT NULL,
  original_path TEXT NOT NULL, content_sha256 TEXT NOT NULL, supersedes TEXT, source_mode TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(document_id, version),
  FOREIGN KEY(issuer_id) REFERENCES issuers(issuer_id)
);
CREATE INDEX IF NOT EXISTS idx_documents_event ON documents(event_id);
CREATE TABLE IF NOT EXISTS earnings_events (
  event_id TEXT PRIMARY KEY, issuer_id TEXT NOT NULL, event_kind TEXT NOT NULL,
  reporting_start TEXT, reporting_end TEXT, input_hash TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(issuer_id) REFERENCES issuers(issuer_id)
);
CREATE TABLE IF NOT EXISTS research_tasks (
  task_id TEXT PRIMARY KEY, task_key TEXT UNIQUE NOT NULL, task_type TEXT NOT NULL,
  subject_id TEXT NOT NULL, period_start TEXT, period_end TEXT, input_hash TEXT NOT NULL,
  method_version TEXT NOT NULL, source_mode TEXT NOT NULL, profile TEXT NOT NULL,
  model TEXT NOT NULL, effort TEXT NOT NULL, state TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 2,
  lease_owner TEXT, lease_expires_at TEXT, next_attempt_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, output_manifest TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON research_tasks(state, created_at);
CREATE TABLE IF NOT EXISTS task_dependencies (
  task_id TEXT NOT NULL, dependency_task_id TEXT NOT NULL,
  PRIMARY KEY(task_id, dependency_task_id),
  FOREIGN KEY(task_id) REFERENCES research_tasks(task_id),
  FOREIGN KEY(dependency_task_id) REFERENCES research_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS source_watermarks (
  source TEXT NOT NULL, scope TEXT NOT NULL, watermark TEXT, status TEXT NOT NULL,
  checked_at TEXT NOT NULL, detail TEXT, PRIMARY KEY(source, scope)
);
CREATE TABLE IF NOT EXISTS source_reconciliations (
  source TEXT NOT NULL, scope TEXT NOT NULL, last_reconciled_at TEXT NOT NULL,
  PRIMARY KEY(source, scope)
);
CREATE TABLE IF NOT EXISTS source_failures (
  failure_id TEXT PRIMARY KEY, source TEXT NOT NULL, scope TEXT NOT NULL,
  item_key TEXT, error TEXT NOT NULL, retryable INTEGER NOT NULL,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS source_rate_limit (
  source TEXT PRIMARY KEY, next_allowed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS source_items (
  source TEXT NOT NULL, scope TEXT NOT NULL, item_key TEXT NOT NULL,
  payload_json TEXT NOT NULL, discovered_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
  next_attempt_at TEXT, content_sha256 TEXT,
  PRIMARY KEY(source, scope, item_key)
);
CREATE INDEX IF NOT EXISTS idx_source_items_pending ON source_items(source,scope,status,discovered_at);
CREATE TABLE IF NOT EXISTS task_inputs (
  task_id TEXT PRIMARY KEY, input_json TEXT NOT NULL, input_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES research_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS task_manifests (
  task_id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES research_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS task_attempt_manifests (
  task_id TEXT NOT NULL, attempt INTEGER NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(task_id,attempt), FOREIGN KEY(task_id) REFERENCES research_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS report_artifacts (
  report_id TEXT PRIMARY KEY, task_id TEXT UNIQUE NOT NULL, report_type TEXT NOT NULL,
  subject_id TEXT NOT NULL, period_start TEXT, period_end TEXT, path TEXT NOT NULL,
  sha256 TEXT NOT NULL, input_manifest_hash TEXT NOT NULL, source_mode TEXT NOT NULL,
  completeness TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES research_tasks(task_id)
);
CREATE TABLE IF NOT EXISTS publication_artifacts (
  publication_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, publication_type TEXT NOT NULL,
  scope_id TEXT NOT NULL, quarter_id TEXT NOT NULL, edition TEXT NOT NULL, version INTEGER NOT NULL,
  manifest_path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, content_sha256 TEXT NOT NULL,
  checker_status TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(series_id,version)
);
CREATE TABLE IF NOT EXISTS publication_delivery (
  series_id TEXT PRIMARY KEY, publication_id TEXT NOT NULL, state TEXT NOT NULL,
  document_id TEXT, url TEXT, local_sha256 TEXT, verified_remote_sha256 TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, reason TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_delivery_routes (
  route_key TEXT NOT NULL, series_id TEXT NOT NULL, publication_id TEXT NOT NULL, state TEXT NOT NULL,
  document_id TEXT, url TEXT, local_sha256 TEXT, verified_remote_sha256 TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, reason TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY(route_key,series_id)
);
CREATE TABLE IF NOT EXISTS publication_jobs (
  job_id TEXT PRIMARY KEY, series_key TEXT NOT NULL, source_path TEXT NOT NULL,
  source_sha256 TEXT NOT NULL, publication_type TEXT NOT NULL, scope_id TEXT NOT NULL,
  quarter_id TEXT NOT NULL, edition TEXT NOT NULL, revision INTEGER NOT NULL,
  state TEXT NOT NULL, input_manifest_path TEXT, publication_manifest_path TEXT,
  error TEXT, attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(series_key,revision), UNIQUE(source_sha256,publication_type,quarter_id)
);
CREATE INDEX IF NOT EXISTS idx_publication_jobs_state ON publication_jobs(state,updated_at);
CREATE TABLE IF NOT EXISTS publication_gaps (
  source_path TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, publication_type TEXT,
  scope_id TEXT, reason TEXT NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


class EarningsState:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except Exception:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def upsert_issuer(self, *, issuer_id: str, cik: str | None, symbol: str | None, name: str, identity_status: str) -> None:
        self.db.execute(
            """INSERT INTO issuers VALUES(?,?,?,?,?,?) ON CONFLICT(issuer_id) DO UPDATE SET
            cik=excluded.cik,symbol=excluded.symbol,name=excluded.name,
            identity_status=excluded.identity_status,updated_at=excluded.updated_at""",
            (issuer_id, cik, symbol, name, identity_status, utc_now()),
        )

    def register_document(self, document: dict[str, Any]) -> tuple[int, bool]:
        row = self.db.execute(
            "SELECT version,content_sha256 FROM documents WHERE document_id=? ORDER BY version DESC LIMIT 1",
            (document["document_id"],),
        ).fetchone()
        if row and row["content_sha256"] == document["content_sha256"]:
            return int(row["version"]), False
        version = int(row["version"]) + 1 if row else 1
        supersedes = f"{document['document_id']}:v{row['version']}" if row else None
        values = dict(document, version=version, supersedes=supersedes)
        columns = (
            "document_id version issuer_id event_id form source_type source_url provider backend "
            "reporting_start reporting_end published_at accepted_at fetched_at public_time_precision "
            "original_path content_sha256 supersedes source_mode metadata_json"
        ).split()
        self.db.execute(
            f"INSERT INTO documents({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            [values.get(column) for column in columns],
        )
        return version, True

    def refresh_event(self, event_id: str, issuer_id: str, event_kind: str, period_start: str | None, period_end: str | None) -> str:
        snapshot = self.event_input_snapshot(event_id, issuer_id)
        import hashlib
        input_hash = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.db.execute(
            """INSERT INTO earnings_events VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
            input_hash=excluded.input_hash,updated_at=excluded.updated_at""",
            (event_id, issuer_id, event_kind, period_start, period_end, input_hash, utc_now()),
        )
        return input_hash

    def event_input_snapshot(self, event_id: str, issuer_id: str) -> dict[str, Any]:
        documents = [dict(row) for row in self.db.execute(
            """SELECT d.* FROM documents d WHERE d.event_id=? AND d.version=(
            SELECT MAX(x.version) FROM documents x WHERE x.document_id=d.document_id)
            ORDER BY d.document_id""", (event_id,),
        )]
        facts = [dict(row) for row in self.db.execute(
            "SELECT * FROM documents WHERE issuer_id=? AND source_type='sec_companyfacts' ORDER BY version DESC LIMIT 1",
            (issuer_id,),
        )]
        return {"event_id": event_id, "issuer_id": issuer_id, "documents": documents, "companyfacts": facts}

    def enqueue_task(self, *, task_type: str, subject_id: str, period_start: str | None, period_end: str | None,
                     input_hash: str, method_version: str, source_mode: str, profile: str,
                     model: str, effort: str, max_attempts: int = 2, dependencies: Sequence[str] = ()) -> tuple[str, bool]:
        task_key = stable_id("key", task_type, subject_id, period_start, period_end, input_hash, method_version)
        task_id = stable_id("task", task_key)
        now = utc_now()
        with self.immediate() as db:
            db.execute(
                """UPDATE research_tasks SET state='terminal_failed',error='superseded by changed input',updated_at=?
                WHERE task_type=? AND subject_id=? AND period_start IS ? AND period_end IS ? AND method_version=?
                AND input_hash!=? AND state IN ('queued','retryable_failed')""",
                (now, task_type, subject_id, period_start, period_end, method_version, input_hash),
            )
            cursor = db.execute(
                """INSERT OR IGNORE INTO research_tasks(
                task_id,task_key,task_type,subject_id,period_start,period_end,input_hash,method_version,
                source_mode,profile,model,effort,state,max_attempts,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, task_key, task_type, subject_id, period_start, period_end, input_hash,
                 method_version, source_mode, profile, model, effort, "queued", max_attempts, now, now),
            )
            created = cursor.rowcount == 1
            if created:
                for dependency in dependencies:
                    db.execute("INSERT INTO task_dependencies VALUES(?,?)", (task_id, dependency))
            else:
                row = db.execute("SELECT task_id FROM research_tasks WHERE task_key=?", (task_key,)).fetchone()
                task_id = str(row["task_id"])
        return task_id, created

    def freeze_task_input(self, task_id: str, payload: dict[str, Any], input_hash: str) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.immediate() as db:
            task = db.execute("SELECT input_hash FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task or task["input_hash"] != input_hash:
                raise ValueError("frozen task input hash does not match task")
            row = db.execute("SELECT input_json,input_sha256 FROM task_inputs WHERE task_id=?", (task_id,)).fetchone()
            if row:
                if row["input_json"] != encoded or row["input_sha256"] != input_hash:
                    raise ValueError("frozen task input cannot be replaced")
                return
            db.execute("INSERT INTO task_inputs VALUES(?,?,?,?)", (task_id, encoded, input_hash, utc_now()))

    def task_input(self, task_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT input_json FROM task_inputs WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError(f"frozen task input missing: {task_id}")
        return json.loads(row["input_json"])

    def register_task_manifest(self, task_id: str, path: str, digest: str) -> None:
        with self.immediate() as db:
            row = db.execute("SELECT path,sha256 FROM task_manifests WHERE task_id=?", (task_id,)).fetchone()
            if row:
                if row["path"] != path or row["sha256"] != digest:
                    raise ValueError("immutable task manifest cannot be replaced")
                return
            db.execute("INSERT INTO task_manifests VALUES(?,?,?,?)", (task_id, path, digest, utc_now()))

    def register_attempt_manifest(self, task_id: str, attempt: int, path: str, digest: str) -> None:
        with self.immediate() as db:
            row = db.execute("SELECT path,sha256 FROM task_attempt_manifests WHERE task_id=? AND attempt=?", (task_id, attempt)).fetchone()
            if row:
                if row["path"] != path or row["sha256"] != digest:
                    raise ValueError("immutable attempt manifest cannot be replaced")
                return
            db.execute("INSERT INTO task_attempt_manifests VALUES(?,?,?,?,?)", (task_id, attempt, path, digest, utc_now()))

    def defer_attempt(self, task_id: str, attempt: int, reason: str, *, superseded: bool = False) -> None:
        """Release a pre-model attempt without spending retry or model budget."""
        with self.immediate() as db:
            row = db.execute("SELECT state,attempts FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row or row["state"] != "running" or int(row["attempts"]) != attempt:
                return
            db.execute("DELETE FROM task_attempt_manifests WHERE task_id=? AND attempt=?", (task_id, attempt))
            db.execute(
                """UPDATE research_tasks SET state=?,attempts=attempts-1,lease_owner=NULL,lease_expires_at=NULL,
                error=?,next_attempt_at=NULL,updated_at=? WHERE task_id=? AND state='running' AND attempts=?""",
                ("terminal_failed" if superseded else "queued", reason, utc_now(), task_id, attempt),
            )

    def discover_source_item(self, source: str, scope: str, item_key: str, payload: dict[str, Any]) -> bool:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        now = utc_now()
        with self.immediate() as db:
            row = db.execute("SELECT payload_json,status FROM source_items WHERE source=? AND scope=? AND item_key=?",
                             (source, scope, item_key)).fetchone()
            if not row:
                db.execute("INSERT INTO source_items VALUES(?,?,?,?,?,?,'pending',0,NULL,NULL,NULL)",
                           (source, scope, item_key, encoded, now, now))
                return True
            if row["payload_json"] != encoded:
                db.execute("UPDATE source_items SET payload_json=?,updated_at=?,status='pending',error=NULL,next_attempt_at=NULL WHERE source=? AND scope=? AND item_key=?",
                           (encoded, now, source, scope, item_key))
                return True
            return False

    def pending_source_items(self, source: str, scope: str, limit: int) -> list[dict[str, Any]]:
        now = utc_now()
        rows = self.db.execute(
            """SELECT * FROM source_items WHERE source=? AND scope=? AND
            (status='pending' OR (status='retryable_failed' AND (next_attempt_at IS NULL OR next_attempt_at<=?)))
            ORDER BY discovered_at,item_key LIMIT ?""", (source, scope, now, limit),
        ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def finish_source_item(self, source: str, scope: str, item_key: str, *, digest: str | None = None) -> None:
        self.db.execute("UPDATE source_items SET status='fetched',attempts=attempts+1,error=NULL,next_attempt_at=NULL,content_sha256=?,updated_at=? WHERE source=? AND scope=? AND item_key=?",
                        (digest, utc_now(), source, scope, item_key))

    def fail_source_item(self, source: str, scope: str, item_key: str, error: str, retryable: bool, max_attempts: int) -> None:
        row = self.db.execute("SELECT attempts FROM source_items WHERE source=? AND scope=? AND item_key=?", (source, scope, item_key)).fetchone()
        attempts = int(row["attempts"] if row else 0) + 1
        status = "retryable_failed" if retryable and attempts < max_attempts else "terminal_failed"
        next_attempt = (datetime.now(timezone.utc) + timedelta(minutes=min(60, 2 ** attempts))).isoformat(timespec="seconds") if status == "retryable_failed" else None
        self.db.execute("UPDATE source_items SET status=?,attempts=?,error=?,next_attempt_at=?,updated_at=? WHERE source=? AND scope=? AND item_key=?",
                        (status, attempts, error, next_attempt, utc_now(), source, scope, item_key))

    def reap_expired_tasks(self) -> int:
        now = utc_now()
        superseded = self.db.execute(
            """UPDATE research_tasks AS t SET state='terminal_failed',error='superseded while lease was active',
            lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE state='running' AND lease_expires_at<? AND EXISTS(
              SELECT 1 FROM research_tasks n WHERE n.rowid>t.rowid AND n.task_type=t.task_type
              AND n.subject_id=t.subject_id AND n.period_start IS t.period_start AND n.period_end IS t.period_end
              AND n.source_mode=t.source_mode)""", (now, now)).rowcount
        terminal = self.db.execute(
            """UPDATE research_tasks SET state='terminal_failed',error='lease expired at maximum attempts',
            lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE state='running' AND lease_expires_at<? AND attempts>=max_attempts""",
            (now, now),
        ).rowcount
        retryable = self.db.execute(
            """UPDATE research_tasks SET state='retryable_failed',error='lease expired; eligible for bounded recovery',
            lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE state='running' AND lease_expires_at<? AND attempts<max_attempts""",
            (now, now),
        ).rowcount
        return superseded + terminal + retryable

    def claim_tasks(self, *, owner: str, limit: int, lease_seconds: int, task_type: str | None = None,
                    company_tier: str | None = None, priority_symbols: Sequence[str] = ()) -> list[dict[str, Any]]:
        self.reap_expired_tasks()
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="seconds")
        expires = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        filters = ["(t.state='queued' OR (t.state='retryable_failed' AND (t.next_attempt_at IS NULL OR t.next_attempt_at<=?)) OR (t.state='running' AND t.lease_expires_at<?))", "t.attempts<t.max_attempts"]
        params: list[Any] = [now_text, now_text]
        if task_type:
            filters.append("t.task_type=?")
            params.append(task_type)
        current_expr = """t.period_end IS (SELECT MAX(x.period_end) FROM research_tasks x
          JOIN earnings_events xe ON xe.event_id=x.subject_id JOIN earnings_events te ON te.event_id=t.subject_id
          WHERE x.task_type='company' AND xe.issuer_id=te.issuer_id AND x.source_mode=t.source_mode
          AND x.state IN ('queued','running','completed','retryable_failed') AND NOT EXISTS(
            SELECT 1 FROM research_tasks xn WHERE xn.rowid>x.rowid AND xn.task_type=x.task_type
            AND xn.subject_id=x.subject_id AND xn.period_start IS x.period_start AND xn.period_end IS x.period_end
            AND xn.source_mode=x.source_mode))"""
        depth_expr = """(SELECT COUNT(DISTINCT x.period_end) FROM research_tasks x
          JOIN earnings_events xe ON xe.event_id=x.subject_id JOIN earnings_events te ON te.event_id=t.subject_id
          WHERE x.task_type='company' AND xe.issuer_id=te.issuer_id AND x.source_mode=t.source_mode
          AND COALESCE(x.period_end,'')>COALESCE(t.period_end,'')
          AND x.state IN ('queued','running','completed','retryable_failed') AND NOT EXISTS(
            SELECT 1 FROM research_tasks xn WHERE xn.rowid>x.rowid AND xn.task_type=x.task_type
            AND xn.subject_id=x.subject_id AND xn.period_start IS x.period_start AND xn.period_end IS x.period_end
            AND xn.source_mode=x.source_mode))"""
        if company_tier not in {None, "current", "history"}:
            raise ValueError("company_tier must be current or history")
        if task_type == "company" and company_tier:
            filters.append(current_expr if company_tier == "current" else f"NOT ({current_expr})")
        priority = list(dict.fromkeys(str(symbol) for symbol in priority_symbols))
        with self.immediate() as db:
            rows = db.execute(
                f"""SELECT t.* FROM research_tasks t WHERE {' AND '.join(filters)} AND NOT EXISTS(
                SELECT 1 FROM task_dependencies d JOIN research_tasks p ON p.task_id=d.dependency_task_id
                WHERE d.task_id=t.task_id AND (p.state!='completed' OR EXISTS(
                  SELECT 1 FROM research_tasks pn WHERE pn.rowid>p.rowid AND pn.task_type=p.task_type
                  AND pn.subject_id=p.subject_id AND pn.period_start IS p.period_start
                  AND pn.period_end IS p.period_end AND pn.source_mode=p.source_mode)))
                AND NOT EXISTS(SELECT 1 FROM research_tasks n WHERE n.rowid>t.rowid
                  AND n.task_type=t.task_type AND n.subject_id=t.subject_id
                  AND n.period_start IS t.period_start AND n.period_end IS t.period_end
                  AND n.source_mode=t.source_mode)
                ORDER BY t.created_at,t.task_id""",
                params,
            ).fetchall()
            def queue_key(row: sqlite3.Row) -> tuple[Any, ...]:
                meta = db.execute("""SELECT e.issuer_id,i.symbol FROM earnings_events e
                  LEFT JOIN issuers i ON i.issuer_id=e.issuer_id WHERE e.event_id=?""",
                  (row["subject_id"],)).fetchone()
                depth = 0
                if row["task_type"] == "company" and meta:
                    depth = db.execute("""SELECT COUNT(DISTINCT x.period_end) FROM research_tasks x
                      JOIN earnings_events xe ON xe.event_id=x.subject_id WHERE x.task_type='company'
                      AND xe.issuer_id=? AND x.source_mode=? AND COALESCE(x.period_end,'')>COALESCE(?, '')
                      AND x.state IN ('queued','running','completed','retryable_failed') AND NOT EXISTS(
                        SELECT 1 FROM research_tasks xn WHERE xn.rowid>x.rowid AND xn.task_type=x.task_type
                        AND xn.subject_id=x.subject_id AND xn.period_start IS x.period_start
                        AND xn.period_end IS x.period_end AND xn.source_mode=x.source_mode)""",
                      (meta["issuer_id"], row["source_mode"], row["period_end"])).fetchone()[0]
                return (0 if meta and meta["symbol"] in priority else 1, depth,
                        0 if int(row["attempts"]) == 0 else 1,
                        -int(str(row["period_end"] or "0").replace("-", "")),
                        row["created_at"], row["task_id"])
            rows = sorted(rows, key=queue_key)[:limit]
            claimed: list[dict[str, Any]] = []
            for row in rows:
                db.execute(
                    "UPDATE research_tasks SET state='running',lease_owner=?,lease_expires_at=?,attempts=attempts+1,updated_at=? WHERE task_id=?",
                    (owner, expires, now_text, row["task_id"]),
                )
                item = dict(row)
                item.update(state="running", lease_owner=owner, lease_expires_at=expires, attempts=int(row["attempts"]) + 1)
                claimed.append(item)
            return claimed

    def claim_task(self, task_id: str, *, owner: str, lease_seconds: int) -> dict[str, Any] | None:
        self.reap_expired_tasks()
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="seconds")
        expires = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        with self.immediate() as db:
            row = db.execute("SELECT rowid AS db_rowid,* FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError(f"unknown task_id: {task_id}")
            eligible_state = row["state"] == "queued" or (
                row["state"] == "retryable_failed" and (not row["next_attempt_at"] or row["next_attempt_at"] <= now_text)
            ) or (row["state"] == "running" and row["lease_expires_at"] and row["lease_expires_at"] < now_text)
            blocked = db.execute(
                """SELECT COUNT(*) count FROM task_dependencies d JOIN research_tasks p ON p.task_id=d.dependency_task_id
                WHERE d.task_id=? AND (p.state!='completed' OR EXISTS(
                  SELECT 1 FROM research_tasks pn WHERE pn.rowid>p.rowid AND pn.task_type=p.task_type
                  AND pn.subject_id=p.subject_id AND pn.period_start IS p.period_start
                  AND pn.period_end IS p.period_end AND pn.source_mode=p.source_mode))""", (task_id,),
            ).fetchone()["count"]
            superseded = db.execute("""SELECT 1 FROM research_tasks n WHERE n.rowid>? AND n.task_type=?
              AND n.subject_id=? AND n.period_start IS ? AND n.period_end IS ? AND n.source_mode=?""",
              (row["db_rowid"], row["task_type"], row["subject_id"],
               row["period_start"], row["period_end"], row["source_mode"])).fetchone()
            if not eligible_state or blocked or superseded or row["attempts"] >= row["max_attempts"]:
                return None
            db.execute("UPDATE research_tasks SET state='running',lease_owner=?,lease_expires_at=?,attempts=attempts+1,updated_at=? WHERE task_id=?",
                       (owner, expires, now_text, task_id))
            item = dict(row)
            item.update(state="running", lease_owner=owner, lease_expires_at=expires, attempts=int(row["attempts"]) + 1)
            return item

    def complete_task(self, task_id: str, output_manifest: str) -> None:
        with self.immediate() as db:
            row = db.execute("SELECT state FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError(f"unknown task_id: {task_id}")
            if row["state"] == "completed":
                existing = db.execute("SELECT output_manifest FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()
                if existing["output_manifest"] == output_manifest:
                    return
                raise ValueError("completed task cannot be overwritten")
            db.execute(
                "UPDATE research_tasks SET state='completed',output_manifest=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE task_id=?",
                (output_manifest, utc_now(), task_id),
            )

    def fail_task(self, task_id: str, error: str, retryable: bool) -> None:
        row = self.db.execute("SELECT attempts,max_attempts FROM research_tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError(f"unknown task_id: {task_id}")
        state = "retryable_failed" if retryable and row["attempts"] < row["max_attempts"] else "terminal_failed"
        self.db.execute(
            "UPDATE research_tasks SET state=?,error=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE task_id=?",
            (state, error, utc_now(), task_id),
        )

    def release_task(self, task_id: str, reason: str) -> None:
        self.db.execute(
            """UPDATE research_tasks SET state='queued',error=?,lease_owner=NULL,lease_expires_at=NULL,
            attempts=CASE WHEN attempts>0 THEN attempts-1 ELSE 0 END,updated_at=? WHERE task_id=? AND state='running'""",
            (reason, utc_now(), task_id),
        )

    def set_watermark(self, source: str, scope: str, watermark: str | None, status: str, detail: str | None = None) -> None:
        self.db.execute(
            """INSERT INTO source_watermarks VALUES(?,?,?,?,?,?) ON CONFLICT(source,scope) DO UPDATE SET
            watermark=excluded.watermark,status=excluded.status,checked_at=excluded.checked_at,detail=excluded.detail""",
            (source, scope, watermark, status, utc_now(), detail),
        )

    def source_watermark(self, source: str, scope: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM source_watermarks WHERE source=? AND scope=?", (source, scope)).fetchone()
        return dict(row) if row else None

    def reconciliation_time(self, source: str, scope: str) -> str | None:
        row = self.db.execute("SELECT last_reconciled_at FROM source_reconciliations WHERE source=? AND scope=?",
                              (source, scope)).fetchone()
        return str(row["last_reconciled_at"]) if row else None

    def mark_reconciled(self, source: str, scope: str, at: str | None = None) -> None:
        self.db.execute(
            """INSERT INTO source_reconciliations VALUES(?,?,?) ON CONFLICT(source,scope) DO UPDATE SET
            last_reconciled_at=excluded.last_reconciled_at""", (source, scope, at or utc_now()),
        )

    def record_failure(self, source: str, scope: str, item_key: str | None, error: str, retryable: bool) -> str:
        failure_id = stable_id("failure", source, scope, item_key)
        now = utc_now()
        self.db.execute(
            """INSERT INTO source_failures VALUES(?,?,?,?,?,?,?,?,NULL) ON CONFLICT(failure_id) DO UPDATE SET
            error=excluded.error,retryable=excluded.retryable,last_seen_at=excluded.last_seen_at,resolved_at=NULL""",
            (failure_id, source, scope, item_key, error, int(retryable), now, now),
        )
        return failure_id

    def resolve_failures(self, source: str, scope: str, item_key: str | None = None) -> int:
        if item_key is None:
            cursor = self.db.execute(
                "UPDATE source_failures SET resolved_at=? WHERE source=? AND scope=? AND resolved_at IS NULL",
                (utc_now(), source, scope),
            )
        else:
            cursor = self.db.execute(
                "UPDATE source_failures SET resolved_at=? WHERE source=? AND scope=? AND item_key=? AND resolved_at IS NULL",
                (utc_now(), source, scope, item_key),
            )
        return cursor.rowcount

    def status(self) -> dict[str, Any]:
        self.reap_expired_tasks()
        queue = {row["state"]: row["count"] for row in self.db.execute("SELECT state,COUNT(*) count FROM research_tasks GROUP BY state")}
        oldest = self.db.execute("SELECT created_at FROM research_tasks WHERE state IN ('queued','retryable_failed') ORDER BY created_at LIMIT 1").fetchone()
        issuers = self.db.execute("SELECT COUNT(*) count FROM issuers WHERE identity_status='verified_sec'").fetchone()["count"]
        documents = self.db.execute("SELECT COUNT(*) count FROM documents").fetchone()["count"]
        events = self.db.execute("SELECT COUNT(*) count FROM earnings_events").fetchone()["count"]
        failures = self.db.execute("SELECT COUNT(*) count FROM source_failures WHERE resolved_at IS NULL").fetchone()["count"]
        watermarks = [dict(row) for row in self.db.execute("SELECT * FROM source_watermarks ORDER BY source,scope")]
        source_items = {row["status"]: row["count"] for row in self.db.execute("SELECT status,COUNT(*) count FROM source_items GROUP BY status")}
        age = None
        if oldest:
            age = max(0, int((datetime.now(timezone.utc) - parse_time(oldest["created_at"])).total_seconds()))
        return {"queue": queue, "oldest_queued_age_seconds": age, "verified_issuers": issuers,
                "documents": documents, "events": events, "unresolved_source_failures": failures,
                "watermarks": watermarks, "source_items": source_items}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read earnings research queue and collection state")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--state", default="runtime/earnings/state.sqlite")
    parser.add_argument("--date")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = Path(args.repo_root).resolve()
    state = EarningsState(confined_path(root, args.state, "runtime/earnings"))
    try:
        payload = envelope("earnings-status", args.date, state_path=str(state.path), summary=state.status())
        payload["artifacts"] = [str(state.path)]
        emit(payload)
    finally:
        state.close()


if __name__ == "__main__":
    main()
