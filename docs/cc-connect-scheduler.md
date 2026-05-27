# cc connect Scheduler Boundary

cc connect is the production scheduler and Feishu delivery surface. It should not own trading rules, validation rules, journal schemas, or review logic.

## Boundary

Use this split:

- cc connect: schedule, trigger Codex, receive the final summary, send Feishu messages.
- Codex: execute the repository workflow using the repo prompts, scripts, and contracts.
- Trading-Copilot-Agent: data preparation, report artifacts, validation, journal append, outcome backfill, self-review, weekly review.

Do not run the same production pre-market or post-market workflow from both cc connect and Codex App automation. Duplicate schedulers can duplicate reports, journal records, Longbridge sync, Twelve Data calls, and Feishu messages.

## Required cc connect Prompt Updates

If cc connect was configured before the read-only position review workflow was added, update the existing production prompts as follows.

### Pre-Market Prompt Addition

Add this block after `extract-report-signals` and before Feishu delivery:

```text
Try to run the read-only position review steps:
python3 script/trading_copilot.py account-snapshot --date <DATE>
python3 script/trading_copilot.py position-review --date <DATE> --append

If either command fails, do not fail the pre-market report workflow.
Continue sending the report and include the failure reason in the Feishu summary.
Never place, cancel, replace, modify, or submit orders.
```

### Post-Market Prompt Addition

Add this block after `extract-report-signals` and before `daily-self-review`:

```text
Try to run the read-only position review steps:
python3 script/trading_copilot.py account-snapshot --date <DATE>
python3 script/trading_copilot.py position-review --date <DATE> --append

If either command fails, do not fail the post-market report workflow.
Continue with daily-self-review and Feishu delivery, and include the failure reason in the Feishu summary.
Never place, cancel, replace, modify, or submit orders.
```

### Feishu Summary Addition

When position review succeeds, include:

- account snapshot artifact path
- position review artifact paths
- total position count
- `review_required` count

When it fails, include:

- `position-review: skipped/failed`
- the failure reason
- a note that the main report was still delivered

## Production Tasks

### Task A: Pre-Market Report

Recommended cc connect instruction:

```text
Run Trading-Copilot-Agent pre-market workflow for today:
prepare the pre-market context, generate exec-brief.md, pre-market.md, and signals.json,
validate the artifacts, extract report signals into the journal, optionally run read-only account snapshot
and position review, and return a Feishu-ready summary.
Do not place trades or output deterministic buy/sell instructions.
```

Repository workflow stages:

```bash
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day
# Codex generates report/<DATE>/exec-brief.md, report/<DATE>/pre-market.md, report/<DATE>/signals.json
python3 script/trading_copilot.py validate-report --session pre-market --date <DATE>
python3 script/trading_copilot.py extract-report-signals --session pre-market --date <DATE> --require-validation --append
python3 script/trading_copilot.py account-snapshot --date <DATE>
python3 script/trading_copilot.py position-review --date <DATE> --append
```

### Task B: Post-Market Review + Daily Self-Review

Recommended cc connect instruction:

```text
Run Trading-Copilot-Agent post-market close workflow for today:
prepare the completed daily snapshot, generate post-market.md and signals.json,
validate artifacts, backfill signal outcomes, extract post-market observation signals,
optionally run read-only account snapshot and position review, generate daily self-review,
and return a Feishu-ready summary.
Do not place trades or output deterministic buy/sell instructions.
```

Repository workflow stages:

```bash
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day
# Codex generates report/<DATE>/post-market.md and report/<DATE>/signals.json
python3 script/trading_copilot.py validate-report --session post-market --date <DATE>
python3 script/trading_copilot.py backfill-signal-outcomes --date <DATE> --append
python3 script/trading_copilot.py extract-report-signals --session post-market --date <DATE> --require-validation --append
python3 script/trading_copilot.py account-snapshot --date <DATE>
python3 script/trading_copilot.py position-review --date <DATE> --append
python3 script/trading_copilot.py daily-self-review --date <DATE> --append
```

### Task C: Weekly Review

Recommended cc connect instruction:

```text
Run Trading-Copilot-Agent weekly review for ISO week <YYYY-Www>:
read the journal signals, outcomes, trades, and reviews; generate weekly-review.md;
append the weekly review record; return a Feishu-ready summary.
```

Repository workflow stage:

```bash
python3 script/trading_copilot.py weekly-review --week <YYYY-Www> --append
```

## Optional Monitor Journal Task

If intraday monitoring is enabled, keep scan generation separate from journal append:

```bash
python3 script/trading_copilot.py monitor-brief --state config/monitor_state.json --interval 5min
python3 script/trading_copilot.py extract-monitor-signals --append
```

Use monitor journal entries as observation records only. They are not execution instructions.

## Feishu Message Shape

The final Feishu message should be a concise summary with artifact paths:

- workflow name and date
- skipped state and reason, if skipped
- generated artifacts
- validation status
- journal append counts
- position review count and human-review count, if account snapshot was enabled
- self-review or weekly-review summary
- data limitations, if `stale_data=true` or any fetch errors exist

The full Markdown reports should remain in `report/<DATE>/` or `report/weekly/` and can be attached or linked by the cc connect integration.
