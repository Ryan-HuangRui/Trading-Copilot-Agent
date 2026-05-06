#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from statistics import mean
from typing import Iterable
from urllib.request import Request, urlopen


ISHARES_IVV_HOLDINGS_URL = (
    "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/"
    "1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund"
)
SLICKCHARTS_SP500_URL = "https://www.slickcharts.com/sp500"

TICKER_ALIASES = {
    "BRKB": "BRK.B",
    "BFB": "BF.B",
}


@dataclass
class UniverseFetchResult:
    source: str
    source_url: str
    as_of: str | None
    holdings: list[dict]
    errors: list[dict]


class SlickchartsTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_cell: list[str] = []
        self._current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table" and "table" in (attrs_dict.get("class") or ""):
            self._in_table = True
        if not self._in_table:
            return
        if tag == "tr":
            self._in_row = True
            self._current_row = []
        if tag in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if tag in {"td", "th"} and self._in_cell:
            text = " ".join("".join(self._current_cell).split())
            self._current_row.append(text)
            self._in_cell = False
        if tag == "tr" and self._in_row:
            if self._current_row:
                self.rows.append(self._current_row)
            self._in_row = False
        if tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def fetch_text(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "trading-copilot-agent/1.0",
            "Accept": "text/csv,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8-sig")


