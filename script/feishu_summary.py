#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

from journal_review import read_jsonl
from signal_artifacts import read_json, resolve_signals_path


def resolve_path(repo_root: Path, value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def output_path(repo_root: Path, date: str, explicit_output: str | None) -> Path:
    return resolve_path(repo_root, explicit_output, repo_root / "report" / date / "feishu-summary.md")


def artifact_json(repo_root: Path, path: str | None, default: Path) -> dict[str, Any]:
    target = resolve_path(repo_root, path, default)
    if not target.exists():
        return {}
    try:
        return read_json(target)
    except Exception:
        return {}


def default_run_manifest(repo_root: Path, date: str, session: str) -> Path:
    return repo_root / "report" / date / f"{session}-run-manifest.json"


def default_analysis_report(repo_root: Path, date: str, session: str) -> Path | None:
    report_dir = repo_root / "report" / date
    candidates = {
        "pre-market": [report_dir / "pre-market.md", report_dir / "exec-brief.md"],
        "post-market": [report_dir / "post-market.md"],
        "monitor": [report_dir / "intraday.md"],
    }.get(session, [])
    for path in candidates:
        if path.exists():
            return path
    return None


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def clean_heading(title: str) -> str:
    title = title.strip().strip("`")
    title = re.sub(r"^\d+[.、]\s*", "", title)
    return title.strip()


def markdown_sections(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    headings: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match:
            continue
        headings.append((idx, len(match.group(1)), clean_heading(match.group(2))))

    sections: list[dict[str, Any]] = []
    for pos, (start, level, title) in enumerate(headings):
        end = len(lines)
        for next_start, next_level, _ in headings[pos + 1 :]:
            if next_level <= level:
                end = next_start
                break
        sections.append({"title": title, "level": level, "lines": lines[start + 1 : end]})
    return sections


def section_by_title(sections: list[dict[str, Any]], candidates: list[str]) -> dict[str, Any] | None:
    for candidate in candidates:
        for section in sections:
            title = str(section.get("title") or "")
            if title == candidate or candidate in title:
                return section
    return None


def clean_content_line(line: str) -> str:
    value = line.strip()
    value = re.sub(r"^[-*]\s+", "", value)
    value = re.sub(r"^\d+[.)、]\s+", "", value)
    return value.strip()


def content_lines(section: dict[str, Any] | None, *, max_lines: int = 4) -> list[str]:
    if not section:
        return []
    result = []
    for line in section.get("lines", []):
        value = clean_content_line(str(line))
        if not value or value.startswith("#"):
            continue
        result.append(value)
        if len(result) >= max_lines:
            break
    return result


def derived_industry_lines(section: dict[str, Any] | None, *, max_lines: int = 4) -> list[str]:
    if not section:
        return []
    keywords = (
        "行业",
        "板块",
        "半导体",
        "科技",
        "AI",
        "电动车",
        "能源",
        "金融",
        "医疗",
        "消费",
        "软件",
        "大型科技",
        "高 beta",
    )
    result = []
    for line in section.get("lines", []):
        value = clean_content_line(str(line))
        if not value or value.startswith("#"):
            continue
        if any(keyword in value for keyword in keywords):
            result.append(value)
        if len(result) >= max_lines:
            break
    return result


def symbol_analysis_lines(section: dict[str, Any] | None, *, max_lines: int = 5) -> list[str]:
    if not section:
        return []
    preferred = [
        "结构结论",
        "结构",
        "观察条件",
        "明日主观察",
        "触发条件",
        "关键位",
        "失效/放弃条件",
        "风险提醒",
        "风险约束",
        "风险",
        "未升级为交易计划的原因",
    ]
    raw = [clean_content_line(str(line)) for line in section.get("lines", [])]
    raw = [line for line in raw if line and not line.startswith("#")]
    selected: list[str] = []
    for key in preferred:
        for line in raw:
            if line in selected:
                continue
            if line.startswith(f"{key}：") or line.startswith(f"{key}:"):
                selected.append(line)
                break
        if len(selected) >= max_lines:
            return selected
    for line in raw:
        if line not in selected:
            selected.append(line)
        if len(selected) >= max_lines:
            break
    return selected


def analysis_report_digest(repo_root: Path, date: str, session: str, symbols: list[str]) -> dict[str, Any]:
    path = default_analysis_report(repo_root, date, session)
    if not path:
        return {"available": False}
    markdown = path.read_text(encoding="utf-8")
    sections = markdown_sections(markdown)

    core_section = section_by_title(sections, ["结论", "复盘结论", "总览", "市场环境"])
    market_section = section_by_title(sections, ["市场复盘", "市场环境", "大盘复盘", "全市场复盘", "全市场"])
    industry_section = section_by_title(
        sections,
        ["行业复盘", "主要行业", "主要行业分析", "行业/板块", "行业板块", "板块复盘", "板块分析"],
    )
    focus_section = section_by_title(sections, ["今日最多3个重点标的", "明日观察清单", "明日最多3个重点观察标的"])
    risk_section = section_by_title(sections, ["组合风控", "组合与流程风控", "NO TRADE"])
    position_advice_section = section_by_title(
        sections,
        ["持仓交易建议", "持仓与组合风险", "持仓与组合风险复盘"],
    )
    trade_review_section = section_by_title(sections, ["当日交易复盘"])

    symbol_sections = []
    seen = set()
    for symbol in symbols:
        normalized = symbol.upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        section = section_by_title(sections, [normalized])
        lines = symbol_analysis_lines(section)
        if lines:
            symbol_sections.append({"symbol": normalized, "lines": lines})

    return {
        "available": True,
        "path": str(path.relative_to(repo_root)),
        "core": content_lines(core_section, max_lines=4),
        "market": content_lines(market_section, max_lines=4),
        "industries": content_lines(industry_section, max_lines=4) or derived_industry_lines(market_section, max_lines=4),
        "industry_source": "section" if industry_section else "derived",
        "focus": content_lines(focus_section, max_lines=6),
        "symbols": symbol_sections,
        "risk": content_lines(risk_section, max_lines=5),
        "position_advice": content_lines(position_advice_section, max_lines=10),
        "trade_review": content_lines(trade_review_section, max_lines=12),
    }


INDEX_SYMBOLS = {"SPY", "QQQ", "DIA", "IWM", "SPX", "NDX", "VIX"}
INDEX_ORDER = ["SPY", "QQQ", "DIA", "IWM", "SPX", "NDX", "VIX"]


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def symbol_change_pct(symbol_snapshot: dict[str, Any]) -> float | None:
    metrics = symbol_snapshot.get("metrics") if isinstance(symbol_snapshot.get("metrics"), dict) else {}
    metric_value = as_float(metrics.get("close_delta_pct"))
    if metric_value is not None:
        return metric_value
    latest = symbol_snapshot.get("latest") if isinstance(symbol_snapshot.get("latest"), dict) else {}
    previous = symbol_snapshot.get("previous") if isinstance(symbol_snapshot.get("previous"), dict) else {}
    latest_close = as_float(latest.get("close"))
    previous_close = as_float(previous.get("close"))
    if latest_close is None or previous_close in (None, 0):
        return None
    return (latest_close - previous_close) / previous_close * 100


def symbol_sector(symbol_snapshot: dict[str, Any]) -> str:
    meta = symbol_snapshot.get("meta") if isinstance(symbol_snapshot.get("meta"), dict) else {}
    value = (
        symbol_snapshot.get("sector")
        or symbol_snapshot.get("industry")
        or meta.get("sector")
        or meta.get("industry")
    )
    return str(value).strip() if value not in (None, "") else ""


def pct_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def symbol_move_text(row: dict[str, Any]) -> str:
    return f"{row.get('symbol')} {pct_text(row.get('pct'))}"


def context_item_pct(item: dict[str, Any]) -> float | None:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    return as_float(metrics.get("close_delta_pct"))


def context_move_text(item: dict[str, Any], *, include_label: bool = True) -> str:
    label = str(item.get("label") or item.get("symbol") or "")
    symbol = str(item.get("symbol") or "")
    name = f"{label}({symbol})" if include_label and label and label != symbol else symbol
    return f"{name} {pct_text(context_item_pct(item))}"


def market_context_lines(market_context: dict[str, Any]) -> list[str]:
    if not market_context:
        return []
    market_items = market_context.get("market") if isinstance(market_context.get("market"), list) else []
    errors = market_context.get("errors") if isinstance(market_context.get("errors"), list) else []
    summary = market_context.get("summary") if isinstance(market_context.get("summary"), dict) else {}
    market_summary = summary.get("market") if isinstance(summary.get("market"), dict) else {}
    lines = [
        (
            f"- Longbridge 大盘数据：status={market_context.get('status', 'unknown')}；"
            f"可评估 {market_summary.get('count', len(market_items))} 个；"
            f"上涨 {market_summary.get('up', 0)} / 下跌 {market_summary.get('down', 0)} / "
            f"持平 {market_summary.get('flat', 0)}；平均 {pct_text(market_summary.get('avg_pct'))}。"
        )
    ]
    if market_items:
        lines.append("- 主要指数/风向标：" + "；".join(context_move_text(item) for item in market_items[:8]))
    if errors:
        lines.append(f"- Longbridge 大盘/行业抓取错误：{len(errors)} 个；已在 market-context artifact 记录。")
    return lines


def industry_context_lines(market_context: dict[str, Any]) -> list[str]:
    if not market_context:
        return []
    items = market_context.get("industries") if isinstance(market_context.get("industries"), list) else []
    valid = [item for item in items if context_item_pct(item) is not None]
    if not valid:
        return ["- Longbridge 行业 ETF 数据不可用；请查看 longbridge-market-context artifact 的 errors。"]
    leaders = sorted(valid, key=lambda item: context_item_pct(item) or 0, reverse=True)[:4]
    laggards = sorted(valid, key=lambda item: context_item_pct(item) or 0)[:4]
    return [
        "- Longbridge 行业代理 ETF 领涨：" + "；".join(context_move_text(item) for item in leaders),
        "- Longbridge 行业代理 ETF 领跌/防御：" + "；".join(context_move_text(item) for item in laggards),
    ]


def market_snapshot_digest(repo_root: Path, date: str) -> dict[str, Any]:
    path = repo_root / "report" / date / "daily-snapshot.json"
    if not path.exists():
        return {"available": False}
    snapshot = artifact_json(repo_root, str(path), path)
    symbols = snapshot.get("symbols") if isinstance(snapshot.get("symbols"), list) else []
    rows: list[dict[str, Any]] = []
    for item in symbols:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        pct = symbol_change_pct(item)
        if not symbol or pct is None:
            continue
        rows.append({"symbol": symbol, "pct": pct, "sector": symbol_sector(item)})

    breadth_rows = [row for row in rows if row["symbol"] not in INDEX_SYMBOLS]
    if not breadth_rows:
        breadth_rows = [row for row in rows if row["symbol"] != "VIX"]
    pcts = [row["pct"] for row in breadth_rows]
    up = len([pct for pct in pcts if pct > 0])
    down = len([pct for pct in pcts if pct < 0])
    flat = len([pct for pct in pcts if pct == 0])
    top_gainers = sorted(breadth_rows, key=lambda row: row["pct"], reverse=True)[:3]
    top_losers = sorted(breadth_rows, key=lambda row: row["pct"])[:3]

    sector_buckets: dict[str, list[dict[str, Any]]] = {}
    for row in breadth_rows:
        sector = row.get("sector")
        if not sector:
            continue
        sector_buckets.setdefault(str(sector), []).append(row)
    sectors = []
    for sector, sector_rows in sector_buckets.items():
        sector_pcts = [row["pct"] for row in sector_rows]
        sectors.append(
            {
                "sector": sector,
                "count": len(sector_rows),
                "up": len([pct for pct in sector_pcts if pct > 0]),
                "down": len([pct for pct in sector_pcts if pct < 0]),
                "avg_pct": round(sum(sector_pcts) / len(sector_pcts), 2),
                "leader": max(sector_rows, key=lambda row: row["pct"]),
                "laggard": min(sector_rows, key=lambda row: row["pct"]),
            }
        )
    sectors.sort(key=lambda row: (abs(row["avg_pct"]), row["count"]), reverse=True)

    index_lookup = {row["symbol"]: row for row in rows if row["symbol"] in INDEX_SYMBOLS}
    return {
        "available": True,
        "path": str(path.relative_to(repo_root)),
        "market_data_source": snapshot.get("market_data_source"),
        "dynamic_universe_enabled": bool(snapshot.get("dynamic_universe_enabled")),
        "symbol_count": len(rows),
        "breadth_count": len(breadth_rows),
        "up": up,
        "down": down,
        "flat": flat,
        "avg_pct": round(sum(pcts) / len(pcts), 2) if pcts else None,
        "median_pct": round(statistics.median(pcts), 2) if pcts else None,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "indices": [index_lookup[symbol] for symbol in INDEX_ORDER if symbol in index_lookup],
        "sectors": sectors[:5],
        "sector_count": len(sectors),
    }


def market_snapshot_lines(snapshot_digest: dict[str, Any]) -> list[str]:
    if not snapshot_digest.get("available"):
        return ["- daily-snapshot 未找到；无法生成结构化市场/行业统计。"]
    lines = [
        (
            f"- 快照：{snapshot_digest.get('path')}；数据源={snapshot_digest.get('market_data_source') or 'unknown'}；"
            f"dynamic_universe={snapshot_digest.get('dynamic_universe_enabled')}"
        ),
        (
            f"- 观察池广度：可评估 {snapshot_digest.get('breadth_count', 0)} 个；"
            f"上涨 {snapshot_digest.get('up', 0)} / 下跌 {snapshot_digest.get('down', 0)} / "
            f"持平 {snapshot_digest.get('flat', 0)}；平均 {pct_text(snapshot_digest.get('avg_pct'))}；"
            f"中位 {pct_text(snapshot_digest.get('median_pct'))}。"
        ),
    ]
    indices = snapshot_digest.get("indices") if isinstance(snapshot_digest.get("indices"), list) else []
    if indices:
        lines.append("- 主要指数/风向标：" + "；".join(symbol_move_text(row) for row in indices))
    top_gainers = snapshot_digest.get("top_gainers") if isinstance(snapshot_digest.get("top_gainers"), list) else []
    top_losers = snapshot_digest.get("top_losers") if isinstance(snapshot_digest.get("top_losers"), list) else []
    if top_gainers:
        lines.append("- 观察池领涨：" + "；".join(symbol_move_text(row) for row in top_gainers))
    if top_losers:
        lines.append("- 观察池领跌：" + "；".join(symbol_move_text(row) for row in top_losers))
    return lines


def sector_snapshot_lines(snapshot_digest: dict[str, Any]) -> list[str]:
    sectors = snapshot_digest.get("sectors") if isinstance(snapshot_digest.get("sectors"), list) else []
    if not sectors:
        return ["- daily-snapshot 未提供可聚合的 sector/industry 元数据；行业判断仅引用报告文本与候选标的背景。"]
    lines = []
    for sector in sectors[:4]:
        leader = sector.get("leader") if isinstance(sector.get("leader"), dict) else {}
        laggard = sector.get("laggard") if isinstance(sector.get("laggard"), dict) else {}
        lines.append(
            f"- {sector.get('sector')}：{sector.get('count')} 个；均值 {pct_text(sector.get('avg_pct'))}；"
            f"上涨 {sector.get('up', 0)}/{sector.get('count', 0)}；"
            f"强={symbol_move_text(leader) if leader else 'N/A'}；"
            f"弱={symbol_move_text(laggard) if laggard else 'N/A'}。"
        )
    return lines


def validation_statuses(run_manifest: dict[str, Any]) -> list[str]:
    statuses = []
    for step in run_manifest.get("steps", []):
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or "")
        if not (
            name.startswith("validate-")
            or name in {"data-quality", "validate-report", "validate-trade-plan"}
        ):
            continue
        status = step.get("status") or "unknown"
        detail = ""
        stdout = step.get("stdout")
        if isinstance(stdout, dict):
            validation = stdout.get("validation") if isinstance(stdout.get("validation"), dict) else stdout.get("stdout")
            if isinstance(validation, dict):
                warnings = validation.get("warnings")
                if warnings:
                    detail = f"；warnings={len(warnings)}"
        statuses.append(f"- {name}：{status}{detail}")
    return statuses


def compact_validation_status(run_manifest: dict[str, Any]) -> str:
    statuses = []
    for step in run_manifest.get("steps", []):
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or "")
        if name in {"validate-report", "validate-trade-plan", "data-quality"} or name.startswith("validate-agent"):
            statuses.append(str(step.get("status") or "unknown"))
    if not statuses:
        return "未记录"
    if all(status == "success" for status in statuses):
        return "通过"
    return " / ".join(statuses)


