#!/usr/bin/env python3
import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional

from market_data_provider import build_market_data_client


@dataclass
class Position:
    symbol: str
    entry: float
    shares: int
    stop: float
    partial_taken: bool = False


def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


def load_state(path: Path) -> Dict:
    if not path.exists():
        return {
            "risk_per_trade_pct": 2,
            "mode": "long_only",
            "symbols": ["INTC", "ORCL", "MU"],
            "positions": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def parse_series(values: List[Dict]) -> List[Dict]:
    # TwelveData returns newest first -> reverse to oldest first
    out = []
    for r in reversed(values):
        out.append(
            {
                "dt": r["datetime"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r.get("volume") or 0),
            }
        )
    return out


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = mean(values[:period])
    for p in values[period:]:
        e = p * k + e * (1 - k)
    return e


def limited_bars(bars: List[Dict], limit: int) -> List[Dict]:
    return [dict(bar) for bar in bars[-limit:]]


def bar_value(bar: Dict | None, key: str) -> Optional[float]:
    if not isinstance(bar, dict):
        return None
    try:
        return float(bar.get(key))
    except (TypeError, ValueError):
        return None


def vwap(bars: List[Dict]) -> Optional[float]:
    total_volume = 0.0
    total_turnover = 0.0
    for bar in bars:
        volume = bar_value(bar, "volume")
        high = bar_value(bar, "high")
        low = bar_value(bar, "low")
        close = bar_value(bar, "close")
        if not volume or high is None or low is None or close is None:
            continue
        typical = (high + low + close) / 3
        total_turnover += typical * volume
        total_volume += volume
    if total_volume <= 0:
        return None
    return round(total_turnover / total_volume, 4)


def pct_change(current: Optional[float], reference: Optional[float]) -> Optional[float]:
    if current is None or reference in {None, 0}:
        return None
    return round((current - float(reference)) / float(reference) * 100, 4)


def max_present(values: List[Optional[float]]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def min_present(values: List[Optional[float]]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def price_evidence_fields(
    bars: List[Dict],
    *,
    supplemental_bars: Dict[str, List[Dict]] | None = None,
    supplemental_errors: Dict[str, str] | None = None,
    primary_interval: str = "5min",
) -> Dict:
    supplemental_bars = supplemental_bars or {}
    supplemental_errors = supplemental_errors or {}
    daily = supplemental_bars.get("1day", [])
    latest = bars[-1] if bars else {}
    latest_close = bar_value(latest, "close")
    previous_day = daily[-2] if len(daily) >= 2 else (daily[-1] if daily else None)
    previous_day_close = bar_value(previous_day, "close")
    key_levels = {
        "previous_day_high": bar_value(previous_day, "high"),
        "previous_day_low": bar_value(previous_day, "low"),
        "previous_day_close": previous_day_close,
        "intraday_high": max_present([bar_value(bar, "high") for bar in bars]),
        "intraday_low": min_present([bar_value(bar, "low") for bar in bars]),
        "vwap": vwap(bars),
    }
    distances = {
        "gap_pct_vs_previous_close": pct_change(latest_close, previous_day_close),
        "distance_to_vwap_pct": pct_change(latest_close, key_levels["vwap"]),
    }
    return {
        "price_data_interval": "scan_interval",
        "latest_bar": dict(latest),
        "recent_bars": limited_bars(bars, 20),
        "price_evidence": {
            "primary_interval": primary_interval,
            "bars": {
                primary_interval: limited_bars(bars, 78),
                "1h": limited_bars(supplemental_bars.get("1h", []), 120),
                "15min": limited_bars(supplemental_bars.get("15min", []), 80),
                "1day": limited_bars(daily, 60),
            },
            "key_levels": key_levels,
            "derived": distances,
            "errors": supplemental_errors,
        },
    }


def price_context_fields(
    bars: List[Dict],
    *,
    limit: int = 20,
    supplemental_bars: Dict[str, List[Dict]] | None = None,
    supplemental_errors: Dict[str, str] | None = None,
    primary_interval: str = "5min",
) -> Dict:
    fields = price_evidence_fields(
        bars,
        supplemental_bars=supplemental_bars,
        supplemental_errors=supplemental_errors,
        primary_interval=primary_interval,
    )
    fields["recent_bars"] = limited_bars(bars, limit)
    return fields


def analyze_long_signal(
    symbol: str,
    bars: List[Dict],
    supplemental_bars: Dict[str, List[Dict]] | None = None,
    supplemental_errors: Dict[str, str] | None = None,
    primary_interval: str = "5min",
) -> Dict:
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b["volume"] for b in bars]

    c = closes[-1]
    prev20_high = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1])
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    avg20_vol = mean(vols[-20:]) if len(vols) >= 20 else mean(vols)
    v = vols[-1]

    trend_up = bool(e20 and e50 and e20 > e50)
    breakout = c > prev20_high
    vol_ok = v >= avg20_vol * 1.2 if avg20_vol > 0 else False

    if trend_up and breakout and vol_ok:
        stop = min(lows[-3:])
        risk = c - stop
        target = c + 2 * risk if risk > 0 else c
        return {
            "symbol": symbol,
            "status": "可执行",
            "setup": "strong_breakout_trend_following.md",
            "setup_files": ["strong_breakout_trend_following.md", "tight_range_breakout_filter.md"],
            "reason": "上升趋势+20Bar突破+放量",
            "trigger": round(c, 3),
            "trigger_detail": {
                "type": "break_above",
                "price": round(c, 3),
                "text": "上升趋势中 20Bar 突破并放量",
            },
            "stop": round(stop, 3),
            "invalidation_detail": {
                "type": "break_below",
                "price": round(stop, 3),
                "text": "5m 收盘跌回突破位下方并失守近3根低点",
            },
            "target1": round(target, 3),
            "invalid": "5m收盘跌回突破位下方并失守近3根低点",
            "risk_quality": "acceptable" if risk > 0 else "invalid",
            "journal_appendable": risk > 0,
            "bar_timestamp": bars[-1].get("dt"),
            **price_context_fields(
                bars,
                supplemental_bars=supplemental_bars,
                supplemental_errors=supplemental_errors,
                primary_interval=primary_interval,
            ),
        }

    near = c >= prev20_high * 0.997
    if trend_up and near:
        return {
            "symbol": symbol,
            "status": "临近触发",
            "setup": "breakout_pullback_continuation.md",
            "setup_files": ["breakout_pullback_continuation.md", "tight_range_breakout_filter.md"],
            "reason": "趋势向上，价格接近突破位",
            "trigger": round(prev20_high, 3),
            "trigger_detail": {
                "type": "break_above",
                "price": round(prev20_high, 3),
                "text": "趋势向上，价格接近 20Bar 突破位",
            },
            "stop": round(min(lows[-3:]), 3),
            "invalidation_detail": {
                "type": "break_below",
                "price": round(min(lows[-3:]), 3),
                "text": "突破后不能放量站稳",
            },
            "target1": None,
            "invalid": "突破后不能放量站稳",
            "risk_quality": "watch_only",
            "journal_appendable": True,
            "bar_timestamp": bars[-1].get("dt"),
            **price_context_fields(
                bars,
                supplemental_bars=supplemental_bars,
                supplemental_errors=supplemental_errors,
                primary_interval=primary_interval,
            ),
        }

    return {
        "symbol": symbol,
        "status": "观察中",
        "setup": "NO VALID SETUP",
        "setup_files": [],
        "reason": "结构未完成或量能不足",
        "trigger": round(prev20_high, 3),
        "trigger_detail": {"type": "watch", "price": round(prev20_high, 3), "text": "观察突破位"},
        "stop": round(min(lows[-3:]), 3),
        "invalidation_detail": {"type": "none", "price": round(min(lows[-3:]), 3), "text": "无可执行失效位"},
        "target1": None,
        "invalid": "无",
        "risk_quality": "insufficient_setup",
        "journal_appendable": False,
        "bar_timestamp": bars[-1].get("dt"),
        **price_context_fields(
            bars,
            supplemental_bars=supplemental_bars,
            supplemental_errors=supplemental_errors,
            primary_interval=primary_interval,
        ),
    }


