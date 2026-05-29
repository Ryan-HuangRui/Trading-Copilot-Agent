#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signal_artifacts import read_json


def resolve_path(repo_root: Path, explicit_path: str | None, default_path: Path) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path
    return default_path


def default_output_path(repo_root: Path, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / "strategy" / "paper-strategy-review.json")


def default_markdown_path(repo_root: Path, explicit_path: str | None) -> Path:
    return resolve_path(repo_root, explicit_path, repo_root / "report" / "strategy" / "paper-strategy-review.md")


def discover_review_paths(repo_root: Path) -> list[Path]:
    report_root = repo_root / "report"
    if not report_root.exists():
        return []
    return sorted(report_root.glob("*/paper-execution-review.json"))


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 4)


def pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 4)


def load_reviews(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        payload = read_json(path)
        date = str(payload.get("date") or path.parent.name)
        reviews = payload.get("reviews")
        if not isinstance(reviews, list):
            continue
        for item in reviews:
            if not isinstance(item, dict):
                continue
            rows.append({**item, "date": date, "source_review": str(path)})
    return rows


def bucket_key(item: dict[str, Any], dimension: str) -> str:
    if dimension == "symbol":
        return str(item.get("symbol") or "unknown")
    return str(item.get("setup") or "unknown")


def aggregate(items: list[dict[str, Any]], *, dimension: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        buckets.setdefault(bucket_key(item, dimension), []).append(item)

    rows = []
    for key, bucket in sorted(buckets.items()):
        result_values = [value for value in (as_float(item.get("result_r")) for item in bucket) if value is not None]
        slippage_values = [value for value in (as_float(item.get("slippage_pct")) for item in bucket) if value is not None]
        submitted_count = len([item for item in bucket if item.get("status")])
        filled_count = len([item for item in bucket if item.get("status") == "filled"])
        closed_count = len(result_values)
        wins = len([value for value in result_values if value > 0])
        cancelled_expired = len([item for item in bucket if item.get("status") in {"cancelled", "expired"}])
        no_fill_then_win = len([item for item in bucket if item.get("plan_adherence") == "not_filled" and item.get("outcome") == "tp1_filled"])
        false_trigger = len([item for item in bucket if item.get("outcome") in {"stop_filled"} and (as_float(item.get("result_r")) or 0) < 0])
        rows.append(
            {
                "dimension": dimension,
                "key": key,
                "planned_count": len(bucket),
                "submitted_count": submitted_count,
                "filled_count": filled_count,
                "cancelled_expired_count": cancelled_expired,
                "closed_count": closed_count,
                "average_r": average(result_values),
                "median_r": median(result_values),
                "win_rate_pct": pct(wins, closed_count),
                "average_slippage_pct": average(slippage_values),
                "false_trigger_rate_pct": pct(false_trigger, closed_count),
                "no_fill_then_win_rate_pct": pct(no_fill_then_win, len(bucket)),
                "candidate_lessons": sum(len(item.get("candidate_lessons") or []) for item in bucket),
            }
        )
    return rows


def render_markdown(summary: dict[str, Any], setup_rows: list[dict[str, Any]], symbol_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Paper Strategy Review",
        "",
        "## Summary",
        f"- Reviews: {summary['reviews']}",
        f"- Setups: {summary['setups']}",
        f"- Symbols: {summary['symbols']}",
        f"- Average R: {summary['average_r'] if summary['average_r'] is not None else 'N/A'}",
        "",
        "## By Setup",
    ]
    for row in setup_rows:
        lines.append(
            f"- {row['key']}: planned={row['planned_count']}, filled={row['filled_count']}, avg_r={row['average_r']}, win_rate={row['win_rate_pct']}"
        )
    lines.extend(["", "## By Symbol"])
    for row in symbol_rows:
        lines.append(
            f"- {row['key']}: planned={row['planned_count']}, filled={row['filled_count']}, avg_r={row['average_r']}, slippage={row['average_slippage_pct']}"
        )
    lines.extend(["", "Safety note: aggregate paper execution evidence only; no refined trading rule is modified.", ""])
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    paths = [Path(path) for path in args.review] if args.review else discover_review_paths(repo_root)
    paths = [path if path.is_absolute() else repo_root / path for path in paths]
    reviews = load_reviews(paths)
    setup_rows = aggregate(reviews, dimension="setup")
    symbol_rows = aggregate(reviews, dimension="symbol")
    result_values = [value for value in (as_float(item.get("result_r")) for item in reviews) if value is not None]
    summary = {
        "reviews": len(reviews),
        "review_files": len(paths),
        "setups": len(setup_rows),
        "symbols": len(symbol_rows),
        "average_r": average(result_values),
        "median_r": median(result_values),
    }
    output = default_output_path(repo_root, args.output)
    markdown = default_markdown_path(repo_root, args.markdown_output)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_reviews": [str(path) for path in paths],
        "summary": summary,
        "by_setup": setup_rows,
        "by_symbol": symbol_rows,
        "safety_note": "Strategy-level paper review only. This workflow does not modify refined rules.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(render_markdown(summary, setup_rows, symbol_rows), encoding="utf-8")
    return {"status": "success", "output": str(output), "markdown": str(markdown), "summary": summary}


def build_args(**overrides: Any) -> argparse.Namespace:
    values = {
        "review": [],
        "output": None,
        "markdown_output": None,
        "repo_root": str(Path(__file__).resolve().parents[1]),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate paper execution reviews by setup and symbol")
    parser.add_argument("--review", action="append", default=[])
    parser.add_argument("--output")
    parser.add_argument("--markdown-output")
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