def manifest_step(run_manifest: dict[str, Any], name: str) -> dict[str, Any]:
    for step in run_manifest.get("steps", []):
        if isinstance(step, dict) and step.get("name") == name:
            return step
    return {}


def step_summary(run_manifest: dict[str, Any], name: str) -> dict[str, Any]:
    stdout = manifest_step(run_manifest, name).get("stdout")
    return stdout if isinstance(stdout, dict) else {}


def step_status(run_manifest: dict[str, Any], name: str) -> str:
    step = manifest_step(run_manifest, name)
    stdout = step.get("stdout")
    if isinstance(stdout, dict) and stdout.get("status"):
        return str(stdout.get("status"))
    return str(step.get("status") or "unknown")


def relative_path(repo_root: Path, value: str | Path | None) -> str:
    if value is None:
        return ""
    path = Path(str(value))
    if not path.is_absolute():
        return str(path)
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def llm_generation_payload(repo_root: Path, date: str, session: str) -> dict[str, Any]:
    path = repo_root / "report" / date / f"{session}-llm-generation.json"
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
    except Exception:
        return {}
    payload["_path"] = str(path.relative_to(repo_root))
    return payload


def agent_decision_paths(repo_root: Path, date: str, signals: list[dict[str, Any]]) -> list[tuple[str, str]]:
    paths = []
    seen = set()
    for signal in signals:
        symbol = signal_symbol(signal)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        path = repo_root / "report" / date / "agents" / symbol / "decision.json"
        if path.exists():
            paths.append((symbol, str(path.relative_to(repo_root))))
    return paths


