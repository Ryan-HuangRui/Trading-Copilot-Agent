# Trading Copilot Workflow Contracts

This repository is a trading research workflow package for Codex/Claude/OpenClaw-style agents. It is not a broker, execution engine, or standalone trading product.

All workflows must preserve the repository safety rules:

- Never place real trades.
- Read-only Longbridge real-account snapshots are allowed only through the account snapshot workflow; real-account order placement, cancellation, replacement, and automatic position changes are prohibited.
- Paper broker writes are allowed only through dedicated guarded paper adapters, only against `lb_papertrading`, and only for explicitly contracted operations.
- Current paper write scope is limited to guarded entry limit-buy submission, guarded cancellation of expired unfilled entry orders, guarded protective stop submission, and guarded TP1 partial-exit submission. Paper replace, break-even stop movement, trailing stops, OCO, market orders, short selling, and real-account writes remain out of scope.
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

## paper-account-snapshot

Purpose: write a read-only Longbridge paper account snapshot for execution-readiness checks and later paper-trade review.

Long-term paper execution milestones are tracked in `docs/paper-execution-roadmap.md`. This contract section only describes currently implemented workflows.

Canonical command:

```bash
python3 script/trading_copilot.py paper-account-snapshot --date <DATE>
```

Inputs:

- Longbridge CLI `auth status`, `assets`, `positions`, today's `order` list, and `order executions`.
- The workflow must verify `account_channel=lb_papertrading` before reading paper orders/executions.
- Optional `--input` JSON fixture for tests.

Output:

- `runtime/paper/<DATE>/paper-account-snapshot.json`

Required behavior:

- Must be read-only.
- Must reject non-paper Longbridge accounts.
- Must not submit, cancel, replace, or modify orders.
- The snapshot is an ignored runtime artifact and should not be committed.

## paper-trade-preview

Purpose: convert validated structured Trade Plan Cards into dry-run Longbridge paper order previews.

Canonical command:

```bash
python3 script/trading_copilot.py paper-trade-preview --date <DATE> --session pre-market --require-validation
```

Inputs:

- `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json`.
- `runtime/paper/<DATE>/paper-account-snapshot.json`.
- `knowledge/refined/setups/` through `validate-trade-plan`.

Output:

- `report/<DATE>/paper-trade-preview.json`

Required behavior:

- Default is dry-run only.
- With `--require-validation`, a complete `conditional_executable` Trade Plan Card is required.
- Quantity is computed from paper account net liquidation, `risk.max_account_risk_pct`, `risk.risk_per_share`, and available cash.
- The output may include preview CLI commands for human/manual use, but the workflow must not execute them.
- Unsupported directions or incomplete risk data must produce blocked previews, not orders.

## paper-trade-review

Purpose: compare dry-run paper order previews with observed Longbridge paper executions and append matched fills to the local journal.

Canonical command:

```bash
python3 script/trading_copilot.py paper-trade-review --date <DATE> --session pre-market --append
```

Inputs:

- `report/<DATE>/paper-trade-preview.json`.
- `runtime/paper/<DATE>/paper-account-snapshot.json`.
- Optional `runtime/paper/<DATE>/paper-orders.jsonl` for precise submitted-order matching.
- Optional `runtime/journal/trades.jsonl` for duplicate detection.

Output:

- `report/<DATE>/paper-trade-review.json`
- With `--append`, matched paper executions are appended to `runtime/journal/trades.jsonl`.

Required behavior:

- Must not infer an execution when no paper fill is observed.
- Must append only matched paper fills, keyed by `source_signal_id` and paper order id.
- When `paper-orders.jsonl` exists, matching must prefer `broker_order_id`, then `remark`, then `intent_id`, and use symbol-side fallback only for compatibility.
- Appended paper trade records should include `mode`, `intent_id`, `broker_order_id`, `planned_entry`, `entry`, `stop`, `take_profit`, `paper_quantity`, `paper_side`, and `slippage_pct` when available.
- Must keep paper execution feedback separate from plan quality and refined trading rules.

