# Daily Report Workflow

Production scheduling is expected to run through cc connect. cc connect triggers Codex and sends Feishu messages; the repository owns workflow contracts, validation, journal writes, and review generation. Do not run the same production pre-market or post-market workflow from both cc connect and Codex App automation.

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
   python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols
   ```
   Optional dynamic universe:
   ```bash
   python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15 --include-journal-signals --include-position-symbols
   ```
2. The snapshot writes:
   - `raw_data/<SNAPSHOT_DATE>/<INTERVAL>/<SYMBOL>.json`
   - `report/<SNAPSHOT_DATE>/daily-snapshot.json`
   - with `--sp500-screen`: `report/<SNAPSHOT_DATE>/candidate-universe.json`
3. Post-market review reads the snapshot and writes:
   - `report/<SNAPSHOT_DATE>/post-market.md`
   - `report/<SNAPSHOT_DATE>/post-market-signals.json`
4. Validate the generated post-market artifacts:
   ```bash
   python3 script/trading_copilot.py validate-report --session post-market --date <SNAPSHOT_DATE>
   python3 script/trading_copilot.py validate-trade-plan --session post-market --date <SNAPSHOT_DATE>
   ```
5. Backfill outcomes for plans whose target date is the completed snapshot date:
   ```bash
   python3 script/trading_copilot.py backfill-signal-outcomes --date <SNAPSHOT_DATE> --append
   ```
   If this fails, send or log a status note, but do not treat it as a trading report quality failure.
6. Append the focused post-market observation plan to `runtime/journal/signals.jsonl`:
   ```bash
   python3 script/trading_copilot.py extract-report-signals --session post-market --date <SNAPSHOT_DATE> --require-validation --append
   ```
7. Generate the plan review and candidate lessons:
   ```bash
   python3 script/trading_copilot.py plan-review --date <SNAPSHOT_DATE> --append-lessons
   ```
8. Generate the daily self-review:
   ```bash
   python3 script/trading_copilot.py daily-self-review --date <SNAPSHOT_DATE> --append
   ```
9. Post-market Longbridge sync fully replaces the `今日关注` group from the generated post-market focus list:
   ```bash
   python3 script/trading_copilot.py sync-longbridge-watchlist --session post-market --date <SNAPSHOT_DATE> --group-name 今日关注 --sync-mode replace --require-validation --execute --no-create
   ```
   This removes stale symbols from the `今日关注` group only; it must not globally unfollow securities or remove them from other watchlists.
10. Next pre-market context reuses the previous trading day's snapshot:
   ```bash
   python3 script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day
   ```
11. Pre-market report generation reads:
   - `report/<PRE_MARKET_DATE>/pre-market-context.json`
12. Pre-market output writes:
   - `report/<PRE_MARKET_DATE>/exec-brief.md`
   - `report/<PRE_MARKET_DATE>/pre-market.md`
   - `report/<PRE_MARKET_DATE>/pre-market-signals.json`
13. Validate the generated pre-market reports:
   ```bash
   python3 script/trading_copilot.py validate-report --session pre-market --date <PRE_MARKET_DATE>
   python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <PRE_MARKET_DATE>
   ```
14. Append the focused pre-market plan to `runtime/journal/signals.jsonl`:
   ```bash
   python3 script/trading_copilot.py extract-report-signals --session pre-market --date <PRE_MARKET_DATE> --require-validation --append
   ```
15. Optional read-only account and position review:
   ```bash
   python3 script/trading_copilot.py account-snapshot --date <PRE_MARKET_DATE>
   python3 script/trading_copilot.py position-review --date <PRE_MARKET_DATE> --append
   ```
16. Pre-market Longbridge sync incrementally adds the generated focus symbols to `今日关注`:
   ```bash
   python3 script/trading_copilot.py sync-longbridge-watchlist --session pre-market --date <PRE_MARKET_DATE> --group-name 今日关注 --sync-mode add --require-validation --execute --no-create
   ```

## Data freshness rules
- `daily-snapshot.json` contains `latest_bar_dates` and `stale_data`.
- If `stale_data=true`, Codex must not treat the snapshot as the completed session for `snapshot_date`.
- For post-market automation, `stale_data=true` should produce a skip/status note instead of a formal post-market review.
- For pre-market automation, stale data may still be usable only if the source snapshot is intentionally the previous completed trading session.

## cc connect-triggered Codex prompts

### Post-market task
Run:
```bash
python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15 --include-journal-signals --include-position-symbols
```

If output contains `skipped=true`, stop. If the generated `daily-snapshot.json` contains `stale_data=true`, write a short status note and stop. Otherwise read `agent/post_market_analysis_prompt.md`, `knowledge/refined/`, and `report/<SNAPSHOT_DATE>/daily-snapshot.json`, then generate `report/<SNAPSHOT_DATE>/post-market.md` and `report/<SNAPSHOT_DATE>/post-market-signals.json`.

The dynamic universe uses iShares IVV holdings CSV as the default source and falls back to Slickcharts if the primary source fails. If the screener itself fails, the snapshot still continues with the fixed watchlist and records the failure in `candidate-universe.json`.

After `post-market.md` is generated, validate it:
```bash
python3 script/trading_copilot.py validate-report --session post-market --date <SNAPSHOT_DATE>
python3 script/trading_copilot.py validate-trade-plan --session post-market --date <SNAPSHOT_DATE>
```

Only after both validations pass, backfill outcomes for the completed snapshot date:
```bash
python3 script/trading_copilot.py backfill-signal-outcomes --date <SNAPSHOT_DATE> --append
```

Then append the focused observation plan:
```bash
python3 script/trading_copilot.py extract-report-signals --session post-market --date <SNAPSHOT_DATE> --require-validation --append
```

Then review plans and aggregate repeated lessons:
```bash
python3 script/trading_copilot.py plan-review --date <SNAPSHOT_DATE> --append-lessons
python3 script/trading_copilot.py learning-review --lookback-days 20
```

Optionally capture a read-only account snapshot and position review:
```bash
python3 script/trading_copilot.py account-snapshot --date <SNAPSHOT_DATE>
python3 script/trading_copilot.py position-review --date <SNAPSHOT_DATE> --append
```

Then generate the daily self-review:
```bash
python3 script/trading_copilot.py daily-self-review --date <SNAPSHOT_DATE> --append
```

Then run:
```bash
python3 script/trading_copilot.py sync-longbridge-watchlist --session post-market --date <SNAPSHOT_DATE> --group-name 今日关注 --sync-mode replace --require-validation --execute --no-create
```
This is a full replacement of the `今日关注` group for tomorrow's focus list. Removing a symbol here only removes it from `今日关注`; do not delete the security globally or from other Longbridge watchlist groups.

### Pre-market task
Run:
```bash
python3 script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day
```

If output contains `skipped=true`, stop. Otherwise read `agent/daily_analysis_prompt.md`, `knowledge/refined/`, and `report/<PRE_MARKET_DATE>/pre-market-context.json`, then generate:
- `report/<PRE_MARKET_DATE>/exec-brief.md`
- `report/<PRE_MARKET_DATE>/pre-market.md`
- `report/<PRE_MARKET_DATE>/pre-market-signals.json`

After `exec-brief.md` and `pre-market.md` are generated, validate them:
```bash
python3 script/trading_copilot.py validate-report --session pre-market --date <PRE_MARKET_DATE>
python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <PRE_MARKET_DATE>
```

Only after both validations pass, append the focused pre-market plan:
```bash
python3 script/trading_copilot.py extract-report-signals --session pre-market --date <PRE_MARKET_DATE> --require-validation --append
```

Then run:
```bash
python3 script/trading_copilot.py sync-longbridge-watchlist --session pre-market --date <PRE_MARKET_DATE> --group-name 今日关注 --sync-mode add --require-validation --execute --no-create
```
This is additive only. It may add new focus symbols from the pre-market plan, but it must not remove existing `今日关注` symbols.

## Analysis boundaries
- Reports are research and process support only; they are not investment advice.
- Do not output deterministic buy/sell instructions.
- Every candidate must include setup reference, trigger, invalidation, and risk constraint.
- `--require-validation` on report extraction and Longbridge sync runs both report validation and trade-plan validation; do not continue when either gate fails.
- If market regime is unclear, data is insufficient, or refined rules do not support a setup, output `NO TRADE`.
- Dynamic S&P 500 candidates are only an observation universe; they must still pass refined setup rules before appearing as executable candidates.
