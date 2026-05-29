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

Goal: automatically submit Longbridge paper entry limit buy orders from validated plans.

Implementation status: order models, risk guard, dry-run submission, guarded Longbridge paper order adapter, `paper-trade-submit --execute` integration, and fixture smoke coverage are implemented.

Scope:

- Long only
- Buy only
- Limit orders only
- Paper account only
- Entry orders only

Out of scope:

- Short selling
- Market orders
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
- `TRADING_COPILOT_PAPER_EXECUTION=enabled` is required for broker submission.

Adapter boundary:

- `longbridge_paper_trade_adapter.py` remains read-only.
- `longbridge_paper_order_adapter.py` is the only paper broker-write adapter.
- The first adapter write capability is limited to `long buy` `LO` entry orders.
- Cancel, replace, stop, take-profit, OCO, market orders, short selling, and real-money orders remain out of scope for Milestone 1.

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

Implementation status: the first read-only `paper-order-sync` command is implemented, and `paper-trade-review` now prefers submitted-order matching data before legacy symbol-side matching.

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

Implementation status: `paper-order-cancel` now generates a dry-run cancel plan for expired unfilled entry orders and can execute those cancels through the guarded paper adapter when `--execute` and `TRADING_COPILOT_PAPER_EXECUTION=enabled` are both set. `paper-protective-stop-plan` now generates a `sell MIT --trigger-price <stop>` plan for filled long entries and can submit those protective stops through the guarded paper adapter under the same execution gates. `paper-take-profit-plan` now generates a default 50% TP1 partial-exit `sell LO --price <tp1>` plan for filled long entries and can submit those take-profit orders through the guarded paper adapter under the same execution gates. `paper-break-even-stop-plan` now generates a dry-run-only plan to move existing protective stops to break-even after TP1 fill evidence exists; it does not execute cancel/replace.

All exit actions must use the same paper-account, env flag, execute flag, idempotency, and audit-log gates as entry submission.

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

## Operating Principle

The project may become a Longbridge paper execution system, but it must not become an uncontrolled LLM trading agent. Every automated action must be backed by validated structured inputs, deterministic risk checks, broker account guards, idempotent state, and audit evidence.
