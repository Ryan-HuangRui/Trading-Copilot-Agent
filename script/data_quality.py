#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signal_artifacts import SESSION_SIGNAL_FILENAMES


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def resolve_path(repo_root: Path, value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def normalized_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".US") and text.count(".") >= 1:
        return text.rsplit(".", 1)[0]
    return text


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def session_signal_paths(repo_root: Path, date: str, session: str) -> list[Path]:
    report_dir = repo_root / "report" / date
    if session == "all":
        return [
            report_dir / SESSION_SIGNAL_FILENAMES["pre-market"],
            report_dir / SESSION_SIGNAL_FILENAMES["post-market"],
            report_dir / "signals.json",
        ]
    if session == "intraday":
        return [report_dir / "monitor-signals.json"]
    return [report_dir / SESSION_SIGNAL_FILENAMES[session]]


def focused_symbols(repo_root: Path, date: str, session: str) -> tuple[list[str], list[str]]:
    symbols = []
    sources = []
    for path in session_signal_paths(repo_root, date, session):
        payload = read_json(path)
        if not payload or not isinstance(payload.get("signals"), list):
            continue
        sources.append(str(path))
        for signal in payload["signals"]:
            if isinstance(signal, dict):
                symbol = normalized_symbol(signal.get("symbol"))
                if symbol:
                    symbols.append(symbol)
    seen = set()
    unique = []
    for symbol in symbols:
        if symbol not in seen:
            seen.add(symbol)
            unique.append(symbol)
    return unique, sources


