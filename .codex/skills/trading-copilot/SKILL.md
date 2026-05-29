---
name: trading-copilot
description: Use this repo-local skill for Trading-Copilot-Agent market research workflows, including pre-market planning, post-market review, symbol analysis, monitor brief generation, research notes, rule validation, and paper-trading readiness review. The skill prepares or reviews trading research artifacts only; it must not place trades, call broker write APIs, or output deterministic buy/sell instructions.
---

# Trading Copilot

## Purpose

Use this skill when the user asks for market preparation, report generation, symbol review, monitoring summaries, research notes, or validation against the repo's trading rules.

This repository is a trading research assistant for Codex/Claude/OpenClaw-style agents. It is not an independent trading product and not an execution system.

Canonical machine entrypoint:

```bash
python3 script/trading_copilot.py <workflow> [options]
```

Every workflow run should return or report the same status fields:

- `status`: `success`, `skipped`, or `failed`
- `workflow`
- `date`
- `artifacts`
- `skipped`
- `reason`

## Safety Rules

- Never place real trades, call broker write APIs, or imply order execution. Read-only account snapshots are allowed only through the repository's account snapshot workflow. Paper-trading workflows may read paper orders/executions and generate dry-run order previews/submission artifacts only.
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
- `raw_data/`, `report/`, `runtime/`, `config/rate_limit_state.json`, and `config/longbridge_rate_limit_state.json`: generated or local runtime data, ignored by git.

## Core Workflows

### Pre-Market Plan

1. Run `python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day`.
2. Read `agent/daily_analysis_prompt.md`, `knowledge/refined/`, and `report/<DATE>/pre-market-context.json`.
3. Write `report/<DATE>/exec-brief.md`, `report/<DATE>/pre-market.md`, and `report/<DATE>/pre-market-signals.json`.
4. Validate both generated reports:
   `python3 script/trading_copilot.py validate-report --session pre-market --date <DATE>`.
5. Validate the structured Trade Plan Cards:
   `python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <DATE>`.
6. After validation passes, append the focused plan to the local journal:
   `python3 script/trading_copilot.py extract-report-signals --session pre-market --date <DATE> --require-validation --append`.
7. If read-only account context is enabled, run:
   `python3 script/trading_copilot.py account-snapshot --date <DATE>`
   then `python3 script/trading_copilot.py position-review --date <DATE> --append`.
8. Build the Feishu execution summary:
   `python3 script/trading_copilot.py feishu-summary --session pre-market --date <DATE>`.
9. Incrementally add focus symbols to Longbridge `今日关注`:
   `python3 script/trading_copilot.py sync-longbridge-watchlist --session pre-market --date <DATE> --group-name 今日关注 --sync-mode add --require-validation --execute --no-create`.

### Post-Market Review

1. Run `python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols`.
2. Read `agent/post_market_analysis_prompt.md`, `knowledge/refined/`, and `report/<DATE>/daily-snapshot.json`.
3. Write `report/<DATE>/post-market.md` and `report/<DATE>/post-market-signals.json`.
4. Validate the generated report:
   `python3 script/trading_copilot.py validate-report --session post-market --date <DATE>`.
5. Validate the structured Trade Plan Cards:
   `python3 script/trading_copilot.py validate-trade-plan --session post-market --date <DATE>`.
6. After validation passes, backfill outcomes for plans targeting the completed snapshot date:
   `python3 script/trading_copilot.py backfill-signal-outcomes --date <DATE> --append`.
7. Append the focused observation plan to the local journal:
   `python3 script/trading_copilot.py extract-report-signals --session post-market --date <DATE> --require-validation --append`.
8. If read-only account context is enabled, run:
   `python3 script/trading_copilot.py account-snapshot --date <DATE>`
   then `python3 script/trading_copilot.py position-review --date <DATE> --append`.
9. Generate the plan review and candidate learning lessons:
   `python3 script/trading_copilot.py plan-review --date <DATE> --append-lessons`.
10. Generate the daily self-review:
   `python3 script/trading_copilot.py daily-self-review --date <DATE> --append`.
11. Build the Feishu execution summary:
   `python3 script/trading_copilot.py feishu-summary --session post-market --date <DATE>`.
12. Fully replace Longbridge `今日关注` from the post-market focus list:
   `python3 script/trading_copilot.py sync-longbridge-watchlist --session post-market --date <DATE> --group-name 今日关注 --sync-mode replace --require-validation --execute --no-create`.

`--require-validation` runs both `validate-report` and `validate-trade-plan`; if either fails, stop before journal append or Longbridge sync.

Use `python3 script/trading_copilot.py learning-review --lookback-days 20` to aggregate repeated daily lessons into `pattern_candidates.jsonl`. Use `promote-lesson --pattern-id <PATTERN_ID> --dry-run` for review, and only use `--apply` when the user explicitly approves the promotion into `knowledge/evolution/validated_lessons.md`.

