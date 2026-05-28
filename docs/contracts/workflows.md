# Trading Copilot Workflow Contracts

This repository is a trading research workflow package for Codex/Claude/OpenClaw-style agents. It is not a broker, execution engine, or standalone trading product.

All workflows must preserve the repository safety rules:

- Never place real trades or call broker APIs.
- Read-only Longbridge account snapshots are allowed only through the account snapshot workflow; order placement, cancellation, replacement, and automatic position changes are prohibited.
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
- `report/<PRE_MARKET_DATE>/pre-market-signals.json`

Skip behavior:

- If `--skip-non-trading-day` is set and the report date is not a regular US trading day, return `status=skipped`.
- If the required source snapshot is missing, return `status=failed` with the missing path in `reason`.

## post-market-review

Purpose: prepare the completed-market snapshot that a post-market review agent will use.

Canonical command:

```bash
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols
```

Inputs:

- `config/watchlist.json`
- Longbridge CLI market data by default, with Twelve Data fallback via `.env` or `TWELVE_DATA_API_KEY`
- Optional S&P 500 dynamic universe flags
- Optional journal signal and position-symbol merge flags for outcome/position coverage
- `knowledge/refined/`
- `agent/post_market_analysis_prompt.md`

Deterministic script outputs:

- `raw_data/<SNAPSHOT_DATE>/<INTERVAL>/<SYMBOL>.json`
- `report/<SNAPSHOT_DATE>/daily-snapshot.json`
- Optional `report/<SNAPSHOT_DATE>/candidate-universe.json`

Agent report output:

- `report/<SNAPSHOT_DATE>/post-market.md`
- `report/<SNAPSHOT_DATE>/post-market-signals.json`

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
- Longbridge CLI intraday K-line data by default, with Twelve Data fallback via `.env` or `TWELVE_DATA_API_KEY`
- `knowledge/refined/`

Deterministic output:

- `report/latest-monitor.json`

Agent summary output:

- A concise monitor brief in the response or a user-requested report file.
- Optional journal extraction through `extract-monitor-signals`.

Output rules:

- Treat statuses as observation states, not trade instructions.
- Include data freshness and rule limitations.
- Use `NO TRADE` when setup quality, data quality, or risk framing is insufficient.

## data-quality

Purpose: generate market-data quality artifacts for the completed daily snapshot.

Canonical command:

```bash
python3 script/trading_copilot.py data-quality --date <DATE>
```

Inputs:

- `report/<DATE>/daily-snapshot.json`
- Optional `report/<DATE>/pre-market-signals.json` and `report/<DATE>/post-market-signals.json`
- Optional `runtime/account/<DATE>/account-snapshot.json`

Output:

- `report/<DATE>/data-quality.json`
- `report/<DATE>/data-quality.md`

Required behavior:

- Check `stale_data`, `latest_bar_dates`, snapshot errors, provider distribution, and abnormal single-day moves.
- Flag focused symbols that are missing from the snapshot.
- Flag focused symbols using fallback data, including `fallback_from` and `primary_error`.
- Flag account last-price vs snapshot close deltas above the configured threshold.
- Feishu summaries should disclose focused-symbol fallback and quality warnings when the artifact exists.

## validate-report

Purpose: enforce quality gates on generated report artifacts before delivery or Longbridge watchlist sync.

Canonical commands:

```bash
python3 script/trading_copilot.py validate-report --session pre-market --date <PRE_MARKET_DATE>
python3 script/trading_copilot.py validate-report --session post-market --date <SNAPSHOT_DATE>
```

Inputs:

- Generated markdown reports under `report/<DATE>/`.
- Structured `report/<DATE>/<SESSION>-signals.json` sidecar when validating a full session.
- `knowledge/refined/setups/` for setup filename validation.
- `pre-market-context.json` or `daily-snapshot.json` when available for stale-data checks.

Output:

- JSON envelope with `status`, `workflow`, `date`, `artifacts`, and `validation`.
- `validation.status` is `pass` or `fail`.
- `validation.errors` contains blocking quality issues.
- `validation.warnings` contains non-blocking wording or disclosure concerns.
- `validation.checked_artifacts` includes markdown reports and the session signal sidecar when present.

Required behavior:

- A failed validation must stop delivery and Longbridge sync.
- `extract-report-signals --require-validation` and `sync-longbridge-watchlist --require-validation` must run this gate before journal append or watchlist sync.
- Full-session validation requires `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json`. Single-report validation through `--report` keeps sidecar validation optional for ad-hoc checks.
- Markdown focus symbols must match the symbols in the session signal sidecar.

## validate-trade-plan

Purpose: enforce structured Trade Plan Card gates on the session sidecar without requiring Markdown report validation.

Canonical commands:

```bash
python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <PRE_MARKET_DATE>
python3 script/trading_copilot.py validate-trade-plan --session post-market --date <SNAPSHOT_DATE>
```

Inputs:

