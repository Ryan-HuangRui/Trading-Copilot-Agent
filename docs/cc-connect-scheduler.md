# cc connect Scheduler Boundary

cc connect is the production scheduler and Feishu delivery surface. It should not own trading rules, validation rules, journal schemas, or review logic.

## Boundary

Use this split:

- cc connect: schedule, trigger Codex, receive the final summary, send Feishu messages.
- Codex: execute the repository workflow using the repo prompts, scripts, and contracts.
- Trading-Copilot-Agent: data preparation, report artifacts, validation, journal append, outcome backfill, self-review, weekly review.

Do not run the same production pre-market or post-market workflow from both cc connect and Codex App automation. Duplicate schedulers can duplicate reports, journal records, Longbridge sync, market-data calls, and Feishu messages.

## Production Wrapper Mode

The production pre-market and post-market report tasks should use a wrapper instead of a direct cc-connect prompt. The wrapper keeps full Codex stdout/stderr in local runtime logs and sends only the final human-facing summary to Feishu.

Use these commands in cc-connect `exec`:

```bash
/home/admin_ryan/repo/Trading-Copilot-Agent/ops/cc-connect/tca-report-wrapper.sh pre-market
/home/admin_ryan/repo/Trading-Copilot-Agent/ops/cc-connect/tca-report-wrapper.sh post-market
```

Wrapper-owned runtime paths:

```text
runtime/cc-connect/logs/pre-market/
runtime/cc-connect/logs/post-market/
runtime/cc-connect/out/pre-market-summary.md
runtime/cc-connect/out/post-market-summary.md
runtime/cc-connect/out/pre-market-delivery.env
runtime/cc-connect/out/post-market-delivery.env
```

The Codex prompts live in:

```text
ops/cc-connect/tca-pre-market-wrapper.prompt.md
ops/cc-connect/tca-post-market-wrapper.prompt.md
```

Codex must not call `cc-connect send` from those prompts. It writes the final summary and delivery metadata only. The shell wrapper sends the summary outside the Codex sandbox, then runs `script/report_delivery_guard.py --mark-sent` only after Feishu send succeeds.

Recommended live cc-connect fields:

```text
pre-market task:  exec=/home/admin_ryan/repo/Trading-Copilot-Agent/ops/cc-connect/tca-report-wrapper.sh pre-market
post-market task: exec=/home/admin_ryan/repo/Trading-Copilot-Agent/ops/cc-connect/tca-report-wrapper.sh post-market
prompt=UNUSED: production workflow is owned by exec wrapper ...
mute=true
session_mode=new_per_run
work_dir=/home/admin_ryan/repo/Trading-Copilot-Agent
```

`cc-connect cron edit` does not accept an empty prompt value. Keep a short `UNUSED` placeholder in `prompt` and rely on the `exec` field as the production entrypoint.

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

### 2. Update cc connect Production Wrappers

Update the configured cc connect production tasks to execute `ops/cc-connect/tca-report-wrapper.sh`. The wrapper prompts require the following artifacts and gates:

- Pre-market generation must write `exec-brief.md`, `pre-market.md`, and `pre-market-signals.json`.
- Post-market generation must write `post-market.md` and `post-market-signals.json`.
- After Codex/LLM writes those reports, record `llm-generation-manifest` so model, prompt, inputs, outputs, git SHA, and dirty files are auditable.
- Market-data preparation should use the repo default provider stack: Longbridge CLI primary, Twelve Data fallback.
- Both workflows should call `pre-market-deliver` or `post-market-deliver` after report generation. These wrappers run `validate-report`, `validate-trade-plan`, `data-quality`, `focus-selection`, journal append, Feishu summary, run manifest, and optional Longbridge sync. Post-market delivery also runs `daily-workflow-review` before `feishu-summary`.
- Optional agent research enhancement may be enabled with `--include-agent-research` on `pre-market-plan` and `post-market-review`; generated agent artifacts are evidence inputs only.
- Optional completed Vibe Swarm research may be consumed with
  `--include-vibe-research`. MCP invocation remains Codex-orchestrated; Python
  only hash-indexes persisted artifacts. Missing or pending Swarm runs are
  non-blocking, and each scheduled session may escalate at most one symbol.
