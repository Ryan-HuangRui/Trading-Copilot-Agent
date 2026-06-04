#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import daily_self_review
import extract_monitor_signals
import extract_report_signals
import feishu_summary
import intraday_lifecycle_append
import journal_review
import learning_review
import longbridge_account_snapshot
import paper_account_snapshot
import paper_break_even_stop_plan
import paper_event_ledger
import paper_execution_review
import paper_exit_plan
import paper_order_sync
import paper_protective_stop_plan
import paper_take_profit_plan
import paper_trade_preview
import paper_trade_review
import paper_trade_submit
import plan_review
import position_review
import validate_report
import validate_trade_plan
import weekly_review


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def broker_order(
    *,
    order_id: str,
    symbol: str = "MU.US",
    side: str,
    quantity: int,
    price: float | None = None,
    status: str = "filled",
    remark: str = "",
) -> dict:
    payload = {
        "order_id": order_id,
        "symbol": symbol,
        "market": "US",
        "side": side,
        "quantity": quantity,
        "price": price,
        "status": status,
        "raw": {"remark": remark} if remark else {},
    }
    return payload


def broker_execution(*, order_id: str, symbol: str = "MU.US", side: str, quantity: int, price: float) -> dict:
    return {
        "order_id": order_id,
        "symbol": symbol,
        "market": "US",
        "side": side,
        "quantity": quantity,
        "price": price,
        "raw": {"order_id": order_id},
    }


def update_paper_snapshot(path: Path, *, orders: list[dict], executions: list[dict]) -> None:
    payload = read_json(path)
    payload["orders"] = orders
    payload["executions"] = executions
    write_json(path, payload)


