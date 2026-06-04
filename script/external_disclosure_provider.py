#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_URL = "https://open-cabinet.org/data/full-dataset.json"
DEFAULT_OFFICIAL_SLUG = "trump-donald-j"
OFFICIAL_URL = "https://open-cabinet.org/officials/trump-donald-j"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected top-level object")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_path(repo_root: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root / candidate


def normalize_symbols(symbols: list[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in symbols or []:
        value = str(raw or "").strip().upper()
        if not value:
            continue
        value = re.sub(r"\.(US|NASDAQ|NYSE|AMEX)$", "", value)
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def load_source(*, input_path: Path | None, source_url: str) -> dict[str, Any]:
    if input_path:
        return read_json(input_path)
    with urllib.request.urlopen(source_url, timeout=30) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError(f"{source_url}: expected top-level object")
    return data


def find_official(payload: dict[str, Any], slug: str) -> dict[str, Any] | None:
    officials = payload.get("officials")
    if not isinstance(officials, list):
        return None
    for official in officials:
        if not isinstance(official, dict):
            continue
        if str(official.get("slug") or "").lower() == slug.lower():
            return official
    return None


def normalized_transaction(raw: dict[str, Any], index: int) -> dict[str, Any]:
    ticker = str(raw.get("ticker") or "").strip().upper() or None
    return {
        "transaction_id": f"open-cabinet-trump-{raw.get('date') or 'unknown'}-{index}",
        "description": str(raw.get("description") or "").strip(),
        "ticker": ticker,
        "type": str(raw.get("type") or "").strip(),
        "date": str(raw.get("date") or "").strip(),
        "amount_range": str(raw.get("amount") or "").strip(),
        "late_filing": bool(raw.get("lateFilingFlag")),
    }


def transaction_summary(item: dict[str, Any]) -> str:
    ticker = item.get("ticker") or "ticker unavailable"
    return (
        f"Trump disclosed {item.get('type') or 'transaction'} of "
        f"{item.get('description') or ticker} ({ticker}) on {item.get('date')}; "
        f"amount range {item.get('amount_range') or 'not disclosed'}."
    )


def evidence_for_transaction(
    *,
    item: dict[str, Any],
    evidence_index: int,
    source_path: str,
    published_at: str,
) -> dict[str, Any]:
    ticker = str(item["ticker"]).upper()
    return {
        "evidence_id": f"trump-disclosure-{ticker}-{item.get('date')}-{evidence_index}",
        "source": source_path,
        "source_type": "news",
        "source_subtype": "oge_disclosure",
        "published_at": published_at,
        "symbol": ticker,
        "summary": transaction_summary(item),
        "confidence": 0.72,
        "limitations": [
            "Public disclosure is delayed and may be filed after the transaction date.",
            "Disclosed amount is a range, not exact shares, price, or execution time.",
            "Open Cabinet is a parsed convenience source; OGE filings remain the source of record.",
            "This evidence is news-layer background only and is not a trading signal.",
        ],
    }


def empty_payload(
    *,
    report_date: str,
    source_url: str,
    reason: str,
    status: str = "success",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "date": report_date,
        "generated_at": now_utc(),
        "official": {
            "slug": DEFAULT_OFFICIAL_SLUG,
            "official_url": OFFICIAL_URL,
        },
        "source": {
            "provider": "open_cabinet",
            "dataset_url": source_url,
            "official_url": OFFICIAL_URL,
        },
        "summary": {
            "recent_transactions": 0,
            "matched_transactions": 0,
            "transactions_without_ticker": 0,
        },
        "transactions": [],
        "matched_symbols": {},
        "evidence": [],
        "limitations": [reason],
        "safety_note": "Trump disclosure data is news-layer background only, not a trading signal.",
    }


def build_payload(
    *,
    report_date: str,
    source_url: str,
    official: dict[str, Any],
    exported_at: str | None,
    symbols: list[str],
    lookback_days: int,
    max_transactions: int,
    source_path: str,
) -> dict[str, Any]:
    target_date = date.fromisoformat(report_date)
    start_date = target_date - timedelta(days=max(0, lookback_days))
    symbol_set = set(symbols)
    raw_transactions = official.get("transactions") if isinstance(official.get("transactions"), list) else []
    all_recent: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_transactions):
        if not isinstance(raw, dict):
            continue
        trade_date = parse_date(raw.get("date"))
        if trade_date is None or trade_date < start_date or trade_date > target_date:
            continue
        item = normalized_transaction(raw, index)
        all_recent.append(item)

    all_recent.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    output_transactions = all_recent[:max_transactions]
    transactions_without_ticker = sum(1 for item in all_recent if not item.get("ticker"))
    matched = [
        item
        for item in all_recent
        if item.get("ticker") and symbol_set and str(item["ticker"]).upper() in symbol_set
    ]
    matched_symbols: dict[str, list[dict[str, Any]]] = {}
    for item in matched:
        matched_symbols.setdefault(str(item["ticker"]).upper(), []).append(item)

    published_at = str(official.get("mostRecentFilingDate") or (exported_at or "")[:10] or report_date)
    evidence = [
        evidence_for_transaction(
            item=item,
            evidence_index=index,
            source_path=source_path,
            published_at=published_at,
        )
        for index, item in enumerate(matched)
    ]
    latest_trade_date = max((str(item.get("date") or "") for item in all_recent), default=None)
    return {
        "schema_version": 1,
        "status": "success",
        "date": report_date,
        "generated_at": now_utc(),
        "official": {
            "name": official.get("name"),
            "slug": official.get("slug"),
            "title": official.get("title"),
            "transaction_count": official.get("transactionCount"),
            "most_recent_filing_date": official.get("mostRecentFilingDate"),
            "official_url": OFFICIAL_URL,
        },
        "source": {
            "provider": "open_cabinet",
            "dataset_url": source_url,
            "official_url": OFFICIAL_URL,
            "exported_at": exported_at,
        },
        "query": {
            "symbols": symbols,
            "lookback_days": lookback_days,
            "from_date": start_date.isoformat(),
            "to_date": report_date,
        },
        "summary": {
            "recent_transactions": len(all_recent),
            "output_transactions": len(output_transactions),
            "matched_transactions": len(matched),
            "transactions_without_ticker": transactions_without_ticker,
            "latest_trade_date": latest_trade_date,
            "latest_filing_date": official.get("mostRecentFilingDate"),
        },
        "transactions": output_transactions,
        "matched_symbols": matched_symbols,
        "evidence": evidence,
        "limitations": [
            "Public disclosure is delayed and amount values are ranges.",
            "Ticker fields can be missing from parsed disclosures; unmatched rows are retained as context only.",
            "No precise share count, execution price, or intraday transaction time is available.",
            "This artifact must not add symbols to watchlists or raise execution readiness.",
        ],
        "safety_note": "Trump disclosure data is news-layer background only, not a trading signal.",
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output = (
        resolve_path(repo_root, args.output)
        if args.output
        else repo_root / "report" / args.date / "external-disclosures" / "trump-trades.json"
    )
    source_path = str(output.relative_to(repo_root)) if output.is_relative_to(repo_root) else str(output)
    input_path = resolve_path(repo_root, args.input) if args.input else None
    symbols = normalize_symbols(args.symbol)
    try:
        source_payload = load_source(input_path=input_path, source_url=args.source_url)
        official = find_official(source_payload, args.official_slug)
        if official is None:
            payload = empty_payload(
                report_date=args.date,
                source_url=args.source_url,
                reason=f"official not found in Open Cabinet dataset: {args.official_slug}",
                status="failed",
            )
        else:
            payload = build_payload(
                report_date=args.date,
                source_url=args.source_url,
                official=official,
                exported_at=source_payload.get("exportedAt"),
                symbols=symbols,
                lookback_days=args.lookback_days,
                max_transactions=args.max_transactions,
                source_path=source_path,
            )
    except Exception as exc:
        payload = empty_payload(
            report_date=args.date,
            source_url=args.source_url,
            reason=f"failed to load disclosure source: {exc}",
            status="failed",
        )
    write_json(output, payload)
    return {
        "status": payload["status"],
        "date": args.date,
        "output": str(output),
        "artifacts": [str(output)],
        "summary": payload.get("summary", {}),
        "reason": "; ".join(payload.get("limitations", [])) if payload["status"] != "success" else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Trump OGE/Open Cabinet disclosure evidence")
    parser.add_argument("--date", required=True)
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--input", help="Fixture or cached Open Cabinet full-dataset JSON")
    parser.add_argument("--output")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--official-slug", default=DEFAULT_OFFICIAL_SLUG)
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--max-transactions", type=int, default=250)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    payload = run(build_parser().parse_args())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "success" else 1)


if __name__ == "__main__":
    main()
