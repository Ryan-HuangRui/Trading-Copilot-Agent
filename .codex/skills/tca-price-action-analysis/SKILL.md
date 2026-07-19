---
name: tca-price-action-analysis
description: Analyze one current or recent US stock ticker inside the Trading-Copilot-Agent repository with fresh market data and the canonical price-action rulebook. Use for 个股价格行为、关键位、结构、做多或做空条件、是否适合入场、能否加仓、ticker setup, entry timing, or symbol memo requests. Do not use for full pre-market/post-market workflows, broad watchlist reports, intraday automation, or fundamentals-only deep research.
---

# TCA Price Action Analysis

## Boundary

Produce a complete evidence-backed analysis, not merely a verdict. Do not place orders, call broker mutation APIs, or say “现在必须买/卖”. Use conditional scenarios and `SETUP VALID`, `WATCH`, or `NO TRADE`. Never recommend adding to a losing position.

## Required contract

Read [references/analysis-contract.md](references/analysis-contract.md) before answering. Also follow `docs/workflows/symbol-analysis.md`, `docs/contracts/workflows.md#symbol-analysis`, the canonical rulebook, and the unified Trading Copilot knowledge pack resolved through `config/knowledge_source.json`.

The knowledge pack is the only runtime knowledge entrypoint. Canonical records decide setup, risk, and execution eligibility. Relevant active method cards may explain regime, structure, key-level behavior, breakout/failure, targets, and risk as `method_context`; cite the method-card path whenever used. Never open `raw/`, SRT, or video sources during symbol analysis, and never let a method card independently upgrade the conclusion.

## Data routing

1. Normalize the symbol and identify the analysis timestamp, market session state, user position context, and requested horizon.
2. For current/recent interactive analysis, prefer connected Longbridge app/MCP read-only market-data tools.
3. If interactive Longbridge is unavailable or incomplete, use the repository Longbridge CLI/provider stack. Use Twelve Data only after the applicable Longbridge path fails and disclose the fallback.
4. When using the repository provider stack, run `python3 script/trading_copilot.py symbol-analysis-context --symbol <SYMBOL>`. It defaults to Longbridge primary with Twelve Data fallback and fetches `1day`, `1h`, `15min`, and `5min` evidence.
5. Obtain enough evidence for the requested horizon. Prefer daily plus 1h context, use 15m/5m only for execution confirmation, and disclose any missing interval. Existing fresh snapshot/context artifacts may be reused when their `price_evidence` contains the required intervals.
6. Reject stale, empty, inconsistent, or anomalous data. If concrete price evidence is unavailable, state the limitation and stop before a price conclusion.

## Analysis workflow

Analyze and show the reasoning in this fixed order:

1. **数据依据与质量**：provider/backend, as-of, session state, timeframes, freshness, missing fields.
2. **大盘与行业环境**：risk regime and whether the symbol’s sector confirms or conflicts.
3. **高周期结构**：trend/range/transition/Barb Wire, swings, gaps, channels, breakout or failure.
4. **关键位置**：current price, prior high/low/close, structural support/resistance, recent swing levels, relevant moving averages or ranges when actually computed.
5. **位置上的价格行为**：acceptance/rejection, signal bars, follow-through, volume/volatility evidence, failed breakout, micro structure.
6. **方法上下文**：select only relevant active method cards, cite their paths, and state what they explain. If none are needed, say so. Raw provenance is unavailable in this workflow.
7. **Setup 审核**：name the exact canonical setup file, list satisfied rules, failed rules, and unknown rules. Apply `tight_range_breakout_filter.md` to breakout candidates.
8. **Bull/base/bear 场景**：give conditional path, confirmation, invalidation, and skip conditions for each material scenario.
9. **风险与计划完整性**：define invalidation before any entry scenario, no-chase rule, credible reward/risk path, correlation/event risks, and no-scaling-in constraint.
10. **结论**：end with exactly one primary label—`SETUP VALID`, `WATCH`, or `NO TRADE`—and explain why. The conclusion summarizes the preceding analysis; it never replaces it.

Use `$tca-swarm-research` only when the user explicitly asks for deep research or when a material company/event thesis cannot be resolved by current evidence. Swarm cannot replace price evidence or raise a price-action conclusion by itself.

## Artifact behavior

Answer in simplified Chinese by default. Write `report/<DATE>/symbol-<SYMBOL>.md` only when the user asks for a reusable file. Do not update the daily watchlist, session sidecars, journal, or approved rulebook during an ad-hoc symbol analysis.
