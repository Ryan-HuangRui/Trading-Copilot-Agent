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

from earnings_common import ROOT, atomic_write_json, canonical_json, classify_model_failure, ensure_inside, load_config, read_json, sha256_bytes, sha256_file, utc_now
from earnings_publication import (REQUIRED_SECTIONS, build_publication, claim_occurrence_inventory,
                                  financial_fact_catalog, validate_reader_markdown)


SUPPORTED = {("gpt-5.6-sol", "medium"), ("gpt-5.6-sol", "high")}


def prepare_input(root: Path, *, publication_type: str, scope_id: str, quarter_id: str,
                  source_paths: list[Path], config_path: str = "config/earnings_research.json",
                  edition: str = "full", title: str | None = None) -> Path:
    config, config_hash = load_config(root, config_path)
    sources = []; reports = []
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
        reports.append(report)
    fact_catalog = financial_fact_catalog(reports)
    resolved_title = title or f"{scope_id} {quarter_id} 财报研究"
    basis = {"type": publication_type, "scope": scope_id, "quarter": quarter_id, "edition": edition,
             "title": resolved_title, "sources": [[r["report_id"], r["sha256"]] for r in sources],
             "method": "reader-publication-v4", "configuration_hash": config_hash,
             "financial_fact_catalog_sha256": sha256_bytes(canonical_json(fact_catalog)),
             "writer_profile": config["profiles"]["daily"], "checker_profile": config["profiles"]["review"],
             "required_sections": list(REQUIRED_SECTIONS[publication_type])}
    publication_key = sha256_bytes(canonical_json(basis))
    run_id = f"publication-{publication_key[:16]}"; run_dir = root / "runtime/earnings/publications/runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "manifest_type": "earnings-publication-input", "publication_key": publication_key,
                "publication_type": publication_type, "scope_id": scope_id, "quarter_id": quarter_id, "edition": edition,
                "title": resolved_title, "created_at": utc_now(), "semantic_input": basis,
                "configuration_hash": config_hash, "sources": sources,
                "financial_fact_catalog": fact_catalog,
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
        if proc.returncode:
            failure_class = classify_model_failure(stderr.read_text(errors="ignore"), events.read_text(errors="ignore"))
            prefix = f"model_{failure_class}: " if failure_class else ""
            raise RuntimeError(f"{prefix}publication role failed with exit {proc.returncode}")
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


def _start_attempt_call(state_path: Path, role: str, profile: dict[str, Any], *, max_attempts: int = 1) -> int:
    state = read_json(state_path)
    calls = state.setdefault("calls", [])
    prior = [row for row in calls if row.get("role") == role]
    if any(row.get("status") == "completed" for row in prior):
        raise ValueError(f"{role} model call already completed")
    if len(prior) >= max_attempts:
        raise ValueError(f"{role} model call was already attempted; bounded attempt limit reached")
    attempt = len(prior) + 1
    calls.append({"role": role, "model": profile["model"], "effort": profile["reasoning_effort"],
                  "attempt": attempt, "status": "started", "usage": None, "failure": None,
                  "started_at": utc_now()})
    state["model_calls_started"] = len(calls)
    atomic_write_json(state_path, state)
    return attempt


def _finish_attempt_call(state_path: Path, role: str, *, status: str, usage: Any = None,
                         failure: str | None = None) -> None:
    state = read_json(state_path); calls = state.setdefault("calls", [])
    matches = [row for row in calls if row.get("role") == role and row.get("status") == "started"]
    if len(matches) != 1:
        raise ValueError(f"{role} model call state is not uniquely started")
    call = matches[0]; call.update({"status": status, "usage": usage, "failure": failure, "finished_at": utc_now()})
    state["model_calls_started"] = len(calls)
    state["model_calls_completed"] = sum(row.get("status") == "completed" for row in calls)
    state["model_calls_failed"] = sum(row.get("status") in {"failed", "unknown"} for row in calls)
    atomic_write_json(state_path, state)


def _adopt_completed_stage(state_path: Path, role: str, profile: dict[str, Any], usage: Any) -> None:
    state = read_json(state_path); calls = state.setdefault("calls", [])
    if not any(row.get("role") == role for row in calls):
        calls.append({"role": role, "model": profile["model"], "effort": profile["reasoning_effort"],
                      "status": "completed", "usage": usage, "failure": None,
                      "started_at": None, "finished_at": utc_now(), "recovered_stage": True})
        state["model_calls_started"] = len(calls)
        state["model_calls_completed"] = sum(row.get("status") == "completed" for row in calls)
        state["model_calls_failed"] = sum(row.get("status") in {"failed", "unknown"} for row in calls)
        atomic_write_json(state_path, state)


