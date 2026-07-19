---
name: tca-swarm-research
description: Run the repo-only Vibe Trading Swarm as an independent evidence-research workflow for Trading-Copilot-Agent. Use for Swarm 深度研究、多角色研究、正反论证、独立取证、thesis audit, abnormal-move evidence review, or a bounded escalation explicitly requested by the pre-market/post-market/price-action skills. Do not use for routine ticker quotes, normal daily analysis, intraday tracking, trade execution, or watchlist updates.
---

# TCA Swarm Research

## Boundary

Use Swarm as an independent research process, not a trading-decision or execution process. It may collect evidence, challenge a thesis, preserve contradictions, and produce a research-quality label. It must not create a Trade Plan Card, raise `execution_status`, edit the canonical rulebook, mutate watchlists, write broker state, or place orders.

The approved preset uses provider-qualified model `openai-codex/gpt-5.6-sol`; the Codex adapter sends the effective model name `gpt-5.6-sol`. The repo-only Vibe MCP defaults reasoning effort to `medium`. Never use the invalid bare name `5.6-sol`. Preserve the effective provider, model, and reasoning setting in the research audit when available.

## Required contract

Read `docs/workflows/vibe-swarm-research.md` completely before starting or reconciling a run. Use [references/research-contract.md](references/research-contract.md) for the synthesis and handoff requirements.

## Start or reconcile

1. Require either an explicit user request or one bounded escalation from another TCA Skill. Scheduled pre/post-market sessions may escalate at most one symbol.
2. Form a narrow research objective with target, market, evidence cutoff, decisive unknowns, and falsification question. Do not submit a generic “research this stock” objective.
3. Call `list_swarm_presets` and require `tca_research_review`.
4. Call `run_swarm` with only the approved preset and variables `target`, `market`, `objective`, and `as_of`. The adapter starts asynchronously and returns a `run_id`.
5. Immediately persist `pending` through:

   ```bash
   python3 script/trading_copilot.py vibe-research-context \
     --date <DATE> --session <SESSION> --run-id <RUN_ID> \
     --target <TARGET> --symbol <SYMBOL> --objective <OBJECTIVE> \
     --as-of <DATE> --status pending \
     --provider openai-codex --model gpt-5.6-sol \
     --limitation "reasoning_effort=medium"
   ```

6. For an explicitly interactive deep-research request, poll `get_swarm_status` until terminal within a reasonable bounded wait. For scheduled pre-market, return pending immediately. Post-market may reconcile existing runs without blocking unrelated report generation.
7. On completion, call `get_run_result`, preserve the raw response under `report/research/vibe-swarm/<RUN_ID>/result.json`, and write a simplified-Chinese `summary.md` satisfying the research contract.
8. Re-index the completed artifacts with hashes, provider/model, confidence, limitations, and one label: `RESEARCH_ONLY`, `WATCH`, or `NO_TRADE`.
9. On failure, preserve the error. Use `retry_run` only for an approved failed run, and `reap_stale_runs` only for an actually stale orphan. Never invent a replacement conclusion.

## Handoff

Return the run id, preset, target, objective, as-of, status, effective provider/model, reasoning effort, artifact paths, accepted/rejected/unresolved claims, confidence, limitations, and research-quality label. State explicitly that the result is secondary evidence and must be re-evaluated by the calling TCA analysis Skill against Longbridge-first price data and the canonical rulebook.
