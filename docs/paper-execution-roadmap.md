# Paper Execution Roadmap

This roadmap defines how Trading Copilot should evolve from paper-trading previews into a Longbridge paper execution and review system. It is a planning document, not a workflow contract. Current executable behavior remains defined in `docs/contracts/workflows.md`.

## Long-Term Goal

Trading Copilot should be able to take validated structured trade plans, submit them to a Longbridge paper account under strict execution gates, track order and fill state, and produce execution and strategy reviews from factual broker and journal data.

The system must preserve these boundaries:

- LLMs generate structured plans and review explanations only.
- Scripts validate plans, run risk gates, submit paper orders, sync broker state, and write journal facts.
- Broker write operations are isolated in a dedicated Longbridge paper order adapter.
- All broker writes must be audit logged, idempotent, and limited to `lb_papertrading`.
- Paper execution results may create learning candidates, but must not directly modify `knowledge/refined/`.
- Real-money execution is out of scope unless a separate real-trading execution domain is explicitly designed.

## Architecture Domains

```text
research domain        pre/post-market reports and structured signals
validation domain      validate-report, validate-trade-plan, data-quality checks
risk domain            paper-risk-guard execution safety checks
execution domain       paper-trade-preview, paper-trade-submit, order adapters
state domain           paper-orders.jsonl, snapshots, journal records, future events
review domain          paper-trade-review, execution review, strategy review, learning review
```

Broker writes must never be added to research, validation, state, or review scripts.

## External Project Learnings

The next phase should borrow patterns from popular open-source trading-agent systems without copying their execution looseness:

- Multi-agent research systems such as TradingAgents and ai-hedge-fund separate market data, fundamentals, sentiment/news, risk, and portfolio review into distinct analyst roles. Trading Copilot should adopt the separation of evidence, but keep broker writes deterministic and script-gated.
- FinRobot-style DataOps is a better fit for news, filings, and earnings ingestion than ad hoc prompt fetching. New information sources should write versioned artifacts first, then reports and validators can consume those artifacts.
- Freqtrade and Lean are useful execution references: dry-run/live parity, event replay, state persistence, and explicit order lifecycle handling matter more than adding many order types early.

Near-term priorities:

1. Strengthen execution state and broker capability visibility.
2. Add a structured market-intelligence layer for news, earnings, filings, and sentiment.
3. Promote intraday monitor observations into validated dry-run paper candidates before enabling any intraday broker writes.
4. Expand order types only after the lifecycle state machine can model linked entry, stop, TP1, cancel, and replace events.

## Milestone 0: Roadmap And Boundary Documentation

Goal: make the long-term direction explicit before expanding execution capability.

Deliverables:

- `docs/paper-execution-roadmap.md`
- A short pointer from `docs/contracts/workflows.md`
- README/runbook references for paper execution milestones

Acceptance:

- The roadmap separates current contracts from future capabilities.
- The boundary between read-only paper workflows and future paper writes is explicit.

## Milestone 1: Controlled Paper Entry Submission

Goal: automatically submit guarded Longbridge paper long entry orders from validated plans.

Implementation status: order models, risk guard, dry-run submission, guarded Longbridge paper order adapter, `paper-trade-submit --execute` integration, and fixture smoke coverage are implemented.

Scope:

- Long only
- Buy only
- Longbridge-supported entry order types only
- Paper account only
- Entry orders only

Out of scope:

- Short selling
- Stop-loss orders
- Take-profit orders
- OCO
- Cancel/replace
- Real-money trading

Core components:

- `script/paper_order_models.py`
- `script/paper_risk_guard.py`
- `script/paper_trade_submit.py`
- `script/longbridge_paper_order_adapter.py`
- `runtime/paper/<DATE>/paper-orders.jsonl`
- `report/<DATE>/paper-trade-submission.json`

Execution gates:

- `validate-trade-plan` must pass for the source sidecar.
- `paper-trade-preview` must produce `status=ready`.
- `paper-risk-guard` must pass.
- `account_channel` must be `lb_papertrading`.
- `--execute` is required for broker submission.
- `config/paper_execution.json` must enable `paper_execution.broker_writes_enabled=true` and the relevant action gate for broker submission.

Adapter boundary:

- `longbridge_paper_trade_adapter.py` remains read-only.
- `longbridge_paper_order_adapter.py` is the only paper broker-write adapter.
- The adapter write capability supports Longbridge order types `LO`, `ELO`, `MO`, `AO`, `ALO`, `ODD`, `SLO`, `LIT`, `MIT`, `TSLPAMT`, and `TSLPPCT`, with command-shape validation for required price, trigger, and trailing fields.
- Replace, OCO, short selling, and real-money orders remain out of scope for Milestone 1.

Idempotency:

