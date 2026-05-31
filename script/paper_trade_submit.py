#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from longbridge_paper_order_adapter import LongbridgePaperOrderAdapter
from paper_execution_config import broker_capability_matrix, load_paper_execution_config, paper_execution_policy
from paper_order_models import build_order_intent, paper_order_record
from paper_risk_guard import RiskGuardConfig, evaluate_order_intent, load_submitted_intent_ids
from signal_artifacts import read_json
from validate_trade_plan import validate as validate_trade_plan


def default_preview_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / date / "paper-trade-preview.json"


def default_account_snapshot_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "runtime" / "paper" / date / "paper-account-snapshot.json"


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "report" / date / "paper-trade-submission.json"


def default_orders_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "runtime" / "paper" / date / "paper-orders.jsonl"


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def validation_result(repo_root: Path, date: str, session: str, signals: str | None) -> dict[str, Any]:
    args = argparse.Namespace(date=date, session=session, signals=signals, repo_root=str(repo_root))
    result = validate_trade_plan(args)
    if result["status"] != "pass":
        raise ValueError(f"trade plan validation failed: {result['errors']}")
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    preview_path = default_preview_path(repo_root, args.date, args.preview)
    account_path = default_account_snapshot_path(repo_root, args.date, args.account_snapshot)
    output = default_output_path(repo_root, args.date, args.output)
    orders_path = default_orders_path(repo_root, args.date, args.orders_journal)
    paper_execution_config, paper_execution_config_path = load_paper_execution_config(
        repo_root,
        getattr(args, "paper_execution_config", None),
    )
    validation = validation_result(repo_root, args.date, args.session, args.signals) if args.require_validation else None
    preview = read_json(preview_path)
    account_snapshot = read_json(account_path)
    orders = preview.get("orders")
    if not isinstance(orders, list):
        raise ValueError(f"{preview_path}: orders must be an array")

    submitted_intent_ids = load_submitted_intent_ids(orders_path)
    config = RiskGuardConfig(max_daily_risk_pct=args.max_daily_risk_pct, max_daily_orders=args.max_daily_orders)
    ready: list[dict[str, Any]] = []
    submitted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    skipped_duplicates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    adapter: LongbridgePaperOrderAdapter | None = None

    for order in orders:
        if not isinstance(order, dict):
            continue
        try:
            intent = build_order_intent(date=args.date, session=args.session, preview=order)
        except Exception as exc:
            blocked.append({"preview": order, "risk_guard": {"passed": False, "errors": [str(exc)], "warnings": []}})
            continue
        if intent["intent_id"] in submitted_intent_ids:
            skipped_duplicates.append({"intent": intent, "reason": "intent_id was already submitted"})
            continue
        result = evaluate_order_intent(
            intent,
            account_snapshot=account_snapshot,
            submitted_intent_ids=submitted_intent_ids,
            config=config,
        )
        if result["passed"]:
            if not args.execute:
                ready.append({"intent": intent, "risk_guard": result})
                continue
            if adapter is None:
                adapter = LongbridgePaperOrderAdapter(
                    cli=args.longbridge_cli,
                    paper_execution_config=paper_execution_config,
                )
            try:
                submit_result = adapter.submit_limit_order(intent, execute=True)
                record = paper_order_record(
                    intent=intent,
                    submit_status="submitted",
                    broker_order_id=submit_result.get("broker_order_id"),
                    raw_request=submit_result.get("raw_request") if isinstance(submit_result.get("raw_request"), dict) else {},
                    raw_response=submit_result.get("raw_response") if isinstance(submit_result.get("raw_response"), dict) else {},
                    dry_run=False,
                )
                if submit_result.get("account_channel"):
                    record["account_channel"] = submit_result["account_channel"]
                append_jsonl(orders_path, record)
                submitted_intent_ids.add(intent["intent_id"])
                submitted.append(record)
            except Exception as exc:
                errors.append({"intent": intent, "error": str(exc)})
        else:
            blocked.append({"intent": intent, "risk_guard": result})

    payload = {
        "date": args.date,
        "session": args.session,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": not args.execute,
        "execute_requested": bool(args.execute),
        "source_preview": str(preview_path),
        "source_account_snapshot": str(account_path),
        "orders_journal": str(orders_path),
        "paper_execution_config": str(paper_execution_config_path),
        "execution_policy": paper_execution_policy(paper_execution_config),
        "broker_capabilities": broker_capability_matrix(paper_execution_config),
        "validation": validation,
        "ready": ready,
        "submitted": submitted,
        "blocked": blocked,
        "skipped_duplicates": skipped_duplicates,
        "errors": errors,
        "summary": {
            "total": len(orders),
            "ready": len(ready),
            "submitted": len(submitted),
            "blocked": len(blocked),
            "skipped_duplicates": len(skipped_duplicates),
            "errors": len(errors),
        },
        "safety_note": (
            "Dry-run only; no broker write APIs were called."
            if not args.execute
            else "Executed paper submissions through guarded Longbridge paper adapter."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "dry_run": payload["dry_run"], "summary": payload["summary"]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare controlled paper order submissions from dry-run previews")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    parser.add_argument("--preview")
    parser.add_argument("--account-snapshot")
    parser.add_argument("--orders-journal")
    parser.add_argument("--signals")
    parser.add_argument("--output")
    parser.add_argument("--require-validation", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Submit passing intents through the guarded paper adapter")
    parser.add_argument("--longbridge-cli")
    parser.add_argument("--paper-execution-config")
    parser.add_argument("--max-daily-risk-pct", type=float, default=3.0)
    parser.add_argument("--max-daily-orders", type=int, default=3)
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
