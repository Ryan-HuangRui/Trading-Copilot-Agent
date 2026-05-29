# Trading Copilot Data Contracts

Generated runtime artifacts live under ignored runtime paths, primarily `raw_data/`, `report/`, `config/rate_limit_state.json`, and `config/longbridge_rate_limit_state.json`. These files are inputs to agent analysis, not source code.

## Shared Conventions

- Dates use `YYYY-MM-DD`.
- Market dates are resolved in `America/New_York` unless a command explicitly overrides the timezone.
- Symbol arrays may include fixed watchlist symbols and temporary dynamic universe symbols.
- Dynamic S&P 500 candidates are observation candidates only; do not write them back to `config/watchlist.json`.
- Any artifact that contains trading observations must preserve scenario, invalidation, and risk framing.

## `report/<DATE>/daily-snapshot.json`

Producer:

```bash
python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols
```

Wrapper:

```bash
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols
```

Expected top-level fields:

- `snapshot_date`: completed market date for the snapshot.
- `generated_at`: generation timestamp if present.
- `trading_day`: trading-day guard payload.
- `watchlist_path`: source watchlist path.
- `symbols`: per-symbol market summaries.
- `watchlist_symbols`: fixed watchlist symbols included in the snapshot.
- `market_data_source`: effective provider stack, normally `longbridge_with_twelve_data_fallback`.
- `primary_market_data_source`: primary provider requested by the workflow, default `longbridge`.
- `fallback_market_data_source`: fallback provider requested by the workflow, default `twelve`.
- `dynamic_universe_symbols`: temporary dynamic candidates included for this snapshot only.
- `candidate_universe_path`: optional path to `candidate-universe.json`.
- `latest_bar_dates`: latest completed bar dates seen across symbols.
- `stale_data`: whether any symbol used stale or fallback data.
- `errors`: per-symbol or universe-fetch failures.

Consumer rules:

- Treat `errors` and `stale_data=true` as report limitations.
- Treat per-symbol `meta.fallback_from` as a data-source limitation worth disclosing when it affects a focused symbol.
- Do not infer recommendations from `dynamic_universe_symbols`; they are an observation universe.
- Do not invent missing indicators when a symbol summary lacks data.

## `report/<DATE>/candidate-universe.json`

Producer:

```bash
python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15
```

Expected top-level fields may include:

- `source`: S&P 500 universe source such as `ishares_ivv`.
- `snapshot_date`: market date used for scoring.
- `evaluated`: number of holdings evaluated.
- `selected`: selected candidate rows.
- `errors`: fetch or scoring failures.

Consumer rules:

- Use this file only to explain why a symbol entered the observation universe.
- Never treat selected rows as trade recommendations.

## `report/<DATE>/pre-market-context.json`

Producer:

```bash
python3 script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day
```

Wrapper:

```bash
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day
```

Expected top-level fields:

- `report_date`: target pre-market report date.
- `session`: expected to be `pre-market`.
- `source_snapshot_date`: completed trading date used as context.
- `source_snapshot_path`: path to the daily snapshot.
- `trading_day`: trading-day guard payload for the report date.
- `snapshot`: embedded daily snapshot payload.

Consumer rules:

- Use `source_snapshot_date` when explaining data freshness.
- If `snapshot.stale_data` is true, surface it in the report.
- If the source snapshot is missing, the workflow should fail before report writing.

## `report/<DATE>/data-quality.json`

Producer:

```bash
python3 script/trading_copilot.py data-quality --date <DATE>
```

Expected top-level fields:

- `date`
- `status`: `pass`, `warn`, or `fail`.
- `snapshot_path`
- `signal_sources`
- `market_data_source`, `primary_market_data_source`, and `fallback_market_data_source`
- `stale_data`, `stale_reason`, and `latest_bar_dates`
- `provider_summary`
- `focused_symbols`
- `missing_focused_symbols`
- `fallback_symbols`
- `focused_fallback_symbols`
- `account_price_deltas`
- `abnormal_moves`
- `snapshot_errors`

Consumer rules:

- Treat `missing_focused_symbols` as blocking data-quality failure.
- Disclose `focused_fallback_symbols` in Feishu summaries and focused reports.
- Treat `status=warn` as deliverable only with explicit data-quality disclosure.

## `report/<DATE>/<SESSION>-signals.json`

Producer:

- Codex report generation using `agent/daily_analysis_prompt.md` or `agent/post_market_analysis_prompt.md`.

Consumers:

```bash
python3 script/trading_copilot.py validate-report --session pre-market --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <DATE>
python3 script/trading_copilot.py extract-report-signals --session pre-market --date <DATE> --require-validation --append
python3 script/trading_copilot.py validate-report --session post-market --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session post-market --date <DATE>
python3 script/trading_copilot.py extract-report-signals --session post-market --date <DATE> --require-validation --append
```

Expected top-level fields:

- `date`: report date in `YYYY-MM-DD`.
- `session`: `pre-market` or `post-market`.
- `source_report`: Markdown report path that the sidecar represents.
- `signals`: at most 3 focused signal objects.

Expected signal fields:

