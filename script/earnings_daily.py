#!/usr/bin/env python3
"""Daily, budgeted earnings orchestration. No polling or exchange-day skipping."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

from earnings_common import ROOT, atomic_write_json, load_config, read_json, sha256_file, utc_now
from earnings_delivery import destination, deliver, exclusive_lock, prepare_notification, runtime_path
from earnings_role_runner import run_role
from earnings_state import EarningsState


class DailyLedger:
    def __init__(self, root: Path):
        runtime_path(root, "runtime/earnings").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(runtime_path(root, "runtime/earnings/daily.sqlite"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS reservations(day TEXT, task_id TEXT, role TEXT, created_at TEXT,
          PRIMARY KEY(day,task_id,created_at));
        CREATE TABLE IF NOT EXISTS initialized(symbol TEXT PRIMARY KEY, at TEXT);
        CREATE TABLE IF NOT EXISTS industry_inputs(scope TEXT PRIMARY KEY, fingerprint TEXT);
        CREATE TABLE IF NOT EXISTS health(day TEXT PRIMARY KEY, failed INTEGER);
        CREATE TABLE IF NOT EXISTS industry_requests(scope TEXT PRIMARY KEY, fingerprint TEXT, cutoff TEXT);
        """)
        self.db.commit()

    def used(self, day: str, role: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM reservations WHERE day=? AND role=?", (day, role)).fetchone()[0]

    def reserve(self, day: str, task: str, role: str, limit: int) -> bool:
        if self.used(day, role) >= limit:
            return False
        self.db.execute("INSERT INTO reservations VALUES(?,?,?,?)", (day, task, role, datetime.now(timezone.utc).isoformat(timespec="microseconds")))
        self.db.commit()
        return True


def command(root: Path, script: str, args: list[str], logs: Path, timeout: int) -> dict:
    result = subprocess.run([sys.executable, str(root / "script" / script), "--repo-root", str(root), *args],
                            capture_output=True, text=True, timeout=timeout, check=False)
    log = logs / f"{script}-{uuid.uuid4().hex[:8]}.log"
    log.write_text(result.stdout + "\n" + result.stderr)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{script} returned no valid envelope; see {log.name}") from exc
    if result.returncode or data.get("status") == "failed":
        raise RuntimeError(f"{script}: {data.get('reason') or 'failed'}")
    return data


def season_limit(config: dict, day: str) -> int:
    month_day = date.fromisoformat(day).strftime("%m-%d")
    strong = any(s["start"] <= month_day <= s["strong_end"] for s in config["seasons"])
    key = "strong_season_company_limit" if strong else "daily_company_limit"
    return int(config["budgets"][key])


def industry_work(root: Path, state: EarningsState, universe: dict, ledger: DailyLedger) -> list[dict]:
    due = []
    for industry in universe["industries"]:
        symbols = [row["symbol"] for row in industry["issuers"]]
        marks = ",".join("?" for _ in symbols)
        rows = state.db.execute(f"""SELECT a.path,a.sha256,a.period_end FROM report_artifacts a
          JOIN earnings_events e ON a.subject_id=e.event_id JOIN issuers i ON e.issuer_id=i.issuer_id
          WHERE a.report_type='company' AND a.source_mode='live' AND i.symbol IN ({marks})
          ORDER BY a.period_end DESC,a.created_at DESC""", symbols).fetchall()
        if not rows or not rows[0]["period_end"]:
            continue
        # Daily cohort is explicitly based on period ends. Source reports preserve fiscal periods.
        end = date.fromisoformat(rows[0]["period_end"])
        month = ((end.month - 1) // 3) * 3 + 1
        start = date(end.year, month, 1)
        next_q = date(end.year + (month == 10), 1 if month == 10 else month + 3, 1)
        from datetime import timedelta
        finish = next_q - timedelta(days=1)
        versions = sorted((r["path"], r["sha256"]) for r in rows if r["period_end"] and start.isoformat() <= r["period_end"] <= finish.isoformat())
        signature = hashlib.sha256(json.dumps(versions).encode()).hexdigest()
        scope = f"{industry['industry_id']}:{start}:{finish}"
        existing = ledger.db.execute("SELECT fingerprint FROM industry_inputs WHERE scope=?", (scope,)).fetchone()
        if not existing or existing[0] != signature:
            due.append({"industry": industry["industry_id"], "start": str(start), "end": str(finish),
                        "scope": scope, "fingerprint": signature})
    return due


def fail_owned_attempt(state: EarningsState, manifest: dict, error: str) -> None:
    """An outer failure must never revert a committed report or another owner's lease."""
    lease = manifest["lease"]
    with state.immediate() as db:
        db.execute("""UPDATE research_tasks SET state=CASE WHEN attempts<max_attempts
          THEN 'retryable_failed' ELSE 'terminal_failed' END,error=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?
          WHERE task_id=? AND state='running' AND lease_owner=? AND attempts=?""",
          (error, utc_now(), manifest["task_id"], lease["owner"], lease["attempt"]))


def notification_material(report: dict, prior: dict | None) -> bool:
    if report.get("completeness", {}).get("status") == "insufficient" or not report.get("evidence"):
        return False
    current_state = report.get("thesis_state")
    if current_state in {None, "insufficient_data"}:
        return False
    # Only an evidenced thesis-state transition is auto-pushed in P3. All other reports remain archived.
    return prior is None or prior.get("thesis_state") != current_state


def unresolved_terminal_count(state: EarningsState) -> int:
    """Keep exhausted current work visible; superseded attempts are historical."""
    return state.db.execute("""SELECT COUNT(*) FROM research_tasks t
      WHERE t.state='terminal_failed' AND t.source_mode='live' AND NOT EXISTS (
        SELECT 1 FROM research_tasks n WHERE n.rowid>t.rowid AND n.source_mode=t.source_mode
        AND n.task_type=t.task_type AND n.subject_id=t.subject_id
        AND n.period_start IS t.period_start AND n.period_end IS t.period_end)""").fetchone()[0]


def finalize(root: Path, deployed: dict, deployed_path: Path, day: str, reports: list[dict], errors: list[str],
             state: EarningsState, *, send: bool) -> dict:
    material = []
    handled = set()
    for decision_path in runtime_path(root, "runtime/earnings/outbox").glob("*/decision.json"):
        prior_decision = read_json(decision_path)
        if prior_decision.get("state") in {"sent", "unknown", "sending", "suppressed"}:
            handled.update(row["sha256"] for row in prior_decision.get("report_versions", []))
    # Recover registered reports even if a previous outer process died before finalization.
    candidates = []
    rows = state.db.execute("""SELECT a.*,a.rowid AS revision,e.issuer_id FROM report_artifacts a
      LEFT JOIN earnings_events e ON a.subject_id=e.event_id WHERE a.source_mode='live'
      ORDER BY a.period_end DESC,a.rowid DESC""").fetchall()
    latest = set()
    for row in rows:
        scope_key = (row["issuer_id"] or row["subject_id"], row["report_type"])
        if scope_key in latest:
            continue
        latest.add(scope_key)
        if row["sha256"] not in handled:
            candidates.append({"report_path": row["path"], "report_sha256": row["sha256"], "task_id": row["task_id"], "revision": row["revision"]})
    for result in candidates:
        report = read_json(root / result["report_path"])
        task = state.db.execute("SELECT subject_id,period_start,period_end FROM research_tasks WHERE task_id=?", (report["task_id"],)).fetchone()
        previous = state.db.execute("""SELECT path FROM report_artifacts WHERE subject_id=? AND report_type=? AND rowid<?
          AND period_start IS ? AND period_end IS ? AND source_mode='live' ORDER BY rowid DESC LIMIT 1""",
          (task[0], report["report_type"], result["revision"], task[1], task[2])).fetchone()
        prior = read_json(root / previous[0]) if previous else None
        if notification_material(report, prior):
            material.append((report, result))
    lines = [f"财报研究 · {day}", ""]
    versions = [{"path": r["report_path"], "sha256": r["report_sha256"]} for r in candidates]
    for report, result in material[:5]:
        title = report["scope"].get("symbol") or report["scope"].get("industry_id") or report["scope"].get("issuer_id")
        lines.append(f"{title}（期间结束：{report['scope'].get('reporting_end') or '未确定'}）：{str(report.get('change_summary', ''))[:650]}")
        lines.append(f"状态：{report['thesis_state']}；完整度：{report['completeness']['status']}。")
        for item in report["evidence"][:2]:
            lines.append(f"来源：{item['source_url']}")
        checks = report.get("next_checks", [])
        if checks:
            lines.append(f"后续核验：{str(checks[0])[:200]}")

    if len(material) > 5:
        lines.append(f"另有 {len(material)-5} 份状态变化报告已归档，本条合并通知。")
    if errors:
        lines.append("本批次存在未完成步骤，已保留本地失败记录和待处理任务。")
    lines.extend(["", "数据质量：仅采用已归档的 SEC/发行人披露，覆盖缺口随报告保留。",
                  f"运行校验：本批次完成 {len(reports)} 个角色，失败步骤 {len(errors)} 个。",
                  "交付审计：完整报告存于 NAS 本地，仅本条摘要通过仓库绑定的 cc-connect 发送。"])
    decision = prepare_notification(root, deployed, day=day, body="\n".join(lines), report_versions=versions,
        kind="daily", rationale="evidenced thesis-state change" if material else "no material thesis-state change",
        should_send=bool(material))
    outcome = deliver(root, decision, deployed_path, execute=send)
    return {"decision": str(decision.relative_to(root)), "delivery": outcome}


def run(args: argparse.Namespace) -> dict:
    root = Path(args.repo_root).resolve()
    runtime_path(root, "runtime/earnings").mkdir(parents=True, exist_ok=True)
    config, _ = load_config(root, args.config)
    deployed_path = runtime_path(root, args.deployment)
    deployed = destination(root, deployed_path)
    if config["paths"]["state"] != "runtime/earnings/state.sqlite":
        raise ValueError("P3 runner requires canonical earnings state path")
    if config["budgets"].get("concurrency") != 1:
        raise ValueError("P3 initial runner requires concurrency=1")
    if config["budgets"].get("max_tokens_per_day") is not None or config["budgets"].get("currency_budget_per_day") is not None:
        raise ValueError("hard token/currency budget unavailable for auth backend; configure observable task limits")
    batch_seconds = int(deployed.get("batch_timeout_seconds", 7200))
    if not 60 <= batch_seconds <= 14400:
        raise ValueError("batch_timeout_seconds must be between 60 and 14400")
    if not 60 <= int(config["budgets"]["task_timeout_seconds"]) <= 7200:
        raise ValueError("task_timeout_seconds must be between 60 and 7200")
    now = datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    cutoff = now.isoformat()
    run_id = f"daily-{day}-{uuid.uuid4().hex[:12]}"
    logs = runtime_path(root, f"runtime/earnings/runs/{run_id}")
    logs.mkdir(parents=True)
    with exclusive_lock(runtime_path(root, "runtime/earnings/daily.lock")):
        ledger = DailyLedger(root)
        state = EarningsState(root / "runtime/earnings/state.sqlite")
        reports, errors = [], []
        deadline = time.monotonic() + batch_seconds
        limit = season_limit(config, day)
        config_args = ["--config", args.config]
        common = [*config_args, "--date", day, "--cutoff", cutoff]
        context_common = [*common, "--universe", config["paths"]["universe"]]
        universe = read_json(root / config["paths"]["universe"])
        try:
            if not args.resume_only:
                symbols = sorted({issuer["symbol"] for industry in universe["industries"] for issuer in industry["issuers"]})
                initialized = {r[0] for r in ledger.db.execute("SELECT symbol FROM initialized")}
                already = ledger.used(day, "initialization")
                pending = [s for s in symbols if s not in initialized][:max(0, int(config["budgets"]["initialization_company_limit"]) - already)]
                for symbol in symbols:
                    if time.monotonic() >= deadline:
                        errors.append("collection budget deadline reached; remaining issuers deferred")
                        break
                    initialization = symbol in pending
                    if initialization:
                        ledger.reserve(day, symbol, "initialization", int(config["budgets"]["initialization_company_limit"]))
                    try:
                        collected = command(root, "earnings_collect.py", [*common, "--mode", "live", "--symbol", symbol,
                            "--collection-kind", "initialization" if initialization else "incremental"], logs, min(600, max(1, int(deadline-time.monotonic()))))
                        failures = collected.get("summary", {}).get("failures", [])
                        if failures:
                            errors.append(f"source:{symbol}:incomplete")
                        elif initialization and collected.get("summary", {}).get("initialization_coverage", {}).get(symbol, {}).get("complete") is True:
                            ledger.db.execute("INSERT OR REPLACE INTO initialized VALUES(?,?)", (symbol, utc_now())); ledger.db.commit()
                    except (RuntimeError, subprocess.TimeoutExpired) as exc:
                        errors.append(str(exc))
                        if "TCA_SEC_USER_AGENT" in str(exc):
                            break
            if not args.collect_only:
                context_attempts = 0
                while ledger.used(day, "company") < limit and time.monotonic() < deadline and context_attempts < limit * 2:
                    context_attempts += 1
                    try:
                        context = command(root, "earnings_context.py", [*context_common, "--limit", "1", "--run-id", f"{run_id}-{uuid.uuid4().hex[:8]}",
                            "--lease-seconds", str(int(config["budgets"]["task_timeout_seconds"]) + 180)], logs, 120)
                    except (RuntimeError, subprocess.TimeoutExpired) as exc:
                        errors.append(f"company-context:{exc}")
                        continue
                    manifests = context.get("manifests", [])
                    if not manifests:
                        break
                    for path in manifests:
                        manifest = read_json(root / path)
                        if not ledger.reserve(day, manifest["task_id"], "company", limit):
                            break
                        try:
                            reports.append(run_role(root, root / path, binary=deployed["codex_bin"],
                                timeout=min(int(config["budgets"]["task_timeout_seconds"]), max(1, int(deadline-time.monotonic())))))
                        except Exception as exc:
                            fail_owned_attempt(state, manifest, str(exc))
                            errors.append(f"role:{manifest['task_id']}:{exc}")
                for work in industry_work(root, state, universe, ledger):
                    cap = int(config["budgets"]["daily_industry_limit"])
                    if ledger.used(day, "industry") >= cap or time.monotonic() >= deadline:
                        break
                    try:
                        request = ledger.db.execute("SELECT fingerprint,cutoff FROM industry_requests WHERE scope=?", (work["scope"],)).fetchone()
                        request_cutoff = request["cutoff"] if request and request["fingerprint"] == work["fingerprint"] else cutoff
                        ledger.db.execute("INSERT OR REPLACE INTO industry_requests VALUES(?,?,?)", (work["scope"], work["fingerprint"], request_cutoff)); ledger.db.commit()
                        industry_common = [*config_args, "--universe", config["paths"]["universe"], "--date", day, "--cutoff", request_cutoff]
                        context = command(root, "earnings_industry_context.py", [*industry_common, "--industry", work["industry"],
                            "--role", "industry", "--mode", "daily", "--period-start", work["start"], "--period-end", work["end"],
                            "--run-id", f"{run_id}-{uuid.uuid4().hex[:8]}", "--lease-seconds", str(int(config["budgets"]["task_timeout_seconds"]) + 180)], logs, 120)
                        if not context.get("model_execution_required"):
                            if context.get("task_state") in {"completed", "terminal_failed"}:
                                ledger.db.execute("INSERT OR REPLACE INTO industry_inputs VALUES(?,?)", (work["scope"], work["fingerprint"])); ledger.db.commit()
                            if context.get("task_state") == "terminal_failed":
                                errors.append(f"industry:{work['industry']}:maximum attempts exhausted for unchanged inputs")
                            continue
                        path = context["artifacts"][0]
                        manifest = read_json(root / path)
                        ledger.reserve(day, manifest["task_id"], "industry", cap)
                        try:
                            reports.append(run_role(root, root / path, binary=deployed["codex_bin"],
                                timeout=min(int(config["budgets"]["task_timeout_seconds"]), max(1, int(deadline-time.monotonic())))))
                        except Exception as exc:
                            fail_owned_attempt(state, manifest, str(exc))
                            raise
                        ledger.db.execute("INSERT OR REPLACE INTO industry_inputs VALUES(?,?)", (work["scope"], work["fingerprint"])); ledger.db.commit()
                    except Exception as exc:
                        errors.append(f"industry:{work['industry']}:{exc}")
            exhausted = unresolved_terminal_count(state)
            if exhausted:
                errors.append(f"terminal_tasks:{exhausted} current research tasks require operator review")
            delivery = finalize(root, deployed, deployed_path, day, reports, errors, state, send=args.send)
            ledger.db.execute("INSERT OR REPLACE INTO health VALUES(?,?)", (day, int(bool(errors))))
            ledger.db.commit()
            health = ledger.db.execute("SELECT failed FROM health ORDER BY day DESC LIMIT 2").fetchall()
            if errors and len(health) == 2 and all(row[0] for row in health):
                # Persistent failures warrant one separate operational notice, deduplicated until content changes.
                body = "财报研究连续两个日批次未完整完成，请核对 NAS 本地 earnings 运行日志。\n" + "\n".join(errors[:3])[:1500]
                failure = prepare_notification(root, deployed, day=day, body=body, report_versions=[], kind="failure",
                    rationale="two distinct failed daily batches", should_send=True)
                delivery["failure_delivery"] = deliver(root, failure, deployed_path, execute=args.send)
            result = {"schema_version": 1, "workflow": "earnings-daily", "status": "success" if not errors else "failed",
                "run_id": run_id, "date": day, "cutoff": cutoff, "reports": reports, "errors": errors,
                "delivery": delivery, "completed_at": utc_now(), "quarterly_automatic_trigger": "P4_disabled"}
            atomic_write_json(logs / "daily-result.json", result)
            return result
        finally:
            state.close(); ledger.db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--config", default="config/earnings_research.json")
    parser.add_argument("--deployment", default="runtime/earnings/deployment.json")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--resume-only", action="store_true")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise InterruptedError(f"batch interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        result = run(args)
    except BlockingIOError:
        result = {"status": "skipped", "reason": "another earnings process holds the lock"}
    except Exception as exc:
        result = {"status": "failed", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result["status"] == "failed" else 0)


if __name__ == "__main__":
    main()