def analyze_position(pos: Position, last_price: float) -> Dict:
    r_per_share = pos.entry - pos.stop
    if r_per_share <= 0:
        return {"symbol": pos.symbol, "action": "检查止损设置", "r": None}
    r_now = (last_price - pos.entry) / r_per_share
    action = "持有"
    if r_now >= 1 and not pos.partial_taken:
        action = "达到1R，考虑减仓并上移止损"
    elif r_now < 0:
        action = "浮亏，严守止损，不加仓"
    elif last_price <= pos.stop:
        action = "失效，执行止损退出"
    return {
        "symbol": pos.symbol,
        "last": round(last_price, 3),
        "r": round(r_now, 2),
        "action": action,
        "stop": pos.stop,
    }


def fetch_optional_bars(client, symbol: str, *, interval: str, outputsize: int) -> tuple[List[Dict], Optional[str]]:
    try:
        data = client.time_series(symbol, interval=interval, outputsize=outputsize)
        return parse_series(data.get("values", [])), None
    except Exception as exc:
        return [], str(exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="config/monitor_state.json")
    ap.add_argument("--interval", default="5min")
    ap.add_argument("--output", default="report/latest-monitor.json")
    ap.add_argument("--market-data-source", default="longbridge", choices=["longbridge", "twelve"])
    ap.add_argument("--fallback-market-data-source", default="twelve", choices=["twelve", "longbridge", "none"])
    ap.add_argument("--longbridge-cli")
    ap.add_argument("--longbridge-default-market", default="US")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    load_env(root / ".env")
    state = load_state(root / args.state)

    client = build_market_data_client(
        repo_root=root,
        primary=args.market_data_source,
        fallback=args.fallback_market_data_source,
        longbridge_cli=args.longbridge_cli,
        longbridge_default_market=args.longbridge_default_market,
    )

    symbols = state.get("symbols", [])
    positions_raw = state.get("positions", {})

    scans = []
    position_updates = []

    for s in symbols:
        d = client.time_series(s, interval=args.interval, outputsize=120)
        bars = parse_series(d.get("values", []))
        supplemental_bars: Dict[str, List[Dict]] = {}
        supplemental_errors: Dict[str, str] = {}
        for extra_interval, outputsize in (("1h", 120), ("15min", 80), ("1day", 60)):
            extra_bars, extra_error = fetch_optional_bars(client, s, interval=extra_interval, outputsize=outputsize)
            supplemental_bars[extra_interval] = extra_bars
            if extra_error:
                supplemental_errors[extra_interval] = extra_error
        if len(bars) < 25:
            scan = {"symbol": s, "status": "数据不足", "reason": "可用K线少于25根", "recent_bars": [dict(bar) for bar in bars]}
            if bars:
                scan["latest_bar"] = dict(bars[-1])
                scan["price_data_interval"] = "scan_interval"
                scan["bar_timestamp"] = bars[-1].get("dt")
                scan["price_evidence"] = price_evidence_fields(
                    bars,
                    supplemental_bars=supplemental_bars,
                    supplemental_errors=supplemental_errors,
                    primary_interval=args.interval,
                )["price_evidence"]
            scans.append(scan)
            continue
        scan = analyze_long_signal(
            s,
            bars,
            supplemental_bars=supplemental_bars,
            supplemental_errors=supplemental_errors,
            primary_interval=args.interval,
        )
        scans.append(scan)

        if s in positions_raw:
            p = positions_raw[s]
            pos = Position(
                symbol=s,
                entry=float(p["entry"]),
                shares=int(p["shares"]),
                stop=float(p["stop"]),
                partial_taken=bool(p.get("partial_taken", False)),
            )
            position_updates.append(analyze_position(pos, bars[-1]["close"]))

    payload = {
        "mode": state.get("mode", "long_only"),
        "risk_per_trade_pct": state.get("risk_per_trade_pct", 2),
        "interval": args.interval,
        "market_data_source": getattr(client, "name", args.market_data_source),
        "primary_market_data_source": args.market_data_source,
        "fallback_market_data_source": args.fallback_market_data_source,
        "scans": scans,
        "positions": position_updates,
    }

    out = root / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
