# Trading Copilot Skill/Plugin Roadmap

This repository should remain a trading research workflow package first. It should not become a standalone trading product unless that is an explicit future decision.

## Phase 1: Repo-Local Skill

Goal: make Codex/Claude/OpenClaw-style agents reliably execute the existing workflows.

Scope:

- Maintain `.codex/skills/trading-copilot/SKILL.md` as the primary capability entrypoint.
- Use `script/trading_copilot.py` as the canonical machine wrapper.
- Keep workflow and data contracts in `docs/contracts/`.
- Keep ad-hoc workflow guidance in `docs/workflows/`.
- Produce research artifacts under existing runtime paths, primarily `report/` and `raw_data/`.

Out of scope:

- Broker APIs.
- Real order placement.
- Standalone UI.
- Complex multi-agent orchestration runtime.

## Phase 2: Stable Tool Surface

Goal: make the skill easier for external agents to call without learning every script.

Candidate work:

- Add JSON schema checks for wrapper responses and generated artifacts.
- Add read-only commands for listing available report dates and latest artifacts.
- Add dry-run validations that do not require market-data API calls.
- Add small tests around skipped/non-trading-day behavior.

## Phase 3: Optional MCP/Plugin Layer

Goal: expose stable read-only workflow commands to hosts that prefer a plugin or MCP surface.

Only start this phase after the wrapper and contracts are stable.

Candidate tools:

- `prepare_pre_market_context`
- `prepare_post_market_snapshot`
- `run_monitor_scan`
- `list_report_artifacts`
- `read_refined_rule`

Plugin/MCP boundaries:

- Read or prepare research artifacts only.
- No broker tools.
- No account, portfolio, or order mutation tools.
- No deterministic buy/sell instruction output.

## Phase 4: Optional Multi-Agent Prompt Roles

Goal: absorb useful TradingAgents/ai-hedge-fund ideas without adopting their runtime.

Recommended shape:

- Keep roles as prompt sections or review checklists.
- Start with technical, market-regime, risk, and reviewer perspectives.
- Do not add a graph/orchestrator until repeated workflows prove the need.

## Phase 5: External Backtest or Paper Portfolio Adapters

Goal: evaluate ideas without turning this repository into an execution engine.

Candidate adapters:

- Longbridge paper-account snapshot, dry-run order preview, and paper execution review.
- Lightweight local paper ledger.
- Lean export for serious multi-asset backtests.
- freqtrade adapter only if crypto execution/backtest becomes relevant.

Boundary:

- External engines remain adapters.
- Human confirmation remains required.
- Research conclusions stay separate from order execution.
- Paper broker writes must stay out of public skill prompts and remain confined to guarded repository workflows with explicit execution gates.
