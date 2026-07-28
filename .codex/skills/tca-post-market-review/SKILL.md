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
3. Resolve the snapshot date. Capture the user's current positions through the connected apps using only read-only capabilities:
   - IBKR: open positions, account financial metrics, and currency balances;
   - Longbridge: current stock positions and account balance.
   Persist only structured payloads without account identifiers under
   `runtime/account/<DATE>/plugin-inputs/`, then run:

   ```bash
   python3 script/trading_copilot.py plugin-account-snapshot \
     --date <DATE> \
     --ibkr-positions runtime/account/<DATE>/plugin-inputs/ibkr-positions.json \
     --ibkr-account runtime/account/<DATE>/plugin-inputs/ibkr-account.json \
     --ibkr-balances runtime/account/<DATE>/plugin-inputs/ibkr-balances.json \
     --longbridge-positions runtime/account/<DATE>/plugin-inputs/longbridge-positions.json \
     --longbridge-account runtime/account/<DATE>/plugin-inputs/longbridge-account.json
   ```

   Omit unavailable inputs. Use the normalized snapshot when at least one broker
   succeeds; otherwise disclose the failure and allow read-only Longbridge CLI fallback.
   Never call order, instruction, submit, cancel, replace, or mutation capabilities.
   Compare the normalized holding symbols with `daily-snapshot.json`. If any are
   missing, rerun step 1 with one `--extra-symbol <SYMBOL>` per missing holding before
   writing the report. If a symbol still cannot be fetched, retain it with an explicit
   `price_evidence_missing` limitation rather than dropping the position.
4. Capture today's completed trades, read-only:
   - IBKR: trades for `TODAY`;
   - Longbridge: today's executions and today's orders, where orders are context
     only and executions are the completed-trade facts.
   Persist structured payloads under `runtime/account/<DATE>/plugin-inputs/`, then run:

   ```bash
   python3 script/trading_copilot.py plugin-trade-snapshot \
     --date <DATE> \
     --ibkr-trades runtime/account/<DATE>/plugin-inputs/ibkr-trades.json \
     --longbridge-executions runtime/account/<DATE>/plugin-inputs/longbridge-executions.json \
     --longbridge-orders runtime/account/<DATE>/plugin-inputs/longbridge-orders.json \
     --longbridge-cli-fallback
   ```

   Omit unavailable plugin input options. Use the CLI fallback only for Longbridge
   read-only order/execution listing. Preserve a failed or unauthorized broker source
   in `sources`; partial coverage must be disclosed and must not be interpreted as
   “no trades”. Never append these observed real-account fills automatically to
   `runtime/journal/trades.jsonl`.
5. Read all existing `next_agent_inputs`, plus the normalized account and trade snapshots when available:
   - this Skill and canonical rulebook;
   - the unified Trading Copilot knowledge pack and relevant method card, when supplied;
   - `report/<DATE>/daily-snapshot.json`;
   - optional intraday Markdown/state/events;
   - agent research and completed Vibe evidence.
6. Review in this order: broad market and major industries, structure changes, key-level behavior, setup validation/failure, intraday plan outcomes, today's real trades, current holdings, then next-session observation scenarios. Use the unified knowledge pack and relevant method card directly for method context; cite the card. Raw sources are compiler-only and unavailable to the report workflow; record a material gap or conflict rather than opening raw content. Never let a method card independently raise `execution_status`.
   Add `当日交易复盘` that evaluates each execution against the pre-market plan,
   actual 5m/15m price evidence, setup trigger, invalidation, no-chase rule, position
   change, commission, and realized P&L when supplied by the broker. Separate facts
   from inference, classify plan linkage as `planned`, `unplanned`, or `unknown`, and
   review entry/exit timing, repeated trading, loss-cutting, profit-taking, and process
   discipline. Missing intraday bars or broker scopes must remain explicit limitations.
   Add `持仓与组合风险复盘` covering broker coverage, per-holding daily behavior,
   plan/invalidation alignment, cross-broker overlap, concentration, leveraged-ETF
   exposure, missing prices, and next-session human-review scenarios. Preserve
   broker-level provenance and aggregate duplicate symbols only for portfolio exposure.
7. Reconcile any pending pre-market Swarm run. Consume only completed, fresh, hash-validated artifacts. Preserve pending, failed, stale, contradictions, and limitations explicitly.
8. If an unresolved abnormal move, material evidence conflict, or repeated thesis failure still needs independent audit, use `$tca-swarm-research` for at most one target. Do not block the report while it runs.
9. Write:
   - `report/<DATE>/post-market.md`
   - `report/<DATE>/post-market-signals.json`
10. Record provenance with this Skill as the analysis source:

   ```bash
   python3 script/trading_copilot.py llm-generation-manifest \
     --session post-market --date <DATE> --model <MODEL> \
     --prompt .codex/skills/tca-post-market-review/SKILL.md \
     --input report/<DATE>/daily-snapshot.json \
     --generated-output report/<DATE>/post-market.md \
     --generated-output report/<DATE>/post-market-signals.json
   ```

   Add `--input runtime/account/<DATE>/plugin-account-snapshot.json` when consumed.
   Add `--input runtime/account/<DATE>/plugin-trade-snapshot.json` when consumed.
   Add one `--input` for each optional intraday, agent-research, or Vibe context artifact actually consumed; omit paths that do not exist.

11. Run the audited outer workflow:

   ```bash
   python3 script/trading_copilot.py post-market-deliver \
     --date <DATE> \
     --account-snapshot runtime/account/<DATE>/plugin-account-snapshot.json \
     --sync-longbridge --execute-sync \
     --append-outcomes --append-lessons --append-self-review
   ```

   Omit `--account-snapshot` only when no plugin snapshot was produced.

12. Ensure the wrapper performs report/trade-plan validation, outcome backfill, position review when enabled, plan review, daily self-review, Feishu summary, and guarded `今日关注` replacement in its canonical order.
13. Never call `promote-lesson --apply` unless the user separately approves the exact promotion. When cc-connect invokes the workflow, the outer wrapper owns `guard -> send -> mark-sent`.

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
