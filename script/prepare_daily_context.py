#!/usr/bin/env python3
"""
准备盘前计划所需的上下文文件。

盘前不重新拉行情数据，而是读取上一个已完成交易日的 daily snapshot：
- source: report/<SNAPSHOT_DATE>/daily-snapshot.json
- output: report/<PRE_MARKET_DATE>/pre-market-context.json
"""
import argparse
import json
from pathlib import Path

from market_calendar import MARKET_TIMEZONE, previous_trading_day, resolve_market_date, trading_day_status
from market_snapshot import snapshot_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", default="config/watchlist.json", help="Kept for compatibility; snapshot stores the watchlist path.")
    parser.add_argument("--interval", default="1day")
    parser.add_argument("--date", help="Pre-market report date in YYYY-MM-DD. Defaults to today in America/New_York.")
    parser.add_argument("--snapshot-date", help="Completed trading date to use. Defaults to previous trading day.")
    parser.add_argument("--timezone", default=MARKET_TIMEZONE)
    parser.add_argument("--skip-non-trading-day", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    report_date = resolve_market_date(args.date, timezone=args.timezone)
    guard = trading_day_status(report_date)
    if args.skip_non_trading_day and not guard["is_trading_day"]:
        print(json.dumps({"skipped": True, "guard": guard}, ensure_ascii=False, indent=2))
        return

    source_date = resolve_market_date(args.snapshot_date, timezone=args.timezone) if args.snapshot_date else previous_trading_day(report_date)
    source_path = snapshot_path(repo_root, source_date.isoformat(), args.interval)
    if not source_path.exists():
        raise FileNotFoundError(
            f"Missing market snapshot: {source_path}. "
            f"Run: python3 script/prepare_market_snapshot.py --date {source_date.isoformat()} --skip-non-trading-day"
        )

    snapshot = json.loads(source_path.read_text(encoding="utf-8"))
    context = {
        "report_date": report_date.isoformat(),
        "session": "pre-market",
        "source_snapshot_date": source_date.isoformat(),
        "source_snapshot_path": str(source_path),
        "trading_day": guard,
        "snapshot": snapshot,
    }

    out_dir = repo_root / "report" / report_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    context_path = out_dir / "pre-market-context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(
        {
            "context_path": str(context_path),
            "report_date": context["report_date"],
            "source_snapshot_date": context["source_snapshot_date"],
            "symbols": len(snapshot.get("symbols", [])),
            "errors": len(snapshot.get("errors", [])),
            "stale_data": snapshot.get("stale_data", False),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
