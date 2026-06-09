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
- Plain `paper-trade-submit --session monitor --execute` remains forbidden.

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

## Active Notification

```bash
python3 script/intraday_event_notify.py --date <DATE> --mark-sent
bash ops/cc-connect/tca-intraday-notify.sh <DATE>
```

`intraday_event_notify.py` builds `report/<DATE>/intraday-notification.md` only when unsent `notify=true` events exist. It records sent ids in `runtime/intraday/<DATE>/sent-events.json`.

`tca-intraday-notify.sh` can be called by a Codex scheduled task. It builds a temporary monitor state from pre-market topN, `config/intraday_watchlist.json`, and `config/monitor_state.json`, then runs monitor/tracker first unless `TCA_INTRADAY_SKIP_MONITOR=1`, and sends the generated notification through cc-connect only when unsent notify events exist.

## Phase 2 Dry-Run Command

```bash
python3 script/trading_copilot.py intraday-dry-run --date <DATE>
python3 script/trading_copilot.py intraday-review-append --date <DATE>
```

This validates Codex-reviewed monitor decisions, builds paper previews, prepares a paper submit dry-run, and writes a Feishu-ready monitor summary. It must not pass `--execute`.

Before Codex writes `report/<DATE>/monitor-signals.json`, build `report/<DATE>/intraday-opportunity-context.json` and use `observation_scans` / `sidecar_template.signals` as the all-symbol decision input. `observation_scans` include latest/recent bar evidence. `candidate_scans` are deterministic highlights only.

After Codex writes or reviews `report/<DATE>/monitor-signals.json`, append the review summary into `report/<DATE>/intraday.md` with `intraday-review-append` so the daily intraday report includes state tracking, the all-symbol input scope, and the reason symbols stayed `watch_only`, became `no_trade`, or became `conditional_executable`.

## Phase 3 Dedicated Paper Entry

```bash
python3 script/trading_copilot.py intraday-paper-entry --date <DATE> --require-validation
python3 script/trading_copilot.py intraday-paper-entry --date <DATE> --require-validation --execute --paper-execution-config config/paper_execution.local.json
```

Only use `--execute` after reviewed dry-run evidence exists and `config/paper_execution.local.json` enables both `broker_writes_enabled=true` and `allow_intraday_entry_submit=true`.

## Paper Lifecycle Audit

```bash
python3 script/trading_copilot.py paper-lifecycle --date <DATE>
python3 script/trading_copilot.py intraday-lifecycle-append --date <DATE>
```

`intraday-lifecycle-append` reads existing paper lifecycle artifacts, appends a concise audit section to `report/<DATE>/intraday.md`, and writes `report/<DATE>/intraday-lifecycle-summary.json` with `should_notify` for Feishu filtering. It does not call broker APIs.

## Output Use

- Treat `intraday.md` as the human-readable rolling log.
- Treat `state.json` as the machine-readable prior state source.
- Treat `events.jsonl` as notification candidates for cc connect or another delivery layer.
- Only important state changes should produce notification events.
- Do not resend events already present in `sent-events.json`.
