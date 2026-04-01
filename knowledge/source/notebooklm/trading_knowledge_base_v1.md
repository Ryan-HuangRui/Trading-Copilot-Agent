# 交易知识库 (Trading Knowledge Base) v1.0

## 1. Trading Philosophy (交易哲学 / 核心原则)

### 核心目标与思维模式
*   **生存第一：** 交易的首要目标是“生存”和“避免大亏”。对于新手，首要任务是控制亏损，而非盈利。[来源：方方土PriceAction - 踏上交易之路(1), 踏上交易之路(8)]
*   **机构跟随：** 市场由机构（Smart Money）主导。零售交易者的目标是识别机构的足跡（如大实体K线、缺口、流动性掠夺）并跟随，而非预测市场或与机构对抗。[来源：Smart Money Concepts (SMC): A Complete Trading Guide, 方方土PriceAction - 价格行为学专题分享]
*   **概率思维 (40-60定律)：** 永远假设任何一笔交易都有 **40% 的概率会亏损**。不要追求完美胜率，而应追求正向的数学期望（概率 × 盈亏比）。[来源：方方土PriceAction - 为什么亏损必然会发生？40—60定律]
*   **不确定性：** 接受入场和离场的不确定性。不存在完美的入场和完美的止损。如果市场证明前提不再成立，必须诚实地认错离场。[来源：方方土PriceAction - 踏上交易之路(3)]

### 胜率 vs 盈亏比
*   **剥头皮 (Scalping)：** 高胜率 (60%-90%)，低盈亏比 (1:1 或更低)。[来源：方方土PriceAction - 收益率是结果，不是目标]
*   **波段交易 (Swing Trading)：** 低胜率 (40%-60%)，高盈亏比 (至少 2:1)。**新手强烈建议从波段交易开始**。[来源：方方土PriceAction - 踏上交易之路(8), Al Brooks Setups 1]

### “不交易”的原则
*   **看不懂的行情：** 如果遇到看不懂或不会做的行情，应停止交易，接受能力有限的事实。害怕错过 (FOMO) 是对自己赚钱能力没有信心的表现。[来源：方方土PriceAction - 踏上交易之路(1)]
*   **窄震荡区间：** 在非常窄的震荡区间 (Tight Trading Range / Barb Wire) 内，不要进行交易，直到出现明确突破。[来源：Al Brooks Setups 1, 方方土PriceAction - 市场周期的4个阶段]

---

## 2. Market Regime & Preconditions (市场环境)

在执行任何 Setup 前，必须先界定当前的市场周期。

### 周期分类与特征
1.  **突破 (Breakout / Spike)：**
    *   **特征：** 连续的大实体趋势K线，收盘价在最高/最低处，K线之间重叠少，有缺口 (Gaps)。
    *   **策略：** 只做顺势 (Always In)。追涨杀跌。[来源：方方土PriceAction - 市场周期的4个阶段, 突破专题(2)]
2.  **窄通道 (Tight Channel)：**
    *   **特征：** 紧贴趋势线运行，回调幅度小且持续时间短 (1-3根K线)。
    *   **策略：** 只做顺势。这是强趋势，第一次反转尝试有 80% 概率失败。[来源：方方土PriceAction - 市场周期的4个阶段]
3.  **宽通道 (Broad Channel)：**
    *   **特征：** 回调幅度深，持续时间长，但关键高低点仍在抬高(多头)或降低(空头)。
    *   **策略：** 顺势为主，也可在区间边缘做逆势 (Scalp)。[来源：方方土PriceAction - 市场周期的4个阶段]
4.  **震荡区间 (Trading Range)：**
    *   **特征：** 缺乏方向，多空平衡，大阳线后紧跟大阴线，均值回归。
    *   **策略：** 高抛低吸 (Buy Low, Sell High)。切勿追单。[来源：方方土PriceAction - 市场周期的4个阶段]

### 关键前置条件 (Context)
*   **SMC 视角：** 寻找高时间周期 (HTF) 的流动性池 (Liquidity Pools) 和 市场结构打破 (BOS)。[来源：Smart Money Concepts (SMC): A Complete Trading Guide]
*   **Wyckoff 视角：** 确定市场是处于吸筹 (Accumulation) 还是派发 (Distribution) 阶段。[来源：Mastering the Wyckoff Method]

---

## 3. Playbook Setups (交易模式库)

**注意：** 新手应仅使用 **Stop Order (突破单)** 入场，直到稳定盈利。[来源：方方土PriceAction - 订单类型和应用(1)]

### Setup-01: 强趋势突破 (Strong Breakout)
*   **Direction:** Long / Short (顺势)
*   **Market Preconditions:** 市场处于突破模式，或者是窄通道。
*   **Entry Triggers:**
    *   出现强劲的趋势K线（实体大，影线短）。
    *   **跟随 (Follow-through)：** 突破K线后紧跟另一根强劲的同向趋势K线。
    *   **缺口 (Gap)：** K线之间无重叠。
    *   **激进：** 市价买入/卖出 (Buy/Sell the Close)。
    *   **稳健：** 在突破K线高点上方1 tick 挂 Buy Stop (做多)。[来源：方方土PriceAction - 突破专题(2)]
