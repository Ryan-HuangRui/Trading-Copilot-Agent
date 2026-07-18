"""Resolve the canonical Trading Copilot rulebook outside this repository.

The Obsidian vault owns all approved price-action rules.  This module exposes
stable logical setup IDs to Trading Copilot without copying rule content back
into the application repository.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


MANIFEST_RELATIVE_PATH = Path("_meta/trading-copilot-rulebook.json")
CONFIG_RELATIVE_PATH = Path("config/knowledge_source.json")
SCHEMA = "trading-copilot-rulebook/v1"


class KnowledgeSourceError(RuntimeError):
    """The configured canonical rulebook cannot be safely used."""


@dataclass(frozen=True)
class KnowledgeSource:
    root: Path
    manifest_path: Path
    approved_setup_files: frozenset[str]


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeSourceError(f"cannot read canonical rulebook metadata: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise KnowledgeSourceError(f"canonical rulebook metadata must be an object: {path}")
    return payload


def _configured_root(repo_root: Path) -> Path | None:
    value = os.environ.get("TCA_KNOWLEDGE_ROOT")
    if value:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else (repo_root / candidate)

    config_path = repo_root / CONFIG_RELATIVE_PATH
    if not config_path.exists():
        return None
    config = _read_json(config_path)
    value = config.get("canonical_root")
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeSourceError(f"missing canonical_root in {config_path}")
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (repo_root / candidate)


def resolve_knowledge_source(repo_root: Path) -> KnowledgeSource:
    """Load the configured rulebook and fail closed when it is malformed."""
    repo_root = repo_root.resolve()
    root = _configured_root(repo_root)
    if root is None:
        # Backward-compatible fixture support only. Production repositories carry
        # config/knowledge_source.json, and the migrated repository contains no
        # local refined directory to fall back to.
        legacy = repo_root / "knowledge" / "refined" / "setups"
        if legacy.is_dir():
            return KnowledgeSource(
                root=repo_root / "knowledge",
                manifest_path=legacy,
                approved_setup_files=frozenset(path.name for path in legacy.glob("*.md")),
            )
        raise KnowledgeSourceError(
            "canonical rulebook is not configured; set TCA_KNOWLEDGE_ROOT or config/knowledge_source.json"
        )

    root = root.resolve()
    manifest_path = root / MANIFEST_RELATIVE_PATH
    if not manifest_path.is_file():
        raise KnowledgeSourceError(f"canonical rulebook manifest is missing: {manifest_path}")
    manifest = _read_json(manifest_path)
    if manifest.get("schema") != SCHEMA:
        raise KnowledgeSourceError(f"unsupported canonical rulebook schema: {manifest_path}")
    setup_files = manifest.get("approved_setup_files")
    if not isinstance(setup_files, list) or not all(isinstance(item, str) and item.endswith(".md") for item in setup_files):
        raise KnowledgeSourceError(f"invalid approved_setup_files in {manifest_path}")
    if not setup_files:
        raise KnowledgeSourceError(f"canonical rulebook has no approved setup files: {manifest_path}")
    return KnowledgeSource(root=root, manifest_path=manifest_path, approved_setup_files=frozenset(setup_files))


def approved_setup_files(repo_root: Path) -> set[str]:
    return set(resolve_knowledge_source(repo_root).approved_setup_files)


def canonical_rulebook_input(repo_root: Path) -> str:
    """Absolute path exposed to Codex report-generation workflows."""
    return str(resolve_knowledge_source(repo_root).root)
