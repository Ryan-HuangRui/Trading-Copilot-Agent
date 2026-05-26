---
name: trading-copilot
description: Use this repo-local skill for Trading-Copilot-Agent market research workflows, including pre-market planning, post-market review, symbol analysis, monitor brief generation, research notes, and rule validation. The skill prepares or reviews trading research artifacts only; it must not place trades, call broker APIs, or output deterministic buy/sell instructions.
---

# Trading Copilot

## Purpose

Use this skill when the user asks for market preparation, report generation, symbol review, monitoring summaries, research notes, or validation against the repo's trading rules.

This repository is a trading research assistant for Codex/Claude/OpenClaw-style agents. It is not an independent trading product and not an execution system.

## Safety Rules

- Never place real trades, call broker APIs, or imply order execution.
- Do not output deterministic buy/sell instructions. Use scenarios, triggers, invalidation, risk, and `NO TRADE`.
- For current or recent symbol analysis, fetch real market data first through the repository scripts or state that no concrete price conclusion can be made.
- Use `knowledge/refined/` as the rule source for trading conclusions.
- Treat `knowledge/source/` as research input only.
- Preserve simplified Chinese for user-facing reports unless the user asks otherwise.

## Repository Map

- `script/`: deterministic data and context tools.
- `agent/`: Codex App automation prompts for scheduled pre-market and post-market reports.
- `knowledge/refined/`: approved trading rules.
- `docs/`: runbooks for automation and human operation.
- `config/`: watchlists and local runtime state paths.
- `raw_data/`, `report/`, and `config/rate_limit_state.json`: generated or local runtime data, ignored by git.

## Core Workflows

### Pre-Market Plan

1. Run `python3 script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day`.
2. Read `agent/daily_analysis_prompt.md`, `knowledge/refined/`, and `report/<DATE>/pre-market-context.json`.
3. Write `report/<DATE>/exec-brief.md` and `report/<DATE>/pre-market.md`.

### Post-Market Review

1. Run `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day`.
2. Read `agent/post_market_analysis_prompt.md`, `knowledge/refined/`, and `report/<DATE>/daily-snapshot.json`.
3. Write `report/<DATE>/post-market.md`.

### Monitor Brief

1. Run `python3 script/monitor_scan.py --state config/monitor_state.json --interval 5min`.
2. Read `report/latest-monitor.json`.
3. Summarize actionable observations as scenarios with invalidation and risk. Use `NO TRADE` when data or setup quality is insufficient.

### Rule Validation

1. Read the relevant artifact or user-supplied thesis.
2. Check it against `knowledge/refined/global/` first, then the relevant setup files under `knowledge/refined/setups/`.
3. Report pass/fail/unclear by rule area. Do not invent missing setup rules.

## Verification

- Syntax check after script changes: `python3 -m py_compile script/*.py`.
- Trading-day guard smoke test: `python3 script/trading_day_guard.py --date 2026-05-06 --format text`.
- Data-fetch smoke tests require `.env` with `TWELVE_DATA_API_KEY`.

## Output Contract

When running a workflow, clearly report:

- workflow name
- market/report date
- input files read
- output artifacts written
- skipped state and reason, if applicable
- data limitations and rule limitations
