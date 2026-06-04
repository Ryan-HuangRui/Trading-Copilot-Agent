---
name: intraday-tracker
description: Use for Trading-Copilot-Agent read-only intraday plan tracking, including pre-market topN follow-up, manual watchlist observation, intraday Markdown logging, state/event review, and notification-candidate summaries. This skill must not place broker orders.
---

# Intraday Tracker

Use this skill when the user asks for intraday monitoring, pre-market plan follow-up, or important state-change notifications.

## Safety

- This is a read-only analysis workflow.
- Do not place real trades or paper trades from this skill.
- Do not output deterministic buy/sell instructions.
- Use scenarios, trigger state, invalidation state, blocked reasons, and `NO TRADE` when quality is insufficient.
- Paper dry-run and paper execution workflows remain separate `trading_copilot.py` commands.

## Phase 1 Command

```bash
python3 script/trading_copilot.py intraday-tracker --date <DATE> --top-n 5
```

The tracker reads:

- `report/<DATE>/pre-market-signals.json`
- `config/intraday_watchlist.json` when present
- `report/latest-monitor.json`
- existing `report/<DATE>/intraday.md`
- existing `runtime/intraday/<DATE>/state.json`
- existing `runtime/intraday/<DATE>/events.jsonl`

It writes:

- `report/<DATE>/intraday.md`
- `runtime/intraday/<DATE>/state.json`
- `runtime/intraday/<DATE>/events.jsonl`

## Output Use

- Treat `intraday.md` as the human-readable rolling log.
- Treat `state.json` as the machine-readable prior state source.
- Treat `events.jsonl` as notification candidates for cc connect or another delivery layer.
- Only important state changes should produce notification events.