- `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json`.
- `knowledge/refined/setups/` for setup filename validation.

Required behavior:

- `execution_status=conditional_executable` requires a complete Trade Plan Card: `entry.trigger_price`, `stop.initial_stop`, `take_profit.tp1`, `risk.max_account_risk_pct`, `risk.risk_per_share`, and at least one `execution_rules.skip_conditions` item.
- TP1 reward/risk must be at least 2R.
- Incomplete plans must be downgraded by the agent to `watch_only` or `no_trade` before delivery.
- `extract-report-signals --require-validation` and `sync-longbridge-watchlist --require-validation` must run this gate as well as `validate-report`.
- With `--require-validation`, a missing session sidecar is blocking even when `--report` points to a valid Markdown file.

## extract-report-signals

Purpose: extract the focused report candidates into structured journal records so later reviews can compare planned setups with outcomes.

Canonical commands:

```bash
python3 script/trading_copilot.py extract-report-signals --session pre-market --date <PRE_MARKET_DATE> --require-validation --append
python3 script/trading_copilot.py extract-report-signals --session post-market --date <SNAPSHOT_DATE> --require-validation --append
```

Inputs:

- Preferred structured input: `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json`.
- Markdown fallback for pre-market: `report/<DATE>/exec-brief.md`.
- Markdown fallback for post-market: `report/<DATE>/post-market.md`.
- Optional `--report` path for ad-hoc extraction.
- Optional `--signals` path for ad-hoc structured extraction.

Output:

- JSON envelope with extracted `signals`.
- With `--append`, writes new records to `runtime/journal/signals.jsonl`.

Required behavior:

- Prefer the session signal sidecar over Markdown parsing.
- With `--require-validation`, run both `validate-report` and `validate-trade-plan` before extracting. Any failure must stop journal append.
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

## plan-review

Purpose: review the generated trade plans, compare them with outcome/trade records, and record candidate learning lessons.

Canonical command:

```bash
python3 script/trading_copilot.py plan-review --date <DATE> --append-lessons
```

Inputs:

- `runtime/journal/signals.jsonl`
- `runtime/journal/outcomes.jsonl`
- Optional `runtime/journal/trades.jsonl`
- Optional `runtime/journal/position_reviews.jsonl`

Outputs:

- `report/<DATE>/plan-review.md`
- `report/<DATE>/plan-review.json`
- With `--append-lessons`, `runtime/learning/daily_lessons.jsonl`

Required behavior:

- Review the plan, not broad market commentary.
- Separate plan quality, price touch outcome, and real execution.
- Include position discipline when position reviews exist: planned symbols without trade records, positions outside the plan, missing trade links, missing `source_signal_id`, and positions near invalidation without complete trade linkage.
- Lessons are candidate process improvements only; they must not mutate `knowledge/refined/`.

## learning-review

Purpose: aggregate repeated `daily_lessons.jsonl` entries into candidate process patterns for human review.

Canonical command:

```bash
python3 script/trading_copilot.py learning-review --lookback-days 20 --min-count 3
```

Inputs:

- `runtime/learning/daily_lessons.jsonl`
- `runtime/journal/outcomes.jsonl`
- `runtime/journal/trades.jsonl`
- `runtime/journal/position_reviews.jsonl`

Outputs:

- `runtime/learning/pattern_candidates.jsonl`
- `report/learning/pattern-review.md`
- `report/learning/pattern-review.json`

Required behavior:

- Group repeated lessons by problem, setup, and lesson type.
- Enrich lesson evidence with matching outcome/trade context and synthesize position-discipline learning events from repeated position review records.
- Only emit candidates that meet the repeat threshold.
- Mark emitted candidates as `promotion_status=needs_human_review`.
- Do not mutate `knowledge/refined/`.

## feishu-summary

Purpose: build a concise Feishu-ready execution panel from validated sidecars and review artifacts.

Canonical command:

```bash
python3 script/trading_copilot.py feishu-summary --session pre-market --date <DATE>
python3 script/trading_copilot.py feishu-summary --session post-market --date <DATE>
```

Inputs:

- `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json`
- Optional `report/<DATE>/position-review.json`
- Optional `report/<DATE>/plan-review.json`
- Optional `runtime/learning/daily_lessons.jsonl`

Output:

- `report/<DATE>/feishu-summary.md`

Required behavior:

- Show only a compact execution panel: conditional plans, watch candidates, `NO TRADE`, position review summary, plan review summary, and daily lessons.
- Keep the full analysis in the Markdown report artifacts; Feishu content should stay summary-first.
- Do not present conditional plans as deterministic buy/sell instructions.

## promote-lesson

Purpose: promote one repeated pattern candidate into prompt-readable validated lessons after human review.

Canonical commands:

```bash
python3 script/trading_copilot.py promote-lesson --pattern-id <PATTERN_ID> --dry-run
python3 script/trading_copilot.py promote-lesson --pattern-id <PATTERN_ID> --apply
```

