# Monitor Brief Workflow

Use when the user asks for an intraday monitoring summary.

## Preconditions

- Run or read `report/latest-monitor.json`.
- If running the scan, use:

```bash
python3 script/trading_copilot.py monitor-brief --state config/monitor_state.json --interval 5min
```

- Data fetch requires `TWELVE_DATA_API_KEY`.

## Steps

1. Read the wrapper status JSON and confirm `status=success`.
2. Read `report/latest-monitor.json`.
3. Group symbols by observation state.
4. Convert script statuses into scenarios with invalidation and risk.
5. Generate monitor signal sidecar when the observations should enter the dry-run loop:

```bash
python3 script/trading_copilot.py extract-monitor-signals --date <DATE>
```

This writes `report/<DATE>/monitor-signals.json`.

6. Optional dry-run only paper flow:

```bash
python3 script/trading_copilot.py validate-trade-plan --session monitor --date <DATE>
python3 script/trading_copilot.py paper-trade-preview --session monitor --date <DATE>
python3 script/trading_copilot.py paper-trade-submit --session monitor --date <DATE>
```

7. State data limitations and use `NO TRADE` for weak or incomplete setups.

## Output Shape

Required sections:

- 监控概览
- 临近触发
- 持仓风险
- 不满足规则的观察项
- 今日不交易条件

## Boundary

The monitor scan is an observation artifact. The agent may summarize risk but must not present order execution instructions.

`paper-trade-submit --session monitor --execute` is hard-disabled. Monitor candidates are `watch_only` dry-run observations unless a future explicitly gated workflow changes the contract.
