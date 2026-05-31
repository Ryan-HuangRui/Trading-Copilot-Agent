# Agent Research Contracts

This contract defines the TradingAgents-style research artifacts used by this repository. These artifacts are evidence inputs for reports and validators; they are not broker commands and must not bypass existing report, trade-plan, or paper-execution gates.

## Shared Rules

- All artifacts use `schema_version=1`.
- Dates use `YYYY-MM-DD`.
- Symbol values are uppercase display symbols such as `MU` or `NVDA`.
- Every workflow wrapper returns the shared status envelope: `status`, `workflow`, `date`, `artifacts`, `skipped`, and `reason`.
- Runtime outputs live under ignored paths: `report/<DATE>/agents/` or `runtime/memory/`.
- Phase 0 placeholder artifacts must include `experimental=true` and `not_for_execution=true`.
- Placeholder `decision.json` artifacts are invalid for report delivery, journal append, paper preview, and broker submission.
- Agent research can downgrade confidence or execution readiness, but must not upgrade an existing `watch_only` or `no_trade` candidate into `conditional_executable`.

## Paths

Default paths:

```text
report/<DATE>/agents/research-context.json
report/<DATE>/agents/<SYMBOL>/market_report.json
report/<DATE>/agents/<SYMBOL>/technicals_report.json
report/<DATE>/agents/<SYMBOL>/fundamentals_report.json
report/<DATE>/agents/<SYMBOL>/news_report.json
report/<DATE>/agents/<SYMBOL>/sentiment_report.json
report/<DATE>/agents/<SYMBOL>/bull_report.json
report/<DATE>/agents/<SYMBOL>/bear_report.json
report/<DATE>/agents/<SYMBOL>/risk_report.json
report/<DATE>/agents/<SYMBOL>/decision.json
report/<DATE>/agents/<SYMBOL>/decision.md
runtime/memory/trading_memory.md
runtime/memory/trading_memory.sqlite
```

## Evidence Object

Every report evidence item must include:

- `evidence_id`: stable id unique within the report set.
- `source`: file path, provider name, or fixture id.
- `source_type`: `market_data`, `technical_indicator`, `fundamentals`, `news`, `sentiment`, `memory`, or `manual_fixture`.
- `as_of` or `published_at`: timestamp or market date for freshness checks.
- `symbol`: ticker symbol.
- `summary`: concise factual summary.
- `confidence`: numeric score from `0` to `1`.
- `limitations`: array of data or interpretation limits.

## Research Reports

The five first-class report types are:

- `market`
- `technicals`
- `fundamentals`
- `news`
- `sentiment`

Required top-level fields:

- `schema_version`
- `report_type`
- `date`
- `symbol`
- `generated_at`
- `evidence`
- `facts`
- `derived_metrics`
- `scores`
- `limitations`

Reports may contain heuristic scores, but must distinguish them from factual evidence and derived market metrics. Reports must not contain direct buy/sell/order instructions.

## Phase 1 Data Tools

Phase 1 introduces deterministic local tools. They do not call an LLM and do not write broker commands.

```bash
python3 script/agent_market_data.py --date <DATE> --symbol MU --snapshot report/<DATE>/daily-snapshot.json
python3 script/agent_technicals.py --date <DATE> --symbol MU --market-data report/<DATE>/agents/market-data.json
```

`agent_market_data.py` reads an existing `daily-snapshot.json` or `pre-market-context.json`, preserves Longbridge/Twelve provider metadata, and emits evidence with source/freshness/confidence/limitations fields.

`agent_technicals.py` reads the market-data artifact and computes deterministic OHLCV-derived metrics such as SMA, ATR, RSI, volume averages, and close-change percentage.

`config/agent_research.json` defines the provider policy. In Phase 1, fundamentals, news, and sentiment providers are fixture-only contracts:

- `mode=fixture`
- `live_enabled=false`
- no API credentials required
- sentiment evidence cannot raise execution status

## Phase 2 Report Generation

Phase 2 converts local data-tool artifacts into per-symbol analyst reports:

```bash
python3 script/trading_copilot.py agent-research-reports --date <DATE> --symbol MU
python3 script/trading_copilot.py validate-agent-reports --date <DATE> --symbol MU
```

Direct script entrypoints are also available:

```bash
python3 script/agent_research_reports.py --date <DATE> --symbol MU --markdown
python3 script/validate_agent_reports.py --date <DATE> --symbol MU
```

Required behavior:

- Writes five JSON reports per symbol under `report/<DATE>/agents/<SYMBOL>/`.
- Optional `--markdown` writes `research_report.md` for human review.
- Same fixture inputs must produce schema-stable reports.
- `validate-agent-reports` fails when required report fields or evidence fields are missing.
- Broker/order command text such as `submit_order` or `broker_command` is forbidden in report artifacts.

## Decision Artifact

`decision.json` is the structured output of role reasoning. It may help draft a Trade Plan Card, but it is not itself an order.

Required fields:

- `schema_version`
- `decision_id`
- `date`
- `symbol`
- `generated_at`
- `plan_type`: one of `trade_plan`, `watch_only`, or `no_trade`.
- `execution_status`: one of `conditional_executable`, `waiting_trigger`, `watch_only`, or `no_trade`.
- `decision_label`
- `evidence_ids`
- `risk_summary`
- `limitations`

Forbidden fields:

- `order`
- `orders`
- `broker_command`
- `submit_order`
- `cancel_order`
- `replace_order`

Validation rules:

- `experimental=true` or `not_for_execution=true` must fail `validate-agent-decision`.
- `conditional_executable` requires a complete draft Trade Plan Card before conversion into session signal sidecars.
- Missing trigger, invalidation, risk, TP1, or skip conditions must downgrade to `watch_only` or `no_trade`.
- Memory references can lower confidence or trigger review only; they cannot raise execution grade.

## Phase 3 Role Reasoning

Phase 3 adds deterministic role artifacts without making LangGraph a hard dependency:

```bash
python3 script/trading_copilot.py agent-decision --date <DATE> --symbol MU
python3 script/trading_copilot.py validate-agent-decision --date <DATE> --symbol MU
```

Direct script entrypoints are also available:

```bash
python3 script/agent_decision.py --date <DATE> --symbol MU
python3 script/validate_agent_decision.py --date <DATE> --symbol MU
```

Expected role outputs:

- `bull_report.json`: supporting and opposing evidence ids for the upside scenario.
- `bear_report.json`: supporting and opposing evidence ids for the downside or no-trade scenario.
- `risk_report.json`: invalidation, liquidity/data limits, portfolio constraints, and risk summary.
- `decision.json`: final structured decision using existing signal semantics.
- `decision.md`: human-readable decision summary.

Validation rules:

- Bull and Bear reports must include both supporting and opposing evidence ids.
- Risk Manager output must include invalidation, data/liquidity limits, portfolio constraints, and risk summary.
- `decision.json` must use `plan_type=trade_plan/watch_only/no_trade` and `execution_status=conditional_executable/waiting_trigger/watch_only/no_trade`.
- `experimental=true` or `not_for_execution=true` decisions fail validation.
- `conditional_executable` decisions require complete Trade Plan Card fields before they can be converted downstream.
- Broker/order command fields and command text are forbidden.

## Phase 5 Memory

Phase 5 adds append-only memory for decision outcomes and reflections:

```bash
python3 script/trading_copilot.py agent-memory-append --decision report/<DATE>/agents/MU/decision.json --outcome-status not_triggered --reflection "Kept as watch only."
python3 script/trading_copilot.py agent-memory-review --date <DATE> --symbol MU
python3 script/trading_copilot.py agent-memory-export
```

Direct script entrypoint:

```bash
python3 script/agent_memory.py append --decision report/<DATE>/agents/MU/decision.json
python3 script/agent_memory.py review --date <DATE> --symbol MU
python3 script/agent_memory.py export --sqlite-output runtime/memory/trading_memory.sqlite
```

Required memory fields:

- `date`
- `symbol`
- `decision_id`
- `evidence_ids`
- `decision_label`
- `plan_type`
- `execution_status`
- `outcome_status`
- `reflection`

Rules:

- Markdown memory is append-only and idempotent by `decision_id`.
- SQLite memory is rebuilt/exported from Markdown memory.
- Memory review is read-only.
- Memory must not edit `knowledge/refined/`.
- Memory can lower confidence, add restrictions, or trigger human review only; it cannot raise `execution_status` or upgrade `watch_only/no_trade` to `conditional_executable`.

## Phase 0 Wrapper Commands

```bash
python3 script/trading_copilot.py agent-research-context --date <DATE> --symbol MU
python3 script/trading_copilot.py agent-research-reports --date <DATE> --symbol MU
python3 script/trading_copilot.py agent-decision --date <DATE> --symbol MU
python3 script/trading_copilot.py agent-memory-review --date <DATE> --symbol MU
```

Phase 0 commands write deterministic skeleton artifacts only. They are useful for contract tests and downstream integration wiring, but they must not be injected into production pre-market or post-market `next_agent_inputs`.