def focus_selection(repo_root: Path, date: str) -> dict[str, Any]:
    path = repo_root / "report" / date / "focus-selection.json"
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def intraday_review(repo_root: Path, date: str) -> dict[str, Any]:
    intraday_md = repo_root / "report" / date / "intraday.md"
    state_path = repo_root / "runtime" / "intraday" / date / "state.json"
    events_path = repo_root / "runtime" / "intraday" / date / "events.jsonl"
    sent_path = repo_root / "runtime" / "intraday" / date / "sent-events.json"

    state = artifact_json(repo_root, str(state_path), state_path)
    events = read_jsonl_records(events_path)
    sent = artifact_json(repo_root, str(sent_path), sent_path)
    sent_ids = sent.get("sent_event_ids") if isinstance(sent.get("sent_event_ids"), list) else []

    symbols = state.get("symbols") if isinstance(state.get("symbols"), dict) else {}
    state_counts: dict[str, int] = {}
    current_states: list[str] = []
    for symbol, payload in symbols.items():
        if not isinstance(payload, dict):
            continue
        state_name = str(payload.get("state") or "unknown")
        state_counts[state_name] = state_counts.get(state_name, 0) + 1
        current_states.append(f"{symbol}:{state_name}")

    artifacts = [
        str(path.relative_to(repo_root))
        for path in (intraday_md, state_path, events_path)
        if path.exists()
    ]
    return {
        "available": bool(artifacts or state or events),
        "artifacts": artifacts,
        "focus_symbols": state.get("focus_symbols") if isinstance(state.get("focus_symbols"), list) else [],
        "generated_at": state.get("generated_at"),
        "state_counts": state_counts,
        "current_states": current_states,
        "event_count": len(events),
        "notify_event_count": len([event for event in events if event.get("notify")]),
        "sent_event_count": len(sent_ids),
        "latest_events": events[-3:],
    }


