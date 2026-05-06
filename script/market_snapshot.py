from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import mean

from twelve_data_client import TwelveDataClient, save_json
from sp500_universe import (
    fetch_sp500_holdings,
    score_candidate,
    select_candidates,
    write_candidate_universe,
)


def load_env(repo_root: Path) -> None:
    env_file = repo_root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def load_cached_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def snapshot_filename(interval: str) -> str:
    return "daily-snapshot.json" if interval == "1day" else f"{interval}-snapshot.json"


def raw_data_dir(repo_root: Path, snapshot_date: str, interval: str) -> Path:
    return repo_root / "raw_data" / snapshot_date / interval


def report_day_dir(repo_root: Path, report_date: str) -> Path:
    return repo_root / "report" / report_date


def snapshot_path(repo_root: Path, snapshot_date: str, interval: str = "1day") -> Path:
    return report_day_dir(repo_root, snapshot_date) / snapshot_filename(interval)


def candidate_universe_path(repo_root: Path, snapshot_date: str) -> Path:
    return report_day_dir(repo_root, snapshot_date) / "candidate-universe.json"


def to_float(row: dict, key: str) -> float | None:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def build_symbol_snapshot(symbol: str, data: dict, raw_path: Path) -> dict:
    values = data.get("values", [])[:120]
    latest = values[0] if values else None
    previous = values[1] if len(values) > 1 else None

    latest_close = to_float(latest or {}, "close")
    latest_open = to_float(latest or {}, "open")
    latest_high = to_float(latest or {}, "high")
    latest_low = to_float(latest or {}, "low")
    previous_close = to_float(previous or {}, "close")

    close_delta_pct = None
    day_change_pct = None
    day_range = None
    range_vs_avg5 = None

    if latest_close is not None and previous_close:
        close_delta_pct = round((latest_close / previous_close - 1) * 100, 2)
    if latest_close is not None and latest_open:
        day_change_pct = round((latest_close / latest_open - 1) * 100, 2)
    if latest_high is not None and latest_low is not None:
        day_range = round(latest_high - latest_low, 3)

    ranges = []
    for row in values[:5]:
        high = to_float(row, "high")
        low = to_float(row, "low")
        if high is not None and low is not None:
            ranges.append(high - low)
    if day_range is not None and ranges:
        avg_range = mean(ranges)
        range_vs_avg5 = round(day_range / avg_range, 2) if avg_range else None

    return {
        "symbol": symbol,
        "meta": data.get("meta", {}),
        "latest": latest,
        "previous": previous,
        "metrics": {
            "close_delta_pct": close_delta_pct,
            "intraday_change_pct": day_change_pct,
            "day_range": day_range,
            "range_vs_avg5": range_vs_avg5,
        },
        "bars": values,
        "raw_path": str(raw_path),
    }


def latest_bar_dates(symbols: list[dict]) -> list[str]:
    dates = []
    for item in symbols:
        latest = item.get("latest") or {}
        date_text = latest.get("datetime")
        if date_text:
            dates.append(str(date_text))
    return sorted(set(dates))


def merge_symbols(primary: list[str], secondary: list[str]) -> list[str]:
    seen = set()
    merged = []
    for symbol in primary + secondary:
        normalized = str(symbol).strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def fetch_symbol_snapshot(
    client: TwelveDataClient,
    symbol: str,
    interval: str,
    outputsize: int,
    raw_dir: Path,
) -> tuple[dict, Path]:
    path = raw_dir / f"{symbol}.json"
    data = client.time_series(symbol=symbol, interval=interval, outputsize=outputsize)
    save_json(path, data)
    return build_symbol_snapshot(symbol, data, path), path


def build_sp500_screen(
    client: TwelveDataClient,
    repo_root: Path,
    snapshot_date: str,
    interval: str,
    outputsize: int,
    raw_dir: Path,
    top_n: int,
    candidate_count: int,
    source: str,
) -> tuple[dict, dict[str, dict]]:
    source_result = fetch_sp500_holdings(source)
    holdings = source_result.holdings[:top_n]
    fetched_snapshots = {}
    scored = []
    fetch_errors = []

    for holding in holdings:
        symbol = holding["symbol"]
        try:
            symbol_snapshot, _ = fetch_symbol_snapshot(
                client=client,
                symbol=symbol,
                interval=interval,
                outputsize=outputsize,
                raw_dir=raw_dir,
            )
            symbol_snapshot["sources"] = ["sp500_screen"]
            symbol_snapshot["universe_rank"] = holding.get("rank")
            symbol_snapshot["index_weight_pct"] = holding.get("weight_pct")
            symbol_snapshot["sector"] = holding.get("sector")
            fetched_snapshots[symbol] = symbol_snapshot
            scored.append(score_candidate(symbol_snapshot, holding))
        except Exception as e:
            fetch_errors.append(
                {
                    "symbol": symbol,
                    "rank": holding.get("rank"),
                    "source": holding.get("source"),
                    "error": str(e),
                }
            )

    candidates = select_candidates(scored, candidate_count)
    payload = write_candidate_universe(
        path=candidate_universe_path(repo_root, snapshot_date),
        snapshot_date=snapshot_date,
        source_result=source_result,
        input_top_n=top_n,
        candidate_count=candidate_count,
        candidates=candidates,
        evaluated_count=len(scored),
        fetch_errors=fetch_errors,
    )
    return payload, fetched_snapshots


