#!/usr/bin/env python3
"""Outer-only earnings delivery with durable decisions and conservative send recovery."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterator

from earnings_common import ROOT, atomic_write_json, ensure_inside, read_json, sha256_file, utc_now


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def runtime_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return ensure_inside(path if path.is_absolute() else root / path, [root / "runtime/earnings"])


def destination(root: Path, config_path: Path) -> dict[str, Any]:
    config = read_json(runtime_path(root, config_path))
    if config.get("schema_version") != 1 or config.get("verified_repo") != str(root.resolve()):
        raise ValueError("deployment must explicitly bind this repository")
    for key in ("project", "session", "cc_connect_bin", "verified_at", "verified_from_cron_id"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"missing verified delivery field: {key}")
    return config


def prepare_notification(root: Path, deployment: dict[str, Any], *, day: str, body: str,
                         report_versions: list[dict[str, str]], kind: str, rationale: str,
                         should_send: bool) -> Path:
    root = root.resolve()
    if kind not in {"daily", "failure", "acceptance"}:
        raise ValueError("invalid notification kind")
    if not body.strip() or len(body) > 15000:
        raise ValueError("notification must be self-contained and bounded")
    identity = {"project": deployment["project"], "session": deployment["session"],
                "content_hash": hashlib.sha256(body.encode()).hexdigest(), "report_versions": report_versions,
                "kind": kind}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    outbox = runtime_path(root, "runtime/earnings/outbox") / key
    decision_path = outbox / "decision.json"
    with exclusive_lock(outbox / "prepare.lock"):
        if decision_path.exists():
            return decision_path
        for row in report_versions:
            path = ensure_inside(root / row["path"], [root / "report/earnings"])
            if sha256_file(path) != row["sha256"]:
                raise ValueError("notification report version mismatch")
        outbox.mkdir(parents=True, exist_ok=True)
        body_path = outbox / "body.md"
        body_path.write_text(body, encoding="utf-8")
        atomic_write_json(decision_path, {"schema_version": 1, "notification_id": key, **identity,
            "day": day, "should_send": should_send, "rationale": rationale,
            "body_path": str(body_path.relative_to(root)), "state": "ready" if should_send else "suppressed",
            "created_at": utc_now(), "attempts": 0, "receipt": None})
    return decision_path


def deliver(root: Path, decision_path: Path, deployment_path: Path, *, execute: bool = False,
            timeout: int = 60) -> dict[str, Any]:
    root = root.resolve()
    path = runtime_path(root, decision_path)
    deployed = destination(root, deployment_path)
    with exclusive_lock(runtime_path(root, "runtime/earnings/delivery.lock")):
        decision = read_json(path)
        if decision.get("schema_version") != 1:
            raise ValueError("unsupported notification schema")
        for key in ("project", "session"):
            if decision.get(key) != deployed[key]:
                raise ValueError("notification destination differs from verified deployment")
        if decision.get("state") == "sending":
            decision.update(state="unknown", reason="interrupted send; reconcile before any retry")
            atomic_write_json(path, decision)
        if not decision.get("should_send") or decision["state"] in {"sent", "unknown", "suppressed"}:
            return {"status": "skipped", "state": decision["state"], "notification_id": decision["notification_id"]}
        if decision["state"] not in {"ready", "retryable_failed"}:
            raise ValueError("invalid delivery state")
        body_path = runtime_path(root, decision["body_path"])
        body = body_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != decision["content_hash"]:
            raise ValueError("notification body hash mismatch")
        for row in decision["report_versions"]:
            report = ensure_inside(root / row["path"], [root / "report/earnings"])
            if sha256_file(report) != row["sha256"]:
                raise ValueError("report changed after notification decision")
        if not execute:
            return {"status": "skipped", "state": "preview", "notification_id": decision["notification_id"]}
        if deployed.get("delivery_enabled") is not True:
            raise ValueError("delivery is not enabled in deployment")
        # Crash-safe at-most-one ordinary notification per day. Unknown sends occupy the slot.
        if decision["kind"] == "daily":
            for other in runtime_path(root, "runtime/earnings/outbox").glob("*/decision.json"):
                if other == path:
                    continue
                prior = read_json(other)
                if (prior.get("kind"), prior.get("day"), prior.get("project"), prior.get("session")) == (
                    "daily", decision["day"], decision["project"], decision["session"]
                ) and prior.get("state") in {"sending", "sent", "unknown"}:
                    return {"status": "skipped", "state": "deferred", "reason": "daily notification slot already used"}
        binary = Path(deployed["cc_connect_bin"])
        if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
            decision.update(state="retryable_failed", reason="cc-connect executable unavailable before send")
            atomic_write_json(path, decision)
            return {"status": "failed", "state": decision["state"]}
        decision.update(state="sending", started_at=utc_now(), attempts=decision["attempts"] + 1)
        atomic_write_json(path, decision)
        try:
            result = subprocess.run([str(binary), "send", "--project", deployed["project"],
                "--session", deployed["session"], "--stdin"], input=body, capture_output=True, timeout=timeout, check=False)
        except OSError as exc:
            # Popen failed before the program executed; safe to retry only delivery.
            decision.update(state="retryable_failed", reason=f"sender did not start: {type(exc).__name__}")
        except subprocess.TimeoutExpired:
            decision.update(state="unknown", reason="sender timed out; delivery may have succeeded")
        else:
            receipt = path.with_name("send-receipt.log")
            receipt.write_bytes(result.stdout + b"\n" + result.stderr)
            decision.update(state="sent" if result.returncode == 0 else "unknown",
                receipt={"path": str(receipt.relative_to(root)), "sha256": sha256_file(receipt),
                         "exit_code": result.returncode, "confirmed_at": utc_now()},
                reason=None if result.returncode == 0 else "nonzero send result; reconcile before retry")
        decision["updated_at"] = utc_now()
        atomic_write_json(path, decision)
        return {"status": "success" if decision["state"] == "sent" else "failed",
                "state": decision["state"], "notification_id": decision["notification_id"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--deployment", default="runtime/earnings/deployment.json")
    parser.add_argument("--decision", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = deliver(Path(args.repo_root), Path(args.decision), Path(args.deployment), execute=args.execute)
    except (ValueError, OSError, KeyError) as exc:
        result = {"status": "failed", "reason": str(exc)}
    print(json.dumps({"workflow": "earnings-deliver", **result}, ensure_ascii=False))
    raise SystemExit(1 if result["status"] == "failed" else 0)


if __name__ == "__main__":
    main()
