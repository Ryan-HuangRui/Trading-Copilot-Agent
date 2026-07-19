# 个股价格行为输出契约

## 必须输出的分析过程

输出必须包含以下部分，不能只给 `SETUP VALID / WATCH / NO TRADE` 结果：

1. `## 数据依据与质量`
   - 实际 provider/backend、数据截止时间、市场开闭市状态、使用周期。
   - 数据新鲜度、缺失、异常和 fallback。
2. `## 大盘与行业背景`
   - 大盘 risk regime、行业相对强弱、与个股方向是否一致。
3. `## 高周期结构`
   - 日线及相关 4h/1h 的趋势、震荡、过渡或 Barb Wire 判断。
   - HH/HL、LL/LH、区间、通道、缺口、突破或失败突破证据。
4. `## 关键位置`
   - 当前价和可核验的结构关键位。
   - 每个关键位为何重要，不得罗列未经计算的指标。
5. `## 位置上的价格行为`
   - 接受、拒绝、延续、衰竭、信号 K、跟随 K、成交量或波动证据。
6. `## 方法上下文`
   - 只使用 unified Trading Copilot knowledge pack 中 active 的方法卡。
   - 使用时必须写方法卡路径及其解释作用；不得读取或引用 `raw/`、SRT、YouTube/Bilibili URL。
   - 方法卡只能解释环境、结构和价格行为，不能单独形成 setup 或提升结论。
7. `## Setup 审核`
   - 精确 setup 文件名。
   - 已满足、未满足、未知条件。
   - breakout 必须额外审核 `tight_range_breakout_filter.md`。
8. `## 条件场景`
   - Bull/base/bear 路径。
   - 每条路径包含确认、失效和放弃条件。
9. `## 风险与计划约束`
   - 先定义失效，再讨论入场观察。
   - 禁止追价，禁止亏损加仓；无法定义风险或合理 RR 时为 `NO TRADE`。
10. `## 结论`
   - `SETUP VALID`：canonical setup、当前结构、确认和完整风险条件均成立，但仍是研究结论而非下单指令。
   - `WATCH`：环境或结构有潜力，但触发、确认或计划完整性不足。
   - `NO TRADE`：数据不可靠、regime 不清、Barb Wire、无有效 setup、失效不可定义或 RR 不足。

## 证据纪律

- 仅使用 canonical approved rules 形成交易结论。
- 默认价格上下文为 Longbridge-first 的 `1day`、`1h`、`15min`、`5min`；缺失周期必须明确披露，不得用方法卡补造价格事实。
- 新闻、情绪、agent research 和 Swarm 只能补充背景、风险或降级理由。
- 不得编造实时价格、指标、公司事件或来源。
- 区分观察事实、计算结果、推断与未知项。
- 用户已有持仓时可以分析持仓风险，但不得把持仓事实当作继续持有或加仓的理由。
