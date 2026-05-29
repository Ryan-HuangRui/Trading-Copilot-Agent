# Agent instructions (scope: script/)

## Scope and layout
- `market_data_provider.py`: provider stack for Longbridge primary market data with Twelve Data fallback.
- `twelve_data_client.py`: Twelve Data HTTP client, API-key lookup, and cross-process rate limiter used for fallback.
- `fetch_daily.py`: generic batch fetch into `raw_data/<DATE>/<INTERVAL>/`.
- `prepare_market_snapshot.py`: canonical daily snapshot builder, writes `raw_data/<DATE>/<INTERVAL>/` and `report/<DATE>/daily-snapshot.json`.
- `sp500_universe.py`: S&P 500 holdings fetcher and deterministic dynamic-candidate scorer. Default source is iShares IVV holdings CSV.
- `prepare_daily_context.py`: pre-market context builder that reads the previous trading day's snapshot and writes `report/<DATE>/pre-market-context.json`.
- `pre_market_report.py`: scripted pre-market report generator.
- `monitor_scan.py`: 5m watchlist/position scan and `report/latest-monitor.json` writer.
- `longbridge_cli_adapter.py`: read-only Longbridge CLI guard. Do not add order/write commands.
- `longbridge_account_snapshot.py`: read-only account/position snapshot writer under `runtime/account/`.
- `longbridge_paper_trade_adapter.py`: Longbridge paper-account guard and read-only paper order/execution fetcher.
- `paper_account_snapshot.py`: read-only paper account, order, and execution snapshot writer under `runtime/paper/`.
- `paper_trade_preview.py`: converts validated Trade Plan Cards into dry-run paper order previews.
- `paper_trade_review.py`: compares dry-run previews with observed paper executions and can append matched paper fills to the journal.
- `position_review.py`: compares read-only positions with a session-specific signal sidecar and writes review artifacts.
- `data_quality.py`: checks daily snapshot data source/freshness, focused-symbol fallback, account price deltas, and abnormal moves.
- `validate_trade_plan.py`: structured Trade Plan Card validator for session sidecars.
- `plan_review.py`: plan-quality review and candidate lesson writer under `runtime/learning/`.
- `learning_review.py`: aggregates repeated daily lessons into `pattern_candidates.jsonl`.
- `feishu_summary.py`: concise Feishu-ready execution panel built from validated sidecars and review artifacts.
- `promote_lesson.py`: human-triggered promotion into `knowledge/evolution/validated_lessons.md`; never edits `knowledge/refined/`.
- `workflow_smoke_test.py`: fixture-based workflow smoke test; must not fetch live market or account data.
- `report_delivery_guard.py`: idempotent delivery-state helper.
- `trading_copilot.py`: unified agent-facing workflow wrapper that returns `status/date/artifacts/skipped/reason`.
- `trading_day_guard.py` and `market_calendar.py`: simple US regular trading-day guard.
- `import_priceactions_knowledge.py`: normalizes source PriceActions docs and refreshes source metadata.