- If agent research is enabled, cc connect must also surface `validate-agent-reports` / `validate-agent-decision` failures as blocking status before report generation consumes those artifacts.
- Agent report validation now fails when `market` or `technicals` evidence is empty. Empty `fundamentals`, `news`, or `sentiment` evidence remains a warning and must be disclosed in the Feishu summary or status note.
- Agent memory tasks are optional and must remain review-only: `agent-memory-append`, `agent-memory-review`, and `agent-memory-export` cannot modify `canonical rulebook/` or raise execution status.
- Post-market should run `data-quality --date <DATE>` before `feishu-summary` so focused-symbol fallback and stale-data warnings are disclosed.
- Any `extract-report-signals --require-validation` failure must stop journal append.
- Any `sync-longbridge-watchlist --require-validation` failure must stop watchlist sync.
- Post-market must run account/position review before `plan-review --append-lessons` when account context is enabled, so plan review can include position discipline.
- Post-market must include `learning-review --lookback-days 20` after `plan-review --append-lessons`.
- Post-market must include `daily-workflow-review` before Feishu delivery so same-day pre-market, intraday, and post-market process gaps are disclosed.
- Both workflows should generate `feishu-summary.md` through `feishu-summary`; Codex should copy that summary into `runtime/cc-connect/out/*-summary.md`, and the outer wrapper sends it instead of dumping the full Markdown report or raw transcript.
- `promote-lesson --apply` must not be scheduled automatically; run it only after human approval of a specific `pattern_id`.
- Paper execution must be scheduled as separate execution tasks. Do not add broker write operations to the pre-market or post-market report-generation tasks.
- Initial paper rollout should execute entries only. Keep paper cancel, protective-stop, and TP1 workflows in dry-run mode until exit-management safety is explicitly upgraded.
- Monitor paper flow is dry-run only in cc connect. `paper-trade-submit --session monitor --execute` is hard-disabled even if local config contains `allow_intraday_entry_submit=true`; any Phase 3 intraday paper entry must be a separate reviewed Codex task using `intraday-paper-entry`.

### 3. Update Failure Policy

Configure cc connect failure handling with these rules:

- `pre-market-plan` or `post-market-review` returns `skipped=true`: send a concise skipped/status message and stop that workflow.
- `daily-snapshot.json` has `stale_data=true`: do not generate a formal post-market review; send a data-not-ready status message.
- `validate-report` or `validate-trade-plan` fails: do not send the report as production output, do not append journal signals, and do not sync Longbridge. Send the validation errors as the Feishu status.
- `backfill-signal-outcomes`, `plan-review`, `learning-review`, `account-snapshot`, `position-review`, or `daily-self-review` fails: continue the main report delivery only if validation already passed, and include the failed step and reason in Feishu.
- Longbridge sync failure: keep the generated report and journal records, include the sync failure in Feishu, and rerun only `sync-longbridge-watchlist` after fixing Longbridge CLI/login/connectivity.
- Paper execution task failure: do not fail or regenerate the pre-market/post-market report. Send a paper-task status with the failing command, reason, and artifact paths.
- Paper dry-run submission with `summary.errors > 0`: do not execute automatically.
- Paper dry-run submission with `summary.ready = 0`: send a status note and stop the paper execution task.

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

Confirm paper execution config stays explicit and defaults to no broker writes. The tracked `config/paper_execution.json` should remain all false; deployment automation can pass an ignored host-local config such as `config/paper_execution.local.json` with `--paper-execution-config`.

