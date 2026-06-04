---
name: trading-copilot
description: Use this repo-local skill for Trading-Copilot-Agent market research workflows, including pre-market planning, post-market review, intraday tracking, monitor brief generation, symbol analysis, research notes, rule validation, and paper-trading readiness review. It must not place real trades or output deterministic buy/sell instructions.
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

- Never place real trades or imply real-account order execution. Read-only account snapshots are allowed only through the repository's account snapshot workflow. Paper-trading broker writes are allowed only through dedicated paper workflows, only against `lb_papertrading`, and only when the user explicitly requests simulated execution with both `--execute` and the matching `config/paper_execution.json` action gate enabled.
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
2. Read `agent/post_market_analysis_prompt.md`, `knowledge/refined/`, `report/<DATE>/daily-snapshot.json`, and optional intraday artifacts `report/<DATE>/intraday.md`, `runtime/intraday/<DATE>/state.json`, and `runtime/intraday/<DATE>/events.jsonl`.
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
11. Build the Feishu execution summary, including a deterministic intraday-monitor recap when artifacts exist:
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

### Intraday Tracker

Use this for Phase 1 read-only pre-market plan tracking.

1. Run `python3 script/trading_copilot.py intraday-tracker --date <DATE> --top-n 5`.
2. Read `report/<DATE>/intraday.md`, `runtime/intraday/<DATE>/state.json`, and `runtime/intraday/<DATE>/events.jsonl` after the run.
3. Treat `report/<DATE>/intraday.md` as the human-readable rolling log.
4. Treat `runtime/intraday/<DATE>/state.json` as the machine-readable prior state.
5. Treat `runtime/intraday/<DATE>/events.jsonl` as notification candidates only, not broker instructions.
6. To build and deduplicate an active notification, run:
   `python3 script/intraday_event_notify.py --date <DATE> --mark-sent`.
7. To send through cc-connect, run:
   `bash ops/cc-connect/tca-intraday-notify.sh <DATE>`.

The tracker reads `report/<DATE>/pre-market-signals.json`, optional `config/intraday_watchlist.json`, and `report/latest-monitor.json`.

### Intraday Dry-Run

Use this for Phase 2 monitor candidates before any paper execution.

1. For LLM-reviewed opportunities, first run `python3 script/trading_copilot.py intraday-opportunity-context --date <DATE>` and read `report/<DATE>/intraday-opportunity-context.json`.
2. Codex may write `report/<DATE>/monitor-signals.json` from the context. Keep candidates `watch_only` unless a complete Trade Plan Card independently satisfies `knowledge/refined/`, risk, invalidation, and RR >= 2.
3. Run `python3 script/trading_copilot.py intraday-dry-run --date <DATE> --signals report/<DATE>/monitor-signals.json`.
4. If no LLM-reviewed sidecar is available, run `python3 script/trading_copilot.py intraday-dry-run --date <DATE>` to generate watch-only monitor candidates from deterministic extraction.
5. Confirm `paper_trade_preview.py` and `paper_trade_submit.py` ran for `session=monitor` without `--execute`.
6. Read the Feishu summary artifact for candidate, blocked, and skipped counts.

