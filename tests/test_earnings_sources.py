import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "script"))

from earnings_collect import (_enqueue_event, _event_identity, _qualifying_exhibits, _sec_acceptance,
                              _select_initial_periods, collect_live, collect_offline)
from earnings_common import canonical_json, sha256_bytes
from earnings_sources import SecClient, SourceError, sec_recent_filings
from earnings_state import EarningsState


class EarningsSourcesTests(unittest.TestCase):
    def config(self):
        return {"profiles": {"daily": {"model": "gpt-5.6-sol", "reasoning_effort": "medium"}},
                "budgets": {"max_task_attempts": 2}}

    def test_offline_duplicate_and_same_event_multiple_documents(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); state = EarningsState(root / "runtime/earnings/state.sqlite")
            fixture = ROOT / "tests/fixtures/earnings/sample_bundle.json"
            cutoff = datetime(2026, 9, 14, tzinfo=timezone.utc)
            first = collect_offline(root, state, self.config(), "config-hash", fixture, cutoff)
            second = collect_offline(root, state, self.config(), "config-hash", fixture, cutoff)
            self.assertEqual(first["registered_documents"], 2)
            self.assertEqual(first["events"], 1)
            self.assertEqual(second["registered_documents"], 0)
            self.assertEqual(second["duplicates"], 2)
            self.assertEqual(state.status()["queue"], {"queued": 1})
            state.close()

    def test_revision_creates_new_document_version_and_new_task(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); state = EarningsState(root / "runtime/earnings/state.sqlite")
            payload = json.loads((ROOT / "tests/fixtures/earnings/sample_bundle.json").read_text())
            path = root / "fixture.json"; path.write_text(json.dumps(payload))
            cutoff = datetime(2026, 9, 14, tzinfo=timezone.utc)
            collect_offline(root, state, self.config(), "config-hash", path, cutoff)
            payload["documents"][0]["content"] += " amended"
            path.write_text(json.dumps(payload))
            result = collect_offline(root, state, self.config(), "config-hash", path, cutoff)
            self.assertEqual(result["revisions"], 1)
            self.assertEqual(state.status()["queue"], {"queued": 1, "terminal_failed": 1})
            state.close()

    def test_sec_recent_filings_keeps_revisions_and_ipo_without_ticker_logic(self):
        recent = {key: [] for key in ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "primaryDocument", "primaryDocDescription")}
        for values in [("1", "2026-01-01", "2025-12-31", "20260101120000", "10-K", "a.htm", "annual"),
                       ("2", "2026-01-02", "2025-12-31", "20260102120000", "10-K/A", "b.htm", "amendment"),
                       ("3", "2026-01-03", "", "20260103120000", "S-1", "c.htm", "IPO")]:
            for key, value in zip(recent, values): recent[key].append(value)
        rows = sec_recent_filings({"filings": {"recent": recent}}, {"10-K", "10-K/A", "S-1"})
        self.assertEqual([row["form"] for row in rows], ["10-K", "10-K/A", "S-1"])
        self.assertEqual(sec_recent_filings(recent, {"S-1"})[0]["accessionNumber"], "3")

    def test_sparse_optional_sec_fields_do_not_drop_required_rows(self):
        payload = {"accessionNumber": ["1"], "form": ["10-Q"], "primaryDocument": ["a.htm"],
                   "filingDate": [], "reportDate": [], "acceptanceDateTime": []}
        rows = sec_recent_filings(payload, {"10-Q"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reportDate"], "")

    def test_release_period_is_unresolved_and_ipo_amendments_share_event(self):
        release = {"accessionNumber": "a", "form": "8-K", "reportDate": "2026-08-01"}
        _, kind, period = _event_identity("cik:1", release)
        self.assertEqual((kind, period), ("unresolved_earnings", None))
        first = _event_identity("cik:1", {"accessionNumber": "b", "form": "S-1", "reportDate": ""})[0]
        amended = _event_identity("cik:1", {"accessionNumber": "c", "form": "S-1/A", "reportDate": ""})[0]
        self.assertEqual(first, amended)

    def test_sec_document_table_finds_release_without_exhibit_filename(self):
        from earnings_sources import FilingDocumentTable
        parser = FilingDocumentTable()
        parser.feed('<table><tr><td>2</td><td>Press Release</td><td><a href="/Archives/edgar/data/1045810/000104581026000073/q2fy27pr.htm">q2fy27pr.htm</a></td><td>EX-99.1</td><td>100</td></tr></table>')
        chosen = _qualifying_exhibits({'directory': {'item': parser.documents}}, 'primary.htm')
        self.assertEqual(chosen[0]['name'], 'q2fy27pr.htm')
        self.assertEqual(chosen[0]['selection_method'], 'sec_document_type')

    def test_earnings_exhibit_selection_is_bounded_and_safe(self):
        payload = {"directory": {"item": [{"name": "primary.htm"},
                   {"name": "nvda-20260726xex991.htm"}, {"name": "googex991q22026.htm"},
                   {"name": "typed.htm", "documentType": "EX-99.1"}, {"name": "ex99-2.htm"},
                   {"name": "chart-ex991.jpg"}, {"name": "../escape.htm"}, {"name": "unrelated.bin"}]}}
        selected = _qualifying_exhibits(payload, "primary.htm")
        self.assertEqual([row["name"] for row in selected],
                         ["typed.htm", "googex991q22026.htm", "nvda-20260726xex991.htm"])
        self.assertEqual(selected[0]["selection_method"], "sec_document_type")
        self.assertTrue(selected[1]["limitations"])

    def test_compact_sec_acceptance_uses_eastern_time(self):
        self.assertEqual(_sec_acceptance("20260701120000").isoformat(), "2026-07-01T16:00:00+00:00")
        self.assertEqual(_sec_acceptance("20260101120000").isoformat(), "2026-01-01T17:00:00+00:00")

    def test_annual_only_20f_history_does_not_masquerade_as_eight_quarters(self):
        annuals = {f"{year}-12-31": {"20-F"} for year in range(2018, 2026)}
        selected = _select_initial_periods(annuals, 8)
        self.assertEqual(len(selected), 2)

    def test_sec_symbol_resolution_normalizes_cik_without_guessing(self):
        client = SecClient(backend="test", user_agent="operator@example.com", timeout=1, max_attempts=1)
        rows = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
        client.fetch = lambda *args, **kwargs: (json.dumps(rows).encode(), {})
        resolved = client.resolve_symbols(["NVDA", "UNKNOWN"])
        self.assertEqual(resolved["NVDA"]["issuer_id"], "cik:0001045810")
        self.assertNotIn("UNKNOWN", resolved)

    def test_source_failure_is_not_an_empty_success(self):
        error = SourceError("timeout", retryable=True)
        self.assertTrue(error.retryable)
        self.assertNotEqual(str(error), "")

    def test_companyfacts_revision_invalidates_existing_event_task(self):
        with TemporaryDirectory() as temp:
            state = EarningsState(Path(temp) / "state.sqlite")
            state.upsert_issuer(issuer_id="cik:0000000001", cik="0000000001", symbol="X", name="X", identity_status="verified_sec")
            base = {"issuer_id": "cik:0000000001", "form": None, "source_url": "https://www.sec.gov/x", "provider": "sec",
                    "backend": "test", "reporting_start": None, "published_at": "2026-01-01", "accepted_at": None,
                    "fetched_at": "2026-01-02T00:00:00Z", "public_time_precision": "day", "supersedes": None,
                    "source_mode": "live", "metadata_json": "{}"}
            state.register_document({**base, "document_id": "filing", "event_id": "event", "source_type": "sec_filing",
                                     "reporting_end": "2025-12-31", "original_path": "raw_data/earnings/f", "content_sha256": "a" * 64})
            state.register_document({**base, "document_id": "facts", "event_id": "facts-event", "source_type": "sec_companyfacts",
                                     "reporting_end": None, "original_path": "raw_data/earnings/cf1", "content_sha256": "b" * 64})
            event = {"event_id": "event", "issuer_id": "cik:0000000001", "event_kind": "earnings",
                     "reporting_start": None, "reporting_end": "2025-12-31"}
            event["input_hash"] = state.refresh_event("event", event["issuer_id"], "earnings", None, event["reporting_end"])
            first, _ = _enqueue_event(state, self.config(), "config", event, "live")
            state.register_document({**base, "document_id": "facts", "event_id": "facts-event", "source_type": "sec_companyfacts",
                                     "reporting_end": None, "original_path": "raw_data/earnings/cf2", "content_sha256": "c" * 64})
            event["input_hash"] = state.refresh_event("event", event["issuer_id"], "earnings", None, event["reporting_end"])
            second, _ = _enqueue_event(state, self.config(), "config", event, "live")
            self.assertNotEqual(first, second)
            self.assertEqual(state.status()["queue"], {"queued": 1, "terminal_failed": 1})
            state.close()

    def test_scoped_config_hash_migration_reuses_identical_legacy_frozen_task(self):
        with TemporaryDirectory() as temp:
            state = EarningsState(Path(temp) / "state.sqlite")
            state.upsert_issuer(issuer_id="issuer", cik=None, symbol="X", name="X", identity_status="resolved")
            event = {"event_id": "event", "issuer_id": "issuer", "event_kind": "earnings",
                     "reporting_start": None, "reporting_end": "2026-06-30"}
            event["input_hash"] = state.refresh_event("event", "issuer", "earnings", None, "2026-06-30")
            frozen = state.event_input_snapshot("event", "issuer")
            frozen.update(event=event, issuer={"issuer_id": "issuer", "cik": None, "symbol": "X", "name": "X",
                                               "identity_status": "resolved"},
                          configuration_hash="legacy-full-config-hash", source_mode="live")
            legacy_hash = sha256_bytes(canonical_json(frozen))
            legacy, _ = state.enqueue_task(task_type="company", subject_id="event", period_start=None,
                period_end="2026-06-30", input_hash=legacy_hash, method_version="earnings-method-v1",
                source_mode="live", profile="daily", model="gpt-5.6-sol", effort="medium")
            state.freeze_task_input(legacy, frozen, legacy_hash)
            current, created = _enqueue_event(state, self.config(), "unrelated-new-full-hash", event, "live")
            self.assertEqual((current, created), (legacy, False))
            self.assertEqual(state.db.execute("SELECT COUNT(*) FROM research_tasks").fetchone()[0], 1)
            state.close()

    def test_bounded_fetch_keeps_discovered_backlog_and_initialization_reaches_eight_periods(self):
        class FakeSec:
            FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            user_agent = "test@example.com"; timeout = 1; max_attempts = 1
            historical_calls = []
            def resolve_symbols(self, symbols):
                return {"NVDA": {"issuer_id": "cik:0001045810", "cik": "0001045810", "name": "NVIDIA", "symbol": "NVDA"}}
            def submissions(self, cik):
                return {"filings": {"recent": self.rows(2), "files": [{"name": "CIK0001045810-submissions-001.json",
                        "filingFrom": "2026-03-01", "filingTo": "2026-08-31"},
                        {"name": "CIK0001045810-submissions-002.json", "filingFrom": "2020-01-01", "filingTo": "2025-12-31"}]}}, {"fetched_at": "2026-09-01T00:00:00Z"}
            def submissions_file(self, name):
                self.historical_calls.append(name)
                return self.rows(6, offset=2), {"fetched_at": "2026-09-01T00:00:00Z"}
            def rows(self, count, offset=0):
                rows = {key: [] for key in ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "primaryDocument", "primaryDocDescription")}
                for index in range(offset, offset + count):
                    values = (f"0001045810-26-{index:06d}", f"2026-{index + 1:02d}-01", f"2025-{index + 1:02d}-28",
                              f"2026-{index + 1:02d}-01T12:00:00Z", "10-Q", f"q{index}.htm", "quarter")
                    for key, value in zip(rows, values): rows[key].append(value)
                return rows
            def filing(self, *, cik, accession, primary_document):
                return f"filing {accession}".encode(), {"fetched_at": "2026-09-01T00:00:00Z"}, f"https://www.sec.gov/{accession}/{primary_document}"
            def company_facts(self, cik): return {"entityName": "NVIDIA", "facts": {}}, {"fetched_at": "2026-09-01T00:00:00Z"}

        config = self.config(); config["sources"] = {"sec": {"max_attempts": 3, "overlap_days": 3, "reconcile_every_days": 7}}
        config["budgets"].update(initialization_lookback_quarters=8)
        with TemporaryDirectory() as temp:
            root = Path(temp); state = EarningsState(root / "runtime/earnings/state.sqlite")
            with patch("earnings_collect.SecClient.from_config", return_value=FakeSec()):
                first = collect_live(root, state, config, "cfg", ["NVDA"], datetime(2026, 12, 31, tzinfo=timezone.utc), 3, None, "initialization")
                self.assertEqual(first["source_items"], {"fetched": 3, "pending": 5})
                self.assertFalse(first["initialization_coverage"]["NVDA"]["complete"])
                self.assertEqual(first["initialization_coverage"]["NVDA"]["covered_periods"], 3)
                collect_live(root, state, config, "cfg", ["NVDA"], datetime(2026, 12, 31, tzinfo=timezone.utc), 3, None, "initialization")
                third = collect_live(root, state, config, "cfg", ["NVDA"], datetime(2026, 12, 31, tzinfo=timezone.utc), 3, None, "initialization")
                self.assertTrue(third["initialization_coverage"]["NVDA"]["complete"])
                self.assertEqual(third["initialization_coverage"]["NVDA"]["covered_periods"], 8)
                self.assertEqual(state.status()["source_items"], {"fetched": 8})
                self.assertNotIn("CIK0001045810-submissions-002.json", FakeSec.historical_calls)
            state.close()

    def test_incremental_discovery_uses_checkpoint_overlap_and_downtime_history_ranges(self):
        class FakeSec:
            FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            user_agent = "test@example.com"; timeout = 1; max_attempts = 1
            history_calls = []
            def resolve_symbols(self, symbols):
                return {"NVDA": {"issuer_id": "cik:0001045810", "cik": "0001045810", "name": "NVIDIA", "symbol": "NVDA"}}
            @staticmethod
            def rows(entries):
                rows = {key: [] for key in ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "primaryDocument", "primaryDocDescription")}
                for accession, filed, period in entries:
                    values = (accession, filed, period, f"{filed}T12:00:00Z", "10-Q", f"{accession[-1]}.htm", "quarter")
                    for key, value in zip(rows, values): rows[key].append(value)
                return rows
            def submissions(self, cik):
                recent = self.rows([("0001045810-26-000003", "2026-11-20", "2026-10-31")])
                files = [{"name": "CIK0001045810-submissions-gap.json", "filingFrom": "2026-09-01", "filingTo": "2026-10-31"},
                         {"name": "CIK0001045810-submissions-old.json", "filingFrom": "2020-01-01", "filingTo": "2025-12-31"}]
                return {"filings": {"recent": recent, "files": files}}, {"fetched_at": "2026-12-01T00:00:00Z"}
            def submissions_file(self, name):
                self.history_calls.append(name)
                return self.rows([("0001045810-26-000002", "2026-10-01", "2026-09-30")]), {"fetched_at": "2026-12-01T00:00:00Z"}
            def filing(self, *, cik, accession, primary_document):
                return accession.encode(), {"fetched_at": "2026-12-01T00:00:00Z"}, f"https://www.sec.gov/{accession}/{primary_document}"
            def company_facts(self, cik): return {"entityName": "NVIDIA", "facts": {}}, {"fetched_at": "2026-12-01T00:00:00Z"}

        config = self.config(); config["sources"] = {"sec": {"max_attempts": 3, "overlap_days": 3, "reconcile_every_days": 7}}
        with TemporaryDirectory() as temp:
            root = Path(temp); state = EarningsState(root / "runtime/earnings/state.sqlite")
            state.set_watermark("sec", "NVDA", "2026-09-15T00:00:00Z", "success")
            state.mark_reconciled("sec", "NVDA", "2026-09-15T00:00:00Z")
            state.discover_source_item("sec_filing", "NVDA", "terminal-old", {"form": "10-Q"})
            state.fail_source_item("sec_filing", "NVDA", "terminal-old", "permanent", False, 1)
            state.record_failure("sec", "NVDA", "terminal-old", "permanent", False)
            with patch("earnings_collect.SecClient.from_config", return_value=FakeSec()):
                result = collect_live(root, state, config, "cfg", ["NVDA"], datetime(2026, 12, 1, tzinfo=timezone.utc), 10, None)
            self.assertEqual(FakeSec.history_calls, ["CIK0001045810-submissions-gap.json"])
            self.assertEqual(result["source_items"], {"fetched": 2, "terminal_failed": 1})
            self.assertEqual(state.status()["unresolved_source_failures"], 1)
            watermark = state.source_watermark("sec", "NVDA")
            self.assertEqual(watermark["watermark"], "2026-12-01T00:00:00+00:00")
            self.assertIn("reconcile_due=true", watermark["detail"])
            state.close()

    def test_fresh_incremental_discovery_is_limited_to_current_overlap(self):
        class FakeSec:
            FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            user_agent = "test@example.com"; timeout = 1; max_attempts = 1
            def resolve_symbols(self, symbols):
                return {"NVDA": {"issuer_id": "cik:0001045810", "cik": "0001045810", "name": "NVIDIA", "symbol": "NVDA"}}
            def submissions(self, cik):
                recent = {"accessionNumber": ["0001045810-26-000001", "0001045810-20-000001"],
                          "filingDate": ["2026-11-30", "2020-01-01"], "reportDate": ["2026-10-31", "2019-12-31"],
                          "acceptanceDateTime": ["2026-11-30T12:00:00Z", "2020-01-01T12:00:00Z"],
                          "form": ["10-Q", "10-K"], "primaryDocument": ["new.htm", "old.htm"],
                          "primaryDocDescription": ["quarter", "annual"]}
                return {"filings": {"recent": recent, "files": []}}, {"fetched_at": "2026-12-01T00:00:00Z"}
            def filing(self, *, cik, accession, primary_document):
                return accession.encode(), {"fetched_at": "2026-12-01T00:00:00Z"}, f"https://www.sec.gov/{accession}/{primary_document}"
            def company_facts(self, cik): return {"entityName": "NVIDIA", "facts": {}}, {"fetched_at": "2026-12-01T00:00:00Z"}

        config = self.config(); config["sources"] = {"sec": {"max_attempts": 3, "overlap_days": 3, "reconcile_every_days": 7}}
        with TemporaryDirectory() as temp:
            root = Path(temp); state = EarningsState(root / "runtime/earnings/state.sqlite")
            with patch("earnings_collect.SecClient.from_config", return_value=FakeSec()):
                result = collect_live(root, state, config, "cfg", ["NVDA"], datetime(2026, 12, 1, tzinfo=timezone.utc), 10, None)
            self.assertEqual(result["source_items"], {"fetched": 1})
            self.assertEqual(state.db.execute("SELECT COUNT(*) FROM earnings_events").fetchone()[0], 1)
            state.close()


if __name__ == "__main__":
    unittest.main()
