# Longbridge Account Snapshot Setup

This runbook covers the read-only account snapshot and position review workflow. It is for portfolio discipline checks only; it must not place, cancel, replace, modify, or submit orders.

## Safety Boundary

- `script/longbridge_cli_adapter.py` owns Longbridge CLI access for account data.
- Allowed operations are read-only account, assets, positions, portfolio, quote, and market lookups.
- Order, cancel, replace, modify, trade, buy, sell, submit, and watchlist write tokens are rejected by the adapter.
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

Review positions against the same day's structured signals:

```bash
python3 script/trading_copilot.py position-review --date <DATE> --append
```

The review uses:

- `runtime/account/<DATE>/account-snapshot.json`
- `report/<DATE>/signals.json`
- `config/position_review.json`

Outputs:

- `report/<DATE>/position-review.md`
- `report/<DATE>/position-review.json`
- `runtime/journal/position_reviews.jsonl` when `--append` is used

## Position Review Config

Default config lives in `config/position_review.json`:

```json
{
  "position_review": {
    "close_to_invalidation_pct": 3,
    "high_concentration_pct": 25,
    "ignore_symbols": [],
    "core_holding_symbols": []
  }
}
```

- `close_to_invalidation_pct`: flags positions close to the structured invalidation price.
- `high_concentration_pct`: flags single-symbol concentration relative to account net liquidation.
- `ignore_symbols`: excludes symbols from the position review.
- `core_holding_symbols`: allows long-term/core holdings to be marked as `core_holding_not_in_plan` instead of requiring review only because they are absent from today's signals.

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
