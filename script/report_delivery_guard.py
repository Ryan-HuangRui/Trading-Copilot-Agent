#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from market_calendar import MARKET_TIMEZONE, resolve_market_date


def main():
    ap = argparse.ArgumentParser(description="Check whether today's report has been delivered")
    ap.add_argument("--state", default="config/delivery_state.json")
    ap.add_argument("--report-dir", default="report")
    ap.add_argument("--mark-sent", action="store_true")
    ap.add_argument("--kind", default="pre-market", choices=["pre-market", "exec-brief", "post-market"])
    ap.add_argument("--date", help="Market date in YYYY-MM-DD. Defaults to today in America/New_York.")
    ap.add_argument("--timezone", default=MARKET_TIMEZONE)
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    today = resolve_market_date(args.date, timezone=args.timezone).isoformat()
    report_path = root / args.report_dir / today / f"{args.kind}.md"
    state_path = root / args.state

    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    sent = state.get("sent", {})
    sent_key = f"{today}:{args.kind}"

    result = {
        "date": today,
        "kind": args.kind,
        "report_exists": report_path.exists(),
        "already_sent": bool(sent.get(sent_key, False)),
        "report_path": str(report_path),
        "should_send": False,
    }

    if result["report_exists"] and not result["already_sent"]:
        result["should_send"] = True

    if args.mark_sent and result["report_exists"]:
        sent[sent_key] = True
        state["sent"] = sent
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        result["marked_sent"] = True

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
