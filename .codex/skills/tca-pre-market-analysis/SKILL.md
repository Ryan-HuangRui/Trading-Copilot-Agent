---
name: tca-pre-market-analysis
description: Run the Trading-Copilot-Agent repo-only pre-market analysis workflow. Use for 盘前分析、盘前计划、开盘前关注、今日执行简版、pre-market report, or generation of exec-brief.md and pre-market-signals.json. Do not use for post-market review, intraday tracking, one-ticker interactive analysis, or standalone Swarm research.
---

# TCA Pre-Market Analysis

## Boundary

Prepare research artifacts and conditional scenarios only. Never place real trades, call real-account mutation APIs, or issue deterministic buy/sell instructions. Keep Longbridge real-account access read-only. Treat `watch_only` as observation evidence, not execution authority.

Use repository scripts as the deterministic control plane. Do not reproduce trading-day, provider fallback, validation, journal, watchlist, or delivery-guard logic in prose.

## Required contract

Read [references/report-contract.md](references/report-contract.md) completely before writing report artifacts. It is the source of truth for analysis order, setup selection, Markdown sections, sidecar schema, agent-research handling, Vibe evidence handling, and quality gates.

Also use:

- `config/knowledge_source.json`, the canonical rulebook, and the unified Trading Copilot knowledge pack returned in `next_agent_inputs`. Its method cards are `method_context`: they may inform regime, structure, price-action interpretation, and evidence tracing, but cannot create an approved setup or override risk/execution rules.
- `docs/contracts/workflows.md` for wrapper behavior.
- `docs/contracts/data-contracts.md` for artifact semantics.
- `docs/contracts/agent-research.md` when agent research is present.

## Workflow

1. From the repository root, run:

   ```bash
   python3 script/trading_copilot.py pre-market-plan \
     --watchlist config/watchlist.json \
     --skip-non-trading-day \
     --include-agent-research \
     --include-vibe-research
   ```

2. Stop when the wrapper returns `failed`, `skipped=true`, or a non-trading-day result. Report the exact reason; do not fabricate missing analysis.
3. Resolve the report date from the wrapper response. Then capture the user's current positions through the connected apps, read-only:
   - IBKR: open positions, account financial metrics, and currency balances;
   - Longbridge: current stock positions and account balance.
   Persist only each tool's structured payload, without account identifiers, under
   `runtime/account/<DATE>/plugin-inputs/`, then normalize the available inputs:

   ```bash
   python3 script/trading_copilot.py plugin-account-snapshot \
     --date <DATE> \
     --ibkr-positions runtime/account/<DATE>/plugin-inputs/ibkr-positions.json \
     --ibkr-account runtime/account/<DATE>/plugin-inputs/ibkr-account.json \
     --ibkr-balances runtime/account/<DATE>/plugin-inputs/ibkr-balances.json \
     --longbridge-positions runtime/account/<DATE>/plugin-inputs/longbridge-positions.json \
     --longbridge-account runtime/account/<DATE>/plugin-inputs/longbridge-account.json
   ```

   Omit an option when that plugin call failed or is unavailable. If at least one broker
   succeeds, use `runtime/account/<DATE>/plugin-account-snapshot.json` as an additional
   analysis input. If both fail, disclose the failure and let the delivery workflow fall
   back to the existing read-only Longbridge CLI account snapshot. Never call order,
   instruction, submit, cancel, replace, or other mutation capabilities.
   For each held symbol missing from `pre-market-context.json`, run the repo's
   read-only `symbol-analysis-context` workflow and add the resulting context artifact
   to analysis provenance. If price retrieval fails, keep the holding in the report
   with an explicit `price_evidence_missing` limitation.
4. Read every existing `next_agent_inputs` entry, plus the normalized plugin account snapshot when available, including:
   - this Skill;
   - the canonical rulebook;
   - the unified Trading Copilot knowledge pack and relevant method card, when supplied;
   - `report/<DATE>/pre-market-context.json`;
   - optional agent research, external disclosure, and completed Vibe artifacts.
