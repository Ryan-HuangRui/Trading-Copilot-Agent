#!/usr/bin/env python3
import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional

from twelve_data_client import TwelveDataClient


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


def analyze_long_signal(symbol: str, bars: List[Dict]) -> Dict:
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
            "reason": "上升趋势+20Bar突破+放量",
            "trigger": round(c, 3),
            "stop": round(stop, 3),
            "target1": round(target, 3),
            "invalid": "5m收盘跌回突破位下方并失守近3根低点",
        }

    near = c >= prev20_high * 0.997
    if trend_up and near:
        return {
            "symbol": symbol,
            "status": "临近触发",
            "reason": "趋势向上，价格接近突破位",
            "trigger": round(prev20_high, 3),
            "stop": round(min(lows[-3:]), 3),
            "target1": None,
            "invalid": "突破后不能放量站稳",
        }

    return {
        "symbol": symbol,
        "status": "观察中",
        "reason": "结构未完成或量能不足",
        "trigger": round(prev20_high, 3),
        "stop": round(min(lows[-3:]), 3),
        "target1": None,
        "invalid": "无",
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="config/monitor_state.json")
    ap.add_argument("--interval", default="5min")
    ap.add_argument("--output", default="report/latest-monitor.json")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    load_env(root / ".env")
    state = load_state(root / args.state)

    client = TwelveDataClient(
        api_key=os.getenv("TWELVE_DATA_API_KEY"),
        max_calls_per_minute=8,
        state_file=str(root / "config/rate_limit_state.json"),
    )

    symbols = state.get("symbols", [])
    positions_raw = state.get("positions", {})

    scans = []
    position_updates = []

    for s in symbols:
        d = client.time_series(s, interval=args.interval, outputsize=120)
        bars = parse_series(d.get("values", []))
        if len(bars) < 25:
            scans.append({"symbol": s, "status": "数据不足"})
            continue
        scan = analyze_long_signal(s, bars)
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
        "scans": scans,
        "positions": position_updates,
    }

    out = root / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
