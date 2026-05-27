# Trading Copilot Workflow Contracts

This repository is a trading research workflow package for Codex/Claude/OpenClaw-style agents. It is not a broker, execution engine, or standalone trading product.

All workflows must preserve the repository safety rules:

- Never place real trades or call broker APIs.
- Do not output deterministic buy/sell instructions.
- Use scenarios, triggers, invalidation, risk, and `NO TRADE`.
- Use `knowledge/refined/` as the only rule source for trading conclusions.
- Fetch or prepare real market data before making current/recent symbol claims.

## Shared Status Envelope

Machine-callable workflows should return a JSON object with these fields:

```json
{
  "status": "success",
  "workflow": "pre-market-plan",
  "date": "2026-05-26",
  "artifacts": ["report/2026-05-26/pre-market-context.json"],
  "skipped": false,
  "reason": null
}
```

Field meanings:

- `status`: `success`, `skipped`, or `failed`.
- `workflow`: the canonical workflow name.
- `date`: the market/report date when the workflow can resolve one.
- `artifacts`: paths that were written or should be read next.
- `skipped`: `true` only when the workflow intentionally did not run.
- `reason`: skip or failure reason. Use `null` for a normal successful run.

## pre-market-plan

Purpose: prepare the context that a human-facing pre-market report agent will use.

Canonical command:

```bash
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day
```

Inputs:

- `config/watchlist.json`
- Prior completed trading day's `report/<SNAPSHOT_DATE>/daily-snapshot.json`
- `knowledge/refined/`
- `agent/daily_analysis_prompt.md`

Deterministic script output:

- `report/<PRE_MARKET_DATE>/pre-market-context.json`

Agent report outputs:

- `report/<PRE_MARKET_DATE>/exec-brief.md`
- `report/<PRE_MARKET_DATE>/pre-market.md`

Skip behavior:

- If `--skip-non-trading-day` is set and the report date is not a regular US trading day, return `status=skipped`.
- If the required source snapshot is missing, return `status=failed` with the missing path in `reason`.

## post-market-review

Purpose: prepare the completed-market snapshot that a post-market review agent will use.

Canonical command:

```bash
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day
```

Inputs:

- `config/watchlist.json`
- Twelve Data market data via `.env` or `TWELVE_DATA_API_KEY`
- Optional S&P 500 dynamic universe flags
- `knowledge/refined/`
- `agent/post_market_analysis_prompt.md`

Deterministic script outputs:

- `raw_data/<SNAPSHOT_DATE>/<INTERVAL>/<SYMBOL>.json`
- `report/<SNAPSHOT_DATE>/daily-snapshot.json`
- Optional `report/<SNAPSHOT_DATE>/candidate-universe.json`

Agent report output:

- `report/<SNAPSHOT_DATE>/post-market.md`

Skip behavior:

- If `--skip-non-trading-day` is set and the snapshot date is not a regular US trading day, return `status=skipped`.
- Per-symbol fetch failures should be recorded in the snapshot `errors` array instead of aborting the whole snapshot when possible.

## monitor-brief

Purpose: scan the active monitor state and produce a structured intraday observation artifact.

Canonical command:

```bash
python3 script/trading_copilot.py monitor-brief --state config/monitor_state.json --interval 5min
```

Inputs:

- `config/monitor_state.json` if present, otherwise the script default state
- Twelve Data intraday data via `.env` or `TWELVE_DATA_API_KEY`
- `knowledge/refined/`

Deterministic output:

- `report/latest-monitor.json`

Agent summary output:

- A concise monitor brief in the response or a user-requested report file.

Output rules:

- Treat statuses as observation states, not trade instructions.
- Include data freshness and rule limitations.
- Use `NO TRADE` when setup quality, data quality, or risk framing is insufficient.

## validate-report

Purpose: enforce quality gates on generated markdown reports before delivery or Longbridge watchlist sync.

Canonical commands:

```bash
python3 script/trading_copilot.py validate-report --session pre-market --date <PRE_MARKET_DATE>
python3 script/trading_copilot.py validate-report --session post-market --date <SNAPSHOT_DATE>
```

Inputs:

- Generated markdown reports under `report/<DATE>/`.
- `knowledge/refined/setups/` for setup filename validation.
- `pre-market-context.json` or `daily-snapshot.json` when available for stale-data checks.

