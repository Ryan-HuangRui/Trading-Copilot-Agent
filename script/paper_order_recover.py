#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from longbridge_paper_order_adapter import LongbridgePaperOrderAdapter, parse_json_output
from longbridge_paper_trade_adapter import PAPER_ACCOUNT_CHANNEL
from paper_order_models import (
    PRICE_REQUIRED_ORDER_TYPES,
    TRAILING_AMOUNT_REQUIRED_ORDER_TYPES,
    TRAILING_PERCENT_REQUIRED_ORDER_TYPES,
    TRIGGER_PRICE_REQUIRED_ORDER_TYPES,
    build_order_intent,
    normalize_order_type,
    normalize_tif,
    paper_order_record,
)
from paper_order_sync import load_order_records
from signal_artifacts import read_json


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def default_preview_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-trade-preview.json")


def default_snapshot_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-account-snapshot.json")


def default_orders_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-orders.jsonl")


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-order-recover.json")


def longbridge_cli_path(explicit_path: str | None = None) -> str:
    if explicit_path:
        return explicit_path
    return shutil.which("longbridge") or str(Path.home() / ".local" / "bin" / "longbridge")


def run_longbridge_order_detail(cli: str, broker_order_id: str) -> dict[str, Any]:
    proc = subprocess.run(
        [cli, "order", "detail", broker_order_id, "--format", "json"],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"Longbridge CLI failed: {detail}")
    payload = parse_json_output(proc.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("Longbridge order detail did not return an object")
    return payload


def normalize_symbol(value: Any) -> str:
    return str(value or "").upper()


def base_symbol(value: Any) -> str:
    symbol = normalize_symbol(value)
    return symbol.split(".", 1)[0] if "." in symbol else symbol


def normalize_side(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"buy", "b"}:
        return "buy"
    if raw in {"sell", "s"}:
        return "sell"
    return raw


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def quantity_equals(left: Any, right: Any) -> bool:
    left_value = as_float(left)
    right_value = as_float(right)
    if left_value is None or right_value is None:
        return False
    return int(left_value) == int(right_value)


def price_equals(left: Any, right: Any, *, tolerance: float = 0.0001) -> bool:
    left_value = as_float(left)
    right_value = as_float(right)
    if left_value is None or right_value is None:
        return False
    return abs(left_value - right_value) <= tolerance


def detail_order_id(detail: dict[str, Any]) -> str:
    return str(detail.get("order_id") or detail.get("id") or detail.get("broker_order_id") or "").strip()


def detail_symbol(detail: dict[str, Any]) -> str:
    return normalize_symbol(detail.get("symbol") or detail.get("security") or detail.get("code"))


def detail_price(detail: dict[str, Any]) -> Any:
    return detail.get("price") or detail.get("limit_price") or detail.get("submitted_price")


def detail_value(detail: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = detail.get(key)
        if value is not None and value != "":
            return value
    return None


def preview_numeric_value(preview: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = preview.get(key)
        if value is not None and value != "":
            return value
    return None


def optional_numeric_matches(preview: dict[str, Any], detail: dict[str, Any], preview_keys: tuple[str, ...], detail_keys: tuple[str, ...]) -> bool:
    detail_raw = detail_value(detail, *detail_keys)
    if detail_raw is None:
        return True
    preview_raw = preview_numeric_value(preview, *preview_keys)
    return price_equals(preview_raw, detail_raw)


def optional_text_matches(preview_value: Any, detail_value_raw: Any, *, normalize: bool = False) -> bool:
    if detail_value_raw is None or detail_value_raw == "":
        return True
    if normalize:
        return normalize_tif(preview_value) == normalize_tif(detail_value_raw)
    return str(preview_value or "").strip() == str(detail_value_raw or "").strip()


def preview_matches_detail(preview: dict[str, Any], detail: dict[str, Any]) -> bool:
    preview_symbol = normalize_symbol(preview.get("longbridge_symbol") or preview.get("symbol"))
    broker_symbol = detail_symbol(detail)
    if preview_symbol and broker_symbol:
        if preview_symbol != broker_symbol and base_symbol(preview_symbol) != base_symbol(broker_symbol):
            return False
    if normalize_side(preview.get("side")) != normalize_side(detail.get("side")):
        return False
    if not quantity_equals(preview.get("quantity"), detail.get("quantity")):
        return False
    order_type = normalize_order_type(preview.get("order_type"))
    if order_type != normalize_order_type(detail.get("order_type")):
        return False
    if order_type in PRICE_REQUIRED_ORDER_TYPES and not price_equals(preview_numeric_value(preview, "limit_price", "entry_price"), detail_price(detail)):
        return False
    if order_type in TRIGGER_PRICE_REQUIRED_ORDER_TYPES and not optional_numeric_matches(
        preview,
        detail,
        ("trigger_price",),
        ("trigger_price", "trigger", "submitted_trigger_price"),
    ):
        return False
    if order_type in TRAILING_AMOUNT_REQUIRED_ORDER_TYPES and not optional_numeric_matches(
        preview,
        detail,
        ("trailing_amount",),
        ("trailing_amount", "submitted_trailing_amount"),
    ):
        return False
    if order_type in TRAILING_PERCENT_REQUIRED_ORDER_TYPES and not optional_numeric_matches(
        preview,
        detail,
        ("trailing_percent",),
        ("trailing_percent", "submitted_trailing_percent"),
    ):
        return False
    if order_type.startswith("TSLP") and not optional_numeric_matches(
        preview,
        detail,
        ("limit_offset",),
        ("limit_offset", "submitted_limit_offset"),
    ):
        return False
    if not optional_text_matches(preview.get("tif"), detail_value(detail, "tif", "time_in_force"), normalize=True):
        return False
    if not optional_text_matches(preview.get("expire_date"), detail_value(detail, "expire_date", "expiry_date")):
        return False
    if not optional_text_matches(preview.get("outside_rth"), detail_value(detail, "outside_rth", "outside_regular_trading_hours")):
        return False
    return True


def matching_preview_orders(
    preview_payload: dict[str, Any],
    detail: dict[str, Any],
    *,
    intent_id: str | None = None,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    orders = preview_payload.get("orders")
    if not isinstance(orders, list):
        raise ValueError("paper preview orders must be an array")
    matches: list[dict[str, Any]] = []
    for item in orders:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "ready":
            continue
        if symbol and base_symbol(item.get("symbol")) != base_symbol(symbol):
            continue
        if intent_id and str(item.get("intent_id") or "") != intent_id:
            continue
        if preview_matches_detail(item, detail):
            matches.append(item)
    return matches


def ensure_paper_snapshot(snapshot_path: Path) -> dict[str, Any] | None:
    if not snapshot_path.exists():
        return None
    snapshot = read_json(snapshot_path)
    channel = str(snapshot.get("account_channel") or "")
    if channel != PAPER_ACCOUNT_CHANNEL:
        raise ValueError(f"paper account snapshot is not paper trading: {channel or 'unknown'}")
    return snapshot


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def build_recovered_record(
    *,
    date: str,
    session: str,
    preview_order: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    intent = build_order_intent(date=date, session=session, preview=preview_order)
    if intent.get("side") != "buy":
        raise ValueError("paper order recovery currently supports submitted entry buy orders only")
    broker_order_id = detail_order_id(detail)
    if not broker_order_id:
        raise ValueError("broker order detail is missing order_id")
    command = LongbridgePaperOrderAdapter().submit_order_command(intent)
    record = paper_order_record(
        intent=intent,
        submit_status="submitted",
        broker_order_id=broker_order_id,
        raw_request={
            "command": command,
            "intent_id": intent["intent_id"],
            "remark": intent["remark"],
            "recovered_from_broker_order_detail": True,
        },
        raw_response=detail,
        dry_run=False,
    )
    if detail.get("submitted_at"):
        record["submitted_at"] = detail["submitted_at"]
    record["recovered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record["account_channel"] = PAPER_ACCOUNT_CHANNEL
    return record


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    preview_path = default_preview_path(repo_root, args.date, args.preview)
    snapshot_path = default_snapshot_path(repo_root, args.date, args.paper_snapshot)
    orders_path = default_orders_path(repo_root, args.date, args.orders_journal)
    output = default_output_path(repo_root, args.date, args.output)

    ensure_paper_snapshot(snapshot_path)
    if args.order_detail:
        detail_path = Path(args.order_detail)
        detail = read_json(detail_path if detail_path.is_absolute() else repo_root / detail_path)
    else:
        detail = run_longbridge_order_detail(longbridge_cli_path(args.longbridge_cli), args.broker_order_id)

    broker_order_id = detail_order_id(detail)
    if broker_order_id and broker_order_id != str(args.broker_order_id):
        raise ValueError(f"broker order id mismatch: expected {args.broker_order_id}, got {broker_order_id}")

    preview_payload = read_json(preview_path)
    matches = matching_preview_orders(preview_payload, detail, intent_id=args.intent_id, symbol=args.symbol)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one matching ready preview order, got {len(matches)}")

    record = build_recovered_record(date=args.date, session=args.session, preview_order=matches[0], detail=detail)
    existing_records = load_order_records(orders_path)
    existing_intent = [item for item in existing_records if item.get("intent_id") == record["intent_id"]]
    existing_broker = [item for item in existing_records if str(item.get("broker_order_id") or "") == record["broker_order_id"]]
    appended = False
    skipped_reason = None
    if existing_intent:
        skipped_reason = "intent_id already exists in orders journal"
    elif existing_broker:
        skipped_reason = "broker_order_id already exists in orders journal"
    elif args.append:
        append_jsonl(orders_path, record)
        appended = True

    payload = {
        "status": "success",
        "date": args.date,
        "session": args.session,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": not args.append,
        "append_requested": bool(args.append),
        "appended": appended,
        "skipped": bool(skipped_reason),
        "reason": skipped_reason,
        "source_preview": str(preview_path),
        "source_paper_snapshot": str(snapshot_path) if snapshot_path.exists() else None,
        "orders_journal": str(orders_path),
        "broker_order_id": record["broker_order_id"],
        "intent_id": record["intent_id"],
        "record": record,
        "summary": {
            "matched": 1,
            "appended": 1 if appended else 0,
            "skipped_duplicates": 1 if skipped_reason else 0,
        },
        "safety_note": "Recover-only local journal write. This workflow does not submit, cancel, or replace broker orders.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "summary": payload["summary"], "appended": appended}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recover a submitted paper order into the local paper orders journal")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    parser.add_argument("--broker-order-id", required=True)
    parser.add_argument("--preview")
    parser.add_argument("--paper-snapshot")
    parser.add_argument("--orders-journal")
    parser.add_argument("--order-detail", help="Read Longbridge order detail from a JSON fixture instead of CLI")
    parser.add_argument("--symbol", help="Optional symbol disambiguation")
    parser.add_argument("--intent-id", help="Optional intent_id disambiguation")
    parser.add_argument("--output")
    parser.add_argument("--append", action="store_true", help="Append the recovered record to paper-orders.jsonl")
    parser.add_argument("--longbridge-cli")
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
