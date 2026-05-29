#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_append import append_jsonl
from journal_review import read_jsonl
from signal_artifacts import read_json


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def default_review_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-execution-review.json")


def learning_path(repo_root: Path, learning_dir: str) -> Path:
    base = Path(learning_dir)
    if not base.is_absolute():
        base = repo_root / base
    return base / "daily_lessons.jsonl"


def default_output_path(repo_root: Path, date: str, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / date / "paper-learning-lessons.json")


def classify_problem(text: str) -> tuple[str, str]:
    lower = text.lower()
    if "protective stop" in lower:
        return "missing_protective_stop", "filled paper entries should have a synced protective stop before exit automation"
    if "slippage" in lower:
        return "high_entry_slippage", "paper entries with repeated slippage should review trigger timing and limit placement"
    if "initial stop" in lower:
        return "missing_initial_stop", "paper execution reviews require a valid planned initial stop"
    if "remains open" in lower:
        return "open_position_pending_exit", "open paper positions should defer final R review until exit evidence exists"
    return "paper_execution_review", "paper execution lessons require human review before process changes"


def lesson_key(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
    evidence = record.get("evidence") if isinstance(record.get("evidence"), list) else []
    return (
        str(record.get("date") or ""),
        str(record.get("lesson_type") or ""),
        str(record.get("symbol") or ""),
        str(record.get("setup") or ""),
        "|".join(str(item) for item in evidence),
    )


def build_lessons(date: str, review_payload: dict[str, Any]) -> list[dict[str, Any]]:
    reviews = review_payload.get("reviews") if isinstance(review_payload.get("reviews"), list) else []
    lessons: list[dict[str, Any]] = []
    for review in reviews:
        if not isinstance(review, dict):
            continue
        for raw_lesson in review.get("candidate_lessons") or []:
            text = str(raw_lesson or "").strip()
            if not text:
                continue
            problem, suggested = classify_problem(text)
            symbol = str(review.get("symbol") or "").upper()
            lessons.append(
                {
                    "kind": "daily_lesson",
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "date": date,
                    "lesson_type": "paper_execution",
                    "symbol": symbol,
                    "setup": review.get("setup") or "paper_execution",
                    "problem": problem,
                    "evidence": [f"{symbol}: {text}" if symbol else text],
                    "suggested_constraint": suggested,
                    "status": "candidate",
                    "source_review": review_payload.get("source_execution_state") or review_payload.get("source_preview"),
                    "source_intent_id": review.get("intent_id"),
                    "source_signal_id": review.get("source_signal_id"),
                }
            )
    return lessons


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    review_path = default_review_path(repo_root, args.date, args.review)
    lessons_path = learning_path(repo_root, args.learning_dir)
    output = default_output_path(repo_root, args.date, args.output)
    review_payload = read_json(review_path)
    lessons = build_lessons(args.date, review_payload)
    existing = read_jsonl(lessons_path)
    existing_keys = {lesson_key(record) for record in existing}
    appended: list[dict[str, Any]] = []
    skipped_duplicates: list[dict[str, Any]] = []
    if args.append:
        for lesson in lessons:
            key = lesson_key(lesson)
            if key in existing_keys:
                skipped_duplicates.append(lesson)
                continue
            append_jsonl(lessons_path, lesson)
            existing_keys.add(key)
            appended.append(lesson)

    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_review": str(review_path),
        "learning_journal": str(lessons_path),
        "append_requested": bool(args.append),
        "lessons": lessons,
        "appended": appended,
        "skipped_duplicates": skipped_duplicates,
        "summary": {
            "candidate_lessons": len(lessons),
            "appended": len(appended),
            "skipped_duplicates": len(skipped_duplicates),
        },
        "safety_note": "Paper learning lessons are runtime candidates only and do not modify refined rules.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "output": str(output), "summary": payload["summary"]}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "date": None,
        "review": None,
        "learning_dir": "runtime/learning",
        "output": None,
        "append": False,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract paper execution candidate lessons into the runtime learning loop")
    parser.add_argument("--date", required=True)
    parser.add_argument("--review")
    parser.add_argument("--learning-dir", default="runtime/learning")
    parser.add_argument("--output")
    parser.add_argument("--append", action="store_true")
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