def parse_percent(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.replace("%", "").replace(",", "").strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper().replace(" ", "")
    return TICKER_ALIASES.get(cleaned, cleaned)


def fetch_ishares_ivv_holdings() -> UniverseFetchResult:
    text = fetch_text(ISHARES_IVV_HOLDINGS_URL)
    lines = [line for line in text.splitlines() if line.strip()]

    as_of = None
    for line in lines[:10]:
        if line.startswith("Fund Holdings as of"):
            row = next(csv.reader([line]))
            if len(row) > 1:
                as_of = row[1]
            break

    header_idx = None
    for idx, line in enumerate(lines):
        if line.startswith("Ticker,Name,Sector,Asset Class"):
            header_idx = idx
            break
    if header_idx is None:
        raise ValueError("iShares IVV holdings CSV header not found")

    rows = csv.DictReader(lines[header_idx:])
    holdings = []
    for row in rows:
        ticker = (row.get("Ticker") or "").strip()
        asset_class = (row.get("Asset Class") or "").strip()
        if not ticker or asset_class != "Equity":
            continue
        holdings.append(
            {
                "rank": len(holdings) + 1,
                "symbol": normalize_symbol(ticker),
                "provider_symbol": ticker,
                "name": (row.get("Name") or "").strip(),
                "sector": (row.get("Sector") or "").strip(),
                "weight_pct": parse_percent(row.get("Weight (%)")),
                "source": "ishares_ivv_holdings",
            }
        )

    return UniverseFetchResult(
        source="ishares_ivv_holdings",
        source_url=ISHARES_IVV_HOLDINGS_URL,
        as_of=as_of,
        holdings=holdings,
        errors=[],
    )


def fetch_slickcharts_sp500() -> UniverseFetchResult:
    text = fetch_text(SLICKCHARTS_SP500_URL)
    parser = SlickchartsTableParser()
    parser.feed(text)

    holdings = []
    for row in parser.rows:
        if len(row) < 4 or not row[0].isdigit():
            continue
        weight = parse_percent(row[3])
        holdings.append(
            {
                "rank": int(row[0]),
                "symbol": normalize_symbol(row[2]),
                "provider_symbol": row[2].strip().upper(),
                "name": row[1].strip(),
                "sector": None,
                "weight_pct": weight,
                "source": "slickcharts_sp500",
            }
        )

    if not holdings:
        raise ValueError("Slickcharts S&P 500 table not found")

    return UniverseFetchResult(
        source="slickcharts_sp500",
        source_url=SLICKCHARTS_SP500_URL,
        as_of=None,
        holdings=holdings,
        errors=[],
    )


def fetch_sp500_holdings(preferred_source: str = "ishares_ivv") -> UniverseFetchResult:
    errors = []
    fetchers = {
        "ishares_ivv": fetch_ishares_ivv_holdings,
        "slickcharts": fetch_slickcharts_sp500,
    }
    ordered_sources = [preferred_source] + [s for s in fetchers if s != preferred_source]

    for source in ordered_sources:
        try:
            result = fetchers[source]()
            result.errors = errors
            return result
        except Exception as exc:
            errors.append({"source": source, "error": str(exc)})

    raise RuntimeError(f"Unable to fetch S&P 500 holdings: {errors}")


def to_float(row: dict, key: str) -> float | None:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def latest_values(symbol_snapshot: dict, limit: int = 120) -> list[dict]:
    return (symbol_snapshot.get("bars") or [])[:limit]


def avg(values: Iterable[float]) -> float | None:
    items = list(values)
    return mean(items) if items else None


def score_candidate(symbol_snapshot: dict, holding: dict) -> dict:
    values = latest_values(symbol_snapshot, 120)
    latest = symbol_snapshot.get("latest") or {}
    metrics = symbol_snapshot.get("metrics") or {}

    closes = [v for v in (to_float(row, "close") for row in values) if v is not None]
    highs = [v for v in (to_float(row, "high") for row in values) if v is not None]
    lows = [v for v in (to_float(row, "low") for row in values) if v is not None]
    volumes = [v for v in (to_float(row, "volume") for row in values) if v is not None]

    close = to_float(latest, "close")
    volume = to_float(latest, "volume")
    avg20_volume = avg(volumes[1:21]) if len(volumes) > 21 else avg(volumes[:20])
    volume_ratio = round(volume / avg20_volume, 2) if volume and avg20_volume else None
    dollar_volume = close * volume if close and volume else None

    ma20 = avg(closes[:20]) if len(closes) >= 20 else None
    ma50 = avg(closes[:50]) if len(closes) >= 50 else None
    high20 = max(highs[:20]) if len(highs) >= 20 else None
    low20 = min(lows[:20]) if len(lows) >= 20 else None

    close_vs_ma20_pct = round((close / ma20 - 1) * 100, 2) if close and ma20 else None
    close_vs_high20_pct = round((close / high20 - 1) * 100, 2) if close and high20 else None
    close_vs_low20_pct = round((close / low20 - 1) * 100, 2) if close and low20 else None
    range_vs_avg5 = metrics.get("range_vs_avg5")
    close_delta_pct = metrics.get("close_delta_pct")
    weight_pct = holding.get("weight_pct") or 0.0

    score = 0.0
    reasons = []

    score += min(weight_pct, 5.0) * 4
    if weight_pct >= 0.5:
        reasons.append("index_weight_leader")

    if dollar_volume:
        score += max(0.0, min(20.0, (math.log10(dollar_volume) - 8.0) * 6))
        if dollar_volume >= 1_000_000_000:
            reasons.append("high_dollar_volume")

    if volume_ratio is not None:
        score += min(25.0, max(0.0, (volume_ratio - 0.8) * 20))
        if volume_ratio >= 1.2:
            reasons.append("relative_volume_expansion")

    if ma20 and ma50 and ma20 > ma50:
        score += 12
        reasons.append("ma20_above_ma50")
    if close and ma20 and close > ma20:
        score += 6
        reasons.append("close_above_ma20")

    if close_vs_high20_pct is not None and close_vs_high20_pct >= -3:
        score += 12
        reasons.append("near_20d_high")
    elif close_vs_low20_pct is not None and close_vs_low20_pct <= 8:
        score += 6
        reasons.append("near_20d_low_reaction_zone")

    if isinstance(range_vs_avg5, (int, float)):
        score += min(10.0, max(0.0, (range_vs_avg5 - 0.8) * 8))
        if range_vs_avg5 >= 1.2:
            reasons.append("range_expansion")

    if isinstance(close_delta_pct, (int, float)):
        score += min(8.0, abs(close_delta_pct) * 1.5)
        if abs(close_delta_pct) >= 2:
            reasons.append("price_displacement")

    if len(values) < 50:
        score -= 20
        reasons.append("data_length_limited")

    return {
        "symbol": symbol_snapshot.get("symbol"),
        "rank": holding.get("rank"),
        "name": holding.get("name"),
        "sector": holding.get("sector"),
        "source": holding.get("source"),
        "provider_symbol": holding.get("provider_symbol"),
        "weight_pct": holding.get("weight_pct"),
        "score": round(score, 2),
        "reasons": reasons,
        "metrics": {
            "latest_close": close,
            "latest_volume": volume,
            "avg20_volume": round(avg20_volume, 2) if avg20_volume is not None else None,
            "volume_ratio": volume_ratio,
            "dollar_volume": round(dollar_volume, 2) if dollar_volume is not None else None,
            "ma20": round(ma20, 2) if ma20 is not None else None,
            "ma50": round(ma50, 2) if ma50 is not None else None,
            "close_vs_ma20_pct": close_vs_ma20_pct,
            "close_vs_high20_pct": close_vs_high20_pct,
            "close_vs_low20_pct": close_vs_low20_pct,
            "range_vs_avg5": range_vs_avg5,
            "close_delta_pct": close_delta_pct,
        },
    }


def select_candidates(scored: list[dict], candidate_count: int) -> list[dict]:
    return sorted(scored, key=lambda item: item.get("score", 0), reverse=True)[:candidate_count]


def write_candidate_universe(
    path: Path,
    snapshot_date: str,
    source_result: UniverseFetchResult,
    input_top_n: int,
    candidate_count: int,
    candidates: list[dict],
    evaluated_count: int,
    fetch_errors: list[dict],
) -> dict:
    payload = {
        "snapshot_date": snapshot_date,
        "source": {
            "name": source_result.source,
            "url": source_result.source_url,
            "as_of": source_result.as_of,
            "fallback_errors": source_result.errors,
        },
        "input_top_n": input_top_n,
        "candidate_count": candidate_count,
        "evaluated_count": evaluated_count,
        "candidates": candidates,
        "errors": fetch_errors,
        "note": "Deterministic liquidity/weight/price-action screener for research context only; not investment advice.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch S&P 500 holdings universe")
    parser.add_argument("--top", type=int, default=100)
    parser.add_argument("--source", choices=["ishares_ivv", "slickcharts"], default="ishares_ivv")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = fetch_sp500_holdings(args.source)
    holdings = result.holdings[: args.top]
    if args.json:
        print(json.dumps({"source": result.source, "as_of": result.as_of, "holdings": holdings}, ensure_ascii=False, indent=2))
        return
    for item in holdings:
        weight = item.get("weight_pct")
        weight_text = f"{weight:.2f}%" if weight is not None else "-"
        print(f"{item['rank']:>3} {item['symbol']:<6} {weight_text:<8} {item.get('name') or ''}")


if __name__ == "__main__":
    main()
