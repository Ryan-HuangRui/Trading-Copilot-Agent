from __future__ import annotations

from pathlib import Path
from typing import Any

from longbridge_cli_adapter import longbridge_cli_path, run_read_only_json
from twelve_data_client import RateLimiter, TwelveDataClient


LONG_BRIDGE_INTERVALS = {
    "1min": "1m",
    "1m": "1m",
    "5min": "5m",
    "5m": "5m",
    "15min": "15m",
    "15m": "15m",
    "30min": "30m",
    "30m": "30m",
    "1h": "1h",
    "1hour": "1h",
    "1day": "day",
    "day": "day",
    "1d": "day",
    "week": "week",
    "1week": "week",
    "month": "month",
    "1month": "month",
}

DAILY_PERIODS = {"day", "week", "month", "year"}
MARKET_SUFFIXES = {"US", "HK", "SG", "SH", "SZ", "HAS"}


def provider_name(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_")


def longbridge_symbol(symbol: str, default_market: str = "US") -> str:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    if "." in normalized and normalized.rsplit(".", 1)[1] in MARKET_SUFFIXES:
        return normalized
    return f"{normalized}.{default_market.upper()}"


def display_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if normalized.endswith(".US"):
        return normalized.rsplit(".", 1)[0]
    return normalized


def longbridge_period(interval: str) -> str:
    key = str(interval or "").strip().lower()
    try:
        return LONG_BRIDGE_INTERVALS[key]
    except KeyError as exc:
        raise ValueError(f"unsupported Longbridge interval: {interval}") from exc


def normalize_time(value: Any, period: str) -> str:
    text = str(value or "")
    if period in DAILY_PERIODS:
        return text.split(" ", 1)[0]
    return text


def normalize_longbridge_kline(
    symbol: str,
    interval: str,
    rows: Any,
    provider: str = "longbridge",
) -> dict[str, Any]:
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"Longbridge returned no K-line data for {symbol}")
    period = longbridge_period(interval)
    values = []
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        item = {
            "datetime": normalize_time(row.get("time"), period),
            "open": str(row.get("open", "")),
            "high": str(row.get("high", "")),
            "low": str(row.get("low", "")),
            "close": str(row.get("close", "")),
            "volume": str(row.get("volume", "0")),
        }
        if row.get("turnover") is not None:
            item["turnover"] = str(row.get("turnover"))
        if row.get("session") is not None:
            item["session"] = str(row.get("session"))
        values.append(item)
    if not values:
        raise RuntimeError(f"Longbridge returned no usable K-line rows for {symbol}")
    lb_symbol = longbridge_symbol(symbol)
    return {
        "meta": {
            "symbol": display_symbol(symbol),
            "longbridge_symbol": lb_symbol,
            "interval": interval,
            "provider": provider,
        },
        "values": values,
        "status": "ok",
    }


class LongbridgeMarketDataClient:
    name = "longbridge"

    def __init__(
        self,
        cli: str | None = None,
        default_market: str = "US",
        max_calls_per_minute: int = 120,
        state_file: str | None = None,
    ):
        self.cli = longbridge_cli_path(cli)
        self.default_market = default_market
        self.limiter = RateLimiter(
            max_calls=max_calls_per_minute,
            period_seconds=60,
            state_file=state_file,
        )

    def time_series(self, symbol: str, interval: str = "1day", outputsize: int = 200) -> dict[str, Any]:
        period = longbridge_period(interval)
        lb_symbol = longbridge_symbol(symbol, self.default_market)
        self.limiter.wait_if_needed()
        rows = run_read_only_json(
            self.cli,
            [
                "kline",
                lb_symbol,
                "--period",
                period,
                "--count",
                str(outputsize),
                "--format",
                "json",
            ],
        )
        payload = normalize_longbridge_kline(
            symbol=lb_symbol,
            interval=interval,
            rows=rows,
            provider=self.name,
        )
        payload["meta"]["symbol"] = display_symbol(symbol)
        return payload


class LazyTwelveDataClient:
    name = "twelve_data"

    def __init__(
        self,
        max_calls_per_minute: int = 8,
        state_file: str | None = None,
    ):
        self.max_calls_per_minute = max_calls_per_minute
        self.state_file = state_file
        self._client: TwelveDataClient | None = None

    def _get_client(self) -> TwelveDataClient:
        if self._client is None:
            self._client = TwelveDataClient(
                max_calls_per_minute=self.max_calls_per_minute,
                state_file=self.state_file,
            )
        return self._client

    def time_series(self, symbol: str, interval: str = "1day", outputsize: int = 200) -> dict[str, Any]:
        data = self._get_client().time_series(
            symbol=symbol,
            interval=interval,
            outputsize=outputsize,
        )
        meta = data.setdefault("meta", {})
        if isinstance(meta, dict):
            meta["provider"] = self.name
        return data


class FallbackMarketDataClient:
    def __init__(self, primary: Any, fallback: Any | None = None):
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name if fallback is None else f"{primary.name}_with_{fallback.name}_fallback"

    def time_series(self, symbol: str, interval: str = "1day", outputsize: int = 200) -> dict[str, Any]:
        try:
            return self.primary.time_series(symbol=symbol, interval=interval, outputsize=outputsize)
        except Exception as primary_error:
            if self.fallback is None:
                raise
            try:
                data = self.fallback.time_series(symbol=symbol, interval=interval, outputsize=outputsize)
            except Exception as fallback_error:
                raise RuntimeError(
                    f"{self.primary.name} failed: {primary_error}; "
                    f"{self.fallback.name} fallback failed: {fallback_error}"
                ) from fallback_error
            meta = data.setdefault("meta", {})
            if isinstance(meta, dict):
                meta["provider"] = self.fallback.name
                meta["fallback_from"] = self.primary.name
                meta["primary_error"] = str(primary_error)
            return data


def build_market_data_client(
    repo_root: Path,
    primary: str = "longbridge",
    fallback: str = "twelve",
    longbridge_cli: str | None = None,
    longbridge_default_market: str = "US",
) -> Any:
    def make(name: str) -> Any | None:
        normalized = provider_name(name)
        if normalized in {"", "none", "off", "disabled"}:
            return None
        if normalized in {"longbridge", "longbridge_cli"}:
            return LongbridgeMarketDataClient(
                cli=longbridge_cli,
                default_market=longbridge_default_market,
                state_file=str(repo_root / "config" / "longbridge_rate_limit_state.json"),
            )
        if normalized in {"twelve", "twelve_data", "twelvedata"}:
            return LazyTwelveDataClient(
                max_calls_per_minute=8,
                state_file=str(repo_root / "config" / "rate_limit_state.json"),
            )
        raise ValueError(f"unsupported market data provider: {name}")

    primary_client = make(primary)
    if primary_client is None:
        raise ValueError("primary market data provider cannot be none")
    fallback_client = make(fallback)
    return FallbackMarketDataClient(primary_client, fallback_client)