```json
{
  "paper_execution": {
    "broker_writes_enabled": false,
    "allow_entry_submit": false,
    "allow_intraday_entry_submit": false,
    "allow_cancel": false,
    "allow_protective_stop": false,
    "allow_take_profit": false,
    "allow_order_replace": false,
    "allow_break_even_stop_move": false,
    "allow_auth_status_unknown_paper_channel": false
  }
}
```

Only enable the specific action gate on the deployment host after the dry-run workflow is accepted. Do not use environment variables as the paper execution gate.

`allow_intraday_entry_submit` belongs to the standalone `intraday-paper-entry` contract. The monitor session itself supports sidecar generation, validation, preview, submit dry-run, and Feishu summary only.

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

For paper execution acceptance, run the dry-run checks only:

```bash
python3 script/trading_copilot.py paper-account-snapshot --date <DATE> --paper-execution-config config/paper_execution.local.json
python3 script/trading_copilot.py paper-trade-preview --date <DATE> --session pre-market --require-validation
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation
```

Do not add `--execute` during acceptance unless you intentionally want to submit paper orders.

For agent research acceptance, run fixture or existing-date checks:

```bash
python3 script/trading_copilot.py agent-research-context --date <DATE> --symbol <SYMBOL>
python3 script/agent_market_data.py --date <DATE> --symbol <SYMBOL>
python3 script/agent_technicals.py --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py agent-research-reports --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py validate-agent-reports --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py agent-decision --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py validate-agent-decision --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py agent-memory-review --date <DATE> --symbol <SYMBOL>
```

For monitor dry-run acceptance:

```bash
python3 script/trading_copilot.py extract-monitor-signals --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session monitor --date <DATE>
python3 script/trading_copilot.py paper-trade-preview --date <DATE> --session monitor --require-validation
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session monitor --require-validation
python3 script/trading_copilot.py feishu-summary --session monitor --date <DATE>
```

