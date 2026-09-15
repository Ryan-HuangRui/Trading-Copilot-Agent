#!/usr/bin/env python3
"""Deterministic period-safe normalization for disclosed financial facts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError(f"nonfinite numeric value: {value}")
        return result
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric value: {value}") from exc


def normalize_fact(fact: dict[str, Any]) -> dict[str, Any]:
    start = fact.get("start")
    end = fact.get("end")
    if not end:
        raise ValueError("financial fact requires end date")
    try:
        date.fromisoformat(str(end))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid financial fact end date: {end}") from exc
    if start and date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("financial fact start is after end")
    value = _decimal(fact.get("value", fact.get("val")))
    unit = str(fact.get("unit") or "").strip()
    if not unit:
        raise ValueError("financial fact requires unit")
    currency = fact.get("currency")
    if unit.upper() in {"USD", "EUR", "CNY", "JPY", "GBP"}:
        currency = unit.upper()
    duration_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 if start else None
    result = {
        "metric": fact.get("metric") or fact.get("tag"),
        "value": None if value is None else str(value),
        "unit": unit,
        "currency": currency,
        "accounting_basis": fact.get("accounting_basis", "GAAP"),
        "period": {"start": start, "end": end, "kind": "duration" if start else "instant", "duration_days": duration_days},
        "derivation": fact.get("derivation", "reported"),
        "source_evidence_ids": list(fact.get("source_evidence_ids") or []),
        "segment": fact.get("segment"),
        "dimensions": fact.get("dimensions") or {},
    }
    return result


def comparable_scope(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = ("metric", "unit", "currency", "accounting_basis", "segment", "dimensions")
    return all(left.get(key) == right.get(key) for key in keys)


def subtract_period(total: dict[str, Any], prior: dict[str, Any], *, derivation: str) -> dict[str, Any]:
    total_n = normalize_fact(total) if "period" not in total else total
    prior_n = normalize_fact(prior) if "period" not in prior else prior
    if not comparable_scope(total_n, prior_n):
        raise ValueError("cannot subtract financial facts with different currency/unit/accounting/segment scope")
    total_period, prior_period = total_n["period"], prior_n["period"]
    if total_period["kind"] != "duration" or prior_period["kind"] != "duration":
        raise ValueError("period subtraction requires duration facts")
    if total_period["start"] != prior_period["start"] or prior_period["end"] >= total_period["end"]:
        raise ValueError("period subtraction requires nested cumulative periods with the same start")
    start = (date.fromisoformat(prior_period["end"]).fromordinal(date.fromisoformat(prior_period["end"]).toordinal() + 1)).isoformat()
    duration_days = (date.fromisoformat(total_period["end"]) - date.fromisoformat(start)).days + 1
    if duration_days < 45 or duration_days > 120:
        raise ValueError("unsupported subtraction duration; derived period is not a standalone quarter")
    result = {
        **total_n,
        "period": {"start": start, "end": total_period["end"], "kind": "duration",
                   "duration_days": duration_days},
        "derivation": derivation,
        "source_evidence_ids": list(dict.fromkeys(total_n["source_evidence_ids"] + prior_n["source_evidence_ids"])),
        "component_provenance": list(total_n.get("component_provenance") or [_component_provenance(total_n)]) +
                                list(prior_n.get("component_provenance") or [_component_provenance(prior_n)]),
    }
    if total_n["value"] is None or prior_n["value"] is None:
        result.update(value=None, limitations=["missing component; derived standalone period has no value"])
        return result
    result["value"] = str(Decimal(total_n["value"]) - Decimal(prior_n["value"]))
    return result


def standalone_quarter(ytd: dict[str, Any], prior_ytd: dict[str, Any]) -> dict[str, Any]:
    return subtract_period(ytd, prior_ytd, derivation="cumulative_difference")


def fourth_quarter(annual: dict[str, Any], nine_month: dict[str, Any]) -> dict[str, Any]:
    return subtract_period(annual, nine_month, derivation="annual_less_nine_months")


def growth_transition(current: Any, previous: Any) -> dict[str, Any]:
    current_d, previous_d = _decimal(current), _decimal(previous)
    if current_d is None or previous_d is None:
        return {"kind": "missing", "growth_percent": None}
    if previous_d < 0 <= current_d:
        return {"kind": "loss_to_profit", "growth_percent": None}
    if previous_d == 0:
        return {"kind": "zero_base", "growth_percent": None}
    return {"kind": "comparable", "growth_percent": str((current_d - previous_d) / abs(previous_d) * 100)}


def extract_sec_company_facts(payload: dict[str, Any], allowed_tags: set[str] | None = None) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for taxonomy, concepts in (payload.get("facts") or {}).items():
        for tag, concept in concepts.items():
            if allowed_tags and tag not in allowed_tags:
                continue
            for unit, rows in (concept.get("units") or {}).items():
                for row in rows:
                    dimensions = {key: value for key, value in row.items() if key not in {
                        "start", "end", "val", "accn", "fy", "fp", "form", "filed", "frame"
                    }}
                    source_fact_id = f"sec-companyfacts:{row.get('accn') or 'unknown'}:{taxonomy}:{tag}:{row.get('start') or 'instant'}:{row.get('end')}"
                    raw = {"metric": f"{taxonomy}:{tag}", "value": row.get("val"), "unit": unit,
                           "start": row.get("start"), "end": row.get("end"), "accounting_basis": taxonomy,
                           "dimensions": dimensions, "form": row.get("form"), "filed": row.get("filed"), "frame": row.get("frame"),
                           "source_evidence_ids": [source_fact_id]}
                    try:
                        output.append({**normalize_fact(raw), "form": raw["form"], "filed": raw["filed"], "frame": raw["frame"],
                                       "accession": row.get("accn"), "fiscal_year": row.get("fy"), "fiscal_period": row.get("fp"),
                                       "source_fact_id": source_fact_id,
                                       "custom_tag": taxonomy != "us-gaap",
                                       "limitations": ["custom taxonomy tag; original filing review required"] if taxonomy != "us-gaap" else []})
                    except ValueError:
                        continue
    return output


def derive_standalone_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return reported facts plus safe, de-duplicated standalone Q2/Q3/Q4 derivations."""
    latest: dict[tuple[Any, ...], dict[str, Any]] = {}
    for fact in facts:
        key = (fact.get("metric"), fact.get("unit"), fact.get("currency"), fact.get("accounting_basis"),
               jsonable(fact.get("dimensions")), fact.get("segment"), (fact.get("period") or {}).get("start"),
               (fact.get("period") or {}).get("end"), fact.get("fiscal_period"))
        if key not in latest or str(fact.get("filed") or "") > str(latest[key].get("filed") or ""):
            latest[key] = fact
    rows = list(latest.values())
    derived: list[dict[str, Any]] = []
    for total in rows:
        period = total.get("period") or {}
        fp = total.get("fiscal_period")
        if period.get("kind") != "duration" or fp not in {"Q2", "Q3", "FY"}:
            continue
        candidates = [row for row in rows if comparable_scope(total, row)
                      and (row.get("period") or {}).get("start") == period.get("start")
                      and (row.get("period") or {}).get("end", "") < period.get("end", "")
                      and row.get("fiscal_year") == total.get("fiscal_year")]
        if not candidates:
            continue
        prior = max(candidates, key=lambda row: (row.get("period") or {}).get("end", ""))
        try:
            result = fourth_quarter(total, prior) if fp == "FY" else standalone_quarter(total, prior)
        except ValueError:
            continue
        result.update(fiscal_year=total.get("fiscal_year"), fiscal_period="Q4" if fp == "FY" else fp,
                      filed=total.get("filed"), accession=total.get("accession"),
                      limitations=list(dict.fromkeys((total.get("limitations") or []) + (prior.get("limitations") or []))))
        derived.append(result)
    return rows + derived


def jsonable(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _component_provenance(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_fact_id": fact.get("source_fact_id") or (fact.get("source_evidence_ids") or [None])[0],
        "accession": fact.get("accession"),
        "tag": fact.get("metric"),
        "period": fact.get("period"),
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "filed": fact.get("filed"),
    }
