#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from longbridge_paper_order_adapter import LongbridgePaperOrderAdapter
from paper_execution_config import load_paper_execution_config
from paper_order_models import normalize_order_type, normalize_tif


DEFAULT_CONFIG = {
    "allow_experimental_micro_paper": False,
    "max_notional": 100.0,
    "max_loss": 5.0,
    "max_daily_trades": 1,
    "max_per_symbol_per_day": 1,
    "time_stop": "same_day_close",
    "default_market": "US",
    "order_type": "LO",
    "tif": "day",
}

OBSERVATION_STATES = {"price_touched", "daily_range_touched_both_order_unknown"}
REVIEW_STATUSES = {"no_trade", "watch_only"}


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def resolve_path(repo_root: Path, value: str | None, default: Path) -> Path:
    if value:
        path = Path(value)
        return path if path.is_absolute() else repo_root / path
    return repo_root / default


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def longbridge_symbol(symbol: str, default_market: str) -> str:
    clean = normalize_symbol(symbol)
    if "." in str(symbol or ""):
        return str(symbol).strip().upper()
    return f"{clean}.{default_market.upper()}"


def load_config(path: Path) -> dict[str, Any]:
    raw = read_json(path, {})
    if isinstance(raw.get("experimental_micro_paper"), dict):
        raw = raw["experimental_micro_paper"]
    config = dict(DEFAULT_CONFIG)
    for key, default_value in DEFAULT_CONFIG.items():
        value = raw.get(key)
        if isinstance(default_value, bool):
            config[key] = bool(value) if isinstance(value, bool) else default_value
        elif isinstance(default_value, int):
            config[key] = int(value) if isinstance(value, int) and value > 0 else default_value
        elif isinstance(default_value, float):
            number = to_float(value)
            config[key] = float(number) if number is not None and number > 0 else default_value
        else:
            text = str(value or "").strip()
            config[key] = text or default_value
    config["order_type"] = normalize_order_type(config["order_type"])
    config["tif"] = normalize_tif(config["tif"])
    return config