def prepare_repair_input(root: Path, original_manifest_path: Path) -> Path:
    """Create the only permitted immutable repair attempt for a failed publication run."""
    root = root.resolve()
    original_path = ensure_inside(original_manifest_path.resolve(), [root / "runtime/earnings/publications/runs"])
    original = read_json(original_path); original_dir = original_path.parent
    if original.get("repair"):
        raise ValueError("a repair manifest cannot create another repair attempt")
    parent_check = dict(original); parent_expected_hash = parent_check.pop("input_manifest_hash", None)
    if not parent_expected_hash or parent_expected_hash != sha256_bytes(canonical_json(parent_check)):
        raise ValueError("parent publication manifest hash mismatch")
    run_root = root / "runtime/earnings/publications/runs"
    original_result_path = ensure_inside((root / original["permitted_outputs"]["runner_result"]).resolve(), [run_root])
    if original_result_path.exists() and read_json(original_result_path).get("status") == "success":
        raise ValueError("successful publication cannot be repaired")
    draft_path = ensure_inside((root / original["permitted_outputs"]["draft"]).resolve(), [run_root])
    checker_stage_path = original_dir / "checker-stage.json"
    checker_stage = read_json(checker_stage_path) if checker_stage_path.exists() else {}
    semantic_path = ensure_inside((root / checker_stage.get("semantic_path",
        original["permitted_outputs"]["semantic_check"])).resolve(), [run_root])
    if not draft_path.exists() or not semantic_path.exists():
        raise ValueError("repair requires a completed failed writer and checker attempt")
    reports = []
    for row in original.get("sources", []):
        source_path = ensure_inside((root / row["path"]).resolve(), [root / "report/earnings"])
        if sha256_file(source_path) != row.get("sha256"):
            raise ValueError("frozen research report changed before repair")
        reports.append(read_json(source_path))
    if not reports:
        raise ValueError("repair requires frozen source reports")
    fact_catalog = financial_fact_catalog(reports)
    semantic = read_json(semantic_path)
    deterministic_path = original_dir / "deterministic-check.json"
    feedback = list(semantic.get("errors") or [])
    if deterministic_path.exists():
        feedback.extend(read_json(deterministic_path).get("errors") or [])
    if original_result_path.exists() and read_json(original_result_path).get("reason"):
        feedback.append(read_json(original_result_path)["reason"])
    feedback = sorted(set(str(row) for row in feedback if row))
    if not feedback:
        raise ValueError("repair requires precise checker or deterministic failure feedback")
    repair_dir = original_dir / "repair-attempt-1"; repair_dir.mkdir(parents=True, exist_ok=True)
    repair_path = repair_dir / "input-manifest.json"
    repair = {key: value for key, value in original.items()
              if key not in {"input_manifest_hash", "created_at", "permitted_outputs", "repair"}}
    semantic_input = dict(repair.get("semantic_input") or {})
    semantic_input["method"] = "reader-publication-v4-repair-1"
    semantic_input["financial_fact_catalog_sha256"] = sha256_bytes(canonical_json(fact_catalog))
    repair["semantic_input"] = semantic_input
    repair["financial_fact_catalog"] = fact_catalog
    repair.update({"created_at": utc_now(),
        "publication_key": stable_repair_key(original["publication_key"], sha256_file(draft_path), sha256_file(semantic_path)),
        "repair": {"attempt": 1, "maximum_attempts": 1,
                   "parent_manifest_path": str(original_path.relative_to(root)),
                   "parent_manifest_sha256": sha256_file(original_path),
                   "parent_draft_path": str(draft_path.relative_to(root)),
                   "parent_draft_sha256": sha256_file(draft_path),
                   "parent_semantic_sha256": sha256_file(semantic_path), "feedback": feedback},
        "permitted_outputs": {"draft": str((repair_dir / "reader-draft.md").relative_to(root)),
                              "semantic_check": str((repair_dir / "semantic-check.json").relative_to(root)),
                              "runner_result": str((repair_dir / "runner-result.json").relative_to(root))}})
    repair["input_manifest_hash"] = sha256_bytes(canonical_json(repair))
    if repair_path.exists():
        existing = read_json(repair_path)
        if existing != repair:
            # created_at is intentionally frozen by the first creation.
            check = dict(existing); expected = check.pop("input_manifest_hash", None)
            if expected != sha256_bytes(canonical_json(check)) or existing.get("repair") != repair.get("repair"):
                raise ValueError("existing repair manifest mismatch")
        return repair_path
    atomic_write_json(repair_path, repair)
    return repair_path


