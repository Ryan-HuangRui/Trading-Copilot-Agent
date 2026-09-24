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


def process_identity(pid: int) -> str | None:
    """Return the live PID/process-group tuple; callers also verify an ownership lock."""
    try:
        return f"{pid}:{os.getpgid(pid)}"
    except (OSError, ProcessLookupError):
        return None


def classify_model_failure(*values: object) -> str | None:
    """Classify explicit backend failures without treating timeouts as quota events."""
    text = "\n".join(str(value) for value in values if value).lower()
    quota_markers = (
        "usage limit", "rate limit exceeded", "quota exceeded", "insufficient_quota",
        "no weighted tokens left", "limit has been reached", "model_quota_exhausted",
    )
    capacity_markers = (
        "server is overloaded", "overloaded", "capacity", "temporarily unavailable",
        "service unavailable", "try again later", "model_capacity_unavailable",
    )
    if any(marker in text for marker in quota_markers):
        return "quota_exhausted"
    if any(marker in text for marker in capacity_markers):
        return "capacity_unavailable"
    return None


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


def explicit_three_month_period(content: bytes, reporting_end: str) -> tuple[str, str] | None:
    """Resolve a calendar-month duration only from an explicit dated source phrase."""
    import calendar
    import html
    end = date.fromisoformat(reporting_end)
    if end.day != calendar.monthrange(end.year, end.month)[1]:
        return None
    text = content.decode("utf-8", errors="replace")
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
    # A week-based accounting calendar cannot be converted to calendar months.
    if re.search(r"\b(?:13|14|thirteen|fourteen)[ -]weeks?\b|\b(?:52|53)[ -]weeks?\b", text, re.I):
        return None
    pattern = rf"\bthree months ended\s+{end.strftime('%B')}\s+{end.day},?\s+{end.year}\b"
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    year, month = divmod(end.year * 12 + end.month - 3, 12)
    return date(year, month + 1, 1).isoformat(), match.group(0)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def company_research_configuration_basis(config: dict[str, Any]) -> dict[str, Any]:
    """Return the complete, versioned semantic input policy for company research."""
    budgets = config.get("budgets") or {}
    return {"scope": "company-research-v1", "schema_version": config.get("schema_version"),
            "sources": config.get("sources"),
            "daily_profile": (config.get("profiles") or {}).get("daily"),
            "company_policy": {key: budgets.get(key) for key in (
                "max_task_attempts", "initialization_lookback_quarters")}}


def company_research_configuration_hash(config: dict[str, Any]) -> str:
    """Hash company-research inputs, excluding publication/delivery operations."""
    return sha256_bytes(canonical_json(company_research_configuration_basis(config)))


def legacy_company_configuration_status(root: Path, legacy_hash: str,
                                        current_config: dict[str, Any]) -> str:
    """Verify a legacy raw config hash against an explicitly registered immutable snapshot."""
    registry = root / "runtime" / "earnings" / "config-migrations" / "company-research.json"
    if not registry.is_file():
        return "missing"
    payload = read_json(registry)
    current_basis = company_research_configuration_basis(current_config)
    current_hash = company_research_configuration_hash(current_config)
    for row in payload.get("snapshots", []):
        if row.get("raw_configuration_sha256") != legacy_hash:
            continue
        snapshot = ensure_inside(resolve_path(root, row.get("snapshot_path", "")),
                                 [root / "runtime" / "earnings" / "config-migrations" / "snapshots"])
        if (not snapshot.is_file() or sha256_file(snapshot) != legacy_hash
                or row.get("snapshot_sha256") != legacy_hash):
            return "missing"
        legacy_config = read_json(snapshot)
        legacy_basis = company_research_configuration_basis(legacy_config)
        return ("compatible" if row.get("semantic_basis") == legacy_basis == current_basis
                and row.get("semantic_hash") == current_hash else "incompatible")
    return "missing"


def legacy_company_configuration_compatible(root: Path, legacy_hash: str,
                                            current_config: dict[str, Any]) -> bool:
    return legacy_company_configuration_status(root, legacy_hash, current_config) == "compatible"


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
    window = data.get("disclosure_window")
    if window is not None:
        if not isinstance(window, dict) or set(window) != {"start", "end_exclusive"}:
            raise ValueError("disclosure_window requires start and end_exclusive")
        start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end_exclusive"])
        if start >= end:
            raise ValueError("disclosure_window start must precede end_exclusive")
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
