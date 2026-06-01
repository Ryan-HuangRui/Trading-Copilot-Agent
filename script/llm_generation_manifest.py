#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def resolve_path(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def relpath(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def hash_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def file_record(repo_root: Path, value: str) -> dict[str, Any]:
    path = resolve_path(repo_root, value)
    return {
        "path": relpath(repo_root, path),
        "exists": path.exists(),
        "sha256": hash_file(path),
    }


def git_output(repo_root: Path, *args: str) -> str | None:
    proc = subprocess.run(["git", *args], cwd=repo_root, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def default_output(repo_root: Path, date: str, session: str, explicit: str | None) -> Path:
    if explicit:
        return resolve_path(repo_root, explicit)
    return repo_root / "report" / date / f"{session}-llm-generation.json"


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output = default_output(repo_root, args.date, args.session, args.output)
    payload = {
        "schema_version": 1,
        "date": args.date,
        "session": args.session,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": args.model,
        "runner": args.runner,
        "prompt": file_record(repo_root, args.prompt) if args.prompt else None,
        "inputs": [file_record(repo_root, item) for item in args.input],
        "outputs": [file_record(repo_root, item) for item in args.generated_output],
        "notes": args.notes,
        "git_sha": git_output(repo_root, "rev-parse", "HEAD"),
        "branch": git_output(repo_root, "branch", "--show-current"),
        "dirty_files": [
            line for line in (git_output(repo_root, "status", "--short") or "").splitlines() if line.strip()
        ],
        "boundary": "LLM may propose analysis and conditional plans; scripted validators gate journal, sync, paper preview, and delivery.",
    }
    payload = {key: value for key, value in payload.items() if value not in (None, "", [])}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "date": args.date, "session": args.session, "output": str(output)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record LLM report-generation provenance after artifacts are written")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--runner", default="codex")
    parser.add_argument("--prompt")
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--generated-output", action="append", default=[])
    parser.add_argument("--notes")
    parser.add_argument("--output")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    try:
        payload = run(build_parser().parse_args())
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
