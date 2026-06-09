#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def repo_path(repo_root: Path, path_text: str | None, default: Path) -> Path:
    if path_text:
        path = Path(path_text)
        return path if path.is_absolute() else repo_root / path
    return repo_root / default


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected object")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            events.append(payload)
    return events


def sent_ids(path: Path) -> set[str]:
    payload = read_json(path, {"sent_event_ids": []})
    raw_ids = payload.get("sent_event_ids")
    if not isinstance(raw_ids, list):
        return set()
    return {str(item) for item in raw_ids if item}


def write_message(path: Path, date: str, events: list[dict[str, Any]]) -> None:
    lines = [
        f"盘中监控事件: {date}",
        f"events: {len(events)}",
        "note: 仅用于盘中观察提醒，不构成交易指令。",
    ]
    for event in events:
        lines.append(
            "- "
            f"{event.get('symbol')} "
            f"{event.get('previous_state') or 'unknown'} -> {event.get('state')} "
            f"bar={event.get('bar_timestamp') or 'n/a'} "
            f"reason={event.get('reason') or 'n/a'}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    events_path = repo_path(repo_root, args.events, Path("runtime") / "intraday" / args.date / "events.jsonl")
    sent_path = repo_path(repo_root, args.sent_state, Path("runtime") / "intraday" / args.date / "sent-events.json")
    message_path = repo_path(repo_root, args.message_output, Path("report") / args.date / "intraday-notification.md")

    sent = sent_ids(sent_path)
    events = [
        event
        for event in read_events(events_path)
        if event.get("notify") and str(event.get("event_id") or "") and str(event.get("event_id")) not in sent
    ][: args.max_events]

    if not events:
        return {
            "status": "success",
            "date": args.date,
            "should_send": False,
            "reason": "no unsent notify events",
            "events_path": str(events_path),
            "sent_state": str(sent_path),
            "summary": {"unsent_events": 0},
        }

    write_message(message_path, args.date, events)
    if args.mark_sent:
        updated = sorted(sent | {str(event["event_id"]) for event in events})
        write_json(
            sent_path,
            {
                "date": args.date,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sent_event_ids": updated,
            },
        )

    return {
        "status": "success",
        "date": args.date,
        "should_send": True,
        "reason": None,
        "events_path": str(events_path),
        "sent_state": str(sent_path),
        "message_output": str(message_path),
        "events": events,
        "summary": {"unsent_events": len(events)},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Feishu-ready notification from unsent intraday tracker events")
    parser.add_argument("--date", required=True)
    parser.add_argument("--events")
    parser.add_argument("--sent-state")
    parser.add_argument("--message-output")
    parser.add_argument("--max-events", type=int, default=5)
    parser.add_argument("--mark-sent", action="store_true")
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
