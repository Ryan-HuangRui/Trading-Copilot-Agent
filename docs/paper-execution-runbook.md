# Paper Execution Runbook

This runbook describes how to operate Longbridge paper trading as an execution extension to the existing pre-market and post-market workflows.

Paper execution depends on validated report artifacts, but broker write operations must be started explicitly through the dedicated paper workflows. Do not add broker writes to report generation prompts.

## Operating Model

Use this split:

- Pre-market and post-market workflows produce research artifacts, structured Trade Plan Cards, validation results, journal records, and Feishu summaries.
- Paper execution workflows consume `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json`.
- Paper broker writes are allowed only against `lb_papertrading`, only through `script/longbridge_paper_order_adapter.py`, and only when both `--execute` and the matching `config/paper_execution.json` action gate are enabled.
- Real-account writes remain prohibited.

Recommended initial rollout:

- Enable entry order execution only: `paper-trade-submit --execute`.
- Keep cancel, protective-stop, and TP1 execution disabled.
- Run exit-management commands in dry-run mode until OCO, stop resize, and cancel/replace safety are explicitly designed.

## Prerequisites

Run from the repository root after deploying the latest code:

```bash
git checkout master
git pull --ff-only
python3 -m py_compile script/*.py
python3 -m unittest discover tests
```

Confirm Longbridge CLI is authenticated to the paper account:

```bash
python3 script/trading_copilot.py paper-account-snapshot --date <DATE>
```

This workflow reads Longbridge auth, assets, positions, orders, and executions, and rejects non-paper accounts. The snapshot is written to:

```text
runtime/paper/<DATE>/paper-account-snapshot.json
```

Review the paper execution config before enabling broker writes:

```json
{
  "paper_execution": {
    "broker_writes_enabled": false,
    "allow_entry_submit": false,
    "allow_intraday_entry_submit": false,
    "allow_experimental_micro_paper": false,
    "allow_cancel": false,
    "allow_protective_stop": false,
    "allow_take_profit": false,
    "allow_order_replace": false,
    "allow_break_even_stop_move": false,
    "allow_auth_status_unknown_paper_channel": false
  }
}
```

The tracked default is intentionally all false. To enable a paper entry rollout on a deployment host, create an ignored host-local config such as `config/paper_execution.local.json`, set `broker_writes_enabled=true` and `allow_entry_submit=true`, and pass it with `--paper-execution-config`. Keep `allow_intraday_entry_submit`, `allow_experimental_micro_paper`, cancel, protective-stop, and take-profit gates false during the initial rollout. If the Longbridge CLI `auth status` response omits `account_channel`, only set `allow_auth_status_unknown_paper_channel=true` on a host separately verified to use the paper account token; explicit non-paper channels still fail.

Execution artifacts include `execution_policy` and `broker_capabilities` so operators can see which paper writes are enabled, disabled, or unsupported in the generated JSON without reading the local config file.

## Pre-Market Dry Run

Run this after the pre-market report has generated `pre-market-signals.json` and both validation gates pass:

```bash
DATE=<DATE>

python3 script/trading_copilot.py validate-report \
  --session pre-market \
  --date "$DATE"
python3 script/trading_copilot.py validate-trade-plan \
  --session pre-market \
  --date "$DATE"
python3 script/trading_copilot.py paper-account-snapshot \
  --date "$DATE"
python3 script/trading_copilot.py paper-trade-preview \
  --date "$DATE" \
  --session pre-market \
  --require-validation
python3 script/trading_copilot.py paper-trade-submit \
  --date "$DATE" \
  --session pre-market \
  --require-validation
```

Review:

```text
report/<DATE>/paper-trade-preview.json
report/<DATE>/paper-trade-submission.json
```

Check these fields before enabling execution:

- `summary.ready`
- `summary.blocked`
- `summary.skipped_duplicates`
- `summary.errors`
- `ready[].intent.quantity`
- `ready[].intent.limit_price`
- `ready[].risk_guard`

## Entry Execution

Only run this after reviewing the dry-run submission artifact:

```bash
python3 script/trading_copilot.py paper-trade-submit \
  --date "$DATE" \
  --session pre-market \
  --require-validation \
  --paper-execution-config config/paper_execution.local.json \
  --execute
```

Successful execution writes:

```text
runtime/paper/<DATE>/paper-orders.jsonl
report/<DATE>/paper-trade-submission.json
```

`paper-orders.jsonl` is written only for successfully submitted broker orders. Re-running the command should skip duplicate `intent_id` values.

If Longbridge accepts the paper order but local journal recording fails, recover the accepted order instead of re-running execution:

```bash
python3 script/trading_copilot.py paper-account-snapshot --date "$DATE"
python3 script/trading_copilot.py paper-order-recover \
  --date "$DATE" \
  --session pre-market \
  --broker-order-id "<ORDER_ID>" \
  --append
```

`paper-order-recover` is a local journal recovery workflow. It does not submit, cancel, or replace broker orders, preserves the submitted order shape for supported long-buy entry orders, and skips duplicate `intent_id` or `broker_order_id` records.

## Intraday And Post-Market Sync

Use these read-only or runtime-only commands to sync observed broker state and produce reviews:

```bash
python3 script/trading_copilot.py paper-account-snapshot --date "$DATE"
python3 script/trading_copilot.py paper-order-sync --date "$DATE"
python3 script/trading_copilot.py paper-event-ledger --date "$DATE"
python3 script/trading_copilot.py paper-execution-review --date "$DATE"
python3 script/trading_copilot.py paper-learning-lessons --date "$DATE" --append
python3 script/trading_copilot.py paper-strategy-review
```