- Every order intent must have a stable `intent_id`.
- Duplicate `intent_id` records in `paper-orders.jsonl` must block repeat submission.
- Submit records must preserve `raw_request`, `raw_response`, `broker_order_id`, `remark`, and `submitted_at` when available.

## Milestone 2: Paper Order State Sync

Goal: track what happened after submission.

Planned state machine:

```text
created
ready
submitted
accepted
partially_filled
filled
rejected
cancel_requested
cancelled
expired
failed
```

Planned components:

- `script/paper_order_sync.py`
- `runtime/paper/<DATE>/paper-execution-state.json`

Implementation status: the read-only `paper-order-sync` command is implemented for entry orders, protective stops, and TP1 take-profit orders. It writes `schema_version=paper-execution-state/v2`, `broker_capabilities`, `protective_stops`, `take_profit_orders`, `exit_summary`, and enriched entry lifecycle fields that later exit planning can consume. `paper-trade-review` now prefers submitted-order matching data before legacy symbol-side matching.

Matching order:

1. `broker_order_id`
2. `remark` containing `tca:<intent_id>`
3. `intent_id`
4. `symbol + side + quantity + submitted_at` time window fallback

Long term, `paper-account-snapshot` should remain the account/position snapshot, while `paper-order-sync` owns order and execution state.

## Milestone 3: Exit And Protection Management

Goal: protect and close paper positions after an entry fill.

Recommended sequence:

1. Cancel expired unfilled entry orders.
2. Submit protective stops after confirmed fills.
3. Add TP1 partial exits.
4. Add break-even stop movement or trailing logic only after basic exits are stable.

Implementation status: `paper-order-cancel` now generates a dry-run cancel plan for expired unfilled entry orders and can execute those cancels through the guarded paper adapter when `--execute` and the cancel gate in `config/paper_execution.json` are both enabled. `paper-protective-stop-plan` now generates a `sell MIT --trigger-price <stop>` plan for filled long entries and can submit those protective stops through the guarded paper adapter under the same config-driven execution gates. `paper-take-profit-plan` now generates a default 50% TP1 partial-exit `sell LO --price <tp1>` plan for filled long entries and can submit those take-profit orders through the guarded paper adapter under the same config-driven execution gates. `paper-break-even-stop-plan` now generates a break-even stop movement plan after TP1 fill evidence exists and can execute it as guarded cancel old stop plus submit new MIT stop when `allow_break_even_stop_move=true`.

All exit actions must use the same paper-account, config gate, execute flag, idempotency, and audit-log gates as entry submission.

The default scheduler posture remains dry-run for exit management. Deployment automation may enable expired-entry cancellation only when both conditions are true:

- `TCA_PAPER_CANCEL_EXECUTE=1` is set for the cc-connect sync task.
- The selected paper execution config enables both `broker_writes_enabled=true` and `allow_cancel=true`.

Protective-stop, TP1, and break-even broker writes should remain manual or dry-run until bracket/OCO and cancel-then-submit state-drift risk are operationally accepted.

## Milestone 3.5: Broker Capability Matrix

Goal: make every paper artifact self-describing about what the current broker adapter can and cannot do.

Implementation status: `paper_execution_config.py` exposes a Longbridge paper capability matrix and policy summary. Execution artifacts now include `execution_policy` and `broker_capabilities`; `paper-order-sync` state v2 includes `broker_capabilities`.

Current supported guarded write actions:

- `entry_submit`: long buy `LO` entry, initial rollout action.
- `cancel`: expired unfilled entry cancel, disabled by default.
- `protective_stop`: sell `MIT` protective stop, dry-run-first.
- `take_profit`: sell `LO` TP1 partial exit, dry-run-first.

Explicitly unsupported actions:

- market entry
- native OCO/bracket order
- cancel/replace stop movement
- short entry

## Milestone 7: Market Intelligence Layer

Goal: enrich plans with evidence beyond Longbridge bars without making news directly executable.

Planned artifacts:

- `report/<DATE>/market-intel.json`
- `report/<DATE>/symbol-intel/<SYMBOL>.json`

Planned sources:

- market and symbol news
- earnings calendar and earnings-call summaries
- SEC filings / 10-Q / 10-K highlights
- analyst rating changes and guidance changes
- optional social sentiment, stored as low-confidence evidence unless source quality is reviewed

Rules:

- Market-intel artifacts are evidence inputs only.
- Every evidence item needs source, timestamp, affected symbols, confidence, and freshness.
- Trade Plan Cards may cite evidence ids, but order previews must still pass deterministic validation and risk guard checks.

## Milestone 8: Intraday Paper Candidate Loop

Goal: convert monitor observations into validated paper dry-run candidates without immediately enabling broker writes.

