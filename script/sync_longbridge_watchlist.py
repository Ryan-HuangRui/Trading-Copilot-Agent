#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from signal_artifacts import read_json, resolve_signals_path


DEFAULT_GROUPS = {
    "pre-market": "今日关注",
    "post-market": "今日关注",
}

DEFAULT_SYNC_MODES = {
    "pre-market": "add",
    "post-market": "replace",
}

REPORT_FILES = {
    "pre-market": "exec-brief.md",
    "post-market": "post-market.md",
}

SYMBOL_RE = re.compile(r"\b[A-Z][A-Z0-9.-]{0,9}\b")
SKIP_TOKENS = {
    "AI",
    "API",
    "BOS",
    "CLI",
    "ETF",
    "LV1",
    "MA20",
    "MA50",
    "MCP",
    "NO",
    "OCO",
    "OPENAPI",
    "R",
    "SDK",
    "TP1",
    "TP2",
    "US",
}


def load_env(repo_root: Path) -> None:
    env_file = repo_root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def report_path(repo_root: Path, report_date: str, session: str) -> Path:
    return repo_root / "report" / report_date / REPORT_FILES[session]


def signals_path(repo_root: Path, report_date: str, session: str, explicit_signals: str | None) -> Path:
    return resolve_signals_path(repo_root, report_date, explicit_signals, session)


def normalize_symbol(symbol: str, default_market: str) -> str:
    value = symbol.strip().upper().strip("`，,。.;；:：()（）[]【】")
    if not value:
        return ""
    if "." in value:
        return value
    return f"{value}.{default_market}"


def ordered_unique(items: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def symbols_from_inline_list(line: str) -> list[str]:
    text = line.split("：", 1)[-1].split(":", 1)[-1]
    return [
        token
        for token in SYMBOL_RE.findall(text.upper())
        if token not in SKIP_TOKENS and not token.startswith("MA")
    ]


def symbols_from_section(markdown: str, heading: str) -> list[str]:
    lines = markdown.splitlines()
    in_section = False
    symbols = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = heading in stripped
            continue
        if not in_section:
            continue
        match = re.match(r"^-\s+`?([A-Z][A-Z0-9.-]{0,9})`?\s*[：:]", stripped)
        if match:
            token = match.group(1).upper()
            if token not in SKIP_TOKENS:
                symbols.append(token)
    return symbols


def symbols_from_candidate_headings(markdown: str) -> list[str]:
    symbols = []
    for line in markdown.splitlines():
        match = re.match(r"^###\s+`?([A-Z][A-Z0-9.-]{0,9})`?\s*$", line.strip())
        if match:
            token = match.group(1).upper()
            if token not in SKIP_TOKENS:
                symbols.append(token)
    return symbols


def extract_focus_symbols(markdown: str, session: str) -> list[str]:
    inline_labels = (
        ["今日最多3个重点标的"]
        if session == "pre-market"
        else ["明日最多3个重点观察标的"]
    )
    for line in markdown.splitlines():
        if any(label in line for label in inline_labels):
            symbols = symbols_from_inline_list(line)
            if symbols:
                return ordered_unique(symbols)

    if session == "post-market":
        symbols = symbols_from_section(markdown, "明日观察清单")
        if symbols:
            return ordered_unique(symbols)

    return ordered_unique(symbols_from_candidate_headings(markdown))


def extract_symbols_from_sidecar(path: Path, session: str) -> list[str]:
    payload = read_json(path)
    if payload.get("session") != session:
        raise ValueError(f"{path}: session must be {session}")
    signals = payload.get("signals")
    if not isinstance(signals, list):
        raise ValueError(f"{path}: signals must be an array")
    symbols = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        if signal.get("status") == "no_trade":
            continue
        symbol = signal.get("symbol")
        if isinstance(symbol, str):
            symbols.append(symbol.upper())
    return ordered_unique(symbols)


def load_symbols(args: argparse.Namespace, repo_root: Path) -> list[str]:
    if args.symbol:
        raw_symbols = args.symbol
    else:
        sidecar = signals_path(repo_root, args.date, args.session, args.signals) if args.date or args.signals else None
        if sidecar and sidecar.exists() and not args.report:
            raw_symbols = extract_symbols_from_sidecar(sidecar, args.session)
        else:
            path = Path(args.report) if args.report else report_path(repo_root, args.date, args.session)
            if not path.exists():
                raise FileNotFoundError(f"Missing report file: {path}")
            raw_symbols = extract_focus_symbols(path.read_text(encoding="utf-8"), args.session)

    symbols = [normalize_symbol(symbol, args.default_market) for symbol in raw_symbols]
    symbols = ordered_unique(symbols)
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]
    return symbols


def group_name_from_args(args: argparse.Namespace) -> str:
    env_key = (
        "LONGBRIDGE_PRE_MARKET_WATCHLIST_GROUP"
        if args.session == "pre-market"
        else "LONGBRIDGE_POST_MARKET_WATCHLIST_GROUP"
    )
    return args.group_name or os.environ.get(env_key) or DEFAULT_GROUPS[args.session]


