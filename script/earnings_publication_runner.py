#!/usr/bin/env python3
"""Run bounded writer and independent checker roles for a reader publication."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any

from earnings_common import ROOT, atomic_write_json, canonical_json, ensure_inside, load_config, read_json, sha256_bytes, sha256_file, utc_now
from earnings_publication import REQUIRED_SECTIONS, build_publication, claim_occurrence_inventory


SUPPORTED = {("gpt-5.6-sol", "medium"), ("gpt-5.6-sol", "high")}


def prepare_input(root: Path, *, publication_type: str, scope_id: str, quarter_id: str,
                  source_paths: list[Path], config_path: str = "config/earnings_research.json",
                  edition: str = "full", title: str | None = None) -> Path:
    config, config_hash = load_config(root, config_path)
    sources = []
    for source in source_paths:
        path = ensure_inside(source.resolve(), [root / "report/earnings"])
        report = read_json(path)
        if report.get("source_mode") != "live": raise ValueError("production publication runner refuses fixture research")
        fiscal_period = None
        if report.get("report_type") == "company":
            from earnings_period_review import resolve_report_period
            fiscal_period = resolve_report_period(report)
        sources.append({"path": str(path.relative_to(root)), "sha256": sha256_file(path), "report_id": report.get("report_id"),
                        "report_type": report.get("report_type"), "cutoff": report.get("cutoff"),
                        "fiscal_period": fiscal_period})
    resolved_title = title or f"{scope_id} {quarter_id} 财报研究"
    basis = {"type": publication_type, "scope": scope_id, "quarter": quarter_id, "edition": edition,
             "title": resolved_title, "sources": [[r["report_id"], r["sha256"]] for r in sources],
             "method": "reader-publication-v2", "configuration_hash": config_hash,
             "writer_profile": config["profiles"]["daily"], "checker_profile": config["profiles"]["review"],
             "required_sections": list(REQUIRED_SECTIONS[publication_type])}
    publication_key = sha256_bytes(canonical_json(basis))
    run_id = f"publication-{publication_key[:16]}"; run_dir = root / "runtime/earnings/publications/runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "manifest_type": "earnings-publication-input", "publication_key": publication_key,
                "publication_type": publication_type, "scope_id": scope_id, "quarter_id": quarter_id, "edition": edition,
                "title": resolved_title, "created_at": utc_now(), "semantic_input": basis,
                "configuration_hash": config_hash, "sources": sources,
                "writer_profile": {"name": "daily", **config["profiles"]["daily"]},
                "checker_profile": {"name": "review", **config["profiles"]["review"]},
                "required_sections": list(REQUIRED_SECTIONS[publication_type]),
                "permitted_outputs": {"draft": str((run_dir / "reader-draft.md").relative_to(root)),
                                      "semantic_check": str((run_dir / "semantic-check.json").relative_to(root)),
                                      "runner_result": str((run_dir / "runner-result.json").relative_to(root))},
                "instructions": {"writer_research_chain_frozen": True, "template_claim_stitching_forbidden": True,
                                 "checker_independent": True, "notification_forbidden": True}}
    path = run_dir / "input-manifest.json"
    if path.exists():
        existing = read_json(path)
        if existing.get("semantic_input") != basis or existing.get("publication_key") != publication_key:
            raise ValueError("existing publication manifest semantic input mismatch")
        expected_hash = existing.get("input_manifest_hash")
        check = dict(existing); check.pop("input_manifest_hash", None)
        if expected_hash != sha256_bytes(canonical_json(check)):
            raise ValueError("existing publication manifest hash mismatch")
        return path
    manifest["input_manifest_hash"] = sha256_bytes(canonical_json(manifest))
    atomic_write_json(path, manifest)
    return path


def _codex(root: Path, binary: Path, profile: dict[str, Any], prompt: str, output: Path, events: Path, stderr: Path, timeout: int) -> dict | None:
    model, effort = profile["model"], profile["reasoning_effort"]
    if (model, effort) not in SUPPORTED: raise ValueError("unsupported publication profile; fallback forbidden")
    command = [str(binary), "exec", "--ignore-user-config", "--ephemeral", "--sandbox", "read-only", "-C", str(root),
               "-m", model, "-c", f'model_reasoning_effort="{effort}"', "-c", 'approval_policy="never"', "--json",
               "--output-last-message", str(output), "-"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CC_CONNECT_", "TCA_SEC_", "FEISHU_", "LARK_"))}
    proc = None
    try:
        with events.open("w") as event_file, stderr.open("w") as error_file:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=event_file, stderr=error_file,
                                    text=True, env=env, start_new_session=True)
            proc.communicate(prompt, timeout=timeout)
        if proc.returncode: raise RuntimeError(f"publication role failed with exit {proc.returncode}")
    except BaseException:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL); proc.wait(timeout=10)
        raise
    usage = None
    for line in events.read_text().splitlines():
        try: event = json.loads(line)
        except json.JSONDecodeError: continue
        if event.get("type") == "turn.completed": usage = event.get("usage")
    return usage


def run_publication(root: Path, manifest_path: Path, *, binary: str, timeout: int = 1800) -> dict[str, Any]:
    root = root.resolve(); path = ensure_inside(manifest_path.resolve(), [root / "runtime/earnings/publications/runs"])
    manifest = read_json(path)
    draft = root / manifest["permitted_outputs"]["draft"]; semantic_path = root / manifest["permitted_outputs"]["semantic_check"]
    result_path = root / manifest["permitted_outputs"]["runner_result"]
    if result_path.exists(): return read_json(result_path)
    deadline = time.monotonic() + timeout
    def remaining() -> int:
        value = int(deadline - time.monotonic())
        if value <= 0: raise subprocess.TimeoutExpired("earnings-publication", timeout)
        return value
    binary_path = Path(binary)
    if not binary_path.is_absolute() or not os.access(binary_path, os.X_OK): raise ValueError("Codex executable unavailable")
    source_text = []
    for row in manifest["sources"]:
        source = root / row["path"]
        if sha256_file(source) != row["sha256"]: raise ValueError("frozen research report changed")
        source_text.append(source.read_text(encoding="utf-8"))
    writer_prompt = ("你是财报读者报告撰写者。只能使用下列已验收冻结研究，不得联网或新增事实。写自然、完整、简体中文报告，"
        "不是拼接 JSON 字段。必须逐项覆盖 manifest.required_sections；保留数字、单位、实际经营期间、资料截止、来源链接、关键反证、"
        "金额和百分比必须使用 validator 支持的明确单位（如 美元/百万美元/亿美元/%/bps）；表格纯数字列必须在表头写单位；"
        "正文用‘经营期间 YYYY-MM-DD 至 YYYY-MM-DD’声明 duration，instant 数字附近用‘截至 YYYY-MM-DD’声明期间。"
        "情景和下一验证点。缺少一致预期或价格时明确未知，不能写超预期、低估、目标价或买卖指令。只输出 Markdown。\n"
        + json.dumps(manifest, ensure_ascii=False) + "\n冻结研究：\n" + "\n---\n".join(source_text))
    writer_stage = path.parent / "writer-stage.json"
    if writer_stage.exists():
        frozen_writer = read_json(writer_stage)
        if not draft.exists() or sha256_file(draft) != frozen_writer.get("draft_sha256"):
            raise ValueError("completed writer draft changed before checker retry")
        writer_usage = frozen_writer.get("usage")
    elif draft.exists():
        raise ValueError("writer result is ambiguous; preserve draft and reconcile before retry")
    else:
        writer_usage = _codex(root, binary_path, manifest["writer_profile"], writer_prompt, draft,
                              path.parent / "writer-events.jsonl", path.parent / "writer-stderr.log", remaining())
        atomic_write_json(writer_stage, {"status": "completed", "draft_sha256": sha256_file(draft),
            "model": manifest["writer_profile"]["model"], "effort": manifest["writer_profile"]["reasoning_effort"],
            "usage": writer_usage, "completed_at": utc_now()})
    occurrence_inventory = claim_occurrence_inventory(draft.read_text(encoding="utf-8"))
    checker_prompt = ("你是独立财报发布核对者。逐项将读者稿与冻结研究比较，检查数字、单位、期间、来源链接、反证、推断强度、"
        "市场预期未知项和可读性。任何漂移或关键遗漏必须 failed。只输出 JSON："
        '输出必须绑定实际位置，只输出 JSON：'
        '{"status":"passed|failed","errors":[],"warnings":[],'
        '"source_mapping":[{"section":"...","claim_ids":[],"evidence_ids":[]}],'
        '"fact_bindings":[{"display":"...","occurrence":{"start":0,"end":1},"evidence_id":"...","metric":"...",'
        '"period":{},"accounting_basis":"...","currency":"USD","source_unit":"...","derivation":"reported",'
        '"source_evidence_ids":[]}]}。fact_bindings 必须按读者稿出现顺序逐个覆盖每个金额、百分比及表格数字；'
        'occurrence 必须原样采用 runner 提供的 inventory 坐标，不要自行计算偏移；仍须独立判断每项绑定是否有证据。'
        '哈希由 runner 程序绑定，不要生成哈希字段。\n'
        + json.dumps(manifest, ensure_ascii=False) + "\n程序提取的 occurrence inventory：\n"
        + json.dumps(occurrence_inventory, ensure_ascii=False) + "\n读者稿：\n" + draft.read_text(encoding="utf-8")
        + "\n冻结研究：\n" + "\n---\n".join(source_text))
    checker_usage = _codex(root, binary_path, manifest["checker_profile"], checker_prompt, semantic_path,
                           path.parent / "checker-events.jsonl", path.parent / "checker-stderr.log", remaining())
    semantic = read_json(semantic_path)
    if semantic.get("status") not in {"passed", "failed"} or not isinstance(semantic.get("errors"), list):
        raise ValueError("invalid semantic checker output")
    if semantic.get("status") == "passed" and semantic.get("errors"):
        raise ValueError("semantic checker cannot pass with errors")
    semantic["draft_sha256"] = sha256_file(draft)
    semantic["input_manifest_hash"] = manifest["input_manifest_hash"]
    semantic["source_sha256s"] = [row["sha256"] for row in manifest["sources"]]
    atomic_write_json(semantic_path, semantic)
    if not isinstance(semantic.get("source_mapping"), list) or not semantic["source_mapping"]:
        raise ValueError("semantic checker source mapping missing")
    if not isinstance(semantic.get("fact_bindings"), list):
        raise ValueError("semantic checker fact bindings missing")
    built = build_publication(root, manifest["publication_type"], manifest["scope_id"], manifest["quarter_id"],
        [root / row["path"] for row in manifest["sources"]], draft.read_text(encoding="utf-8"), semantic_checker=semantic,
        edition=manifest["edition"], title=manifest["title"], input_manifest_hash=manifest["input_manifest_hash"])
    result = {**built, "writer": {"model": manifest["writer_profile"]["model"], "effort": manifest["writer_profile"]["reasoning_effort"], "usage": writer_usage},
              "checker": {"model": manifest["checker_profile"]["model"], "effort": manifest["checker_profile"]["reasoning_effort"], "usage": checker_usage},
              "completed_at": utc_now()}
    atomic_write_json(result_path, result); return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT)); parser.add_argument("--config", default="config/earnings_research.json")
    parser.add_argument("--type", choices=sorted(REQUIRED_SECTIONS), required=True); parser.add_argument("--scope", required=True)
    parser.add_argument("--quarter", required=True); parser.add_argument("--source-report", action="append", required=True)
    parser.add_argument("--edition", choices=["full", "stage", "revision"], default="full"); parser.add_argument("--title")
    parser.add_argument("--codex-bin"); parser.add_argument("--execute", action="store_true"); parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(); root = Path(args.repo_root).resolve()
    try:
        manifest = prepare_input(root, publication_type=args.type, scope_id=args.scope, quarter_id=args.quarter,
            source_paths=[root / value for value in args.source_report], config_path=args.config, edition=args.edition, title=args.title)
        result = run_publication(root, manifest, binary=args.codex_bin, timeout=args.timeout) if args.execute else {
            "status": "success", "model_execution_required": True, "manifest_path": str(manifest.relative_to(root))}
    except (ValueError, OSError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        result = {"status": "failed", "reason": str(exc)}
    print(json.dumps({"workflow": "earnings-publication-runner", **result}, ensure_ascii=False))
    raise SystemExit(result["status"] == "failed")


if __name__ == "__main__":
    main()
