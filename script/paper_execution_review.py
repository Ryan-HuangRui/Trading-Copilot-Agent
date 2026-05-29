#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signal_artifacts import read_json


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def default_preview_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-trade-preview.json")


def default_state_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "runtime" / "paper" / date / "paper-execution-state.json")


def default_json_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-execution-review.json")


def default_md_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-execution-review.md")


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_quantity(value: Any) -> float:
    return as_float(value) or 0.0


def round_or_none(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def preview_by_signal_id(preview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    orders = preview.get("orders") if isinstance(preview.get("orders"), list) else []
    indexed: dict[str, dict[str, Any]] = {}
    for order in orders:
        if not isinstance(order, dict):
            continue
        signal_id = str(order.get("signal_id") or "")
        if signal_id:
            indexed[signal_id] = order
    return indexed


def first_by_intent(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        intent_id = str(item.get("intent_id") or "")
        if intent_id and intent_id not in indexed:
            indexed[intent_id] = item
    return indexed


def risk_per_share(entry: float | None, stop: float | None, side: str) -> float | None:
    if entry is None or stop is None:
        return None
    if side == "buy":
        risk = entry - stop
    else:
        risk = stop - entry
    return round(risk, 4) if risk > 0 else None


def planned_rr(entry: float | None, stop: float | None, take_profit: float | None, side: str) -> float | None:
    risk = risk_per_share(entry, stop, side)
    if risk is None or take_profit is None or entry is None:
        return None
    reward = take_profit - entry if side == "buy" else entry - take_profit
    if reward <= 0:
        return None
    return round(reward / risk, 4)


def slippage_pct(planned_entry: float | None, actual_entry: float | None, side: str) -> float | None:
    if planned_entry is None or actual_entry is None or planned_entry == 0:
        return None
    raw = (actual_entry - planned_entry) / planned_entry * 100
    return round(raw if side == "buy" else -raw, 4)


def execution_result_r(actual_entry: float | None, exit_price: float | None, planned_risk: float | None, side: str) -> float | None:
    if actual_entry is None or exit_price is None or planned_risk is None or planned_risk <= 0:
        return None
    result = exit_price - actual_entry if side == "buy" else actual_entry - exit_price
    return round(result / planned_risk, 4)


def outcome_from_exits(entry: dict[str, Any], stop: dict[str, Any] | None, take_profit: dict[str, Any] | None) -> tuple[str, float | None]:
    tp_status = str((take_profit or {}).get("status") or entry.get("tp1_status") or "").lower()
    stop_status = str((stop or {}).get("status") or entry.get("stop_status") or "").lower()
    if tp_status == "filled":
        return "tp1_filled", as_float((take_profit or {}).get("avg_fill_price")) or as_float(entry.get("take_profit"))
    if stop_status == "filled":
        return "stop_filled", as_float((stop or {}).get("avg_fill_price")) or as_float(entry.get("current_stop_price")) or as_float(entry.get("stop_price"))
    if str(entry.get("status") or "").lower() == "filled":
        return "open_after_entry", None
    return str(entry.get("status") or "unknown"), None


def review_order(entry: dict[str, Any], preview: dict[str, Any] | None, stop: dict[str, Any] | None, take_profit: dict[str, Any] | None) -> dict[str, Any]:
    side = str(entry.get("side") or (preview or {}).get("side") or "buy").lower()
    planned_entry = as_float((preview or {}).get("entry_price")) or as_float(entry.get("limit_price"))
    planned_stop = as_float((preview or {}).get("stop_price")) or as_float(entry.get("stop_price"))
    planned_tp = as_float((preview or {}).get("take_profit")) or as_float(entry.get("take_profit"))
    actual_entry = as_float(entry.get("avg_fill_price")) or planned_entry
    risk = risk_per_share(planned_entry, planned_stop, side)
    outcome, exit_price = outcome_from_exits(entry, stop, take_profit)
    lessons: list[str] = []

    plan_adherence = "passed"
    if str(entry.get("status") or "") != "filled":
        plan_adherence = "not_filled"
    elif as_quantity(entry.get("filled_quantity")) > as_quantity(entry.get("quantity")):
        plan_adherence = "overfilled"

    slip = slippage_pct(planned_entry, actual_entry, side)
    fill_quality = "not_available" if slip is None else ("slippage_ok" if abs(slip) <= 0.5 else "slippage_high")
    if fill_quality == "slippage_high":
        lessons.append("review entry timing and limit placement; slippage exceeded 0.5%")

    risk_discipline = "passed"
    if planned_stop is None or risk is None:
        risk_discipline = "missing_or_invalid_stop"
        lessons.append("paper plan was missing a valid initial stop")
    elif str(entry.get("status") or "") == "filled" and not entry.get("protective_stop_order_id"):
        risk_discipline = "missing_protective_stop"
        lessons.append("filled paper entry had no synced protective stop")

    if outcome == "open_after_entry" and str(entry.get("tp1_status") or "") != "filled":
        lessons.append("paper position remains open; defer final R review until exit evidence exists")

    return {
        "intent_id": entry.get("intent_id"),
        "source_signal_id": entry.get("source_signal_id"),
        "symbol": entry.get("symbol"),
        "setup": entry.get("setup") or (preview or {}).get("setup"),
        "side": side,
        "quantity": entry.get("quantity"),
        "filled_quantity": entry.get("filled_quantity"),
        "status": entry.get("status"),
        "outcome": outcome,
        "planned_entry": planned_entry,
        "actual_entry": actual_entry,
        "planned_stop": planned_stop,
        "planned_take_profit": planned_tp,
        "exit_price": exit_price,
        "risk_per_share": risk,
        "planned_rr": planned_rr(planned_entry, planned_stop, planned_tp, side),
        "slippage_pct": slip,
        "result_r": execution_result_r(actual_entry, exit_price, risk, side),
        "mfe_r": None,
        "mae_r": None,
        "mfe_mae_note": "not_available_without_intraday_path_or_mark_data",
        "plan_adherence": plan_adherence,
        "fill_quality": fill_quality,
        "risk_discipline": risk_discipline,
        "protective_stop_status": (stop or {}).get("status") or entry.get("stop_status"),
        "tp1_status": (take_profit or {}).get("status") or entry.get("tp1_status"),
        "candidate_lessons": lessons,
    }


def render_markdown(date: str, reviews: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        f"# Paper Execution Review - {date}",
        "",
        "## Summary",
        f"- Total: {summary['total']}",
        f"- Filled: {summary['filled']}",
        f"- Open after entry: {summary['open_after_entry']}",
        f"- TP1 filled: {summary['tp1_filled']}",
        f"- Stop filled: {summary['stop_filled']}",
        f"- Average result R: {summary['average_result_r'] if summary['average_result_r'] is not None else 'N/A'}",
        "",
        "## Orders",
    ]
    for item in reviews:
        result = item["result_r"] if item["result_r"] is not None else "N/A"
        slippage = item["slippage_pct"] if item["slippage_pct"] is not None else "N/A"
        lines.extend(
            [
                f"- {item.get('symbol')} / {item.get('intent_id')}: outcome={item.get('outcome')}, result_r={result}, slippage_pct={slippage}, risk={item.get('risk_discipline')}",
            ]
        )
        for lesson in item.get("candidate_lessons") or []:
            lines.append(f"  - Lesson candidate: {lesson}")
    lines.extend(["", "Safety note: paper execution review only; this report does not modify trading rules or place orders.", ""])
    return "\n".join(lines)


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    preview_path = default_preview_path(repo_root, args.date, args.preview)
    state_path = default_state_path(repo_root, args.date, args.execution_state)
    json_output = default_json_output_path(repo_root, args.date, args.output)
    md_output = default_md_output_path(repo_root, args.date, args.markdown_output)
    preview = read_json(preview_path)
    state = read_json(state_path)
    entries = state.get("orders") if isinstance(state.get("orders"), list) else []
    stops = state.get("protective_stops") if isinstance(state.get("protective_stops"), list) else []
    take_profits = state.get("take_profit_orders") if isinstance(state.get("take_profit_orders"), list) else []
    previews = preview_by_signal_id(preview)
    stops_by_intent = first_by_intent([item for item in stops if isinstance(item, dict)])
    take_profits_by_intent = first_by_intent([item for item in take_profits if isinstance(item, dict)])

    reviews = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        intent_id = str(entry.get("intent_id") or "")
        signal_id = str(entry.get("source_signal_id") or "")
        reviews.append(
            review_order(
                entry,
                previews.get(signal_id),
                stops_by_intent.get(intent_id),
                take_profits_by_intent.get(intent_id),
            )
        )
    result_values = [item["result_r"] for item in reviews if isinstance(item.get("result_r"), (int, float))]
    summary = {
        "total": len(reviews),
        "filled": len([item for item in reviews if item.get("status") == "filled"]),
        "open_after_entry": len([item for item in reviews if item.get("outcome") == "open_after_entry"]),
        "tp1_filled": len([item for item in reviews if item.get("outcome") == "tp1_filled"]),
        "stop_filled": len([item for item in reviews if item.get("outcome") == "stop_filled"]),
        "average_result_r": average(result_values),
        "candidate_lessons": sum(len(item.get("candidate_lessons") or []) for item in reviews),
    }
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_preview": str(preview_path),
        "source_execution_state": str(state_path),
        "summary": summary,
        "reviews": reviews,
        "safety_note": "Paper execution review only. Candidate lessons are not promoted into refined rules.",
    }
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.write_text(render_markdown(args.date, reviews, summary), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(json_output), "markdown": str(md_output), "summary": summary}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "preview": None,
        "execution_state": None,
        "output": None,
        "markdown_output": None,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review paper execution quality from synced paper order state")
    parser.add_argument("--date", required=True)
    parser.add_argument("--preview")
    parser.add_argument("--execution-state")
    parser.add_argument("--output")
    parser.add_argument("--markdown-output")
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
