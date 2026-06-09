#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from argparse import Namespace
from pathlib import Path

import paper_trade_submit


def default_output(repo_root: Path, date: str, explicit_output: str | None) -> str:
    if explicit_output:
        path = Path(explicit_output)
        return str(path if path.is_absolute() else repo_root / path)
    return str(repo_root / "report" / date / "intraday-paper-entry.json")


def run(args: argparse.Namespace) -> dict:
    repo_root = Path(args.repo_root).resolve()
    submit_args = Namespace(
        repo_root=str(repo_root),
        date=args.date,
        session="monitor",
        preview=args.preview,
        account_snapshot=args.account_snapshot,
        orders_journal=args.orders_journal,
        signals=args.signals,
        output=default_output(repo_root, args.date, args.output),
        require_validation=args.require_validation,
        execute=args.execute,
        longbridge_cli=args.longbridge_cli,
        max_daily_risk_pct=args.max_daily_risk_pct,
        max_daily_orders=args.max_daily_orders,
        paper_execution_config=args.paper_execution_config,
        broker_action="intraday_entry_submit",
    )
    result = paper_trade_submit.run(submit_args)
    return {
        "status": result["status"],
        "date": result["date"],
        "workflow": "intraday-paper-entry",
        "output": result["output"],
        "dry_run": result["dry_run"],
        "summary": result["summary"],
        "safety_note": (
            "Dry-run intraday paper entry only."
            if result["dry_run"]
            else "Executed through intraday_entry_submit paper gate against lb_papertrading."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone gated intraday paper-entry workflow")
    parser.add_argument("--date", required=True)
    parser.add_argument("--preview")
    parser.add_argument("--account-snapshot")
    parser.add_argument("--orders-journal")
    parser.add_argument("--signals")
    parser.add_argument("--output")
    parser.add_argument("--longbridge-cli")
    parser.add_argument("--require-validation", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-daily-risk-pct", type=float, default=3.0)
    parser.add_argument("--max-daily-orders", type=int, default=1)
    parser.add_argument("--paper-execution-config")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        payload = run(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
