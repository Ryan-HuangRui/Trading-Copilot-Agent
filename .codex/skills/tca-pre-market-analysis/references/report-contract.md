# 盘前分析与产物契约

严格基于 wrapper 的 `next_agent_inputs` 中提供的 canonical rulebook（Obsidian vault）规则，对 watchlist 数据做逐标的价格行为分析，并产出「精简执行版 + 完整报告」。

【硬性要求】
1) 分析顺序固定：市场环境 → 结构 → 关键位 → 价格行为 → 交易逻辑（候选，不是指令）
2) 每个标的必须先归类为：条件化交易计划 / 观察候选 / NO TRADE
3) 每个“条件化交易计划”必须补充完整 Trade Plan Card：
   - 参考 setup 文件（必须写文件名）
   - 入场：触发价、确认条件、禁止追价规则
   - 止损：初始止损、失效条件
   - 止盈：TP1，必要时 TP2 或跟踪止盈规则
   - 风险：账户最大风险%、单股风险、最低 RR
   - 执行规则：有效时间窗口、至少 1 条 skip condition
4) 若市场状态为 Tight Trading Range（Barb Wire）或无法识别，输出 `NO TRADE`（并说明原因）
5) 不输出确定性结论，不输出“必须买/卖”
6) 输出简体中文，结构化 markdown
7) unified Trading Copilot knowledge pack 中的统一方法卡可直接作为方法上下文解释市场环境、结构、关键位和价格行为；若使用，必须在 Markdown 标出方法卡。raw 视频与字幕是 compiler-only，不得在报告流程中读取。方法卡不得单独形成 approved setup、覆盖 canonical rulebook 或提升 `execution_status`

【优先使用的 setup 规则池】
- `breakout_pullback_continuation.md`
- `double_top_bottom_reversal.md`
- `channel_break_reversal.md`
- `major_trend_reversal_mtr.md`
- `tight_range_breakout_filter.md`
- `liquidity_grab_and_break_of_structure.md`
- `trading_range_fade.md`
- `wedge_reversal.md`
- `strong_breakout_trend_following.md`
- `trend_pullback_high2_low2.md`
- `90-minute_opening_range_breakout.md`

【setup 选择规则】
- 先判断 regime：趋势 / 震荡 / 过渡 / Barb Wire
- 再匹配 setup：
  - 趋势延续优先：breakout_pullback_continuation / strong_breakout_trend_following / trend_pullback_high2_low2
  - 震荡边界优先：trading_range_fade / double_top_bottom_reversal
  - 衰竭反转优先：wedge_reversal / major_trend_reversal_mtr / liquidity_grab_and_break_of_structure
  - 通道转折：channel_break_reversal
- 若 breakout 类 setup 触发，必须额外通过 tight_range_breakout_filter

【输入数据】
- 主周期：1day；snapshot 默认同时提供 Longbridge-first 的 1h、15min、5min `price_evidence`
- 任何缺失周期必须明确写为未知；不得用方法卡推断未抓取到的低周期事实
- 来自 report/<PRE_MARKET_DATE>/pre-market-context.json（由脚本预先生成）
- context 中的 `source_snapshot_date` 是上一个已完成交易日；`snapshot` 是该交易日收盘后的同一份 daily snapshot
- watchlist: 默认由长桥自选「持仓 / ibkr持仓 / 老朋友 / AI先进封装HBM / AI Top 10 Research」刷新到 config/watchlist.json；长桥不可用时才回退本地文件
- 若 `knowledge/evolution/validated_lessons.md` 存在非空经验，可作为近期流程约束参考；它不能覆盖 canonical rulebook
- 若 `next_agent_inputs` 包含 `playbooks/trading-copilot-knowledge-pack.md`：先读取其中与当前问题相关的统一方法卡；它们是可直接消费的 `method_context`。raw transcript 是 compiler-only，报告流程不得按索引追溯；方法卡不足或存在实质矛盾时，记录知识缺口并保持保守结论。
- 若 snapshot 中存在 `candidate_universe`，它是盘后从 S&P 500 top 100 动态筛出的观察池；分析范围为固定 watchlist + 动态候选去重后的 merged universe
- 不得把动态候选视为交易建议；它们只代表“值得盘前观察”的流动性/权重/量价结构候选
- 若 wrapper 的 `next_agent_inputs` 包含 `report/<PRE_MARKET_DATE>/agents/` 下的 agent research artifacts，只能把它们作为证据增强输入：
  - 优先引用 `decision.json`、`bull_report.json`、`bear_report.json`、`risk_report.json` 中的 evidence id 和 limitations
  - 不得把 agent decision 当成订单输入
  - agent research / memory / sentiment 只能作为证据、风险限制或降级理由；不能单独作为升级为 `conditional_executable` 的理由
  - sidecar 的最终执行状态最终由报告生成 LLM 判断：若你基于 canonical rulebook、当前价格行为、结构关键位和完整 Trade Plan Card 独立判断条件成立，可以在 sidecar 中标记为 `conditional_executable`
  - 如果缺少完整 Trade Plan Card，或只是因为 agent decision / 消息层 / sentiment 支持而缺少价格结构确认，必须维持 `watch_only/no_trade`