Do not add `--execute` to monitor submit; the command must reject it.

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
# Or replace the previous line with this optional evidence-enhanced wrapper call:
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day --include-agent-research --include-vibe-research
# Codex generates report/<DATE>/exec-brief.md, report/<DATE>/pre-market.md, report/<DATE>/pre-market-signals.json
python3 script/trading_copilot.py llm-generation-manifest --session pre-market --date <DATE> --model <MODEL> --prompt .codex/skills/tca-pre-market-analysis/SKILL.md --input report/<DATE>/pre-market-context.json --generated-output report/<DATE>/exec-brief.md --generated-output report/<DATE>/pre-market.md --generated-output report/<DATE>/pre-market-signals.json
python3 script/trading_copilot.py pre-market-deliver --date <DATE> --sync-longbridge --execute-sync
```

`pre-market-deliver` runs both validation gates before journal append, Feishu summary, or Longbridge sync. It writes `report/<DATE>/pre-market-run-manifest.json` and `report/<DATE>/focus-selection.json`.

### Task B: Post-Market Review + Daily Self-Review

Recommended cc connect instruction:

```text
Run Trading-Copilot-Agent post-market close workflow for today:
prepare the completed daily snapshot, generate post-market.md and post-market-signals.json,
summarize same-day intraday monitor artifacts when present,
validate artifacts and Trade Plan Cards, backfill signal outcomes, extract post-market observation signals,
generate plan-review lessons, optionally run read-only account snapshot and position review, generate daily self-review,
generate same-day workflow review from pre-market/intraday/post-market artifacts and completed price evidence,
and return a Feishu-ready summary.
Do not place trades or output deterministic buy/sell instructions.
```

Repository workflow stages:

```bash
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols
# Or replace the previous line with this optional evidence-enhanced wrapper call:
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols --include-agent-research --include-vibe-research
# Codex reads optional report/<DATE>/intraday.md and runtime/intraday/<DATE>/{state.json,events.jsonl}, then generates report/<DATE>/post-market.md and report/<DATE>/post-market-signals.json
python3 script/trading_copilot.py llm-generation-manifest --session post-market --date <DATE> --model <MODEL> --prompt .codex/skills/tca-post-market-review/SKILL.md --input report/<DATE>/daily-snapshot.json --generated-output report/<DATE>/post-market.md --generated-output report/<DATE>/post-market-signals.json
python3 script/trading_copilot.py post-market-deliver --date <DATE> --sync-longbridge --execute-sync --append-outcomes --append-lessons --append-self-review
```

`post-market-deliver` runs validation, data-quality, focus-selection, outcome backfill, journal append, optional read-only account/position review, plan review, learning review, self-review, daily workflow review, Feishu summary, and optional `今日关注` replacement sync. It writes `report/<DATE>/post-market-run-manifest.json`, `report/<DATE>/focus-selection.json`, and `report/<DATE>/workflow-review.json/md`.

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

### Task D: Paper Pre-Market Dry Run

Recommended cc connect instruction:

```text
Run Trading-Copilot-Agent paper pre-market dry run for <DATE>:
verify the pre-market report and Trade Plan Cards, read the Longbridge paper account snapshot,
generate paper order previews, generate the controlled dry-run submission artifact, and return a Feishu-ready summary.
Do not use --execute and do not place, cancel, replace, or modify orders.
```

Repository workflow stages:

```bash
python3 script/trading_copilot.py validate-report --session pre-market --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <DATE>
python3 script/trading_copilot.py paper-account-snapshot --date <DATE> --paper-execution-config config/paper_execution.local.json
python3 script/trading_copilot.py paper-trade-preview --date <DATE> --session pre-market --require-validation
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation
```

Feishu should include:

- `report/<DATE>/paper-trade-preview.json`
- `report/<DATE>/paper-trade-submission.json`
- `summary.ready`
- `summary.blocked`
- `summary.skipped_duplicates`
- `summary.errors`

If `summary.errors > 0`, do not run the execution task automatically.

### Task E: Paper Entry Execution

Recommended cc connect instruction:

```text
Run Trading-Copilot-Agent paper entry execution for <DATE> only if the paper dry-run artifact was reviewed
or the scheduler's paper-entry policy allows automatic paper entry execution.
Submit only ready long entry intents to the Longbridge paper account.
Do not run cancel, protective-stop, TP1, break-even, or real-account operations.
```

Repository workflow stage:

```bash
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation --paper-execution-config config/paper_execution.local.json --execute
```

Required scheduler gates:

- selected paper execution config has `paper_execution.broker_writes_enabled=true`
- selected paper execution config has `paper_execution.allow_entry_submit=true`
- prior dry-run `summary.errors = 0`
- prior dry-run `summary.ready > 0`
- Longbridge paper account snapshot passes `account_channel=lb_papertrading`

Successful execution writes `runtime/paper/<DATE>/paper-orders.jsonl`. Re-runs must rely on duplicate `intent_id` checks and should report `skipped_duplicates` instead of submitting duplicate orders.

After a successful or partially successful execution attempt, the scheduler should refresh paper account state and run the read-only follow-up chain:

```bash
python3 script/trading_copilot.py paper-account-snapshot --date <DATE>
python3 script/trading_copilot.py paper-order-sync --date <DATE>
python3 script/trading_copilot.py paper-event-ledger --date <DATE>
python3 script/trading_copilot.py paper-execution-review --date <DATE>
```

If the broker accepted an order but the local journal was not written, do not re-run `paper-trade-submit --execute`. Recover the accepted order id first:

```bash
python3 script/trading_copilot.py paper-order-recover --date <DATE> --session pre-market --broker-order-id <ORDER_ID> --append
```

### Task F: Paper Order Sync And Review

Recommended cc connect instruction:

```text
Run Trading-Copilot-Agent paper order sync and execution review for <DATE>:
refresh the Longbridge paper account snapshot, sync submitted order state, project paper events,
generate execution review, append paper learning candidates, and refresh strategy-level paper review.
Run cancel/pending-order-replace/protective-stop/TP1 workflows in dry-run mode only.
```

Repository workflow stages:

```bash
python3 script/trading_copilot.py paper-lifecycle --date <DATE> --append-lessons --strategy-review
```

The wrapper expands to account snapshot, order sync, cancel/pending-order-replace/protective-stop/TP1/full-exit/break-even planning, a post-plan resync, event ledger, execution review, optional learning append, and optional strategy review.

Keep these execution switches disabled in the initial rollout:

```json
{
  "paper_execution": {
    "allow_cancel": false,
    "allow_order_replace": false,
    "allow_protective_stop": false,
    "allow_take_profit": false,
    "allow_break_even_stop_move": false,
    "allow_auth_status_unknown_paper_channel": false
  }
}
```

Do not add `--execute` to `paper-order-cancel`, `paper-order-replace`, `paper-protective-stop-plan`, `paper-take-profit-plan`, or `paper-break-even-stop-plan` while those config gates are false. `paper-order-replace` is limited to pending order quantity/limit-price changes from `paper-replace-decisions.json`; stop trigger movement stays cancel+submit. Current exit-management execution is intentionally dry-run because protective stops use the full filled quantity while TP1 uses a partial exit quantity; automatic execution needs OCO or stop resize/cancel-then-submit safety before rollout. If the Longbridge CLI omits `account_channel` from `auth status`, `allow_auth_status_unknown_paper_channel=true` may be used only in ignored host-local config after the host token has been separately verified as paper trading; explicit non-paper channels still fail.

The provided `ops/cc-connect/tca-paper-sync-review.sh` calls `paper-lifecycle`. It keeps each exit action dry-run unless the matching environment switch is set (`TCA_PAPER_CANCEL_EXECUTE=1`, `TCA_PAPER_ORDER_REPLACE_EXECUTE=1`, `TCA_PAPER_PROTECTIVE_STOP_EXECUTE=1`, `TCA_PAPER_TAKE_PROFIT_EXECUTE=1`, or `TCA_PAPER_BREAK_EVEN_STOP_EXECUTE=1`). Even with those switches, the selected `config/paper_execution.local.json` must enable `broker_writes_enabled=true` and the matching action gate.

## Optional Monitor Journal Task

If intraday monitoring is enabled, keep scan generation, sidecar generation, dry-run paper checks, and journal append separate. The tracked Codex wrapper is:

```bash
bash ops/cc-connect/tca-intraday-codex-monitor.sh <DATE>
```

Default wrapper behavior is read-only: run `tca-intraday-notify.sh`, append `report/<DATE>/intraday.md`, update `runtime/intraday/<DATE>/state.json`, and send Feishu only when the notification filter has an unsent important event.

Current NAS production cron enables opportunity-review dry-run, guarded intraday paper entry execution, and lifecycle management. Exit broker writes are controlled by separate switches so TP1 is not accidentally enabled together with a full-size protective stop:

```bash
TCA_INTRADAY_ENABLE_PAPER_DRY_RUN=1 \
TCA_INTRADAY_ENABLE_PAPER_LIFECYCLE=1 \
TCA_INTRADAY_PAPER_EXECUTE=1 \
TCA_INTRADAY_EXIT_EXECUTE=0 \
TCA_INTRADAY_CANCEL_EXECUTE=1 \
TCA_INTRADAY_ORDER_REPLACE_EXECUTE=0 \
TCA_INTRADAY_PROTECTIVE_STOP_EXECUTE=1 \
TCA_INTRADAY_TAKE_PROFIT_EXECUTE=0 \
TCA_INTRADAY_RESIZE_STOP_BEFORE_TAKE_PROFIT=0 \
TCA_INTRADAY_PLAN_EXIT_EXECUTE=0 \
TCA_INTRADAY_BREAK_EVEN_STOP_EXECUTE=0 \
bash ops/cc-connect/tca-intraday-codex-monitor.sh <DATE>
```

This requires the ignored NAS-local `config/paper_execution.local.json` to set `broker_writes_enabled=true`, `allow_intraday_entry_submit=true`, `allow_cancel=true`, and `allow_protective_stop=true`. The wrapper still submits only when Codex writes a validated monitor sidecar and `intraday-dry-run` reports ready orders. Pending order replace, TP1, plan-invalidated full exit, and break-even stop movement stay disabled in NAS cron by default. Pending order replace requires both `TCA_INTRADAY_ORDER_REPLACE_EXECUTE=1` and local gate `allow_order_replace=true`; it only updates unfilled pending order quantity/limit price. TP1 execution is blocked in code when an active protective stop quantity exceeds the post-TP1 remaining quantity unless `--resize-stop-before-submit` is explicitly used with the separate stop-resize gate. Plan-invalidated full exit requires both `TCA_INTRADAY_PLAN_EXIT_EXECUTE=1` and local gates `allow_exit_cancel_replace=true` plus `allow_exit_submit=true`.

Optional experimental micro paper learning is separate from the formal paper path and is disabled by default. It does not change `intraday-paper-entry` or `paper-trade-submit` eligibility. To preview learning samples after the Codex-reviewed monitor sidecar and intraday dry-run, use:

```bash
TCA_INTRADAY_ENABLE_EXPERIMENTAL_MICRO_PAPER=1 \
bash ops/cc-connect/tca-intraday-codex-monitor.sh <DATE>

