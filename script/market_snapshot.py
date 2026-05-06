import json
import os
from pathlib import Path
from statistics import mean

from twelve_data_client import TwelveDataClient, save_json


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


def build_market_snapshot(
    repo_root: Path,
    snapshot_date: str,
    trading_day: dict,
    watchlist_path: str = "config/watchlist.json",
    interval: str = "1day",
    outputsize: int = 200,
) -> tuple[dict, Path]:
    load_env(repo_root)
    watch = json.loads((repo_root / watchlist_path).read_text(encoding="utf-8"))
    symbols = watch.get("symbols", [])

    client = TwelveDataClient(
        max_calls_per_minute=8,
        state_file=str(repo_root / "config" / "rate_limit_state.json"),
    )

    raw_dir = raw_data_dir(repo_root, snapshot_date, interval)
    raw_dir.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "snapshot_date": snapshot_date,
        "interval": interval,
        "outputsize": outputsize,
        "watchlist_path": watchlist_path,
        "trading_day": trading_day,
        "symbols": [],
        "errors": [],
    }

    for symbol in symbols:
        path = raw_dir / f"{symbol}.json"
        try:
            data = client.time_series(symbol=symbol, interval=interval, outputsize=outputsize)
            save_json(path, data)
        except Exception as e:
            cached = load_cached_json(path)
            if cached is None:
                snapshot["errors"].append({"symbol": symbol, "error": str(e), "raw_path": str(path)})
                continue
            data = cached
            snapshot["errors"].append({"symbol": symbol, "error": str(e), "raw_path": str(path), "used_cache": True})
        snapshot["symbols"].append(build_symbol_snapshot(symbol, data, path))

    dates = latest_bar_dates(snapshot["symbols"])
    snapshot["latest_bar_dates"] = dates
    snapshot["stale_data"] = bool(dates and (len(dates) != 1 or dates[0] != snapshot_date))
    if snapshot["stale_data"]:
        snapshot["stale_reason"] = "latest_bar_date_does_not_match_snapshot_date"

    out = snapshot_path(repo_root, snapshot_date, interval)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot, out
