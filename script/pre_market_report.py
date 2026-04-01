#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from statistics import mean

from twelve_data_client import TwelveDataClient, save_json


def load_env(repo_root: Path):
    env_file = repo_root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def classify_regime(closes: list[float]) -> str:
    if len(closes) < 30:
        return "数据不足"
    ma20 = mean(closes[:20])
    ma50 = mean(closes[:50])
    if ma20 > ma50 * 1.01:
        return "上行趋势"
    if ma20 < ma50 * 0.99:
        return "下行趋势"
    return "震荡/过渡"


def build_plan(symbol: str, values: list[dict]) -> dict:
    closes = [float(v["close"]) for v in values]
    highs = [float(v["high"]) for v in values]
    lows = [float(v["low"]) for v in values]
    opens = [float(v["open"]) for v in values]

    regime = classify_regime(closes)
    last_close = closes[0]
    last_open = opens[0]
    recent_high = max(highs[:20])
    recent_low = min(lows[:20])
    day_change_pct = ((last_close / last_open) - 1) * 100 if last_open else 0.0

    if regime == "上行趋势":
        setup = "回调做多（等待回踩后确认）"
        trigger = f"站稳并重新突破近20日高点 {recent_high:.2f}"
    elif regime == "下行趋势":
        setup = "反弹转弱（仅观察，不追空）"
        trigger = f"反弹未破结构并跌回 {last_close:.2f} 下方"
    else:
        setup = "区间边界反应"
        trigger = f"观察 {recent_low:.2f} - {recent_high:.2f} 区间的假突破/真突破"

    return {
        "symbol": symbol,
        "regime": regime,
        "last_close": round(last_close, 2),
        "recent_high_20": round(recent_high, 2),
        "recent_low_20": round(recent_low, 2),
        "day_change_pct": round(day_change_pct, 2),
        "potential_setup": setup,
        "trigger": trigger,
        "risk_note": "单笔风险<=1%，必须先定义失效位再下单",
    }


def build_exec_brief(today: str, plans: list[dict], report_path: Path) -> str:
    if not plans:
        return "\n".join(
            [
                f"# 今日盘前执行简版（{today}）",
                "## 总览",
                "- 市场状态：数据不足",
                "- 今日最多3个重点标的：无",
                "",
                "## 组合风控",
                "- 当日总风险上限：账户净值 2%",
                "- 相关性约束：同板块同方向仓位合并计风险，避免叠加暴露",
                "- 放弃交易条件：数据不足或关键位无法定义时 NO TRADE",
                "",
                f"完整报告：{report_path}",
            ]
        )

    regime_counts = {"上行趋势": 0, "下行趋势": 0, "震荡/过渡": 0, "数据不足": 0}
    for p in plans:
        regime_counts[p["regime"]] = regime_counts.get(p["regime"], 0) + 1

    if regime_counts.get("下行趋势", 0) >= max(3, len(plans) // 2):
        market_state = "震荡偏弱（优先等待确认，不追价）"
    elif regime_counts.get("上行趋势", 0) >= max(3, len(plans) // 2):
        market_state = "偏强修复（只做回踩确认，不做情绪追高）"
    else:
        market_state = "分化震荡（以关键位反应为主）"

    focus = sorted(plans, key=lambda x: abs(x.get("day_change_pct", 0.0)), reverse=True)[:3]

    lines = [
        f"# 今日盘前执行简版（{today}）",
        "## 总览",
        f"- 市场状态：{market_state}",
        "- 今日最多3个重点标的：" + "、".join(p["symbol"] for p in focus),
        "",
        "## 执行清单（逐标的）",
    ]

    for p in focus:
        support = p["recent_low_20"]
        resistance = p["recent_high_20"]
        pivot = round((support + resistance) / 2, 2)
        lines.extend(
            [
                f"### {p['symbol']}",
                f"- 分水岭/关键位：{support}（支撑） / {pivot}（枢轴） / {resistance}（压力）",
                f"- 主场景：若站稳 {pivot} 上方并保持，关注向 {resistance} 的延续测试。",
                f"- 备选场景：若反抽不过 {pivot} 且回落，优先看向 {support} 回测。",
                f"- 失效条件：主场景在跌回 {support} 下方并收不回时失效。",
                "- 执行要点（1行）：只做‘触发-失效-仓位’完整定义的计划，单笔风险≤1%。",
                "",
            ]
        )

    lines.extend(
        [
            "## 组合风控",
            "- 当日总风险上限：账户净值 2%",
            "- 相关性约束：同板块同方向仓位合并计风险，避免叠加暴露",
            "- 放弃交易条件：关键位反复假突破、开盘即无序拉扯（Barb Wire）时 NO TRADE",
            "",
            f"完整报告：{report_path}",
        ]
    )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate pre-market report from watchlist")
    parser.add_argument("--watchlist", default="config/watchlist.json")
    parser.add_argument("--interval", default="1day")
    parser.add_argument("--outputsize", type=int, default=200)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    load_env(repo_root)

    watchlist_path = repo_root / args.watchlist
    watch = json.loads(watchlist_path.read_text(encoding="utf-8"))
    symbols = watch.get("symbols", [])

    client = TwelveDataClient(
        max_calls_per_minute=8,
        state_file=str(repo_root / "config" / "rate_limit_state.json"),
    )
    today = dt.datetime.utcnow().strftime("%Y-%m-%d")

    plans = []
    raw_dir = repo_root / "raw_data" / args.interval / today
    raw_dir.mkdir(parents=True, exist_ok=True)

    for symbol in symbols:
        data = client.time_series(symbol=symbol, interval=args.interval, outputsize=args.outputsize)
        save_json(raw_dir / f"{symbol}.json", data)
        values = data.get("values", [])
        if not values:
            continue
        plans.append(build_plan(symbol, values))

    report_lines = [
        f"# Pre-Market Report ({today})",
        "",
        "## 总结",
        f"- 覆盖标的数: {len(plans)}",
        f"- 数据周期: {args.interval}",
        "",
        "## 交易计划候选",
    ]

    for p in plans:
        report_lines.extend(
            [
                f"### {p['symbol']}",
                f"- 市场状态: {p['regime']}",
                f"- 最新收盘: {p['last_close']}",
                f"- 近20日区间: {p['recent_low_20']} - {p['recent_high_20']}",
                f"- 关注机会: {p['potential_setup']}",
                f"- 触发参考: {p['trigger']}",
                f"- 风险约束: {p['risk_note']}",
                "",
            ]
        )

    report_path = repo_root / "report" / f"{today}-pre-market.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    exec_brief_path = repo_root / "report" / f"{today}-exec-brief.md"
    exec_brief_content = build_exec_brief(today=today, plans=plans, report_path=report_path)
    exec_brief_path.write_text(exec_brief_content, encoding="utf-8")

    print(f"Report generated: {report_path}")
    print(f"Exec brief generated: {exec_brief_path}")


if __name__ == "__main__":
    main()