Output:

- JSON envelope with `status`, `workflow`, `date`, `artifacts`, and `validation`.
- `validation.status` is `pass` or `fail`.
- `validation.errors` contains blocking quality issues.
- `validation.warnings` contains non-blocking wording or disclosure concerns.

Required behavior:

- A failed validation must stop delivery and Longbridge sync.
- `sync-longbridge-watchlist --require-validation` must run this gate before extracting and syncing report focus symbols.

## extract-report-signals

Purpose: extract the focused report candidates into structured journal records so later reviews can compare planned setups with outcomes.

Canonical commands:

```bash
python3 script/trading_copilot.py extract-report-signals --session pre-market --date <PRE_MARKET_DATE> --require-validation --append
python3 script/trading_copilot.py extract-report-signals --session post-market --date <SNAPSHOT_DATE> --require-validation --append
```

Inputs:

- Pre-market default: `report/<DATE>/exec-brief.md`.
- Post-market default: `report/<DATE>/post-market.md`.
- Optional `--report` path for ad-hoc extraction.

Output:

- JSON envelope with extracted `signals`.
- With `--append`, writes new records to `runtime/journal/signals.jsonl`.

Required behavior:

- Extract at most 3 focused candidates by default.
- Prefer explicit focus lists such as `今日最多3个重点标的` and `明日观察清单`.
- Include `signal_id`, `symbol`, `setup`, `setup_files`, `trigger`, `invalidation`, `risk`, `status`, and `source_report` when available.
- Repeated extraction of the same source report should skip duplicate `signal_id` records.

## backfill-signal-outcomes

Purpose: compare previously planned signals with a completed daily snapshot and append objective outcome records for review statistics.

Canonical command:

```bash
python3 script/trading_copilot.py backfill-signal-outcomes --date <SNAPSHOT_DATE> --append
```

Inputs:

- `runtime/journal/signals.jsonl`.
- `report/<DATE>/daily-snapshot.json`.

Output:

- JSON envelope with `outcomes`, `summary`, and optional appended outcome ids.
- With `--append`, writes new records to `runtime/journal/outcomes.jsonl`.

Required behavior:

- Pre-market signals target the same date as the signal.
- Post-market signals target the next regular trading day after the signal date.
- Outcome statuses are observational: `triggered`, `invalidated`, `triggered_and_invalidated`, `not_triggered`, `not_evaluable`, or `no_data`.
- Daily bars cannot determine intraday order; if both trigger and invalidation are touched, use `triggered_and_invalidated`.
- This is research feedback only and must not imply a trade was entered.

## symbol-analysis

Purpose: perform an ad-hoc review of a single symbol.

Inputs:

- A symbol.
- A date or clearly stated analysis timestamp.
- The latest relevant snapshot/context artifact.
- Optional user-supplied position or thesis.
- `knowledge/refined/`.

Outputs:

- A symbol memo, either in the response or under `report/<DATE>/symbol-<SYMBOL>.md` when the user asks for a file.

Required sections:

- Data basis.
- Market context.
- Setup candidates.
- Bull/base/bear scenarios.
- Triggers and invalidation.
- Risk notes.
- `NO TRADE` when no rule-backed setup exists.

Boundary:

- Do not analyze current/recent prices without first preparing or reading real market data.

## research-note

Purpose: turn source material, market observations, or user questions into a reusable research note.

Inputs:

- User-provided topic or source path.
- Optional `knowledge/source/` material.
- `knowledge/refined/` for rule consistency checks.

Outputs:

- A research memo in the response or a user-requested file.

Boundary:

- Research notes do not automatically promote new trading rules.
- Rule promotion requires an explicit review task and should update `knowledge/refined/` only after checking conflicts.

## rule-check

Purpose: validate a user thesis, report section, or setup idea against the approved rule base.

Inputs:

- User-supplied thesis or artifact path.
- `knowledge/refined/global/`.
- Relevant `knowledge/refined/setups/` files.

Outputs:

- `pass`, `fail`, or `unclear` by rule area.
- Missing-data and missing-rule notes.
- Concrete edits needed to make the thesis compliant.

Boundary:

- Do not invent missing setup rules.
- Prefer `NO TRADE` when a thesis depends on unresolved rule conflicts.
