# Setup: Stop Run + BOS Reversal

## Scope (MANDATORY)
- Market: US Stocks / ETFs
- Timeframe: HTF key level + LTF confirmation
- Direction: Reversal to continuation
- Style: Price Action first (SMC terms as辅助)

## Concept
- 本策略核心不是“猜扫流动性”，而是识别：
  1) 关键位外的止损触发（Stop Run）
  2) 随后出现反向结构破坏（BOS / MSS）
  3) 回踩确认后再执行

## Market Preconditions
- 价格到达以下任一位置：
  - 前高/前低（明显止损池）
  - 区间边界（TR high/low）
  - HTF 支撑/阻力
- 环境要求：
  - 非极端乱序（Barb Wire 直接 NO TRADE）
  - 有可定义的失效位

## Entry Triggers
1. Stop Run 发生
   - 价格短暂刺破关键位后快速收回
   - 常见表现：长影线、假突破、收盘回到区间内
2. 结构确认（BOS / MSS）
   - 反向突破最近一段微结构高/低
   - 至少 1 根有效 Trend Bar 支持
3. 回踩确认
   - 回踩 BOS 位、供应/需求区或小缺口后未失守
4. 执行
   - 仅在触发-失效-仓位三件套完整时入场

## Invalidation / Failure Conditions
- 回踩后重新跌破/突破 Stop Run 极值并收盘确认
- BOS 后无延续，转为重叠震荡且优势消失
- 大盘/板块方向与该信号强冲突

## Risk Management
- Stop: 放在 Stop Run 极值外
- 单笔风险: <= 1%
- 若止损距离过大导致仓位不可接受 -> NO TRADE

## Profit Taking
- 第一目标：回到区间中轴或最近反向流动性池
- 第二目标：区间另一侧 / 前高前低
- 采用分批止盈，避免单点预测

## Common Mistakes
- 把“长影线”直接当反转（未等 BOS）
- 在区间中部追单
- 失效位定义模糊，导致风险不可控

## Source Notes
- PriceActions docs/qa-summary/about-liquidity-grab.md
- PriceActions docs/18-trading-ranges/*
- PriceActions docs/19-support-resistance/*