def stable_repair_key(publication_key: str, draft_sha256: str, semantic_sha256: str) -> str:
    return sha256_bytes(canonical_json([publication_key, "repair-attempt-1", draft_sha256, semantic_sha256]))


def run_publication(root: Path, manifest_path: Path, *, binary: str, timeout: int = 1800) -> dict[str, Any]:
    root = root.resolve(); path = ensure_inside(manifest_path.resolve(), [root / "runtime/earnings/publications/runs"])
    manifest = read_json(path)
    manifest_check = dict(manifest); expected_manifest_hash = manifest_check.pop("input_manifest_hash", None)
    if not expected_manifest_hash or expected_manifest_hash != sha256_bytes(canonical_json(manifest_check)):
        raise ValueError("publication input manifest hash mismatch")
    repair_meta = manifest.get("repair")
    if repair_meta:
        if repair_meta.get("attempt") != 1 or repair_meta.get("maximum_attempts") != 1:
            raise ValueError("invalid repair attempt bounds")
        parent_manifest = ensure_inside((root / repair_meta["parent_manifest_path"]).resolve(),
                                        [root / "runtime/earnings/publications/runs"])
        if sha256_file(parent_manifest) != repair_meta.get("parent_manifest_sha256"):
            raise ValueError("parent manifest changed before repair execution")
        parent = read_json(parent_manifest)
        parent_semantic = ensure_inside((root / parent["permitted_outputs"]["semantic_check"]).resolve(),
                                        [root / "runtime/earnings/publications/runs"])
        if sha256_file(parent_semantic) != repair_meta.get("parent_semantic_sha256"):
            raise ValueError("parent semantic check changed before repair execution")
    draft = root / manifest["permitted_outputs"]["draft"]; semantic_path = root / manifest["permitted_outputs"]["semantic_check"]
    result_path = root / manifest["permitted_outputs"]["runner_result"]
    if result_path.exists(): return read_json(result_path)
    attempt_state_path = path.parent / "attempt-state.json"
    if not attempt_state_path.exists():
        atomic_write_json(attempt_state_path, {"schema_version": 1, "status": "running", "started_at": utc_now(),
            "latest_timeout_seconds": timeout, "invocations": [],
            "model_calls_started": 0, "model_calls_completed": 0, "model_calls_failed": 0, "calls": [],
            "repair_attempt": (manifest.get("repair") or {}).get("attempt", 0)})
    attempt_state = read_json(attempt_state_path)
    invocation_deadline = time.time() + timeout
    attempt_state.pop("deadline_epoch", None)
    attempt_state["latest_timeout_seconds"] = timeout
    attempt_state.setdefault("invocations", []).append({"started_at": utc_now(), "timeout_seconds": timeout})
    atomic_write_json(attempt_state_path, attempt_state)
    def remaining() -> int:
        value = int(invocation_deadline - time.time())
        if value <= 0: raise subprocess.TimeoutExpired("earnings-publication", timeout)
        return value
    binary_path = Path(binary)
    if not binary_path.is_absolute() or not os.access(binary_path, os.X_OK): raise ValueError("Codex executable unavailable")
    source_text = []; source_reports = []
    for row in manifest["sources"]:
        source = root / row["path"]
        if sha256_file(source) != row["sha256"]: raise ValueError("frozen research report changed")
        source_text.append(source.read_text(encoding="utf-8")); source_reports.append(read_json(source))
    if manifest.get("financial_fact_catalog") != financial_fact_catalog(source_reports):
        raise ValueError("financial fact catalog does not match frozen sources")
    repair = manifest.get("repair")
    repair_prompt = ""
    if repair:
        parent_draft = ensure_inside((root / repair["parent_draft_path"]).resolve(),
                                     [root / "runtime/earnings/publications/runs"])
        if sha256_file(parent_draft) != repair["parent_draft_sha256"]:
            raise ValueError("failed parent draft changed before repair")
        repair_prompt = ("这是唯一一次修复尝试。逐项消除下列 checker/validator 错误；必须重写为新稿，不得覆盖父稿。"
            "被指出的因果强化必须删除或降为证据允许的定性表述。\n精确错误：\n"
            + json.dumps(repair["feedback"], ensure_ascii=False) + "\n父稿：\n" + parent_draft.read_text(encoding="utf-8") + "\n")
    writer_prompt = ("你是面向普通读者的财报报告撰写者。只能使用下列已验收冻结研究，不得联网、新增事实、改变指标或强化因果。"
        "目标正文约2500至4000个中文字符，语言自然紧凑，接近一篇成熟的公司财报解读，不是内部审计记录或 JSON 字段拼接。"
        "必须覆盖 manifest.required_sections。开头只集中说明一次财年季度、实际经营期间和资料截止；之后仅在累计口径、时点口径或跨期比较"
        "确有歧义时补充期间，不要逐段重复完整日期，也不要写‘冻结输入’‘研究季度映射’等内部流程词。"
        "所有精确财务数字只能从 manifest.financial_fact_catalog 的 facts 或程序算好的 derivations 中选择，展示时必须逐字采用该项的"
        "display_candidates 之一；"
        "不得根据研究叙述创造 RevenueYoYGrowth 等新指标，不得自行组合任意两个数字计算同比、占比或差额。"
        "使用派生量时必须在含义上对应 catalog 的 operation、metric、期间和 input_fact_ids；目录没有的精确数字应删除或改成定性表述。"
        "用一个表格汇总5至8个最关键且由事实目录直接支持的数字；优先选收入、核心业务、利润率、经营利润、现金流、"
        "应收/库存或关键承诺。表格纯数字列必须在表头写单位，正文金额和百分比使用 validator 支持的明确单位（如美元、百万美元、亿美元、%、bps）。"
        "向普通读者解释术语，避免 bps 等专业缩写；优先直接展示已核验的百分比并用中文说明含义，不额外编写未绑定证据的数值换算示例。"
        "经营期间用‘经营期间 YYYY-MM-DD 至 YYYY-MM-DD’声明 duration，时点数字附近用‘截至 YYYY-MM-DD’声明 instant；"
        "但不要为了机器校验堆砌日期。若冻结研究没有已核验 numeric_facts，就省去次要精确数字并如实定性，绝不能为过 gate 编造数字。"
        "必须逐项区分单季、半年、九个月累计和年度口径：累计期数字不得写进单季表格、单季变化或单季因果语境；"
        "如需引用累计值，必须在同一句和表头明确写出累计期间。manifest 缺少某项材料只表示本次冻结资料未包含，"
        "不得扩大写成公司未披露、未提供指引或不存在该信息。期限类金融事实（如合同剩余期限）同样只能使用目录展示值并绑定。"
        "清楚解释生意与产业链机会，并分别写独立验证、利润归属、持续性、最强反证、未知市场预期和下一步验证。"
        "毛利率改善本身不能证明定价权；采购或云服务承诺本身也不能证明未来需求信心，除非冻结证据另有直接支持。"
        "不得把未经充分核验的历史期事项写成同比因果；尤其不能声称上年同期Q2的H20计提造成低基数，除非冻结 numeric_facts 与原文定位"
        "明确支持同一对比期间和该因果。缺少一致预期或价格时明确未知，不写超预期、低估、目标价或买卖指令。只输出 Markdown。\n"
        + repair_prompt + json.dumps(manifest, ensure_ascii=False) + "\n冻结研究：\n" + "\n---\n".join(source_text))
    writer_stage = path.parent / "writer-stage.json"
    if writer_stage.exists():
        frozen_writer = read_json(writer_stage)
        if not draft.exists() or sha256_file(draft) != frozen_writer.get("draft_sha256"):
            raise ValueError("completed writer draft changed before checker retry")
        writer_usage = frozen_writer.get("usage")
        _adopt_completed_stage(attempt_state_path, "writer", manifest["writer_profile"], writer_usage)
    elif draft.exists():
        raise ValueError("writer result is ambiguous; preserve draft and reconcile before retry")
    else:
        _start_attempt_call(attempt_state_path, "writer", manifest["writer_profile"])
        try:
            writer_usage = _codex(root, binary_path, manifest["writer_profile"], writer_prompt, draft,
                                  path.parent / "writer-events.jsonl", path.parent / "writer-stderr.log", remaining())
        except BaseException as exc:
            _finish_attempt_call(attempt_state_path, "writer", status="failed", failure=f"{type(exc).__name__}: {exc}")
            raise
        _finish_attempt_call(attempt_state_path, "writer", status="completed", usage=writer_usage)
        atomic_write_json(writer_stage, {"status": "completed", "draft_sha256": sha256_file(draft),
            "model": manifest["writer_profile"]["model"], "effort": manifest["writer_profile"]["reasoning_effort"],
            "usage": writer_usage, "completed_at": utc_now()})
    occurrence_inventory = claim_occurrence_inventory(draft.read_text(encoding="utf-8"))
    checker_prompt = ("你是独立财报发布核对者。逐项将读者稿与冻结研究比较，检查数字、单位、期间、来源链接、反证、推断强度、"
        "市场预期未知项和可读性。任何漂移或关键遗漏必须 failed。只输出 JSON："
        '输出必须绑定实际位置，只输出 JSON：'
        '{"status":"passed|failed","errors":[],"warnings":[],'
        '"source_mapping":[{"section":"...","claim_ids":[],"evidence_ids":[]}],'
        '"fact_bindings":[{"display":"...","occurrence":{"start":0,"end":1},"fact_id":"...或省略",'
        '"derivation_id":"...或省略","operation":"growth_rate|difference|share 或省略","input_fact_ids":[],"evidence_id":"...",'
        '"metric":"...","period":{},"comparison_period":null,"accounting_basis":"...","currency":"USD",'
        '"source_unit":"...","source_locator":"...","source_evidence_ids":[]}]}。每个 binding 必须从 manifest.financial_fact_catalog '
        '选择且恰好使用 fact_id 或 derivation_id 之一；使用 derivation_id 时 operation 与 input_fact_ids 必须原样复制。'
        '不得发明指标、事实 ID 或自行组合数字。fact_bindings 必须按读者稿出现顺序逐个覆盖每个金额、百分比及表格数字；'
        'occurrence 必须原样采用 runner 提供的 inventory 坐标，不要自行计算偏移；仍须独立判断每项绑定是否有证据。'
        '哈希由 runner 程序绑定，不要生成哈希字段。\n'
        + json.dumps(manifest, ensure_ascii=False) + "\n程序提取的 occurrence inventory：\n"
        + json.dumps(occurrence_inventory, ensure_ascii=False) + "\n读者稿：\n" + draft.read_text(encoding="utf-8")
        + "\n冻结研究：\n" + "\n---\n".join(source_text))
    checker_stage = path.parent / "checker-stage.json"
    if checker_stage.exists():
        frozen_checker = read_json(checker_stage)
        completed_semantic = root / frozen_checker.get("semantic_path", manifest["permitted_outputs"]["semantic_check"])
        if not completed_semantic.exists() or sha256_file(completed_semantic) != frozen_checker.get("semantic_sha256"):
            raise ValueError("completed checker output changed before retry")
        checker_usage = frozen_checker.get("usage"); semantic = read_json(completed_semantic)
        _adopt_completed_stage(attempt_state_path, "checker", manifest["checker_profile"], checker_usage)
    else:
        checker_attempt = _start_attempt_call(attempt_state_path, "checker", manifest["checker_profile"], max_attempts=2)
        checker_output = semantic_path if checker_attempt == 1 else path.parent / f"semantic-check-attempt-{checker_attempt}.json"
        if checker_output.exists():
            raise ValueError("checker result is ambiguous; preserve output and reconcile before retry")
        try:
            suffix = "" if checker_attempt == 1 else f"-attempt-{checker_attempt}"
            checker_usage = _codex(root, binary_path, manifest["checker_profile"], checker_prompt, checker_output,
                                   path.parent / f"checker-events{suffix}.jsonl",
                                   path.parent / f"checker-stderr{suffix}.log", remaining())
        except BaseException as exc:
            _finish_attempt_call(attempt_state_path, "checker", status="failed", failure=f"{type(exc).__name__}: {exc}")
            raise
        _finish_attempt_call(attempt_state_path, "checker", status="completed", usage=checker_usage)
        semantic = read_json(checker_output)
        if semantic.get("status") not in {"passed", "failed"} or not isinstance(semantic.get("errors"), list):
            raise ValueError("invalid semantic checker output")
        if semantic.get("status") == "passed" and semantic.get("errors"):
            raise ValueError("semantic checker cannot pass with errors")
        semantic["draft_sha256"] = sha256_file(draft)
        semantic["input_manifest_hash"] = manifest["input_manifest_hash"]
        semantic["source_sha256s"] = [row["sha256"] for row in manifest["sources"]]
        atomic_write_json(checker_output, semantic)
        atomic_write_json(checker_stage, {"status": "completed", "semantic_path": str(checker_output.relative_to(root)),
            "semantic_sha256": sha256_file(checker_output), "attempt": checker_attempt,
            "model": manifest["checker_profile"]["model"], "effort": manifest["checker_profile"]["reasoning_effort"],
            "usage": checker_usage, "completed_at": utc_now()})
    if semantic.get("status") not in {"passed", "failed"} or not isinstance(semantic.get("errors"), list):
        raise ValueError("invalid semantic checker output")
    if semantic.get("status") == "passed" and semantic.get("errors"):
        raise ValueError("semantic checker cannot pass with errors")
    if not isinstance(semantic.get("source_mapping"), list) or not semantic["source_mapping"]:
        raise ValueError("semantic checker source mapping missing")
    if not isinstance(semantic.get("fact_bindings"), list):
        raise ValueError("semantic checker fact bindings missing")
    deterministic = validate_reader_markdown(draft.read_text(encoding="utf-8"), source_reports,
        publication_type=manifest["publication_type"], explicit_fact_bindings=semantic["fact_bindings"])
    atomic_write_json(path.parent / "deterministic-check.json", deterministic)
    if (deterministic["status"] == "passed"
            and len(semantic["fact_bindings"]) == len(deterministic["fact_mappings"])):
        # The checker selects evidence and judges meaning; trusted catalog supplies
        # canonical metadata. Keep its verbatim output on disk and in the audit.
        semantic = {**semantic, "checker_fact_bindings": semantic["fact_bindings"],
                    "fact_bindings": deterministic["fact_mappings"]}
    role_result = {"writer": {"model": manifest["writer_profile"]["model"], "effort": manifest["writer_profile"]["reasoning_effort"], "usage": writer_usage},
                   "checker": {"model": manifest["checker_profile"]["model"], "effort": manifest["checker_profile"]["reasoning_effort"], "usage": checker_usage}}
    try:
        built = build_publication(root, manifest["publication_type"], manifest["scope_id"], manifest["quarter_id"],
            [root / row["path"] for row in manifest["sources"]], draft.read_text(encoding="utf-8"), semantic_checker=semantic,
            edition=manifest["edition"], title=manifest["title"], input_manifest_hash=manifest["input_manifest_hash"])
        result = {**built, **role_result, "completed_at": utc_now()}
    except ValueError as exc:
        result = {"status": "failed", "reason": str(exc), "semantic_errors": semantic.get("errors", []),
                  "deterministic_errors": deterministic.get("errors", []), **role_result, "completed_at": utc_now()}
    attempt_state = read_json(attempt_state_path); attempt_state["status"] = result["status"]
    attempt_state["completed_at"] = result["completed_at"]
    atomic_write_json(attempt_state_path, attempt_state)
    result["attempt"] = {"model_calls_started": attempt_state.get("model_calls_started", len(attempt_state.get("calls", []))),
                         "model_calls_completed": attempt_state.get("model_calls_completed", 0),
                         "model_calls_failed": attempt_state.get("model_calls_failed", 0),
                         "latest_timeout_seconds": attempt_state.get("latest_timeout_seconds"),
                         "repair_attempt": attempt_state.get("repair_attempt", 0), "calls": attempt_state.get("calls", [])}
    atomic_write_json(result_path, result); return result


