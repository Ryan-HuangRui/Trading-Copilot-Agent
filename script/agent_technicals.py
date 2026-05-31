#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected top-level object")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(repo_root: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root / candidate


def normalize_symbols(symbols: list[str] | None, available: list[str]) -> list[str]:
    source = symbols if symbols else available
    seen = set()
    result: list[str] = []
    for symbol in source or []:
        value = str(symbol or "").strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def round_or_none(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def sma(values: list[float], period: int) -> float | None:
    if not values:
        return None
    window = values[: min(period, len(values))]
    return sum(window) / len(window)


def true_ranges(bars_newest_first: list[dict[str, Any]]) -> list[float]:
    rows = list(reversed(bars_newest_first))
    ranges: list[float] = []
    previous_close: float | None = None
    for row in rows:
        high = to_float(row.get("high"))
        low = to_float(row.get("low"))
        close = to_float(row.get("close"))
        if high is None or low is None:
            continue
        candidates = [high - low]
        if previous_close is not None:
            candidates.append(abs(high - previous_close))
            candidates.append(abs(low - previous_close))
        ranges.append(max(candidates))
        previous_close = close
    return list(reversed(ranges))


def rsi(closes_newest_first: list[float], period: int = 14) -> float | None:
    closes = list(reversed(closes_newest_first))
    if len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(closes[-period - 1 : -1], closes[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def metrics_for_bars(bars: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [value for value in (to_float(row.get("close")) for row in bars) if value is not None]
    volumes = [value for value in (to_float(row.get("volume")) for row in bars) if value is not None]
    ranges = true_ranges(bars)
    latest_close = closes[0] if closes else None
    previous_close = closes[1] if len(closes) > 1 else None
    close_change_pct = None
    if latest_close is not None and previous_close:
        close_change_pct = (latest_close / previous_close - 1) * 100
    return {
        "latest_close": round_or_none(latest_close),
        "close_change_pct": round_or_none(close_change_pct),
        "sma_20": round_or_none(sma(closes, 20)),
        "sma_50": round_or_none(sma(closes, 50)),
        "volume_sma_20": round_or_none(sma(volumes, 20)),
        "atr_14": round_or_none(sma(ranges, 14)),
        "rsi_14": round_or_none(rsi(closes, 14)),
    }


def build_technicals_payload(
    *,
    date: str,
    symbols: list[str],
    source_payload: dict[str, Any],
    source_path: Path,
) -> dict[str, Any]:
    market_data = source_payload.get("market_data")
    if not isinstance(market_data, dict):
        raise ValueError(f"{source_path}: missing market_data object")
    normalized = normalize_symbols(symbols, list(market_data.keys()))
    technicals: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    missing: list[str] = []
    for symbol in normalized:
        item = market_data.get(symbol)
        if not isinstance(item, dict):
            missing.append(symbol)
            continue
        bars = item.get("bars")
        if not isinstance(bars, list) or not bars:
            missing.append(symbol)
            continue
        metrics = metrics_for_bars([row for row in bars if isinstance(row, dict)])
        latest = item.get("latest") if isinstance(item.get("latest"), dict) else {}
        technicals[symbol] = {
            "symbol": symbol,
            "as_of": latest.get("datetime") or date,
            "metrics": metrics,
            "bar_count": len(bars),
        }
        evidence.append(
            {
                "evidence_id": f"{date}:technical_indicator:{symbol}:{technicals[symbol]['as_of']}",
                "source": str(source_path),
                "source_type": "technical_indicator",
                "as_of": str(technicals[symbol]["as_of"]),
                "symbol": symbol,
                "summary": (
                    f"{symbol} technicals: close={metrics.get('latest_close')}, "
                    f"sma20={metrics.get('sma_20')}, rsi14={metrics.get('rsi_14')}"
                ),
                "confidence": 0.85 if len(bars) >= 20 else 0.65,
                "limitations": [] if len(bars) >= 20 else ["fewer than 20 bars"],
            }
        )
    return {
        "schema_version": 1,
        "tool": "agent_technicals",
        "date": date,
        "generated_at": now_utc(),
        "source_path": str(source_path),
        "symbols": normalized,
        "missing_symbols": missing,
        "technicals": technicals,
        "evidence": evidence,
        "limitations": [f"missing technical input: {symbol}" for symbol in missing],
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    source_path = resolve_path(repo_root, args.market_data) if args.market_data else repo_root / "report" / args.date / "agents" / "market-data.json"
    output = resolve_path(repo_root, args.output) if args.output else repo_root / "report" / args.date / "agents" / "technicals.json"
    payload = build_technicals_payload(
        date=args.date,
        symbols=args.symbol,
        source_payload=read_json(source_path),
        source_path=source_path,
    )
    write_json(output, payload)
    return {
        "status": "success",
        "date": args.date,
        "output": str(output),
        "symbols": payload["symbols"],
        "missing_symbols": payload["missing_symbols"],
        "evidence_count": len(payload["evidence"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build deterministic technical evidence from agent market-data bars")
    parser.add_argument("--date", required=True)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--market-data")
    parser.add_argument("--output")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    try:
        payload = run(build_parser().parse_args())
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