5. Complete the normal analysis first in this order: market environment, structure, key levels, behavior at levels, setup/trade logic. Analyze the merged fixed watchlist and dynamic observation universe without treating dynamic candidates as recommendations.
   Add a dedicated `持仓与组合风险` section that:
   - states broker coverage and snapshot time;
   - reviews every aggregated holding against current structure, key levels, today's plan, and invalidation evidence;
   - highlights cross-broker overlap, concentration, leveraged-ETF exposure, missing prices, and positions absent from today's plan;
   - preserves broker-level rows without double-counting duplicated symbols;
   - gives one conditional advice label per holding:
     `HOLD_WATCH`, `NO_ADD`, `RISK_REVIEW`, `EXIT_IF_INVALIDATED`, or
     `DATA_INSUFFICIENT`;
   - states the condition that would maintain the position thesis, the invalidation
     or exit-review trigger, whether adding is prohibited, and the maximum risk
     constraint;
   - uses scenarios and human-review flags only, never deterministic add/reduce/sell instructions.
6. Use the unified knowledge pack and its relevant method card as direct method context when it clarifies regime, structure, breakouts, pullbacks, failed breaks, targets, or risk/reward. Cite the method card in the Markdown. Raw sources are compiler-only and unavailable to the report workflow; if a card has a material gap or conflict, record it rather than opening raw content. Do not treat a method card as an approved setup or let it independently raise `execution_status`.
7. Use normal agent research only as evidence. Make the final sidecar classification independently from canonical rules, current price structure, complete Trade Plan Card fields, and risk constraints.
8. Consume only completed and hash-validated Vibe runs. They may maintain or lower confidence but cannot independently raise `execution_status`.
9. If a material evidence conflict remains, use `$tca-swarm-research` for at most one focus symbol. Start it asynchronously, persist `pending`, and continue the pre-market report without waiting.
10. Write all required artifacts atomically:
   - `report/<DATE>/exec-brief.md`
   - `report/<DATE>/pre-market.md`
   - `report/<DATE>/pre-market-signals.json`
11. Record generation provenance with this Skill as the analysis source:

   ```bash
   python3 script/trading_copilot.py llm-generation-manifest \
     --session pre-market --date <DATE> --model <MODEL> \
     --prompt .codex/skills/tca-pre-market-analysis/SKILL.md \
     --input report/<DATE>/pre-market-context.json \
     --generated-output report/<DATE>/exec-brief.md \
     --generated-output report/<DATE>/pre-market.md \
     --generated-output report/<DATE>/pre-market-signals.json
   ```

   Add `--input runtime/account/<DATE>/plugin-account-snapshot.json` when consumed.
   Add one `--input` for each optional agent-research, disclosure, or Vibe context artifact actually consumed; omit paths that do not exist.

12. Run the audited outer workflow:

   ```bash
   python3 script/trading_copilot.py pre-market-deliver \
     --date <DATE> \
     --account-snapshot runtime/account/<DATE>/plugin-account-snapshot.json \
     --sync-longbridge --execute-sync
   ```

   Omit `--account-snapshot` only when no plugin snapshot was produced.

13. Stop before external delivery if validation, data quality, agent research, or delivery guard fails. When cc-connect invokes the workflow, let its outer wrapper own `guard -> send -> mark-sent`.

## Scheduled cc-connect handoff

When invoked by `ops/cc-connect/tca-pre-market-wrapper.prompt.md`:

1. Never call `cc-connect send`.
2. Write the human-facing body to `runtime/cc-connect/out/pre-market-summary.md` and metadata to `runtime/cc-connect/out/pre-market-delivery.env`.
3. On skipped/failed/stale/validation failure, write a concise Chinese status with the exact reason and `SHOULD_MARK_SENT=0`, then stop.
4. After a successful delivery wrapper, run `python3 script/report_delivery_guard.py --kind exec-brief --date <DATE>` without `--mark-sent`.
5. If `should_send=false`, write the guard reason and `SHOULD_MARK_SENT=0`.
6. If `should_send=true`, copy `report/<DATE>/feishu-summary.md` exactly into the summary file and write:

   ```text
   DELIVERY_KIND=exec-brief
   DELIVERY_DATE=<DATE>
   SHOULD_MARK_SENT=1
   ```

7. End the Codex response with `FEISHU_SUMMARY_READY`. The outer shell sends first and marks sent only after send succeeds.

## Output handoff

Report the workflow/date, inputs read, artifacts written, validation result, data limitations, Swarm status, watchlist sync status, and broker boundary. Preserve simplified Chinese unless the user requests otherwise.
