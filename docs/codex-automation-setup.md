# Codex App Automation Setup

This runbook is for setting up legacy Codex App automations on a new machine. For the current production cc connect scheduler boundary, prefer `docs/cc-connect-scheduler.md`.

## Machine setup
1. Clone the repository and enter the repo root.
2. Create the Python environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Ensure Longbridge CLI is installed and authenticated for primary market data:
   ```bash
   longbridge auth status
   longbridge check
   ```
4. Create `.env` from `.env.example` and set a Twelve Data fallback key when needed:
   ```bash
   TWELVE_DATA_API_KEY=<your-api-key>
   ```
5. Verify deterministic scripts:
   ```bash
   python3 -m py_compile script/*.py
   python3 script/trading_day_guard.py --format text
   python3 script/sp500_universe.py --top 5
   ```

Do not copy generated `raw_data/`, `report/`, `config/rate_limit_state.json`, or `config/longbridge_rate_limit_state.json` between machines unless intentionally restoring local runtime history.

## Automation shape
Create two Codex App automations against this repository root.

Use a worktree/local execution environment that can read the repo files, write ignored runtime outputs, and load the local `.env`.

### Post-market automation
Recommended schedule: weekdays after the U.S. regular session close, with enough delay for daily bars to settle.

Suggested wall-clock times:
- New York time: after 17:30.
- Asia/Shanghai during U.S. daylight time: next calendar day after 05:30.
- Asia/Shanghai during U.S. standard time: next calendar day after 06:30.

Automation prompt:
```text
Run the post-market workflow for this repository.

1. Run:
   python3 script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15 --include-journal-signals --include-position-symbols
   Optional wrapper-level agent research enhancement:
   python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15 --include-journal-signals --include-position-symbols --include-agent-research
2. If the command output contains skipped=true, stop without generating a report.
3. Read the generated report/<SNAPSHOT_DATE>/daily-snapshot.json.
4. If stale_data=true, write a short status note explaining that the completed daily bars are not ready and stop.
5. Otherwise read:
   - agent/post_market_analysis_prompt.md
   - canonical rulebook/
   - report/<SNAPSHOT_DATE>/daily-snapshot.json
   - optional report/<SNAPSHOT_DATE>/intraday.md
   - optional runtime/intraday/<SNAPSHOT_DATE>/state.json
   - optional runtime/intraday/<SNAPSHOT_DATE>/events.jsonl
   - optional report/<SNAPSHOT_DATE>/agents/<SYMBOL>/decision.json and role reports when `--include-agent-research` was used
6. Generate:
   - report/<SNAPSHOT_DATE>/post-market.md
   - report/<SNAPSHOT_DATE>/post-market-signals.json
7. Validate the generated artifacts. If validation fails, stop and do not sync Longbridge:
   python3 script/trading_copilot.py validate-report --session post-market --date <SNAPSHOT_DATE>
8. Validate the Trade Plan Cards. If validation fails, stop and do not sync Longbridge:
   python3 script/trading_copilot.py validate-trade-plan --session post-market --date <SNAPSHOT_DATE>
9. Backfill outcomes for prior plans whose target date is the completed snapshot date:
   python3 script/trading_copilot.py backfill-signal-outcomes --date <SNAPSHOT_DATE> --append
   If this fails, send or log a short status note, but do not treat it as a report-quality failure.
10. Append the focused post-market observation plan to the local journal:
   python3 script/trading_copilot.py extract-report-signals --session post-market --date <SNAPSHOT_DATE> --require-validation --append
11. Optionally run read-only account and position review:
   python3 script/trading_copilot.py account-snapshot --date <SNAPSHOT_DATE>
   python3 script/trading_copilot.py position-review --date <SNAPSHOT_DATE> --append
12. Run plan-review and append candidate lessons:
   python3 script/trading_copilot.py plan-review --date <SNAPSHOT_DATE> --append-lessons
13. Aggregate repeated lessons into pattern candidates:
   python3 script/trading_copilot.py learning-review --lookback-days 20
14. Run daily self-review:
   python3 script/trading_copilot.py daily-self-review --date <SNAPSHOT_DATE> --append
15. Run the same-day workflow review:
   python3 script/trading_copilot.py daily-workflow-review --date <SNAPSHOT_DATE>
16. Build the Feishu summary:
   python3 script/trading_copilot.py feishu-summary --session post-market --date <SNAPSHOT_DATE>
17. Update the Longbridge watchlist group `今日关注` as a full replacement from the post-market focus list:
   python3 script/trading_copilot.py sync-longbridge-watchlist --session post-market --date <SNAPSHOT_DATE> --group-name 今日关注 --sync-mode replace --require-validation --execute --no-create

Keep output in simplified Chinese. Treat S&P 500 dynamic candidates as an observation universe only, not investment advice.
The Longbridge sync replaces only the securities inside the `今日关注` group; symbols removed from that group must not be globally unfollowed or deleted from other watchlists.
```

### Pre-market automation
Recommended schedule: weekdays before the U.S. regular session open, after the previous post-market automation has completed.

Suggested wall-clock times:
- New York time: before 08:30.
- Asia/Shanghai during U.S. daylight time: same calendar day before 20:30.
- Asia/Shanghai during U.S. standard time: same calendar day before 21:30.