def build_market_snapshot(
    repo_root: Path,
    snapshot_date: str,
    trading_day: dict,
    watchlist_path: str = "config/watchlist.json",
    interval: str = "1day",
    outputsize: int = 200,
    sp500_screen: bool = False,
    sp500_top: int = 100,
    sp500_candidates: int = 15,
    sp500_source: str = "ishares_ivv",
) -> tuple[dict, Path]:
    load_env(repo_root)
    watch = json.loads((repo_root / watchlist_path).read_text(encoding="utf-8"))
    watchlist_symbols = merge_symbols(watch.get("symbols", []), [])

    client = TwelveDataClient(
        max_calls_per_minute=8,
        state_file=str(repo_root / "config" / "rate_limit_state.json"),
    )

    raw_dir = raw_data_dir(repo_root, snapshot_date, interval)
    raw_dir.mkdir(parents=True, exist_ok=True)

    screen_payload = None
    pre_fetched_snapshots = {}
    dynamic_symbols = []
    if sp500_screen:
        try:
            screen_payload, pre_fetched_snapshots = build_sp500_screen(
                client=client,
                repo_root=repo_root,
                snapshot_date=snapshot_date,
                interval=interval,
                outputsize=outputsize,
                raw_dir=raw_dir,
                top_n=sp500_top,
                candidate_count=sp500_candidates,
                source=sp500_source,
            )
        except Exception as e:
            screen_payload = {
                "snapshot_date": snapshot_date,
                "source": {"name": sp500_source, "url": None, "as_of": None, "fallback_errors": []},
                "input_top_n": sp500_top,
                "candidate_count": sp500_candidates,
                "evaluated_count": 0,
                "candidates": [],
                "errors": [{"stage": "sp500_screen", "error": str(e)}],
                "note": "S&P 500 screener failed; snapshot continued with the fixed watchlist only.",
            }
            failed_screen_path = candidate_universe_path(repo_root, snapshot_date)
            failed_screen_path.parent.mkdir(parents=True, exist_ok=True)
            failed_screen_path.write_text(
                json.dumps(screen_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        dynamic_symbols = [item["symbol"] for item in screen_payload.get("candidates", [])]

    symbols = merge_symbols(watchlist_symbols, dynamic_symbols)

    snapshot = {
        "snapshot_date": snapshot_date,
        "interval": interval,
        "outputsize": outputsize,
        "watchlist_path": watchlist_path,
        "watchlist_symbols": watchlist_symbols,
        "trading_day": trading_day,
        "dynamic_universe_enabled": sp500_screen,
        "dynamic_universe_symbols": dynamic_symbols,
        "symbols": [],
        "errors": [],
    }
    if screen_payload:
        snapshot["candidate_universe_path"] = str(candidate_universe_path(repo_root, snapshot_date))
        snapshot["candidate_universe"] = {
            "source": screen_payload.get("source"),
            "input_top_n": screen_payload.get("input_top_n"),
            "candidate_count": screen_payload.get("candidate_count"),
            "evaluated_count": screen_payload.get("evaluated_count"),
            "candidates": screen_payload.get("candidates", []),
            "errors": screen_payload.get("errors", []),
        }

    for symbol in symbols:
        if symbol in pre_fetched_snapshots:
            symbol_snapshot = pre_fetched_snapshots[symbol]
            sources = set(symbol_snapshot.get("sources", []))
            if symbol in watchlist_symbols:
                sources.add("watchlist")
            symbol_snapshot["sources"] = sorted(sources)
            snapshot["symbols"].append(symbol_snapshot)
            continue

        path = raw_dir / f"{symbol}.json"
        try:
            symbol_snapshot, _ = fetch_symbol_snapshot(
                client=client,
                symbol=symbol,
                interval=interval,
                outputsize=outputsize,
                raw_dir=raw_dir,
            )
        except Exception as e:
            cached = load_cached_json(path)
            if cached is None:
                snapshot["errors"].append({"symbol": symbol, "error": str(e), "raw_path": str(path)})
                continue
            data = cached
            snapshot["errors"].append({"symbol": symbol, "error": str(e), "raw_path": str(path), "used_cache": True})
            symbol_snapshot = build_symbol_snapshot(symbol, data, path)

        sources = []
        if symbol in watchlist_symbols:
            sources.append("watchlist")
        if symbol in dynamic_symbols:
            sources.append("sp500_screen")
        symbol_snapshot["sources"] = sources
        snapshot["symbols"].append(symbol_snapshot)

    dates = latest_bar_dates(snapshot["symbols"])
    snapshot["latest_bar_dates"] = dates
    snapshot["stale_data"] = bool(dates and (len(dates) != 1 or dates[0] != snapshot_date))
    if snapshot["stale_data"]:
        snapshot["stale_reason"] = "latest_bar_date_does_not_match_snapshot_date"

    out = snapshot_path(repo_root, snapshot_date, interval)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot, out