def recheck_publication(root: Path, manifest_path: Path) -> dict[str, Any]:
    """Revalidate an unchanged, semantically accepted draft after a validator fix.

    No model call, source mutation or semantic override is permitted. Previous
    attempt records stay immutable; the validator code hashes identify this audit.
    """
    root = root.resolve()
    path = ensure_inside(manifest_path.resolve(), [root / "runtime/earnings/publications/runs"])
    manifest = read_json(path)
    check = dict(manifest); digest = check.pop("input_manifest_hash", None)
    if not digest or digest != sha256_bytes(canonical_json(check)):
        raise ValueError("publication input manifest hash mismatch")
    draft_path = ensure_inside(root / manifest["permitted_outputs"]["draft"], [path.parent])
    checker_stage = read_json(path.parent / "checker-stage.json")
    semantic_path = ensure_inside(root / checker_stage.get("semantic_path",
        manifest["permitted_outputs"]["semantic_check"]), [path.parent])
    writer_stage = read_json(path.parent / "writer-stage.json")
    if sha256_file(draft_path) != writer_stage.get("draft_sha256"):
        raise ValueError("completed writer draft changed")
    if sha256_file(semantic_path) != checker_stage.get("semantic_sha256"):
        raise ValueError("completed checker output changed")
    semantic = read_json(semantic_path)
    if semantic.get("status") != "passed" or semantic.get("errors"):
        raise ValueError("recheck requires an independently passed semantic check")
    sources = []
    for row in manifest["sources"]:
        source = ensure_inside(root / row["path"], [root / "report/earnings"])
        if sha256_file(source) != row["sha256"]:
            raise ValueError("frozen research report changed")
        sources.append(source)
    audit = {"input_manifest_sha256": sha256_file(path), "draft_sha256": sha256_file(draft_path),
             "semantic_sha256": sha256_file(semantic_path), "model_calls": 0,
             "validator_sha256": sha256_file(Path(__file__).with_name("earnings_publication.py")),
             "runner_sha256": sha256_file(Path(__file__))}
    audit_path = path.parent / ("recheck-" + sha256_bytes(canonical_json(audit))[:16] + ".json")
    if audit_path.exists(): return read_json(audit_path)
    body = draft_path.read_text(encoding="utf-8")
    deterministic = validate_reader_markdown(body, [read_json(source) for source in sources],
        publication_type=manifest["publication_type"], explicit_fact_bindings=semantic.get("fact_bindings"))
    if (deterministic["status"] == "passed" and isinstance(semantic.get("fact_bindings"), list)
            and len(semantic["fact_bindings"]) == len(deterministic["fact_mappings"])):
        semantic = {**semantic, "checker_fact_bindings": semantic["fact_bindings"],
                    "fact_bindings": deterministic["fact_mappings"]}
    try:
        result = build_publication(root, manifest["publication_type"], manifest["scope_id"], manifest["quarter_id"],
            sources, body, semantic_checker=semantic, edition=manifest["edition"], title=manifest["title"],
            input_manifest_hash=digest)
    except ValueError as exc:
        result = {"status": "failed", "reason": str(exc)}
    result.update(audit=audit, checked_at=utc_now(), audit_path=str(audit_path.relative_to(root)))
    atomic_write_json(audit_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT)); parser.add_argument("--config", default="config/earnings_research.json")
    parser.add_argument("--type", choices=sorted(REQUIRED_SECTIONS)); parser.add_argument("--scope")
    parser.add_argument("--quarter"); parser.add_argument("--source-report", action="append")
    parser.add_argument("--edition", choices=["full", "stage", "revision"], default="full"); parser.add_argument("--title")
    parser.add_argument("--codex-bin"); parser.add_argument("--execute", action="store_true"); parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--repair", help="path to one failed input-manifest.json; creates at most repair-attempt-1")
    parser.add_argument("--recheck", help="unchanged input-manifest with passed semantic check; no model execution")
    args = parser.parse_args(); root = Path(args.repo_root).resolve()
    try:
        if args.recheck:
            if args.repair or args.execute:
                raise ValueError("--recheck cannot be combined with --repair or --execute")
            result = recheck_publication(root, root / args.recheck)
            print(json.dumps({"workflow": "earnings-publication-runner", **result}, ensure_ascii=False))
            raise SystemExit(result["status"] == "failed")
        if args.repair:
            manifest = prepare_repair_input(root, root / args.repair)
        else:
            if not args.type or not args.scope or not args.quarter or not args.source_report:
                raise ValueError("--type, --scope, --quarter and --source-report are required unless --repair is used")
            manifest = prepare_input(root, publication_type=args.type, scope_id=args.scope, quarter_id=args.quarter,
                source_paths=[root / value for value in args.source_report], config_path=args.config, edition=args.edition, title=args.title)
        result = run_publication(root, manifest, binary=args.codex_bin, timeout=args.timeout) if args.execute else {
            "status": "success", "model_execution_required": True, "repair": bool(args.repair),
            "manifest_path": str(manifest.relative_to(root))}
    except (ValueError, OSError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        result = {"status": "failed", "reason": str(exc)}
    print(json.dumps({"workflow": "earnings-publication-runner", **result}, ensure_ascii=False))
    raise SystemExit(result["status"] == "failed")


if __name__ == "__main__":
    main()