## paper-order-sync

Purpose: sync submitted paper order records with the latest Longbridge paper account snapshot.

Canonical command:

```bash
python3 script/trading_copilot.py paper-order-sync --date <DATE>
```

Inputs:

- `runtime/paper/<DATE>/paper-orders.jsonl`.
- Optional `runtime/paper/<DATE>/paper-stop-orders.jsonl`.
- Optional `runtime/paper/<DATE>/paper-take-profit-orders.jsonl`.
- `runtime/paper/<DATE>/paper-account-snapshot.json`.

Output:

- `runtime/paper/<DATE>/paper-execution-state.json`

Required behavior:

- Must be read-only; it must not submit, cancel, replace, or adjust orders.
- Must match submitted entry, protective stop, and TP1 orders by `broker_order_id`, then `remark`, then `intent_id`, then `symbol + side + quantity` fallback.
- Must summarize order states including `submitted`, `accepted`, `partially_filled`, `filled`, `cancelled`, `rejected`, and `expired`.
- Must include `protective_stops`, `take_profit_orders`, and `exit_summary` in the execution state when those journals exist.
- Must enrich entry orders with matched stop/TP1 fields such as `protective_stop_order_id`, `stop_status`, `take_profit_order_id`, `tp1_status`, `tp1_filled_quantity`, and `remaining_quantity`.
- Must preserve matched broker order and execution payloads for audit and later review.

## paper-order-cancel

Purpose: build a dry-run cancel plan for expired unfilled paper entry orders.

Canonical command:

```bash
python3 script/trading_copilot.py paper-order-cancel --date <DATE>
```

Execute command:

```bash
TRADING_COPILOT_PAPER_EXECUTION=enabled \
python3 script/trading_copilot.py paper-order-cancel --date <DATE> --execute
```

Inputs:

- `runtime/paper/<DATE>/paper-execution-state.json`.

Output:

- `report/<DATE>/paper-order-cancel-plan.json`

Required behavior:

- Default behavior is dry-run and must not call broker cancel APIs.
- Broker cancellation requires both `--execute` and `TRADING_COPILOT_PAPER_EXECUTION=enabled`.
- Broker cancellation must use only `script/longbridge_paper_order_adapter.py`.
- Only unfilled `submitted` or `accepted` entry orders with `broker_order_id` and `submitted_at` may become cancel candidates.
- Filled and partially filled orders must be blocked from cancellation planning.
- The artifact must separate `cancel_candidates`, `blocked`, `executed`, and `errors`.
- Executed cancel records must preserve `intent_id`, `broker_order_id`, `raw_request`, and `raw_response`.

## paper-event-ledger

Purpose: project submitted and observed paper execution facts into the unified event stream.

Canonical command:

```bash
python3 script/trading_copilot.py paper-event-ledger --date <DATE>
```

Inputs:

- `runtime/paper/<DATE>/paper-orders.jsonl`.
- Optional `runtime/paper/<DATE>/paper-stop-orders.jsonl`.
- Optional `runtime/paper/<DATE>/paper-take-profit-orders.jsonl`.
- Optional `runtime/paper/<DATE>/paper-execution-state.json`.

Output:

- `runtime/journal/events.jsonl`
- `report/<DATE>/paper-event-ledger.json`

Required behavior:

- Must be read-only with respect to broker APIs; it must not submit, cancel, replace, or adjust orders.
- Must emit deterministic event ids so repeated runs for the same date replace the same workflow/date projection without duplicate events.
- Must preserve existing events from other workflows or dates.
- Must emit at least submitted events from paper journals and observed status events from `paper-execution-state.json` when available.
- Event payloads must preserve `intent_id`, `source_signal_id`, `broker_order_id`, `symbol`, `side`, `quantity`, `remark`, and raw request/response fields when present.

## paper-execution-review