def workflow_review_artifact(repo_root: Path, date: str, explicit_path: str | None = None) -> dict[str, Any]:
    return artifact_json(repo_root, explicit_path, repo_root / "report" / date / "workflow-review.json")


def longbridge_market_context(repo_root: Path, date: str, explicit_path: str | None = None) -> dict[str, Any]:
    return artifact_json(repo_root, explicit_path, repo_root / "report" / date / "longbridge-market-context.json")


def session_title(session: str) -> str:
    if session == "monitor":
        return "盘中"
    return "今日" if session == "pre-market" else "明日"


def signal_symbol(signal: dict[str, Any]) -> str:
    return str(signal.get("symbol") or "").upper()


def compact_price(value: Any) -> str:
    if value in (None, ""):
        return "待定"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def compact_text(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(item) for item in value if item not in (None, ""))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value in (None, ""):
        return "无补充"
    return str(value)


def append_bullets(lines: list[str], items: list[str]) -> None:
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        lines.append(text if text.startswith("-") else f"- {text}")


def signal_note(signal: dict[str, Any]) -> str:
    return str(signal.get("notes") or signal.get("setup") or "无补充")


def nested_text(payload: dict[str, Any], key: str, *fallback_keys: str) -> str:
    value = payload.get(key)
    if isinstance(value, dict):
        for name in ("text", *fallback_keys):
            if value.get(name):
                return str(value.get(name))
    return compact_text(value)


def signal_analysis_line(signal: dict[str, Any]) -> str:
    symbol = signal_symbol(signal)
    status = str(signal.get("execution_status") or signal.get("plan_type") or "unknown")
    setup = str(signal.get("setup") or "未标注 setup")
    note = signal_note(signal)

    if status == "conditional_executable":
        entry = compact_price((signal.get("entry") or {}).get("trigger_price"))
        stop = compact_price((signal.get("stop") or {}).get("initial_stop"))
        tp1 = compact_price((signal.get("take_profit") or {}).get("tp1"))
        risk = compact_price((signal.get("risk") or {}).get("max_account_risk_pct"))
        confirmation = (signal.get("entry") or {}).get("confirmation")
        confirmation_text = f"；确认：{confirmation}" if confirmation else ""
        return (
            f"- {symbol}：conditional_executable；setup={setup}；触发 {entry}；止损 {stop}；"
            f"TP1 {tp1}；账户风险 <= {risk}%{confirmation_text}。"
        )

    if status == "no_trade" or signal.get("plan_type") == "no_trade" or signal.get("status") == "no_trade":
        return f"- {symbol}：NO TRADE；{note}"

    trigger = nested_text(signal, "trigger")
    invalidation = nested_text(signal, "invalidation")
    risk = nested_text(signal, "risk")
    return (
        f"- {symbol}：watch_only；setup={setup}；{note}。"
        f"观察：{trigger}；失效/放弃：{invalidation}；风险：{risk}"
    )


