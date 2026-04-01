#!/usr/bin/env python3
"""
准备“由 Agent 分析”所需的上下文文件：
1) 拉取 watchlist 日线数据（限频）
2) 生成 report/<date>-context.json 供 Agent 分析
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="config/watchlist.json")
    parser.add_argument("--interval", default="1day")
    parser.add_argument("--outputsize", type=int, default=200)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    load_env(repo_root)

    watch = json.loads((repo_root / args.watchlist).read_text(encoding="utf-8"))
    symbols = watch.get("symbols", [])

    today = dt.datetime.utcnow().strftime("%Y-%m-%d")
    data_tag = dt.datetime.utcnow().strftime("%Y%m%d")

    client = TwelveDataClient(
        max_calls_per_minute=8,
        state_file=str(repo_root / "config" / "rate_limit_state.json"),
    )

    raw_dir = repo_root / "raw_data" / args.interval / data_tag
    raw_dir.mkdir(parents=True, exist_ok=True)

    context = {
        "date": today,
        "interval": args.interval,
        "symbols": [],
    }

    for s in symbols:
        data = client.time_series(symbol=s, interval=args.interval, outputsize=args.outputsize)
        path = raw_dir / f"{s}.json"
        save_json(path, data)
        values = data.get("values", [])[:120]
        context["symbols"].append(
            {
                "symbol": s,
                "meta": data.get("meta", {}),
                "latest": values[0] if values else None,
                "bars": values,
                "raw_path": str(path),
            }
        )

    context_path = repo_root / "report" / f"{today}-context.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Context ready: {context_path}")
    print("Next step: let Agent read this context + knowledge/refined and produce report markdown.")


if __name__ == "__main__":
    main()