- 若 `next_agent_inputs` 包含 `vibe-research-context.json` 及其指向的 Vibe Swarm result/summary：
  - 先完成当前 market/technicals/fundamentals/news/sentiment 与 bull/bear/risk 分析，再用 Swarm 结果审计证据缺口；不得用 Swarm 替代当前分析 skill
  - 只消费 `status=completed` 且 `usable_as_agent_evidence=true` 的 run；必须记录 `run_id`、`as_of`、provider/model、confidence 和 limitations
  - Swarm 只用于补充基本面、事件、反方情景和证据矛盾；不得覆盖 Longbridge-first 价格证据或 canonical rulebook
  - Swarm 结论只能维持或降低信心，不能单独把 `watch_only/no_trade` 升级为 `conditional_executable`
  - pending/failed/stale run 只写入深度研究状态，不得阻断盘前报告，也不得猜测缺失结论
- 当前分析 skill 仅在以下情况标记 `deep_research_required`：重点标的存在会实质影响结论的证据冲突、fundamentals/news 关键缺口、异常波动缺少可核验解释，或 bull/bear/risk 无法在现有证据下收敛。普通数据缺失不得批量触发 Swarm。
- 若 `next_agent_inputs` 或 `report/<PRE_MARKET_DATE>/external-disclosures/` 中存在特朗普/OGE/Open Cabinet/Quiver/InsiderCat 相关持仓或交易披露 artifact，可作为消息层证据输入；若没有结构化 artifact 或最新联网核验，不得编造具体持仓、交易数量、金额、日期或影响，只能说明“未获取到可核验的最新披露”。
- 特朗普持仓与交易披露只属于消息层背景：不得自动加入 watchlist，不得提升任何标的 `execution_status`，不得把披露解读为买卖指令；最多用于提示相关标的需要额外核验政策/舆情/流动性风险。

【输出文件（必须同时生成）】
1) 精简执行版（用于 Cron 正文发送）
   - report/<PRE_MARKET_DATE>/exec-brief.md
2) 完整报告（用于附件发送）
   - report/<PRE_MARKET_DATE>/pre-market.md
3) 机器可读信号 sidecar（用于校验、journal、复盘）
   - report/<PRE_MARKET_DATE>/pre-market-signals.json

【精简执行版模板】
# 今日盘前执行简版（<PRE_MARKET_DATE>）
## 总览
- 市场状态：
- 今日最多3个重点标的：

## 消息层汇总
### 特朗普持仓与交易变化
- 数据来源：<OGE / Open Cabinet / Quiver / InsiderCat / 未接入结构化披露输入>
- 持仓变化：<只写可核验事实；无数据则写“未获取到可核验的最新披露”>
- 交易变化：<只写可核验事实；金额必须保留披露区间或区间中点估算口径；无数据则写“未获取到可核验的最新披露”>
- 关联观察：<仅列与当前 merged universe 重合的标的及消息层风险；不得作为交易信号>
- 限制：披露有延迟、金额为区间、缺少精确股数/成交价/盘中成交时间；本节不能提升执行等级。

## 深度研究状态
- 已完成研究：<run_id / 标的 / as_of / RESEARCH_ONLY|WATCH|NO_TRADE；没有则写“无”>
- 待处理升级：<deep_research_required 的标的、证据缺口与研究问题；没有则写“无”>
- 边界：Vibe Swarm 仅为二级研究证据，不提升执行等级。

## 方法上下文（如使用）
- <标的或市场>：<方法要点>；来源：<统一方法卡路径>；作用：<仅解释结构/行为/观察条件>
- 知识缺口（如有）：<方法卡无法覆盖的具体问题；不得以 raw 补全>

## 今日可执行交易计划
### <SYMBOL>
- 参考 setup：<setup-file.md>
- 方向：
- 状态：conditional_executable / waiting_trigger
- 入场：<触发价 + 确认条件 + 回踩条件>
- 止损：<初始止损 + 失效条件>
- 止盈：<TP1 + TP2/跟踪规则>
- 风险：<单笔账户风险 <=1%，单股风险，最低 RR>
- 禁止执行：<至少一条 skip condition>

