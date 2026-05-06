# Agent instructions (scope: agent/)

## Scope and layout
- `daily_analysis_prompt.md`: daily report contract for context-driven pre-market analysis.
- `post_market_analysis_prompt.md`: post-market review contract for context-driven close analysis.
- Legacy OpenClaw/general coaching prompts live under `docs/legacy-prompts/` and are not loaded by Codex App automation.

## Conventions
- Keep simplified Chinese as the default user-facing output language unless a prompt explicitly requires English.
- Keep the fixed analysis order: market environment, structure, key levels, behavior at levels, then trade logic.
- Use `knowledge/refined/` as the rule source for conclusions. Do not let `knowledge/source/` drive a trading conclusion directly.
- For pre-market reports, require both `report/<DATE>/exec-brief.md` and `report/<DATE>/pre-market.md`.
- For post-market reports, require `report/<SNAPSHOT_DATE>/post-market.md`.
- Pre-market analysis should read `report/<DATE>/pre-market-context.json`, which references the previous trading day's `daily-snapshot.json`.
- Post-market analysis should read `report/<SNAPSHOT_DATE>/daily-snapshot.json` directly.
- Every executable candidate needs setup file, trigger, invalidation, and risk constraint.
- Use `NO TRADE` when regime is unclear, Barb Wire/Tight Trading Range, data is insufficient, or global rules fail.
- For scheduled pre/post-market analysis, run the trading-day guard first and stop on non-trading days.

## Do not
- Do not add deterministic profit claims or certainty language.
- Do not remove risk responsibility or invalidation requirements.
- Do not expand prompts with long raw knowledge excerpts; reference files by path instead.
