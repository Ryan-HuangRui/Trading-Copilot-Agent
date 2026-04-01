# Setup: Trading Range Fade

## Scope (MANDATORY)
- Market: US Stocks / ETFs
- Timeframe: 1day / 4h / 1h
- Direction: Mean Reversion (边界反转)
- Style: Price Action

## Market Preconditions
- 市场处于明确 TR（高低点可画出）
- K线重叠增加，延续性下降
- 中轴附近噪音高，不做方向押注

## Entry Triggers
### Short at Range High
- 到达上沿并出现拒绝（上影/失败突破）
- 或出现二次上冲失败（double top 失败）

### Long at Range Low
- 到达下沿并出现承接（下影/失败跌破）
- 或出现二次下探失败（double bottom 失败）

### Confirmation
- 至少一项：
  - 反向信号棒
  - 微结构反向 BOS
  - 回踩不破边界

## Invalidation / Failure Conditions
- 边界被强趋势棒有效突破并跟随
- 回到区间内失败，变成趋势日

## Risk Management
- Stop: 边界外 + 结构缓冲
- 单笔风险 <= 1%
- 不在区间中部开仓

## Profit Taking
- TP1: 区间中轴
- TP2: 区间对侧边界
- 若趋势动能突然增强，提前保护利润

## Common Mistakes
- 在中部交易（R/R 差）
- 把第一次触边当高胜率信号（忽略确认）
- 忽略大盘 regime，逆势硬做

## Source Notes
- PriceActions docs/18-trading-ranges/*
- PriceActions docs/10-buying-selling-pressure/*
- PriceActions docs/19-support-resistance/*
