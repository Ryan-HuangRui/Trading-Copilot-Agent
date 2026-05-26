# Trading Copilot Data Contracts

Generated runtime artifacts live under ignored runtime paths, primarily `raw_data/`, `report/`, and `config/rate_limit_state.json`. These files are inputs to agent analysis, not source code.

## Shared Conventions

- Dates use `YYYY-MM-DD`.
- Market dates are resolved in `America/New_York` unless a command explicitly overrides the timezone.
- Symbol arrays may include fixed watchlist symbols and temporary dynamic universe symbols.
- Dynamic S&P 500 candidates are observation candidates only; do not write them back to `config/watchlist.json`.
- Any artifact that contains trading observations must preserve scenario, invalidation, and risk framing.

## `report/<DATE>/daily-snapshot.json`

Producer:

```bash
python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day
```

Wrapper:

```bash
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day
```

Expected top-level fields:

- `snapshot_date`: completed market date for the snapshot.
- `generated_at`: generation timestamp if present.
- `trading_day`: trading-day guard payload.
- `watchlist_path`: source watchlist path.
- `symbols`: per-symbol market summaries.
- `watchlist_symbols`: fixed watchlist symbols included in the snapshot.
- `dynamic_universe_symbols`: temporary dynamic candidates included for this snapshot only.
- `candidate_universe_path`: optional path to `candidate-universe.json`.
- `latest_bar_dates`: latest completed bar dates seen across symbols.
- `stale_data`: whether any symbol used stale or fallback data.
- `errors`: per-symbol or universe-fetch failures.

Consumer rules:

- Treat `errors` and `stale_data=true` as report limitations.
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