Purpose: analyze individual paper orders and trades from synced execution state.

Canonical command:

```bash
python3 script/trading_copilot.py paper-execution-review --date <DATE>
```

Inputs:

- `report/<DATE>/paper-trade-preview.json`.
- `runtime/paper/<DATE>/paper-execution-state.json`.

Output:

- `report/<DATE>/paper-execution-review.json`
- `report/<DATE>/paper-execution-review.md`

Required behavior:

- Must be review-only; it must not submit, cancel, replace, sync broker state, or modify refined rules.
- Must report plan adherence, slippage, fill quality, risk discipline, planned RR, realized/result R when exit evidence exists, and unavailable MFE/MAE when intraday path data is missing.
- Result R must use actual entry-to-exit P/L divided by planned initial risk per share.
- Candidate lessons may be emitted as review observations, but they must not be promoted into `knowledge/refined/`.

## paper-strategy-review

Purpose: aggregate paper execution evidence by setup and symbol.

Canonical command:

```bash
python3 script/trading_copilot.py paper-strategy-review
```

Inputs:

- `report/<DATE>/paper-execution-review.json` files, discovered automatically or passed with repeated `--review`.

Output:

- `report/strategy/paper-strategy-review.json`
- `report/strategy/paper-strategy-review.md`

Required behavior:

- Must be aggregate review only; it must not submit orders, sync broker state, or modify refined rules.
- Must aggregate by setup and symbol with planned count, submitted count, filled count, cancelled/expired count, average R, median R, win rate, average slippage, false-trigger rate, and no-fill-then-win rate where evidence exists.
- Must preserve source review paths and keep unavailable metrics as `null` rather than inventing values.

## paper-trade-submit

Purpose: prepare controlled paper order submissions from validated dry-run order previews.

Canonical command:

```bash
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation
```

Execute command:

```bash
TRADING_COPILOT_PAPER_EXECUTION=enabled \
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation --execute
```

Inputs:

- `report/<DATE>/paper-trade-preview.json`.
- `runtime/paper/<DATE>/paper-account-snapshot.json`.
- Optional `runtime/paper/<DATE>/paper-orders.jsonl` for duplicate detection.
- `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json` when `--require-validation` is used.

Output:

- `report/<DATE>/paper-trade-submission.json`
- `runtime/paper/<DATE>/paper-orders.jsonl` only when `--execute` successfully submits an order.

Required behavior:

- Default behavior is dry-run and must not call broker write APIs.
- Broker submission requires both `--execute` and `TRADING_COPILOT_PAPER_EXECUTION=enabled`.
- Broker submission must use only `script/longbridge_paper_order_adapter.py`.
- `--require-validation` must run `validate-trade-plan` for the session sidecar.
- Only `status=ready` long buy limit order intents may pass the risk guard.
- Account snapshot `account_channel` must be `lb_papertrading`.
- Duplicate `intent_id` values already present in `paper-orders.jsonl` must be skipped.
- The submission artifact must separate `ready`, `submitted`, `blocked`, `skipped_duplicates`, and `errors`.
- Successful submit records must preserve `intent_id`, `broker_order_id`, `remark`, `raw_request`, `raw_response`, and `submitted_at`.

## paper-protective-stop-plan

Purpose: build a protective stop plan for filled long paper entries, and optionally submit those stops to the Longbridge paper account through the guarded paper adapter.

Canonical command:

```bash
python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE>
```

Execution command:

```bash
TRADING_COPILOT_PAPER_EXECUTION=enabled \
python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE> --execute
```

Inputs:

- `runtime/paper/<DATE>/paper-execution-state.json`.
- Optional `runtime/paper/<DATE>/paper-stop-orders.jsonl` for duplicate detection.

Output:

- `report/<DATE>/paper-protective-stop-plan.json`
- `runtime/paper/<DATE>/paper-stop-orders.jsonl` only when `--execute` successfully submits a protective stop.