def sync_mode_from_args(args: argparse.Namespace) -> str:
    if args.sync_mode != "auto":
        return args.sync_mode
    return DEFAULT_SYNC_MODES[args.session]


def group_symbols(group: object) -> list[str]:
    result = []
    for security in getattr(group, "securities", []) or []:
        symbol = getattr(security, "symbol", None)
        if symbol:
            result.append(str(symbol))
    return result


def longbridge_cli_path(explicit_path: str | None) -> str | None:
    if explicit_path:
        return explicit_path
    return shutil.which("longbridge") or str(Path.home() / ".local" / "bin" / "longbridge")


def run_longbridge_cli(cli: str, args: list[str]) -> object:
    proc = subprocess.run(
        [cli, *args],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"Longbridge CLI failed: {detail}")
    output = proc.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def unwrap_payload(payload: object) -> object:
    current = payload
    for key in ("data", "groups", "items", "result"):
        if isinstance(current, dict) and key in current:
            current = current[key]
    return current


def iter_group_payloads(payload: object) -> list[dict[str, object]]:
    current = unwrap_payload(payload)
    if isinstance(current, list):
        return [item for item in current if isinstance(item, dict)]
    if isinstance(current, dict):
        for key in ("watchlists", "watchlist_groups", "groups", "items"):
            value = current.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [current]
    return []


def payload_value(payload: dict[str, object], keys: Iterable[str]) -> object | None:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def group_symbol_payloads(payload: dict[str, object]) -> list[str]:
    securities = payload_value(payload, ("securities", "security_list", "symbols", "stocks")) or []
    result = []
    if isinstance(securities, list):
        for item in securities:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                symbol = payload_value(item, ("symbol", "security", "code"))
                if symbol:
                    result.append(str(symbol))
    return result


def extract_created_group_id(payload: object) -> str:
    current = unwrap_payload(payload)
    if isinstance(current, (str, int)):
        return str(current)
    if isinstance(current, dict):
        group_id = payload_value(current, ("id", "group_id", "watchlist_id"))
        if group_id:
            return str(group_id)
    raise RuntimeError(f"Could not determine created Longbridge watchlist group id from: {payload!r}")


def sync_with_longbridge_cli(
    cli: str,
    group_name: str,
    symbols: list[str],
    create: bool,
    sync_mode: str,
) -> dict:
    if not Path(cli).exists() and shutil.which(cli) is None:
        raise RuntimeError(f"Longbridge CLI not found: {cli}")

    groups_payload = run_longbridge_cli(cli, ["watchlist", "--format", "json"])
    existing = None
    for group in iter_group_payloads(groups_payload):
        name = payload_value(group, ("name", "group_name"))
        if name == group_name:
            existing = group
            break

    if existing is None:
        if not create:
            raise RuntimeError(f"Longbridge watchlist group not found: {group_name}")
        created = run_longbridge_cli(cli, ["watchlist", "create", group_name, "--format", "json"])
        group_id = extract_created_group_id(created)
        run_longbridge_cli(cli, watchlist_update_args(group_id, symbols, "replace"))
        return {
            "tool": "cli",
            "action": "create",
            "group_id": group_id,
            "group_name": group_name,
            "sync_mode": sync_mode,
            "previous_symbols": [],
            "added_symbols": symbols,
            "removed_from_group": [],
            "symbols": symbols,
        }

    group_id = payload_value(existing, ("id", "group_id", "watchlist_id"))
    if not group_id:
        raise RuntimeError(f"Longbridge watchlist group has no id: {existing!r}")
    previous = group_symbol_payloads(existing)
    if sync_mode == "add":
        desired = ordered_unique([*previous, *symbols])
        added = [symbol for symbol in symbols if symbol not in previous]
        removed = []
    elif sync_mode == "replace":
        desired = symbols
        added = [symbol for symbol in symbols if symbol not in previous]
        removed = [symbol for symbol in previous if symbol not in symbols]
    else:
        raise ValueError(f"Unsupported sync mode: {sync_mode}")

    run_longbridge_cli(cli, watchlist_update_args(str(group_id), symbols, sync_mode))
    return {
        "tool": "cli",
        "action": sync_mode,
        "group_id": str(group_id),
        "group_name": group_name,
        "sync_mode": sync_mode,
        "previous_symbols": previous,
        "added_symbols": added,
        "removed_from_group": removed,
        "symbols": desired,
    }


def watchlist_update_args(group_id: str, symbols: list[str], sync_mode: str) -> list[str]:
    args = ["watchlist", "update", group_id, "--mode", sync_mode]
    for symbol in symbols:
        args.extend(["--add", symbol])
    args.extend(["--format", "json"])
    return args


