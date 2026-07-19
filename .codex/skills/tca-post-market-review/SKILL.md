---
name: tca-post-market-review
description: Run the Trading-Copilot-Agent repo-only post-market review workflow. Use for 盘后复盘、收盘总结、计划验证、当日执行质量、明日观察计划、post-market report, or generation of post-market.md and post-market-signals.json. Do not use for pre-market planning, intraday tracking, one-ticker interactive analysis, or standalone Swarm research.
---

# TCA Post-Market Review

## Boundary

Review market behavior, plan quality, and next-session observation scenarios. Never place real trades, mutate a real account, promote a learning candidate without explicit approval, or issue deterministic next-day buy/sell instructions.

Keep deterministic date, market-data, validation, journal, watchlist, learning, and delivery state transitions in repository scripts.

## Required contract

Read [references/report-contract.md](references/report-contract.md) completely before writing report artifacts. It owns the analysis order, report structure, sidecar schema, intraday recap, Vibe reconciliation, and quality gates.

Also use:

- `config/knowledge_source.json`, the canonical rulebook, and the unified Trading Copilot knowledge pack returned in `next_agent_inputs`. Its method cards are `method_context`: they can explain structure and price behavior, but cannot create an approved setup or override risk/execution rules.
- `docs/contracts/workflows.md` and `docs/contracts/data-contracts.md`.
- `docs/contracts/agent-research.md` when role-research artifacts exist.
- `docs/workflows/vibe-swarm-research.md` when reconciling a Swarm run.

## Workflow

1. From the repository root, run:

   ```bash
   python3 script/trading_copilot.py post-market-review \
     --watchlist config/watchlist.json \
     --skip-non-trading-day \
     --include-journal-signals \
     --include-position-symbols \
     --include-agent-research \
     --include-vibe-research
   ```

2. Stop on `failed`, `skipped=true`, non-trading day, or unusable/stale canonical snapshot. Disclose the exact state.
3. Resolve the snapshot date and read all existing `next_agent_inputs`:
   - this Skill and canonical rulebook;
   - the unified Trading Copilot knowledge pack and relevant method card, when supplied;
   - `report/<DATE>/daily-snapshot.json`;
   - optional intraday Markdown/state/events;
   - agent research and completed Vibe evidence.
4. Review in this order: broad market and major industries, structure changes, key-level behavior, setup validation/failure, intraday plan outcomes, then next-session observation scenarios. Use the unified knowledge pack and relevant method card directly for method context; cite the card. Raw sources are compiler-only and unavailable to the report workflow; record a material gap or conflict rather than opening raw content. Never let a method card independently raise `execution_status`.
5. Reconcile any pending pre-market Swarm run. Consume only completed, fresh, hash-validated artifacts. Preserve pending, failed, stale, contradictions, and limitations explicitly.
6. If an unresolved abnormal move, material evidence conflict, or repeated thesis failure still needs independent audit, use `$tca-swarm-research` for at most one target. Do not block the report while it runs.
7. Write:
   - `report/<DATE>/post-market.md`
   - `report/<DATE>/post-market-signals.json`
8. Record provenance with this Skill as the analysis source:

   ```bash
   python3 script/trading_copilot.py llm-generation-manifest \
     --session post-market --date <DATE> --model <MODEL> \
     --prompt .codex/skills/tca-post-market-review/SKILL.md \
     --input report/<DATE>/daily-snapshot.json \
     --generated-output report/<DATE>/post-market.md \
     --generated-output report/<DATE>/post-market-signals.json
   ```

   Add one `--input` for each optional intraday, agent-research, or Vibe context artifact actually consumed; omit paths that do not exist.

9. Run the audited outer workflow:

   ```bash
   python3 script/trading_copilot.py post-market-deliver \
     --date <DATE> --sync-longbridge --execute-sync \
     --append-outcomes --append-lessons --append-self-review
   ```

10. Ensure the wrapper performs report/trade-plan validation, outcome backfill, position review when enabled, plan review, daily self-review, Feishu summary, and guarded `今日关注` replacement in its canonical order.
11. Never call `promote-lesson --apply` unless the user separately approves the exact promotion. When cc-connect invokes the workflow, the outer wrapper owns `guard -> send -> mark-sent`.

## Scheduled cc-connect handoff

When invoked by `ops/cc-connect/tca-post-market-wrapper.prompt.md`:

1. Never call `cc-connect send`.
2. Write the human-facing body to `runtime/cc-connect/out/post-market-summary.md` and metadata to `runtime/cc-connect/out/post-market-delivery.env`.
3. On skipped/failed/stale/validation failure, write a concise Chinese status with the exact reason and `SHOULD_MARK_SENT=0`, then stop.
4. After a successful delivery wrapper, run `python3 script/report_delivery_guard.py --kind post-market --date <DATE>` without `--mark-sent`.
5. If `should_send=false`, write the guard reason and `SHOULD_MARK_SENT=0`.
6. If `should_send=true`, copy `report/<DATE>/feishu-summary.md` exactly into the summary file and write:

   ```text
   DELIVERY_KIND=post-market
   DELIVERY_DATE=<DATE>
   SHOULD_MARK_SENT=1
   ```

7. End the Codex response with `FEISHU_SUMMARY_READY`. The outer shell sends first and marks sent only after send succeeds.

## Output handoff

Summarize the workflow/date, market and industry conclusion, plan validation outcomes, artifacts, data quality, position review, learning candidates, Swarm state, watchlist sync, delivery guard, and broker boundary in simplified Chinese.
