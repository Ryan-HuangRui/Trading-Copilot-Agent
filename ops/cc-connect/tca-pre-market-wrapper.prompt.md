# Trading-Copilot Pre-Market Wrapper Prompt

You are running the scheduled Trading-Copilot-Agent pre-market workflow from the cc-connect wrapper.

Hard boundaries:

- Do not place trades.
- Do not call broker trading APIs.
- Do not output deterministic buy/sell instructions.
- Longbridge real-account operations must stay read-only.
- Do not call `cc-connect send`; the scheduler wrapper owns Feishu delivery outside Codex.

Output files for the wrapper:

- Human-facing Feishu body: `runtime/cc-connect/out/pre-market-summary.md`
- Delivery metadata: `runtime/cc-connect/out/pre-market-delivery.env`

Run this workflow:

1. Prepare pre-market context and optional agent research:

```bash
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day --include-agent-research
```

Parse the JSON output. If `skipped=true`, write a concise Chinese status summary to `runtime/cc-connect/out/pre-market-summary.md`, write `SHOULD_MARK_SENT=0` to `runtime/cc-connect/out/pre-market-delivery.env`, and stop. If `status=failed` or `agent_research.status=failed`, write the failure reason and artifact paths to the same summary file, write `SHOULD_MARK_SENT=0`, and stop.

The wrapper may generate `report/<PRE_MARKET_DATE>/external-disclosures/trump-trades.json`. If `external_disclosures.status=failed` or evidence is empty, disclose the data gap in the generated report and summary; do not invent disclosure facts.

2. Read:

- `agent/daily_analysis_prompt.md`
- canonical rulebook: run `python3 -c 'from pathlib import Path; from script.knowledge_source import canonical_rulebook_input; print(canonical_rulebook_input(Path.cwd()))'` and read the returned path
- `report/<PRE_MARKET_DATE>/pre-market-context.json`
- `report/<PRE_MARKET_DATE>/external-disclosures/trump-trades.json` when present
- `report/<PRE_MARKET_DATE>/agents/**` artifacts when present

Generate:

- `report/<PRE_MARKET_DATE>/exec-brief.md`
- `report/<PRE_MARKET_DATE>/pre-market.md`
- `report/<PRE_MARKET_DATE>/pre-market-signals.json`

`pre-market-signals.json` must match the Markdown focus symbols. Any actionable signal must include Trade Plan Card fields: `setup_files`, `trigger`, `invalidation`, `risk`, `entry`, `stop`, `take_profit`, and `execution_rules`.

`exec-brief.md` and `pre-market.md` must contain `## 消息层汇总` and a separate `### 特朗普持仓与交易变化` section. That section may only summarize verifiable OGE/Open Cabinet/Quiver/InsiderCat disclosure facts, overlapping symbols, and data limitations. It cannot become a trading signal and cannot raise any symbol's `execution_status`. If no verifiable latest disclosure data is available, say so explicitly. If setup quality or risk conditions are insufficient, use `watch_only` or `no_trade`.

3. Record LLM generation provenance:

```bash
python3 script/trading_copilot.py llm-generation-manifest --session pre-market --date <PRE_MARKET_DATE> --model codex --prompt agent/daily_analysis_prompt.md --input report/<PRE_MARKET_DATE>/pre-market-context.json --generated-output report/<PRE_MARKET_DATE>/exec-brief.md --generated-output report/<PRE_MARKET_DATE>/pre-market.md --generated-output report/<PRE_MARKET_DATE>/pre-market-signals.json
```

If this fails, write a concise Chinese failure summary and `SHOULD_MARK_SENT=0`, then stop.

4. Run the deterministic delivery wrapper:

```bash
python3 script/trading_copilot.py pre-market-deliver --date <PRE_MARKET_DATE> --sync-longbridge --execute-sync
```

This wrapper runs validation, data quality, focus selection, journal extraction, read-only account/position review, Longbridge watchlist sync, and `feishu-summary`. If it returns `status=failed`, write a concise Chinese failure summary with reason and manifest path, write `SHOULD_MARK_SENT=0`, and stop.

5. Run the delivery guard, but do not mark sent yet:

```bash
python3 script/report_delivery_guard.py --kind exec-brief --date <PRE_MARKET_DATE>
```

If `should_send=false`, write a concise Chinese skipped/status summary to `runtime/cc-connect/out/pre-market-summary.md`, include the guard reason, write `SHOULD_MARK_SENT=0`, and stop.

If `should_send=true`, copy the exact Feishu-ready body from `report/<PRE_MARKET_DATE>/feishu-summary.md` into `runtime/cc-connect/out/pre-market-summary.md`. The summary must include workflow/date, LLM generation, generated artifacts, validation, data-quality, focus-selection, journal append counts, position review, agent decision artifact paths, Longbridge sync status, and boundary notes.

Write this metadata file exactly:

```text
DELIVERY_KIND=exec-brief
DELIVERY_DATE=<PRE_MARKET_DATE>
SHOULD_MARK_SENT=1
```

The outer wrapper will send the summary and then run `report_delivery_guard.py --mark-sent`.

End your final response with a concise Chinese run report and the marker:

```text
FEISHU_SUMMARY_READY
```
