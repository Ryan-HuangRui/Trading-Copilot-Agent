# Agent instructions (scope: agent/)

## Scope and layout
- `daily_analysis_prompt.md`: thin compatibility trigger for `$tca-pre-market-analysis`.
- `post_market_analysis_prompt.md`: thin compatibility trigger for `$tca-post-market-review`.
- `intraday_opportunity_prompt.md`: Codex-reviewed monitor opportunity contract for writing `monitor-signals.json` from `intraday-opportunity-context.json`.
- Legacy OpenClaw/general coaching prompts live under `docs/legacy-prompts/` and are not loaded by Codex App automation.

## Conventions
- Keep simplified Chinese as the default user-facing output language unless a prompt explicitly requires English.
- Keep the fixed analysis order in the dedicated Skill contracts, not in these trigger prompts.
- Use the canonical rulebook path provided in `next_agent_inputs` as the rule source for conclusions. Raw vault sources must not drive a trading conclusion directly.
- Use the unified Trading Copilot knowledge pack path in `next_agent_inputs` as the only runtime method source. Cite active method-card paths when used; never read raw transcripts or video sources during report, symbol, or intraday analysis.
- When `next_agent_inputs` contains `vibe-research-context.json`, the dedicated Skill consumes only completed hash-validated runs after normal analysis. Vibe is secondary evidence and cannot raise execution status, replace Longbridge-first price data, or override the canonical rulebook.
- A scheduled pre/post-market analysis may escalate at most one focus symbol to Vibe Swarm when a material evidence conflict or gap remains. Pending/failed research must not block the main report.
- For pre-market reports, require both `report/<DATE>/exec-brief.md` and `report/<DATE>/pre-market.md`.
- For post-market reports, require `report/<SNAPSHOT_DATE>/post-market.md`.
- Pre-market analysis should read `report/<DATE>/pre-market-context.json`, which references the previous trading day's `daily-snapshot.json`.
- Post-market analysis should read `report/<SNAPSHOT_DATE>/daily-snapshot.json` directly.
- Every executable candidate needs setup file, trigger, invalidation, and risk constraint.
- For monitor-session opportunities, read `report/<DATE>/intraday-opportunity-context.json` first. `observation_scans` / `sidecar_template.signals` are the all-symbol decision inputs and include multi-timeframe `price_evidence`; deterministic `candidate_scans` are evidence only. Only the Codex review prompt may write a `conditional_executable` monitor Trade Plan Card.
- After writing monitor-session `monitor-signals.json`, run `intraday-decision-coverage` before trade-plan validation so every observation symbol has an explicit Codex decision.
- Use `NO TRADE` when regime is unclear, Barb Wire/Tight Trading Range, data is insufficient, or global rules fail.
- For scheduled pre/post-market analysis, run the trading-day guard first and stop on non-trading days.

## Do not
- Do not add deterministic profit claims or certainty language.
- Do not remove risk responsibility or invalidation requirements.
- Do not expand these prompts with analysis rules, templates, setup lists, or raw knowledge excerpts; keep them as explicit Skill triggers.
