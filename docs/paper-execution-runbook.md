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
    "allow_cancel": false,
    "allow_protective_stop": false,
    "allow_take_profit": false
  }
}
```

The tracked default is intentionally all false. To enable a paper entry rollout on a deployment host, create an ignored host-local config such as `config/paper_execution.local.json`, set `broker_writes_enabled=true` and `allow_entry_submit=true`, and pass it with `--paper-execution-config`. Keep cancel, protective-stop, and take-profit gates false during the initial rollout.

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

`paper-order-recover` is a local journal recovery workflow. It does not submit, cancel, or replace broker orders, and it skips duplicate `intent_id` or `broker_order_id` records.

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

Optional compatibility projection into `runtime/journal/trades.jsonl`:

```bash
python3 script/trading_copilot.py paper-trade-review \
  --date "$DATE" \
  --session pre-market \
  --append
```

Paper lessons are candidate process feedback only. They do not modify `knowledge/refined/`; use `learning-review` and explicit human-approved `promote-lesson --apply` for promotion.

## Exit Management

These commands are useful as dry-run plans:

```bash
python3 script/trading_copilot.py paper-order-cancel --date "$DATE"
python3 script/trading_copilot.py paper-protective-stop-plan --date "$DATE"
python3 script/trading_copilot.py paper-take-profit-plan --date "$DATE"
python3 script/trading_copilot.py paper-break-even-stop-plan --date "$DATE"
```

Do not enable these execution commands in the initial rollout:

```bash
python3 script/trading_copilot.py paper-order-cancel --date "$DATE" --execute

python3 script/trading_copilot.py paper-protective-stop-plan --date "$DATE" --execute

python3 script/trading_copilot.py paper-take-profit-plan --date "$DATE" --execute
```

Reason: current protective-stop planning submits a stop for the full filled quantity, while TP1 planning submits a partial sell order. Until OCO, stop resize, and cancel/replace behavior are explicitly implemented, automatic exit execution can create oversell or state-drift risk.

`paper-break-even-stop-plan` is plan-only. It does not cancel, replace, or submit broker orders.

## Execution Config

Use a config file as the paper broker-write policy. The tracked `config/paper_execution.json` is the default all-off policy; deployment automation may pass an ignored host-local file such as `config/paper_execution.local.json`:

```json
{
  "paper_execution": {
    "broker_writes_enabled": true,
    "allow_entry_submit": true,
    "allow_cancel": false,
    "allow_protective_stop": false,
    "allow_take_profit": false
  }
}
```

Interpretation:

- If `broker_writes_enabled=true` and `allow_entry_submit=true`, the scheduler may run `paper-trade-submit --execute` after the dry-run artifact has no blocking errors.
- If `allow_protective_stop=false` and `allow_take_profit=false`, the scheduler must run protective-stop and TP1 workflows without `--execute`.
- If `allow_cancel=false`, the scheduler must run cancel planning without `--execute`.
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
