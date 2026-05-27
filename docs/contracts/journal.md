# Trading Copilot Journal Contract

The journal is an ignored runtime record used to close the loop between plans, observations, and later reviews. It is not a broker ledger and should not contain secrets or account identifiers.

Default location:

```bash
runtime/journal/
```

## Files

- `signals.jsonl`: planned or observed setup candidates extracted from reports or monitor scans.
- `outcomes.jsonl`: computed signal outcome backfills from completed daily snapshots.
- `trades.jsonl`: optional human-entered execution/outcome records.
- `reviews.jsonl`: post-market or weekly lessons linked back to reports, signals, or setups.

Each line is one JSON object. Fields are intentionally append-only so reports can be audited later.

## `signals.jsonl`

Required fields:

- `date`: `YYYY-MM-DD`.
- `session`: `pre-market`, `post-market`, or `monitor`.
- `symbol`: ticker symbol.
- `setup`: refined setup file name, or `NO VALID SETUP`.
- `status`: `planned`, `observed`, `triggered`, `invalidated`, or `no_trade`.
- `source_report`: report or artifact path.

Recommended fields:

- `regime`: market regime used for setup selection.
- `trigger`: trigger price or textual trigger condition.
- `invalidation`: invalidation price or textual abandonment condition.
- `risk_r`: planned risk in R units when available.
- `notes`: concise context.

Example:

```json
{"date":"2026-05-26","session":"pre-market","symbol":"MU","setup":"breakout_pullback_continuation.md","regime":"trend","trigger":"breaks and holds above prior high","invalidation":"falls back into prior range","risk_r":1.0,"status":"planned","source_report":"report/2026-05-26/pre-market.md"}
```

## `trades.jsonl`

Required fields:

- `date`
- `symbol`
- `status`: `entered`, `missed`, `no_trade`, `win`, `loss`, or `invalidated`.

Recommended fields:

- `planned_setup`
- `entry`
- `stop`
- `exit`
- `result_r`
- `lesson`
- `source_signal_id`

## `reviews.jsonl`

Required fields:

- `date`
- `scope`: `daily`, `weekly`, `setup`, or `symbol`.
- `summary`

Recommended fields:

- `setup`
- `symbol`
- `outcome`
- `lesson`
- `source_report`

## Append Tool

Use the report extractor for the normal report-to-journal path:

```bash
python3 script/trading_copilot.py extract-report-signals --date 2026-05-26 --session pre-market --require-validation --append
python3 script/trading_copilot.py extract-report-signals --date 2026-05-22 --session post-market --require-validation --append
```

The extractor records at most 3 focused candidates by default. It uses a stable `signal_id`, so rerunning the same extraction skips duplicate records in `signals.jsonl`.

Use the outcome backfill after the completed daily snapshot is available:

```bash
python3 script/trading_copilot.py backfill-signal-outcomes --date 2026-05-26 --append
```

Backfill reads `runtime/journal/signals.jsonl` and `report/<DATE>/daily-snapshot.json`, then appends computed records to `runtime/journal/outcomes.jsonl`. For pre-market signals, the target date is the signal date. For post-market signals, the target date is the next regular trading day. Daily bars cannot determine intraday order, so a signal that touches both trigger and invalidation is recorded as `triggered_and_invalidated`.

Use the lower-level append helper for manual entries:

```bash
python3 script/journal_append.py signal --date 2026-05-26 --session pre-market --symbol MU --setup breakout_pullback_continuation.md --status planned --source-report report/2026-05-26/pre-market.md
python3 script/journal_append.py trade --date 2026-05-26 --symbol MU --status missed --planned-setup breakout_pullback_continuation.md --lesson "triggered without clean pullback"
python3 script/journal_append.py review --date 2026-05-26 --scope daily --summary "Breakout candidates needed follow-through confirmation."
```