def seed_submitted_entry(repo_root: Path, date: str, *, preview_path: str) -> tuple[str, Path]:
    preview = read_json(Path(preview_path))
    orders = preview.get("orders") if isinstance(preview.get("orders"), list) else []
    ready = next((item for item in orders if isinstance(item, dict) and item.get("status") == "ready"), None)
    if not ready:
        raise ValueError("paper lifecycle smoke requires one ready preview order")
    intent_id = f"{date}:{preview.get('session', 'pre-market')}:{ready['symbol']}:smoke"
    record = {
        "kind": "paper_order",
        "intent_id": intent_id,
        "source_signal_id": ready.get("signal_id") or "sig-1",
        "date": date,
        "session": preview.get("session") or "pre-market",
        "symbol": ready.get("symbol"),
        "longbridge_symbol": ready.get("longbridge_symbol"),
        "side": ready.get("side"),
        "order_type": ready.get("order_type"),
        "quantity": ready.get("quantity"),
        "entry_price": ready.get("entry_price"),
        "reference_price": ready.get("reference_price"),
        "limit_price": ready.get("entry_price"),
        "trigger_price": ready.get("trigger_price"),
        "trailing_amount": ready.get("trailing_amount"),
        "trailing_percent": ready.get("trailing_percent"),
        "limit_offset": ready.get("limit_offset"),
        "stop_price": ready.get("stop_price"),
        "take_profit": ready.get("take_profit"),
        "risk_per_share": ready.get("risk_per_share"),
        "max_account_risk_pct": ready.get("max_account_risk_pct"),
        "setup": ready.get("setup"),
        "tif": ready.get("tif"),
        "expire_date": ready.get("expire_date"),
        "outside_rth": ready.get("outside_rth"),
        "remark": f"tca:{intent_id}",
        "broker_order_id": "paper-o-1",
        "submit_status": "submitted",
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = repo_root / "runtime" / "paper" / date / "paper-orders.jsonl"
    write_jsonl(path, [record])
    return intent_id, path


def seed_stop_journal(repo_root: Path, date: str, *, intent_id: str) -> Path:
    paper_dir = repo_root / "runtime" / "paper" / date
    stops_path = paper_dir / "paper-stop-orders.jsonl"
    stop_record = {
        "kind": "paper_stop_order",
        "intent_id": intent_id,
        "source_signal_id": "sig-1",
        "entry_broker_order_id": "paper-o-1",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "side": "sell",
        "order_type": "MIT",
        "quantity": 200,
        "trigger_price": 95,
        "tif": "gtc",
        "remark": f"tca-stop:{intent_id}",
        "broker_order_id": "stop-o-1",
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_jsonl(stops_path, [stop_record])
    return stops_path


def seed_take_profit_journal(repo_root: Path, date: str, *, intent_id: str) -> Path:
    take_profit_path = repo_root / "runtime" / "paper" / date / "paper-take-profit-orders.jsonl"
    take_profit_record = {
        "kind": "paper_take_profit_order",
        "intent_id": intent_id,
        "source_signal_id": "sig-1",
        "entry_broker_order_id": "paper-o-1",
        "symbol": "MU",
        "longbridge_symbol": "MU.US",
        "side": "sell",
        "order_type": "LO",
        "quantity": 100,
        "limit_price": 112,
        "take_profit": 112,
        "exit_fraction": 0.5,
        "tif": "gtc",
        "remark": f"tca-tp1:{intent_id}",
        "broker_order_id": "tp-o-1",
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_jsonl(take_profit_path, [take_profit_record])
    return take_profit_path


def run_paper_lifecycle_smoke(repo_root: Path, args: argparse.Namespace, steps: dict) -> None:
    paper_snapshot = steps["paper-account-snapshot"].get("output")
    preview = steps["paper-trade-preview"].get("output")
    if not paper_snapshot or not preview:
        raise ValueError("paper lifecycle smoke requires paper account snapshot and preview outputs")
    intent_id, _orders_path = seed_submitted_entry(repo_root, args.date, preview_path=preview)
    update_paper_snapshot(
        Path(paper_snapshot),
        orders=[
            broker_order(order_id="paper-o-1", side="buy", quantity=200, price=100, status="filled", remark=f"tca:{intent_id}"),
        ],
        executions=[broker_execution(order_id="paper-o-1", side="buy", quantity=200, price=100.2)],
    )
    sync_args = paper_order_sync.build_args(repo_root=str(repo_root), date=args.date, paper_snapshot=paper_snapshot)
    steps["paper-order-sync"] = paper_order_sync.run(sync_args)
    steps["paper-protective-stop-plan"] = paper_protective_stop_plan.run(
        paper_protective_stop_plan.build_args(repo_root=str(repo_root), date=args.date)
    )

    seed_stop_journal(repo_root, args.date, intent_id=intent_id)
    update_paper_snapshot(
        Path(paper_snapshot),
        orders=[
            broker_order(order_id="paper-o-1", side="buy", quantity=200, price=100, status="filled", remark=f"tca:{intent_id}"),
            broker_order(order_id="stop-o-1", side="sell", quantity=200, price=None, status="accepted", remark=f"tca-stop:{intent_id}"),
        ],
        executions=[
            broker_execution(order_id="paper-o-1", side="buy", quantity=200, price=100.2),
        ],
    )
    steps["paper-order-sync"] = paper_order_sync.run(sync_args)
    steps["paper-take-profit-plan"] = paper_take_profit_plan.run(
        paper_take_profit_plan.build_args(repo_root=str(repo_root), date=args.date, resize_stop_before_submit=True)
    )
    seed_take_profit_journal(repo_root, args.date, intent_id=intent_id)
    update_paper_snapshot(
        Path(paper_snapshot),
        orders=[
            broker_order(order_id="paper-o-1", side="buy", quantity=200, price=100, status="filled", remark=f"tca:{intent_id}"),
            broker_order(order_id="stop-o-1", side="sell", quantity=200, price=None, status="accepted", remark=f"tca-stop:{intent_id}"),
            broker_order(order_id="tp-o-1", side="sell", quantity=100, price=112, status="filled", remark=f"tca-tp1:{intent_id}"),
        ],
        executions=[
            broker_execution(order_id="paper-o-1", side="buy", quantity=200, price=100.2),
            broker_execution(order_id="tp-o-1", side="sell", quantity=100, price=112),
        ],
    )
    steps["paper-order-sync"] = paper_order_sync.run(sync_args)
    intraday_state = {
        "date": args.date,
        "symbols": {
            "MU": {
                "symbol": "MU",
                "state": "invalidated",
                "reason": "fixture smoke invalidation",
                "bar_timestamp": f"{args.date} 15:00:00",
            }
        },
    }
    write_json(repo_root / "runtime" / "intraday" / args.date / "state.json", intraday_state)
    steps["paper-exit-plan"] = paper_exit_plan.run(paper_exit_plan.build_args(repo_root=str(repo_root), date=args.date))
    steps["paper-break-even-stop-plan"] = paper_break_even_stop_plan.run(
        paper_break_even_stop_plan.build_args(repo_root=str(repo_root), date=args.date)
    )
    steps["paper-event-ledger"] = paper_event_ledger.run(paper_event_ledger.build_args(repo_root=str(repo_root), date=args.date))
    steps["paper-execution-review"] = paper_execution_review.run(
        paper_execution_review.build_args(repo_root=str(repo_root), date=args.date)
    )
    steps["intraday-lifecycle-append"] = intraday_lifecycle_append.run(
        argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            markdown=None,
            output=None,
            timezone=args.timezone,
            as_of=f"{args.date}T20:00:00+00:00",
        )
    )


def run(args: argparse.Namespace) -> dict:
    repo_root = Path(args.repo_root).resolve()
    steps = {}

    validate_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        session=args.session,
        report=None,
        signals=None,
    )
    validation = validate_report.validate(validate_args)
    steps["validate-report"] = {"validation": validation}
    if validation["status"] != "pass":
        return {"status": "failed", "date": args.date, "steps": steps, "reason": "validate-report failed"}

    plan_validation = validate_trade_plan.validate(validate_args)
    steps["validate-trade-plan"] = {"validation": plan_validation}
    if plan_validation["status"] != "pass":
        return {"status": "failed", "date": args.date, "steps": steps, "reason": "validate-trade-plan failed"}

    extract_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        session=args.session,
        report=None,
        signals=None,
        max_signals=3,
        append=True,
        journal_dir=args.journal_dir,
        require_validation=False,
    )
    steps["extract-report-signals"] = extract_report_signals.extract(extract_args)

    backfill_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        session=None,
        snapshot=None,
        append=True,
        journal_dir=args.journal_dir,
    )
    steps["backfill-signal-outcomes"] = journal_review.backfill(backfill_args)

    account_snapshot_path = None
    if args.account_input:
        account_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            timezone=args.timezone,
            input=args.account_input,
            output=args.account_output,
            longbridge_cli=None,
        )
        steps["account-snapshot"] = longbridge_account_snapshot.run(account_args)
        account_snapshot_path = steps["account-snapshot"].get("output")
    elif args.account_snapshot:
        account_snapshot_path = args.account_snapshot
    else:
        default_account = repo_root / "runtime" / "account" / args.date / "account-snapshot.json"
        if default_account.exists():
            account_snapshot_path = str(default_account)

    if account_snapshot_path:
        position_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            account_snapshot=account_snapshot_path,
            signals=None,
            session=args.session,
            snapshot=None,
            config=args.position_config,
            output=None,
            append=True,
            journal_dir=args.journal_dir,
        )
        steps["position-review"] = position_review.run(position_args)

    if args.paper_input:
        paper_account_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            timezone=args.timezone,
            input=args.paper_input,
            output=args.paper_output,
            longbridge_cli=None,
            paper_execution_config=None,
        )
        steps["paper-account-snapshot"] = paper_account_snapshot.run(paper_account_args)
        paper_preview_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            session=args.session,
            signals=None,
            account_snapshot=steps["paper-account-snapshot"].get("output"),
            output=None,
            require_validation=True,
            default_market="US",
            tif="day",
        )
        steps["paper-trade-preview"] = paper_trade_preview.run(paper_preview_args)
        paper_submit_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            session=args.session,
            preview=steps["paper-trade-preview"].get("output"),
            account_snapshot=steps["paper-account-snapshot"].get("output"),
            orders_journal=None,
            signals=None,
            output=None,
            require_validation=True,
            execute=False,
            longbridge_cli=None,
            max_daily_risk_pct=3.0,
            max_daily_orders=3,
        )
        steps["paper-trade-submit"] = paper_trade_submit.run(paper_submit_args)
        paper_review_args = argparse.Namespace(
            repo_root=str(repo_root),
            date=args.date,
            session=args.session,
            preview=steps["paper-trade-preview"].get("output"),
            paper_snapshot=steps["paper-account-snapshot"].get("output"),
            orders_journal=None,
            output=None,
            append=True,
            journal_dir=args.journal_dir,
        )
        steps["paper-trade-review"] = paper_trade_review.run(paper_review_args)
        if args.paper_lifecycle_smoke:
            run_paper_lifecycle_smoke(repo_root, args, steps)

    daily_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        append=True,
        output=None,
        journal_dir=args.journal_dir,
    )
    steps["daily-self-review"] = daily_self_review.run(daily_args)

    plan_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        append_lessons=True,
        output=None,
        journal_dir=args.journal_dir,
        learning_dir=args.learning_dir,
    )
    steps["plan-review"] = plan_review.run(plan_args)

    learning_args = argparse.Namespace(
        repo_root=str(repo_root),
        lookback_days=20,
        min_count=3,
        end_date=args.date,
        output=None,
        learning_dir=args.learning_dir,
        journal_dir=args.journal_dir,
    )
    steps["learning-review"] = learning_review.run(learning_args)

    feishu_args = argparse.Namespace(
        repo_root=str(repo_root),
        date=args.date,
        session=args.session,
        signals=None,
        position_review=None,
        plan_review=None,
        output=None,
        learning_dir=args.learning_dir,
    )
    steps["feishu-summary"] = feishu_summary.run(feishu_args)

    weekly_args = argparse.Namespace(
        repo_root=str(repo_root),
        week=args.week,
        append=True,
        output=None,
        journal_dir=args.journal_dir,
    )
    steps["weekly-review"] = weekly_review.run(weekly_args)

    monitor_args = argparse.Namespace(
        repo_root=str(repo_root),
        monitor=args.monitor,
        date=args.date,
        timezone="America/New_York",
        max_signals=5,
        append=True,
        journal_dir=args.journal_dir,
    )
    steps["extract-monitor-signals"] = extract_monitor_signals.extract(monitor_args)

    return {"status": "success", "date": args.date, "week": args.week, "steps": steps}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fixture-based workflow smoke test without external data calls")
    parser.add_argument("--date", required=True)
    parser.add_argument("--week", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market"], default="pre-market")
    parser.add_argument("--monitor", default="report/latest-monitor.json")
    parser.add_argument("--account-input", help="Fixture payload for account-snapshot smoke coverage")
    parser.add_argument("--account-output", help="Optional account snapshot output path")
    parser.add_argument("--account-snapshot", help="Existing account snapshot path for position-review smoke coverage")
    parser.add_argument("--paper-input", help="Fixture payload for paper account/submit/review smoke coverage")
    parser.add_argument("--paper-output", help="Optional paper account snapshot output path")
    parser.add_argument("--paper-lifecycle-smoke", action="store_true", help="Exercise fixture-based paper order lifecycle artifacts")
    parser.add_argument("--position-config", help="Optional position review config path")
    parser.add_argument("--journal-dir", default="runtime/journal")
    parser.add_argument("--learning-dir", default="runtime/learning")
    parser.add_argument("--timezone", default="America/New_York")
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
    raise SystemExit(0 if payload["status"] == "success" else 1)


if __name__ == "__main__":
    main()