def snapshot_symbols(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in snapshot.get("symbols", []):
        if not isinstance(item, dict):
            continue
        symbol = normalized_symbol(item.get("symbol"))
        if symbol:
            result[symbol] = item
    return result


def provider_summary(symbols: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in symbols.values():
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        provider = str(meta.get("provider") or "unknown")
        counts[provider] = counts.get(provider, 0) + 1
    return dict(sorted(counts.items()))


def fallback_rows(symbols: dict[str, dict[str, Any]], focus: set[str] | None = None) -> list[dict[str, Any]]:
    rows = []
    for symbol, item in symbols.items():
        if focus is not None and symbol not in focus:
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        if not meta.get("fallback_from"):
            continue
        rows.append(
            {
                "symbol": symbol,
                "provider": meta.get("provider"),
                "fallback_from": meta.get("fallback_from"),
                "primary_error": meta.get("primary_error"),
                "fallback_reason": normalize_fallback_reason(meta.get("primary_error")),
            }
        )
    return rows


def normalize_fallback_reason(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if "connection reset" in text or "reset by peer" in text:
        return "connection_reset"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "permission" in text or "unauthorized" in text or "forbidden" in text:
        return "permission_or_auth"
    return text.replace(" ", "_")[:80]


def account_price_deltas(
    repo_root: Path,
    date: str,
    symbols: dict[str, dict[str, Any]],
    threshold_pct: float,
    explicit_account_snapshot: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    path = resolve_path(
        repo_root,
        explicit_account_snapshot,
        repo_root / "runtime" / "account" / date / "account-snapshot.json",
    )
    payload = read_json(path)
    if not payload:
        return [], None
    rows = []
    for position in payload.get("positions", []):
        if not isinstance(position, dict):
            continue
        symbol = normalized_symbol(position.get("symbol"))
        snapshot_row = symbols.get(symbol)
        latest = snapshot_row.get("latest") if isinstance(snapshot_row, dict) else None
        snapshot_close = to_float((latest or {}).get("close") if isinstance(latest, dict) else None)
        account_price = to_float(position.get("last_price"))
        if symbol and snapshot_close and account_price:
            delta_pct = round((account_price / snapshot_close - 1) * 100, 3)
            if abs(delta_pct) >= threshold_pct:
                rows.append(
                    {
                        "symbol": symbol,
                        "account_last_price": account_price,
                        "snapshot_close": snapshot_close,
                        "delta_pct": delta_pct,
                    }
                )
    return rows, str(path)


def abnormal_moves(symbols: dict[str, dict[str, Any]], threshold_pct: float) -> list[dict[str, Any]]:
    rows = []
    for symbol, item in symbols.items():
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        close_delta_pct = to_float(metrics.get("close_delta_pct"))
        if close_delta_pct is not None and abs(close_delta_pct) >= threshold_pct:
            rows.append({"symbol": symbol, "close_delta_pct": close_delta_pct})
    return rows


def load_snapshot(repo_root: Path, date: str, explicit_snapshot: str | None) -> tuple[dict[str, Any], str]:
    if explicit_snapshot:
        path = resolve_path(repo_root, explicit_snapshot, repo_root / explicit_snapshot)
        payload = read_json(path)
        if not payload:
            raise FileNotFoundError(f"missing or invalid snapshot: {path}")
        return payload, str(path)

    report_dir = repo_root / "report" / date
    daily_path = report_dir / "daily-snapshot.json"
    payload = read_json(daily_path)
    if payload:
        return payload, str(daily_path)

    context_path = report_dir / "pre-market-context.json"
    context = read_json(context_path)
    if context and isinstance(context.get("snapshot"), dict):
        return context["snapshot"], str(context.get("source_snapshot_path") or context_path)

    raise FileNotFoundError(f"missing or invalid snapshot: {daily_path}")


def latest_bar_date_from_symbols(symbols: dict[str, dict[str, Any]]) -> str | None:
    dates: list[str] = []
    for item in symbols.values():
        latest = item.get("latest") if isinstance(item.get("latest"), dict) else {}
        for key in ("datetime", "date", "dt"):
            value = latest.get(key)
            if value:
                text = str(value)
                if len(text) >= 10:
                    dates.append(text[:10])
                break
    return max(dates) if dates else None


def provider_phase_summary(symbols: dict[str, dict[str, Any]], focused_fallback: list[dict[str, Any]]) -> dict[str, Any]:
    providers = provider_summary(symbols)
    provider_source = None
    if len(providers) == 1:
        provider_source = next(iter(providers))
    elif providers:
        provider_source = "mixed"
    fallback_from = None
    fallback_reason = None
    if focused_fallback:
        from_values = sorted({str(row.get("fallback_from")) for row in focused_fallback if row.get("fallback_from")})
        reason_values = sorted({str(row.get("fallback_reason")) for row in focused_fallback if row.get("fallback_reason")})
        fallback_from = from_values[0] if len(from_values) == 1 else ("mixed" if from_values else None)
        fallback_reason = reason_values[0] if len(reason_values) == 1 else ("mixed" if reason_values else None)
    return {"provider_source": provider_source, "fallback_from": fallback_from, "fallback_reason": fallback_reason}


def phase_name(session: str) -> str:
    return {"pre-market": "pre_market", "post-market": "post_market"}.get(session, session)


def phase_freshness(
    *,
    session: str,
    date: str,
    snapshot: dict[str, Any],
    symbols: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    phase = phase_name(session)
    latest_dates = snapshot.get("latest_bar_dates")
    latest_date = None
    if isinstance(latest_dates, list) and latest_dates:
        latest_date = str(max(latest_dates))
    latest_date = latest_date or latest_bar_date_from_symbols(symbols)
    expected = str(snapshot.get("snapshot_date") or latest_date or date) if phase == "pre_market" else date
    stale = bool(snapshot.get("stale_data"))
    reason = snapshot.get("stale_reason")
    if phase in {"intraday", "post_market"} and latest_date and latest_date != expected:
        stale = True
        label = "intraday latest bar date" if phase == "intraday" else "post-market latest bar date"
        reason = f"{label} {latest_date} != expected {expected}"
    return {
        "session_phase": phase,
        "expected_bar_date": expected,
        "actual_latest_bar_date": latest_date,
        "stale_data": stale,
        "stale_reason": reason,
    }


def status_for(payload: dict[str, Any]) -> str:
    if payload["missing_focused_symbols"]:
        return "fail"
    if payload["stale_data"] or payload["focused_fallback_symbols"] or payload["account_price_deltas"] or payload["abnormal_moves"]:
        return "warn"
    return "pass"


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Data Quality ({payload['date']})",
        "",
        f"- status: {payload['status']}",
        f"- session_phase: {payload['session_phase']}",
        f"- snapshot: {payload['snapshot_path']}",
        f"- expected_bar_date: {payload.get('expected_bar_date') or 'unknown'}",
        f"- actual_latest_bar_date: {payload.get('actual_latest_bar_date') or 'unknown'}",
        f"- stale_data: {payload['stale_data']}",
        f"- stale_reason: {payload.get('stale_reason') or 'none'}",
        f"- provider_source: {payload.get('provider_source') or 'unknown'}",
        f"- fallback_from: {payload.get('fallback_from') or 'none'}",
        f"- fallback_reason: {payload.get('fallback_reason') or 'none'}",
        f"- latest_bar_dates: {', '.join(payload['latest_bar_dates']) or 'none'}",
        f"- providers: {json.dumps(payload['provider_summary'], ensure_ascii=False, sort_keys=True)}",
        f"- focused_symbols: {', '.join(payload['focused_symbols']) or 'none'}",
        "",
        "## Focused Fallback",
    ]
    if payload["focused_fallback_symbols"]:
        for row in payload["focused_fallback_symbols"]:
            lines.append(f"- {row['symbol']}: {row.get('provider')} fallback_from={row.get('fallback_from')} error={row.get('primary_error')}")
    else:
        lines.append("- none")
    lines.extend(["", "## Missing Focused Symbols"])
    if payload["missing_focused_symbols"]:
        for symbol in payload["missing_focused_symbols"]:
            lines.append(f"- {symbol}")
    else:
        lines.append("- none")
    lines.extend(["", "## Account Price Deltas"])
    if payload["account_price_deltas"]:
        for row in payload["account_price_deltas"]:
            lines.append(f"- {row['symbol']}: account={row['account_last_price']} snapshot={row['snapshot_close']} delta={row['delta_pct']}%")
    else:
        lines.append("- none")
    lines.extend(["", "## Abnormal Moves"])
    if payload["abnormal_moves"]:
        for row in payload["abnormal_moves"]:
            lines.append(f"- {row['symbol']}: close_delta_pct={row['close_delta_pct']}%")
    else:
        lines.append("- none")
    lines.extend(["", "## Snapshot Errors"])
    if payload["snapshot_errors"]:
        for row in payload["snapshot_errors"]:
            lines.append(f"- {json.dumps(row, ensure_ascii=False, sort_keys=True)}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    report_dir = repo_root / "report" / args.date
    snapshot, snapshot_path = load_snapshot(repo_root, args.date, args.snapshot)
    symbols = snapshot_symbols(snapshot)
    focus, signal_sources = focused_symbols(repo_root, args.date, args.session)
    focus_set = set(focus)
    missing_focus = sorted(symbol for symbol in focus if symbol not in symbols)
    focused_fallback = fallback_rows(symbols, focus_set)
    freshness = phase_freshness(session=args.session, date=args.date, snapshot=snapshot, symbols=symbols)
    provider_fields = provider_phase_summary(symbols, focused_fallback)
    account_deltas, account_path = account_price_deltas(
        repo_root=repo_root,
        date=args.date,
        symbols=symbols,
        threshold_pct=args.account_delta_threshold_pct,
        explicit_account_snapshot=args.account_snapshot,
    )
    payload = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **freshness,
        "snapshot_path": snapshot_path,
        "signal_sources": signal_sources,
        "account_snapshot_path": account_path,
        "market_data_source": snapshot.get("market_data_source"),
        "primary_market_data_source": snapshot.get("primary_market_data_source"),
        "fallback_market_data_source": snapshot.get("fallback_market_data_source"),
        **provider_fields,
        "latest_bar_dates": snapshot.get("latest_bar_dates", []),
        "provider_summary": provider_summary(symbols),
        "focused_symbols": focus,
        "missing_focused_symbols": missing_focus,
        "fallback_symbols": fallback_rows(symbols),
        "focused_fallback_symbols": focused_fallback,
        "account_price_deltas": account_deltas,
        "abnormal_moves": abnormal_moves(symbols, args.abnormal_move_threshold_pct),
        "snapshot_errors": snapshot.get("errors", []),
    }
    payload["status"] = status_for(payload)
    payload["quality_status"] = payload["status"]

    json_path = resolve_path(repo_root, args.output_json, report_dir / "data-quality.json")
    md_path = resolve_path(repo_root, args.output_md, report_dir / "data-quality.md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown(payload), encoding="utf-8")
    return {
        "status": "success",
        "date": args.date,
        "quality_status": payload["status"],
        "artifacts": [str(json_path), str(md_path)],
        "focused_fallback_symbols": payload["focused_fallback_symbols"],
        "missing_focused_symbols": payload["missing_focused_symbols"],
        "account_price_deltas": payload["account_price_deltas"],
        "abnormal_moves": payload["abnormal_moves"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate daily market-data quality artifacts")
    parser.add_argument("--date", required=True)
    parser.add_argument("--session", choices=["pre-market", "intraday", "post-market", "all"], default="all")
    parser.add_argument("--snapshot")
    parser.add_argument("--account-snapshot")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--account-delta-threshold-pct", type=float, default=5.0)
    parser.add_argument("--abnormal-move-threshold-pct", type=float, default=20.0)
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