def split_signals(signals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    executable = []
    watch = []
    no_trade = []
    for signal in signals:
        status = signal.get("execution_status")
        plan_type = signal.get("plan_type")
        if status == "conditional_executable" and plan_type == "trade_plan":
            executable.append(signal)
        elif status == "no_trade" or plan_type == "no_trade" or signal.get("status") == "no_trade":
            no_trade.append(signal)
        else:
            watch.append(signal)
    return executable, watch, no_trade


def lessons_for_date(repo_root: Path, date: str, learning_dir: str) -> list[dict[str, Any]]:
    path = Path(learning_dir)
    if not path.is_absolute():
        path = repo_root / path
    return [
        record
        for record in read_jsonl(path / "daily_lessons.jsonl")
        if record.get("kind") == "daily_lesson" and record.get("date") == date
    ]


def build_markdown(
    *,
    date: str,
    session: str,
    signals: list[dict[str, Any]],
    position_review: dict[str, Any],
    plan_review: dict[str, Any],
    data_quality: dict[str, Any],
    run_manifest: dict[str, Any],
    repo_root: Path,
    lessons: list[dict[str, Any]],
    signal_source_summary: dict[str, Any] | None = None,
    focus_selection_payload: dict[str, Any] | None = None,
    intraday_payload: dict[str, Any] | None = None,
    workflow_review_payload: dict[str, Any] | None = None,
    analysis_digest_payload: dict[str, Any] | None = None,
    market_snapshot_payload: dict[str, Any] | None = None,
    market_context_payload: dict[str, Any] | None = None,
) -> str:
    title_prefix = session_title(session)
    executable, watch, no_trade = split_signals(signals)
    position_summary = position_review.get("summary") if isinstance(position_review.get("summary"), dict) else {}
    plan_summary = plan_review.get("summary") if isinstance(plan_review.get("summary"), dict) else {}

    workflow = run_manifest.get("workflow")
    source_snapshot_date = None
    context = run_manifest.get("context")
    if isinstance(context, dict):
        source_snapshot_date = context.get("source_snapshot_date")

    session_label = {"pre-market": "盘前", "post-market": "盘后", "monitor": "盘中"}.get(session, session)
    analysis_digest = analysis_digest_payload or {}
    digest_core = analysis_digest.get("core") if isinstance(analysis_digest.get("core"), list) else []
    digest_focus = analysis_digest.get("focus") if isinstance(analysis_digest.get("focus"), list) else []
    digest_symbols = analysis_digest.get("symbols") if isinstance(analysis_digest.get("symbols"), list) else []
    digest_risk = analysis_digest.get("risk") if isinstance(analysis_digest.get("risk"), list) else []
    digest_position_advice = (
        analysis_digest.get("position_advice")
        if isinstance(analysis_digest.get("position_advice"), list)
        else []
    )
    digest_trade_review = (
        analysis_digest.get("trade_review")
        if isinstance(analysis_digest.get("trade_review"), list)
        else []
    )
    digest_market = analysis_digest.get("market") if isinstance(analysis_digest.get("market"), list) else []
    digest_industries = analysis_digest.get("industries") if isinstance(analysis_digest.get("industries"), list) else []
    market_snapshot = market_snapshot_payload or {}
    market_context = market_context_payload or {}
    lines = [
        f"# {session_label}分析摘要（{date}）",
        "",
        "【核心结论】",
    ]
    if analysis_digest.get("path"):
        lines.append(f"- 来源：{analysis_digest.get('path')}")
    overview = digest_core
    if overview:
        append_bullets(lines, overview)
    elif executable:
        lines.append(f"- 有 {len(executable)} 个条件执行计划；仍需人工确认触发、失效和风险。")
    elif watch:
        lines.append(f"- 无可直接执行交易计划；{len(watch)} 个重点标的仅作观察候选。")
    else:
        lines.append("- 无重点交易计划；默认 NO TRADE。")

    if session == "post-market":
        lines.extend(["", "【市场与行业】"])
        context_lines = market_context_lines(market_context)
        append_bullets(lines, context_lines or market_snapshot_lines(market_snapshot))
        has_context_market = bool(
            market_context
            and any(
                context_item_pct(item) is not None
                for item in (market_context.get("market") if isinstance(market_context.get("market"), list) else [])
            )
        )
        if market_context and not has_context_market:
            lines.append("- Longbridge 大盘数据不可用，以下补充 daily-snapshot 观察池 fallback：")
            append_bullets(lines, market_snapshot_lines(market_snapshot))
        if digest_market:
            lines.append("- 报告市场判断：")
            for item in digest_market[:4]:
                lines.append(f"  - {item}")
        lines.append("- 主要行业/板块：")
        industry_lines = industry_context_lines(market_context) if market_context else sector_snapshot_lines(market_snapshot)
        for item in industry_lines:
            lines.append(f"  {item}")
        has_context_industries = bool(
            market_context
            and any(
                context_item_pct(item) is not None
                for item in (
                    market_context.get("industries")
                    if isinstance(market_context.get("industries"), list)
                    else []
                )
            )
        )
        if market_context and not has_context_industries:
            for item in sector_snapshot_lines(market_snapshot):
                lines.append(f"  {item}")
        if digest_industries and analysis_digest.get("industry_source") == "section":
            lines.append("- 报告行业判断：")
            for item in digest_industries[:4]:
                lines.append(f"  - {item}")

    if digest_focus:
        lines.extend(["", "【重点标的】"])
        append_bullets(lines, digest_focus)

    lines.extend(["", f"【{title_prefix}重点标的分析】"])
    focus_signals = executable + watch
    if digest_symbols:
        for item in digest_symbols:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('symbol')}")
            for detail in item.get("lines", [])[:5]:
                lines.append(f"  - {detail}")
    elif not focus_signals:
        lines.append("- 无。")
    else:
        for signal in focus_signals:
            lines.append(signal_analysis_line(signal))

    lines.extend(["", "【NO TRADE】"])
    if not no_trade:
        lines.append("- 无。")
    for signal in no_trade:
        lines.append(signal_analysis_line(signal))

    if digest_risk:
        lines.extend(["", "【风控与放弃条件】"])
        append_bullets(lines, digest_risk)

    position_rows = (
        position_review.get("position_reviews")
        if isinstance(position_review.get("position_reviews"), list)
        else []
    )
    lines.extend(["", "【持仓与组合风险】"])
    if digest_position_advice:
        lines.append("- 报告条件化建议：")
        for item in digest_position_advice[:10]:
            lines.append(f"  - {item}")
    if not position_rows:
        lines.append("- 未取得可用持仓复核；本节不对账户仓位作推断。")
    else:
        brokers = position_summary.get("brokers")
        overlaps = position_summary.get("cross_broker_overlaps")
        lines.append(
            f"- 券商覆盖：{', '.join(brokers) if isinstance(brokers, list) and brokers else '未知'}；"
            f"持仓数：{position_summary.get('positions', len(position_rows))}；"
            f"需人工复核：{position_summary.get('review_required', 0)}"
        )
        lines.append(
            "- 跨账户重复持仓："
            + (", ".join(overlaps) if isinstance(overlaps, list) and overlaps else "无")
        )
        if position_summary.get("concentration_currency_limited"):
            lines.append(
                f"- {position_summary.get('concentration_currency_limited')} 个持仓与账户净资产币种不可比；"
                "未计算组合集中度。"
            )
        flagged = [row for row in position_rows if isinstance(row, dict) and row.get("review_required")]
        for row in (flagged or [row for row in position_rows if isinstance(row, dict)])[:5]:
            broker_text = ",".join(row.get("brokers", [])) if isinstance(row.get("brokers"), list) else "未知"
            lines.append(
                f"- {row.get('symbol')}：risk={row.get('risk_state', 'unknown')}；"
                f"brokers={broker_text}；计划内={'是' if row.get('in_today_signals') else '否'}；"
                f"集中度={row.get('concentration_pct', '未知')}%"
            )
        lines.append("- 仅作持仓一致性与风险复核，不产生加仓、减仓或卖出指令。")

    if session == "post-market":
        lines.extend(["", "【当日交易复盘】"])
        if digest_trade_review:
            append_bullets(lines, digest_trade_review)
        else:
            lines.append("- 未取得可核验的当日成交复盘；不能据此推断当日无交易。")
        lines.append("- 订单上下文不等于成交；本节只评价已发生的 executions。")

    focused_fallback = data_quality.get("focused_fallback_symbols")
    quality_parts = [
        f"状态：{data_quality.get('quality_status') or data_quality.get('status', 'unknown')}"
    ]
    if data_quality.get("stale_data"):
        dates = data_quality.get("latest_bar_dates")
        date_text = ", ".join(str(item) for item in dates) if isinstance(dates, list) else "unknown"
        quality_parts.append(
            f"stale_data=True，reason={data_quality.get('stale_reason') or 'unknown'}，"
            f"latest_bar_dates={date_text}"
        )
    if isinstance(focused_fallback, list) and focused_fallback:
        fallback_items = []
        for row in focused_fallback:
            if isinstance(row, dict):
                fallback_items.append(
                    f"{row.get('symbol')} 使用 {row.get('provider')} fallback"
                    f"（primary={row.get('fallback_from')}，原因={row.get('primary_error')}）"
                )
        quality_parts.append("focused fallback：" + "、".join(fallback_items))
    else:
        quality_parts.append("无 focused fallback 风险")
    lines.extend(["", "【数据质量】", f"- {'；'.join(quality_parts)}。"])

    if session == "monitor":
        summary = signal_source_summary or {}
        lines.extend(
            [
                "",
                "【盘中候选状态】",
                f"- candidate：{summary.get('candidate', len(watch))}",
                f"- blocked：{summary.get('blocked', 0)}",
                f"- skipped：{summary.get('skipped', 0)}",
                "- monitor 候选仅用于 dry-run 和人工观察，不能自动执行。",
            ]
        )

    if session == "post-market":
        intraday = intraday_payload or {}
        lines.extend(["", "【盘中监控回顾】"])
        if not intraday.get("available"):
            lines.append("- 今日无盘中监控产物；盘后报告只能基于日线 snapshot 和已记录 journal 复盘。")
        else:
            focus_symbols = intraday.get("focus_symbols") if isinstance(intraday.get("focus_symbols"), list) else []
            state_counts = intraday.get("state_counts") if isinstance(intraday.get("state_counts"), dict) else {}
            current_states = intraday.get("current_states") if isinstance(intraday.get("current_states"), list) else []
            lines.append(f"- 关注池：{', '.join(focus_symbols) if focus_symbols else '无'}")
            lines.append(
                "- 状态分布："
                + (
                    ", ".join(f"{key}={value}" for key, value in sorted(state_counts.items()))
                    if state_counts
                    else "无"
                )
            )
            if current_states:
                lines.append(f"- 最新状态：{', '.join(current_states[:8])}")
            lines.append("- 盘中监控只用于复盘和提醒，不作为交易指令。")

        workflow_review = workflow_review_payload or {}
        workflow_summary = workflow_review.get("summary") if isinstance(workflow_review.get("summary"), dict) else {}
        missed = workflow_summary.get("missed_or_misjudged") if isinstance(workflow_summary.get("missed_or_misjudged"), dict) else {}
        lines.extend(["", "【当日复盘结论】"])
        if not workflow_review:
            lines.append("- 未生成 workflow-review；请检查 post-market-deliver 是否跳过 daily-workflow-review。")
        else:
            lines.append(
                f"- 可能漏接候选：{missed.get('possible_missed_candidates', 0)}；"
                f"触价后回落/失效：{missed.get('touch_fade_or_invalidated', 0)}；"
                f"未触发：{missed.get('not_triggered', 0)}"
            )
            if missed.get("confirmed_no_missed_executable"):
                lines.append("- 结论：未发现可执行漏判；触价不等于完整交易计划。")
            else:
                lines.append("- 结论：存在可能漏接候选，需要人工复核盘中确认、RR 和风险。")

    if plan_summary and session == "post-market":
        lines.extend(
            [
                "",
                "【昨日计划复盘】",
                f"- 计划数：{plan_summary.get('plans', 0)}；"
                f"价格触达：{json.dumps(plan_summary.get('outcomes', {}), ensure_ascii=False, sort_keys=True)}；"
                f"真实执行：{json.dumps(plan_summary.get('trade_state', {}), ensure_ascii=False, sort_keys=True)}",
            ]
        )
    if lessons:
        lines.extend(["", "【新增规则提醒】"])
        for lesson in lessons[:3]:
            lines.append(f"- {lesson.get('problem')}：{lesson.get('suggested_constraint')}")

    focus_payload = focus_selection_payload or {}
    selected_focus = focus_payload.get("selected") if isinstance(focus_payload.get("selected"), list) else []
    validation_parts = [
        f"validation：{compact_validation_status(run_manifest)}",
        f"data_quality：{data_quality.get('quality_status') or data_quality.get('status', 'unknown')}",
        f"持仓数：{position_summary.get('positions', 0)}",
        f"重点标的数：{len(selected_focus) if selected_focus else len(focus_signals)}",
    ]
    if source_snapshot_date:
        validation_parts.append(f"source_snapshot_date：{source_snapshot_date}")
    sync = step_summary(run_manifest, "sync-longbridge-watchlist")
    if sync:
        validation_parts.append(
            f"watchlist_sync：{step_status(run_manifest, 'sync-longbridge-watchlist')}，"
            f"symbols={', '.join(sync.get('symbols', []) or [])}"
        )
    lines.extend(["", "【运行校验】", f"- {'；'.join(validation_parts)}。"])

    llm_generation = llm_generation_payload(repo_root, date, session)
    focus_payload = focus_selection_payload or {}
    backfill_stdout = step_summary(run_manifest, "backfill-signal-outcomes")
    extract_stdout = step_summary(run_manifest, "extract-report-signals")
    position_stdout = step_summary(run_manifest, "position-review")
    plan_stdout = step_summary(run_manifest, "plan-review")
    learning_stdout = step_summary(run_manifest, "learning-review")
    self_review_stdout = step_summary(run_manifest, "daily-self-review")
    workflow_review_stdout = step_summary(run_manifest, "daily-workflow-review")
    position_stdout_summary = (
        position_stdout.get("summary") if isinstance(position_stdout.get("summary"), dict) else {}
    )
    decision_paths = agent_decision_paths(repo_root, date, focus_signals)
    manifest_artifacts = run_manifest.get("artifacts") if isinstance(run_manifest.get("artifacts"), list) else []
    selected_focus = focus_payload.get("selected") if isinstance(focus_payload.get("selected"), list) else []
    focus_conditional_count = sum(
        1
        for item in selected_focus
        if isinstance(item, dict) and item.get("execution_status") == "conditional_executable"
    )
    focus_watch_count = sum(
        1
        for item in selected_focus
        if isinstance(item, dict) and item.get("execution_status") == "watch_only"
    )
    focus_no_trade_count = sum(
        1
        for item in selected_focus
        if isinstance(item, dict) and item.get("execution_status") == "no_trade"
    )
    audit_parts = [f"workflow/date：{workflow or session}-{date}"]
    if llm_generation:
        outputs = llm_generation.get("outputs") if isinstance(llm_generation.get("outputs"), list) else []
        output_paths = [str(item.get("path")) for item in outputs if isinstance(item, dict) and item.get("path")]
        audit_parts.append(
            f"LLM generation：model={llm_generation.get('model') or 'unknown'}，"
            f"manifest={llm_generation.get('_path')}"
        )
        audit_parts.append(f"generated artifacts：{', '.join(output_paths) if output_paths else '未记录'}")
    else:
        audit_parts.append("LLM generation：未记录")
    audit_parts.append(
        f"focus-selection：selected={len(selected_focus)}，"
        f"conditional_executable={focus_conditional_count}，"
        f"watch_only={focus_watch_count}，"
        f"no_trade={focus_no_trade_count}"
    )
    appended = extract_stdout.get("appended") if isinstance(extract_stdout.get("appended"), list) else []
    skipped_duplicates = (
        extract_stdout.get("skipped_duplicates")
        if isinstance(extract_stdout.get("skipped_duplicates"), list)
        else []
    )
    outcome_appended = (
        backfill_stdout.get("appended") if isinstance(backfill_stdout.get("appended"), list) else []
    )
    outcome_skipped = (
        backfill_stdout.get("skipped_duplicates")
        if isinstance(backfill_stdout.get("skipped_duplicates"), list)
        else []
    )
    self_review_appended = (
        self_review_stdout.get("appended") if isinstance(self_review_stdout.get("appended"), list) else []
    )
    audit_parts.append(
        f"journal append：signals_appended={len(appended)}，"
        f"outcomes_appended={len(outcome_appended)}，"
        f"self_review_appended={len(self_review_appended)}，"
        f"skipped_duplicates={len(skipped_duplicates) + len(outcome_skipped)}"
    )
    audit_parts.append(
        f"position review：positions={position_stdout_summary.get('positions', position_summary.get('positions', 0))}，"
        f"planned_signals={position_stdout_summary.get('planned_signals', len(focus_signals))}"
    )
    plan_artifacts = plan_stdout.get("artifacts") if isinstance(plan_stdout.get("artifacts"), list) else []
    learning_artifacts = learning_stdout.get("artifacts") if isinstance(learning_stdout.get("artifacts"), list) else []
    workflow_artifacts = (
        workflow_review_stdout.get("artifacts")
        if isinstance(workflow_review_stdout.get("artifacts"), list)
        else []
    )
    self_review_output = self_review_stdout.get("output")
    audit_parts.append(
        f"review artifacts：plan={', '.join(relative_path(repo_root, item) for item in plan_artifacts) or '未记录'}，"
        f"learning={', '.join(relative_path(repo_root, item) for item in learning_artifacts) or '未记录'}，"
        f"self={relative_path(repo_root, self_review_output) or '未记录'}，"
        f"workflow={', '.join(relative_path(repo_root, item) for item in workflow_artifacts) or '未记录'}"
    )
    if decision_paths:
        audit_parts.append(
            "agent decision artifacts："
            + ", ".join(f"{symbol}={path}" for symbol, path in decision_paths)
        )
    if manifest_artifacts:
        audit_parts.append(
            "workflow artifacts："
            + ", ".join(relative_path(repo_root, artifact) for artifact in manifest_artifacts[:8])
        )
    lines.extend(["", "【交付审计】", f"- {'；'.join(audit_parts)}。"])
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    signals_file = resolve_signals_path(repo_root, args.date, args.signals, args.session)
    payload = read_json(signals_file)
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    signal_source_summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    position_review = artifact_json(repo_root, args.position_review, repo_root / "report" / args.date / "position-review.json")
    plan_review = artifact_json(repo_root, args.plan_review, repo_root / "report" / args.date / "plan-review.json")
    data_quality = artifact_json(repo_root, None, repo_root / "report" / args.date / "data-quality.json")
    run_manifest = artifact_json(
        repo_root,
        getattr(args, "run_manifest", None),
        default_run_manifest(repo_root, args.date, args.session),
    )
    if run_manifest:
        run_manifest.setdefault("repo_root", str(repo_root))
    lessons = lessons_for_date(repo_root, args.date, args.learning_dir)
    focus_payload = focus_selection(repo_root, args.date)
    digest_symbols = []
    seen_digest_symbols = set()
    for signal in signals:
        symbol = signal_symbol(signal)
        if symbol and symbol not in seen_digest_symbols:
            seen_digest_symbols.add(symbol)
            digest_symbols.append(symbol)
    selected_focus = focus_payload.get("selected") if isinstance(focus_payload.get("selected"), list) else []
    for row in selected_focus:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if symbol and symbol not in seen_digest_symbols:
            seen_digest_symbols.add(symbol)
            digest_symbols.append(symbol)
    analysis_digest = (
        analysis_report_digest(repo_root, args.date, args.session, digest_symbols)
        if args.session in {"pre-market", "post-market"}
        else {"available": False}
    )
    market_snapshot = market_snapshot_digest(repo_root, args.date) if args.session == "post-market" else {}
    market_context = (
        longbridge_market_context(repo_root, args.date, getattr(args, "market_context", None))
        if args.session == "post-market"
        else {}
    )
    intraday_payload = intraday_review(repo_root, args.date) if args.session == "post-market" else {}
    workflow_payload = (
        workflow_review_artifact(repo_root, args.date, getattr(args, "workflow_review", None))
        if args.session == "post-market"
        else {}
    )
    output = output_path(repo_root, args.date, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_markdown(
            date=args.date,
            session=args.session,
            signals=signals,
            position_review=position_review,
            plan_review=plan_review,
            data_quality=data_quality,
            run_manifest=run_manifest,
            repo_root=repo_root,
            lessons=lessons,
            signal_source_summary=signal_source_summary,
            focus_selection_payload=focus_payload,
            intraday_payload=intraday_payload,
            workflow_review_payload=workflow_payload,
            analysis_digest_payload=analysis_digest,
            market_snapshot_payload=market_snapshot,
            market_context_payload=market_context,
        ),
        encoding="utf-8",
    )
    executable, watch, no_trade = split_signals(signals)
    fallback_report = default_analysis_report(repo_root, args.date, args.session)
    source_report = analysis_digest.get("path") if analysis_digest.get("available") else (
        str(fallback_report) if fallback_report else None
    )
    workflow_summary = workflow_payload.get("summary") if isinstance(workflow_payload.get("summary"), dict) else {}
    missed_summary = (
        workflow_summary.get("missed_or_misjudged")
        if isinstance(workflow_summary.get("missed_or_misjudged"), dict)
        else {}
    )
    return {
        "status": "success",
        "date": args.date,
        "session": args.session,
        "output": str(output),
        "source_signals": str(signals_file),
        "source_report": source_report,
        "summary": {
            "conditional_executable": len(executable),
            "watch_only": len(watch),
            "no_trade": len(no_trade),
            "lessons": len(lessons),
            "data_quality_status": data_quality.get("quality_status") or data_quality.get("status"),
            "focused_fallback_symbols": len(data_quality.get("focused_fallback_symbols", []))
            if isinstance(data_quality.get("focused_fallback_symbols"), list)
            else 0,
            "candidate": signal_source_summary.get("candidate"),
            "blocked": signal_source_summary.get("blocked"),
            "skipped": signal_source_summary.get("skipped"),
            "focus_selected": len(focus_payload.get("selected", []))
            if isinstance(focus_payload.get("selected"), list)
            else 0,
            "intraday_available": bool(intraday_payload.get("available")),
            "intraday_events": intraday_payload.get("event_count", 0),
            "intraday_notify_events": intraday_payload.get("notify_event_count", 0),
            "workflow_review_available": bool(workflow_payload),
            "workflow_possible_missed_candidates": missed_summary.get("possible_missed_candidates", 0),
            "workflow_touch_fade_or_invalidated": missed_summary.get("touch_fade_or_invalidated", 0),
            "workflow_intraday_failures": workflow_summary.get("intraday_failures", 0),
            "market_snapshot_available": bool(market_snapshot.get("available")),
            "market_breadth_count": market_snapshot.get("breadth_count", 0),
            "market_sector_count": market_snapshot.get("sector_count", 0),
            "longbridge_market_context_available": bool(market_context),
            "longbridge_market_context_status": market_context.get("status"),
            "longbridge_market_context_errors": len(market_context.get("errors", []))
            if isinstance(market_context.get("errors"), list)
            else 0,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a concise Feishu-ready analysis summary")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "post-market", "monitor"], required=True)
    parser.add_argument("--signals")
    parser.add_argument("--position-review")
    parser.add_argument("--plan-review")
    parser.add_argument("--run-manifest")
    parser.add_argument("--workflow-review")
    parser.add_argument("--market-context")
    parser.add_argument("--output")
    parser.add_argument("--learning-dir", default="runtime/learning")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        payload = run(args)
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