*   **Invalidation / Failure Conditions:**
    *   突破K线后紧跟反向强K线或十字星（缺乏跟随）。
    *   价格立即回到突破前的区间内。[来源：方方土PriceAction - 突破专题(1)]
*   **Risk Management:**
    *   **止损：** 放置在突破起涨/起跌点下方/上方。由于止损宽，必须**缩小仓位**。[来源：方方土PriceAction - 踏上交易之路(5)]
*   **Profit Taking:**
    *   测量移动 (Measured Move)：基于突破K线实体或区间高度翻倍。
    *   胜率高 (60%)，盈亏比通常 1:1。[来源：方方土PriceAction - 止盈目标位(1)]
*   **Common Mistakes:** 在震荡区间内误判连续大K线为突破（往往是陷阱）。

### Setup-02: 顺势回调 (Pullback / High 1, High 2)
*   **Direction:** Long (in Bull Trend) / Short (in Bear Trend)
*   **Market Preconditions:** 市场处于趋势中（窄通道或宽通道），出现回调。
*   **Entry Triggers:**
    *   **做多 (High 2)：** 在上行趋势回调中，出现第二次尝试恢复趋势的信号（即第二个高点抬高的K线）。在信号K线高点上方1 tick 挂 Buy Stop。
    *   **做空 (Low 2)：** 在下行趋势反弹中，出现第二次尝试恢复趋势的信号。在信号K线低点下方1 tick 挂 Sell Stop。[来源：方方土PriceAction - 顺势交易: 回调&数k线, Al Brooks Setups 1]
    *   **均线法则：** 价格回调至 EMA20 附近出现反转信号K线。[来源：方方土PriceAction - 价格行为学专题分享2——缺口]
*   **Invalidation / Failure Conditions:**
    *   入场后价格迅速反向突破信号K线的另一端。
    *   回调演变为更复杂的调整（如 High 3, High 4），若超过20根K线则可能转为震荡。[来源：方方土PriceAction - 顺势交易: 回调&数k线]
*   **Risk Management:**
    *   **止损：** 信号K线下方/上方，或前一个主要波段低点/高点。
*   **Profit Taking:** 前期高点/低点，或测量移动目标。

### Setup-03: 楔形反转 (Wedge Reversal)
*   **Direction:** Counter-Trend (逆势)
*   **Market Preconditions:** 趋势末端，动能衰竭，或者震荡区间边缘。
*   **Entry Triggers:**
    *   **三推 (3 Pushes)：** 价格出现三次推动（高点/低点逐渐抬高/降低）。
    *   **信号K线：** 第3推后出现强劲的反转K线（如做空需看到收在最低的阴线）。
    *   **入场：** 在信号K线下方/上方 1 tick 挂 Stop Order。[来源：方方土PriceAction - Wedge — 楔形反转(1) & (2)]
*   **Invalidation / Failure Conditions:**
    *   第3推后没有出现反转信号，而是出现强趋势K线继续突破。
    *   反转后的走势缺乏跟随，演变为旗形整理。
*   **Risk Management:**
    *   **止损：** 楔形极值点（最高/最低点）外侧。
*   **Profit Taking:**
    *   至少两段式回调 (TBTL - Ten Bars, Two Legs)。
    *   起始目标：楔形的起点。[来源：方方土PriceAction - Wedge — 楔形反转(2)]
*   **Common Mistakes:** 在极强趋势（窄通道）中过早尝试反转。必须等待明确的信号K线。

### Setup-04: 震荡区间高抛低吸 (Trading Range Fade)
*   **Direction:** Reversal (逆势)
*   **Market Preconditions:** 市场处于震荡区间，高低点明确。
*   **Entry Triggers:**
    *   **做空：** 价格到达区间上沿，出现反转信号K线，或者“第二段陷阱 (2nd Leg Trap)”形成。
    *   **做多：** 价格到达区间下沿，出现反转信号K线。
    *   **入场：** Stop Order 挂单入场。(*注：Limit Order 挂单属于进阶技巧，不推荐新手*)。[来源：方方土PriceAction - 订单类型和应用(2), 价格行为学专题分享7——震荡区间]
*   **Invalidation / Failure Conditions:**
    *   价格强力突破区间边界，并伴随跟随K线（真突破）。
*   **Risk Management:**
    *   **止损：** 区间边界外侧。
*   **Profit Taking:** 区间对侧边界，或区间中部。
*   **Common Mistakes:** 在区间中部追涨杀跌。

