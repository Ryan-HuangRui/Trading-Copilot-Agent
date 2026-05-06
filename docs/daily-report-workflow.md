# Daily Report Workflow

This project uses Codex App automation as the scheduler and report-generation runner.

## Responsibility split
- `script/`: deterministic data work, market-date checks, path layout, cache fallback, and context generation.
- `agent/`: report-generation prompts that Codex automation actually reads.
- `knowledge/refined/`: the only trading-rule source for analysis conclusions.
- `docs/`: runbooks and operational documentation for humans and automation prompts.
- `AGENTS.md`: engineering guidance for Codex when maintaining this repository. It is not a trading-analysis prompt.

## Prompt loading rules
- Codex App automation does not automatically load every file under `agent/`.
- Post-market automation reads only `agent/post_market_analysis_prompt.md` plus the snapshot and refined rules.
- Pre-market automation reads only `agent/daily_analysis_prompt.md` plus the pre-market context and refined rules.
- Legacy OpenClaw/general coaching prompts are archived under `docs/legacy-prompts/` and are not part of scheduled report generation.

## Canonical data flow
1. After market close, generate one reusable daily snapshot:
   ```bash
   python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day
   ```
   Optional dynamic universe:
   ```bash
   python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15
   ```
2. The snapshot writes:
   - `raw_data/<SNAPSHOT_DATE>/<INTERVAL>/<SYMBOL>.json`
   - `report/<SNAPSHOT_DATE>/daily-snapshot.json`
   - with `--sp500-screen`: `report/<SNAPSHOT_DATE>/candidate-universe.json`
3. Post-market review reads the snapshot and writes:
   - `report/<SNAPSHOT_DATE>/post-market.md`
4. Next pre-market context reuses the previous trading day's snapshot:
   ```bash
   python3 script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day
   ```
5. Pre-market report generation reads:
   - `report/<PRE_MARKET_DATE>/pre-market-context.json`
6. Pre-market output writes:
   - `report/<PRE_MARKET_DATE>/exec-brief.md`
   - `report/<PRE_MARKET_DATE>/pre-market.md`

## Data freshness rules
- `daily-snapshot.json` contains `latest_bar_dates` and `stale_data`.
- If `stale_data=true`, Codex must not treat the snapshot as the completed session for `snapshot_date`.
- For post-market automation, `stale_data=true` should produce a skip/status note instead of a formal post-market review.
- For pre-market automation, stale data may still be usable only if the source snapshot is intentionally the previous completed trading session.

## Codex automation prompts

### Post-market automation
Run:
```bash
python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15
```

If output contains `skipped=true`, stop. If the generated `daily-snapshot.json` contains `stale_data=true`, write a short status note and stop. Otherwise read `agent/post_market_analysis_prompt.md`, `knowledge/refined/`, and `report/<SNAPSHOT_DATE>/daily-snapshot.json`, then generate `report/<SNAPSHOT_DATE>/post-market.md`.

The dynamic universe uses iShares IVV holdings CSV as the default source and falls back to Slickcharts if the primary source fails. If the screener itself fails, the snapshot still continues with the fixed watchlist and records the failure in `candidate-universe.json`.

### Pre-market automation
Run:
```bash
python3 script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day
```

If output contains `skipped=true`, stop. Otherwise read `agent/daily_analysis_prompt.md`, `knowledge/refined/`, and `report/<PRE_MARKET_DATE>/pre-market-context.json`, then generate:
- `report/<PRE_MARKET_DATE>/exec-brief.md`
- `report/<PRE_MARKET_DATE>/pre-market.md`

## Analysis boundaries
- Reports are research and process support only; they are not investment advice.
- Do not output deterministic buy/sell instructions.
- Every candidate must include setup reference, trigger, invalidation, and risk constraint.
- If market regime is unclear, data is insufficient, or refined rules do not support a setup, output `NO TRADE`.
- Dynamic S&P 500 candidates are only an observation universe; they must still pass refined setup rules before appearing as executable candidates.
