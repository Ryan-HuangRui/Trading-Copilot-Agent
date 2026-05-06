# Agent instructions (scope: script/)

## Scope and layout
- `twelve_data_client.py`: Twelve Data HTTP client, API-key lookup, and cross-process rate limiter.
- `fetch_daily.py`: generic batch fetch into `raw_data/<DATE>/<INTERVAL>/`.
- `prepare_market_snapshot.py`: canonical daily snapshot builder, writes `raw_data/<DATE>/<INTERVAL>/` and `report/<DATE>/daily-snapshot.json`.
- `sp500_universe.py`: S&P 500 holdings fetcher and deterministic dynamic-candidate scorer. Default source is iShares IVV holdings CSV.
- `prepare_daily_context.py`: pre-market context builder that reads the previous trading day's snapshot and writes `report/<DATE>/pre-market-context.json`.
- `pre_market_report.py`: scripted pre-market report generator.
- `monitor_scan.py`: 5m watchlist/position scan and `report/latest-monitor.json` writer.
- `report_delivery_guard.py`: idempotent delivery-state helper.
- `trading_day_guard.py` and `market_calendar.py`: simple US regular trading-day guard.
- `import_priceactions_knowledge.py`: normalizes source PriceActions docs and refreshes source metadata.

## Commands
- Syntax check: `python3 -m py_compile script/*.py`.
- Fetch daily data: `python3 script/fetch_daily.py --symbols AAPL,MSFT --interval 1day --output raw_data`.
- Prepare daily snapshot with guard: `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day`.
- Prepare daily snapshot with S&P 500 dynamic universe: `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15`.
- Fetch S&P 500 universe only: `python3 script/sp500_universe.py --top 100`.
- Prepare pre-market context with guard: `python3 script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day`.
- Check trading day: `python3 script/trading_day_guard.py`.
- Generate scripted report: `python3 script/pre_market_report.py --watchlist config/watchlist.json`.
- Generate scripted report with guard: `python3 script/pre_market_report.py --watchlist config/watchlist.json --skip-non-trading-day`.
- Monitor scan: `python3 script/monitor_scan.py --state config/monitor_state.json --interval 5min`.
- Refresh source metadata: `python3 script/import_priceactions_knowledge.py`.

## Conventions
- Run commands from the repository root unless a script explicitly documents otherwise.
- Keep scripts compatible with the standard library and `requirements.txt`.
- Load `TWELVE_DATA_API_KEY` from `.env` or the process environment; never hardcode or print secrets.
- Preserve the 8 requests/minute default unless the data provider contract is intentionally changed.
- Keep output writes under ignored runtime paths (`raw_data/`, `report/`, `config/rate_limit_state.json`) unless the task is metadata import.
- If adding a script that fetches market data, reuse `TwelveDataClient` and its shared limiter.
- S&P 500 universe fetches may use standard-library HTTP, but per-symbol market-data screening must still use `TwelveDataClient` and the shared limiter.
- For scheduled report scripts, support `--skip-non-trading-day` and use the market date in `America/New_York`.
- Snapshot builders should continue after per-symbol fetch failures and record failures in `errors`; same-day cache fallback must be marked with `used_cache`.
- Dynamic S&P 500 candidates should be written to `report/<DATE>/candidate-universe.json` and merged into the snapshot only for that date; do not mutate `config/watchlist.json`.

## Common pitfalls
- Twelve Data returns newest bars first; reverse only in code paths that need oldest-to-newest series.
- Daily snapshots use `raw_data/<SNAPSHOT_DATE>/<INTERVAL>/<SYMBOL>.json` and `report/<SNAPSHOT_DATE>/daily-snapshot.json`.
- Pre-market context uses `report/<PRE_MARKET_DATE>/pre-market-context.json` and references the previous trading day's snapshot.
- The trading-day guard covers standard NYSE full-day holidays, not special one-off closures or early closes.
- `pre_market_report.py` writes direct-script output under `report/<DATE>/` and raw data under `raw_data/<DATE>/<INTERVAL>/`.
- `report_delivery_guard.py` supports `pre-market`, `exec-brief`, and `post-market` report kinds.
- Trading outputs must include invalidation/risk framing and avoid direct buy/sell instructions.
