# Trading-Copilot Post-Market Wrapper Prompt

You are running the scheduled Trading-Copilot-Agent post-market workflow from the cc-connect wrapper.

Hard boundaries:

- Do not place trades.
- Do not call broker trading APIs.
- Do not output deterministic buy/sell instructions.
- Longbridge real-account operations must stay read-only.
- Do not call `cc-connect send`; the scheduler wrapper owns Feishu delivery outside Codex.

Output files for the wrapper:

- Human-facing Feishu body: `runtime/cc-connect/out/post-market-summary.md`
- Delivery metadata: `runtime/cc-connect/out/post-market-delivery.env`

Run this workflow:

1. Prepare the post-market snapshot and optional agent research:

```bash
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols --include-agent-research
```

Parse the JSON output. If `skipped=true`, write a concise Chinese status summary to `runtime/cc-connect/out/post-market-summary.md`, write `SHOULD_MARK_SENT=0` to `runtime/cc-connect/out/post-market-delivery.env`, and stop. If `status=failed` or `agent_research.status=failed`, write the failure reason and artifact paths to the same summary file, write `SHOULD_MARK_SENT=0`, and stop.

If `report/<SNAPSHOT_DATE>/daily-snapshot.json` has `stale_data=true`, write a data-not-ready status summary, write `SHOULD_MARK_SENT=0`, and stop. Do not generate a formal post-market review from stale data.

2. Read:

- `agent/post_market_analysis_prompt.md`
- canonical rulebook: run `python3 -c 'from pathlib import Path; from script.knowledge_source import canonical_rulebook_input; print(canonical_rulebook_input(Path.cwd()))'` and read the returned path
- `report/<SNAPSHOT_DATE>/daily-snapshot.json`
- `report/<SNAPSHOT_DATE>/intraday.md` when present
- `runtime/intraday/<SNAPSHOT_DATE>/state.json` and `events.jsonl` when present
- `report/<SNAPSHOT_DATE>/agents/**` artifacts when present

Agent research artifacts are evidence only. Intraday monitor artifacts can validate, negate, or explain prior plans, but cannot become order inputs or independently raise `execution_status`.

Generate:

- `report/<SNAPSHOT_DATE>/post-market.md`
- `report/<SNAPSHOT_DATE>/post-market-signals.json`

`post-market.md` must include `## 盘中监控回顾`, covering intraday focus pool, important state changes, sent alerts, pre-market plan validation/negation, untriggered/waiting states, and data/process issues. If intraday artifacts are missing, say so explicitly.

`post-market-signals.json` must match the Markdown next-day observation list. Any actionable signal must include Trade Plan Card fields: `setup_files`, `trigger`, `invalidation`, `risk`, `entry`, `stop`, `take_profit`, and `execution_rules`. If setup quality or risk conditions are insufficient, use `watch_only` or `no_trade`.

3. Record LLM generation provenance:

```bash
python3 script/trading_copilot.py llm-generation-manifest --session post-market --date <SNAPSHOT_DATE> --model codex --prompt agent/post_market_analysis_prompt.md --input report/<SNAPSHOT_DATE>/daily-snapshot.json --generated-output report/<SNAPSHOT_DATE>/post-market.md --generated-output report/<SNAPSHOT_DATE>/post-market-signals.json
```

If this fails, write a concise Chinese failure summary and `SHOULD_MARK_SENT=0`, then stop.

4. Run the deterministic delivery wrapper:

```bash
python3 script/trading_copilot.py post-market-deliver --date <SNAPSHOT_DATE> --sync-longbridge --execute-sync --append-outcomes --append-lessons --append-self-review
```

This wrapper runs validation, data quality, outcome backfill, focus selection, journal extraction, read-only account/position review, plan review, learning review, daily self-review, daily workflow review, Longbridge watchlist sync, and `feishu-summary`. If it returns `status=failed`, write a concise Chinese failure summary with reason and manifest path, write `SHOULD_MARK_SENT=0`, and stop. Do not run `promote-lesson --apply`.

5. Run the delivery guard, but do not mark sent yet:

```bash
python3 script/report_delivery_guard.py --kind post-market --date <SNAPSHOT_DATE>
```

If `should_send=false`, write a concise Chinese skipped/status summary to `runtime/cc-connect/out/post-market-summary.md`, include the guard reason, write `SHOULD_MARK_SENT=0`, and stop.

If `should_send=true`, copy the exact Feishu-ready body from `report/<SNAPSHOT_DATE>/feishu-summary.md` into `runtime/cc-connect/out/post-market-summary.md`. The summary must include workflow/date, LLM generation, generated artifacts, validation, data-quality, focus-selection, journal append counts, intraday monitor review, workflow-review artifact paths, position review, plan-review/learning-review/self-review, agent decision artifact paths, Longbridge sync status, and boundary notes. It should preserve the market/industry section before symbol-level detail.

Write this metadata file exactly:

```text
DELIVERY_KIND=post-market
DELIVERY_DATE=<SNAPSHOT_DATE>
SHOULD_MARK_SENT=1
```

The outer wrapper will send the summary and then run `report_delivery_guard.py --mark-sent`.

End your final response with a concise Chinese run report and the marker:

```text
FEISHU_SUMMARY_READY
```
