# cc connect Scheduler Boundary

cc connect is the production scheduler and Feishu delivery surface. It should not own trading rules, validation rules, journal schemas, or review logic.

## Boundary

Use this split:

- cc connect: schedule, trigger Codex, receive the final summary, send Feishu messages.
- Codex: execute the repository workflow using the repo prompts, scripts, and contracts.
- Trading-Copilot-Agent: data preparation, report artifacts, validation, journal append, outcome backfill, self-review, weekly review.

Do not run the same production pre-market or post-market workflow from both cc connect and Codex App automation. Duplicate schedulers can duplicate reports, journal records, Longbridge sync, market-data calls, and Feishu messages.

## Server Update Checklist

Apply these changes on the server that runs cc connect before enabling the upgraded workflow.

### 1. Deploy Repository Code

On the Trading-Copilot-Agent checkout used by cc connect:

```bash
git fetch origin
git checkout master
git pull --ff-only
python3 -m py_compile script/*.py
python3 -m unittest discover tests
python3 script/trading_day_guard.py --date 2026-05-06 --format text
```

Expected smoke result:

```text
TRADING_DAY 2026-05-06 regular_session
```

If the server does not run the full test suite in production, at minimum run:

```bash
python3 -m py_compile script/*.py
python3 script/trading_copilot.py trading-day-check --date 2026-05-06
```

### 2. Update cc connect Production Prompts

Update the configured cc connect prompts so they require the new artifacts and gates:

- Pre-market generation must write `exec-brief.md`, `pre-market.md`, and `pre-market-signals.json`.
- Post-market generation must write `post-market.md` and `post-market-signals.json`.
- Market-data preparation should use the repo default provider stack: Longbridge CLI primary, Twelve Data fallback.
- Both workflows must run `validate-report` and `validate-trade-plan` before journal append or Longbridge sync.
- Any `extract-report-signals --require-validation` failure must stop journal append.
- Any `sync-longbridge-watchlist --require-validation` failure must stop watchlist sync.
- Post-market must run account/position review before `plan-review --append-lessons` when account context is enabled, so plan review can include position discipline.
- Post-market must include `learning-review --lookback-days 20` after `plan-review --append-lessons`.
- Both workflows should generate `feishu-summary.md` through `feishu-summary` and send that summary body instead of dumping the full Markdown report.
- `promote-lesson --apply` must not be scheduled automatically; run it only after human approval of a specific `pattern_id`.

### 3. Update Failure Policy

Configure cc connect failure handling with these rules:

- `pre-market-plan` or `post-market-review` returns `skipped=true`: send a concise skipped/status message and stop that workflow.
- `daily-snapshot.json` has `stale_data=true`: do not generate a formal post-market review; send a data-not-ready status message.
- `validate-report` or `validate-trade-plan` fails: do not send the report as production output, do not append journal signals, and do not sync Longbridge. Send the validation errors as the Feishu status.
- `backfill-signal-outcomes`, `plan-review`, `learning-review`, `account-snapshot`, `position-review`, or `daily-self-review` fails: continue the main report delivery only if validation already passed, and include the failed step and reason in Feishu.
- Longbridge sync failure: keep the generated report and journal records, include the sync failure in Feishu, and rerun only `sync-longbridge-watchlist` after fixing Longbridge CLI/login/connectivity.

### 4. Confirm Runtime Paths

Confirm the cc connect job uses the same repository root and writable ignored runtime paths:

```text
raw_data/
report/
runtime/
config/rate_limit_state.json
config/longbridge_rate_limit_state.json
```

Confirm server secrets and local state are not committed:

```text
.env
runtime/
report/
raw_data/
config/rate_limit_state.json
config/longbridge_rate_limit_state.json
```

### 5. Server Acceptance Check

After updating the prompts, run one dry validation cycle against an existing report date that already has the required generated artifacts:

```bash
python3 script/trading_copilot.py validate-report --session pre-market --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <DATE>
python3 script/trading_copilot.py extract-report-signals --session pre-market --date <DATE> --require-validation

python3 script/trading_copilot.py validate-report --session post-market --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session post-market --date <DATE>
python3 script/trading_copilot.py learning-review --lookback-days 20
```

