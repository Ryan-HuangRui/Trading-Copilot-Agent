#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
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


def resolve_path(repo_root: Path, value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def relative(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def signal_symbol(signal: dict[str, Any]) -> str:
    return str(signal.get("symbol") or "").upper()


def level_price(container: Any, *keys: str) -> float | None:
    if isinstance(container, dict):
        for key in keys:
            value = to_float(container.get(key))
            if value is not None:
                return value
    return None


def signal_trigger(signal: dict[str, Any]) -> float | None:
    return (
        level_price(signal.get("trigger"), "price", "trigger_price")
        or level_price(signal.get("entry"), "trigger_price", "price")
        or to_float(signal.get("trigger_price"))
    )


def signal_stop(signal: dict[str, Any]) -> float | None:
    return (
        level_price(signal.get("stop"), "initial_stop", "price")
        or level_price(signal.get("invalidation"), "price")
        or to_float(signal.get("stop_price"))
        or to_float(signal.get("invalidation_price"))
    )


def daily_by_symbol(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("symbols")
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("symbol"):
            result[str(row["symbol"]).upper()] = row
    return result


def context_bars_by_symbol(context: dict[str, Any], date: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    scans = context.get("observation_scans")
    if not isinstance(scans, list):
        return result
    for scan in scans:
        if not isinstance(scan, dict) or not scan.get("symbol"):
            continue
        evidence = scan.get("price_evidence") if isinstance(scan.get("price_evidence"), dict) else {}
        bars_by_interval = evidence.get("bars") if isinstance(evidence.get("bars"), dict) else {}
        bars = bars_by_interval.get("5min") if isinstance(bars_by_interval.get("5min"), list) else []
        same_day = [
            bar
            for bar in bars
            if isinstance(bar, dict) and str(bar.get("dt") or "").startswith(date)
        ]
        result[str(scan["symbol"]).upper()] = sorted(same_day, key=lambda item: str(item.get("dt") or ""))
    return result


def daily_price_row(row: dict[str, Any]) -> dict[str, float | None]:
    latest = row.get("latest") if isinstance(row.get("latest"), dict) else {}
    return {
        "high": to_float(latest.get("high")),
        "low": to_float(latest.get("low")),
        "close": to_float(latest.get("close")),
    }


def price_extremes(
    *,
    symbol: str,
    date: str,
    daily_rows: dict[str, dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    bars = bars_by_symbol.get(symbol) or []
    if bars:
        highs = [to_float(bar.get("high")) for bar in bars]
        lows = [to_float(bar.get("low")) for bar in bars]
        closes = [to_float(bar.get("close")) for bar in bars]
        highs = [value for value in highs if value is not None]
        lows = [value for value in lows if value is not None]
        closes = [value for value in closes if value is not None]
        return {
            "source": "intraday_5min",
            "bars": len(bars),
            "first_bar": bars[0].get("dt"),
            "last_bar": bars[-1].get("dt"),
            "high": max(highs) if highs else None,
            "low": min(lows) if lows else None,
            "close": closes[-1] if closes else None,
            "max_close": max(closes) if closes else None,
        }
    daily = daily_price_row(daily_rows.get(symbol, {}))
    return {
        "source": "daily_snapshot",
        "bars": 0,
        "first_bar": None,
        "last_bar": date if daily.get("close") is not None else None,
        "high": daily.get("high"),
        "low": daily.get("low"),
        "close": daily.get("close"),
        "max_close": daily.get("close"),
    }


def classify_price_review(
    *,
    signal: dict[str, Any],
    source_session: str,
    date: str,
    daily_rows: dict[str, dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    symbol = signal_symbol(signal)
    trigger = signal_trigger(signal)
    stop = signal_stop(signal)
    price = price_extremes(symbol=symbol, date=date, daily_rows=daily_rows, bars_by_symbol=bars_by_symbol)
    high = price.get("high")
    low = price.get("low")
    close = price.get("close")
    max_close = price.get("max_close")
    execution_status = str(signal.get("execution_status") or "")
    plan_type = str(signal.get("plan_type") or "")

    touched_trigger = bool(trigger is not None and high is not None and high >= trigger)
    closed_above_trigger = bool(trigger is not None and max_close is not None and max_close >= trigger)
    final_close_above_trigger = bool(trigger is not None and close is not None and close >= trigger)
    touched_stop = bool(stop is not None and low is not None and low <= stop)

    classification = "not_evaluable"
    reason = "missing trigger or price evidence"
    if trigger is not None and high is not None:
        if execution_status == "conditional_executable" and plan_type == "trade_plan":
            classification = "planned_executable"
            reason = "complete Trade Plan Card existed; evaluate in execution review"
        elif not touched_trigger:
            classification = "not_triggered"
            reason = "price never touched the trigger"
        elif touched_stop or not closed_above_trigger or not final_close_above_trigger:
            classification = "touch_fade_or_invalidated"
            reason = "price touched the observation trigger but lacked follow-through or hit risk"
        else:
            classification = "possible_process_miss"
            reason = "watch/no-trade signal touched trigger and closed above it without stop evidence"

    return {
        "symbol": symbol,
        "source_session": source_session,
        "classification": classification,
        "reason": reason,
        "execution_status": execution_status or None,
        "plan_type": plan_type or None,
        "trigger": trigger,
        "stop": stop,
        "price": price,
        "touched_trigger": touched_trigger,
        "closed_above_trigger": closed_above_trigger,
        "final_close_above_trigger": final_close_above_trigger,
        "touched_stop": touched_stop,
    }


def stage_status(repo_root: Path, date: str, session: str, manifest_override: Path | None = None) -> dict[str, Any]:
    report_dir = repo_root / "report" / date
    manifest_path = manifest_override or report_dir / f"{session}-run-manifest.json"
    manifest = read_json(manifest_path)
    if manifest:
        status = manifest.get("status") or "unknown"
        reason = manifest.get("reason")
    else:
        status = "missing"
        reason = "run manifest missing"
    suffix = "pre-market" if session == "pre-market" else "post-market"
    artifacts = [
        relative(repo_root, path)
        for path in [
            report_dir / f"{suffix}.md",
            report_dir / f"{suffix}-signals.json",
            manifest_path,
        ]
        if path.exists()
    ]
    if session == "pre-market":
        for path in [report_dir / "exec-brief.md", report_dir / "pre-market-context.json"]:
            if path.exists():
                artifacts.append(relative(repo_root, path))
    else:
        for path in [report_dir / "daily-snapshot.json", report_dir / "data-quality.json"]:
            if path.exists():
                artifacts.append(relative(repo_root, path))
    return {
        "status": status,
        "reason": reason,
        "artifacts": sorted(dict.fromkeys(artifacts)),
        "steps": [
            {"name": step.get("name"), "status": step.get("status")}
            for step in manifest.get("steps", [])
            if isinstance(step, dict)
        ],
    }


def intraday_status(repo_root: Path, date: str) -> dict[str, Any]:
    report_dir = repo_root / "report" / date
    runtime_dir = repo_root / "runtime" / "intraday" / date
    state = read_json(runtime_dir / "state.json")
    events = read_jsonl(runtime_dir / "events.jsonl")
    sent = read_json(runtime_dir / "sent-events.json")
    sent_ids = sent.get("sent_event_ids") if isinstance(sent.get("sent_event_ids"), list) else []
    monitor = read_json(report_dir / "monitor-signals.json")
    summary = monitor.get("summary") if isinstance(monitor.get("summary"), dict) else {}
    failures = []
    failure_path = repo_root / "runtime" / "intraday" / "codex-monitor-failure.md"
    if failure_path.exists() and date in failure_path.read_text(encoding="utf-8", errors="replace"):
        failures.append(relative(repo_root, failure_path))
    state_payload = state.get("symbols") if isinstance(state.get("symbols"), dict) else {}
    state_counts = Counter()
    for payload in state_payload.values():
        if isinstance(payload, dict):
            state_counts[str(payload.get("state") or "unknown")] += 1
    artifacts = [
        relative(repo_root, path)
        for path in [
            report_dir / "intraday.md",
            report_dir / "intraday-opportunity-context.json",
            report_dir / "monitor-signals.json",
            report_dir / "paper-trade-preview.json",
            report_dir / "paper-trade-submission.json",
            runtime_dir / "state.json",
            runtime_dir / "events.jsonl",
        ]
        if path.exists()
    ]
    return {
        "status": "available" if artifacts else "missing",
        "artifacts": artifacts,
        "focus_symbols": state.get("focus_symbols") if isinstance(state.get("focus_symbols"), list) else [],
        "state_counts": dict(sorted(state_counts.items())),
        "events": len(events),
        "notify_events": len([event for event in events if event.get("notify")]),
        "sent_events": len(sent_ids),
        "monitor_summary": summary,
        "failures": failures,
    }


def load_signals(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    signals = payload.get("signals")
    return [item for item in signals if isinstance(item, dict)] if isinstance(signals, list) else []


def build_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    missed = summary["missed_or_misjudged"]
    stages = payload["workflow_stages"]
    lines = [
        f"# 当日工作过程复盘（{payload['date']}）",
        "",
        "## 流程状态",
        f"- 盘前：{stages['pre_market']['status']}；reason={stages['pre_market'].get('reason') or 'none'}",
        (
            f"- 盘中：{stages['intraday']['status']}；events={stages['intraday']['events']}；"
            f"notify={stages['intraday']['notify_events']}；failures={len(stages['intraday']['failures'])}"
        ),
        f"- 盘后：{stages['post_market']['status']}；reason={stages['post_market'].get('reason') or 'none'}",
        "",
        "## 价格/信号复核",
        f"- 可能漏接候选：{missed['possible_missed_candidates']}",
        f"- 触价后回落/失效：{missed['touch_fade_or_invalidated']}",
        f"- 未触发：{missed['not_triggered']}",
    ]
    if missed["confirmed_no_missed_executable"]:
        lines.append("- 结论：未发现可执行漏判；触价不等于完整交易计划。")
    else:
        lines.append("- 结论：存在可能漏接候选，需要人工复核盘中确认、RR 和风险。")

    details = payload.get("price_reviews") or []
    if details:
        lines.extend(["", "## 逐标的证据"])
        for item in details[:12]:
            price = item.get("price") if isinstance(item.get("price"), dict) else {}
            lines.append(
                "- "
                f"{item.get('symbol')} {item.get('source_session')} {item.get('classification')}："
                f"trigger={item.get('trigger')} stop={item.get('stop')} "
                f"high={price.get('high')} low={price.get('low')} close={price.get('close')}；"
                f"{item.get('reason')}"
            )

    lines.extend(
        [
            "",
            "## 边界",
            "- 本复盘评价流程、信号分层和价格证据，不构成投资建议。",
            "- 只有完整 Trade Plan Card、确认、失效位、TP/RR 和风险都满足时，才可进入下一层模拟盘评审。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    date = args.date
    report_dir = repo_root / "report" / date
    daily_rows = daily_by_symbol(read_json(resolve_path(repo_root, args.snapshot, report_dir / "daily-snapshot.json")))
    context = read_json(resolve_path(repo_root, args.intraday_context, report_dir / "intraday-opportunity-context.json"))
    bars_by_symbol = context_bars_by_symbol(context, date)

    price_reviews: list[dict[str, Any]] = []
    for source_session, path in [
        ("pre-market", resolve_path(repo_root, args.pre_market_signals, report_dir / "pre-market-signals.json")),
        ("monitor", resolve_path(repo_root, args.monitor_signals, report_dir / "monitor-signals.json")),
    ]:
        for signal in load_signals(path):
            price_reviews.append(
                classify_price_review(
                    signal=signal,
                    source_session=source_session,
                    date=date,
                    daily_rows=daily_rows,
                    bars_by_symbol=bars_by_symbol,
                )
            )

    counts = Counter(item["classification"] for item in price_reviews)
    post_manifest = resolve_path(repo_root, args.run_manifest, report_dir / "post-market-run-manifest.json") if args.run_manifest else None
    workflow_stages = {
        "pre_market": stage_status(repo_root, date, "pre-market"),
        "intraday": intraday_status(repo_root, date),
        "post_market": stage_status(repo_root, date, "post-market", manifest_override=post_manifest),
    }
    possible = counts.get("possible_process_miss", 0)
    payload = {
        "schema_version": 1,
        "status": "success",
        "workflow": "daily-workflow-review",
        "date": date,
        "generated_at": now_utc(),
        "workflow_stages": workflow_stages,
        "price_reviews": price_reviews,
        "summary": {
            "workflow_status": {
                "pre_market": workflow_stages["pre_market"]["status"],
                "intraday": workflow_stages["intraday"]["status"],
                "post_market": workflow_stages["post_market"]["status"],
            },
            "intraday_events": workflow_stages["intraday"]["events"],
            "intraday_failures": len(workflow_stages["intraday"]["failures"]),
            "missed_or_misjudged": {
                "possible_missed_candidates": possible,
                "touch_fade_or_invalidated": counts.get("touch_fade_or_invalidated", 0),
                "not_triggered": counts.get("not_triggered", 0),
                "not_evaluable": counts.get("not_evaluable", 0),
                "confirmed_no_missed_executable": possible == 0,
            },
        },
        "safety_note": "Process review only. This workflow reads artifacts and price evidence; it does not place orders.",
    }
    return payload


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    date = args.date
    json_output = resolve_path(repo_root, args.json_output, repo_root / "report" / date / "workflow-review.json")
    markdown_output = resolve_path(repo_root, args.markdown_output, repo_root / "report" / date / "workflow-review.md")
    payload = build_payload(args)
    payload["json_output"] = str(json_output)
    payload["markdown_output"] = str(markdown_output)
    payload["artifacts"] = [str(json_output), str(markdown_output)]
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_output.write_text(build_markdown(payload), encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review the same-day trading workflow execution and price evidence")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--snapshot")
    parser.add_argument("--pre-market-signals")
    parser.add_argument("--monitor-signals")
    parser.add_argument("--intraday-context")
    parser.add_argument("--run-manifest")
    parser.add_argument("--json-output")
    parser.add_argument("--markdown-output")
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
