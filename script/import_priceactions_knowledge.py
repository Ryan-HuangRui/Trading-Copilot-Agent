#!/usr/bin/env python3
"""
Import + normalize PriceActions docs into knowledge/source/priceactions.

Usage:
  python3 script/import_priceactions_knowledge.py
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "knowledge" / "source" / "priceactions" / "docs"
OUT = ROOT / "knowledge" / "source" / "priceactions" / "meta"

NOISE_PATTERNS = [
    r"^\s*\[?\s*←?\s*Back\s*\]?\s*$",
    r"^\s*Edit this page\s*$",
    r"^\s*Last updated.*$",
]

TERM_MAP = {
    "liquidity grab": "stop run",
    "liquidity sweep": "stop run",
    "market structure shift": "MSS",
    "break of structure": "BOS",
    "trading range": "TR",
    "trend bar": "Trend Bar",
    "trading range bar": "TR Bar",
}


def normalize_text(text: str) -> str:
    lines = text.splitlines()
    out = []
    for line in lines:
        if any(re.match(p, line, re.I) for p in NOISE_PATTERNS):
            continue
        out.append(line.rstrip())

    cleaned = "\n".join(out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    for k, v in TERM_MAP.items():
        cleaned = re.sub(rf"\b{re.escape(k)}\b", v, cleaned, flags=re.I)

    return cleaned.strip() + "\n"


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    files = sorted(SRC.rglob("*.md"))
    records = []
    seen_hash = {}

    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        raw = p.read_text(encoding="utf-8", errors="ignore")
        cleaned = normalize_text(raw)
        h = sha1(cleaned)

        title = ""
        for ln in cleaned.splitlines():
            if ln.startswith("# "):
                title = ln[2:].strip()
                break

        record = {
            "path": rel,
            "title": title,
            "chars_raw": len(raw),
            "chars_cleaned": len(cleaned),
            "requires_chart": bool(re.search(r"!\[|<img", raw)),
            "sha1_cleaned": h,
            "duplicate_of": seen_hash.get(h),
        }
        if h not in seen_hash:
            seen_hash[h] = rel
        records.append(record)

    (OUT / "index.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "total_docs": len(records),
        "unique_docs": len({r['sha1_cleaned'] for r in records}),
        "duplicates": sum(1 for r in records if r["duplicate_of"]),
        "with_charts": sum(1 for r in records if r["requires_chart"]),
        "term_map": TERM_MAP,
    }
    (OUT / "clean_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    glossary_lines = ["# PriceActions 术语映射（清洗标准）", ""]
    for k, v in TERM_MAP.items():
        glossary_lines.append(f"- `{k}` -> `{v}`")
    glossary_lines.append("")
    (OUT / "term_mapping.md").write_text("\n".join(glossary_lines), encoding="utf-8")

    print(f"Indexed {len(records)} docs")
    print(f"Summary: {summary}")


if __name__ == "__main__":
    main()