### Setup-05: 90分钟开盘区间突破 (90-Min Opening Range Breakout)
*   **Direction:** Long / Short (顺势)
*   **Market Preconditions:** 仅适用于日内交易早盘。
*   **Entry Triggers:**
    *   观察前18根5分钟K线（90分钟）形成的高低点区间。
    *   如果价格强力突破该区间的某一侧（收盘在区间外）。
    *   **入场：** 顺着突破方向交易。[来源：方方土PriceAction - 早盘交易策略(5)]
*   **Invalidation / Failure Conditions:**
    *   突破后立即反转回到区间内（假突破）。
*   **Risk Management:**
    *   **止损：** 突破K线的另一端或区间中点。
*   **Profit Taking:** 测量移动目标（区间高度翻倍）。

### Setup-06: SMC 流动性掠夺与结构破坏 (Liquidity Grab + BOS)
*   **Direction:** Reversal (逆势转顺势)
*   **Market Preconditions:** 价格到达大周期(HTF)的关键支撑/阻力位或流动性池。
*   **Entry Triggers:**
    *   **流动性掠夺 (Sweep)：** 价格短暂突破旧高/旧低后迅速收回（插针）。
    *   **结构破坏 (BOS/MSS)：** 随后价格强力反向移动，打破短期市场结构（如下跌中突破前一个小高点）。
    *   **回踩 (Return)：** 价格回踩 **订单块 (Order Block)** 或 **失衡区 (FVG)**。
    *   **入场：** 在回踩区域出现确认信号时入场。[来源：Smart Money Concepts (SMC): A Complete Trading Guide, ATAS - What Is the Smart Money Concept]
*   **Invalidation / Failure Conditions:**
    *   价格完全穿透订单块或FVG。
*   **Risk Management:**
    *   **止损：** 掠夺流动性的K线极值点外。
*   **Profit Taking:** 下一个流动性池（旧高/旧低）。

---

## 4. Risk & Position Management (风险管理)

### 核心规则
*   **单笔风险：** 不超过账户资金的 1%-2%。[来源：Smart Money Concepts (SMC): A Complete Trading Guide, 方方土PriceAction - 踏上交易之路(5)]
*   **I Don't Care Size：** 仓位必须小到即使亏损也不会引起情绪波动。[来源：Al Brooks' Key Lessons - Altrady, 方方土PriceAction - 踏上交易之路(5)]
*   **禁止加仓 (No Scaling In)：** 新手严禁在亏损时加仓。只有在已有盈利且趋势确认时才可加仓。[来源：方方土PriceAction - 踏上交易之路(8)]
*   **保护性止损：** 开仓同时必须设置物理止损单，不可依赖心理止损。[来源：方方土PriceAction - 踏上交易之路(3)]

### 连续亏损处理
*   **规则：** 如果连续亏损（如连续3笔），停止交易。重新审视市场周期，因为很可能你对市场环境的判断是错误的（如在震荡中做突破）。[来源：方方土PriceAction - 踏上交易之路(8)]

### 沃尔玛交易法 (Walmart Trade)
*   **执行：** 入场后立即设置 OCO (One Cancels Other) 订单（止损单 + 止盈单），然后离开屏幕。避免情绪化干预。[来源：方方土PriceAction - 订单类型和应用(2)]

---

## 5. Decision Process & Checklist (决策流程)

在执行任何交易前，必须按顺序通过以下检查清单。

### 交易前检查清单 (Pre-Trade Checklist)
1.  **环境识别 (Context)：**
    *   现在是趋势还是震荡？
    *   如果是趋势，多头还是空头？强还是弱？
    *   如果是震荡，价格位于区间高位、低位还是中间？
    *   *依据：Market Regime & Preconditions*
2.  **大周期确认 (HTF Alignment)：**
    *   大周期图表（如H4/D1）的趋势是什么？
    *   是否有关键支撑/阻力或流动性池？
    *   *依据：Multi-Timeframe Analysis sources*
3.  **信号确认 (Signal)：**
    *   是否有明确的 Setup（如Setup-01至06）？
    *   是否有合格的信号K线（Signal Bar）？
    *   *依据：Playbook Setups*
4.  **风险计算 (Risk)：**
    *   止损点在哪里？
    *   根据止损距离，我应该开多少仓位才能保证风险 < 2%？
    *   盈亏比是否至少 1:1（高胜率策略）或 2:1（波段策略）？
    *   *依据：Risk Management*
5.  **心理检查 (Psychology)：**
    *   我现在是否情绪稳定？（无报复心理，无FOMO）
    *   *依据：From Hesitation to Execution - Checklist*

### 冲突处理原则
*   如果 **价格行为 (Price Action)** 与 **SMC (订单块/FVG)** 发生冲突，以当前的 **价格动能 (Momentum/Price Action)** 为准。例如，价格强力突破订单块且无反转迹象，视为突破成功，订单块失效。[来源：Smart Money Concepts (SMC): A Complete Trading Guide - "Market structure... come first"]
*   如果 **指标 (Indicators)** 与 **价格行为 (Price Action)** 冲突，忽略指标，只看价格行为。[来源：Price Action vs. Indicators - TradingView, 方方土PriceAction]