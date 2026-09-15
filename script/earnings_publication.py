#!/usr/bin/env python3
"""Build immutable reader publications and apply deterministic acceptance gates."""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import html
import json
from pathlib import Path
import re
from typing import Any

from earnings_common import ROOT, atomic_write_json, ensure_inside, read_json, sha256_file, stable_id, utc_now


REQUIRED_SECTIONS = {
    "company": ("一分钟读完", "先看懂这门生意", "本季度关键变化", "增长留下多少钱", "潜在线索与市场预期", "最强反证与风险", "三种情景与下一次验证", "来源"),
    "ipo": ("一分钟读完", "先看懂这门生意", "客户与履约", "现金与融资用途", "稀释与解禁", "最强反证与风险", "三种情景与下一次验证", "来源"),
    "industry": ("一页结论", "行业怎样运转", "全样本比较", "本季经营变化", "值得验证的机会", "最强反证与风险", "下一季度验证", "来源"),
    "market": ("一页总览", "行业比较", "资金支出与利润传导", "优先研究顺序", "证据不足或已被否定", "下一季度验证", "来源"),
}


def _fold(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


_UNIT = {
    "%": ("ratio", Decimal("0.01")), "％": ("ratio", Decimal("0.01")),
    "percent": ("ratio", Decimal("0.01")), "percent-upper-bound": ("ratio", Decimal("0.01")),
    "bps": ("ratio", Decimal("0.0001")), "个基点": ("ratio", Decimal("0.0001")),
    "percentage points": ("ratio", Decimal("0.01")), "个百分点": ("ratio", Decimal("0.01")),
    "USD": ("USD", Decimal("1")), "美元": ("USD", Decimal("1")),
    "USD million": ("USD", Decimal("1000000")), "million USD": ("USD", Decimal("1000000")),
    "百万美元": ("USD", Decimal("1000000")),
    "USD billion": ("USD", Decimal("1000000000")), "billion USD": ("USD", Decimal("1000000000")),
    "亿美元": ("USD", Decimal("100000000")),
    "CNY": ("CNY", Decimal("1")), "元": ("CNY", Decimal("1")),
    "百万元": ("CNY", Decimal("1000000")), "亿元": ("CNY", Decimal("100000000")),
}


def _unit(value: str) -> tuple[str, Decimal] | None:
    return _UNIT.get(re.sub(r"\s+", " ", (value or "").strip()))


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _display_candidates(value: str, unit: str, *, rounded: bool = False) -> list[dict[str, str]]:
    source = _unit(unit)
    if not source:
        return [{"value": value, "unit": unit}]
    targets = {"USD": ("USD", "USD million", "USD billion", "亿美元"),
               "CNY": ("CNY", "百万元", "亿元"),
               "ratio": (unit,)}[source[0]]
    base = Decimal(value) * source[1]
    rows: list[dict[str, str]] = []
    for target in targets:
        converted = base / _unit(target)[1]
        values = [converted]
        if rounded:
            values.extend(converted.quantize(Decimal(step), rounding=ROUND_HALF_UP) for step in ("1", "0.1", "0.01"))
        for candidate in values:
            row = {"value": _decimal_text(candidate), "unit": target}
            if row not in rows:
                rows.append(row)
    return rows


def _period_key(period: Any) -> str:
    return json.dumps(period or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _basis(value: Any) -> str:
    return str(value or "unspecified").strip().lower()


def _duration_days(period: dict[str, Any]) -> int | None:
    try:
        return (date.fromisoformat(period["end"][:10]) - date.fromisoformat(period["start"][:10])).days
    except (KeyError, TypeError, ValueError):
        return None


def _comparable_periods(newer: dict[str, Any], older: dict[str, Any]) -> bool:
    if newer.get("kind") != older.get("kind") or not newer.get("end") or not older.get("end"):
        return False
    if str(newer["end"]) <= str(older["end"]):
        return False
    if newer.get("kind") == "instant":
        return True
    newer_days, older_days = _duration_days(newer), _duration_days(older)
    return newer_days is not None and older_days is not None and abs(newer_days - older_days) <= 3


def _issuer_id(report: dict[str, Any], evidence: dict[str, Any], report_index: int) -> str:
    scope = report.get("scope") or {}
    return str(evidence.get("issuer_id") or report.get("issuer_id") or scope.get("issuer_id")
               or scope.get("cik") or scope.get("symbol") or report.get("report_id") or f"report-{report_index}")


def financial_fact_catalog(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the sole program-authorized catalog for displayed financial quantities.

    Derived candidates are deliberately narrow: the program only compares the same
    metric, basis, currency/unit family, and segment across comparable periods.  It
    never searches arbitrary pairs merely because their arithmetic happens to fit.
    """
    facts: list[dict[str, Any]] = []
    for report_index, report in enumerate(reports):
        for evidence in report.get("evidence", []):
            for fact_index, fact in enumerate(evidence.get("numeric_facts", [])):
                value = fact.get("value")
                if isinstance(value, bool) or value is None: continue
                try: decimal_value = Decimal(str(value).replace(",", ""))
                except InvalidOperation: continue
                normalized = {**fact, "value": format(decimal_value, "f"),
                    "issuer_id": _issuer_id(report, evidence, report_index),
                    "evidence_id": evidence.get("evidence_id"), "source_url": evidence.get("source_url"),
                    "source_locator": fact.get("source_locator") or evidence.get("source_locator"),
                    "segment": fact.get("segment") or fact.get("dimensions") or evidence.get("segment")}
                normalized["source_evidence_ids"] = list(dict.fromkeys(
                    fact.get("source_evidence_ids") or [evidence.get("evidence_id")]))
                normalized_unit = _unit(str(normalized.get("unit") or ""))
                normalized["currency"] = normalized.get("currency") or (normalized_unit[0] if normalized_unit else None)
                identity = [normalized["issuer_id"], report.get("report_id") or report_index,
                    evidence.get("evidence_id"), fact_index,
                    normalized.get("metric"), normalized["value"], normalized.get("unit"),
                    normalized.get("currency"), _period_key(normalized.get("period")),
                    _basis(normalized.get("accounting_basis")), normalized.get("segment"),
                    normalized.get("source_locator")]
                normalized["fact_id"] = stable_id("financial-fact", *identity)
                normalized["accounting_basis"] = normalized.get("accounting_basis") or "unspecified"
                normalized["display_candidates"] = _display_candidates(normalized["value"], str(normalized.get("unit") or ""))
                facts.append(normalized)
    derivations: list[dict[str, Any]] = []
    for newer in facts:
        newer_unit = _unit(str(newer.get("unit") or ""))
        if not newer_unit:
            continue
        for older in facts:
            older_unit = _unit(str(older.get("unit") or ""))
            if (newer is older or not older_unit or newer.get("metric") != older.get("metric")
                    or newer.get("issuer_id") != older.get("issuer_id")
                    or newer.get("segment") != older.get("segment")
                    or _basis(newer.get("accounting_basis")) != _basis(older.get("accounting_basis"))
                    or newer.get("currency") != older.get("currency")
                    or newer_unit[0] != older_unit[0]
                    or not _comparable_periods(newer.get("period") or {}, older.get("period") or {})):
                continue
            current = Decimal(newer["value"]) * newer_unit[1]
            prior = Decimal(older["value"]) * older_unit[1]
            common = {"issuer_id": newer.get("issuer_id"), "metric": newer.get("metric"), "period": newer.get("period"),
                "comparison_period": older.get("period"), "accounting_basis": newer.get("accounting_basis"),
                "currency": newer.get("currency") or newer_unit[0],
                "input_fact_ids": [newer["fact_id"], older["fact_id"]],
                "source_evidence_ids": list(dict.fromkeys([newer.get("evidence_id"), older.get("evidence_id")])),
                "source_locators": list(dict.fromkeys([newer.get("source_locator"), older.get("source_locator")])),
                "segment": newer.get("segment")}
            difference_unit = "percentage points" if newer_unit[0] == "ratio" else newer_unit[0]
            difference_value = (current - prior) / (_unit(difference_unit) or ("", Decimal(1)))[1]
            for operation, output_value, output_unit in (
                    ("difference", difference_value, difference_unit),
                    *(([("growth_rate", ((current / prior) - 1) * 100, "%")]) if prior != 0 else [])):
                candidate = {**common, "operation": operation, "value": format(output_value, "f"), "unit": output_unit}
                candidate["derivation_id"] = stable_id("financial-derivation", operation, *candidate["input_fact_ids"])
                candidate["display_candidates"] = _display_candidates(candidate["value"], candidate["unit"], rounded=True)
                derivations.append(candidate)
    for numerator in facts:
        relationship = numerator.get("share_relationship")
        denominator_metric = relationship.get("denominator_metric") if isinstance(relationship, dict) else None
        total_dimension = relationship.get("total_dimension") if isinstance(relationship, dict) else None
        numerator_unit = _unit(str(numerator.get("unit") or ""))
        if (not denominator_metric or not total_dimension or not numerator_unit
                or numerator_unit[0] not in {"USD", "CNY"}):
            continue
        denominators = [row for row in facts if row.get("metric") == denominator_metric
            and row.get("issuer_id") == numerator.get("issuer_id")
            and row.get("period") == numerator.get("period")
            and _basis(row.get("accounting_basis")) == _basis(numerator.get("accounting_basis"))
            and row.get("currency") == numerator.get("currency")
            and row.get("is_total") is True and row.get("total_dimension") == total_dimension
            and (_unit(str(row.get("unit") or "")) or (None,))[0] == numerator_unit[0]]
        if len(denominators) != 1:
            continue
        denominator = denominators[0]; denominator_unit = _unit(str(denominator.get("unit") or ""))
        denominator_value = Decimal(denominator["value"]) * denominator_unit[1]
        if denominator_value == 0:
            continue
        candidate = {"operation": "share", "issuer_id": numerator.get("issuer_id"),
            "metric": numerator.get("metric"), "denominator_metric": denominator_metric,
            "total_dimension": total_dimension, "period": numerator.get("period"),
            "comparison_period": None, "accounting_basis": numerator.get("accounting_basis"),
            "currency": "ratio", "input_fact_ids": [numerator["fact_id"], denominator["fact_id"]],
            "source_evidence_ids": list(dict.fromkeys(numerator.get("source_evidence_ids", [])
                                                      + denominator.get("source_evidence_ids", []))),
            "source_locators": list(dict.fromkeys([numerator.get("source_locator"), denominator.get("source_locator")])),
            "segment": numerator.get("segment"),
            "value": format((Decimal(numerator["value"]) * numerator_unit[1] / denominator_value) * 100, "f"),
            "unit": "%"}
        candidate["derivation_id"] = stable_id("financial-derivation", "share", *candidate["input_fact_ids"])
        candidate["display_candidates"] = _display_candidates(candidate["value"], candidate["unit"], rounded=True)
        derivations.append(candidate)
    return {"schema_version": 1, "facts": facts,
            "derivations": sorted(derivations, key=lambda row: row["derivation_id"]),
            "policy": {"operations": ["growth_rate", "difference", "share"],
                       "same_issuer_required": True,
                       "share_requires_source_declared_relationship_and_total_dimension": True}}


def _facts(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return financial_fact_catalog(reports)["facts"]


def _claims(markdown: str) -> list[dict[str, Any]]:
    # Coordinates always address the exact UTF-8-decoded Markdown string supplied to
    # the checker. Numeric normalization happens only in value, never in the text used
    # for offsets, so thousands separators cannot shift later occurrences.
    clean = markdown
    number = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    # Python's Unicode \w treats a preceding Chinese character as a word character,
    # which hid ordinary forms such as “收入962.21亿美元”. Only an immediately
    # preceding ASCII digit/dot can make this the middle of another numeric token.
    pattern = rf"(?<![0-9.])({number})\s*(USD\s+(?:million|billion)|million\s+USD|billion\s+USD|percentage\s+points|百万美元|亿美元|百万元|亿元|个百分点|个基点|美元|元|%|％|bps)"
    rows = []
    global_period = None
    period_match = re.search(r"经营期间[^\n]*?(20\d{2}-\d{2}-\d{2})\s*(?:至|—|-)[^\n]*?(20\d{2}-\d{2}-\d{2})", clean)
    if period_match: global_period = {"kind": "duration", "start": period_match.group(1), "end": period_match.group(2)}
    for match in re.finditer(pattern, clean, re.I):
        context = clean[max(0, match.start() - 40):match.end() + 15]
        basis = "non-GAAP" if re.search(r"non[- ]?GAAP|非GAAP", context, re.I) else ("GAAP" if re.search(r"\bGAAP\b", context, re.I) else None)
        local_end = re.search(r"截至\s*(20\d{2}-\d{2}-\d{2})", context)
        period = {"kind": "instant", "start": None, "end": local_end.group(1)} if local_end else global_period
        value = match.group(1).replace(",", "")
        rows.append({"value": value, "unit": match.group(2), "display": match.group(0).strip(),
                     "decimals": len(value.split(".", 1)[1]) if "." in value else 0,
                     "accounting_basis": basis, "period": period, "occurrence": {"start": match.start(), "end": match.end()}})
    lines = clean.splitlines()
    header: list[str] = []
    for index, line in enumerate(lines):
        if not (line.startswith("|") and line.endswith("|")): continue
        if index + 1 < len(lines) and re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", lines[index + 1]):
            header = [c.strip() for c in line.strip("|").split("|")]
            continue
        if re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", line): continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        for column, cell in enumerate(cells):
            if re.fullmatch(number, cell):
                heading = header[column] if column < len(header) else ""
                found = next((name for name in sorted(_UNIT, key=len, reverse=True) if name in heading), None)
                value = cell.replace(",", "")
                rows.append({"value": value, "unit": found, "display": cell, "table": True,
                             "decimals": len(value.split(".", 1)[1]) if "." in value else 0,
                             "period": global_period, "occurrence": {"line": index + 1, "column": column + 1}})
    return rows


def claim_occurrence_inventory(markdown: str) -> dict[str, Any]:
    """Expose deterministic claim locations without asking a model to count offsets."""
    return {"coordinate_contract": "python-string-codepoint-offsets-v1; tables use one-based line/column",
            "claims": _claims(markdown)}


def _markdown_urls(text: str) -> set[str]:
    return set(re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", text))


def _sentence_at(text: str, start: int, end: int) -> str:
    left = max(text.rfind(mark, 0, start) for mark in ("。", "！", "？", "\n")) + 1
    stops = [index for mark in ("。", "！", "？", "\n") if (index := text.find(mark, end)) >= 0]
    right = min(stops) + 1 if stops else len(text)
    return text[left:right]


def _explicitly_negates_certainty(sentence: str, risky: str) -> bool:
    term = re.escape(risky)
    before = rf"(?:不能判断|无法判断|无法形成|无从判断|不作|不提供|不给出|不形成|不构成)[^。！？\n]{{0,80}}{term}"
    after = rf"{term}[^。！？\n]{{0,40}}(?:未知|不能判断|无法判断|不作判断|不提供|不形成|不构成)"
    return bool(re.search(before, sentence) or re.search(after, sentence))


def validate_reader_markdown(markdown: str, reports: list[dict[str, Any]], *, publication_type: str = "company",
                             explicit_fact_bindings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    errors: list[str] = []; warnings: list[str] = []
    required = REQUIRED_SECTIONS.get(publication_type)
    if required is None:
        raise ValueError("unsupported publication type")
    headings = {_fold(value) for value in re.findall(r"^##\s+(.+?)\s*$", markdown, re.MULTILINE)}
    for section in required:
        if _fold(section) not in headings:
            errors.append(f"missing required section: {section}")
    allowed_dates_and_values = {str(v)[:10] for report in reports for v in
        [report.get("cutoff"), (report.get("scope") or {}).get("reporting_start"), (report.get("scope") or {}).get("reporting_end")] if v}
    for fact in _facts(reports):
        period = fact.get("period") or {}
        allowed_dates_and_values.update(filter(None, (period.get("start"), period.get("end"))))
    for date_text in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", markdown):
        if date_text not in allowed_dates_and_values:
            errors.append(f"unmapped period/date: {date_text}")
    # Reader prose may contain ordinal/count numbers, but every number carrying a financial unit must map to evidence.
    numeric_claims = _claims(markdown); mappings = []
    catalog = financial_fact_catalog(reports); facts = catalog["facts"]
    facts_by_id = {row["fact_id"]: row for row in facts}
    derivations_by_id = {row["derivation_id"]: row for row in catalog["derivations"]}
    for claim in numeric_claims:
        explicit = next((row for row in (explicit_fact_bindings or [])
                         if row.get("occurrence") == claim.get("occurrence") and row.get("display") == claim.get("display")), None)
        claim_unit = _unit(claim.get("unit") or "")
        if claim_unit is None:
            errors.append(f"unmapped table quantity without unit: {claim['display']}")
            continue
        def quantity_matches(row: dict[str, Any]) -> bool:
            row_unit = _unit(str(row.get("unit") or ""))
            if not row_unit or row_unit[0] != claim_unit[0]:
                return False
            if explicit_fact_bindings is not None:
                actual = Decimal(str(claim["value"])) * claim_unit[1]
                return any(_unit(candidate["unit"])
                    and Decimal(candidate["value"]) * _unit(candidate["unit"])[1] == actual
                    for candidate in row.get("display_candidates", []))
            expected = Decimal(str(row["value"])) * row_unit[1]
            actual = Decimal(str(claim["value"])) * claim_unit[1]
            tolerance = max(abs(expected) * Decimal("0.0005"),
                claim_unit[1] * Decimal("0.5") * (Decimal(10) ** -claim.get("decimals", 0)))
            return abs(expected - actual) <= tolerance

        matches: list[dict[str, Any]] = []
        derived = None
        if explicit_fact_bindings is not None:
            if explicit is None:
                errors.append(f"missing explicit catalog binding: {claim['display']}")
                continue
            fact_id, derivation_id = explicit.get("fact_id"), explicit.get("derivation_id")
            if bool(fact_id) == bool(derivation_id):
                errors.append(f"binding must select exactly one catalog fact or derivation: {claim['display']}")
                continue
            if fact_id:
                fact = facts_by_id.get(fact_id)
                if fact is None:
                    errors.append(f"binding references unknown fact_id: {claim['display']}")
                    continue
                if not quantity_matches(fact):
                    errors.append(f"catalog fact does not match displayed quantity: {claim['display']}")
                    continue
                if claim.get("period") and claim.get("period") != fact.get("period"):
                    errors.append(f"displayed period differs from catalog fact: {claim['display']}")
                    continue
                matches = [fact]
            else:
                candidate = derivations_by_id.get(derivation_id)
                if candidate is None:
                    errors.append(f"binding references unknown derivation_id: {claim['display']}")
                    continue
                if (explicit.get("operation") != candidate.get("operation")
                        or explicit.get("input_fact_ids") != candidate.get("input_fact_ids")):
                    errors.append(f"derivation binding changed operation or source facts: {claim['display']}")
                    continue
                if not quantity_matches(candidate):
                    errors.append(f"catalog derivation does not match displayed quantity: {claim['display']}")
                    continue
                if claim.get("period") and claim.get("period") != candidate.get("period"):
                    errors.append(f"displayed period differs from catalog derivation: {claim['display']}")
                    continue
                derived = candidate
        else:
            for fact in facts:
                fact_unit = _unit(str(fact.get("unit") or ""))
                if not fact_unit or fact_unit[0] != claim_unit[0]: continue
                fact_basis = str(fact.get("accounting_basis") or "").lower()
                claim_basis = str(claim.get("accounting_basis") or "").lower()
                if claim_basis and not (claim_basis == fact_basis or (claim_basis == "gaap" and fact_basis == "us-gaap")): continue
                if claim.get("period") and fact.get("period") != claim.get("period"): continue
                if quantity_matches(fact): matches.append(fact)
        if explicit and matches:
            fact = matches[0]
            if (explicit.get("metric") != fact.get("metric") or explicit.get("period") != fact.get("period")
                    or _basis(explicit.get("accounting_basis")) != _basis(fact.get("accounting_basis"))):
                errors.append(f"fact binding metadata differs from catalog: {claim['display']}")
                continue
        if not matches and derived is None:
            errors.append(f"unmapped numeric claim (value/unit/currency): {claim['display']}")
        elif len(matches) > 1:
            errors.append(f"ambiguous numeric claim requires explicit evidence binding: {claim['display']}")
        elif derived is not None:
            mappings.append({"display": claim["display"], "occurrence": claim.get("occurrence"),
                "derivation_id": derived["derivation_id"], "operation": derived["operation"],
                "input_fact_ids": derived["input_fact_ids"], "metric": derived.get("metric"),
                "period": derived.get("period"), "comparison_period": derived.get("comparison_period"),
                "accounting_basis": derived.get("accounting_basis"), "currency": derived.get("currency"),
                "source_unit": derived.get("unit"), "source_evidence_ids": derived.get("source_evidence_ids", [])})
        else:
            fact = matches[0]; mappings.append({"display": claim["display"], "occurrence": claim.get("occurrence"),
                "fact_id": fact["fact_id"], "evidence_id": fact.get("evidence_id"), "metric": fact.get("metric"),
                "period": fact.get("period"), "accounting_basis": fact.get("accounting_basis"),
                "currency": fact.get("currency") or claim_unit[0], "source_unit": fact.get("unit"),
                "source_locator": fact.get("source_locator"), "source_evidence_ids": fact.get("source_evidence_ids", [])})
    source_urls = {e.get("source_url") for report in reports for e in report.get("evidence", []) if e.get("source_url")}
    for url in _markdown_urls(markdown):
        if url not in source_urls:
            errors.append(f"unmapped source link: {url}")
    if source_urls and not (_markdown_urls(markdown) & source_urls):
        errors.append("no source link from frozen research appears in publication")
    limitations = [str(v) for report in reports for v in report.get("limitations", [])]
    missing_consensus = any("一致预期" in value or "consensus" in value.lower() for value in limitations)
    if missing_consensus:
        for risky in ("超市场预期", "超预期", "被低估", "目标价"):
            for match in re.finditer(risky, markdown):
                sentence = _sentence_at(markdown, match.start(), match.end())
                if not _explicitly_negates_certainty(sentence, risky):
                    errors.append(f"unsupported market-expectation certainty: {risky}")
    if publication_type in {"company", "industry"}:
        alternatives = [str(claim.get("alternative_explanation") or "") for report in reports for claim in report.get("claims", [])]
        if alternatives and not any(_fold(value)[:8] in _fold(markdown) for value in alternatives if value):
            warnings.append("alternative explanation is paraphrased; semantic checker must confirm retention")
    if len(markdown) < 300:
        errors.append("reader publication is too short to satisfy the approved structure")
    return {"schema_version": 1, "status": "failed" if errors else "passed", "errors": sorted(set(errors)),
            "warnings": sorted(set(warnings)), "checked_at": utc_now(),
            "checks": {"sections": len(required), "source_links": len(_markdown_urls(markdown)),
                       "numeric_claims": len(numeric_claims)}, "fact_mappings": mappings}


def _inline(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" rel="noopener">\1</a>', escaped)


def markdown_to_html(markdown: str, title: str) -> str:
    lines = markdown.splitlines(); body: list[str] = []; in_list = False; in_table = False
    for index, line in enumerate(lines):
        if line.startswith("| ") and line.endswith("|"):
            if index + 1 < len(lines) and re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", lines[index + 1]):
                if in_list: body.append("</ul>"); in_list = False
                body.append("<table><thead><tr>" + "".join(f"<th>{_inline(c.strip())}</th>" for c in line.strip("|").split("|")) + "</tr></thead><tbody>")
                in_table = True
                continue
            if re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", line):
                continue
            if in_table:
                body.append("<tr>" + "".join(f"<td>{_inline(c.strip())}</td>" for c in line.strip("|").split("|")) + "</tr>")
                continue
        elif in_table:
            body.append("</tbody></table>"); in_table = False
        if line.startswith("# "): body.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("## "): body.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("### "): body.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("- "):
            if not in_list: body.append("<ul>"); in_list = True
            body.append(f"<li>{_inline(line[2:])}</li>")
        elif line.strip():
            if in_list: body.append("</ul>"); in_list = False
            body.append(f"<p>{_inline(line)}</p>")
    if in_list: body.append("</ul>")
    if in_table: body.append("</tbody></table>")
    css = "body{font:17px/1.7 system-ui;margin:auto;max-width:820px;padding:24px;color:#18212f}h1,h2{line-height:1.3}table{border-collapse:collapse;width:100%;overflow:auto}th,td{border:1px solid #ccd3dc;padding:8px;text-align:left}a{color:#1769aa}"
    return f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(title)}</title><style>{css}</style></head><body>{"".join(body)}</body></html>\n'


def _existing_manifests(base: Path) -> list[Path]:
    return sorted(base.glob("v*/publication-manifest.json"), key=lambda p: int(p.parent.name[1:]))


def build_publication(root: Path, publication_type: str, scope_id: str, quarter_id: str,
                      source_paths: list[Path], markdown: str, *, semantic_checker: dict[str, Any] | None = None,
                      edition: str = "full", title: str | None = None,
                      input_manifest_hash: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    if publication_type not in REQUIRED_SECTIONS or edition not in {"full", "stage", "revision"}:
        raise ValueError("invalid publication type or edition")
    sources = []
    for value in source_paths:
        path = ensure_inside(value.resolve(), [root / "report/earnings"])
        sources.append({"path": str(path.relative_to(root)), "sha256": sha256_file(path), "report": read_json(path)})
    checker = validate_reader_markdown(markdown, [row["report"] for row in sources], publication_type=publication_type,
        explicit_fact_bindings=semantic_checker.get("fact_bindings") if semantic_checker else None)
    source_hashes = [row["sha256"] for row in sources]
    draft_hash = hashlib.sha256(markdown.encode()).hexdigest()
    if semantic_checker is not None:
        if semantic_checker.get("status") != "passed" or semantic_checker.get("errors"):
            checker["errors"].append("semantic checker rejected publication")
        if semantic_checker.get("draft_sha256") != draft_hash:
            checker["errors"].append("semantic checker is not bound to this draft")
        if semantic_checker.get("source_sha256s") != source_hashes:
            checker["errors"].append("semantic checker is not bound to frozen sources")
        if not input_manifest_hash or semantic_checker.get("input_manifest_hash") != input_manifest_hash:
            checker["errors"].append("semantic checker is not bound to its input manifest")
        if not isinstance(semantic_checker.get("source_mapping"), list) or not semantic_checker["source_mapping"]:
            checker["errors"].append("semantic checker source mapping is missing")
        else:
            valid_claims = {claim.get("claim_id") for row in sources for claim in row["report"].get("claims", [])}
            valid_evidence = {evidence.get("evidence_id") for row in sources for evidence in row["report"].get("evidence", [])}
            for mapping in semantic_checker["source_mapping"]:
                if (not isinstance(mapping, dict)
                        or not mapping.get("evidence_ids")
                        or not set(mapping.get("claim_ids", [])) <= valid_claims
                        or not set(mapping.get("evidence_ids", [])) <= valid_evidence):
                    checker["errors"].append("semantic checker source mapping references unknown evidence")
                    break
            mapped_evidence = {evidence_id for mapping in semantic_checker["source_mapping"] for evidence_id in mapping.get("evidence_ids", [])}
            required_evidence = {evidence_id for row in checker.get("fact_mappings", [])
                                 for evidence_id in ([row.get("evidence_id")] + row.get("source_evidence_ids", []))
                                 if evidence_id}
            if not required_evidence <= mapped_evidence:
                checker["errors"].append("semantic checker source mapping omits numeric evidence")
        expected_bindings = checker.get("fact_mappings", [])
        supplied_bindings = semantic_checker.get("fact_bindings")
        keys = ("display", "occurrence", "fact_id", "derivation_id", "operation", "input_fact_ids",
                "evidence_id", "metric", "period", "comparison_period", "accounting_basis", "currency",
                "source_unit", "source_locator", "source_evidence_ids")
        if not isinstance(supplied_bindings, list) or len(supplied_bindings) != len(expected_bindings):
            checker["errors"].append("semantic checker does not bind every displayed financial quantity")
        elif [{key: row.get(key) for key in keys} for row in supplied_bindings] != [{key: row.get(key) for key in keys} for row in expected_bindings]:
            checker["errors"].append("semantic checker fact bindings differ from deterministic evidence mappings")
        checker["semantic"] = semantic_checker
        checker["status"] = "failed" if checker["errors"] else "passed"
    if checker["status"] != "passed":
        raise ValueError("publication checker failed: " + "; ".join(checker["errors"]))
    safe_scope = re.sub(r"[^A-Za-z0-9._-]", "-", scope_id).strip("-")
    safe_quarter = re.sub(r"[^A-Za-z0-9._-]", "-", quarter_id).strip("-")
    base = root / "report/earnings/publications" / publication_type / safe_scope / safe_quarter
    prior_paths = _existing_manifests(base)
    body_hash = draft_hash
    report_title = title or f"{scope_id} {quarter_id} 读者报告"
    live_accepted = False
    state_path = root / "runtime/earnings/state.sqlite"
    if state_path.exists() and all(r["report"].get("source_mode") == "live" for r in sources):
        from earnings_state import EarningsState
        acceptance_state = EarningsState(state_path)
        try:
            live_accepted = all(acceptance_state.db.execute(
                """SELECT 1 FROM report_artifacts a JOIN research_tasks t ON t.task_id=a.task_id
                   WHERE a.path=? AND a.sha256=? AND a.source_mode='live' AND t.state='completed'""",
                (r["path"], r["sha256"])).fetchone() is not None for r in sources)
        finally:
            acceptance_state.close()
    publishable = bool(live_accepted and semantic_checker is not None and checker["status"] == "passed")
    for path in prior_paths:
        previous = read_json(path)
        if (previous["content_sha256"] == body_hash
                and [r["sha256"] for r in previous["sources"]] == [r["sha256"] for r in sources]
                and previous.get("edition") == edition and previous.get("title") == report_title
                and previous.get("publishable") == publishable
                and previous.get("checker", {}).get("semantic") == semantic_checker):
            return {"status": "success", "publication_id": previous["publication_id"], "version": previous["version"],
                    "manifest_path": str(path.relative_to(root)), "idempotent": True}
    version = len(prior_paths) + 1; output = base / f"v{version}"
    output.mkdir(parents=True, exist_ok=False)
    markdown_path = output / "reader-report.md"; html_path = output / "reader-report.html"
    markdown_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(markdown_to_html(markdown, report_title), encoding="utf-8")
    previous_sha = sha256_file(prior_paths[-1]) if prior_paths else None
    publication_id = stable_id("publication", publication_type, scope_id, quarter_id, edition, version, body_hash)
    series_id = stable_id("publication-series", publication_type, scope_id, quarter_id)
    manifest = {"schema_version": 2, "publication_id": publication_id, "series_id": series_id, "publication_type": publication_type,
                "scope_id": scope_id, "quarter_id": quarter_id, "edition": edition, "version": version,
                "title": report_title, "language": "zh-CN", "created_at": utc_now(), "content_sha256": body_hash,
                "previous_manifest_sha256": previous_sha,
                "sources": [{"path": r["path"], "sha256": r["sha256"], "report_id": r["report"].get("report_id")} for r in sources],
                "source_mapping": [{"claim_id": c.get("claim_id"), "evidence_ids": c.get("evidence_ids", [])}
                                   for r in sources for c in r["report"].get("claims", [])],
                "checker": checker, "publishable": publishable,
                "artifacts": {"markdown": {"path": str(markdown_path.relative_to(root)), "sha256": sha256_file(markdown_path)},
                              "html": {"path": str(html_path.relative_to(root)), "sha256": sha256_file(html_path)}},
                "cloud": {"state": "pending", "document_id": None, "url": None}}
    manifest_path = output / "publication-manifest.json"; atomic_write_json(manifest_path, manifest)
    if state_path.exists():
        from earnings_state import EarningsState
        state = EarningsState(state_path)
        try:
            state.db.execute("""INSERT OR IGNORE INTO publication_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (publication_id, series_id, publication_type, scope_id, quarter_id, edition, version,
                 str(manifest_path.relative_to(root)), sha256_file(manifest_path), body_hash, checker["status"], utc_now()))
        finally:
            state.close()
    return {"status": "success", "publication_id": publication_id, "version": version,
            "manifest_path": str(manifest_path.relative_to(root)), "idempotent": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT)); parser.add_argument("--type", choices=sorted(REQUIRED_SECTIONS), required=True)
    parser.add_argument("--scope", required=True); parser.add_argument("--quarter", required=True)
    parser.add_argument("--source-report", action="append", required=True); parser.add_argument("--markdown", required=True)
    parser.add_argument("--semantic-check"); parser.add_argument("--edition", choices=["full", "stage", "revision"], default="full")
    parser.add_argument("--title")
    parser.add_argument("--input-manifest-hash")
    args = parser.parse_args(); root = Path(args.repo_root).resolve()
    try:
        result = build_publication(root, args.type, args.scope, args.quarter,
            [root / value for value in args.source_report], (root / args.markdown).read_text(encoding="utf-8"),
            semantic_checker=read_json(root / args.semantic_check) if args.semantic_check else None,
            edition=args.edition, title=args.title, input_manifest_hash=args.input_manifest_hash)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        result = {"status": "failed", "reason": str(exc)}
    print(json.dumps({"workflow": "earnings-publication", **result}, ensure_ascii=False))
    raise SystemExit(result["status"] == "failed")


if __name__ == "__main__":
    main()
