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
- `position_reviews.jsonl`: read-only position risk and plan-consistency review records.

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
- `trigger_price`: numeric trigger price when available.
- `trigger_detail`: structured trigger object from the session signal sidecar when available.
- `invalidation`: invalidation price or textual abandonment condition.
- `invalidation_price`: numeric invalidation price when available.
- `invalidation_detail`: structured invalidation object from the session signal sidecar when available.
- `risk_r`: planned risk in R units when available.
- `risk`: textual risk limit.
- `risk_detail`: structured risk object from the session signal sidecar when available.
- `plan_type`: `trade_plan`, `watch_only`, or `no_trade` when provided by the sidecar.
- `execution_status`: `conditional_executable`, `waiting_trigger`, `watch_only`, or `no_trade` when provided by the sidecar.
- `entry`, `stop`, `take_profit`, `execution_rules`: copied from complete Trade Plan Cards when present.
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

## `position_reviews.jsonl`

Required fields:

- `date`
- `symbol`
- `in_today_signals`
- `risk_state`
- `review_required`
- `source_account_snapshot`

Recommended fields:

- `market_value`
- `last_price`
- `source_signal_id`
- `linked_trade_date`
- `linked_trade_status`
- `trade_link_state`: `linked_to_source_signal`, `trade_missing_source_signal_id`, or `no_trade_record`.
- `entry`
- `stop`
- `estimated_r`
- `nearest_invalidation`
- `distance_to_invalidation_pct`
- `concentration_pct`
- `source_signals`

Consumer rules:

- These records are human-review prompts only.
- Do not treat them as order instructions.
- `trade_link_state` reflects whether the position can be connected to `trades.jsonl` and `signals.jsonl`; it is not an execution signal.

## Append Tool

Use the report extractor for the normal report-to-journal path:

```bash
python3 script/trading_copilot.py extract-report-signals --date 2026-05-26 --session pre-market --require-validation --append
python3 script/trading_copilot.py extract-report-signals --date 2026-05-22 --session post-market --require-validation --append
```

The extractor records at most 3 focused candidates by default. It uses a stable `signal_id`, so rerunning the same extraction skips duplicate records in `signals.jsonl`.

The extractor prefers `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json` and only falls back to Markdown parsing when the sidecar is absent. New production reports should generate both Markdown and the session signal sidecar.

## Plan Review Learning

`plan-review` reads `signals.jsonl`, `outcomes.jsonl`, and optional `trades.jsonl`, then writes:

- `report/<DATE>/plan-review.md`
- `report/<DATE>/plan-review.json`
- `runtime/learning/daily_lessons.jsonl` when `--append-lessons` is used.

Learning lessons are candidate process improvements only. They must not be treated as approved trading rules or promoted into `knowledge/refined/` without human review.

Use the outcome backfill after the completed daily snapshot is available:

```bash
python3 script/trading_copilot.py backfill-signal-outcomes --date 2026-05-26 --append
```

Backfill reads `runtime/journal/signals.jsonl` and `report/<DATE>/daily-snapshot.json`, then appends computed records to `runtime/journal/outcomes.jsonl`. For pre-market signals, the target date is the signal date. For post-market signals, the target date is the next regular trading day. Daily bars cannot determine intraday order, so a signal that touches both trigger and invalidation is recorded as `triggered_and_invalidated`.

Use daily and weekly review workflows after outcome backfill:

```bash
python3 script/trading_copilot.py daily-self-review --date 2026-05-26 --append
python3 script/trading_copilot.py weekly-review --week 2026-W22 --append
```

Use monitor extraction only for observation records:

```bash
python3 script/trading_copilot.py extract-monitor-signals --append
```

Use read-only account and position review workflows when account context is needed:

```bash
python3 script/trading_copilot.py account-snapshot --date 2026-05-26
python3 script/trading_copilot.py position-review --date 2026-05-26 --config config/position_review.json --append
```

Use the lower-level append helper for manual entries:

```bash
python3 script/journal_append.py signal --date 2026-05-26 --session pre-market --symbol MU --setup breakout_pullback_continuation.md --status planned --source-report report/2026-05-26/pre-market.md
python3 script/journal_append.py trade --date 2026-05-26 --symbol MU --status missed --planned-setup breakout_pullback_continuation.md --lesson "triggered without clean pullback"
python3 script/journal_append.py review --date 2026-05-26 --scope daily --summary "Breakout candidates needed follow-through confirmation."
```
