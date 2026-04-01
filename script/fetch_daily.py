#!/usr/bin/env python3
import argparse
import datetime as dt
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
    parser = argparse.ArgumentParser(description="Batch fetch price data from Twelve Data with rate limit control")
    parser.add_argument("--symbols", required=True, help="comma-separated symbols, e.g. AAPL,MSFT,NVDA")
    parser.add_argument("--interval", default="1day", help="e.g. 1day, 4h, 1h")
    parser.add_argument("--outputsize", type=int, default=200)
    parser.add_argument("--output", default="raw_data", help="output directory")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    load_env(repo_root)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    client = TwelveDataClient(
        max_calls_per_minute=8,
        state_file=str(repo_root / "config" / "rate_limit_state.json"),
    )

    date_tag = dt.datetime.utcnow().strftime("%Y%m%d")
    out_dir = repo_root / args.output / args.interval / date_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {len(symbols)} symbols with interval={args.interval}, outputsize={args.outputsize}")

    for idx, symbol in enumerate(symbols, start=1):
        try:
            data = client.time_series(symbol=symbol, interval=args.interval, outputsize=args.outputsize)
            save_json(out_dir / f"{symbol}.json", data)
            print(f"[{idx}/{len(symbols)}] OK {symbol}")
        except Exception as e:
            print(f"[{idx}/{len(symbols)}] FAIL {symbol}: {e}")

    print(f"Done. Data saved to: {out_dir}")


if __name__ == "__main__":
    main()