Automation prompt:
```text
Run the pre-market workflow for this repository.

1. Run:
   python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day
   Optional wrapper-level agent research enhancement:
   python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day --include-agent-research
   The wrapper collects report/<PRE_MARKET_DATE>/external-disclosures/trump-trades.json by default. Use --no-external-disclosures only when the disclosure source is intentionally disabled.
2. If the command output contains skipped=true, stop without generating a report.
3. Read the generated report/<PRE_MARKET_DATE>/pre-market-context.json.
4. Read:
   - agent/daily_analysis_prompt.md
   - canonical rulebook/
   - report/<PRE_MARKET_DATE>/pre-market-context.json
   - optional report/<PRE_MARKET_DATE>/external-disclosures/trump-trades.json
   - optional report/<PRE_MARKET_DATE>/agents/<SYMBOL>/decision.json and role reports when `--include-agent-research` was used
5. Generate all files:
   - report/<PRE_MARKET_DATE>/exec-brief.md
   - report/<PRE_MARKET_DATE>/pre-market.md
   - report/<PRE_MARKET_DATE>/pre-market-signals.json
   Both Markdown outputs must include `## 消息层汇总` with `### 特朗普持仓与交易变化`. Treat OGE/Open Cabinet/Quiver/InsiderCat disclosure data as news-layer background only; if no verified disclosure input is available, explicitly state the data gap. Do not use this section to upgrade any symbol's execution status; the report-generation LLM may still mark `conditional_executable` only when price action, refined setup rules, risk framing, and a complete Trade Plan Card independently support the plan.
6. Validate the generated reports. If validation fails, stop and do not sync Longbridge:
   python3 script/trading_copilot.py validate-report --session pre-market --date <PRE_MARKET_DATE>
7. Validate the Trade Plan Cards. If validation fails, stop and do not sync Longbridge:
   python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <PRE_MARKET_DATE>
8. Append the focused pre-market plan to the local journal:
   python3 script/trading_copilot.py extract-report-signals --session pre-market --date <PRE_MARKET_DATE> --require-validation --append
9. Optionally run read-only account and position review:
   python3 script/trading_copilot.py account-snapshot --date <PRE_MARKET_DATE>
   python3 script/trading_copilot.py position-review --date <PRE_MARKET_DATE> --append
10. Build the Feishu summary:
   python3 script/trading_copilot.py feishu-summary --session pre-market --date <PRE_MARKET_DATE>
11. Incrementally add the pre-market focus symbols to the Longbridge watchlist group `今日关注`:
   python3 script/trading_copilot.py sync-longbridge-watchlist --session pre-market --date <PRE_MARKET_DATE> --group-name 今日关注 --sync-mode add --require-validation --execute --no-create

Keep output in simplified Chinese. The merged universe may include fixed watchlist symbols and S&P 500 dynamic candidates, but every executable candidate must still pass refined setup rules.
The Longbridge sync is additive before market open and must not remove existing `今日关注` symbols.
```

`--require-validation` on `extract-report-signals` and `sync-longbridge-watchlist` runs both `validate-report` and `validate-trade-plan`. If either gate fails, stop the journal append or Longbridge sync and fix the Markdown/sidecar artifacts first.

## First-run checklist
- Run the post-market automation once first, or manually run its snapshot command, so the next pre-market job has a previous completed trading-day snapshot.
- Confirm `report/<DATE>/daily-snapshot.json` exists and contains `symbols`.
- If `--sp500-screen` is enabled, confirm `report/<DATE>/candidate-universe.json` exists.
- Confirm `report/<DATE>/post-market.md`, `report/<DATE>/post-market-signals.json`, `report/<DATE>/exec-brief.md`, `report/<DATE>/pre-market.md`, and `report/<DATE>/pre-market-signals.json` are generated by their respective jobs.
- Confirm post-market delivery generates `report/<DATE>/workflow-review.json` and `report/<DATE>/workflow-review.md` before Feishu summary delivery.
- Confirm `longbridge auth status` is valid before enabling `--execute` watchlist sync.

## Failure handling
- Longbridge market-data failure: fix CLI login/connectivity with `longbridge auth login` and `longbridge check`; if Twelve Data fallback is intended, verify `.env` has `TWELVE_DATA_API_KEY`.
- `stale_data=true` after market close: wait and rerun the post-market automation later.
- S&P 500 screener failure: the snapshot continues with the fixed watchlist and records the screener error in `candidate-universe.json`.
- Missing pre-market source snapshot: run the post-market snapshot workflow for the previous completed trading day first.
- Report or trade-plan validation failure: fix the generated report and session signal sidecar so every actionable candidate has setup, structured trigger price, structured invalidation price, risk framing, and complete Trade Plan Card fields when marked `conditional_executable`; rerun validation before Longbridge sync.
- Rate limiting: Longbridge market data uses `config/longbridge_rate_limit_state.json`; Twelve Data fallback uses the shared 8 requests/minute limiter in `config/rate_limit_state.json`.
- Longbridge watchlist sync failure: keep the generated report, fix CLI login/connectivity with `longbridge auth login` and `longbridge check`, then rerun only the `sync-longbridge-watchlist` command for that report date.