- `symbol`: ticker symbol.
- `setup`: refined setup filename or `NO VALID SETUP`.
- `direction`: usually `long` for the current workflow.
- `regime`: trend/range/transition/Barb Wire label when available.
- `trigger`: object with `type`, numeric `price`, and human-readable `text`.
- `invalidation`: object with `type`, numeric `price`, and human-readable `text`.
- `risk`: object with `max_risk_pct` and optional `text`.
- `status`: `planned`, `observed`, or `no_trade`.
- `plan_type`: `trade_plan`, `watch_only`, or `no_trade`.
- `execution_status`: `conditional_executable`, `waiting_trigger`, `watch_only`, or `no_trade`.
- `entry`: for conditional plans, includes `trigger_price`, confirmation, and no-chase rule.
- `stop`: for conditional plans, includes `initial_stop` and invalidation text.
- `take_profit`: for conditional plans, includes `tp1` and management rules.
- `execution_rules`: for conditional plans, includes valid time window and `skip_conditions`.
- `notes`: concise context.

Consumer rules:

- Markdown remains the human-facing artifact; `pre-market-signals.json` and `post-market-signals.json` are the machine-facing artifacts.
- Full-session validation requires this file.
- `extract-report-signals` prefers this file and falls back to Markdown only when it is absent.
- Actionable signals must include structured trigger, invalidation, and risk fields.
- `conditional_executable` plans must include a complete Trade Plan Card and at least 2R to TP1.
- Incomplete plan cards should be downgraded to `watch_only` or `no_trade`, not delivered as executable.
- The session signal sidecar must match the report focus list.

## `runtime/learning/daily_lessons.jsonl`

Producer:

```bash
python3 script/trading_copilot.py plan-review --date <DATE> --append-lessons
```

Expected fields:

- `date`: review date.
  - `lesson_type`: `plan_quality`, `position_discipline`, or `paper_execution`.
- `symbol`, `setup`: evidence scope.
- `problem`: concise issue key such as `missing_take_profit`.
- `evidence`: short evidence strings.
- `suggested_constraint`: candidate future constraint.
- `status`: `candidate`.

Consumer rules:

- Daily lessons are runtime learning artifacts, not approved trading rules.
- They may be summarized into candidate patterns later.
- Do not promote them into `knowledge/refined/` without human review.

## `report/latest-monitor.json`

Producer:

```bash
python3 script/monitor_scan.py --state config/monitor_state.json --interval 5min
```

Wrapper:

```bash
python3 script/trading_copilot.py monitor-brief --state config/monitor_state.json --interval 5min
```

Expected top-level fields:

- `mode`: monitor mode such as `long_only`.
- `risk_per_trade_pct`: risk setting from monitor state.
- `interval`: market-data interval used.
- `scans`: per-symbol observation statuses.
- `positions`: position risk observations when positions are configured.

Consumer rules:

- Treat scan statuses as observations only.
- Position actions require human review.
- Include `NO TRADE` if data is insufficient or a setup lacks rule confirmation.
- Setup-backed scans may include `setup`, `setup_files`, `trigger_detail`, `invalidation_detail`, `risk_quality`, `journal_appendable`, and `bar_timestamp`.

## `runtime/account/<DATE>/account-snapshot.json`

Producer:

```bash
python3 script/trading_copilot.py account-snapshot --date <DATE>
```

Expected top-level fields:

- `date`
- `source`
- `generated_at`
- `account`: net liquidation, cash, and currency when available.
- `positions`: read-only position rows.
- `safety_note`

Expected position fields:

- `symbol`
- `market`
- `quantity`
- `avg_cost`
- `last_price`
- `market_value`
- `unrealized_pnl`
- `unrealized_pnl_pct`
- `currency`

Consumer rules:

- This is a local ignored runtime artifact.
- Use it only for read-only position review.
- Do not use it to place, cancel, replace, or modify orders.

## `report/<DATE>/position-review.json`

Producer:

```bash
python3 script/trading_copilot.py position-review --date <DATE> --config config/position_review.json --append
```

Expected top-level fields:

- `date`
- `source_account_snapshot`
- `source_signals`
- `position_reviews`
- `summary`

Consumer rules:

- Treat `review_required=true` as a prompt for human review only.
- `summary.empty_position_state=true` means there are no reviewed positions; keep the report informational.
- Review thresholds and core holding handling come from `config/position_review.json` unless an alternate `--config` path is passed.
- `summary.trade_link_state` summarizes whether positions were linked to `trades.jsonl` records and their `source_signal_id`.
- `position_reviews[].estimated_r` is an estimate from read-only position price plus human-entered `entry/stop`; it is not a broker-confirmed realized result.
- Do not convert risk states into automatic trading actions.

## Wrapper Status JSON

Producer:

```bash
python3 script/trading_copilot.py <workflow> [options]
```

Required fields:

- `status`: `success`, `skipped`, or `failed`.
- `workflow`: canonical workflow name.
- `date`: resolved market/report date when available.
- `artifacts`: artifact paths written or prepared for the agent.
- `skipped`: boolean skip flag.
- `reason`: skip or failure reason.
- `command`: underlying script command when applicable.
- `stdout`: parsed underlying JSON output when useful.

Consumer rules:

- On `skipped=true`, do not generate a trading report unless the user explicitly asks for a non-trading-day note.
- On `status=failed`, inspect `reason` before retrying data fetches to avoid rate-limit churn.
- On `status=success`, read the listed artifacts before producing any trading analysis.
