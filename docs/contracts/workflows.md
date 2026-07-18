# Trading Copilot Workflow Contracts

This repository is a trading research workflow package for Codex/Claude/OpenClaw-style agents. It is not a broker, execution engine, or standalone trading product.

All workflows must preserve the repository safety rules:

- Never place real trades.
- Read-only Longbridge real-account snapshots are allowed only through the account snapshot workflow; real-account order placement, cancellation, replacement, and automatic position changes are prohibited.
- Paper broker writes are allowed only through dedicated guarded paper adapters, only against `lb_papertrading`, and only for explicitly contracted operations.
- Current paper write scope is limited to guarded paper entry submission, guarded cancellation of expired unfilled entry orders, guarded pending order quantity/limit replace, guarded protective stop submission, guarded TP1 partial-exit submission, guarded plan-invalidated exit submission, and guarded break-even stop movement. OCO, short selling, native stop-trigger replace, and real-account writes remain out of scope.
- Do not output deterministic buy/sell instructions.
- Use scenarios, triggers, invalidation, risk, and `NO TRADE`.
- Use `canonical rulebook/` as the only rule source for trading conclusions.
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
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day --include-agent-research
```

Inputs:

- Longbridge watchlist groups `持仓`, `ibkr持仓`, `老朋友`, `AI先进封装HBM`, and `AI Top 10 Research`; `config/watchlist.json` is refreshed from their full union before the context step.
- `config/watchlist.json`, used only as the fallback when Longbridge watchlist retrieval is unavailable.
- Prior completed trading day's `report/<SNAPSHOT_DATE>/daily-snapshot.json`
- `canonical rulebook/`
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
- With `--include-agent-research`, only the wrapper layer runs the independent agent research scripts and appends their artifact paths to `next_agent_inputs`.
- `prepare_daily_context.py` remains deterministic and must not import or call agent research modules.

## post-market-review

Purpose: prepare the completed-market snapshot that a post-market review agent will use.

Canonical command:

```bash
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-agent-research
```

Inputs:

- Longbridge watchlist groups `持仓`, `ibkr持仓`, `老朋友`, `AI先进封装HBM`, and `AI Top 10 Research`; `config/watchlist.json` is refreshed from their full union before snapshot generation.
- `config/watchlist.json`, used only as the fallback when Longbridge watchlist retrieval is unavailable.
- Longbridge CLI market data by default, with Twelve Data fallback via `.env` or `TWELVE_DATA_API_KEY`
- Optional S&P 500 dynamic universe flags
- Optional journal signal and position-symbol merge flags for outcome/position coverage
- `canonical rulebook/`
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
- With `--include-agent-research`, only the wrapper layer runs the independent agent research scripts and appends their artifact paths to `next_agent_inputs`.
- `prepare_market_snapshot.py` remains deterministic and must not import or call agent research modules.

## monitor-brief

Purpose: scan the active monitor state and produce a structured intraday observation artifact.

Canonical command:

```bash
python3 script/trading_copilot.py monitor-brief --state config/monitor_state.json --interval 5min
```

Inputs:

- `config/monitor_state.json` if present, otherwise the script default state
- Longbridge CLI intraday K-line data by default, with Twelve Data fallback via `.env` or `TWELVE_DATA_API_KEY`
- `canonical rulebook/`

Deterministic output:

- `report/latest-monitor.json`

Agent summary output:

- A concise monitor brief in the response or a user-requested report file.
- Optional journal extraction through `extract-monitor-signals`.

Output rules:

- Treat statuses as observation states, not trade instructions.
- Include data freshness and rule limitations.
- Use `NO TRADE` when setup quality, data quality, or risk framing is insufficient.

Monitor dry-run sidecar:

```bash
python3 script/trading_copilot.py extract-monitor-signals --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session monitor --date <DATE>
python3 script/trading_copilot.py paper-trade-preview --session monitor --date <DATE>
python3 script/trading_copilot.py paper-trade-submit --session monitor --date <DATE>
```

Required behavior:

- `extract-monitor-signals` writes `report/<DATE>/monitor-signals.json`.
- Monitor signals default to `plan_type=watch_only` and `execution_status=watch_only`.
- Monitor paper submission is dry-run only.
- `paper-trade-submit --session monitor --execute` must be hard-rejected before broker credentials or adapters are used.

## intraday-tracker

Purpose: track pre-market focus plans and manually watched symbols against the latest intraday monitor artifact.

Canonical command:

```bash
python3 script/trading_copilot.py intraday-tracker --date <DATE> --top-n 5
```

Inputs:

- `report/<DATE>/pre-market-signals.json`
- Optional `config/intraday_watchlist.json`
- `report/latest-monitor.json`
- Existing `report/<DATE>/intraday.md` when present
- Existing `runtime/intraday/<DATE>/state.json` and `events.jsonl` when present

Deterministic output:

- Appends `report/<DATE>/intraday.md`
- Writes `runtime/intraday/<DATE>/state.json`
- Appends important state changes to `runtime/intraday/<DATE>/events.jsonl`

Required behavior:

- Treat the Markdown file as a human-readable rolling log.
- Treat `state.json` as the machine-readable prior state source.
- Treat `events.jsonl` as notification candidates only.
- Do not submit, cancel, or replace broker orders from this workflow.

## intraday-dry-run

Purpose: run monitor-session paper preview/submission dry-run checks from either deterministic watch-only monitor extraction or a Codex-reviewed monitor sidecar.

Canonical command:

```bash
python3 script/trading_copilot.py intraday-dry-run --date <DATE>
python3 script/trading_copilot.py intraday-decision-coverage --date <DATE> --context report/<DATE>/intraday-opportunity-context.json --signals report/<DATE>/monitor-signals.json
python3 script/trading_copilot.py intraday-dry-run --date <DATE> --signals report/<DATE>/monitor-signals.json
python3 script/trading_copilot.py intraday-review-append --date <DATE> --signals report/<DATE>/monitor-signals.json --submission report/<DATE>/paper-trade-submission.json --context report/<DATE>/intraday-opportunity-context.json
```

Deterministic sequence:

1. If `--signals` is omitted, run `extract_monitor_signals.py` to create watch-only monitor signals.
2. If `--signals` is provided, use that sidecar directly and do not overwrite it.
3. `validate_trade_plan.py --session monitor`
4. `paper_trade_preview.py --session monitor --require-validation`
5. `paper_trade_submit.py --session monitor --require-validation`
6. `feishu_summary.py --session monitor`
7. `intraday_review_append.py` appends the Codex sidecar decision summary and dry-run counts to `report/<DATE>/intraday.md`

Required behavior:

- The wrapper must not pass `--execute` to any child command.
- `paper_trade_submit.py` remains dry-run for monitor session.
- A `conditional_executable` monitor sidecar must come from Codex/LLM review of `intraday-opportunity-context`; deterministic extraction must keep `watch_only`.
- Codex must treat `observation_scans` / `sidecar_template.signals` as the all-symbol decision input. Deterministic `candidate_scans` are highlights only and must not restrict LLM opportunity discovery.
- A Codex-reviewed sidecar must pass `intraday-decision-coverage` before trade-plan validation or dry-run so every observation symbol has an explicit `watch_only`, `no_trade`, or `conditional_executable` decision.
- After a Codex-reviewed sidecar exists, the daily intraday Markdown should include the review summary so each poll preserves why candidates stayed `watch_only`, became `no_trade`, or became `conditional_executable`.
- Output artifacts are review and notification inputs only.
- This workflow must not submit broker orders.

## intraday-decision-coverage

Purpose: verify that a Codex-reviewed monitor sidecar contains one explicit decision for every symbol in `intraday-opportunity-context`'s `sidecar_template.signals`.

Canonical command:

```bash
python3 script/trading_copilot.py intraday-decision-coverage --date <DATE> --context report/<DATE>/intraday-opportunity-context.json --signals report/<DATE>/monitor-signals.json
```

Required behavior:

- The validator must fail when any observation symbol is missing from `monitor-signals.json`.
- The validator does not judge trade quality, RR, setup validity, or broker readiness; those remain `validate-trade-plan`, `paper_trade_preview`, and risk guard responsibilities.
- This workflow must not submit, cancel, replace, or recover broker orders.

## intraday-opportunity-context

Purpose: build the fixed Codex review context for deciding, across the full intraday observation universe, whether each monitor observation remains `watch_only`, becomes `no_trade`, or becomes a complete `conditional_executable` monitor Trade Plan Card.

Canonical command:

```bash
python3 script/trading_copilot.py intraday-opportunity-context --date <DATE>
```

Inputs:

- `report/latest-monitor.json`
- `report/<DATE>/pre-market-signals.json` when present
- `runtime/intraday/<DATE>/state.json` when present
- `report/<DATE>/intraday.md` when present
- `runtime/paper/<DATE>/paper-execution-state.json` when present
- `canonical rulebook/setups/*.md`

Output:

- `report/<DATE>/intraday-opportunity-context.json`

Required behavior:

- The artifact must include `observation_scans` for every monitor scan selected for LLM review, including multi-timeframe `price_evidence` by default: 5m up to 78 bars, 15m 40 bars, daily 60 bars, key levels, and derived distances. It must also include `candidate_scans` for deterministic highlights, matching pre-market plans, intraday state, paper state summary, refined setup file names, and a `sidecar_template`.
- `sidecar_template.signals` must cover the full `observation_scans` universe and default to `plan_type=watch_only` and `execution_status=watch_only`.
- `candidate_scans` must not be used as a pre-filter for Codex decisions; it is supporting evidence only.
- Only Codex/LLM review may raise a signal to `plan_type=trade_plan` and `execution_status=conditional_executable`; validation still requires the complete Trade Plan Card and RR >= 2.
- This workflow must not submit, cancel, replace, or recover broker orders.

## intraday-paper-entry

Purpose: standalone gated paper-entry workflow for monitor-session candidates after the dry-run loop has been reviewed.

Canonical dry-run command:

```bash
python3 script/trading_copilot.py intraday-paper-entry --date <DATE> --require-validation
```

Canonical execute command:

```bash
python3 script/trading_copilot.py intraday-paper-entry --date <DATE> --require-validation --execute --paper-execution-config config/paper_execution.local.json
```

Required behavior:

- This is the only supported Phase 3 intraday paper-entry execute wrapper.
- It fixes the submission session to `monitor` and broker action to `intraday_entry_submit`.
- Execute requires `broker_writes_enabled=true`, `allow_intraday_entry_submit=true`, `--execute`, and the Longbridge paper account channel `lb_papertrading`.
- Plain `paper-trade-submit --session monitor --execute` remains hard-rejected.
- The workflow must write `report/<DATE>/intraday-paper-entry.json` by default.

## agent-research-context

Purpose: create the TradingAgents-style research context envelope used by later deterministic data tools and role prompts.

Canonical command:

```bash
python3 script/trading_copilot.py agent-research-context --date <DATE> --symbol MU
```

Output:

- `report/<DATE>/agents/research-context.json` by default, or the explicit `--output` path.

Required behavior:

- Return the shared status envelope.
- Normalize symbols to uppercase and deduplicate them.
- Phase 0 output must include `experimental=true` and `not_for_execution=true`.
- The artifact must not be injected into production `pre-market-plan` or `post-market-review` inputs until later phases add explicit `--include-agent-research`.

## agent-research-reports

Purpose: write placeholder or generated analyst report artifacts under the agent research path.

Canonical command:

```bash
python3 script/trading_copilot.py agent-research-reports --date <DATE> --symbol MU
```

Output:

- `report/<DATE>/agents/<SYMBOL>/market_report.json`
- `report/<DATE>/agents/<SYMBOL>/technicals_report.json`
- `report/<DATE>/agents/<SYMBOL>/fundamentals_report.json`
- `report/<DATE>/agents/<SYMBOL>/news_report.json`
- `report/<DATE>/agents/<SYMBOL>/sentiment_report.json`

Required behavior:

- Use `--placeholder` only for Phase 0 contract checks; placeholder artifacts must include `experimental=true` and `not_for_execution=true`.
- Without `--placeholder`, generate structured analyst reports from `agent_market_data.py`, `agent_technicals.py`, and fixture-only fundamentals/news/sentiment provider contracts.
- Reports are evidence artifacts only and must not contain broker order commands.

## validate-agent-reports

Purpose: validate structured agent research reports before role reasoning consumes them.

Canonical command:

```bash
python3 script/trading_copilot.py validate-agent-reports --date <DATE> --symbol MU
```

Required behavior:

- Check all five expected report files for each requested symbol.
- Fail when top-level report fields are missing.
- Fail when evidence items miss `source`, `source_type`, freshness, `symbol`, `summary`, `confidence`, or `limitations`.
- Fail when `market` or `technicals` evidence is empty.
- Warn, but do not fail, when `fundamentals`, `news`, or `sentiment` evidence is empty; downstream summaries must disclose the weaker evidence base.
- Fail when report artifacts contain broker/order command text.

## llm-generation-manifest

Purpose: record provenance for the LLM-authored report artifacts. This command does not call a model; it records what Codex/LLM already generated.

Canonical command:

```bash
python3 script/trading_copilot.py llm-generation-manifest --session pre-market --date <DATE> --model <MODEL> --prompt agent/daily_analysis_prompt.md --input report/<DATE>/pre-market-context.json --generated-output report/<DATE>/exec-brief.md --generated-output report/<DATE>/pre-market.md --generated-output report/<DATE>/pre-market-signals.json
```

Output:

- `report/<DATE>/<SESSION>-llm-generation.json`

Required behavior:

- Record model, runner, prompt path/hash, input path/hash, generated output path/hash, git SHA, branch, and dirty files.
- Preserve the safety boundary: LLM may propose analysis and conditional plans; deterministic scripts gate journal append, sync, paper preview, and delivery.

## focus-selection

Purpose: create an auditable explanation of why each report symbol became a focus candidate.

Canonical command:

```bash
python3 script/trading_copilot.py focus-selection --session pre-market --date <DATE>
```

Output:

- `report/<DATE>/focus-selection.json`

Required behavior:

- Read the structured sidecar for the requested session.
- Merge available agent decision fields such as `rank_score`, `momentum_score`, `risk_heat`, `setup_match`, `why_focus`, `why_not_executable`, and `required_intraday_confirmation`.
- Include selected symbols and a bounded list of non-focus universe symbols when context/snapshot data is present.

## pre-market-deliver / post-market-deliver

Purpose: deterministic delivery wrapper for report bundles already generated by Codex/LLM.

Canonical commands:

```bash
python3 script/trading_copilot.py pre-market-deliver --date <DATE> --sync-longbridge --execute-sync
python3 script/trading_copilot.py post-market-deliver --date <DATE> --sync-longbridge --execute-sync --append-outcomes --append-lessons --append-self-review
```

Outputs:

- `report/<DATE>/<SESSION>-run-manifest.json`
- `report/<DATE>/focus-selection.json`
- Post-market only: `report/<DATE>/workflow-review.json`
- Post-market only: `report/<DATE>/workflow-review.md`
- `report/<DATE>/feishu-summary.md`

Required behavior:

- Run validation before journal append or Longbridge sync.
- Run `data-quality` before `extract-report-signals`.
- Run `focus-selection` before journal append so the selection audit is included in the run manifest and Feishu summary.
- Post-market delivery should include learning-review by default after plan-review, with `--skip-learning-review` available for debugging or recovery.
- Post-market delivery should run `daily-workflow-review` before `feishu-summary`, with `--skip-workflow-review` available for debugging or recovery.
- Keep Longbridge sync optional and explicit. Pre-market defaults to additive sync; post-market defaults to replacement sync for `今日关注`.
- Real-account broker writes remain prohibited.

## inspect-pre-market-context

Purpose: expose the pre-market context schema as a stable summary instead of ad-hoc `jq`.

Canonical command:

```bash
python3 script/trading_copilot.py inspect-pre-market-context --date <DATE>
```

Required behavior:

- Return report date, source snapshot date/path, symbol count, symbol list, provider summary, latest bar dates, focused symbols when a sidecar exists, fallback symbols, missing symbols, stale-data state, and context errors.

## agent-decision

Purpose: write the final role-reasoning decision artifact.

Canonical command:

```bash
python3 script/trading_copilot.py agent-decision --date <DATE> --symbol MU
```

Output:

- `report/<DATE>/agents/<SYMBOL>/decision.json`
- `report/<DATE>/agents/<SYMBOL>/decision.md`

Required behavior:

- Use `--placeholder` only for Phase 0 contract checks. Placeholder `decision.json` uses `plan_type=no_trade`, `execution_status=no_trade`, `experimental=true`, and `not_for_execution=true`.
- Without `--placeholder`, generate Bull Researcher, Bear Researcher, Risk Manager, and Portfolio Manager artifacts from validated agent reports.
- The decision artifact must not include `order`, `broker_command`, `submit_order`, `cancel_order`, or `replace_order` fields.
- Later validation must reject placeholder decisions before any downstream use.

## validate-agent-decision

Purpose: validate role reports and final agent decisions before they are injected into daily workflows.

Canonical command:

```bash
python3 script/trading_copilot.py validate-agent-decision --date <DATE> --symbol MU
```

Required behavior:

- Reject placeholder decisions with `experimental=true` or `not_for_execution=true`.
- Require Bull and Bear reports to cite both supporting and opposing evidence.
- Require Risk Manager invalidation, data/liquidity limits, portfolio constraints, and risk summary.
- Enforce existing `plan_type` and `execution_status` enums.
- Reject incomplete `conditional_executable` trade plans and broker/order command fields.

## agent-memory-review

Purpose: read memory context for role reasoning without modifying approved trading rules or execution status.

Canonical command:

```bash
python3 script/trading_copilot.py agent-memory-review --date <DATE> --symbol MU
```

Required behavior:

- Phase 0 behavior is read-only and returns an empty `memory_matches` list.
- Memory may lower confidence or trigger review only; it must not raise execution readiness.

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
- `canonical rulebook/setups/` for setup filename validation.
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
- `canonical rulebook/setups/` for setup filename validation.

Required behavior:

- `execution_status=conditional_executable` requires a complete Trade Plan Card: `entry.trigger_price`, `stop.initial_stop`, `take_profit.tp1`, `risk.max_account_risk_pct`, `risk.risk_per_share`, and at least one `execution_rules.skip_conditions` item.
- Conditional entry order fields must pass the shared Longbridge paper order shape checks: supported `order_type`, required price/trigger/trailing fields, valid `tif`, required `expire_date` for `gtd`, and valid `outside_rth` when supplied.
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
- Lessons are candidate process improvements only; they must not mutate `canonical rulebook/`.

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
- Do not mutate `canonical rulebook/`.

## feishu-summary

Purpose: build a concise Feishu-ready analysis summary from validated sidecars and review artifacts.

Canonical command:

```bash
python3 script/trading_copilot.py feishu-summary --session pre-market --date <DATE>
python3 script/trading_copilot.py feishu-summary --session post-market --date <DATE>
```

Inputs:

- `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json`
- For post-market: `report/<DATE>/longbridge-market-context.json` for Longbridge read-only market index/indicator moves and industry/sector ETF proxy moves
- For post-market fallback only: `report/<DATE>/daily-snapshot.json` for observation-pool breadth and sector/industry aggregation when Longbridge market context is unavailable
- Optional `report/<DATE>/position-review.json`
- Optional `report/<DATE>/plan-review.json`
- Optional post-market intraday artifacts: `report/<DATE>/intraday.md`, `runtime/intraday/<DATE>/state.json`, `runtime/intraday/<DATE>/events.jsonl`, and `runtime/intraday/<DATE>/sent-events.json`
- Optional post-market workflow review: `report/<DATE>/workflow-review.json`
- Optional `runtime/learning/daily_lessons.jsonl`

Output:

- `report/<DATE>/feishu-summary.md`

Required behavior:

- Put analysis content first: conditional plans, watch candidates, `NO TRADE`, position review summary, plan review summary, and daily lessons.
- Keep execution bookkeeping out of the main body. Do not list generated artifacts, journal append counts, dirty files, LLM metadata, or full validation step logs in Feishu.
- For post-market summaries, include a `市场与行业` section before symbol-level analysis. Use `longbridge-market-context` as the primary source for major index/indicator moves and industry/sector ETF proxy strength. If Longbridge context is missing or failed, fall back to daily snapshot observation-pool breadth and clearly label it as watchlist/snapshot evidence rather than full-market coverage.
- For post-market summaries, include a compact intraday-monitor recap when artifacts exist: focus symbols, final state distribution, and latest state summary. Do not include sent-notification counts or artifact paths.
- For post-market summaries, include the same-day review conclusion when available: possible missed candidates, touch-fade/invalidated counts, not-triggered counts, and the conclusion.
- Keep a small trailing workflow check with workflow/date, validation status, data-quality status, and optional watchlist-sync status.
- Keep the full detailed report in the Markdown report artifacts; Feishu content should stay analysis-first.
- Do not present conditional plans as deterministic buy/sell instructions.

## longbridge-market-context

Purpose: fetch read-only Longbridge daily bars for broad-market and industry/sector proxy instruments before post-market Feishu delivery.

Canonical command:

```bash
python3 script/trading_copilot.py longbridge-market-context --date <DATE>
```

Output:

- `report/<DATE>/longbridge-market-context.json`

Required behavior:

- Use Longbridge read-only K-line data only; do not call broker trading/order APIs.
- Default market proxies: `SPY`, `QQQ`, `DIA`, `IWM`, `VIX`.
- Default industry/sector proxies: `XLK`, `XLC`, `XLY`, `XLP`, `XLF`, `XLV`, `XLI`, `XLE`, `XLU`, `XLB`, `XLRE`, `SMH`.
- Continue after per-symbol failures and record errors in the artifact. Post-market delivery may continue, but Feishu must disclose missing Longbridge context or error counts.

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
- Promotion is still process guidance only; it must not edit `canonical rulebook/`.

## paper-account-snapshot

Purpose: write a read-only Longbridge paper account snapshot for execution-readiness checks and later paper-trade review.

Long-term paper execution milestones are tracked in `docs/paper-execution-roadmap.md`. This contract section only describes currently implemented workflows.

Canonical command:

```bash
python3 script/trading_copilot.py paper-account-snapshot --date <DATE>
python3 script/trading_copilot.py paper-account-snapshot --date <DATE> --paper-execution-config config/paper_execution.local.json
```

Inputs:

- Longbridge CLI `auth status`, `assets`, `positions`, today's `order` list, and `order executions`.
- The workflow must verify `account_channel=lb_papertrading` before reading paper orders/executions.
- Optional `--paper-execution-config` may enable `allow_auth_status_unknown_paper_channel=true` only for hosts separately verified to use the paper account token. Explicit non-paper channels must still fail.
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
- `canonical rulebook/setups/` through `validate-trade-plan`.

Output:

- `report/<DATE>/paper-trade-preview.json`

Required behavior:

- Default is dry-run only.
- With `--require-validation`, a complete `conditional_executable` Trade Plan Card is required.
- Quantity is computed from paper account net liquidation, `risk.max_account_risk_pct`, `risk.risk_per_share`, and available cash.
- Entry order type comes from `entry.order_type` and supports the shared Longbridge paper order model: `LO`, `ELO`, `MO`, `AO`, `ALO`, `ODD`, `SLO`, `LIT`, `MIT`, `TSLPAMT`, and `TSLPPCT`.
- Preview must preserve entry-level `tif`, `expire_date` for `gtd`, and `outside_rth` when supplied. If `entry.tif` is absent, the command-level `--tif` default is used.
- Required order-shape fields must block incomplete previews: price for price-based orders, trigger price for trigger orders, trailing amount/percent for trailing orders, and `expire_date` for `gtd`.
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
- Must write `schema_version=paper-execution-state/v2`.
- Must include `broker_capabilities` so downstream schedulers and summaries can inspect supported, disabled, and unsupported Longbridge paper actions without reading local config.
- Must match submitted entry, protective stop, and TP1 orders by `broker_order_id`, then `remark`, then `intent_id`, then `symbol + side + quantity` fallback.
- Must summarize order states including `submitted`, `accepted`, `partially_filled`, `filled`, `cancelled`, `rejected`, and `expired`.
- Must include `protective_stops`, `take_profit_orders`, and `exit_summary` in the execution state when those journals exist.
- Must preserve order shape fields for entry/stop/TP1/exit records, including limit, trigger, trailing, `tif`, `expire_date`, and `outside_rth`.
- Must enrich entry orders with matched stop/TP1 fields such as `protective_stop_order_id`, `stop_status`, `take_profit_order_id`, `tp1_status`, `tp1_filled_quantity`, and `remaining_quantity`.
- Must enrich entry orders with a `lifecycle` summary including entry, protection, TP1, remaining quantity, and overall lifecycle status.
- Must preserve matched broker order and execution payloads for audit and later review.

## paper-order-recover

Purpose: recover a broker-submitted paper entry order into the local paper order journal when the broker accepted the order but local submission recording failed.

Canonical dry-run command:

```bash
python3 script/trading_copilot.py paper-order-recover --date <DATE> --session pre-market --broker-order-id <ORDER_ID>
```

Append command:

```bash
python3 script/trading_copilot.py paper-order-recover --date <DATE> --session pre-market --broker-order-id <ORDER_ID> --append
```

Inputs:

- `report/<DATE>/paper-trade-preview.json`.
- `runtime/paper/<DATE>/paper-account-snapshot.json` when available, which must show `account_channel=lb_papertrading`.
- Longbridge `order detail <ORDER_ID> --format json`, or a JSON fixture through `--order-detail`.
- Optional `runtime/paper/<DATE>/paper-orders.jsonl` for duplicate detection.

Output:

- `report/<DATE>/paper-order-recover.json`
- `runtime/paper/<DATE>/paper-orders.jsonl` only when `--append` is used and no duplicate exists.

Required behavior:

- Must not submit, cancel, replace, or adjust broker orders.
- Must only recover ready long-buy paper entry orders supported by the shared paper order model.
- Must match the broker order detail to exactly one ready preview order by symbol, side, quantity, order type, and the order-type-specific fields present in broker detail, including limit price, trigger price, trailing amount/percent, limit offset, `tif`, `expire_date`, and `outside_rth`.
- Must skip duplicate `intent_id` or `broker_order_id` values already present in `paper-orders.jsonl`.
- Recovered records must preserve `intent_id`, `broker_order_id`, `remark`, order shape fields, reconstructed `raw_request`, full broker `raw_response`, and recovery timestamp.

## paper-order-cancel

Purpose: build a dry-run cancel plan for expired unfilled paper entry orders.

Canonical command:

```bash
python3 script/trading_copilot.py paper-order-cancel --date <DATE>
```

Execute command:

```bash
python3 script/trading_copilot.py paper-order-cancel --date <DATE> --execute
```

Inputs:

- `runtime/paper/<DATE>/paper-execution-state.json`.
- `config/paper_execution.json` when `--execute` is used.

Output:

- `report/<DATE>/paper-order-cancel-plan.json`

Required behavior:

- Default behavior is dry-run and must not call broker cancel APIs.
- Broker cancellation requires both `--execute` and `config/paper_execution.json` with `paper_execution.broker_writes_enabled=true` and `paper_execution.allow_cancel=true`.
- Broker cancellation must use only `script/longbridge_paper_order_adapter.py`.
- Only unfilled `submitted` or `accepted` entry orders with `broker_order_id` and `submitted_at` may become cancel candidates.
- Filled and partially filled orders must be blocked from cancellation planning.
- The artifact must separate `cancel_candidates`, `blocked`, `executed`, and `errors`.
- The artifact must include `execution_policy` and `broker_capabilities`.
- Executed cancel records must preserve `intent_id`, `broker_order_id`, `raw_request`, and `raw_response`.

## paper-order-replace

Purpose: build a dry-run replace plan for pending paper entry orders when a Codex-reviewed sidecar requests a safer quantity or limit price, and optionally execute the replace through the guarded paper adapter.

Canonical command:

```bash
python3 script/trading_copilot.py paper-order-replace --date <DATE>
```

Execute command:

```bash
python3 script/trading_copilot.py paper-order-replace --date <DATE> --execute
```

Inputs:

- `runtime/paper/<DATE>/paper-execution-state.json`.
- `report/<DATE>/paper-replace-decisions.json`.
- Optional `runtime/paper/<DATE>/paper-replace-orders.jsonl` for duplicate detection.
- `config/paper_execution.json` when `--execute` is used.

Output:

- `report/<DATE>/paper-replace-plan.json`
- `runtime/paper/<DATE>/paper-replace-orders.jsonl` only when `--execute` successfully replaces a pending paper order.

Required behavior:

- Default behavior is dry-run and must not call broker replace APIs.
- Broker replace requires `--execute`, paper account validation, and config gates `broker_writes_enabled=true` plus `allow_order_replace=true`.
- Only pending/open/submitted paper orders with `filled_quantity=0`, a broker order id, and a matching `replace_pending` decision may become replace candidates.
- Replacement may reduce or keep quantity and may set a new positive limit price; it must not increase above current order quantity or reduce below filled quantity.
- The workflow must not replace filled or partially filled orders.
- This workflow is for Longbridge `order replace` quantity/price changes only. Stop trigger movement remains a cancel-and-submit workflow because MIT trigger prices cannot be safely changed through this replace path.
- Duplicate `intent_id` values already present in `paper-replace-orders.jsonl` must be blocked.
- The artifact must separate `replace_candidates`, `blocked`, `replaced`, and `errors`, and include `execution_policy` and `broker_capabilities`.
- Successful replace records must preserve `intent_id`, `broker_order_id`, previous and new quantity/limit price, raw request/response, account channel, and `submitted_at`.

Decision sidecar example:

```json
{
  "date": "<DATE>",
  "workflow": "paper-order-replace-decision",
  "decisions": [
    {
      "intent_id": "<INTENT_ID>",
      "symbol": "MU",
      "action": "replace_pending",
      "execution_status": "conditional_executable",
      "reason": "limit should be tightened after failed reclaim",
      "new_quantity": 100,
      "new_limit_price": 99.5,
      "risk_check": {
        "remaining_unfilled_quantity": 100,
        "max_account_risk_pct": 1,
        "risk_per_share": 4.5
      },
      "evidence": ["report/latest-monitor.json", "runtime/paper/<DATE>/paper-execution-state.json"]
    }
  ]
}
```

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
- Optional `runtime/paper/<DATE>/paper-exit-orders.jsonl`.
- Optional `runtime/paper/<DATE>/paper-replace-orders.jsonl`.
- Optional `report/<DATE>/paper-order-cancel-plan.json`.
- Optional `runtime/paper/<DATE>/paper-execution-state.json`.

Output:

- `runtime/journal/events.jsonl`
- `report/<DATE>/paper-event-ledger.json`

Required behavior:

- Must be read-only with respect to broker APIs; it must not submit, cancel, replace, or adjust orders.
- Must emit deterministic event ids so repeated runs for the same date replace the same workflow/date projection without duplicate events.
- Must preserve existing events from other workflows or dates.
- Must include order shape fields in submitted and state-derived event payloads so non-LO TP1, trigger, and trailing orders remain auditable.
- Must emit `order_replaced` events from `paper-replace-orders.jsonl`, preserving previous/new quantity, previous/new limit price, decision reason, and raw request/response.
- Must emit `order_cancel_executed` and `order_cancel_failed` events from `paper-order-cancel-plan.json`, preserving cancel reason, broker order id, raw request/response, and error details when present.
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
- Candidate lessons may be emitted as review observations, but they must not be promoted into `canonical rulebook/`.

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

## paper-learning-lessons

Purpose: feed paper execution review candidate lessons into the existing runtime learning loop.

Canonical command:

```bash
python3 script/trading_copilot.py paper-learning-lessons --date <DATE> --append
```

Inputs:

- `report/<DATE>/paper-execution-review.json`.

Outputs:

- `report/<DATE>/paper-learning-lessons.json`
- With `--append`, `runtime/learning/daily_lessons.jsonl`

Required behavior:

- Must write runtime candidate lessons only; it must not modify `canonical rulebook/`.
- Must be idempotent for repeated runs of the same paper review lessons.
- Lessons must use `lesson_type=paper_execution`, `status=candidate`, and preserve symbol/setup/evidence/source ids where available.
- Promotion remains gated by `learning-review` and explicit human-approved `promote-lesson --apply`.

## paper-trade-submit

Purpose: prepare controlled paper order submissions from validated dry-run order previews.

Canonical command:

```bash
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation
```

Execute command:

```bash
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation --execute
```

Inputs:

- `report/<DATE>/paper-trade-preview.json`.
- `runtime/paper/<DATE>/paper-account-snapshot.json`.
- Optional `runtime/paper/<DATE>/paper-orders.jsonl` for duplicate detection.
- `report/<DATE>/pre-market-signals.json` or `report/<DATE>/post-market-signals.json` when `--require-validation` is used.
- `config/paper_execution.json` when `--execute` is used.

Output:

- `report/<DATE>/paper-trade-submission.json`
- `runtime/paper/<DATE>/paper-orders.jsonl` only when `--execute` successfully submits an order.

Required behavior:

- Default behavior is dry-run and must not call broker write APIs.
- Broker submission requires both `--execute` and `config/paper_execution.json` with `paper_execution.broker_writes_enabled=true` and `paper_execution.allow_entry_submit=true`.
- Broker submission must use only `script/longbridge_paper_order_adapter.py`.
- `--require-validation` must run `validate-trade-plan` for the session sidecar.
- Only `status=ready` long buy limit order intents may pass the risk guard.
- Account snapshot `account_channel` must be `lb_papertrading`.
- Duplicate `intent_id` values already present in `paper-orders.jsonl` must be skipped.
- The submission artifact must separate `ready`, `submitted`, `blocked`, `skipped_duplicates`, and `errors`.
- The submission artifact must include `execution_policy` and `broker_capabilities`.
- Successful submit records must preserve `intent_id`, `broker_order_id`, `remark`, `raw_request`, `raw_response`, and `submitted_at`.

## paper-protective-stop-plan

Purpose: build a protective stop plan for filled long paper entries, and optionally submit those stops to the Longbridge paper account through the guarded paper adapter.

Canonical command:

```bash
python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE>
python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE> --order-type LIT --limit-price <LIMIT>
python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE> --order-type TSLPPCT --trailing-percent 2.5 --limit-offset 0.3
```

Execution command:

```bash
python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE> --execute
```

Inputs:

- `runtime/paper/<DATE>/paper-execution-state.json`.
- Optional `runtime/paper/<DATE>/paper-stop-orders.jsonl` for duplicate detection.
- `config/paper_execution.json` when `--execute` is used.

Output:

- `report/<DATE>/paper-protective-stop-plan.json`
- `runtime/paper/<DATE>/paper-stop-orders.jsonl` only when `--execute` successfully submits a protective stop.

Required behavior:

- Default behavior is dry-run and must not call broker write APIs.
- Broker stop submission requires both `--execute` and `config/paper_execution.json` with `paper_execution.broker_writes_enabled=true` and `paper_execution.allow_protective_stop=true`.
- Broker stop submission must use only `script/longbridge_paper_order_adapter.py`.
- Only fully filled long buy entries with positive `stop_price`, positive filled quantity, and no existing protective stop may become stop candidates.
- The default stop plan uses Longbridge `sell` `MIT` with `--trigger-price <stop_price>` and `tif=gtc`.
- Protective stops may use the shared Longbridge order shape through `--order-type`, `--limit-price`, `--trigger-price`, `--trailing-amount`, `--trailing-percent`, `--limit-offset`, `--expire-date`, and `--outside-rth`. Price-based orders default price to `stop_price`; trigger-based orders default trigger to `stop_price`.
- Duplicate `intent_id` values already present in `paper-stop-orders.jsonl` must be blocked.
- The artifact must separate `stop_candidates`, `blocked`, `submitted`, and `errors`.
- The artifact must include `execution_policy` and `broker_capabilities`.
- Successful stop records must preserve `intent_id`, `entry_broker_order_id`, `broker_order_id`, `remark`, `raw_request`, `raw_response`, and `submitted_at`.

## paper-take-profit-plan

Purpose: build a TP1 partial-exit plan for filled long paper entries, and optionally submit those take-profit orders to the Longbridge paper account through the guarded paper adapter.

Canonical command:

```bash
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE>
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE> --order-type MIT
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE> --order-type TSLPPCT --trailing-percent 2.5 --limit-offset 0.3
```

Execution command:

```bash
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE> --execute
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE> --execute --resize-stop-before-submit
```

Inputs:

- `runtime/paper/<DATE>/paper-execution-state.json`.
- Optional `runtime/paper/<DATE>/paper-take-profit-orders.jsonl` for duplicate detection.
- `config/paper_execution.json` when `--execute` is used.

Output:

- `report/<DATE>/paper-take-profit-plan.json`
- `runtime/paper/<DATE>/paper-take-profit-orders.jsonl` only when `--execute` successfully submits a TP1 order.

Required behavior:

- Default behavior is dry-run and must not call broker write APIs.
- Broker TP1 submission requires both `--execute` and `config/paper_execution.json` with `paper_execution.broker_writes_enabled=true` and `paper_execution.allow_take_profit=true`.
- When `--resize-stop-before-submit` is used, stop resizing requires the separate `paper_execution.allow_take_profit_stop_resize=true` gate. The workflow must cancel the existing over-sized protective stop, submit a resized MIT stop for the post-TP1 remaining quantity, record it in `paper-stop-orders.jsonl`, and only then submit TP1.
- Broker TP1 submission must use only `script/longbridge_paper_order_adapter.py`.
- Only fully filled long buy entries with positive `take_profit`, positive filled quantity, and no existing TP1/take-profit order may become TP1 candidates.
- The default TP1 plan uses Longbridge `sell` `LO` with `--price <take_profit>`, `tif=gtc`, and `--exit-fraction 0.5`.
- TP1 may use the shared Longbridge order shape through `--order-type`, `--limit-price`, `--trigger-price`, `--trailing-amount`, `--trailing-percent`, `--limit-offset`, `--expire-date`, and `--outside-rth`. `LO`/price-based orders default the price to `take_profit`; trigger-based orders default the trigger to `take_profit`.
- TP1 execution must block when an active protective stop quantity exceeds the post-TP1 remaining quantity. This prevents full-size stop plus partial TP orders from creating over-exit risk when no OCO link exists.
- Duplicate `intent_id` values already present in `paper-take-profit-orders.jsonl` must be blocked.
- The artifact must separate `take_profit_candidates`, `blocked`, `submitted`, and `errors`.
- The artifact must include `execution_policy` and `broker_capabilities`.
- Successful TP1 records must preserve `intent_id`, `entry_broker_order_id`, `broker_order_id`, order shape fields, `remark`, `raw_request`, `raw_response`, `exit_fraction`, and `submitted_at`.

## paper-exit-plan

Purpose: build or execute a guarded full/remaining-position exit plan when the intraday tracker marks an open paper position's plan as invalidated.

Canonical command:

```bash
python3 script/trading_copilot.py paper-exit-plan --date <DATE>
```

Execution command:

```bash
python3 script/trading_copilot.py paper-exit-plan --date <DATE> --execute
```

Inputs:

- `runtime/paper/<DATE>/paper-execution-state.json`.
- `runtime/intraday/<DATE>/state.json`.
- Optional `runtime/paper/<DATE>/paper-exit-orders.jsonl` for duplicate detection.
- Optional `report/<DATE>/paper-exit-decisions.json` for LLM/Codex-reviewed exit decisions before hard invalidation.

Output:

- `report/<DATE>/paper-exit-plan.json`
- `runtime/paper/<DATE>/paper-exit-orders.jsonl` only when `--execute` successfully submits an exit order.

Required behavior:

- Default behavior is dry-run and must not call broker write APIs.
- Broker execution requires `--execute`, paper account validation, and config gates `allow_exit_cancel_replace=true` plus `allow_exit_submit=true`.
- Only filled long entries with an open lifecycle state and positive remaining quantity may become exit candidates.
- The first trigger source is `runtime/intraday/<DATE>/state.json` with symbol state `invalidated`.
- A second trigger source is `report/<DATE>/paper-exit-decisions.json`. A decision can trigger an exit only when it matches the open `intent_id` or symbol and contains `action=exit_remaining`, `execution_status=conditional_executable`, a non-empty `reason`, `risk_check.cancel_open_exits_first=true`, and `risk_check.remaining_quantity` equal to the current remaining quantity.
- Execution must cancel open protective stop and TP1 orders before submitting the exit order, so independent exit orders cannot over-exit the simulated position.
- The default exit order type is Longbridge `sell` `MO`; `LO`, `MIT`, `LIT`, and trailing order types are available through the shared order model when their required price/trigger/trailing fields are supplied.
- Duplicate `intent_id` values already present in `paper-exit-orders.jsonl` must be blocked.
- The artifact must separate `exit_candidates`, `blocked`, `submitted`, and `errors`, and include `execution_policy` and `broker_capabilities`.

Decision sidecar example:

```json
{
  "date": "<DATE>",
  "workflow": "paper-exit-decision",
  "decisions": [
    {
      "intent_id": "<INTENT_ID>",
      "symbol": "MU",
      "action": "exit_remaining",
      "execution_status": "conditional_executable",
      "reason": "5m lower-high breakdown with failed reclaim",
      "risk_check": {
        "remaining_quantity": 100,
        "cancel_open_exits_first": true,
        "max_loss_if_exit_now_r": 1.1
      },
      "evidence": ["runtime/intraday/<DATE>/state.json", "report/latest-monitor.json"]
    }
  ]
}
```

## paper-break-even-stop-plan

Purpose: build or execute a guarded plan to move an existing protective stop to break-even after TP1 fill evidence exists.

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
- Candidates must include the existing stop order id, remaining quantity, replacement order shape, and preview steps for canceling the old stop and submitting the replacement stop.
- The default mode is dry-run. `--execute` is allowed only against `lb_papertrading` when the selected paper execution config enables `broker_writes_enabled=true` and `allow_break_even_stop_move=true`.
- The default replacement stop is Longbridge `sell MIT` at the computed break-even price. Alternative replacement stop order types may use the shared order model through `--order-type`, `--limit-price`, `--trigger-price`, `--trailing-amount`, `--trailing-percent`, `--limit-offset`, `--expire-date`, and `--outside-rth`; price-based orders default price to the computed break-even price and trigger-based orders default trigger to the computed break-even price.
- Execution must cancel the old stop first and submit a new stop for the remaining quantity. Longbridge `order replace` must not be used for this movement because it cannot update MIT trigger prices.
- Successful movement records must be appended to `runtime/paper/<DATE>/paper-stop-orders.jsonl` and preserve the replaced stop id, new stop id, raw cancel request/response, raw submit request/response, and `intent_id`.

## paper-lifecycle

Purpose: orchestrate paper order lifecycle management for a date: refresh paper account state, sync order lifecycle, prepare or execute exit-management actions, rebuild event ledger, and generate execution review.

Canonical command:

```bash
python3 script/trading_copilot.py paper-lifecycle --date <DATE>
```

Optional execution flags:

```bash
python3 script/trading_copilot.py paper-lifecycle --date <DATE> \
  --paper-execution-config config/paper_execution.local.json \
  --execute-cancel \
  --execute-order-replace \
  --execute-protective-stop \
  --execute-take-profit \
  --execute-exit \
  --execute-break-even-stop
```

Required behavior:

- The wrapper must run paper account snapshot and paper order sync before exit planning.
- It must run cancel, pending order replace, protective-stop, TP1, full-exit, and break-even workflows, passing `--execute` only for the explicitly requested action flags.
- For pending order replace, it must pass `report/<DATE>/paper-replace-decisions.json` through to `paper-order-replace`.
- For plan-invalidated exits, it must pass shared exit order shape fields through to `paper-exit-plan`: `--exit-order-type`, `--exit-limit-price`, `--exit-trigger-price`, `--exit-trailing-amount`, `--exit-trailing-percent`, `--exit-limit-offset`, `--exit-expire-date`, and `--exit-outside-rth`.
- For break-even stop movement, it must pass shared replacement stop order shape fields through to `paper-break-even-stop-plan`: `--break-even-order-type`, `--break-even-limit-price`, `--break-even-trigger-price`, `--break-even-trailing-amount`, `--break-even-trailing-percent`, `--break-even-limit-offset`, `--break-even-expire-date`, and `--break-even-outside-rth`.
- It must refresh paper account snapshot and order sync after exit planning, then run paper event ledger and paper execution review.
- It may append paper learning lessons only with `--append-lessons`.
- It may refresh strategy-level paper review only with `--strategy-review`.
- The response must include child command payloads, artifacts, aggregate summaries, and an `execute_requested` object for each exit action.
- It must not bypass the child workflows' paper account, execute, duplicate, and config-gate checks.

## intraday-lifecycle-append

Purpose: append paper lifecycle status into the same-day intraday Markdown log and produce a compact notification/filter artifact.

Canonical command:

```bash
python3 script/trading_copilot.py intraday-lifecycle-append --date <DATE>
```

Inputs:

- `runtime/paper/<DATE>/paper-execution-state.json`
- `report/<DATE>/paper-order-cancel-plan.json`
- `report/<DATE>/paper-replace-plan.json`
- `report/<DATE>/paper-protective-stop-plan.json`
- `report/<DATE>/paper-take-profit-plan.json`
- `report/<DATE>/paper-exit-plan.json`
- `report/<DATE>/paper-break-even-stop-plan.json`
- `report/<DATE>/paper-event-ledger.json`
- `report/<DATE>/paper-execution-review.json`

Outputs:

- Appends a `模拟盘生命周期` section to `report/<DATE>/intraday.md`
- Writes `report/<DATE>/intraday-lifecycle-summary.json`

Required behavior:

- It must only read existing paper lifecycle artifacts and must not call Longbridge or any broker API.
- If no lifecycle artifacts exist, it must return `status=skipped` and avoid creating `intraday.md`.
- The summary must include cancel, pending order replace, protective-stop, take-profit, full-exit, break-even stop, ledger, and execution-review counts when those artifacts exist.
- `should_notify=true` should be set when there are lifecycle candidates, submitted/moved/cancelled actions, errors, or ledger events.

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
- `canonical rulebook/`.

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

## daily-workflow-review

Purpose: review the same-day pre-market, intraday, and post-market process after post-market validation, and compare recorded signals against completed price evidence.

Canonical command:

```bash
python3 script/trading_copilot.py daily-workflow-review --date <SNAPSHOT_DATE>
```

Inputs:

- `report/<DATE>/pre-market-signals.json`
- `report/<DATE>/monitor-signals.json`
- `report/<DATE>/intraday-opportunity-context.json`
- `runtime/intraday/<DATE>/state.json`
- `runtime/intraday/<DATE>/events.jsonl`
- `report/<DATE>/daily-snapshot.json`
- `report/<DATE>/post-market-run-manifest.json`

Output:

- `report/<DATE>/workflow-review.json`
- `report/<DATE>/workflow-review.md`

Required behavior:

- Summarize whether pre-market, intraday, and post-market artifacts were present and successful.
- Classify watch/no-trade observations against price evidence as `possible_process_miss`, `touch_fade_or_invalidated`, `not_triggered`, or `not_evaluable`.
- Treat `conditional_executable` Trade Plan Cards as execution-review inputs, not as missed watch-only opportunities.
- Keep the review read-only; it must not mutate journal records, watchlists, broker state, or `canonical rulebook/`.

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

Purpose: convert actionable monitor observations into a watch-only sidecar and optionally append them to the journal.

Canonical command:

```bash
python3 script/trading_copilot.py extract-monitor-signals --append
python3 script/trading_copilot.py extract-monitor-signals --date <DATE>
```

Inputs:

- `report/latest-monitor.json`

Output:

- Writes `report/<DATE>/monitor-signals.json`.
- With `--append`, writes observed monitor signals to `runtime/journal/signals.jsonl`.

Required behavior:

- Append only actionable observation statuses such as `可执行` and `临近触发`.
- Treat monitor entries as observations, not trade instructions.
- Sidecar signals must use `plan_type=watch_only` and `execution_status=watch_only`.
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
python3 script/workflow_smoke_test.py --date <DATE> --week <YYYY-Www> --paper-input path/to/paper-fixture.json --paper-lifecycle-smoke
```

Required behavior:

- Use existing fixture artifacts under `--repo-root`.
- Exercise validation, signal extraction, outcome backfill, optional account snapshot and position review, daily review, weekly review, and monitor extraction.
- When `--paper-lifecycle-smoke` is set with `--paper-input`, seed local paper order journals from the fixture preview and exercise paper order sync, protective stop, TP1, plan-invalidated exit, break-even stop planning, event ledger, execution review, and intraday lifecycle append.
- Must not fetch market data or account data.
- Must not call Longbridge or any broker API.

## research-note

Purpose: turn source material, market observations, or user questions into a reusable research note.

Inputs:

- User-provided topic or source path.
- Optional `vault raw sources/` material.
- `canonical rulebook/` for rule consistency checks.

Outputs:

- A research memo in the response or a user-requested file.

Boundary:

- Research notes do not automatically promote new trading rules.
- Rule promotion requires an explicit review task and should update `canonical rulebook/` only after checking conflicts.

## rule-check

Purpose: validate a user thesis, report section, or setup idea against the approved rule base.

Inputs:

- User-supplied thesis or artifact path.
- `canonical rulebook/global/`.
- Relevant `canonical rulebook/setups/` files.

Outputs:

- `pass`, `fail`, or `unclear` by rule area.
- Missing-data and missing-rule notes.
- Concrete edits needed to make the thesis compliant.

Boundary:

- Do not invent missing setup rules.
- Prefer `NO TRADE` when a thesis depends on unresolved rule conflicts.
