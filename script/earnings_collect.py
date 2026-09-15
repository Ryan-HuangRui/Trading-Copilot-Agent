#!/usr/bin/env python3
"""Collect immutable SEC/issuer-IR originals and enqueue changed earnings events."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any

from earnings_common import (ROOT, atomic_write_json, canonical_json, confined_path, configuration_path, emit, envelope, ensure_inside, load_config, parse_time,
                             read_json, relative_to_root, resolve_path, safe_segment, sha256_bytes, shanghai_date, stable_id, utc_now)
from earnings_sources import IssuerIRClient, SecClient, SharedRateLimiter, SourceError, sec_recent_filings
from earnings_state import EarningsState


FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "20-F/A", "6-K", "S-1", "S-1/A", "F-1", "F-1/A"}
PERIODIC_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A"}
RELEASE_FORMS = {"8-K", "8-K/A", "6-K"}


def _filing_date(filing: dict[str, Any]) -> date | None:
    value = str(filing.get("filingDate") or "")
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _eligible_before_cutoff(filing: dict[str, Any], cutoff: datetime) -> bool:
    accepted = _sec_acceptance(filing.get("acceptanceDateTime"))
    filed = _filing_date(filing)
    return bool((accepted and accepted <= cutoff) or (not accepted and filed and filed < cutoff.date()))


def _history_range(file_meta: dict[str, Any]) -> tuple[date, date] | None:
    try:
        return date.fromisoformat(str(file_meta["filingFrom"])), date.fromisoformat(str(file_meta["filingTo"]))
    except (KeyError, TypeError, ValueError):
        return None


def _period_forms(filings: list[dict[str, Any]]) -> dict[str, set[str]]:
    periods: dict[str, set[str]] = {}
    for filing in filings:
        period = str(filing.get("reportDate") or "")
        if filing.get("form") in PERIODIC_FORMS and period:
            periods.setdefault(period, set()).add(str(filing["form"]))
    return periods


def _select_initial_periods(period_forms: dict[str, set[str]], target: int) -> list[str]:
    """Select quarter endpoints while capping annual-only disclosures to one year-end slot per four periods."""
    selected: list[str] = []
    year_ends = 0
    max_year_ends = max(1, (target + 3) // 4)
    for period in sorted(period_forms, reverse=True):
        forms = period_forms[period]
        if any(form.startswith("10-Q") for form in forms):
            selected.append(period)
        elif year_ends < max_year_ends:
            selected.append(period)
            year_ends += 1
        if len(selected) >= target:
            break
    return selected


def _incremental_window(state: EarningsState, config: dict[str, Any], symbol: str,
                        cutoff: datetime) -> tuple[datetime, bool, dict[str, Any] | None]:
    sec_config = config.get("sources", {}).get("sec", {})
    overlap_days = max(1, int(sec_config.get("overlap_days", 3)))
    reconcile_days = max(overlap_days, int(sec_config.get("reconcile_every_days", 7)))
    watermark = state.source_watermark("sec", symbol)
    checkpoint = parse_time(watermark.get("watermark")) if watermark and watermark.get("status") == "success" and watermark.get("watermark") else None
    start = (checkpoint or cutoff) - timedelta(days=overlap_days)
    reconciled_text = state.reconciliation_time("sec", symbol)
    reconciled = parse_time(reconciled_text) if reconciled_text else None
    reconcile_due = bool(watermark and (reconciled is None or reconciled <= cutoff - timedelta(days=reconcile_days)))
    if reconcile_due:
        start = min(start, cutoff - timedelta(days=reconcile_days))
    return start, reconcile_due, watermark


def _sec_acceptance(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 14 and text.isdigit():
        # EDGAR compact acceptanceDateTime is Eastern local time, not UTC.
        from zoneinfo import ZoneInfo

        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    return parse_time(text)


def _event_identity(issuer_id: str, filing: dict[str, Any]) -> tuple[str, str, str | None]:
    form = str(filing.get("form") or "")
    accession = str(filing["accessionNumber"])
    if form.startswith(("S-1", "F-1")):
        return stable_id("event", issuer_id, "ipo-registration"), "ipo", None
    if form in PERIODIC_FORMS:
        period_end = str(filing.get("reportDate") or "") or None
        if period_end:
            return stable_id("event", issuer_id, "earnings", period_end), "earnings", period_end
    # An 8-K/6-K reportDate is the event date, not a verified fiscal period.
    return stable_id("event", issuer_id, "unresolved-filing", accession), "unresolved_earnings", None


def _qualifying_exhibits(index_payload: dict[str, Any], primary_document: str) -> list[dict[str, Any]]:
    exhibits: list[dict[str, Any]] = []
    for item in index_payload.get("directory", {}).get("item", []):
        name = str(item.get("name") or "")
        lowered = name.lower()
        suffix = Path(name).suffix.lower()
        if not name or name == primary_document or Path(name).name != name or suffix not in {".htm", ".html", ".xhtml", ".txt"}:
            continue
        declared_type = str(item.get("documentType") or item.get("document_type") or item.get("exhibitType") or "").upper().replace(" ", "")
        compact = re.sub(r"[^a-z0-9]", "", lowered)
        if declared_type in {"EX-99.1", "EX99.1", "EX991"}:
            exhibits.append({"name": name, "selection_method": "sec_document_type", "limitations": []})
        elif re.search(r"ex991(?!\d)", compact) or "earningsrelease" in compact or "pressrelease" in compact:
            exhibits.append({"name": name, "selection_method": "filename_heuristic",
                             "limitations": ["EX-99.1 inferred from filename; confirm filing document type/original content"]})
    by_name = {row["name"]: row for row in exhibits}
    return sorted(by_name.values(), key=lambda row: (0 if row["selection_method"] == "sec_document_type" else 1, row["name"]))[:3]


def _store_original(root: Path, issuer_id: str, document_id: str, content: bytes, suffix: str) -> tuple[Path, str]:
    digest = sha256_bytes(content)
    safe_issuer = safe_segment(issuer_id, "issuer id").replace(":", "-")
    safe_document = stable_id("doc", document_id)
    allowed_suffixes = {".htm", ".html", ".txt", ".json", ".xml", ".pdf", ".xhtml", ".bin"}
    normalized_suffix = str(suffix or ".bin").lower()
    if normalized_suffix not in allowed_suffixes:
        normalized_suffix = ".bin"
    path = confined_path(root, root / "raw_data" / "earnings" / safe_issuer / safe_document / digest[:16] / f"original{normalized_suffix}",
                         "raw_data/earnings")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and sha256_bytes(path.read_bytes()) != digest:
        raise ValueError(f"immutable original collision: {path}")
    if not path.exists():
        path.write_bytes(content)
    return path, digest


def _document_record(*, issuer_id: str, event_id: str, document_id: str, source_type: str,
                     source_url: str, provider: str, backend: str, form: str | None,
                     period_start: str | None, period_end: str | None, published_at: str | None,
                     accepted_at: str | None, fetched_at: str, original_path: str, digest: str,
                     source_mode: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "document_id": document_id, "issuer_id": issuer_id, "event_id": event_id, "form": form,
        "source_type": source_type, "source_url": source_url, "provider": provider, "backend": backend,
        "reporting_start": period_start, "reporting_end": period_end, "published_at": published_at,
        "accepted_at": accepted_at, "fetched_at": fetched_at, "public_time_precision": "second" if accepted_at else ("day" if published_at else "unknown"),
        "original_path": original_path, "content_sha256": digest, "source_mode": source_mode,
        "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
    }


def _profile(config: dict[str, Any], name: str = "daily") -> tuple[str, str]:
    row = config["profiles"][name]
    return str(row["model"]), str(row["reasoning_effort"])


def _enqueue_event(state: EarningsState, config: dict[str, Any], config_hash: str,
                   event: dict[str, Any], source_mode: str) -> tuple[str, bool]:
    model, effort = _profile(config)
    frozen = state.event_input_snapshot(event["event_id"], event["issuer_id"])
    issuer = state.db.execute("SELECT * FROM issuers WHERE issuer_id=?", (event["issuer_id"],)).fetchone()
    if not issuer:
        raise ValueError(f"issuer missing before task freeze: {event['issuer_id']}")
    frozen_issuer = {key: issuer[key] for key in ("issuer_id", "cik", "symbol", "name", "identity_status")}
    frozen.update({"event": dict(event), "issuer": frozen_issuer,
                   "configuration_hash": config_hash, "source_mode": source_mode})
    input_hash = sha256_bytes(canonical_json(frozen))
    task_id, created = state.enqueue_task(
        task_type="company", subject_id=event["event_id"], period_start=event.get("reporting_start"),
        period_end=event.get("reporting_end"), input_hash=input_hash, method_version="earnings-method-v1",
        source_mode=source_mode, profile="daily", model=model, effort=effort,
        max_attempts=int(config["budgets"].get("max_task_attempts", 2)),
    )
    state.freeze_task_input(task_id, frozen, input_hash)
    return task_id, created


def collect_offline(root: Path, state: EarningsState, config: dict[str, Any], config_hash: str, input_path: Path,
                    cutoff: datetime) -> dict[str, Any]:
    payload = read_json(input_path)
    if not isinstance(payload, dict) or payload.get("source_mode") != "fixture":
        raise ValueError("offline input must declare source_mode=fixture")
    registered = revised = duplicate = queued = 0
    events: dict[str, dict[str, Any]] = {}
    for issuer in payload.get("issuers", []):
        state.upsert_issuer(issuer_id=issuer["issuer_id"], cik=issuer.get("cik"), symbol=issuer.get("symbol"),
                            name=issuer["name"], identity_status=issuer.get("identity_status", "fixture"))
    for item in payload.get("documents", []):
        public_at = parse_time(item.get("accepted_at") or item.get("published_at"))
        if public_at and public_at > cutoff:
            continue
        issuer_id = item["issuer_id"]
        period_end = item.get("reporting_end")
        event_kind = item.get("event_kind", "earnings" if period_end else "ipo")
        event_id = item.get("event_id") or stable_id("event", issuer_id, event_kind, period_end or item["document_id"])
        content = item.get("content", "").encode("utf-8")
        path, digest = _store_original(root, issuer_id, item["document_id"], content, item.get("suffix", ".txt"))
        record = _document_record(
            issuer_id=issuer_id, event_id=event_id, document_id=item["document_id"], source_type=item.get("source_type", "fixture"),
            source_url=item.get("source_url", f"fixture://{item['document_id']}"), provider=item.get("provider", "fixture"), backend="offline-fixture",
            form=item.get("form"), period_start=item.get("reporting_start"), period_end=period_end,
            published_at=item.get("published_at"), accepted_at=item.get("accepted_at"), fetched_at=utc_now(),
            original_path=relative_to_root(root, path), digest=digest, source_mode="fixture", metadata={"fixture_input": str(input_path)},
        )
        version, changed = state.register_document(record)
        registered += int(changed)
        duplicate += int(not changed)
        revised += int(changed and version > 1)
        events[event_id] = {"event_id": event_id, "issuer_id": issuer_id, "event_kind": event_kind,
                            "reporting_start": item.get("reporting_start"), "reporting_end": period_end}
    for event in events.values():
        event["input_hash"] = state.refresh_event(event["event_id"], event["issuer_id"], event["event_kind"], event.get("reporting_start"), event.get("reporting_end"))
        _, created = _enqueue_event(state, config, config_hash, event, "fixture")
        queued += int(created)
    state.set_watermark("fixture", str(input_path), cutoff.isoformat(), "success", f"documents={registered};duplicates={duplicate}")
    return {"registered_documents": registered, "revisions": revised, "duplicates": duplicate, "events": len(events), "queued_tasks": queued, "failures": []}


def collect_live(root: Path, state: EarningsState, config: dict[str, Any], config_hash: str, symbols: list[str],
                 cutoff: datetime, max_filings: int, ir_manifest: Path | None,
                 collection_kind: str = "incremental") -> dict[str, Any]:
    cache = root / "runtime" / "earnings" / "cache"
    client = SecClient.from_config(config, state.db, cache / "sec")
    resolved = client.resolve_symbols(symbols)
    missing = sorted(set(symbols) - set(resolved))
    failures: list[dict[str, Any]] = []
    initialization_coverage: dict[str, Any] = {}
    registered = revised = duplicate = queued = 0
    for symbol in missing:
        error = "symbol not found in SEC ticker mapping"
        failures.append({"source": "sec", "scope": symbol, "error": error, "retryable": False})
        state.record_failure("sec", symbol, "identity", error, False)
    for symbol, issuer in resolved.items():
        state.upsert_issuer(**issuer, identity_status="verified_sec")
        state.resolve_failures("sec", symbol, "identity")
        cik = issuer["cik"]
        issuer_events: dict[str, dict[str, Any]] = {}
        try:
            submissions, submission_meta = client.submissions(cik)
            state.resolve_failures("sec", symbol, "submissions")
            recent_filings = sec_recent_filings(submissions, FORMS)
            filings = list(recent_filings)
            history_files = list(submissions.get("filings", {}).get("files", []))
            discovery_failures = False
            selected_periods: list[str] = []
            window_start, reconcile_due, previous_watermark = _incremental_window(state, config, symbol, cutoff)
            if collection_kind == "initialization":
                target = int(config["budgets"].get("initialization_lookback_quarters", 8))
                selected_periods = _select_initial_periods(
                    _period_forms([row for row in filings if _eligible_before_cutoff(row, cutoff)]), target)
                # Historical indexes are ordered by their declared date ranges and loaded only
                # until enough distinct reporting periods have been found.
                candidates = [(bounds, row) for row in history_files if (bounds := _history_range(row)) and bounds[0] <= cutoff.date()]
                for _, file_meta in sorted(candidates, key=lambda item: item[0][1], reverse=True):
                    if len(selected_periods) >= target:
                        break
                    try:
                        historical, _ = client.submissions_file(str(file_meta["name"]))
                        filings.extend(sec_recent_filings(historical, FORMS))
                        state.resolve_failures("sec", symbol, str(file_meta["name"]))
                        selected_periods = _select_initial_periods(
                            _period_forms([row for row in filings if _eligible_before_cutoff(row, cutoff)]), target)
                    except (KeyError, SourceError, json.JSONDecodeError) as exc:
                        discovery_failures = True
                        retryable = getattr(exc, "retryable", False)
                        failures.append({"source": "sec", "scope": symbol, "item": str(file_meta.get("name")),
                                         "error": str(exc), "retryable": retryable})
                        state.record_failure("sec", symbol, str(file_meta.get("name")), str(exc), retryable)
                selected = set(selected_periods)
                current_start = cutoff.date() - timedelta(days=max(1, int(config.get("sources", {}).get("sec", {}).get("overlap_days", 3))))
                filings = [row for row in filings if
                           (row.get("form") in PERIODIC_FORMS and row.get("reportDate") in selected) or
                           (row.get("form") not in PERIODIC_FORMS and _filing_date(row) and _filing_date(row) >= current_start)]
            else:
                oldest_recent = min((_filing_date(row) for row in recent_filings if _filing_date(row)), default=cutoff.date())
                # After downtime, traverse only historical index files whose declared ranges
                # overlap the checkpoint window not represented in the recent submissions set.
                if window_start.date() < oldest_recent:
                    candidates = [(bounds, row) for row in history_files if (bounds := _history_range(row))
                                  and bounds[1] >= window_start.date() and bounds[0] <= min(cutoff.date(), oldest_recent)]
                    for _, file_meta in sorted(candidates, key=lambda item: item[0][1], reverse=True):
                        try:
                            historical, _ = client.submissions_file(str(file_meta["name"]))
                            filings.extend(sec_recent_filings(historical, FORMS))
                            state.resolve_failures("sec", symbol, str(file_meta["name"]))
                        except (KeyError, SourceError, json.JSONDecodeError) as exc:
                            discovery_failures = True
                            retryable = getattr(exc, "retryable", False)
                            failures.append({"source": "sec", "scope": symbol, "item": str(file_meta.get("name")),
                                             "error": str(exc), "retryable": retryable})
                            state.record_failure("sec", symbol, str(file_meta.get("name")), str(exc), retryable)
                filings = [row for row in filings if _filing_date(row) and _filing_date(row) >= window_start.date()]
            unique_filings: dict[str, dict[str, Any]] = {str(row["accessionNumber"]): row for row in filings}
            for accession, filing in unique_filings.items():
                state.discover_source_item("sec_filing", symbol, accession, filing)
            pending = state.pending_source_items("sec_filing", symbol, max(10000, len(unique_filings) + max_filings))
            eligible_pending: list[dict[str, Any]] = []
            for pending_item in pending:
                filing = pending_item["payload"]
                accepted = _sec_acceptance(filing.get("acceptanceDateTime"))
                filing_date = parse_time(filing.get("filingDate")) if filing.get("filingDate") else None
                # Date-only values on the cutoff day cannot establish intraday availability.
                if accepted and accepted > cutoff:
                    continue
                if not accepted and (not filing_date or filing_date.date() >= cutoff.date()):
                    continue
                eligible_pending.append(pending_item)
            # Initialization prioritizes periodic filings until the configured quarter target is covered.
            if collection_kind == "initialization":
                eligible_pending.sort(key=lambda row: str(row["payload"].get("reportDate") or ""), reverse=True)
                eligible_pending.sort(key=lambda row: 0 if row["payload"].get("form") in PERIODIC_FORMS else 1)
            for pending_item in eligible_pending[:max_filings]:
                filing = pending_item["payload"]
                accession = pending_item["item_key"]
                accepted = _sec_acceptance(filing.get("acceptanceDateTime"))
                try:
                    event_id, event_kind, period_end = _event_identity(issuer["issuer_id"], filing)
                    document_specs = [{"name": str(filing["primaryDocument"]), "selection_method": "primary_document", "limitations": []}]
                    if filing.get("form") in RELEASE_FORMS:
                        index_payload, _ = client.filing_index(cik=cik, accession=accession)
                        document_specs.extend(_qualifying_exhibits(index_payload, str(filing["primaryDocument"])))
                    item_digest_parts: list[str] = []
                    for position, document_spec in enumerate(document_specs):
                        document_name = document_spec["name"]
                        content, fetch_meta, url = client.filing(cik=cik, accession=accession, primary_document=document_name)
                        path, digest = _store_original(root, issuer["issuer_id"], f"{accession}-{document_name}", content, Path(document_name).suffix or ".html")
                        record = _document_record(
                            issuer_id=issuer["issuer_id"], event_id=event_id, document_id=f"sec:{accession}:{document_name}",
                            source_type="sec_filing" if position == 0 else "sec_earnings_exhibit", source_url=url,
                            provider="sec", backend="sec-edgar-http", form=filing["form"], period_start=None, period_end=period_end,
                            published_at=filing.get("filingDate") or None, accepted_at=accepted.isoformat() if accepted else None,
                            fetched_at=fetch_meta["fetched_at"], original_path=relative_to_root(root, path), digest=digest, source_mode="live",
                            metadata={"accession": accession, "primary_document": filing["primaryDocument"],
                                      "document_name": document_name, "is_exhibit": position > 0,
                                      "selection_method": document_spec["selection_method"],
                                      "selection_limitations": document_spec["limitations"],
                                      "fiscal_period_verified": period_end is not None and filing.get("form") in PERIODIC_FORMS},
                        )
                        version, changed = state.register_document(record)
                        registered += int(changed); duplicate += int(not changed); revised += int(changed and version > 1)
                        item_digest_parts.append(digest)
                    state.finish_source_item("sec_filing", symbol, accession, digest=sha256_bytes("".join(item_digest_parts).encode()))
                    state.resolve_failures("sec", symbol, accession)
                    issuer_events[event_id] = {"event_id": event_id, "issuer_id": issuer["issuer_id"], "event_kind": event_kind,
                                               "reporting_start": None, "reporting_end": period_end}
                except (SourceError, json.JSONDecodeError) as exc:
                    retryable = getattr(exc, "retryable", True)
                    failures.append({"source": "sec", "scope": symbol, "item": accession, "error": str(exc), "retryable": retryable})
                    state.record_failure("sec", symbol, accession, str(exc), retryable)
                    state.fail_source_item("sec_filing", symbol, accession, str(exc), retryable,
                                           int(config["sources"]["sec"].get("max_attempts", 3)))
            try:
                facts, facts_meta = client.company_facts(cik)
                facts_bytes = json.dumps(facts, ensure_ascii=False, sort_keys=True).encode("utf-8")
                path, digest = _store_original(root, issuer["issuer_id"], "companyfacts", facts_bytes, ".json")
                event_id = stable_id("event", issuer["issuer_id"], "facts", "all")
                filed_dates = [str(row.get("filed")) for concepts in (facts.get("facts") or {}).values()
                               for concept in concepts.values() for rows in (concept.get("units") or {}).values()
                               for row in rows if row.get("filed")]
                latest_filed = max(filed_dates) if filed_dates else None
                record = _document_record(issuer_id=issuer["issuer_id"], event_id=event_id, document_id=f"sec:companyfacts:{cik}",
                    source_type="sec_companyfacts", source_url=client.FACTS_URL.format(cik=cik), provider="sec", backend="sec-edgar-http",
                    form=None, period_start=None, period_end=None, published_at=None, accepted_at=None,
                    fetched_at=facts_meta["fetched_at"], original_path=relative_to_root(root, path), digest=digest, source_mode="live",
                    metadata={"entity_name": facts.get("entityName"), "latest_filed_date": latest_filed,
                              "aggregate_public_time_unprovable": True})
                version, changed = state.register_document(record)
                registered += int(changed); duplicate += int(not changed); revised += int(changed and version > 1)
                state.resolve_failures("sec", symbol, "companyfacts")
            except SourceError as exc:
                failures.append({"source": "sec", "scope": symbol, "item": "companyfacts", "error": str(exc), "retryable": exc.retryable})
                state.record_failure("sec", symbol, "companyfacts", str(exc), exc.retryable)
            for existing in state.db.execute("SELECT * FROM earnings_events WHERE issuer_id=?", (issuer["issuer_id"],)):
                issuer_events.setdefault(existing["event_id"], {"event_id": existing["event_id"], "issuer_id": issuer["issuer_id"],
                    "event_kind": existing["event_kind"], "reporting_start": existing["reporting_start"], "reporting_end": existing["reporting_end"]})
            for event in issuer_events.values():
                event["input_hash"] = state.refresh_event(event["event_id"], event["issuer_id"], event["event_kind"], event.get("reporting_start"), event.get("reporting_end"))
                _, created = _enqueue_event(state, config, config_hash, event, "live")
                queued += int(created)
            prior_checkpoint = previous_watermark.get("watermark") if previous_watermark else None
            # The checkpoint is the fully discovered scan boundary, not the latest filing date;
            # successful empty scans must advance too.
            new_checkpoint = cutoff.isoformat()
            watermark_status = "partial" if discovery_failures else "success"
            state.set_watermark("sec", symbol, prior_checkpoint if discovery_failures else new_checkpoint, watermark_status,
                                f"window_start={window_start.isoformat()};reconcile_due={str(reconcile_due).lower()};"
                                f"discovered={len(unique_filings)};pending={len(state.pending_source_items('sec_filing', symbol, 100000))};"
                                f"registered={registered};explicit_failures={sum(f['scope'] == symbol for f in failures)}")
            if not discovery_failures and (reconcile_due or state.reconciliation_time("sec", symbol) is None):
                state.mark_reconciled("sec", symbol, cutoff.isoformat())
            if collection_kind == "initialization":
                period_forms = _period_forms([row for row in unique_filings.values() if _eligible_before_cutoff(row, cutoff)])
                target = int(config["budgets"].get("initialization_lookback_quarters", 8))
                periods = _select_initial_periods(period_forms, target)
                selected_rows = [{"reporting_end": period, "forms": sorted(period_forms[period]),
                                  "period_kind": "quarter" if any(form.startswith("10-Q") for form in period_forms[period]) else "year_end"}
                                 for period in periods[:target]]
                fetched_periods = {row["reporting_end"] for row in state.db.execute(
                    "SELECT DISTINCT reporting_end FROM documents WHERE issuer_id=? AND source_type='sec_filing' AND source_mode='live'",
                    (issuer["issuer_id"],)) if row["reporting_end"] in periods}
                fetched_rows = [row for row in selected_rows if row["reporting_end"] in fetched_periods]
                initialization_coverage[symbol] = {"target_periods": target, "discovered_periods": len(periods),
                    "verified_periods": selected_rows, "fetched_periods": sorted(fetched_periods, reverse=True),
                    "covered_periods": len(fetched_periods), "complete": len(fetched_periods) >= target,
                    "quarter_periods": sum(row["period_kind"] == "quarter" for row in fetched_rows),
                    "year_end_periods": sum(row["period_kind"] == "year_end" for row in fetched_rows)}
        except SourceError as exc:
            failures.append({"source": "sec", "scope": symbol, "error": str(exc), "retryable": exc.retryable})
            state.record_failure("sec", symbol, "submissions", str(exc), exc.retryable)
            state.set_watermark("sec", symbol, None, "unavailable", str(exc))

    if ir_manifest:
        ir_payload = read_json(ir_manifest)
        if ir_payload.get("source_mode") != "live":
            raise ValueError("IR manifest in live collection must declare source_mode=live")
        for issuer in ir_payload.get("issuers", []):
            if issuer.get("identity_status") != "verified_sec" or not issuer.get("cik"):
                raise ValueError("live IR issuer identity must be verified against SEC and include CIK")
            state.upsert_issuer(issuer_id=issuer["issuer_id"], cik=str(issuer["cik"]).zfill(10),
                                symbol=issuer.get("symbol"), name=issuer["name"], identity_status="verified_sec")
        ir_client = IssuerIRClient(backend="issuer-ir-http", user_agent=client.user_agent, timeout=client.timeout,
                                   max_attempts=client.max_attempts, limiter=SharedRateLimiter(state.db, "issuer_ir", 1), cache_dir=cache / "ir")
        for item in ir_payload.get("documents", []):
            try:
                issuer_id = item["issuer_id"]
                content, meta = ir_client.fetch_document(item["source_url"], stable_id("ir", item["source_url"]))
                event_kind = item.get("event_kind", "earnings")
                reporting_end = item.get("reporting_end")
                if reporting_end and not item.get("period_source"):
                    raise ValueError("issuer IR reporting_end requires period_source evidence")
                normalized_kind = "ipo" if event_kind == "ipo" else ("earnings" if reporting_end else "unresolved_earnings")
                default_event = stable_id("event", issuer_id, "ipo-registration") if normalized_kind == "ipo" else stable_id(
                    "event", issuer_id, normalized_kind, reporting_end or item["source_url"])
                event_id = item.get("event_id") or default_event
                doc_id = item.get("document_id") or stable_id("ir-doc", item["source_url"])
                suffix = {"text/html": ".html", "application/xhtml+xml": ".xhtml", "application/pdf": ".pdf",
                          "text/plain": ".txt"}.get(meta.get("content_type"), Path(item["source_url"].split("?", 1)[0]).suffix or ".bin")
                path, digest = _store_original(root, issuer_id, doc_id, content, suffix)
                record = _document_record(issuer_id=issuer_id, event_id=event_id, document_id=doc_id,
                    source_type=item.get("source_type", "issuer_ir"), source_url=item["source_url"], provider="issuer_ir", backend="issuer-ir-http",
                    form=item.get("form"), period_start=item.get("reporting_start"), period_end=reporting_end,
                    published_at=item.get("published_at"), accepted_at=None, fetched_at=meta["fetched_at"],
                    original_path=relative_to_root(root, path), digest=digest, source_mode="live", metadata={"manifest": str(ir_manifest)})
                version, changed = state.register_document(record)
                registered += int(changed); duplicate += int(not changed); revised += int(changed and version > 1)
                input_hash = state.refresh_event(event_id, issuer_id, normalized_kind, item.get("reporting_start"), reporting_end)
                event = {"event_id": event_id, "event_kind": normalized_kind,
                         "reporting_start": item.get("reporting_start"), "reporting_end": reporting_end, "input_hash": input_hash}
                event["issuer_id"] = issuer_id
                _, created = _enqueue_event(state, config, config_hash, event, "live")
                queued += int(created)
            except (SourceError, KeyError, ValueError) as exc:
                retryable = getattr(exc, "retryable", False)
                failures.append({"source": "issuer_ir", "scope": str(item.get("issuer_id")), "error": str(exc), "retryable": retryable})
                state.record_failure("issuer_ir", str(item.get("issuer_id")), str(item.get("source_url")), str(exc), retryable)
    return {"resolved_issuers": len(resolved), "unresolved_symbols": missing, "registered_documents": registered,
            "revisions": revised, "duplicates": duplicate, "queued_tasks": queued, "failures": failures,
            "initialization_coverage": initialization_coverage, "source_items": state.status().get("source_items", {})}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect SEC and issuer IR evidence for earnings research")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--config", default="config/earnings_research.json")
    parser.add_argument("--state", default="runtime/earnings/state.sqlite")
    parser.add_argument("--mode", choices=["offline", "live"], required=True)
    parser.add_argument("--collection-kind", choices=["incremental", "initialization"], default="incremental")
    parser.add_argument("--input", help="Required source_mode=fixture bundle for offline mode")
    parser.add_argument("--ir-manifest", help="Explicit source_mode=live issuer IR URLs")
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--cutoff", required=True, help="Public availability cutoff (ISO UTC)")
    parser.add_argument("--date")
    parser.add_argument("--max-filings", type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = Path(args.repo_root).resolve()
    config_path = configuration_path(root, args.config)
    config, config_hash = load_config(root, str(config_path))
    cutoff = parse_time(args.cutoff)
    if cutoff is None:
        raise ValueError("cutoff is required")
    state = EarningsState(confined_path(root, args.state, "runtime/earnings"))
    try:
        batch_date = safe_segment(args.date or shanghai_date(), "batch date")
        datetime.strptime(batch_date, "%Y-%m-%d")
        max_filings = args.max_filings
        if max_filings is None:
            max_filings = 4 if args.collection_kind == "incremental" else int(config["budgets"].get("initialization_lookback_quarters", 8)) * 3
        if max_filings < 1:
            raise ValueError("max-filings must be positive")
        if args.mode == "offline":
            if not args.input:
                raise ValueError("offline mode requires --input; fixture fallback is forbidden")
            input_path = ensure_inside(resolve_path(root, args.input), [root / "tests" / "fixtures" / "earnings", root / "runtime" / "earnings"])
            summary = collect_offline(root, state, config, config_hash, input_path, cutoff)
        else:
            symbols = sorted({symbol.strip().upper() for symbol in args.symbol if symbol.strip()})
            if args.collection_kind == "initialization":
                symbols = symbols[:int(config["budgets"].get("initialization_company_limit", 5))]
            if not symbols and not args.ir_manifest:
                raise ValueError("live mode requires --symbol or --ir-manifest")
            try:
                ir_path = ensure_inside(resolve_path(root, args.ir_manifest), [root / "runtime" / "earnings"]) if args.ir_manifest else None
                summary = collect_live(root, state, config, config_hash, symbols, cutoff, max_filings, ir_path,
                                       collection_kind=args.collection_kind)
                state.resolve_failures("sec", "live_preflight")
            except SourceError as exc:
                state.record_failure("sec", "live_preflight", None, str(exc), exc.retryable)
                state.set_watermark("sec", "live_preflight", None, "unavailable", str(exc))
                summary = {"resolved_issuers": 0, "registered_documents": 0, "queued_tasks": 0,
                           "failures": [{"source": "sec", "scope": "live_preflight", "error": str(exc), "retryable": exc.retryable}]}
                result_path = root / "runtime" / "earnings" / "runs" / f"collect-{batch_date}-live-failed.json"
                atomic_write_json(result_path, {"schema_version": 1, "workflow": "earnings-collect", "status": "failed",
                                                "source_mode": "live", "collection_kind": args.collection_kind,
                                                "cutoff": cutoff.isoformat(), "generated_at": utc_now(), "summary": summary})
                payload = envelope("earnings-collect", batch_date, source_mode="live",
                                   collection_kind=args.collection_kind, cutoff=cutoff.isoformat(), summary=summary)
                payload.update(status="failed", reason=str(exc), artifacts=[relative_to_root(root, result_path), relative_to_root(root, state.path)])
                emit(payload, 1)
        result_path = root / "runtime" / "earnings" / "runs" / f"collect-{batch_date}-{args.mode}.json"
        result = {"schema_version": 1, "workflow": "earnings-collect", "source_mode": "fixture" if args.mode == "offline" else "live",
                  "collection_kind": args.collection_kind, "cutoff": cutoff.isoformat(), "generated_at": utc_now(), "summary": summary}
        atomic_write_json(result_path, result)
        payload = envelope("earnings-collect", batch_date, source_mode=result["source_mode"],
                           collection_kind=args.collection_kind, cutoff=cutoff.isoformat(), summary=summary)
        payload["artifacts"] = [relative_to_root(root, result_path), relative_to_root(root, state.path)]
        if summary.get("failures"):
            payload["reason"] = "collection completed with explicit source failures"
        emit(payload)
    finally:
        state.close()


if __name__ == "__main__":
    main()