Do not add `--append` or `--execute` during the acceptance check unless you intentionally want to mutate the journal or Longbridge watchlist.

## Required cc connect Prompt Updates

If cc connect was configured before the read-only position review workflow was added, update the existing production prompts as follows.

### Pre-Market Prompt Addition

Add this block after `extract-report-signals` and before Feishu delivery:

```text
Try to run the read-only position review steps:
python3 script/trading_copilot.py account-snapshot --date <DATE>
python3 script/trading_copilot.py position-review --date <DATE> --config config/position_review.json --append

If either command fails, do not fail the pre-market report workflow.
Continue sending the report and include the failure reason in the Feishu summary.
Never place, cancel, replace, modify, or submit orders.
```

### Post-Market Prompt Addition

Add this block after `extract-report-signals` and before `daily-self-review`:

```text
Try to run the read-only position review steps:
python3 script/trading_copilot.py account-snapshot --date <DATE>
python3 script/trading_copilot.py position-review --date <DATE> --config config/position_review.json --append

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
prepare the pre-market context, generate exec-brief.md, pre-market.md, and pre-market-signals.json,
validate the artifacts and Trade Plan Cards, extract report signals into the journal, optionally run read-only account snapshot
and position review, and return a Feishu-ready summary.
Do not place trades or output deterministic buy/sell instructions.
```

Repository workflow stages:

```bash
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day
# Codex generates report/<DATE>/exec-brief.md, report/<DATE>/pre-market.md, report/<DATE>/pre-market-signals.json
python3 script/trading_copilot.py validate-report --session pre-market --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <DATE>
python3 script/trading_copilot.py extract-report-signals --session pre-market --date <DATE> --require-validation --append
python3 script/trading_copilot.py account-snapshot --date <DATE>
python3 script/trading_copilot.py position-review --date <DATE> --config config/position_review.json --append
python3 script/trading_copilot.py feishu-summary --session pre-market --date <DATE>
```

`extract-report-signals --require-validation` runs both `validate-report` and `validate-trade-plan`; if either gate fails, do not append journal records or continue to Longbridge sync. `sync-longbridge-watchlist --require-validation` repeats both gates before any watchlist update.

### Task B: Post-Market Review + Daily Self-Review

Recommended cc connect instruction:

```text
Run Trading-Copilot-Agent post-market close workflow for today:
prepare the completed daily snapshot, generate post-market.md and post-market-signals.json,
validate artifacts and Trade Plan Cards, backfill signal outcomes, extract post-market observation signals,
generate plan-review lessons, optionally run read-only account snapshot and position review, generate daily self-review,
and return a Feishu-ready summary.
Do not place trades or output deterministic buy/sell instructions.
```

Repository workflow stages:

```bash
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols
# Codex generates report/<DATE>/post-market.md and report/<DATE>/post-market-signals.json
python3 script/trading_copilot.py validate-report --session post-market --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session post-market --date <DATE>
python3 script/trading_copilot.py backfill-signal-outcomes --date <DATE> --append
python3 script/trading_copilot.py extract-report-signals --session post-market --date <DATE> --require-validation --append
python3 script/trading_copilot.py account-snapshot --date <DATE>
python3 script/trading_copilot.py position-review --date <DATE> --config config/position_review.json --append
python3 script/trading_copilot.py plan-review --date <DATE> --append-lessons
python3 script/trading_copilot.py learning-review --lookback-days 20
python3 script/trading_copilot.py daily-self-review --date <DATE> --append
python3 script/trading_copilot.py feishu-summary --session post-market --date <DATE>
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
- plan-review position discipline summary and learning-review candidate count, when available
- data limitations, if `stale_data=true` or any fetch errors exist

The full Markdown reports should remain in `report/<DATE>/` or `report/weekly/` and can be attached or linked by the cc connect integration.

For Longbridge account setup, read-only CLI assumptions, and position review threshold config, see `docs/longbridge-account-setup.md`.
