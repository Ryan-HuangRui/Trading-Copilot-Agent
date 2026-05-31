#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from signal_artifacts import read_json, resolve_signals_path, validate_sidecar_payload
from validate_report import refined_setup_files


def validate(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    sidecar = resolve_signals_path(repo_root, args.date, args.signals, args.session)
    setup_files = refined_setup_files(repo_root)
    errors: list[str] = []
    warnings: list[str] = []

    if not setup_files:
        errors.append("missing refined setup directory or setup markdown files")
    if not sidecar.exists():
        errors.append(f"missing structured signal sidecar: {sidecar}")
        return {
            "status": "fail",
            "date": args.date,
            "session": args.session,
            "checked_artifacts": [],
            "checked_signals": None,
            "errors": errors,
            "warnings": warnings,
        }

    try:
        payload = read_json(sidecar)
    except Exception as exc:
        errors.append(f"{sidecar}: invalid JSON: {exc}")
        payload = None

    if payload is not None:
        sidecar_errors, sidecar_warnings = validate_sidecar_payload(
            payload=payload,
            path=sidecar,
            expected_date=args.date,
            expected_session=args.session,
            setup_files=setup_files,
        )
        errors.extend(sidecar_errors)
        warnings.extend(sidecar_warnings)

    return {
        "status": "fail" if errors else "pass",
        "date": args.date,
        "session": args.session,
        "checked_artifacts": [str(sidecar)],
        "checked_signals": str(sidecar),
        "errors": errors,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate structured Trading Copilot trade-plan sidecars")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market", "monitor"], required=True)
    parser.add_argument("--signals", help="Structured signal sidecar path")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = validate(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
