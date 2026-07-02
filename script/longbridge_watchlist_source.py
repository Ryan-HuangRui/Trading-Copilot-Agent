from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable

from sync_longbridge_watchlist import (
    group_symbols,
    iter_group_payloads,
    longbridge_cli_path,
    ordered_unique,
    run_longbridge_cli,
    sdk_watchlist_snapshots,
    watchlist_snapshots,
)

US_MARKET_SUFFIX = ".US"
MARKET_SUFFIX_RE = re.compile(r"\.[A-Z]{2,4}$")


DEFAULT_SOURCE_GROUPS = [
    "持仓",
    "ibkr持仓",
    "老朋友",
    "AI先进封装HBM",
    "AI Top 10 Research",
]


def config_symbol(symbol: object) -> str:
    value = str(symbol or "").strip().upper().strip("`，,。.;；:：()（）[]【】")
    if value.endswith(US_MARKET_SUFFIX):
        return value.rsplit(".", 1)[0]
    if MARKET_SUFFIX_RE.search(value):
        return ""
    return value


def read_manual_watchlist(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    symbols = payload.get("symbols", []) if isinstance(payload, dict) else []
    return ordered_unique(config_symbol(symbol) for symbol in symbols)


def write_manual_watchlist(path: Path, symbols: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"symbols": ordered_unique(config_symbol(symbol) for symbol in symbols)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_env(repo_root: Path) -> None:
    env_file = repo_root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        import os

        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def fetch_cli_snapshots(cli: str) -> list[dict[str, object]]:
    if not Path(cli).exists() and shutil.which(cli) is None:
        raise RuntimeError(f"Longbridge CLI not found: {cli}")
    payload = run_longbridge_cli(cli, ["watchlist", "--format", "json"])
    return watchlist_snapshots(iter_group_payloads(payload))


def fetch_sdk_snapshots() -> list[dict[str, object]]:
    try:
        from longbridge.openapi import Config, QuoteContext
    except ImportError as exc:
        raise RuntimeError("Missing Longbridge Python SDK.") from exc

    config_factory = getattr(Config, "from_apikey_env", None) or getattr(Config, "from_env", None)
    if config_factory is None:
        raise RuntimeError("Longbridge SDK Config has no from_env/from_apikey_env initializer.")

    ctx = QuoteContext(config_factory())
    groups = list(ctx.watchlist())
    # Keep this call near the SDK path so tests can exercise the same group object contract.
    for group in groups:
        group_symbols(group)
    return sdk_watchlist_snapshots(groups)


def fetch_longbridge_snapshots(
    *,
    repo_root: Path,
    method: str = "auto",
    longbridge_cli: str | None = None,
) -> tuple[list[dict[str, object]], str, str | None]:
    load_env(repo_root)
    cli = longbridge_cli_path(longbridge_cli)
    if method in ("auto", "cli") and cli:
        try:
            return fetch_cli_snapshots(cli), "cli", None
        except Exception as cli_exc:
            if method == "cli":
                raise
            try:
                snapshots = fetch_sdk_snapshots()
                return snapshots, "sdk", str(cli_exc)
            except Exception as sdk_exc:
                raise RuntimeError(f"Longbridge CLI failed: {cli_exc}; SDK fallback failed: {sdk_exc}") from sdk_exc
    return fetch_sdk_snapshots(), "sdk", None


def collect_source_symbols(
    snapshots: Iterable[dict[str, object]],
    group_names: Iterable[str],
) -> dict[str, object]:
    by_name = {str(item.get("group_name")): item for item in snapshots if item.get("group_name")}
    found_groups = []
    missing_groups = []
    longbridge_symbols = []

    for group_name in group_names:
        snapshot = by_name.get(group_name)
        if not snapshot:
            missing_groups.append(group_name)
            continue
        symbols = [str(symbol).strip().upper() for symbol in snapshot.get("symbols", []) if str(symbol).strip()]
        found_groups.append(
            {
                "group_name": group_name,
                "group_id": snapshot.get("group_id"),
                "symbols": symbols,
                "count": len(symbols),
            }
        )
        longbridge_symbols.extend(symbols)

    longbridge_symbols = ordered_unique(longbridge_symbols)
    return {
        "found_groups": found_groups,
        "missing_groups": missing_groups,
        "longbridge_symbols": longbridge_symbols,
        "symbols": ordered_unique(config_symbol(symbol) for symbol in longbridge_symbols),
    }


def refresh_watchlist(
    *,
    repo_root: Path,
    watchlist_path: str = "config/watchlist.json",
    group_names: Iterable[str] | None = None,
    method: str = "auto",
    longbridge_cli: str | None = None,
) -> dict[str, object]:
    source_groups = list(group_names or DEFAULT_SOURCE_GROUPS)
    path = Path(watchlist_path)
    if not path.is_absolute():
        path = repo_root / path

    try:
        snapshots, backend, cli_fallback_reason = fetch_longbridge_snapshots(
            repo_root=repo_root,
            method=method,
            longbridge_cli=longbridge_cli,
        )
        collected = collect_source_symbols(snapshots, source_groups)
        symbols = collected["symbols"]
        if not symbols:
            raise RuntimeError(f"No symbols found in Longbridge source groups: {', '.join(source_groups)}")
        write_manual_watchlist(path, symbols)
        return {
            "status": "success",
            "source": "longbridge",
            "backend": backend,
            "watchlist_path": str(path),
            "source_groups": source_groups,
            "found_groups": collected["found_groups"],
            "missing_groups": collected["missing_groups"],
            "longbridge_symbols": collected["longbridge_symbols"],
            "symbols": symbols,
            "updated_watchlist": True,
            "fallback_reason": None,
            "cli_fallback_reason": cli_fallback_reason,
        }
    except Exception as exc:
        manual_symbols = read_manual_watchlist(path)
        return {
            "status": "fallback",
            "source": "manual",
            "backend": None,
            "watchlist_path": str(path),
            "source_groups": source_groups,
            "found_groups": [],
            "missing_groups": source_groups,
            "longbridge_symbols": [],
            "symbols": manual_symbols,
            "updated_watchlist": False,
            "fallback_reason": str(exc),
            "cli_fallback_reason": None,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh local config/watchlist.json from selected read-only Longbridge watchlist groups."
    )
    parser.add_argument("--watchlist", default="config/watchlist.json")
    parser.add_argument("--group-name", action="append", default=[])
    parser.add_argument("--method", choices=["auto", "cli", "sdk"], default="auto")
    parser.add_argument("--longbridge-cli")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    payload = refresh_watchlist(
        repo_root=repo_root,
        watchlist_path=args.watchlist,
        group_names=args.group_name or DEFAULT_SOURCE_GROUPS,
        method=args.method,
        longbridge_cli=args.longbridge_cli,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
