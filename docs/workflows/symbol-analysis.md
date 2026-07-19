# Symbol Analysis Workflow

Use when the user asks for an ad-hoc analysis of a single ticker.

## Preconditions

- For current or recent analysis, prepare or read real market data first.
- Prefer `python3 script/trading_copilot.py symbol-analysis-context --symbol <SYMBOL>` when the connected Longbridge app/MCP is unavailable or incomplete. The repository path is Longbridge-first and fetches `1day`, `1h`, `15min`, and `5min` by default.
- Prefer an existing `report/<DATE>/daily-snapshot.json` or `report/<DATE>/pre-market-context.json`.
- If no fresh data is available, say that no concrete price conclusion can be made.
- Use `canonical rulebook/` for setup and risk conclusions.
- Use only active method cards from the unified Trading Copilot knowledge pack for analysis context. Runtime analysis must not read raw transcripts or video sources.

## Steps

1. Identify the symbol, analysis date, and whether the user has a position.
2. Locate the latest relevant snapshot/context artifact.
3. Check market regime and risk preconditions under `canonical rulebook/global/`.
4. Check relevant setup files under `canonical rulebook/setups/`.
5. Read the relevant compiled method card and cite its path when it materially informs the analysis.
6. Write the memo with scenarios rather than directives.

## Output Shape

Use simplified Chinese unless the user asks otherwise.

Required sections:

- 数据依据
- 大盘与行业背景
- 当前结构
- 可观察场景
- 触发与失效
- 风险与仓位约束
- 结论

The conclusion must be `NO TRADE` when data is stale, setup quality is insufficient, or refined rules do not support the thesis.

## Prohibited Output

- Do not say "buy now" or "sell now".
- Do not infer missing prices, indicators, or news.
- Do not treat S&P 500 dynamic candidates as recommendations.
