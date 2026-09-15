#!/usr/bin/env python3
"""Shared, standard-library helpers for the earnings research workflow."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def shanghai_date() -> str:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        except ValueError as exc:
            raise ValueError(f"invalid ISO date/time: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: Any, length: int = 24) -> str:
    raw = "\x1f".join("" if part is None else str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:length]}"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    os.replace(temp, path)


def ensure_inside(path: Path, roots: Iterable[Path]) -> Path:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            pass
    raise ValueError(f"path is outside allowed roots: {path}")


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def safe_segment(value: Any, label: str = "path segment") -> str:
    text = str(value or "")
    if not SAFE_SEGMENT_RE.fullmatch(text) or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError(f"unsafe {label}: {value!r}")
    return text


def confined_path(root: Path, value: str | Path, allowed_relative_root: str | Path) -> Path:
    path = resolve_path(root, value)
    return ensure_inside(path, [root / allowed_relative_root])


def relative_to_root(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def configuration_path(root: Path, value: str | Path) -> Path:
    """Allow tracked defaults and private deployment overrides inside this repo."""
    return ensure_inside(resolve_path(root, value), [root / "config", root / "runtime/earnings"])


def load_config(root: Path, config_path: str = "config/earnings_research.json") -> tuple[dict[str, Any], str]:
    path = configuration_path(root, config_path)
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"unsupported earnings config: {path}")
    return data, sha256_file(path)


def envelope(workflow: str, report_date: str | None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "success",
        "workflow": workflow,
        "date": report_date or shanghai_date(),
        "artifacts": [],
        "skipped": False,
        "reason": None,
    }
    payload.update(extra)
    return payload


def emit(payload: dict[str, Any], exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(exit_code)
