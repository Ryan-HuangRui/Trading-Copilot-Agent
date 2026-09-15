#!/usr/bin/env python3
"""Structural, evidence, cutoff and provenance validation for earnings role reports."""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import json
from pathlib import Path
from typing import Any

from earnings_common import ROOT, canonical_json, emit, envelope, ensure_inside, parse_time, read_json, resolve_path, sha256_bytes, sha256_file


REPORT_TYPES = {"company", "industry", "challenge", "synthesis"}
THESIS_STATES = {"emerging", "strengthening", "validating", "weakening", "invalidated", "insufficient_data"}
COMPLETENESS = {"full", "partial", "insufficient"}
EVIDENCE_KINDS = {"fact", "management_outlook", "inference"}
DIRECTIONS = {"support", "oppose", "mixed", "neutral"}
CLAIM_KINDS = {"fact", "management_outlook", "inference", "major_inference"}
PROHIBITED_KEYS = {"broker_order", "submit_order", "cancel_order", "replace_order", "watchlist_mutation", "trade_signal", "price_target"}


class _DisclosureText(HTMLParser):
    """Preserve inline text while separating table cells and block boundaries."""
    boundaries = {"p", "div", "br", "tr", "td", "th", "li", "h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.skip += 1
        if tag in self.boundaries:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        if tag in self.boundaries:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def quote_in_original(quote: str, original: str, suffix: str) -> bool:
    if quote.strip() in original:
        return True
    if suffix.lower() not in {".htm", ".html", ".xhtml"}:
        return False
    parser = _DisclosureText()
    parser.feed(original)
    normalized_quote = " ".join(quote.split())
    # HTML readers differ on inline boundaries (e.g. <span>16</span>%).
    # Accept either boundary spacing, without deleting or changing source tokens.
    return any(normalized_quote in " ".join(text.split())
               for text in ("".join(parser.parts), " ".join(parser.parts)))


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def _required(report: dict[str, Any], fields: list[str], errors: list[str]) -> None:
    for field in fields:
        if field not in report or report[field] is None or report[field] == "":
            errors.append(f"missing required field: {field}")


def _validate_period(start: str | None, end: str | None, label: str, errors: list[str]) -> None:
    try:
        parsed_start = date.fromisoformat(start) if start is not None else None
        parsed_end = date.fromisoformat(end) if end is not None else None
        if parsed_start and parsed_end and parsed_start > parsed_end:
            errors.append(f"{label}: start after end")
    except (TypeError, ValueError):
        errors.append(f"{label}: invalid ISO date")


def _validate_report_inner(report_path: Path, manifest_path: Path, root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        report = read_json(report_path)
        manifest = read_json(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"valid": False, "errors": [str(exc)], "warnings": []}
    if not isinstance(report, dict) or not isinstance(manifest, dict):
        return {"valid": False, "errors": ["report and manifest must be JSON objects"], "warnings": []}
    frozen = dict(manifest)
    declared_manifest_hash = frozen.pop("input_manifest_hash", None)
    if not declared_manifest_hash or sha256_bytes(canonical_json(frozen)) != declared_manifest_hash:
        errors.append("frozen input manifest hash mismatch")
    _required(report, ["schema_version", "report_id", "report_type", "research_mode", "task_id", "run_id", "scope", "cutoff",
                       "generated_at", "input_manifest_hash", "source_mode", "provenance", "evidence", "claims",
                       "limitations", "completeness"], errors)
    if report.get("schema_version") != 1:
        errors.append("unsupported schema_version")
    report_type = report.get("report_type")
    if report_type not in REPORT_TYPES:
        errors.append("invalid report_type")
    expected_role = manifest.get("assigned_role")
    if report_type != expected_role:
        errors.append(f"report_type {report_type!r} does not match assigned_role {expected_role!r}")
    for field in ("task_id", "run_id", "source_mode", "cutoff"):
        if report.get(field) != manifest.get(field):
            errors.append(f"{field} does not match frozen manifest")
    report_scope = report.get("scope") if isinstance(report.get("scope"), dict) else {}
    manifest_scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    if not report_scope:
        errors.append("scope must be an object")
    for field in ("issuer_id", "industry_id", "event_id", "reporting_start", "reporting_end", "universe_version", "expected_issuer_ids"):
        if field in manifest_scope and report_scope.get(field) != manifest_scope.get(field):
            errors.append(f"scope.{field} does not match frozen manifest")
    if report.get("research_mode") != manifest.get("research_mode"):
        errors.append("research_mode does not match frozen manifest")
    if report.get("input_manifest_hash") != manifest.get("input_manifest_hash"):
        errors.append("input_manifest_hash mismatch")
    if report.get("source_mode") == "fixture" and manifest.get("source_mode") != "fixture":
        errors.append("undeclared fixture evidence")
    if report.get("source_mode") not in {"live", "fixture"}:
        errors.append("invalid source_mode")
    permitted = manifest.get("permitted_outputs") if isinstance(manifest.get("permitted_outputs"), dict) else {}
    if not permitted:
        errors.append("permitted_outputs must be an object")
    for kind, path_text in permitted.items():
        try:
            roots = [root / "report" / "earnings"] if kind in {"json", "markdown"} else [root / "runtime" / "earnings"]
            ensure_inside(resolve_path(root, path_text), roots)
        except (TypeError, ValueError) as exc:
            errors.append(f"invalid permitted output {kind}: {exc}")
    profile = manifest.get("profile") if isinstance(manifest.get("profile"), dict) else {}
    provenance = report.get("provenance") if isinstance(report.get("provenance"), dict) else {}
    if not provenance:
        errors.append("provenance must be an object")
    for field in ("model", "effort"):
        if provenance.get(field) != profile.get(field):
            errors.append(f"provenance.{field} does not match runner profile")
    if provenance.get("configuration_hash") != manifest.get("configuration_hash"):
        errors.append("provenance.configuration_hash mismatch")
    if provenance.get("method_version") != manifest.get("method_version"):
        errors.append("provenance.method_version mismatch")
    if "usage" not in provenance:
        errors.append("provenance.usage must be present (null when unavailable)")
    try:
        cutoff = parse_time(str(report.get("cutoff")))
    except ValueError as exc:
        errors.append(str(exc)); cutoff = None
    manifest_documents = manifest.get("documents") if isinstance(manifest.get("documents"), list) else []
    if not isinstance(manifest.get("documents"), list):
        errors.append("manifest.documents must be an array")
    calculation_inputs = manifest.get("calculation_inputs") if isinstance(manifest.get("calculation_inputs"), list) else []
    document_map = {(d.get("document_id"), d.get("version")): d for d in manifest_documents if isinstance(d, dict)}
    for document in document_map.values():
        path_text = document.get("original_path")
        if not path_text:
            errors.append(f"document {document.get('document_id')} missing original_path")
            continue
        try:
            path = ensure_inside(resolve_path(root, path_text), [root / "raw_data" / "earnings"])
            if not path.exists():
                errors.append(f"input document missing: {path_text}")
            elif sha256_file(path) != document.get("content_sha256"):
                errors.append(f"input document hash mismatch: {path_text}")
        except ValueError as exc:
            errors.append(str(exc))
        public = document.get("accepted_at") or document.get("published_at")
        if not public:
            errors.append(f"unknown public timestamp: {document.get('document_id')}")
        elif cutoff:
            try:
                if parse_time(public) > cutoff:
                    errors.append(f"known post-cutoff input: {document.get('document_id')}")
                if document.get("public_time_precision") == "day" and parse_time(public).date() == cutoff.date():
                    errors.append(f"same-day public availability is unprovable: {document.get('document_id')}")
            except ValueError:
                errors.append(f"invalid public timestamp: {document.get('document_id')}")
        if document.get("source_mode") != report.get("source_mode"):
            errors.append(f"document source_mode mismatch: {document.get('document_id')}")
        if document.get("issuer_id") not in {manifest_scope.get("issuer_id"), *(manifest_scope.get("expected_issuer_ids") or [])}:
            errors.append(f"document issuer is outside frozen scope: {document.get('document_id')}")
    for calculation in calculation_inputs:
        if not isinstance(calculation, dict):
            errors.append("calculation input must be an object")
            continue
        try:
            calculation_path = ensure_inside(resolve_path(root, calculation.get("original_path")), [root / "raw_data" / "earnings"])
            if not calculation_path.exists() or sha256_file(calculation_path) != calculation.get("content_sha256"):
                errors.append(f"calculation input hash mismatch: {calculation.get('document_id')}")
        except (TypeError, ValueError) as exc:
            errors.append(f"invalid calculation input path: {exc}")
        if calculation.get("issuer_id") != manifest_scope.get("issuer_id") or calculation.get("source_mode") != report.get("source_mode"):
            errors.append(f"calculation input identity/source mode mismatch: {calculation.get('document_id')}")
    declared_input_hashes = provenance.get("input_document_hashes")
    if not isinstance(declared_input_hashes, list):
        errors.append("provenance.input_document_hashes must be an array")
        declared_input_hashes = []
    known_hashes = {document.get("content_sha256") for document in document_map.values()}
    known_hashes.update(row.get("content_sha256") for row in calculation_inputs if isinstance(row, dict))
    predecessor_rows = manifest.get("previous_artifacts") if isinstance(manifest.get("previous_artifacts"), list) else []
    predecessor_hashes: set[str] = set()
    for predecessor in predecessor_rows:
        if not isinstance(predecessor, dict):
            errors.append("manifest predecessor must be an object"); continue
        try:
            predecessor_path = ensure_inside(resolve_path(root, predecessor.get("path")), [root / "report" / "earnings"])
            if not predecessor_path.exists() or sha256_file(predecessor_path) != predecessor.get("sha256"):
                errors.append(f"predecessor hash mismatch: {predecessor.get('path')}")
            else:
                predecessor_hashes.add(predecessor.get("sha256"))
        except (TypeError, ValueError) as exc:
            errors.append(f"invalid predecessor path: {exc}")
    for digest in declared_input_hashes:
        if digest not in known_hashes:
            errors.append(f"provenance contains unknown input document hash: {digest}")
    if set(declared_input_hashes) != {digest for digest in known_hashes if digest}:
        errors.append("provenance.input_document_hashes must exactly match frozen input documents")
    declared_predecessors = provenance.get("predecessor_report_hashes")
    if not isinstance(declared_predecessors, list) or set(declared_predecessors) != predecessor_hashes:
        errors.append("provenance.predecessor_report_hashes must exactly match frozen predecessors")
    evidence = report.get("evidence")
    if not isinstance(evidence, list):
        errors.append("evidence must be an array"); evidence = []
    evidence_ids: set[str] = {str(item.get("evidence_id")) for item in evidence if isinstance(item, dict) and item.get("evidence_id")}
    seen_evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"evidence[{index}] must be an object"); continue
        evidence_id = item.get("evidence_id")
        if not evidence_id or evidence_id in seen_evidence_ids:
            errors.append(f"evidence[{index}] missing or duplicate evidence_id")
        else:
            seen_evidence_ids.add(evidence_id)
        if item.get("evidence_kind") not in EVIDENCE_KINDS:
            errors.append(f"evidence {evidence_id}: invalid evidence_kind")
        for field, expected_type in (("issuer_id", str), ("source_locator", str), ("short_quote", str),
                                     ("source_url", str), ("summary", str), ("limitations", list), ("numeric_facts", list),
                                     ("industry_ids", list)):
            if not isinstance(item.get(field), expected_type) or (expected_type is str and not item.get(field).strip()):
                errors.append(f"evidence {evidence_id}: invalid or missing {field}")
        if item.get("schema_version") != 1:
            errors.append(f"evidence {evidence_id}: schema_version must be 1")
        if not isinstance(item.get("document_id"), str) or not isinstance(item.get("document_version"), int):
            errors.append(f"evidence {evidence_id}: document identity/version is invalid")
        if not isinstance(item.get("document_hash"), str) or len(item.get("document_hash", "")) != 64:
            errors.append(f"evidence {evidence_id}: document hash is invalid")
        bound_issuers = set(report_scope.get("expected_issuer_ids") or [])
        if report_scope.get("issuer_id"):
            bound_issuers.add(report_scope["issuer_id"])
        if item.get("issuer_id") not in bound_issuers:
            errors.append(f"evidence {evidence_id}: issuer is outside frozen scope")
        key = (item.get("document_id"), item.get("document_version"))
        source = document_map.get(key)
        if not source:
            errors.append(f"evidence {evidence_id}: unresolved document/version")
        else:
            if item.get("issuer_id") != source.get("issuer_id"):
                errors.append(f"evidence {evidence_id}: issuer does not match source document")
            if item.get("document_hash") != source.get("content_sha256"):
                errors.append(f"evidence {evidence_id}: document hash mismatch")
            if item.get("source_url") != source.get("source_url"):
                errors.append(f"evidence {evidence_id}: source URL mismatch")
            expected_public = source.get("accepted_at") or source.get("published_at")
            if item.get("public_timestamp") != expected_public:
                errors.append(f"evidence {evidence_id}: public timestamp mismatch")
            if item.get("document_hash") not in declared_input_hashes:
                errors.append(f"evidence {evidence_id}: document hash absent from provenance inputs")
            source_path = resolve_path(root, source.get("original_path")) if source.get("original_path") else None
            quote = item.get("short_quote")
            if source_path and isinstance(quote, str) and source_path.suffix.lower() in {".txt", ".htm", ".html", ".xhtml", ".json", ".xml"}:
                try:
                    if not quote_in_original(quote, source_path.read_text(encoding="utf-8", errors="ignore"), source_path.suffix):
                        errors.append(f"evidence {evidence_id}: quote not found in original")
                except OSError as exc:
                    errors.append(f"evidence {evidence_id}: cannot inspect original quote: {exc}")
        if not item.get("source_locator"):
            errors.append(f"evidence {evidence_id}: missing source locator")
        _validate_period(item.get("reporting_start"), item.get("reporting_end"), f"evidence {evidence_id}", errors)
        numeric_rows = item.get("numeric_facts") if isinstance(item.get("numeric_facts"), list) else []
        for numeric in numeric_rows:
            if not isinstance(numeric, dict):
                errors.append(f"evidence {evidence_id}: numeric fact must be object"); continue
            if "value" not in numeric or not numeric.get("unit") or not numeric.get("period"):
                errors.append(f"evidence {evidence_id}: numeric fact missing value/unit/period")
            for field in ("metric", "accounting_basis", "derivation"):
                if not isinstance(numeric.get(field), str) or not numeric.get(field).strip():
                    errors.append(f"evidence {evidence_id}: numeric fact missing {field}")
            if not isinstance(numeric.get("source_evidence_ids"), list):
                errors.append(f"evidence {evidence_id}: numeric source_evidence_ids must be an array")
            period = numeric.get("period") or {}
            if not isinstance(period, dict) or period.get("kind") not in {"duration", "instant"}:
                errors.append(f"evidence {evidence_id}: numeric period kind invalid")
            _validate_period(period.get("start"), period.get("end"), f"evidence {evidence_id} numeric period", errors)
            if period.get("kind") == "instant" and period.get("start"):
                errors.append(f"evidence {evidence_id}: instant numeric period cannot have start")
            if numeric.get("value") is not None:
                try:
                    if not Decimal(str(numeric.get("value"))).is_finite():
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    errors.append(f"evidence {evidence_id}: numeric value must be finite")
            if numeric.get("currency") and str(numeric.get("unit", "")).upper() in {"SHARES", "%"}:
                errors.append(f"evidence {evidence_id}: impossible currency/unit combination")
            for source_id in numeric.get("source_evidence_ids") or []:
                if source_id not in evidence_ids and source_id != evidence_id:
                    errors.append(f"evidence {evidence_id}: unresolved numeric source evidence {source_id}")
    claims = report.get("claims")
    claim_ids: set[str] = set()
    if not isinstance(claims, list):
        errors.append("claims must be an array"); claims = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"claims[{index}] must be an object"); continue
        claim_id = claim.get("claim_id")
        if not claim_id or claim_id in claim_ids:
            errors.append(f"claims[{index}] missing or duplicate claim_id")
        else:
            claim_ids.add(claim_id)
        if claim.get("direction") not in DIRECTIONS:
            errors.append(f"claim {claim_id}: invalid direction")
        if claim.get("kind") not in CLAIM_KINDS:
            errors.append(f"claim {claim_id}: invalid kind")
        for field, expected_type in (("statement", str), ("limitations", list)):
            if not isinstance(claim.get(field), expected_type) or (expected_type is str and not claim.get(field).strip()):
                errors.append(f"claim {claim_id}: invalid or missing {field}")
        refs = claim.get("evidence_ids")
        if not isinstance(refs, list):
            errors.append(f"claim {claim_id}: evidence_ids must be array"); refs = []
        for evidence_id in refs:
            if evidence_id not in evidence_ids:
                errors.append(f"claim {claim_id}: unresolved evidence {evidence_id}")
        if claim.get("kind") in CLAIM_KINDS and not refs:
            errors.append(f"claim {claim_id}: claim has no evidence")
        if claim.get("kind") == "fact" and any((item.get("evidence_kind") == "inference") for item in evidence if item.get("evidence_id") in refs):
            errors.append(f"claim {claim_id}: inference is presented as fact")
        if not claim.get("alternative_explanation"):
            errors.append(f"claim {claim_id}: missing alternative_explanation")
    completeness = report.get("completeness") if isinstance(report.get("completeness"), dict) else {}
    if not isinstance(completeness, dict) or completeness.get("status") not in COMPLETENESS:
        errors.append("invalid completeness.status")
    if report_type in {"company", "industry", "synthesis"}:
        if report.get("thesis_state") not in THESIS_STATES:
            errors.append("invalid thesis_state")
        _required(report, ["change_summary", "invalidation_conditions", "next_checks", "coverage"], errors)
        coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
        required_counts = ("expected_issuers", "disclosed_issuers", "fetched_issuers", "researched_issuers")
        for field in required_counts:
            if not isinstance(coverage.get(field), int) or coverage.get(field, -1) < 0:
                errors.append(f"coverage.{field} must be a nonnegative integer")
        if all(isinstance(coverage.get(field), int) for field in required_counts):
            if not (coverage["expected_issuers"] >= coverage["disclosed_issuers"] >= coverage["fetched_issuers"] >= coverage["researched_issuers"]):
                errors.append("coverage counts do not reconcile")
            frozen = (report.get("scope") or {}).get("expected_issuer_ids")
            if isinstance(frozen, list) and len(frozen) != coverage["expected_issuers"]:
                errors.append("coverage denominator differs from frozen expected issuer sample")
        if coverage.get("key_missing_issuers") and completeness.get("status") == "full":
            errors.append("full completeness is impossible with key missing issuers")
        frozen_counts = (manifest.get("coverage_audit") or {}).get("counts") if isinstance(manifest.get("coverage_audit"), dict) else None
        if frozen_counts is not None and coverage != frozen_counts:
            errors.append("coverage must exactly match deterministic frozen coverage")
        maturity = (manifest.get("coverage_audit") or {}).get("maturity") if isinstance(manifest.get("coverage_audit"), dict) else None
        if report_type in {"industry", "synthesis"} and report.get("research_mode") == "quarterly" and completeness.get("status") == "full":
            if not isinstance(maturity, dict) or not maturity.get("eligible_full"):
                errors.append("full quarterly report does not meet deterministic maturity requirements")
    if report_type == "challenge":
        findings = report.get("findings")
        if not isinstance(findings, list):
            errors.append("challenge findings must be an array")
        else:
            finding_ids: set[str] = set()
            for finding in findings:
                if not isinstance(finding, dict):
                    errors.append("challenge finding must be an object"); continue
                finding_id = finding.get("finding_id")
                if not isinstance(finding_id, str) or not finding_id or finding_id in finding_ids:
                    errors.append("challenge finding_id must be nonempty and unique")
                else:
                    finding_ids.add(finding_id)
                if finding.get("materiality") not in {"high", "material", "medium", "low"}:
                    errors.append(f"finding {finding_id}: invalid materiality")
                for field in ("competing_explanation", "requested_check"):
                    if not isinstance(finding.get(field), str) or not finding[field].strip():
                        errors.append(f"finding {finding_id}: missing {field}")
                if not isinstance(finding.get("evidence_ids"), list):
                    errors.append(f"finding {finding_id}: evidence_ids must be an array")
                if finding.get("disputed_claim_id") and finding["disputed_claim_id"] not in claim_ids:
                    # The disputed claim commonly belongs to the predecessor draft, not the challenge's own claims.
                    predecessor_claims = set(manifest.get("predecessor_claim_ids") or [])
                    if finding["disputed_claim_id"] not in predecessor_claims:
                        errors.append(f"finding {finding.get('finding_id')}: unresolved disputed claim")
                for evidence_id in finding.get("evidence_ids") or []:
                    if evidence_id not in evidence_ids:
                        errors.append(f"finding {finding.get('finding_id')}: unresolved evidence {evidence_id}")
    if report_type == "synthesis":
        material_ids = set(manifest.get("material_challenge_finding_ids") or [])
        dispositions = report.get("challenge_dispositions")
        if not isinstance(dispositions, list):
            errors.append("synthesis challenge_dispositions must be an array")
        else:
            by_id = {item.get("finding_id"): item for item in dispositions if isinstance(item, dict)}
            for item in dispositions:
                if isinstance(item, dict):
                    for evidence_id in item.get("supporting_evidence_ids") or []:
                        if evidence_id not in evidence_ids:
                            errors.append(f"challenge disposition: unresolved evidence {evidence_id}")
            for finding_id in material_ids:
                item = by_id.get(finding_id)
                if not item or item.get("disposition") not in {"accepted", "rejected", "unresolved"}:
                    errors.append(f"missing material challenge disposition: {finding_id}")
                elif item.get("disposition") == "rejected" and not item.get("supporting_evidence_ids"):
                    errors.append(f"rejected challenge requires supporting evidence: {finding_id}")
    prohibited = sorted(_walk_keys(report) & PROHIBITED_KEYS)
    if prohibited:
        errors.append(f"prohibited trading/broker fields: {', '.join(prohibited)}")
    if completeness.get("status") == "full" and (not evidence or completeness.get("missing_inputs")):
        errors.append("full completeness requires evidence and no missing inputs")
    source_types = {doc.get("source_type") for doc in manifest_documents if isinstance(doc, dict)}
    if "issuer_call_transcript" not in source_types:
        warnings.append("optional issuer call transcript unavailable")
    if completeness.get("status") != "full":
        warnings.append("research inputs are incomplete; conclusion strength must remain separate")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "semantic_verification": False,
            "report_type": report_type, "report_id": report.get("report_id"), "task_id": report.get("task_id")}


def validate_report(report_path: Path, manifest_path: Path, root: Path) -> dict[str, Any]:
    try:
        report_path = ensure_inside(report_path, [root / "report" / "earnings"])
        manifest_path = ensure_inside(manifest_path, [root / "runtime" / "earnings" / "runs"])
    except (TypeError, ValueError) as exc:
        return {"valid": False, "errors": [f"report/manifest path confinement failed: {exc}"], "warnings": []}
    try:
        return _validate_report_inner(report_path, manifest_path, root)
    except Exception as exc:
        return {"valid": False, "errors": [f"invalid report or manifest type/value: {exc}"], "warnings": []}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate an earnings research role artifact")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--report", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--date")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = Path(args.repo_root).resolve()
    report = resolve_path(root, args.report)
    manifest = resolve_path(root, args.manifest)
    result = validate_report(report, manifest, root)
    payload = envelope("validate-earnings-research", args.date, validation=result)
    payload["artifacts"] = [str(report), str(manifest)]
    if not result["valid"]:
        payload.update(status="failed", reason="earnings research validation failed")
        emit(payload, 1)
    emit(payload)


if __name__ == "__main__":
    main()
