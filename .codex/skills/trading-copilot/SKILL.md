---
name: trading-copilot
description: Route broad or operational Trading-Copilot-Agent repository requests that do not clearly belong to a dedicated analysis skill. Use for monitor briefs, weekly/process review, research notes, rule validation, position review, workflow diagnostics, or paper-trading readiness/lifecycle. For 盘前、盘后、单一 ticker 价格行为、Swarm 深度研究或盘中跟踪, route to the corresponding dedicated repo-only skill instead of performing that analysis here.
---

# Trading Copilot Router and Operations

## Route first

Choose exactly one primary Skill whenever the request has a clear scope:

- 盘前分析、盘前计划、`exec-brief.md` → `$tca-pre-market-analysis`
- 盘后复盘、收盘总结、明日观察 → `$tca-post-market-review`
- 单 ticker 当前价格行为、关键位、入场/加仓条件 → `$tca-price-action-analysis`
- 财报、招股书、行业景气、季度行业比较 → `$tca-earnings-research`；独立基本面研究，不套用价格行为交易规则
- Swarm 深度研究、正反论证、独立取证 → `$tca-swarm-research`
- 盘中计划跟踪、事件日志、通知候选 → `$intraday-tracker`

Do not duplicate those workflows here. Coordinate more than one Skill only when the user explicitly requests a cross-session or research-plus-price-action workflow.

## Shared safety

- Keep Longbridge real-account workflows read-only.
- Never place real trades or output deterministic buy/sell instructions.
- Paper broker writes are allowed only in dedicated paper workflows, only against `lb_papertrading`, only after explicit user authorization, and only with both `--execute` and the exact config action gate enabled.
- Use the canonical rulebook resolved by `config/knowledge_source.json`; local runtime learning is non-authoritative.
- Use Longbridge CLI primary and Twelve Data fallback for repository scripts. Disclose the actual provider/backend.
- Stop on `failed`, `skipped=true`, failed agent research, `stale_data=true`, failed validation, or failed delivery guard.

## Operational entrypoint

Use:

```bash
python3 script/trading_copilot.py <workflow> [options]
```

The wrapper status envelope is authoritative: `status`, `workflow`, `date`, `artifacts`, `skipped`, and `reason`.

Read `docs/contracts/workflows.md` for the exact command and state-transition contract before running an operational workflow.

## Supported operations

### Monitor and process review

- `monitor-brief`: read-only market observation summary.
- `weekly-review`: process feedback; never infer trading win rate from signal outcomes.
- `daily-workflow-review`, `plan-review`, `daily-self-review`, and `learning-review`: evidence and candidate lessons only.
- `promote-lesson --apply`: require explicit approval for the exact candidate.

### Position review

Run `account-snapshot` followed by `position-review`. Treat output as human-review prompts, not automatic adjustment instructions.

### Research notes and rule validation

Treat vault raw sources as research input only. Check theses against canonical global rules and setup files. Report pass/fail/unclear by rule area without inventing missing rules.

### Paper-trading readiness

Default every paper command to dry-run. Before any `--execute`, read the exact paper workflow in `docs/contracts/workflows.md`, confirm `lb_papertrading`, explicit user authorization, `broker_writes_enabled=true`, and the matching per-action gate. Real-account writes remain forbidden.

## Output contract

Report the workflow/date, inputs read, artifacts written, status/reason, validation and data-quality results, actual data provider, account boundary, and any pending human approval. Use simplified Chinese unless requested otherwise.
