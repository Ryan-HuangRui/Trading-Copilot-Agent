#!/usr/bin/env python3
"""Index persisted Vibe Swarm results for Trading Copilot analysis.

This script never calls MCP or an LLM. Codex owns the interactive MCP run and
persists its raw result/summary first; this script only validates paths, records
provenance, and writes the optional evidence context consumed by report agents.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = 1
PRESET_NAME = "tca_research_review"
TERMINAL_STATUSES = {"completed", "failed", "stale"}
ALL_STATUSES = {"pending", "running", *TERMINAL_STATUSES}
QUALITY_LABELS = {"RESEARCH_ONLY", "WATCH", "NO_TRADE"}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected top-level object")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_symbols(symbols: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for symbol in symbols or []:
        value = str(symbol or "").strip().upper()
        if value.endswith(".US"):
            value = value[:-3]
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def resolve_repo_artifact(repo_root: Path, path_text: str | None) -> Path | None:
    if not path_text:
        return None
    candidate = Path(path_text)
    resolved = (candidate if candidate.is_absolute() else repo_root / candidate).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"artifact must stay inside repository: {path_text}") from exc
    return resolved


def relative_path(repo_root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(repo_root))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def artifact_record(repo_root: Path, path: Path | None, kind: str) -> dict[str, Any] | None:
    if path is None:
        return None
    if not path.exists() or not path.is_file():
        raise ValueError(f"missing {kind} artifact: {path}")
    return {
        "kind": kind,
        "path": relative_path(repo_root, path),
        "sha256": sha256_file(path),
    }


def build_run_record(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    run_id = str(args.run_id or "").strip()
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id contains unsupported characters or is too long")
    status = str(args.status).lower()
    if status not in ALL_STATUSES:
        raise ValueError(f"unsupported status: {status}")
    symbols = normalize_symbols(args.symbol)
    if not symbols:
        raise ValueError("at least one --symbol is required")
    quality_label = str(args.quality_label).upper()
    if quality_label not in QUALITY_LABELS:
        raise ValueError(f"unsupported quality label: {quality_label}")
    confidence = float(args.confidence)
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence must be between 0 and 1")

    result_path = resolve_repo_artifact(repo_root, args.result)
    summary_path = resolve_repo_artifact(repo_root, args.summary)
    if status == "completed" and (result_path is None or summary_path is None):
        raise ValueError("completed runs require both --result and --summary")

    artifact_rows = [
        row
        for row in (
            artifact_record(repo_root, result_path, "raw_result"),
            artifact_record(repo_root, summary_path, "chinese_summary"),
        )
        if row is not None
    ]
    limitations = list(args.limitation or [])
    limitations.extend(
        [
            "Vibe Swarm output is secondary research evidence, not a Trading Copilot rule source.",
            "This record cannot raise execution_status, create a Trade Plan Card, or authorize broker activity.",
            "Current price conclusions still require Longbridge-first market evidence and the canonical rulebook.",
        ]
    )
    return {
        "run_id": run_id,
        "preset_name": PRESET_NAME,
        "status": status,
        "session": args.session,
        "target": args.target,
        "symbols": symbols,
        "objective": args.objective,
        "as_of": args.as_of,
        "indexed_at": now_utc(),
        "provider": args.provider,
        "model": args.model,
        "quality_label": quality_label,
        "confidence": confidence,
        "artifacts": artifact_rows,
        "limitations": list(dict.fromkeys(limitations)),
        "usable_as_agent_evidence": status == "completed" and len(artifact_rows) == 2,
    }


def load_or_initialize(output: Path, date: str) -> dict[str, Any]:
    if not output.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "workflow": "vibe-research-context",
            "date": date,
            "generated_at": now_utc(),
            "policy": {
                "role": "secondary_agent_research_evidence",
                "can_raise_execution_status": False,
                "can_create_trade_plan": False,
                "can_edit_canonical_rulebook": False,
                "can_mutate_watchlist": False,
                "can_call_broker": False,
            },
            "runs": [],
        }
    payload = read_json(output)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{output}: unsupported schema_version")
    if payload.get("date") != date:
        raise ValueError(f"{output}: date mismatch")
    if not isinstance(payload.get("runs"), list):
        raise ValueError(f"{output}: runs must be an array")
    return payload


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output = resolve_repo_artifact(repo_root, args.output)
    if output is None:
        output = repo_root / "report" / args.date / "agents" / "vibe-research-context.json"
    record = build_run_record(args, repo_root)
    payload = load_or_initialize(output, args.date)
    existing = [row for row in payload["runs"] if isinstance(row, dict)]
    payload["runs"] = [row for row in existing if row.get("run_id") != record["run_id"]]
    payload["runs"].append(record)
    payload["generated_at"] = now_utc()
    write_json(output, payload)

    artifacts = [relative_path(repo_root, output)]
    artifacts.extend(row["path"] for row in record["artifacts"])
    return {
        "status": "success",
        "workflow": "vibe-research-context",
        "date": args.date,
        "artifacts": artifacts,
        "skipped": False,
        "reason": None,
        "run": record,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Index a persisted Vibe Swarm research run")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market", "research"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--status", choices=sorted(ALL_STATUSES), required=True)
    parser.add_argument("--result")
    parser.add_argument("--summary")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--quality-label", choices=sorted(QUALITY_LABELS), default="RESEARCH_ONLY")
    parser.add_argument("--confidence", type=float, default=0.5)
    parser.add_argument("--limitation", action="append", default=[])
    parser.add_argument("--output")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    try:
        payload = run(build_parser().parse_args())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "workflow": "vibe-research-context",
                    "date": None,
                    "artifacts": [],
                    "skipped": False,
                    "reason": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