## Commands
- Syntax check: `python3 -m py_compile script/*.py`.
- Fetch daily data: `python3 script/fetch_daily.py --symbols AAPL,MSFT --interval 1day --output raw_data`.
- Prepare daily snapshot with guard: `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day`.
- Prepare daily snapshot with S&P 500 dynamic universe: `python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15`.
- Fetch S&P 500 universe only: `python3 script/sp500_universe.py --top 100`.
- Prepare pre-market context with guard: `python3 script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day`.
- Run unified pre-market workflow: `python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day`.
- Run unified post-market workflow: `python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols`.
- Run unified monitor workflow: `python3 script/trading_copilot.py monitor-brief --state config/monitor_state.json --interval 5min`.
- Run read-only account snapshot: `python3 script/trading_copilot.py account-snapshot --date 2026-05-06`.
- Run read-only paper account snapshot: `python3 script/trading_copilot.py paper-account-snapshot --date 2026-05-06`.
- Build paper order previews: `python3 script/trading_copilot.py paper-trade-preview --date 2026-05-06 --session pre-market --require-validation`.
- Review paper executions: `python3 script/trading_copilot.py paper-trade-review --date 2026-05-06 --session pre-market --append`.
- Run position review: `python3 script/trading_copilot.py position-review --date 2026-05-06 --append`.
- Run data quality review: `python3 script/trading_copilot.py data-quality --date 2026-05-06`.
- Validate trade plan sidecars: `python3 script/trading_copilot.py validate-trade-plan --session pre-market --date 2026-05-06`.
- Review generated plans: `python3 script/trading_copilot.py plan-review --date 2026-05-06 --append-lessons`.
- Aggregate candidate lessons: `python3 script/trading_copilot.py learning-review --lookback-days 20`.
- Build Feishu summary: `python3 script/trading_copilot.py feishu-summary --session pre-market --date 2026-05-06`.
- Preview lesson promotion: `python3 script/trading_copilot.py promote-lesson --pattern-id <PATTERN_ID> --dry-run`.
- Run fixture workflow smoke test: `python3 script/workflow_smoke_test.py --date 2026-05-06 --week 2026-W19`.
- Check trading day through wrapper: `python3 script/trading_copilot.py trading-day-check --date 2026-05-06`.
- Check trading day: `python3 script/trading_day_guard.py`.
- Generate scripted report: `python3 script/pre_market_report.py --watchlist config/watchlist.json`.
- Generate scripted report with guard: `python3 script/pre_market_report.py --watchlist config/watchlist.json --skip-non-trading-day`.
- Monitor scan: `python3 script/monitor_scan.py --state config/monitor_state.json --interval 5min`.
- Refresh source metadata: `python3 script/import_priceactions_knowledge.py`.

## Conventions
- Run commands from the repository root unless a script explicitly documents otherwise.
- Keep scripts compatible with the standard library and `requirements.txt`.
- Load `TWELVE_DATA_API_KEY` from `.env` or the process environment only for Twelve Data fallback; never hardcode or print secrets.
- Preserve provider rate limits unless the data provider contract is intentionally changed. Longbridge uses `config/longbridge_rate_limit_state.json`; Twelve Data fallback uses `config/rate_limit_state.json`.
- Keep output writes under ignored runtime paths (`raw_data/`, `report/`, `config/rate_limit_state.json`, `config/longbridge_rate_limit_state.json`) unless the task is metadata import.
- Longbridge account workflows are read-only. Paper-trading workflows may inspect paper orders/executions and produce dry-run order previews, but must never submit, cancel, replace, or automatically adjust orders or positions.
- If adding a script that fetches market data, reuse `build_market_data_client()` so Longbridge remains primary and Twelve Data remains fallback.
- S&P 500 universe fetches may use standard-library HTTP, but per-symbol market-data screening must still use the shared market-data provider stack.
- For scheduled report scripts, support `--skip-non-trading-day` and use the market date in `America/New_York`.
- Agent-facing wrapper responses should keep the shared fields `status`, `workflow`, `date`, `artifacts`, `skipped`, and `reason`.
- Snapshot builders should continue after per-symbol fetch failures and record failures in `errors`; same-day cache fallback must be marked with `used_cache`.
- Dynamic S&P 500 candidates should be written to `report/<DATE>/candidate-universe.json` and merged into the snapshot only for that date; do not mutate `config/watchlist.json`.

## Common pitfalls
- The normalized market-data contract returns newest bars first. Longbridge raw K-line data returns oldest first and must be normalized before snapshot or monitor analysis.
- Daily snapshots use `raw_data/<SNAPSHOT_DATE>/<INTERVAL>/<SYMBOL>.json` and `report/<SNAPSHOT_DATE>/daily-snapshot.json`.
- Pre-market context uses `report/<PRE_MARKET_DATE>/pre-market-context.json` and references the previous trading day's snapshot.
- The trading-day guard covers standard NYSE full-day holidays, not special one-off closures or early closes.
- `pre_market_report.py` writes direct-script output under `report/<DATE>/` and raw data under `raw_data/<DATE>/<INTERVAL>/`.
- `report_delivery_guard.py` supports `pre-market`, `exec-brief`, and `post-market` report kinds.
- Trading outputs must include invalidation/risk framing and avoid direct buy/sell instructions.
