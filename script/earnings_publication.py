#!/usr/bin/env python3
"""Build immutable reader publications and apply deterministic acceptance gates."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
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


def _facts(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for report in reports:
        for evidence in report.get("evidence", []):
            for fact in evidence.get("numeric_facts", []):
                value = fact.get("value")
                if isinstance(value, bool) or value is None: continue
                try: decimal_value = Decimal(str(value).replace(",", ""))
                except InvalidOperation: continue
                rows.append({**fact, "value": format(decimal_value, "f"), "evidence_id": evidence.get("evidence_id"),
                             "source_url": evidence.get("source_url")})
    return rows


def _claims(markdown: str) -> list[dict[str, Any]]:
    # Coordinates always address the exact UTF-8-decoded Markdown string supplied to
    # the checker. Numeric normalization happens only in value, never in the text used
    # for offsets, so thousands separators cannot shift later occurrences.
    clean = markdown
    number = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    pattern = rf"(?<![\w.])({number})\s*(USD\s+(?:million|billion)|million\s+USD|billion\s+USD|百万美元|亿美元|百万元|亿元|美元|元|%|％|bps|个基点)"
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
    facts = _facts(reports)
    for claim in numeric_claims:
        explicit = next((row for row in (explicit_fact_bindings or [])
                         if row.get("occurrence") == claim.get("occurrence") and row.get("display") == claim.get("display")), None)
        claim_unit = _unit(claim.get("unit") or "")
        if claim_unit is None:
            errors.append(f"unmapped table quantity without unit: {claim['display']}")
            continue
        matches = []
        for fact in facts:
            fact_unit = _unit(str(fact.get("unit") or ""))
            if not fact_unit or fact_unit[0] != claim_unit[0]: continue
            fact_basis = str(fact.get("accounting_basis") or "").lower()
            claim_basis = str(claim.get("accounting_basis") or "").lower()
            if claim_basis and not (claim_basis == fact_basis or (claim_basis == "gaap" and fact_basis == "us-gaap")): continue
            bound_period = explicit.get("period") if explicit else claim.get("period")
            if bound_period and fact.get("period") != bound_period: continue
            expected = Decimal(str(fact["value"])) * fact_unit[1]
            actual = Decimal(str(claim["value"])) * claim_unit[1]
            tolerance = max(abs(expected) * Decimal("0.0005"), claim_unit[1] * Decimal("0.5") * (Decimal(10) ** -claim.get("decimals", 0)))
            if abs(expected - actual) <= tolerance: matches.append(fact)
        derived = None
        if not matches and claim_unit[0] == "ratio":
            candidates = []
            for numerator in facts:
                n_unit = _unit(str(numerator.get("unit") or ""))
                if not n_unit or n_unit[0] not in {"USD", "CNY"}: continue
                bound_period = explicit.get("period") if explicit else claim.get("period")
                if bound_period and numerator.get("period") != bound_period: continue
                for denominator in facts:
                    d_unit = _unit(str(denominator.get("unit") or ""))
                    if not d_unit or d_unit[0] != n_unit[0] or Decimal(str(denominator["value"])) == 0: continue
                    if numerator is denominator: continue
                    ratio = (Decimal(str(numerator["value"])) * n_unit[1] / (Decimal(str(denominator["value"])) * d_unit[1])) - 1
                    tolerance = claim_unit[1] * Decimal("0.5") * (Decimal(10) ** -claim.get("decimals", 0))
                    if abs(ratio - Decimal(str(claim["value"])) * claim_unit[1]) <= tolerance:
                        candidates.append((numerator, denominator))
            if len(candidates) == 1: derived = candidates[0]
        if len(matches) > 1 and explicit:
            selected = [fact for fact in matches if fact.get("evidence_id") == explicit.get("evidence_id")
                        and fact.get("metric") == explicit.get("metric") and fact.get("period") == explicit.get("period")]
            if len(selected) == 1: matches = selected
        if not matches and derived is None:
            errors.append(f"unmapped numeric claim (value/unit/currency): {claim['display']}")
        elif len(matches) > 1:
            errors.append(f"ambiguous numeric claim requires explicit evidence binding: {claim['display']}")
        elif derived is not None:
            numerator, denominator = derived
            mappings.append({"display": claim["display"], "occurrence": claim.get("occurrence"),
                "evidence_id": numerator.get("evidence_id"), "metric": numerator.get("metric"),
                "period": numerator.get("period"), "accounting_basis": numerator.get("accounting_basis"),
                "currency": "ratio", "source_unit": "%", "derivation": "growth-rate",
                "source_evidence_ids": [numerator.get("evidence_id"), denominator.get("evidence_id")]})
        else:
            fact = matches[0]; mappings.append({"display": claim["display"], "occurrence": claim.get("occurrence"), "evidence_id": fact.get("evidence_id"),
                "metric": fact.get("metric"), "period": fact.get("period"), "accounting_basis": fact.get("accounting_basis"),
                "currency": fact.get("currency") or claim_unit[0], "source_unit": fact.get("unit"),
                "derivation": fact.get("derivation"), "source_evidence_ids": fact.get("source_evidence_ids", [])})
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
                context = markdown[max(0, match.start() - 18):match.end() + 18]
                if not any(token in context for token in ("不能", "无法", "未知", "缺少", "不判断")):
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
            required_evidence = {row.get("evidence_id") for row in checker.get("fact_mappings", []) if row.get("evidence_id")}
            if not required_evidence <= mapped_evidence:
                checker["errors"].append("semantic checker source mapping omits numeric evidence")
        expected_bindings = checker.get("fact_mappings", [])
        supplied_bindings = semantic_checker.get("fact_bindings")
        keys = ("display", "occurrence", "evidence_id", "metric", "period", "accounting_basis", "currency",
                "source_unit", "derivation", "source_evidence_ids")
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