def sync_with_longbridge_sdk(group_name: str, symbols: list[str], create: bool, sync_mode: str) -> dict:
    try:
        from longbridge.openapi import Config, QuoteContext, SecuritiesUpdateMode
    except ImportError as exc:
        raise RuntimeError(
            "Missing Longbridge Python SDK. Install it in the runtime environment "
            "or run without --execute for dry-run."
        ) from exc

    config_factory = getattr(Config, "from_apikey_env", None) or getattr(Config, "from_env", None)
    if config_factory is None:
        raise RuntimeError("Longbridge SDK Config has no from_env/from_apikey_env initializer.")

    ctx = QuoteContext(config_factory())
    groups = ctx.watchlist()
    existing = next((group for group in groups if getattr(group, "name", None) == group_name), None)

    if existing is None:
        if not create:
            raise RuntimeError(f"Longbridge watchlist group not found: {group_name}")
        group_id = ctx.create_watchlist_group(name=group_name, securities=symbols)
        return {
            "tool": "sdk",
            "action": "create",
            "group_id": group_id,
            "group_name": group_name,
            "sync_mode": sync_mode,
            "previous_symbols": [],
            "added_symbols": symbols,
            "removed_from_group": [],
            "symbols": symbols,
        }

    previous = group_symbols(existing)
    if sync_mode == "add":
        update_mode = SecuritiesUpdateMode.Add
        desired = ordered_unique([*previous, *symbols])
        added = [symbol for symbol in symbols if symbol not in previous]
        removed = []
    elif sync_mode == "replace":
        update_mode = SecuritiesUpdateMode.Replace
        desired = symbols
        added = [symbol for symbol in symbols if symbol not in previous]
        removed = [symbol for symbol in previous if symbol not in symbols]
    else:
        raise ValueError(f"Unsupported sync mode: {sync_mode}")

    ctx.update_watchlist_group(
        getattr(existing, "id"),
        securities=symbols,
        mode=update_mode,
    )
    return {
        "tool": "sdk",
        "action": sync_mode,
        "group_id": getattr(existing, "id"),
        "group_name": group_name,
        "sync_mode": sync_mode,
        "previous_symbols": previous,
        "added_symbols": added,
        "removed_from_group": removed,
        "symbols": desired,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract daily focus symbols from report Markdown and sync them to a Longbridge watchlist group."
    )
    parser.add_argument("--session", choices=["pre-market", "post-market"], required=True)
    parser.add_argument("--date", help="Report date in YYYY-MM-DD. Required unless --report or --symbol is used.")
    parser.add_argument("--report", help="Explicit report Markdown path.")
    parser.add_argument("--signals", help="Structured signal sidecar path. Defaults to report/<DATE>/<SESSION>-signals.json.")
    parser.add_argument("--group-name", help="Longbridge watchlist group name. Defaults by session or env var.")
    parser.add_argument("--default-market", default="US", help="Suffix for bare tickers, e.g. AAPL -> AAPL.US.")
    parser.add_argument("--max-symbols", type=int, default=3)
    parser.add_argument("--symbol", action="append", help="Override extracted symbols; can be repeated.")
    parser.add_argument("--execute", action="store_true", help="Actually update Longbridge. Without this, dry-run only.")
    parser.add_argument("--no-create", action="store_true", help="Fail if target group does not exist.")
    parser.add_argument(
        "--sync-mode",
        choices=["auto", "add", "replace"],
        default="auto",
        help="Watchlist update mode. auto uses add for pre-market and replace for post-market.",
    )
    parser.add_argument("--method", choices=["auto", "cli", "sdk"], default="auto", help="Execution backend. auto prefers CLI and falls back to SDK.")
    parser.add_argument("--longbridge-cli", help="Path to longbridge CLI. Defaults to PATH or ~/.local/bin/longbridge.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    load_env(repo_root)

    if not args.symbol and not args.report and not args.date:
        raise SystemExit("--date is required unless --report or --symbol is used")

    group_name = group_name_from_args(args)
    sync_mode = sync_mode_from_args(args)
    symbols = load_symbols(args, repo_root)
    if not symbols:
        raise SystemExit("No focus symbols found; Longbridge watchlist was not updated.")

    payload = {
        "session": args.session,
        "date": args.date,
        "group_name": group_name,
        "sync_mode": sync_mode,
        "symbols": symbols,
        "dry_run": not args.execute,
        "safety_note": "replace/add updates only the target Longbridge watchlist group; it does not delete securities globally.",
    }

    if args.execute:
        cli = longbridge_cli_path(args.longbridge_cli)
        if args.method in ("auto", "cli") and cli:
            try:
                payload["longbridge"] = sync_with_longbridge_cli(
                    cli=cli,
                    group_name=group_name,
                    symbols=symbols,
                    create=not args.no_create,
                    sync_mode=sync_mode,
                )
            except Exception as cli_exc:
                if args.method == "cli":
                    raise
                try:
                    payload["longbridge"] = sync_with_longbridge_sdk(
                        group_name=group_name,
                        symbols=symbols,
                        create=not args.no_create,
                        sync_mode=sync_mode,
                    )
                    payload["longbridge"]["cli_fallback_reason"] = str(cli_exc)
                except Exception as sdk_exc:
                    raise RuntimeError(f"Longbridge CLI failed: {cli_exc}; SDK fallback failed: {sdk_exc}") from sdk_exc
        else:
            payload["longbridge"] = sync_with_longbridge_sdk(
                group_name=group_name,
                symbols=symbols,
                create=not args.no_create,
                sync_mode=sync_mode,
            )

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