## 观察候选
### <SYMBOL>
- 参考 setup：
- 观察条件：
- 未升级为交易计划的原因：

## NO TRADE
- <SYMBOL>：<原因>

## 组合风控
- 当日总风险上限：
- 相关性约束：
- 放弃交易条件：

【完整报告模板】
沿用当前完整版结构输出到 report/<PRE_MARKET_DATE>/pre-market.md，并对每个标的增加一行：
- 参考 setup：<setup-file.md>
完整报告必须包含 `## 消息层汇总`，并在其中单独设置 `### 特朗普持仓与交易变化` 小节。该小节只汇总可核验披露事实、与今日观察池重合的标的、数据限制和“不作为交易信号”的说明；如果没有数据，必须明确写出未获取到可核验的最新披露。
完整报告还必须包含 `## 深度研究状态`：列出已消费的 Vibe run、证据冲突、limitations，以及需要异步升级研究的问题。没有完成的 Swarm 结果时必须明确写“本轮未使用已完成的 Vibe 深度研究”，不能阻断主报告。

【pre-market-signals.json 模板】
必须与精简执行版里的「今日最多3个重点标的」一致。`conditional_executable` 表示满足人工执行前置条件的交易计划；`watch_only` 只代表观察候选；`no_trade` 表示不允许执行。
最终由报告生成 LLM 判断每个信号的 `execution_status`；agent research artifacts 是证据输入而非最终裁决。只有当完整 Trade Plan Card 与 canonical rulebook 同时满足时，才可以在 sidecar 中标记为 `conditional_executable`。

```json
{
  "date": "<PRE_MARKET_DATE>",
  "session": "pre-market",
  "source_report": "report/<PRE_MARKET_DATE>/exec-brief.md",
  "signals": [
    {
      "symbol": "MU",
      "setup": "breakout_pullback_continuation.md",
      "direction": "long",
      "regime": "trend",
      "plan_type": "trade_plan",
      "execution_status": "conditional_executable",
      "trigger": {
        "type": "break_above",
        "price": 100.0,
        "text": "突破 100 后回踩站稳"
      },
      "invalidation": {
        "type": "break_below",
        "price": 95.0,
        "text": "跌破 95"
      },
      "risk": {
        "max_risk_pct": 1.0,
        "max_account_risk_pct": 1.0,
        "risk_per_share": 5.0,
        "min_rr": 2.0,
        "text": "单笔风险 <=1%，止损过宽则放弃"
      },
      "entry": {
        "type": "breakout_pullback",
        "trigger_price": 100.0,
        "confirmation": "5m/15m 收盘站上触发价，回踩不破",
        "no_chase_rule": "若实际入场距离止损超过计划 2R 则放弃"
      },
      "stop": {
        "initial_stop": 95.0,
        "invalidation": "跌破 95 且无法收回"
      },
      "take_profit": {
        "tp1": 112.0,
        "management": "达到 +1R 后考虑减仓或上移止损"
      },
      "execution_rules": {
        "valid_time_window": "开盘前 90 分钟或清晰回踩后",
        "skip_conditions": ["大盘转 risk-off", "突破 K 线过度延伸", "成交量无法确认"]
      },
      "status": "planned",
      "notes": "只做确认，不追第一波"
    }
  ]
}
```

【质量门槛】
- 未标注 setup 文件名 -> 该标的分析视为无效
- 未给失效位或风险约束 -> 该标的分析视为无效
- 可执行候选未写入 pre-market-signals.json，或 pre-market-signals.json 与 Markdown 重点标的不一致 -> 视为无效
- pre-market-signals.json 中 actionable signal 必须有结构化 trigger.price / invalidation.price / risk
- execution_status=conditional_executable 必须有 entry.trigger_price、stop.initial_stop、take_profit.tp1、risk.max_account_risk_pct、risk.risk_per_share、execution_rules.skip_conditions，且 TP1 的 RR >= 2
- 缺少完整 Trade Plan Card 的标的只能标记为 watch_only 或 no_trade
- regime 无法识别时，默认 NO TRADE
- 盘前 Markdown 缺少 `## 消息层汇总` 或 `### 特朗普持仓与交易变化` -> 视为无效
- 特朗普披露小节不得包含确定性买卖建议，不得提升任何标的执行等级；无可核验数据时必须说明数据缺口
- 使用外部价格行为方法时，Markdown 必须保留统一方法卡路径；raw 视频和字幕不得进入运行期报告输入