python3 script/trading_copilot.py experimental-micro-paper-entry \
  --date <DATE> \
  --config config/experimental_micro_paper.json \
  --paper-execution-config config/paper_execution.local.json
```

To execute those learning samples against the paper account, both configs must opt in: `config/experimental_micro_paper.json` or an ignored local override must set `experimental_micro_paper.allow_experimental_micro_paper=true`, and the selected paper execution config must set `paper_execution.broker_writes_enabled=true` plus `paper_execution.allow_experimental_micro_paper=true`. Then set `TCA_INTRADAY_EXPERIMENTAL_MICRO_PAPER_EXECUTE=1` or run:

```bash
python3 script/trading_copilot.py experimental-micro-paper-entry \
  --date <DATE> \
  --config config/experimental_micro_paper.json \
  --paper-execution-config config/paper_execution.local.json \
  --execute
```

This writes only `runtime/learning/<DATE>/learning-trade-journal.jsonl` and marks each record `not_for_formal_stats=true`; it must not write formal `trades.jsonl` or the formal paper order journal.

Optional dry-run paper checks:

```bash
TCA_INTRADAY_ENABLE_PAPER_DRY_RUN=1 \
bash ops/cc-connect/tca-intraday-codex-monitor.sh <DATE>

python3 script/trading_copilot.py monitor-brief --state config/monitor_state.json --interval 5min
python3 script/trading_copilot.py intraday-opportunity-context --date <DATE>
# Codex reads observation_scans / sidecar_template.signals for the full observation universe,
# including price_evidence with 5m up to 78 bars, 15m 40 bars, daily 60 bars, key levels,
# and writes reviewed report/<DATE>/monitor-signals.json from the opportunity context.
python3 script/trading_copilot.py intraday-decision-coverage --date <DATE> --context report/<DATE>/intraday-opportunity-context.json --signals report/<DATE>/monitor-signals.json
python3 script/trading_copilot.py validate-trade-plan --session monitor --date <DATE> --signals report/<DATE>/monitor-signals.json
python3 script/trading_copilot.py paper-account-snapshot --date <DATE> --paper-execution-config config/paper_execution.local.json
python3 script/trading_copilot.py intraday-dry-run --date <DATE> --signals report/<DATE>/monitor-signals.json
python3 script/trading_copilot.py intraday-review-append --date <DATE> --signals report/<DATE>/monitor-signals.json --submission report/<DATE>/paper-trade-submission.json --context report/<DATE>/intraday-opportunity-context.json
```

If Codex writes a reviewed `report/<DATE>/monitor-signals.json` from `intraday-opportunity-context`, use:

```bash
python3 script/trading_copilot.py intraday-dry-run --date <DATE> --signals report/<DATE>/monitor-signals.json
python3 script/trading_copilot.py intraday-review-append --date <DATE> --signals report/<DATE>/monitor-signals.json --submission report/<DATE>/paper-trade-submission.json --context report/<DATE>/intraday-opportunity-context.json
```

Optional guarded intraday paper entry:

```bash
TCA_INTRADAY_ENABLE_PAPER_DRY_RUN=1 \
TCA_INTRADAY_PAPER_EXECUTE=1 \
TCA_PAPER_EXECUTION_CONFIG=config/paper_execution.local.json \
bash ops/cc-connect/tca-intraday-codex-monitor.sh <DATE>
```

The wrapper must use `intraday-paper-entry --execute`, never `paper-trade-submit --session monitor --execute`.

Optional guarded lifecycle planning:

```bash
TCA_INTRADAY_ENABLE_PAPER_LIFECYCLE=1 \
bash ops/cc-connect/tca-intraday-codex-monitor.sh <DATE>

