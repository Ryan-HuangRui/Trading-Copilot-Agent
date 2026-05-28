#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal_review import read_jsonl


DEFAULT_VALIDATED = """# Validated Lessons

This file contains human-reviewed process lessons that may guide future analysis prompts.

Rules:
- Runtime lessons from runtime/learning/daily_lessons.jsonl are candidates only.
- Content here is process feedback; it must not override knowledge/refined/.
- Do not promote a lesson directly into knowledge/refined/ without explicit human approval.
"""


def resolve_path(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def candidates_path(repo_root: Path, learning_dir: str) -> Path:
    return resolve_path(repo_root, learning_dir) / "pattern_candidates.jsonl"


def default_output(repo_root: Path) -> Path:
    return repo_root / "knowledge" / "evolution" / "validated_lessons.md"


def load_candidate(path: Path, pattern_id: str) -> dict[str, Any]:
    for record in read_jsonl(path):
        if record.get("pattern_id") == pattern_id:
            return record
    raise ValueError(f"pattern candidate not found: {pattern_id}")


def markdown_block(candidate: dict[str, Any]) -> str:
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), list) else []
    evidence_lines = [f"  - {item}" for item in evidence[:5]] or ["  - No compact evidence recorded."]
    return "\n".join(
        [
            f"## {candidate.get('pattern_id')}",
            "",
            "- Status: validated_lesson",
            f"- Promoted at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"- Lesson type: {candidate.get('lesson_type')}",
            f"- Problem: {candidate.get('problem')}",
            f"- Setup: {candidate.get('setup')}",
            f"- Seen: {candidate.get('seen_count')} ({candidate.get('first_seen')} to {candidate.get('last_seen')})",
            f"- Symbols: {', '.join(candidate.get('symbols') or []) or 'n/a'}",
            f"- Constraint: {candidate.get('suggested_constraint')}",
            "- Evidence:",
            *evidence_lines,
            "",
            "Boundary: this lesson may guide prompts, but it does not override knowledge/refined/.",
            "",
        ]
    )


def apply_block(path: Path, pattern_id: str, block: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        current = path.read_text(encoding="utf-8")
    else:
        current = DEFAULT_VALIDATED.rstrip() + "\n"
    if f"## {pattern_id}" in current:
        return False
    separator = "" if current.endswith("\n\n") else "\n"
    path.write_text(current.rstrip() + separator + "\n" + block, encoding="utf-8")
    return True


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    candidate_file = resolve_path(repo_root, args.candidates) if args.candidates else candidates_path(repo_root, args.learning_dir)
    candidate = load_candidate(candidate_file, args.pattern_id)
    block = markdown_block(candidate)
    output = resolve_path(repo_root, args.output) if args.output else default_output(repo_root)
    applied = False
    if args.apply:
        applied = apply_block(output, args.pattern_id, block)
    return {
        "status": "success",
        "pattern_id": args.pattern_id,
        "applied": applied,
        "output": str(output),
        "candidate": candidate,
        "markdown_block": block,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Promote a repeated pattern candidate into validated lessons")
    parser.add_argument("--pattern-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--candidates", help="Pattern candidate JSONL path")
    parser.add_argument("--learning-dir", default="runtime/learning")
    parser.add_argument("--output", help="Validated lessons markdown path")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.dry_run and args.apply:
        print(json.dumps({"status": "failed", "reason": "--dry-run and --apply are mutually exclusive"}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    if not args.dry_run and not args.apply:
        args.dry_run = True
    try:
        payload = run(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
