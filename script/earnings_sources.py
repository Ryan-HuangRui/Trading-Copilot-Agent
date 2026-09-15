#!/usr/bin/env python3
"""SEC and issuer-IR source clients with explicit modes, cache and bounded retry."""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit, parse_qs
import email.utils
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from earnings_common import atomic_write_json, sha256_bytes, utc_now


class FilingDocumentTable(HTMLParser):
    """Read SEC filing index document types instead of guessing exhibit filenames."""
    def __init__(self):
        super().__init__()
        self.documents = []
        self.cells = []
        self.cell = None
        self.href = ""

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells = []
        elif tag == "td":
            self.cell, self.href = [], ""
        elif tag == "a" and self.cell is not None:
            self.href = dict(attrs).get("href", "")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.cells.append(("".join(self.cell).strip(), self.href))
            self.cell = None
        elif tag == "tr" and len(self.cells) >= 4:
            label, href = self.cells[2]
            doc_type = self.cells[3][0]
            parsed = urlsplit(href)
            document_path = parse_qs(parsed.query).get("doc", [parsed.path])[0]
            name = Path(document_path).name
            if name and href and doc_type:
                self.documents.append({"name": name, "documentType": doc_type, "description": self.cells[1][0]})


class SourceError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class SharedRateLimiter:
    def __init__(self, db: sqlite3.Connection, source: str, requests_per_second: float):
        self.db = db
        self.source = source
        self.interval = 1.0 / max(requests_per_second, 0.01)

    def wait(self) -> None:
        while True:
            now = time.time()
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT next_allowed_at FROM source_rate_limit WHERE source=?", (self.source,)).fetchone()
            next_allowed = float(row[0]) if row else now
            if next_allowed <= now:
                self.db.execute(
                    "INSERT INTO source_rate_limit VALUES(?,?) ON CONFLICT(source) DO UPDATE SET next_allowed_at=excluded.next_allowed_at",
                    (self.source, now + self.interval),
                )
                self.db.commit()
                return
            self.db.rollback()
            time.sleep(min(next_allowed - now, self.interval))


class HttpSourceClient:
    def __init__(self, *, backend: str, user_agent: str, timeout: int, max_attempts: int,
                 limiter: SharedRateLimiter | None = None, cache_dir: Path | None = None):
        self.backend = backend
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.limiter = limiter
        self.cache_dir = cache_dir

    def fetch(self, url: str, *, accept: str = "application/json", cache_key: str | None = None,
              allow_cached: bool = True) -> tuple[bytes, dict[str, Any]]:
        safe_cache_key = sha256_bytes(str(cache_key).encode("utf-8")) if cache_key else None
        cache_path = self.cache_dir / f"{safe_cache_key}.bin" if self.cache_dir and safe_cache_key else None
        meta_path = cache_path.with_suffix(".meta.json") if cache_path else None
        if allow_cached and cache_path and cache_path.exists() and meta_path and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return cache_path.read_bytes(), {**meta, "used_cache": True}
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            if self.limiter:
                self.limiter.wait()
            request = Request(url, headers={"User-Agent": self.user_agent, "Accept": accept, "Accept-Encoding": "identity"})
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    content = response.read()
                    fetched_at = utc_now()
                    meta = {
                        "url": response.geturl(),
                        "status": response.status,
                        "content_type": response.headers.get_content_type(),
                        "last_modified": response.headers.get("Last-Modified"),
                        "fetched_at": fetched_at,
                        "content_sha256": sha256_bytes(content),
                        "used_cache": False,
                        "attempts": attempt,
                        "backend": self.backend,
                    }
                    if cache_path and meta_path:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_bytes(content)
                        atomic_write_json(meta_path, meta)
                    return content, meta
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    raise SourceError(f"HTTP {exc.code} for {url}", retryable=False) from exc
                retry_after = exc.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(8.0, 0.5 * 2 ** (attempt - 1))
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                delay = min(8.0, 0.5 * 2 ** (attempt - 1))
            if attempt < self.max_attempts:
                time.sleep(delay + random.random() * 0.1)
        raise SourceError(f"source request failed after {self.max_attempts} attempts: {url}: {last_error}") from last_error