Planned flow:

```text
monitor_scan -> monitor-signals.json -> validate-trade-plan --session monitor
             -> paper-trade-preview --session monitor -> paper-trade-submit dry-run
```

Initial constraints:

- Only symbols already in the pre-market/post-market focus pool or configured monitor state.
- Per-day and per-symbol cooldown gates.
- Separate action gate such as `allow_intraday_entry_submit`, disabled by default.
- `paper-trade-submit --session monitor --execute` remains hard-disabled regardless of config; Phase 3 execution must use the standalone `intraday-paper-entry` contract.
- Feishu delivery of candidate, skipped, and blocked reasons before any execution rollout.

## Milestone 9: Bracket/OCO And Advanced Order Types

Goal: support richer paper order management without creating state drift.

Recommended sequence:

1. Local bracket model that links entry, protective stop, and TP1 records by `intent_id`.
2. OCO simulation in local state and review artifacts, still dry-run.
3. Broker capability discovery for native OCO or bracket support.
4. Cancel/replace safety for stop resizing and break-even moves.
5. Market orders only after slippage caps and liquidity checks are contracted.

Do not enable automatic market orders or native OCO until event replay can prove the state machine handles partial fills, cancels, rejected child orders, and duplicate scheduler runs.

## Milestone 4: Unified Trading Event Ledger

Goal: make all execution facts replayable.

Planned event stream:

```text
runtime/journal/events.jsonl
```

Example event types:

- `signal_created`
- `order_intent_created`
- `order_submitted`
- `order_filled`
- `order_rejected`
- `stop_submitted`
- `take_profit_filled`
- `position_closed`
- `review_generated`

Existing files such as `signals.jsonl`, `trades.jsonl`, and `reviews.jsonl` can remain as projections or compatibility views.

Implementation status: `paper-event-ledger` projects paper entry, stop, and TP1 submit/status facts into `runtime/journal/events.jsonl` with deterministic event ids, while preserving existing events from other dates/workflows.

Milestone 1 should already preserve fields needed for future replay: `intent_id`, `source_signal_id`, `broker_order_id`, `remark`, `submitted_at`, `raw_request`, `raw_response`, and `idempotency_key`.

## Milestone 5: Paper Execution Review

Goal: analyze individual paper orders and trades.

Planned outputs:

- `report/<DATE>/paper-execution-review.json`
- `report/<DATE>/paper-execution-review.md`

Review dimensions:

- Plan adherence
- Entry timing
- Slippage
- Fill quality
- Risk discipline
- MFE and MAE in R
- Result in R
- Violated skip conditions
- Candidate lessons

Reviews may create learning candidates, but must not edit `knowledge/refined/`.

Implementation status: `paper-execution-review` writes JSON and Markdown reviews from `paper-trade-preview.json` plus synced `paper-execution-state.json`, including plan adherence, slippage, fill quality, risk discipline, planned RR, result R when exit evidence exists, and candidate lessons without modifying refined rules.

## Milestone 6: Strategy-Level Paper Review

Goal: aggregate paper execution evidence by setup and context.

Planned outputs:

- `report/strategy/paper-strategy-review.json`
- `report/strategy/paper-strategy-review.md`

Core dimensions:

- setup
- symbol
- market regime
- session
- time window
- entry type
- risk bucket
- holding period

Core metrics:

- planned count
- submitted count
- filled count
- cancelled/expired count
- average R
- median R
- win rate
- average slippage
- false trigger rate
- no-fill-then-win rate

Implementation status: `paper-strategy-review` aggregates discovered or explicitly provided paper execution reviews by setup and symbol, writing JSON and Markdown strategy-level evidence without editing refined rules.

## Milestone 7: Learning Loop

Goal: feed paper execution evidence into the existing learning workflow without allowing automatic rule mutation.

Flow:

```text
paper execution facts
-> order reviews
-> daily/weekly reviews
-> strategy statistics
-> pattern candidates
-> human approval
-> promote-lesson
```

Allowed writes:

- `runtime/learning/daily_lessons.jsonl`
- `runtime/learning/pattern_candidates.jsonl`
- `knowledge/evolution/validated_lessons.md` only through explicit human-approved promotion

Disallowed writes:

- Direct automatic edits to `knowledge/refined/`

Implementation status: `paper-learning-lessons` extracts candidate lessons from paper execution reviews into `runtime/learning/daily_lessons.jsonl` with idempotent append behavior. Existing `learning-review` and human-approved `promote-lesson` remain the only path toward validated lessons.

## Operating Principle

The project may become a Longbridge paper execution system, but it must not become an uncontrolled LLM trading agent. Every automated action must be backed by validated structured inputs, deterministic risk checks, broker account guards, idempotent state, and audit evidence.
