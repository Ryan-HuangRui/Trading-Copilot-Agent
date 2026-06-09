#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def display_time(value: datetime, timezone_name: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%H:%M %Z")


def source_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    value = payload.get("summary")
    return value if isinstance(value, dict) else {}


def int_value(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value))
            except ValueError:
                continue
    return 0


def line(label: str, pieces: list[tuple[str, int]]) -> str:
    return f"- {label}：" + "；".join(f"{name}={value}" for name, value in pieces)


def append_markdown(path: Path, date: str, section: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else f"# {date} Intraday Tracker\n\n"
    if previous and not previous.endswith("\n"):
        previous += "\n"
    path.write_text(previous + section + "\n", encoding="utf-8")


def build_payloads(repo_root: Path, date: str) -> dict[str, tuple[Path, dict[str, Any] | None]]:
    report_dir = repo_root / "report" / date
    paper_dir = repo_root / "runtime" / "paper" / date
    paths = {
        "paper_execution_state": paper_dir / "paper-execution-state.json",
        "paper_order_cancel": report_dir / "paper-order-cancel-plan.json",
        "paper_order_replace": report_dir / "paper-replace-plan.json",
        "paper_protective_stop_plan": report_dir / "paper-protective-stop-plan.json",
        "paper_take_profit_plan": report_dir / "paper-take-profit-plan.json",
        "paper_exit_plan": report_dir / "paper-exit-plan.json",
        "paper_break_even_stop_plan": report_dir / "paper-break-even-stop-plan.json",
        "paper_event_ledger": report_dir / "paper-event-ledger.json",
        "paper_execution_review": report_dir / "paper-execution-review.json",
    }
    return {name: (path, read_json(path)) for name, path in paths.items()}


def summarize(payloads: dict[str, tuple[Path, dict[str, Any] | None]]) -> dict[str, int]:
    sync = summary(payloads["paper_execution_state"][1])
    cancel = summary(payloads["paper_order_cancel"][1])
    replace = summary(payloads["paper_order_replace"][1])
    stop = summary(payloads["paper_protective_stop_plan"][1])
    take_profit = summary(payloads["paper_take_profit_plan"][1])
    exit_plan = summary(payloads["paper_exit_plan"][1])
    break_even = summary(payloads["paper_break_even_stop_plan"][1])
    ledger = summary(payloads["paper_event_ledger"][1])
    review = summary(payloads["paper_execution_review"][1])
    return {
        "submitted_orders": int_value(sync, "submitted"),
        "filled_orders": int_value(sync, "filled"),
        "cancelled_orders": int_value(sync, "cancelled"),
        "cancel_candidates": int_value(cancel, "cancel_candidates", "candidates"),
        "cancelled": int_value(cancel, "cancelled", "submitted"),
        "cancel_errors": int_value(cancel, "errors"),
        "replace_candidates": int_value(replace, "replace_candidates", "candidates"),
        "replaced": int_value(replace, "replaced", "submitted"),
        "replace_errors": int_value(replace, "errors"),
        "protective_stop_candidates": int_value(stop, "stop_candidates", "candidates"),
        "protective_stop_submitted": int_value(stop, "submitted"),
        "protective_stop_errors": int_value(stop, "errors"),
        "take_profit_candidates": int_value(take_profit, "take_profit_candidates", "candidates"),
        "take_profit_submitted": int_value(take_profit, "submitted"),
        "take_profit_blocked": int_value(take_profit, "blocked"),
        "exit_candidates": int_value(exit_plan, "exit_candidates", "candidates"),
        "exit_submitted": int_value(exit_plan, "submitted"),
        "exit_blocked": int_value(exit_plan, "blocked"),
        "break_even_move_candidates": int_value(break_even, "move_candidates", "candidates"),
        "break_even_moved": int_value(break_even, "moved", "submitted"),
        "break_even_blocked": int_value(break_even, "blocked"),
        "ledger_events": int_value(ledger, "events"),
        "review_orders": int_value(review, "orders"),
        "review_fills": int_value(review, "fills"),
    }


def should_notify(counts: dict[str, int]) -> bool:
    keys = (
        "cancel_candidates",
        "cancelled",
        "cancel_errors",
        "replace_candidates",
        "replaced",
        "replace_errors",
        "protective_stop_candidates",
        "protective_stop_submitted",
        "protective_stop_errors",
        "take_profit_candidates",
        "take_profit_submitted",
        "exit_candidates",
        "exit_submitted",
        "break_even_move_candidates",
        "break_even_moved",
        "ledger_events",
    )
    return any(counts.get(key, 0) > 0 for key in keys)


def build_section(
    *,
    date: str,
    as_of: datetime,
    timezone_name: str,
    payloads: dict[str, tuple[Path, dict[str, Any] | None]],
    counts: dict[str, int],
    repo_root: Path,
) -> str:
    artifacts = [source_path(path, repo_root) for path, payload in payloads.values() if payload]
    lines = [
        f"## {display_time(as_of, timezone_name)} 模拟盘生命周期",
        "- 来源：" + ("；".join(artifacts) if artifacts else "missing"),
        line(
            "订单同步",
            [
                ("submitted", counts["submitted_orders"]),
                ("filled", counts["filled_orders"]),
                ("cancelled", counts["cancelled_orders"]),
            ],
        ),
        line(
            "撤单",
            [
                ("candidates", counts["cancel_candidates"]),
                ("cancelled", counts["cancelled"]),
                ("errors", counts["cancel_errors"]),
            ],
        ),
        line(
            "改单",
            [
                ("candidates", counts["replace_candidates"]),
                ("replaced", counts["replaced"]),
                ("errors", counts["replace_errors"]),
            ],
        ),
        line(
            "保护止损",
            [
                ("candidates", counts["protective_stop_candidates"]),
                ("submitted", counts["protective_stop_submitted"]),
                ("errors", counts["protective_stop_errors"]),
            ],
        ),
        line(
            "止盈",
            [
                ("candidates", counts["take_profit_candidates"]),
                ("submitted", counts["take_profit_submitted"]),
                ("blocked", counts["take_profit_blocked"]),
            ],
        ),
        line(
            "完整退出",
            [
                ("candidates", counts["exit_candidates"]),
                ("submitted", counts["exit_submitted"]),
                ("blocked", counts["exit_blocked"]),
            ],
        ),
        line(
            "保本止损",
            [
                ("candidates", counts["break_even_move_candidates"]),
                ("moved", counts["break_even_moved"]),
                ("blocked", counts["break_even_blocked"]),
            ],
        ),
        line("事件与复盘", [("ledger_events", counts["ledger_events"]), ("review_orders", counts["review_orders"]), ("review_fills", counts["review_fills"])]),
        "- 说明：本段为模拟盘生命周期审计记录，不构成真实账户交易指令。",
    ]
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    markdown_path = resolve_path(repo_root, args.markdown, repo_root / "report" / args.date / "intraday.md")
    output_path = resolve_path(repo_root, args.output, repo_root / "report" / args.date / "intraday-lifecycle-summary.json")
    payloads = build_payloads(repo_root, args.date)
    existing = {name: source_path(path, repo_root) for name, (path, payload) in payloads.items() if payload}
    if not existing:
        return {
            "status": "skipped",
            "workflow": "intraday-lifecycle-append",
            "date": args.date,
            "reason": "lifecycle artifacts missing",
            "markdown": str(markdown_path),
            "output": str(output_path),
        }

    counts = summarize(payloads)
    as_of = parse_as_of(args.as_of)
    section = build_section(
        date=args.date,
        as_of=as_of,
        timezone_name=args.timezone,
        payloads=payloads,
        counts=counts,
        repo_root=repo_root,
    )
    append_markdown(markdown_path, args.date, section)
    output = {
        "status": "success",
        "workflow": "intraday-lifecycle-append",
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "markdown": str(markdown_path),
        "artifacts": existing,
        "summary": counts,
        "should_notify": should_notify(counts),
        "safety_note": "Read-only intraday paper lifecycle summary. This workflow does not call broker APIs.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append paper lifecycle status into the daily intraday Markdown log")
    parser.add_argument("--date", required=True)
    parser.add_argument("--markdown")
    parser.add_argument("--output")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--as-of")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        payload = run(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "workflow": "intraday-lifecycle-append", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