class SecClient(HttpSourceClient):
    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    SUBMISSIONS_FILE_URL = "https://data.sec.gov/submissions/{name}"
    ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_compact}/{primary_document}"
    ARCHIVE_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_compact}/index.json"

    @classmethod
    def from_config(cls, config: dict[str, Any], db: sqlite3.Connection, cache_dir: Path) -> "SecClient":
        sec = config["sources"]["sec"]
        env_name = sec.get("user_agent_env", "TCA_SEC_USER_AGENT")
        user_agent = os.environ.get(env_name, "").strip()
        if not user_agent or "@" not in user_agent:
            raise SourceError(f"live SEC mode requires {env_name} with operator contact email", retryable=False)
        return cls(
            backend="sec-edgar-http", user_agent=user_agent,
            timeout=int(sec.get("timeout_seconds", 30)), max_attempts=int(sec.get("max_attempts", 3)),
            limiter=SharedRateLimiter(db, "sec", float(sec.get("max_requests_per_second", 2))), cache_dir=cache_dir,
        )

    def resolve_symbols(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        content, _ = self.fetch(self.TICKERS_URL, cache_key="company-tickers")
        rows = json.loads(content)
        by_symbol = {str(row["ticker"]).upper(): row for row in rows.values()}
        resolved: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            row = by_symbol.get(symbol.upper())
            if row:
                cik = str(row["cik_str"]).zfill(10)
                resolved[symbol.upper()] = {"issuer_id": f"cik:{cik}", "cik": cik, "name": row["title"], "symbol": symbol.upper()}
        return resolved

    def submissions(self, cik: str) -> tuple[dict[str, Any], dict[str, Any]]:
        content, meta = self.fetch(self.SUBMISSIONS_URL.format(cik=cik), cache_key=f"submissions-{cik}", allow_cached=False)
        return json.loads(content), meta

    def company_facts(self, cik: str) -> tuple[dict[str, Any], dict[str, Any]]:
        content, meta = self.fetch(self.FACTS_URL.format(cik=cik), cache_key=f"companyfacts-{cik}", allow_cached=False)
        return json.loads(content), meta

    def submissions_file(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not name.startswith("CIK") or "/" in name or "\\" in name or not name.endswith(".json"):
            raise SourceError(f"invalid SEC submissions file name: {name}", retryable=False)
        content, meta = self.fetch(self.SUBMISSIONS_FILE_URL.format(name=name), cache_key=f"submissions-file-{name}", allow_cached=False)
        return json.loads(content), meta

    def filing(self, *, cik: str, accession: str, primary_document: str) -> tuple[bytes, dict[str, Any], str]:
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise SourceError(f"invalid SEC accession: {accession}", retryable=False)
        if Path(primary_document).name != primary_document or primary_document in {".", ".."}:
            raise SourceError(f"invalid SEC primary document name: {primary_document}", retryable=False)
        url = self.ARCHIVE_URL.format(cik_int=int(cik), accession_compact=accession.replace("-", ""), primary_document=primary_document)
        content, meta = self.fetch(url, accept="text/html,application/xhtml+xml", cache_key=f"filing-{accession}-{primary_document}", allow_cached=True)
        return content, meta, url

    def filing_index(self, *, cik: str, accession: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise SourceError(f"invalid SEC accession: {accession}", retryable=False)
        url = self.ARCHIVE_INDEX_URL.format(cik_int=int(cik), accession_compact=accession.replace("-", ""))
        content, meta = self.fetch(url, cache_key=f"filing-index-{accession}", allow_cached=True)
        payload = json.loads(content)
        html, table_meta, _ = self.filing(cik=cik, accession=accession, primary_document=f"{accession}-index.html")
        table = FilingDocumentTable()
        table.feed(html.decode("utf-8", errors="replace"))
        by_name = {item["name"]: dict(item) for item in payload.get("directory", {}).get("item", [])}
        for item in table.documents:
            by_name[item["name"]] = {**by_name.get(item["name"], {}), **item}
        payload.setdefault("directory", {})["item"] = list(by_name.values())
        return payload, {**meta, "document_table_sha256": table_meta["content_sha256"]}


class IssuerIRClient(HttpSourceClient):
    """Fetch explicitly supplied issuer IR originals; discovery stays manifest-driven."""

    def fetch_document(self, url: str, cache_key: str) -> tuple[bytes, dict[str, Any]]:
        if not url.lower().startswith("https://"):
            raise SourceError("issuer IR URL must use HTTPS", retryable=False)
        return self.fetch(url, accept="application/pdf,text/html,text/plain,*/*", cache_key=cache_key, allow_cached=True)


def sec_recent_filings(submissions: dict[str, Any], forms: set[str]) -> list[dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent") or submissions
    keys = ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "primaryDocument", "primaryDocDescription")
    accession_rows = recent.get("accessionNumber") or []
    result = []
    for index in range(len(accession_rows)):
        row = {}
        for key in keys:
            values = recent.get(key) or []
            row[key] = values[index] if index < len(values) else ""
        if row["accessionNumber"] and row["form"] in forms and row["primaryDocument"]:
            result.append(row)
    return result