`paper-event-ledger` reads entry, cancel-plan, replace, protective-stop, TP1, and full-exit artifacts by default. Successful pending-order replaces appear as deterministic `order_replaced` events, and executed/failed cancels appear as `order_cancel_executed` / `order_cancel_failed` events in `runtime/journal/events.jsonl`.

Optional compatibility projection into `runtime/journal/trades.jsonl`:

```bash
python3 script/trading_copilot.py paper-trade-review \
  --date "$DATE" \
  --session pre-market \
  --append
```

Paper lessons are candidate process feedback only. They do not modify `canonical rulebook/`; use `learning-review` and explicit human-approved `promote-lesson --apply` for promotion.

## Exit Management

These commands are useful as dry-run plans:

```bash
python3 script/trading_copilot.py paper-order-cancel --date "$DATE"
python3 script/trading_copilot.py paper-order-replace --date "$DATE"
python3 script/trading_copilot.py paper-protective-stop-plan --date "$DATE"
python3 script/trading_copilot.py paper-take-profit-plan --date "$DATE"
python3 script/trading_copilot.py paper-break-even-stop-plan --date "$DATE"
```

Do not enable these exit execution commands in the initial rollout:

```bash
python3 script/trading_copilot.py paper-order-cancel --date "$DATE" --execute

python3 script/trading_copilot.py paper-order-replace --date "$DATE" --execute

python3 script/trading_copilot.py paper-protective-stop-plan --date "$DATE" --execute

python3 script/trading_copilot.py paper-take-profit-plan --date "$DATE" --execute

python3 script/trading_copilot.py paper-break-even-stop-plan --date "$DATE" --execute
```

Reason: current protective-stop planning submits a stop for the full filled quantity, while TP1 planning submits a partial sell order. Break-even movement is implemented as cancel old stop plus submit a new MIT stop because Longbridge `order replace` cannot modify MIT trigger prices. `paper-order-replace` is limited to pending order quantity/limit-price changes from `report/<DATE>/paper-replace-decisions.json`; it must not be used for filled orders or stop trigger movement. Keep automatic exit execution disabled until the operator has reviewed OCO, stop resize, and cancel-then-submit state-drift risk.

For cc-connect deployments, `ops/cc-connect/tca-paper-sync-review.sh` runs cancel planning in dry-run mode by default. It adds `--execute` to `paper-order-cancel` only when `TCA_PAPER_CANCEL_EXECUTE=1` is set for that task, and the selected config must still enable `broker_writes_enabled=true` plus `allow_cancel=true`.

## Execution Config

Use a config file as the paper broker-write policy. The tracked `config/paper_execution.json` is the default all-off policy; deployment automation may pass an ignored host-local file such as `config/paper_execution.local.json`:

```json
{
  "paper_execution": {
    "broker_writes_enabled": true,
    "allow_entry_submit": true,
    "allow_intraday_entry_submit": false,
    "allow_experimental_micro_paper": false,
    "allow_cancel": false,
    "allow_protective_stop": false,
    "allow_take_profit": false,
    "allow_order_replace": false,
    "allow_break_even_stop_move": false,
    "allow_auth_status_unknown_paper_channel": false
  }
}
```

Interpretation:

- If `broker_writes_enabled=true` and `allow_entry_submit=true`, the scheduler may run `paper-trade-submit --execute` after the dry-run artifact has no blocking errors.
- If `broker_writes_enabled=true` and `allow_intraday_entry_submit=true`, a reviewed Codex intraday task may run `intraday-paper-entry --execute` after the monitor dry-run artifact has no blocking errors.
- If `broker_writes_enabled=true` and `allow_experimental_micro_paper=true`, `experimental-micro-paper-entry --execute` may submit independent paper-only learning trades after its own dry-run preview and `experimental_micro_paper.allow_experimental_micro_paper=true`; these records stay out of formal stats.
- `paper-trade-submit --session monitor --execute` is still hard-disabled even if a local config sets `allow_intraday_entry_submit=true`; use `intraday-paper-entry` for the dedicated Phase 3 path.
- If `allow_protective_stop=false` and `allow_take_profit=false`, the scheduler must run protective-stop and TP1 workflows without `--execute`.
- If `allow_cancel=false`, the scheduler must run cancel planning without `--execute`.
- If `allow_order_replace=false`, the scheduler must run pending order replace planning without `--execute`.
- If `TCA_PAPER_CANCEL_EXECUTE` is unset or not `1`, the provided cc-connect sync script keeps `paper-order-cancel` dry-run even when the local config enables cancellation.
- Every broker write still needs its own `--execute`; config alone never submits orders.

All paper execution commands accept an alternate config path:

```bash
python3 script/trading_copilot.py paper-trade-submit \
  --date "$DATE" \
  --session pre-market \
  --require-validation \
  --paper-execution-config config/paper_execution.local.json \
  --execute
```

## Failure Policy

- If report validation fails, do not run paper preview or execution.
- If `paper-account-snapshot` fails, stop the paper task and report the account/auth reason.
- If dry-run submission has `summary.errors > 0`, do not execute automatically.
- If dry-run submission has `summary.ready = 0`, send a status note and stop.
- If broker execution partially succeeds, keep `paper-orders.jsonl` as the source of submitted facts and run `paper-order-sync` before any follow-up planning.
- Do not retry execution by re-running with changed inputs until duplicate intent behavior and the existing `paper-orders.jsonl` have been reviewed.