This workflow must not submit broker orders.

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
4. Only when the user explicitly wants simulated order submission and `config/paper_execution.json` enables `paper_execution.broker_writes_enabled=true` plus `paper_execution.allow_entry_submit=true`, run `python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation --execute`.
5. Run `python3 script/trading_copilot.py paper-order-sync --date <DATE>` after refreshing the paper account snapshot to sync submitted entry, stop, TP1, and plan-invalidated exit order state.
6. Run `python3 script/trading_copilot.py paper-event-ledger --date <DATE>` to project submitted and observed paper execution facts into `runtime/journal/events.jsonl`.
7. Run `python3 script/trading_copilot.py paper-execution-review --date <DATE>` to generate paper execution quality JSON/Markdown without promoting lessons.
8. Run `python3 script/trading_copilot.py paper-learning-lessons --date <DATE> --append` to append paper execution candidate lessons into the runtime learning queue.
9. Run `python3 script/trading_copilot.py paper-strategy-review` to aggregate paper execution reviews by setup and symbol.
10. Run `python3 script/trading_copilot.py paper-order-cancel --date <DATE>` to prepare a dry-run cancel plan for expired unfilled entry orders.
11. Only when the user explicitly wants simulated cancellation and `config/paper_execution.json` enables `paper_execution.allow_cancel=true`, run `python3 script/trading_copilot.py paper-order-cancel --date <DATE> --execute`.
12. Run `python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE>` to prepare a dry-run protective stop plan for filled long entries. The default stop order is `sell MIT`; alternative Longbridge order types may use `--order-type` with the matching price, trigger, trailing, `gtd`, and session fields.
13. Only when the user explicitly wants simulated protective stop submission and `config/paper_execution.json` enables `paper_execution.allow_protective_stop=true`, run `python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE> --execute`.
14. Run `python3 script/trading_copilot.py paper-take-profit-plan --date <DATE>` to prepare a dry-run TP1 partial-exit plan for filled long entries. The default TP1 order is `sell LO`; alternative Longbridge order types may use `--order-type` with the matching price, trigger, trailing, `gtd`, and session fields.
15. Only when the user explicitly wants simulated TP1 submission and `config/paper_execution.json` enables `paper_execution.allow_take_profit=true`, run `python3 script/trading_copilot.py paper-take-profit-plan --date <DATE> --execute`.
16. Run `python3 script/trading_copilot.py paper-exit-plan --date <DATE>` to prepare a full/remaining-position exit plan when `runtime/intraday/<DATE>/state.json` marks an open paper position as `invalidated` or when `report/<DATE>/paper-exit-decisions.json` contains a complete LLM-reviewed `action=exit_remaining` and `execution_status=conditional_executable` decision. It defaults to dry-run; only when the user explicitly wants simulated plan-invalidated exits and `config/paper_execution.json` enables both `paper_execution.allow_exit_cancel_replace=true` and `paper_execution.allow_exit_submit=true`, run `python3 script/trading_copilot.py paper-exit-plan --date <DATE> --execute`.
17. Run `python3 script/trading_copilot.py paper-break-even-stop-plan --date <DATE>` to prepare a break-even stop movement plan after TP1 fill evidence exists. It defaults to dry-run; only when the user explicitly wants simulated stop movement and `config/paper_execution.json` enables `paper_execution.allow_break_even_stop_move=true`, run `python3 script/trading_copilot.py paper-break-even-stop-plan --date <DATE> --execute`.
18. Run `python3 script/trading_copilot.py paper-trade-review --date <DATE> --session pre-market --append` only after paper executions exist and should be recorded.
19. Treat paper results as execution feedback. Do not promote paper P/L directly into `knowledge/refined/`.

For repeated lifecycle management, prefer the unified wrapper:

```bash
python3 script/trading_copilot.py paper-lifecycle --date <DATE>
```

Use `--execute-cancel`, `--execute-protective-stop`, `--execute-take-profit`, `--execute-exit`, or `--execute-break-even-stop` only with the matching paper execution config gates enabled.

### Intraday Paper Entry

Use this only for Phase 3 after reviewed monitor dry-run evidence exists.

1. Dry-run first:
   `python3 script/trading_copilot.py intraday-paper-entry --date <DATE> --require-validation`.
2. Only when the user explicitly wants simulated intraday paper entry submission and `config/paper_execution.local.json` enables `broker_writes_enabled=true` plus `allow_intraday_entry_submit=true`, run:
   `python3 script/trading_copilot.py intraday-paper-entry --date <DATE> --require-validation --execute --paper-execution-config config/paper_execution.local.json`.
3. Never use `paper-trade-submit --session monitor --execute`; that path must remain hard-rejected.
4. Treat the output `report/<DATE>/intraday-paper-entry.json` as paper execution evidence for follow-up sync/review, not as investment advice.

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
