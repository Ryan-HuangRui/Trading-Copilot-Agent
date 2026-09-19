#!/usr/bin/env python3
"""Preview/register an exact raw legacy config snapshot for company-task reuse."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from earnings_common import (ROOT, atomic_write_json, company_research_configuration_basis,
                             company_research_configuration_hash, ensure_inside, read_json,
                             relative_to_root, resolve_path, sha256_file, utc_now)
from earnings_delivery import exclusive_lock


def register_snapshot(root: Path, snapshot_path: Path, *, execute: bool = False) -> dict:
    root = root.resolve()
    source = ensure_inside(snapshot_path.resolve(), [root / "runtime" / "earnings"])
    config = read_json(source)
    raw_hash = sha256_file(source)
    basis = company_research_configuration_basis(config)
    semantic_hash = company_research_configuration_hash(config)
    target = root / "runtime" / "earnings" / "config-migrations" / "snapshots" / f"{raw_hash}.json"
    registry = target.parents[1] / "company-research.json"
    result = {"schema_version": 1, "workflow": "earnings-config-migration", "status": "preview",
              "raw_configuration_sha256": raw_hash, "semantic_hash": semantic_hash,
              "semantic_basis": basis, "snapshot_path": relative_to_root(root, target), "executed": False}
    if not execute:
        return result
    with exclusive_lock(root / "runtime" / "earnings" / "daily.lock"):
        raw = source.read_bytes()
        if target.exists() and target.read_bytes() != raw:
            raise ValueError("immutable legacy config snapshot collision")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f".{target.name}.tmp")
            temp.write_bytes(raw)
            temp.replace(target)
        payload = read_json(registry) if registry.exists() else {
            "schema_version": 1, "workflow": "earnings-company-config-migrations", "snapshots": []}
        entry = {"raw_configuration_sha256": raw_hash, "snapshot_sha256": sha256_file(target),
                 "snapshot_path": relative_to_root(root, target), "semantic_hash": semantic_hash,
                 "semantic_basis": basis, "registered_at": utc_now()}
        matches = [row for row in payload.get("snapshots", []) if row.get("raw_configuration_sha256") == raw_hash]
        if matches and any({key: row.get(key) for key in entry if key != "registered_at"}
                           != {key: entry.get(key) for key in entry if key != "registered_at"} for row in matches):
            raise ValueError("legacy config hash is already registered with different evidence")
        if not matches:
            payload.setdefault("snapshots", []).append(entry)
            atomic_write_json(registry, payload)
        result.update(status="success", executed=True, registry_path=relative_to_root(root, registry),
                      registered_at=(matches[0].get("registered_at") if matches else entry["registered_at"]))
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--snapshot", required=True, help="raw pre-deployment config copy under runtime/earnings")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = register_snapshot(Path(args.repo_root), resolve_path(Path(args.repo_root), args.snapshot), execute=args.execute)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result = {"workflow": "earnings-config-migration", "status": "failed", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(result["status"] == "failed")


if __name__ == "__main__":
    main()
