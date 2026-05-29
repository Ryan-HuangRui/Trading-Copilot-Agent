# Longbridge Account Snapshot Setup

This runbook covers the read-only account snapshot, paper account snapshot, paper order preview/submit/cancel/protective-stop/review, and position review workflows. Production account workflows remain read-only. Paper writes are limited to guarded Longbridge paper-account entry orders, expired unfilled entry-order cancellation, and protective stop submission.

## Safety Boundary

- `script/longbridge_cli_adapter.py` owns Longbridge CLI access for account data.
- Allowed operations are read-only account, assets, positions, portfolio, quote, and market lookups.
- Order, cancel, replace, modify, trade, buy, sell, submit, and watchlist write tokens are rejected by the adapter.
- `script/longbridge_paper_trade_adapter.py` is separate and only supports Longbridge paper accounts. It may read paper order and execution lists after verifying `account_channel=lb_papertrading`.
- `script/longbridge_paper_order_adapter.py` is the only broker-write adapter. It requires `account_channel=lb_papertrading`, `--execute`, and `TRADING_COPILOT_PAPER_EXECUTION=enabled`, and currently supports simulated limit buy entry orders, expired unfilled entry-order cancellation, and protective stop submission.
- Downstream scripts read `runtime/account/<DATE>/account-snapshot.json` instead of calling Longbridge directly.

## Longbridge CLI Commands

The account snapshot script expects these read-only CLI commands to return JSON:

```bash
longbridge assets --format json
longbridge positions --format json
```

If your server uses a different executable path, pass it explicitly:

```bash
python3 script/trading_copilot.py account-snapshot --date <DATE> --longbridge-cli /path/to/longbridge
```

## Repository Commands

Create the runtime account snapshot:

```bash
python3 script/trading_copilot.py account-snapshot --date <DATE>
```

Create the runtime paper account snapshot:

```bash
python3 script/trading_copilot.py paper-account-snapshot --date <DATE>
```

Build dry-run paper order previews from validated Trade Plan Cards:

```bash
python3 script/trading_copilot.py paper-trade-preview --date <DATE> --session pre-market --require-validation
```

Prepare controlled paper submissions without calling broker write APIs:

```bash
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation
```

Submit passing paper entry intents through the guarded paper adapter:

```bash
TRADING_COPILOT_PAPER_EXECUTION=enabled \
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation --execute
```

Sync submitted paper order state from the latest paper account snapshot:

```bash
python3 script/trading_copilot.py paper-order-sync --date <DATE>
```

Project submitted and observed paper facts into the unified event ledger:

```bash
python3 script/trading_copilot.py paper-event-ledger --date <DATE>
```

Generate a paper execution quality review:

```bash
python3 script/trading_copilot.py paper-execution-review --date <DATE>
```

Build a dry-run cancel plan for expired unfilled paper entry orders:

```bash
python3 script/trading_copilot.py paper-order-cancel --date <DATE>
```

Cancel passing expired unfilled paper entry orders through the guarded paper adapter:

```bash
TRADING_COPILOT_PAPER_EXECUTION=enabled \
python3 script/trading_copilot.py paper-order-cancel --date <DATE> --execute
```

Build a dry-run protective stop plan for filled long paper entries:

```bash
python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE>
```

Submit guarded paper protective stops for filled long entries:

```bash
TRADING_COPILOT_PAPER_EXECUTION=enabled \
python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE> --execute
```

Build a dry-run TP1 partial-exit plan for filled long paper entries:

```bash
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE>
```

Submit guarded paper TP1 partial exits for filled long entries:

```bash
TRADING_COPILOT_PAPER_EXECUTION=enabled \
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE> --execute
```

Build a dry-run break-even stop movement plan after TP1 fill evidence exists:

```bash
python3 script/trading_copilot.py paper-break-even-stop-plan --date <DATE>
```

Review observed paper executions against the preview and append matched paper fills:

```bash
python3 script/trading_copilot.py paper-trade-review --date <DATE> --session pre-market --append
```

Review positions against the same day's structured signals:

```bash
python3 script/trading_copilot.py position-review --date <DATE> --config config/position_review.json --append
```

The review uses:

- `runtime/account/<DATE>/account-snapshot.json`
- `report/<DATE>/pre-market-signals.json` by default, or an explicit `--signals` sidecar path
- `config/position_review.json`

Outputs:

- `report/<DATE>/position-review.md`
- `report/<DATE>/position-review.json`
- `runtime/journal/position_reviews.jsonl` when `--append` is used

Paper outputs:

- `runtime/paper/<DATE>/paper-account-snapshot.json`
- `report/<DATE>/paper-trade-preview.json`
- `report/<DATE>/paper-trade-submission.json`
- `runtime/paper/<DATE>/paper-orders.jsonl` when `paper-trade-submit --execute` succeeds
- `runtime/paper/<DATE>/paper-execution-state.json`, including synced entry, protective stop, and TP1 state when the corresponding journals exist
- `runtime/journal/events.jsonl`
- `report/<DATE>/paper-event-ledger.json`
- `report/<DATE>/paper-execution-review.json`
- `report/<DATE>/paper-execution-review.md`
- `report/<DATE>/paper-order-cancel-plan.json`
- `report/<DATE>/paper-protective-stop-plan.json`
- `runtime/paper/<DATE>/paper-stop-orders.jsonl` when `paper-protective-stop-plan --execute` succeeds
- `report/<DATE>/paper-take-profit-plan.json`
- `runtime/paper/<DATE>/paper-take-profit-orders.jsonl` when `paper-take-profit-plan --execute` succeeds
- `report/<DATE>/paper-break-even-stop-plan.json`
- `report/<DATE>/paper-trade-review.json`
- matched paper fills in `runtime/journal/trades.jsonl` when `paper-trade-review --append` is used

## Position Review Config

Default config lives in `config/position_review.json`:

```json
{
  "position_review": {
    "close_to_invalidation_pct": 3,
    "high_concentration_pct": 25,
    "ignore_symbols": [],
    "core_holding_symbols": [],
    "require_trade_link": false
  }
}
```

- `close_to_invalidation_pct`: flags positions close to the structured invalidation price.
- `high_concentration_pct`: flags single-symbol concentration relative to account net liquidation.
- `ignore_symbols`: excludes symbols from the position review.
- `core_holding_symbols`: allows long-term/core holdings to be marked as `core_holding_not_in_plan` instead of requiring review only because they are absent from today's signals.
- `require_trade_link`: when `true`, non-core positions without a `trades.jsonl` record linked by `source_signal_id` require human review.

## trades.jsonl Linkage

Position review reads `runtime/journal/trades.jsonl` and `runtime/journal/signals.jsonl` through the local journal files. It does not call Longbridge again after the account snapshot is written.

For best linkage, human-entered trade records should include:

```json
{
  "kind": "trade",
  "date": "2026-05-27",
  "symbol": "MU",
  "status": "entered",
  "planned_setup": "breakout_pullback_continuation.md",
  "entry": 100,
  "stop": 95,
  "source_signal_id": "signal-id-from-signals-jsonl"
}
```

The position review will surface:

- `trade_link_state`: `linked_to_source_signal`, `trade_missing_source_signal_id`, or `no_trade_record`
- `source_signal_id`
- `linked_trade_date`
- `linked_trade_status`
- `entry`, `stop`, and `estimated_r` when enough trade data exists

These fields are review context only. They must not be converted into automatic order actions.

Use an alternate config when needed:

```bash
python3 script/trading_copilot.py position-review --date <DATE> --config config/position_review.json --append
```

## Fixture Test

Run without Longbridge access by passing a fixture account payload:

```bash
python3 script/longbridge_account_snapshot.py --date <DATE> --input path/to/account-fixture.json
```

Full fixture smoke test:

```bash
python3 script/workflow_smoke_test.py --date <DATE> --week <YYYY-Www> --account-input path/to/account-fixture.json
```

## cc connect Handling

Keep cc connect as the scheduler and Feishu delivery surface. The account snapshot and position review should run inside the pre-market and post-market repository workflow, after report signal extraction and before final Feishu delivery.

If account snapshot or position review fails, cc connect should continue the main report delivery and include the failure reason in the Feishu summary. Account data failure should not block report generation, validation, signal extraction, outcome backfill, or daily self-review.