Inputs:

- `runtime/learning/pattern_candidates.jsonl`

Output:

- `knowledge/evolution/validated_lessons.md`

Required behavior:

- `--dry-run` must show the exact Markdown block without writing.
- `--apply` may append to `knowledge/evolution/validated_lessons.md`.
- Promotion is still process guidance only; it must not edit `knowledge/refined/`.

Required behavior for outcome backfill:

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

## daily-self-review

Purpose: generate a daily self-review after post-market validation and outcome backfill.

Canonical command:

```bash
python3 script/trading_copilot.py daily-self-review --date <SNAPSHOT_DATE> --append
```

Inputs:

- `runtime/journal/signals.jsonl`
- `runtime/journal/outcomes.jsonl`
- `runtime/journal/trades.jsonl`
- Optional `report/<DATE>/post-market.md`

Output:

- `report/<DATE>/self-review.md`
- With `--append`, a deduplicated daily review record in `runtime/journal/reviews.jsonl`.

Required behavior:

- Treat signal outcomes as objective price-touch observations, not true trade results.
- Use `trades.jsonl` only for actual execution review.
- Surface `not_evaluable`, `no_data`, and `triggered_and_invalidated` counts as follow-up items.

## weekly-review

Purpose: generate a weekly process review from the journal.

Canonical command:

```bash
python3 script/trading_copilot.py weekly-review --week <YYYY-Www> --append
```

Inputs:

- `runtime/journal/signals.jsonl`
- `runtime/journal/outcomes.jsonl`
- `runtime/journal/trades.jsonl`

Output:

- `report/weekly/<YYYY-Www>.md`
- With `--append`, a deduplicated weekly review record in `runtime/journal/reviews.jsonl`.

Required behavior:

- Summarize planned signals, outcome distribution, setup distribution, symbol distribution, and trade records.
- Do not convert outcome touch statistics into win rate unless trades contain actual `result_r`.
- Include position review counts when `runtime/journal/position_reviews.jsonl` exists.

## extract-monitor-signals

Purpose: append actionable monitor observations to the journal.

Canonical command:

```bash
python3 script/trading_copilot.py extract-monitor-signals --append
```

Inputs:

- `report/latest-monitor.json`

Output:

- With `--append`, writes observed monitor signals to `runtime/journal/signals.jsonl`.

Required behavior:

- Append only actionable observation statuses such as `可执行` and `临近触发`.
- Treat monitor entries as observations, not trade instructions.
- Prefer setup-backed fields emitted by `monitor_scan.py`, including `setup`, `setup_files`, `trigger_detail`, `invalidation_detail`, `risk_quality`, and `journal_appendable`.

## account-snapshot

Purpose: write a read-only account and position snapshot for later local review.

Canonical command:

```bash
python3 script/trading_copilot.py account-snapshot --date <DATE>
```

Inputs:

- Longbridge CLI read-only account/position commands, or `--input` JSON fixture for tests.

Output:

- `runtime/account/<DATE>/account-snapshot.json`

Required behavior:

- Must not place, cancel, replace, modify, or submit orders.
- Must reject non-read-only Longbridge CLI commands.
- The snapshot is an ignored runtime artifact and should not be committed.

## position-review

Purpose: compare current read-only positions against the structured daily plan.

Canonical command:

```bash
python3 script/trading_copilot.py position-review --date <DATE> --config config/position_review.json --append
```

Inputs:

- `runtime/account/<DATE>/account-snapshot.json`
- `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json`
- `config/position_review.json`
- Optional `runtime/journal/trades.jsonl` and `runtime/journal/signals.jsonl` for `source_signal_id` linkage.
- Optional `runtime/journal/position_reviews.jsonl` for duplicate detection.

Output:

- `report/<DATE>/position-review.md`
- `report/<DATE>/position-review.json`
- With `--append`, writes `runtime/journal/position_reviews.jsonl`.

Required behavior:

- Report whether each position appears in today's structured signals.
- Report concentration, distance to invalidation, and whether human review is required.
- Use configurable thresholds for close-to-invalidation and high concentration.
- Treat configured core holdings as review context, not as automatic exceptions to risk checks.
- Link positions to the latest same-symbol trade record when available, and prefer `source_signal_id` to recover the originating signal/setup.
- Estimate open-position R only from human-entered `entry/stop`; do not treat it as realized trade performance.
- Do not output deterministic buy/sell instructions or automatic adjustment actions.

## workflow-smoke-test

Purpose: run a fixture-based end-to-end loop without external data calls.

Canonical command:

```bash
python3 script/workflow_smoke_test.py --date <DATE> --week <YYYY-Www> --account-input path/to/account-fixture.json
```

Required behavior:

- Use existing fixture artifacts under `--repo-root`.
- Exercise validation, signal extraction, outcome backfill, optional account snapshot and position review, daily review, weekly review, and monitor extraction.
- Must not fetch market data or account data.

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
