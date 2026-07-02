#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from market_data_provider import build_market_data_client
from market_snapshot import load_env


MARKET_SYMBOLS = [
    {"symbol": "SPY", "label": "S&P 500 ETF", "group": "market"},
    {"symbol": "QQQ", "label": "Nasdaq 100 ETF", "group": "market"},
    {"symbol": "DIA", "label": "Dow 30 ETF", "group": "market"},
    {"symbol": "IWM", "label": "Russell 2000 ETF", "group": "market"},
    {"symbol": "VIX", "label": "VIX", "group": "risk_indicator"},
]

INDUSTRY_SYMBOLS = [
    {"symbol": "XLK", "label": "科技", "group": "sector"},
    {"symbol": "XLC", "label": "通信服务", "group": "sector"},
    {"symbol": "XLY", "label": "可选消费", "group": "sector"},
    {"symbol": "XLP", "label": "必需消费", "group": "sector"},
    {"symbol": "XLF", "label": "金融", "group": "sector"},
    {"symbol": "XLV", "label": "医疗保健", "group": "sector"},
    {"symbol": "XLI", "label": "工业", "group": "sector"},
    {"symbol": "XLE", "label": "能源", "group": "sector"},
    {"symbol": "XLU", "label": "公用事业", "group": "sector"},
    {"symbol": "XLB", "label": "材料", "group": "sector"},
    {"symbol": "XLRE", "label": "房地产", "group": "sector"},
    {"symbol": "SMH", "label": "半导体", "group": "industry"},
]


def output_path(repo_root: Path, date: str, explicit_output: str | None = None) -> Path:
    if explicit_output:
        path = Path(explicit_output)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / date / "longbridge-market-context.json"


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def change_pct(latest: dict[str, Any], previous: dict[str, Any]) -> float | None:
    latest_close = to_float(latest.get("close"))
    previous_close = to_float(previous.get("close"))
    if latest_close is None or previous_close in (None, 0):
        return None
    return round((latest_close / previous_close - 1) * 100, 2)


def compact_bar(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {
        "datetime": row.get("datetime"),
        "open": to_float(row.get("open")),
        "high": to_float(row.get("high")),
        "low": to_float(row.get("low")),
        "close": to_float(row.get("close")),
        "volume": to_float(row.get("volume")),
    }


def fetch_item(client: Any, item: dict[str, str], interval: str, outputsize: int) -> dict[str, Any]:
    data = client.time_series(symbol=item["symbol"], interval=interval, outputsize=outputsize)
    values = data.get("values") if isinstance(data.get("values"), list) else []
    latest = values[0] if values else {}
    previous = values[1] if len(values) > 1 else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return {
        "symbol": item["symbol"],
        "label": item["label"],
        "group": item["group"],
        "provider": meta.get("provider") or "longbridge",
        "longbridge_symbol": meta.get("longbridge_symbol"),
        "latest": compact_bar(latest),
        "previous": compact_bar(previous),
        "metrics": {
            "close_delta_pct": change_pct(latest, previous),
        },
    }


def parse_symbol_arg(value: str, default_group: str) -> dict[str, str]:
    parts = [part.strip() for part in value.split(":", 2)]
    symbol = parts[0].upper()
    label = parts[1] if len(parts) > 1 and parts[1] else symbol
    group = parts[2] if len(parts) > 2 and parts[2] else default_group
    return {"symbol": symbol, "label": label, "group": group}


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [item for item in items if item.get("metrics", {}).get("close_delta_pct") is not None]
    if not valid:
        return {"count": 0, "up": 0, "down": 0, "flat": 0, "avg_pct": None}
    pcts = [item["metrics"]["close_delta_pct"] for item in valid]
    return {
        "count": len(valid),
        "up": len([pct for pct in pcts if pct > 0]),
        "down": len([pct for pct in pcts if pct < 0]),
        "flat": len([pct for pct in pcts if pct == 0]),
        "avg_pct": round(sum(pcts) / len(pcts), 2),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    load_env(repo_root)
    client = build_market_data_client(
        repo_root=repo_root,
        primary="longbridge",
        fallback="none",
        longbridge_cli=args.longbridge_cli,
        longbridge_default_market=args.longbridge_default_market,
    )

    market_symbols = list(MARKET_SYMBOLS)
    industry_symbols = list(INDUSTRY_SYMBOLS)
    market_symbols.extend(parse_symbol_arg(value, "market") for value in args.market_symbol)
    industry_symbols.extend(parse_symbol_arg(value, "sector") for value in args.industry_symbol)

    market_items: list[dict[str, Any]] = []
    industry_items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for bucket, target in ((market_symbols, market_items), (industry_symbols, industry_items)):
        for item in bucket:
            try:
                target.append(fetch_item(client, item, args.interval, args.outputsize))
            except Exception as exc:
                errors.append({"symbol": item["symbol"], "label": item["label"], "group": item["group"], "error": str(exc)})

    status = "success" if not errors else "warn"
    if not market_items and not industry_items:
        status = "failed"

    payload = {
        "status": status,
        "workflow": "longbridge-market-context",
        "date": args.date,
        "source": "longbridge",
        "interval": args.interval,
        "outputsize": args.outputsize,
        "market": market_items,
        "industries": industry_items,
        "errors": errors,
        "summary": {
            "market": summarize(market_items),
            "industries": summarize(industry_items),
            "errors": len(errors),
        },
    }
    output = output_path(repo_root, args.date, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": status, "date": args.date, "output": str(output), "summary": payload["summary"], "errors": errors}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch read-only Longbridge market and industry context")
    parser.add_argument("--date", required=True)
    parser.add_argument("--interval", default="1day")
    parser.add_argument("--outputsize", type=int, default=3)
    parser.add_argument("--market-symbol", action="append", default=[], help="Extra market symbol as SYMBOL[:label[:group]].")
    parser.add_argument("--industry-symbol", action="append", default=[], help="Extra industry/sector symbol as SYMBOL[:label[:group]].")
    parser.add_argument("--longbridge-cli")
    parser.add_argument("--longbridge-default-market", default="US")
    parser.add_argument("--output")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload.get("status") == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