### Longbridge Watchlist Sync

- Default target group is `今日关注`.
- Pre-market sync is additive (`add`) so newly selected focus symbols are added without removing existing `今日关注` symbols.
- Post-market sync is full replacement (`replace`) so the next day's focus list is reset from the post-market report.
- Removing a symbol from `今日关注` must only remove it from that group. Do not globally unfollow/delete the security or remove it from other Longbridge watchlist groups.

### Monitor Brief

1. Run `python3 script/trading_copilot.py monitor-brief --state config/monitor_state.json --interval 5min`.
2. Read `report/latest-monitor.json`.
3. Summarize actionable observations as scenarios with invalidation and risk. Use `NO TRADE` when data or setup quality is insufficient.
4. If the user wants monitor observations in the journal, run:
   `python3 script/trading_copilot.py extract-monitor-signals --append`.

### Weekly Review

1. Run `python3 script/trading_copilot.py weekly-review --week <YYYY-Www> --append`.
2. Read `report/weekly/<YYYY-Www>.md`.
3. Present the weekly review as process feedback only. Do not convert signal outcomes into trading win rate unless `trades.jsonl` contains actual execution results.

### Position Review

1. Run `python3 script/trading_copilot.py account-snapshot --date <DATE>` to write a read-only account snapshot.
2. Run `python3 script/trading_copilot.py position-review --date <DATE> --append`.
3. Treat output as human-review prompts only. Do not output automatic buy/sell/adjustment instructions.

### Paper Trading Readiness

1. Run `python3 script/trading_copilot.py paper-account-snapshot --date <DATE>` to write a read-only paper account, order, and execution snapshot.
2. Run `python3 script/trading_copilot.py paper-trade-preview --date <DATE> --session pre-market --require-validation` to convert complete Trade Plan Cards into dry-run order previews.
3. Run `python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation` to prepare a dry-run controlled submission artifact.
4. Only when the user explicitly wants simulated order submission, run `TRADING_COPILOT_PAPER_EXECUTION=enabled python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation --execute`.
5. Run `python3 script/trading_copilot.py paper-order-sync --date <DATE>` after refreshing the paper account snapshot to sync submitted order state.
6. Run `python3 script/trading_copilot.py paper-order-cancel --date <DATE>` to prepare a dry-run cancel plan for expired unfilled entry orders.
7. Only when the user explicitly wants simulated cancellation, run `TRADING_COPILOT_PAPER_EXECUTION=enabled python3 script/trading_copilot.py paper-order-cancel --date <DATE> --execute`.
8. Run `python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE>` to prepare a dry-run protective stop plan for filled long entries; do not use `--execute`.
9. Run `python3 script/trading_copilot.py paper-trade-review --date <DATE> --session pre-market --append` only after paper executions exist and should be recorded.
10. Treat paper results as execution feedback. Do not promote paper P/L directly into `knowledge/refined/`.

### Symbol Analysis

1. For current or recent analysis, first run a data-preparation workflow that covers the symbol, or state that fresh market data is unavailable.
2. Read the relevant snapshot/context artifact and `knowledge/refined/`.
3. Write a concise symbol memo with setup quality, scenarios, invalidation, risk, and `NO TRADE` when the rules are not satisfied.

### Research Note

1. Use `knowledge/source/` only as raw research material.
2. Promote conclusions only when they are consistent with `knowledge/refined/`.
3. Write the note as a research artifact; do not change refined rules unless the user explicitly asks for a rule promotion task.

### Rule Validation

1. Read the relevant artifact or user-supplied thesis.
2. Check it against `knowledge/refined/global/` first, then the relevant setup files under `knowledge/refined/setups/`.
3. Report pass/fail/unclear by rule area. Do not invent missing setup rules.

## Verification

- Syntax check after script changes: `python3 -m py_compile script/*.py`.
- Trading-day guard smoke test: `python3 script/trading_day_guard.py --date 2026-05-06 --format text`.
- Wrapper smoke test without market-data access: `python3 script/trading_copilot.py trading-day-check --date 2026-05-06`.
- Review smoke test without market-data access: `python3 script/trading_copilot.py weekly-review --week 2026-W22`.
- Fixture workflow smoke test: `python3 script/workflow_smoke_test.py --date 2026-05-26 --week 2026-W22`.
- Data-quality smoke test after a snapshot exists: `python3 script/trading_copilot.py data-quality --date 2026-05-26`.
- Data-fetch smoke tests use Longbridge CLI by default. Twelve Data fallback tests require `.env` with `TWELVE_DATA_API_KEY`.

## Output Contract

When running a workflow, clearly report:

- workflow name
- market/report date
- input files read
- output artifacts written
- skipped state and reason, if applicable
- data limitations and rule limitations

The detailed workflow and data contracts live in `docs/contracts/`.
