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
KNOWLEDGE_PACK_SCHEMA = "trading-copilot-knowledge-pack/v1"
RAW_SOURCE_POLICY = "compiler_only"


class KnowledgeSourceError(RuntimeError):
    """The configured canonical rulebook cannot be safely used."""


@dataclass(frozen=True)
class KnowledgeSource:
    root: Path
    manifest_path: Path
    approved_setup_files: frozenset[str]
    analysis_methods_path: Path
    knowledge_pack_manifest_path: Path
    method_card_paths: frozenset[str]


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


def _contained_path(root: Path, value: str, *, label: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise KnowledgeSourceError(f"{label} must be relative to the canonical vault: {value}")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise KnowledgeSourceError(f"{label} escapes the canonical vault: {value}") from exc
    return resolved


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
                analysis_methods_path=repo_root / "knowledge" / "analysis-methods.md",
                knowledge_pack_manifest_path=repo_root / "knowledge" / "knowledge-pack.json",
                method_card_paths=frozenset(),
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
    analysis_methods = manifest.get("analysis_methods_path")
    if not isinstance(analysis_methods, str) or not analysis_methods.endswith(".md"):
        raise KnowledgeSourceError(f"invalid analysis_methods_path in {manifest_path}")
    analysis_methods_path = _contained_path(root, analysis_methods, label="analysis_methods_path")
    if not analysis_methods_path.is_file():
        raise KnowledgeSourceError(f"analysis methods input is missing: {analysis_methods_path}")

    knowledge_pack = manifest.get("knowledge_pack_manifest")
    if not isinstance(knowledge_pack, str) or not knowledge_pack.endswith(".json"):
        raise KnowledgeSourceError(f"invalid knowledge_pack_manifest in {manifest_path}")
    knowledge_pack_manifest_path = _contained_path(root, knowledge_pack, label="knowledge_pack_manifest")
    knowledge_pack_payload = _read_json(knowledge_pack_manifest_path)
    if knowledge_pack_payload.get("schema") != KNOWLEDGE_PACK_SCHEMA:
        raise KnowledgeSourceError(f"unsupported knowledge pack schema: {knowledge_pack_manifest_path}")
    if knowledge_pack_payload.get("raw_source_policy") != RAW_SOURCE_POLICY:
        raise KnowledgeSourceError(f"knowledge pack must keep raw sources compiler-only: {knowledge_pack_manifest_path}")
    records = knowledge_pack_payload.get("records")
    if not isinstance(records, list) or not records:
        raise KnowledgeSourceError(f"knowledge pack has no records: {knowledge_pack_manifest_path}")
    approved_rules = manifest.get("approved_rules")
    if not isinstance(approved_rules, list) or not approved_rules:
        raise KnowledgeSourceError(f"canonical rulebook has no approved_rules: {manifest_path}")
    approved_rule_paths: set[str] = set()
    manifest_setup_files: set[str] = set()
    for rule in approved_rules:
        if not isinstance(rule, dict):
            raise KnowledgeSourceError(f"invalid approved rule: {manifest_path}")
        rule_id = rule.get("id")
        kind = rule.get("kind")
        path = rule.get("path")
        if not isinstance(rule_id, str) or not rule_id.endswith(".md"):
            raise KnowledgeSourceError(f"approved rule missing markdown id: {manifest_path}")
        if kind not in {"global", "setup"}:
            raise KnowledgeSourceError(f"approved rule has invalid kind: {manifest_path}")
        if not isinstance(path, str) or not path.endswith(".md"):
            raise KnowledgeSourceError(f"approved rule has invalid path: {manifest_path}")
        rule_path = _contained_path(root, path, label="approved rule path")
        if not rule_path.is_file():
            raise KnowledgeSourceError(f"approved rule is missing: {rule_path}")
        if path in approved_rule_paths:
            raise KnowledgeSourceError(f"duplicate approved rule path: {path}")
        approved_rule_paths.add(path)
        if kind == "setup":
            manifest_setup_files.add(rule_id)
    if manifest_setup_files != set(setup_files):
        raise KnowledgeSourceError(
            f"approved_setup_files do not match approved setup rules: {manifest_path}"
        )

    valid_kind_roles = {
        ("canonical_rule", "execution_constraint"),
        ("canonical_rule", "setup"),
        ("method_card", "analysis_context"),
    }
    record_ids: set[str] = set()
    canonical_record_ids: set[str] = set()
    canonical_record_paths: set[str] = set()
    method_records: list[dict[str, object]] = []
    method_card_paths: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise KnowledgeSourceError(f"invalid knowledge pack record: {knowledge_pack_manifest_path}")
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise KnowledgeSourceError(f"knowledge pack record missing id: {knowledge_pack_manifest_path}")
        if record_id in record_ids:
            raise KnowledgeSourceError(f"duplicate knowledge pack record id: {record_id}")
        record_ids.add(record_id)
        kind_role = (record.get("kind"), record.get("consumer_role"))
        if kind_role not in valid_kind_roles:
            raise KnowledgeSourceError(f"knowledge pack record has invalid type: {knowledge_pack_manifest_path}")
        if record.get("status") != "active":
            raise KnowledgeSourceError(f"knowledge pack record is not active: {knowledge_pack_manifest_path}")
        path = record.get("path")
        if not isinstance(path, str) or not path.endswith(".md"):
            raise KnowledgeSourceError(f"knowledge pack record cannot point to raw content: {knowledge_pack_manifest_path}")
        record_path = _contained_path(root, path, label="knowledge pack record path")
        relative_path = record_path.relative_to(root).as_posix()
        if relative_path.startswith("raw/"):
            raise KnowledgeSourceError(f"knowledge pack record cannot point to raw content: {knowledge_pack_manifest_path}")
        if not record_path.is_file():
            raise KnowledgeSourceError(f"knowledge pack record is missing: {record_path}")
        alignment = record.get("canonical_alignment")
        if not isinstance(alignment, list) or not all(isinstance(item, str) for item in alignment):
            raise KnowledgeSourceError(f"invalid canonical alignment: {knowledge_pack_manifest_path}")
        if record.get("kind") == "canonical_rule":
            if alignment:
                raise KnowledgeSourceError(f"canonical rule alignment must be empty: {record_id}")
            canonical_record_ids.add(record_id)
            canonical_record_paths.add(path)
        else:
            if not alignment:
                raise KnowledgeSourceError(f"method card must align to a canonical rule: {record_id}")
            method_records.append(record)
            method_card_paths.add(path)
    if canonical_record_paths != approved_rule_paths:
        raise KnowledgeSourceError(
            f"canonical knowledge pack records do not match approved_rules: {knowledge_pack_manifest_path}"
        )
    for record in method_records:
        unknown = sorted(set(record["canonical_alignment"]) - canonical_record_ids)
        if unknown:
            raise KnowledgeSourceError(
                f"method card {record['id']} has unknown canonical alignment: {unknown}"
            )
    return KnowledgeSource(
        root=root,
        manifest_path=manifest_path,
        approved_setup_files=frozenset(setup_files),
        analysis_methods_path=analysis_methods_path,
        knowledge_pack_manifest_path=knowledge_pack_manifest_path,
        method_card_paths=frozenset(method_card_paths),
    )


def approved_setup_files(repo_root: Path) -> set[str]:
    return set(resolve_knowledge_source(repo_root).approved_setup_files)


def canonical_rulebook_input(repo_root: Path) -> str:
    """Absolute path exposed to Codex report-generation workflows."""
    return str(resolve_knowledge_source(repo_root).root)


def analysis_methods_input(repo_root: Path) -> str:
    """Expose external price-action methods for analysis, not rule validation."""
    return str(resolve_knowledge_source(repo_root).analysis_methods_path)


def approved_method_card_paths(repo_root: Path) -> set[str]:
    return set(resolve_knowledge_source(repo_root).method_card_paths)