Required behavior:

- Default behavior is dry-run and must not call broker write APIs.
- Broker stop submission requires both `--execute` and `TRADING_COPILOT_PAPER_EXECUTION=enabled`.
- Broker stop submission must use only `script/longbridge_paper_order_adapter.py`.
- Only fully filled long buy entries with positive `stop_price`, positive filled quantity, and no existing protective stop may become stop candidates.
- The first stop plan uses Longbridge `sell` `MIT` with `--trigger-price <stop_price>` and `tif=gtc` by default.
- Duplicate `intent_id` values already present in `paper-stop-orders.jsonl` must be blocked.
- The artifact must separate `stop_candidates`, `blocked`, `submitted`, and `errors`.
- Successful stop records must preserve `intent_id`, `entry_broker_order_id`, `broker_order_id`, `remark`, `raw_request`, `raw_response`, and `submitted_at`.

## paper-take-profit-plan

Purpose: build a TP1 partial-exit plan for filled long paper entries, and optionally submit those take-profit orders to the Longbridge paper account through the guarded paper adapter.

Canonical command:

```bash
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE>
```

Execution command:

```bash
TRADING_COPILOT_PAPER_EXECUTION=enabled \
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE> --execute
```

Inputs:

- `runtime/paper/<DATE>/paper-execution-state.json`.
- Optional `runtime/paper/<DATE>/paper-take-profit-orders.jsonl` for duplicate detection.

Output:

- `report/<DATE>/paper-take-profit-plan.json`
- `runtime/paper/<DATE>/paper-take-profit-orders.jsonl` only when `--execute` successfully submits a TP1 order.

Required behavior:

- Default behavior is dry-run and must not call broker write APIs.
- Broker TP1 submission requires both `--execute` and `TRADING_COPILOT_PAPER_EXECUTION=enabled`.
- Broker TP1 submission must use only `script/longbridge_paper_order_adapter.py`.
- Only fully filled long buy entries with positive `take_profit`, positive filled quantity, and no existing TP1/take-profit order may become TP1 candidates.
- The first TP1 plan uses Longbridge `sell` `LO` with `--price <take_profit>`, `tif=gtc` by default, and a default `--exit-fraction 0.5`.
- Duplicate `intent_id` values already present in `paper-take-profit-orders.jsonl` must be blocked.
- The artifact must separate `take_profit_candidates`, `blocked`, `submitted`, and `errors`.
- Successful TP1 records must preserve `intent_id`, `entry_broker_order_id`, `broker_order_id`, `remark`, `raw_request`, `raw_response`, `exit_fraction`, and `submitted_at`.

## paper-break-even-stop-plan

Purpose: build a dry-run plan to move an existing protective stop to break-even after TP1 fill evidence exists.

Canonical command:

```bash
python3 script/trading_copilot.py paper-break-even-stop-plan --date <DATE>
```

Inputs:

- `runtime/paper/<DATE>/paper-execution-state.json`.
- `runtime/paper/<DATE>/paper-stop-orders.jsonl` when the state file does not already include a protective stop order id.

Output:

- `report/<DATE>/paper-break-even-stop-plan.json`

Required behavior:

- This workflow is dry-run only and must not cancel, replace, or submit broker orders.
- Only fully filled long buy entries with TP1 fill evidence, a positive remaining quantity, an existing protective stop order id, and a break-even price may become move candidates.
- TP1 fill evidence may come from `tp1_status=filled`, `take_profit_status=filled`, or positive `tp1_filled_quantity` / `take_profit_filled_quantity` in the execution state.
- Break-even price is based on `avg_fill_price`, falling back to entry/limit price, with optional non-negative `--buffer-pct`.
- Candidates must include the existing stop order id, remaining quantity, new trigger price, and preview steps for canceling the old stop and submitting a replacement `sell MIT`.
- Because cancel/replace safety needs separate execution design, there is no `--execute` mode for this workflow.

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