def signals_by_symbol(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    signals = payload.get("signals")
    if not isinstance(signals, list):
        return result
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        symbol = normalize_symbol(signal.get("symbol"))
        if symbol:
            result[symbol] = signal
    return result


def existing_learning_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def learning_signal_id(date: str, symbol: str, bar_timestamp: Any) -> str:
    digest = hashlib.sha1(f"{date}|{symbol}|{bar_timestamp or ''}|experimental_micro_paper".encode("utf-8")).hexdigest()[:12]
    return f"learn-{date}-{symbol}-{digest}"


def intent_id_for(candidate: dict[str, Any]) -> str:
    raw = f"{candidate['date']}|experimental_micro_paper|{candidate['symbol']}|{candidate['learning_signal_id']}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{candidate['date']}:experimental_micro_paper:{candidate['symbol']}:{digest}"


def data_quality_label(payload: dict[str, Any], state: dict[str, Any]) -> str:
    if state.get("state") == "stale":
        return "stale"
    if payload:
        return "stale" if payload.get("stale_data") else "fresh"
    return "fresh"


def block(symbol: str, reason: str) -> dict[str, Any]:
    return {"symbol": symbol, "reason": reason}


def build_candidates(
    *,
    date: str,
    state_payload: dict[str, Any],
    monitor_signals: dict[str, Any],
    data_quality: dict[str, Any],
    config: dict[str, Any],
    journal_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    symbols = state_payload.get("symbols") if isinstance(state_payload.get("symbols"), dict) else {}
    signals = signals_by_symbol(monitor_signals)
    submitted_today = [record for record in journal_records if record.get("date") == date and record.get("mode") == "experimental_micro_paper"]
    symbol_counts: dict[str, int] = {}
    for record in submitted_today:
        symbol_counts[normalize_symbol(record.get("symbol"))] = symbol_counts.get(normalize_symbol(record.get("symbol")), 0) + 1

    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    if not config["allow_experimental_micro_paper"]:
        return [], [block("*", "allow_experimental_micro_paper is false")]
    if len(submitted_today) >= int(config["max_daily_trades"]):
        return [], [block("*", "daily experimental micro paper limit reached")]

    for symbol, state in sorted(symbols.items()):
        clean = normalize_symbol(symbol)
        if not clean or not isinstance(state, dict):
            continue
        if symbol_counts.get(clean, 0) >= int(config["max_per_symbol_per_day"]):
            blocked.append(block(clean, "symbol daily experimental micro paper limit reached"))
            continue
        if state.get("state") not in OBSERVATION_STATES:
            continue
        signal = signals.get(clean)
        status = str((signal or {}).get("execution_status") or "")
        plan_type = str((signal or {}).get("plan_type") or "")
        if status == "conditional_executable" or plan_type == "trade_plan":
            blocked.append(block(clean, "formal trade candidate must use formal paper path"))
            continue
        if status not in REVIEW_STATUSES:
            blocked.append(block(clean, "codex review is missing or not no_trade/watch_only"))
            continue
        entry = to_float(state.get("trigger_price"))
        invalidation = to_float(state.get("invalidation_price"))
        if entry is None or entry <= 0:
            blocked.append(block(clean, "entry_reference is required"))
            continue
        if invalidation is None or invalidation <= 0:
            blocked.append(block(clean, "invalidation_reference is required"))
            continue
        risk_per_share = entry - invalidation
        if risk_per_share <= 0:
            blocked.append(block(clean, "entry_reference must be above invalidation_reference"))
            continue
        quality = data_quality_label(data_quality, state)
        if quality != "fresh":
            blocked.append(block(clean, "data_quality is not fresh"))
            continue

        max_notional = float(config["max_notional"])
        max_loss = float(config["max_loss"])
        quantity = math.floor(min(max_notional / entry, max_loss / risk_per_share))
        if quantity <= 0:
            blocked.append(block(clean, "computed quantity is zero under micro limits"))
            continue
        estimated_notional = round(quantity * entry, 4)
        estimated_max_loss = round(quantity * risk_per_share, 4)
        if estimated_notional > max_notional or estimated_max_loss > max_loss:
            blocked.append(block(clean, "micro limits exceeded"))
            continue

        level_source = state.get("level_source")
        reason = "price_touched_plan_trigger_but_codex_no_trade" if level_source == "pre_market_plan" else "price_touched_reference_but_codex_no_trade"
        candidates.append(
            {
                "mode": "experimental_micro_paper",
                "date": date,
                "learning_signal_id": learning_signal_id(date, clean, state.get("bar_timestamp")),
                "symbol": clean,
                "longbridge_symbol": longbridge_symbol(clean, str(config["default_market"])),
                "reason": reason,
                "hypothesis": "测试盘前或参考 trigger 被触及时，后续 30-90 分钟是否有延续性",
                "entry_reference": entry,
                "invalidation_reference": invalidation,
                "max_notional": max_notional,
                "max_loss": max_loss,
                "time_stop": str(config["time_stop"]),
                "level_source": level_source,
                "data_quality": quality,
                "not_for_formal_stats": True,
                "post_review_label": None,
                "source_state": state.get("state"),
                "source_bar_timestamp": state.get("bar_timestamp"),
                "codex_review_status": status,
                "codex_review_plan_type": plan_type,
                "quantity": quantity,
                "estimated_notional": estimated_notional,
                "estimated_max_loss": estimated_max_loss,
                "risk_per_share": round(risk_per_share, 4),
                "order_type": str(config["order_type"]),
                "tif": str(config["tif"]),
                "status": "ready",
            }
        )
        if len(candidates) + len(submitted_today) >= int(config["max_daily_trades"]):
            break
    return candidates, blocked


def build_intent(candidate: dict[str, Any]) -> dict[str, Any]:
    intent_id = intent_id_for(candidate)
    return {
        "kind": "paper_order_intent",
        "intent_id": intent_id,
        "idempotency_key": intent_id,
        "source_signal_id": candidate["learning_signal_id"],
        "date": candidate["date"],
        "session": "experimental_micro_paper",
        "symbol": candidate["symbol"],
        "longbridge_symbol": candidate["longbridge_symbol"],
        "setup": "experimental_micro_paper",
        "side": "buy",
        "order_type": candidate["order_type"],
        "quantity": candidate["quantity"],
        "limit_price": candidate["entry_reference"],
        "reference_price": candidate["entry_reference"],
        "trigger_price": candidate["entry_reference"],
        "trailing_amount": None,
        "trailing_percent": None,
        "limit_offset": None,
        "stop_price": candidate["invalidation_reference"],
        "take_profit": None,
        "risk_per_share": candidate["risk_per_share"],
        "max_account_risk_pct": None,
        "estimated_account_risk": candidate["estimated_max_loss"],
        "estimated_notional": candidate["estimated_notional"],
        "tif": candidate["tif"],
        "expire_date": None,
        "outside_rth": None,
        "remark": f"tca-learning:{intent_id}",
        "client_order_id": None,
        "status": "ready",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def journal_record(candidate: dict[str, Any], submit_result: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "learning_trade",
        "mode": "experimental_micro_paper",
        "date": candidate["date"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": candidate["symbol"],
        "learning_signal_id": candidate["learning_signal_id"],
        "intent_id": intent["intent_id"],
        "hypothesis": candidate["hypothesis"],
        "reason": candidate["reason"],
        "entry_reference": candidate["entry_reference"],
        "invalidation_reference": candidate["invalidation_reference"],
        "max_notional": candidate["max_notional"],
        "max_loss": candidate["max_loss"],
        "quantity": candidate["quantity"],
        "estimated_notional": candidate["estimated_notional"],
        "estimated_max_loss": candidate["estimated_max_loss"],
        "time_stop": candidate["time_stop"],
        "level_source": candidate["level_source"],
        "data_quality": candidate["data_quality"],
        "not_for_formal_stats": True,
        "post_review_label": None,
        "broker_order_id": submit_result.get("broker_order_id"),
        "account_channel": submit_result.get("account_channel"),
        "raw_request": submit_result.get("raw_request") if isinstance(submit_result.get("raw_request"), dict) else {},
        "raw_response": submit_result.get("raw_response") if isinstance(submit_result.get("raw_response"), dict) else {},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    date = args.date
    config_path = resolve_path(repo_root, args.config, Path("config") / "experimental_micro_paper.json")
    state_path = resolve_path(repo_root, args.state, Path("runtime") / "intraday" / date / "state.json")
    signals_path = resolve_path(repo_root, args.signals, Path("report") / date / "monitor-signals.json")
    data_quality_path = resolve_path(repo_root, args.data_quality, Path("report") / date / "data-quality.json")
    output_path = resolve_path(repo_root, args.output, Path("report") / date / "experimental-micro-paper-preview.json")
    journal_path = resolve_path(repo_root, args.journal, Path("runtime") / "learning" / date / "learning-trade-journal.jsonl")

    config = load_config(config_path)
    journal_records = existing_learning_records(journal_path)
    if args.execute:
        if not output_path.exists():
            raise FileNotFoundError(f"dry-run preview is required before execute: {output_path}")
        if not config["allow_experimental_micro_paper"]:
            raise PermissionError("allow_experimental_micro_paper is false")
        preview = read_json(output_path, {})
        candidates = [row for row in preview.get("candidates", []) if isinstance(row, dict) and row.get("status") == "ready"]
        blocked = []
    else:
        candidates, blocked = build_candidates(
            date=date,
            state_payload=read_json(state_path, {}),
            monitor_signals=read_json(signals_path, {}),
            data_quality=read_json(data_quality_path, {}),
            config=config,
            journal_records=journal_records,
        )
        payload = {
            "status": "success",
            "workflow": "experimental-micro-paper-entry",
            "date": date,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dry_run": True,
            "execute_requested": False,
            "config": str(config_path),
            "source_state": str(state_path),
            "source_signals": str(signals_path),
            "source_data_quality": str(data_quality_path),
            "candidates": candidates,
            "blocked": blocked,
            "summary": {"ready": len(candidates), "blocked": len(blocked), "submitted": 0, "errors": 0},
            "safety_note": "Experimental micro paper preview only; not for formal stats.",
        }
        write_json(output_path, payload)
        return {
            "status": "success",
            "date": date,
            "workflow": "experimental-micro-paper-entry",
            "output": str(output_path),
            "dry_run": True,
            "candidates": candidates,
            "blocked": blocked,
            "summary": payload["summary"],
        }

    submitted = []
    errors = []
    paper_config, paper_config_path = load_paper_execution_config(repo_root, args.paper_execution_config)
    adapter = LongbridgePaperOrderAdapter(cli=args.longbridge_cli, paper_execution_config=paper_config)
    existing_today = [
        record
        for record in journal_records
        if record.get("date") == date and record.get("mode") == "experimental_micro_paper"
    ]
    existing_symbols = {normalize_symbol(record.get("symbol")) for record in existing_today}
    for candidate in candidates:
        if len(existing_today) + len(submitted) >= int(config["max_daily_trades"]):
            blocked.append(block("*", "daily experimental micro paper limit reached"))
            break
        symbol = normalize_symbol(candidate.get("symbol"))
        if symbol in existing_symbols:
            blocked.append(block(symbol, "symbol daily experimental micro paper limit reached"))
            continue
        try:
            intent = build_intent(candidate)
            submit_result = adapter.submit_order(intent, execute=True, action="experimental_micro_paper")
            record = journal_record(candidate, submit_result, intent)
            append_jsonl(journal_path, record)
            submitted.append(record)
            existing_symbols.add(symbol)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    execution_payload = {
        "status": "success",
        "workflow": "experimental-micro-paper-entry",
        "date": date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": False,
        "execute_requested": True,
        "source_preview": str(output_path),
        "journal": str(journal_path),
        "paper_execution_config": str(paper_config_path),
        "submitted": submitted,
        "blocked": blocked,
        "errors": errors,
        "summary": {"ready": len(candidates), "submitted": len(submitted), "blocked": len(blocked), "errors": len(errors)},
        "safety_note": "Executed only through experimental_micro_paper paper gate; records are excluded from formal stats.",
    }
    write_json(output_path, {**read_json(output_path, {}), "last_execution": execution_payload})
    return {
        "status": "success",
        "date": date,
        "workflow": "experimental-micro-paper-entry",
        "output": str(output_path),
        "journal": str(journal_path),
        "dry_run": False,
        "submitted": submitted,
        "blocked": blocked,
        "errors": errors,
        "summary": execution_payload["summary"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Experimental micro paper learning-entry workflow")
    parser.add_argument("--date", required=True)
    parser.add_argument("--config")
    parser.add_argument("--state")
    parser.add_argument("--signals")
    parser.add_argument("--data-quality")
    parser.add_argument("--output")
    parser.add_argument("--journal")
    parser.add_argument("--paper-execution-config")
    parser.add_argument("--longbridge-cli")
    parser.add_argument("--execute", action="store_true")
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