python3 script/trading_copilot.py paper-lifecycle --date <DATE> --paper-execution-config config/paper_execution.local.json --append-lessons --strategy-review
python3 script/trading_copilot.py intraday-lifecycle-append --date <DATE>
```

After lifecycle planning or execution, read `report/<DATE>/intraday-lifecycle-summary.json`; send a Feishu lifecycle status only when the lifecycle wrapper reports actual executed/submitted/cancelled/replaced/moved actions, protective stop/exit risk actions, or critical errors. Do not send command execution traces, dry-run summaries, skipped/blocked states, artifact-only status, or candidate lessons as separate Feishu messages.

Use monitor sidecar and journal entries as observation records unless Codex writes a validated monitor Trade Plan Card and the explicit paper gates are enabled.

## Feishu Message Shape

The final Feishu message should be a concise summary with artifact paths:

- workflow name and date
- skipped state and reason, if skipped
- generated artifacts
- validation status
- journal append counts
- position review count and human-review count, if account snapshot was enabled
- self-review or weekly-review summary
- daily workflow review counts for possible missed candidates and touch-fade/invalidated observations
- plan-review position discipline summary and learning-review candidate count, when available
- data-quality status and focused-symbol fallback, when available
- monitor candidate/blocked/skipped counts, when a monitor task runs
- agent research validation status and decision artifact paths, when `--include-agent-research` is used
- paper dry-run ready/blocked/error counts, when a paper task runs
- paper execution submitted/skipped/error counts, when paper entry execution runs
- paper execution review and learning artifact paths, when paper review runs
- data limitations, if `stale_data=true` or any fetch errors exist

The full Markdown reports should remain in `report/<DATE>/` or `report/weekly/` and can be attached or linked by the cc connect integration.

For Longbridge account setup, read-only CLI assumptions, and position review threshold config, see `docs/longbridge-account-setup.md`.
For paper execution operation, scheduler switches, and rollout gates, see `docs/paper-execution-runbook.md`.

## Independent earnings research (P0 contract; P3 deployment)

Use `docs/contracts/earnings-research.md` and the earnings workflow plan. The planned NAS task runs once daily at 10:00 Asia/Shanghai, including weekends, through a separate earnings wrapper. Set `mute=true`; disable intermediate and final-result auto-forwarding. Role logs and normal skips stay local. Only the finalizer may explicitly call `cc-connect send` when its notification decision warrants it.

Reuse this repository's verified `CC_CONNECT_PROJECT`, `CC_CONNECT_SESSION` and binary configuration. Do not rely on working directory alone to route messages, and do not use the Feishu CLI bot configured for another workspace. Store report versions and notification receipts independently of existing pre/post-market daily delivery keys. This section does not create or enable a production schedule.
