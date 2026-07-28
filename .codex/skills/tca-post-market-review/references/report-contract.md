# 盘后复盘与产物契约

严格基于 wrapper 的 `next_agent_inputs` 中提供的 canonical rulebook（Obsidian vault）规则，对 watchlist 收盘后数据做「盘后复盘」，目标是总结当天结构与执行质量，不是给确定性预测。

【硬性要求】
1) 分析顺序固定：市场环境复盘 → 结构变化 → 关键位表现 → setup 有效性 → 明日观察计划
2) 每个重点标的必须包含：当日行为、结构结论、有效/无效 setup、关键位、明日关注点、风险提醒
3) 每个被标记为“明日条件化交易计划”的标的必须补充完整 Trade Plan Card：
   - 参考 setup 文件（必须写文件名）
   - 入场：触发价、确认条件、禁止追价规则
   - 止损：初始止损、失效条件
   - 止盈：TP1，必要时 TP2 或跟踪止盈规则
   - 风险：账户最大风险%、单股风险、最低 RR
   - 执行规则：有效时间窗口、至少 1 条 skip condition
4) 若市场状态为 Tight Trading Range（Barb Wire）或无法识别，输出 `NO TRADE / 仅复盘不计划`（并说明原因）
5) 不输出确定性结论，不输出“明天必须买/卖”
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

【setup 复盘规则】
- 先判断 regime：趋势 / 震荡 / 过渡 / Barb Wire
- 再判断当天行为是否确认、失败、或仍未触发 setup
- breakout 类必须额外检查 `tight_range_breakout_filter`
- 盘后复盘优先回答“今天发生了什么、哪些计划被验证/否定、明天只观察什么”

【输入数据】
- 主周期：1day；snapshot 默认同时提供 Longbridge-first 的 1h、15min、5min `price_evidence`
- 任何缺失周期必须明确写为未知；不得用方法卡推断未抓取到的低周期事实
- 来自 report/<SNAPSHOT_DATE>/daily-snapshot.json（收盘后生成的最新已完成交易日 snapshot）
- watchlist: 默认由长桥自选「持仓 / ibkr持仓 / 老朋友 / AI先进封装HBM / AI Top 10 Research」刷新到 config/watchlist.json；长桥不可用时才回退本地文件
- 若 `knowledge/evolution/validated_lessons.md` 存在非空经验，可作为近期流程约束参考；它不能覆盖 canonical rulebook
- 若 `next_agent_inputs` 包含 `playbooks/trading-copilot-knowledge-pack.md`：先读取其中与当前问题相关的统一方法卡；它们是可直接消费的 `method_context`。raw transcript 是 compiler-only，报告流程不得按索引追溯；方法卡不足或存在实质矛盾时，记录知识缺口并保持保守结论。
- 若 snapshot 中存在 `candidate_universe`，它是盘后从 S&P 500 top 100 动态筛出的观察池；复盘时优先说明固定 watchlist 与动态候选中哪些值得明日继续观察
- 动态候选只代表流动性/权重/量价结构筛选结果，不代表交易建议
- 若 wrapper 的 `next_agent_inputs` 包含 `report/<SNAPSHOT_DATE>/agents/` 下的 agent research artifacts，只能把它们作为证据增强输入：
  - 引用 `decision.json`、`bull_report.json`、`bear_report.json`、`risk_report.json` 的 evidence id、风险限制和 limitations
  - 不得把 agent decision 当成订单输入
  - agent research / memory / sentiment 只能作为证据、风险限制或降级理由；不能单独作为升级为 `conditional_executable` 的理由
  - sidecar 的最终执行状态最终由报告生成 LLM 判断：若你基于 canonical rulebook、当日价格行为、明日关键位和完整 Trade Plan Card 独立判断条件成立，可以在 sidecar 中标记为 `conditional_executable`
  - 如果缺少完整 Trade Plan Card，或只是因为 agent decision / 消息层 / sentiment 支持而缺少价格结构确认，必须维持 `watch_only/no_trade`
- 若 `next_agent_inputs` 包含 `vibe-research-context.json` 及其指向的 Vibe Swarm result/summary：
  - 先完成当前 market/technicals/fundamentals/news/sentiment 与 bull/bear/risk 复盘，再用 Swarm 做独立取证和反方审计；不得替代当前分析 skill
  - 只消费 `status=completed` 且 `usable_as_agent_evidence=true` 的 run，并记录 `run_id`、`as_of`、provider/model、confidence 和 limitations
  - Swarm 可以解释异常走势、补充基本面/事件证据或否定原假设，但不能覆盖 Longbridge-first 收盘数据、canonical rulebook 或 Trade Plan validators
  - Swarm 只能维持或降低明日计划信心，不能单独提升 execution_status；pending/failed/stale 必须保留状态并继续主流程
- 当前分析 skill 仅在异常走势无可核验解释、bull/bear/risk 存在重大冲突、重点假设缺关键基本面/事件证据、或重复失败模式需要独立审计时标记 `deep_research_required`。每个 session 最多升级 1 个标的，避免无差别消耗。
- 若 wrapper 的 `next_agent_inputs` 包含盘中监控 artifacts，必须读取并在报告中单独总结：
  - `report/<SNAPSHOT_DATE>/intraday.md`
  - `runtime/intraday/<SNAPSHOT_DATE>/state.json`
  - `runtime/intraday/<SNAPSHOT_DATE>/events.jsonl`
  - 盘中监控只用于复盘“盘前计划是否被盘中验证、否定、错过或保持等待”，不能作为订单输入，不能单独提升任何标的 execution_status。
  - 若盘中 artifacts 缺失，必须在「盘中监控回顾」中说明今日无盘中监控产物。
- 若存在 `runtime/account/<SNAPSHOT_DATE>/plugin-account-snapshot.json`，必须按券商保留 provenance，并在组合层按 symbol/currency 聚合。复盘每个持仓的当日行为、计划覆盖、失效证据与次日人工复核条件；跨券商重复标的不能被当作两个互不相关的风险。缺少价格、汇率或券商输入必须显式披露。
- 若存在 `runtime/account/<SNAPSHOT_DATE>/plugin-trade-snapshot.json`，其中 `executions` 是当日真实成交证据，`orders` 只作订单上下文。必须逐笔或按同标的同方向成交组复盘：计划关联、成交时点、成交价相对 5m/15m 结构、是否追价、失效纪律、重复交易、佣金与券商提供的已实现盈亏。不得因某一券商 scope 失败就推断该账户当日无交易，也不得自动写入 formal `trades.jsonl`。

【输出文件（必须生成）】
- report/<SNAPSHOT_DATE>/post-market.md
- report/<SNAPSHOT_DATE>/post-market-signals.json

【输出模板】
# 今日盘后复盘（<SNAPSHOT_DATE>）
## 总览
- 市场状态：
- 今日最值得复盘的标的：
- 明日最多3个重点观察标的：

## 市场复盘
- 环境：
- 结构变化：
- 风险偏好：
- 明日全局放弃条件：

## 盘中监控回顾
- 盘中关注池：
- 重要状态变化：
- 已发送提醒：
- 盘前计划验证/否定：
- 未触发/继续等待：
- 数据或流程问题：

## 当日交易复盘
- 数据覆盖：<IBKR / Longbridge / 部分覆盖>；未授权或失败来源：<明确列出>
- 成交汇总：<只统计 executions；按券商、方向、标的和币种分别汇总>
- 逐笔/成交组：
  - <BROKER / SYMBOL / SIDE / QTY / PRICE / TIME>
  - 计划关联：<planned / unplanned / unknown + 证据>
  - 时点与价格质量：<相对 5m/15m 结构、触发、失效和 no-chase；缺数据则 unknown>
  - 仓位影响：<开仓/加仓/减仓/退出/unknown，仅基于前后持仓与方向证据>
  - 纪律评价：<做得好 / 需改进 / 无法评价 + 原因>
- 当日行为模式：<追价、过度交易、亏损加仓、及时止损、过早止盈等；只写有证据者>
- 改进项：<可验证的流程改进，不直接修改 canonical rulebook>
- 边界：复盘只评价已发生交易，不产生下一笔订单或自动写入 trades.jsonl。

## 持仓与组合风险复盘
- 数据覆盖：<IBKR / Longbridge / 部分可用 / 均不可用>；快照时间：<generated_at>
- 跨账户重复持仓：<标的或“无”>
- 集中度与相关性：<仅使用可比币种和可靠净资产口径；否则写“无法可靠聚合”>
- 逐持仓：<当日行为 / 结构结论 / 计划与失效一致性 / 次日人工复核条件>
- 特殊风险：<杠杆 ETF、保证金、缺失价格、未纳入计划>
- 边界：只读复盘，不生成加仓、减仓、卖出或订单动作。

## 深度研究复盘
- 已完成研究：<run_id / 标的 / as_of / RESEARCH_ONLY|WATCH|NO_TRADE；没有则写“无”>
- 对原假设的支持/否定：
- 未解决矛盾与 limitations：
- 待升级研究：<最多1个标的及明确 research objective；没有则写“无”>
- 边界：Vibe Swarm 仅为二级研究证据，不提升执行等级。

## 方法上下文（如使用）
- <标的或市场>：<方法要点>；来源：<统一方法卡路径>；作用：<仅解释结构/行为/复盘结论>
- 知识缺口（如有）：<方法卡无法覆盖的具体问题；不得以 raw 补全>

## 重点标的复盘
### <SYMBOL>
- 当日行为：
- 结构结论：
- 参考 setup：<setup-file.md 或 NO VALID SETUP>
- setup 状态：确认 / 失败 / 未触发 / 仅观察
- 关键位：
- 明日主观察：
- 明日备选路径：
- 失效/放弃条件：
- 风险提醒：

## 明日观察清单
- <SYMBOL>：

## 明日条件化交易计划
### <SYMBOL>
- 方向：
- 状态：conditional_executable / waiting_trigger
- 入场：
- 止损：
- 止盈：
- 风险：
- 禁止执行：

## NO TRADE
- <SYMBOL>：<原因>

## 复盘结论
- 今天验证的规则：
- 今天应避免的行为：
- 明日执行纪律：

【post-market-signals.json 模板】
必须与 Markdown 中「明日最多3个重点观察标的」和「明日观察清单」一致；它表示明日计划，不是交易指令。`conditional_executable` 表示满足人工执行前置条件的交易计划；`watch_only` 只代表观察候选；`no_trade` 表示不允许执行。
最终由报告生成 LLM 判断每个信号的 `execution_status`；agent research artifacts 是证据输入而非最终裁决。只有当完整 Trade Plan Card 与 canonical rulebook 同时满足时，才可以在 sidecar 中标记为 `conditional_executable`。

```json
{
  "date": "<SNAPSHOT_DATE>",
  "session": "post-market",
  "source_report": "report/<SNAPSHOT_DATE>/post-market.md",
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
        "text": "明日突破 100 后回踩站稳"
      },
      "invalidation": {
        "type": "break_below",
        "price": 95.0,
        "text": "跌破 95 或开盘跳空后无法收复"
      },
      "risk": {
        "max_risk_pct": 1.0,
        "max_account_risk_pct": 1.0,
        "risk_per_share": 5.0,
        "min_rr": 2.0,
        "text": "单笔风险 <=1%，触发和失效距离过宽则放弃"
      },
      "entry": {
        "type": "breakout_pullback",
        "trigger_price": 100.0,
        "confirmation": "5m/15m 收盘站上触发价，回踩不破",
        "no_chase_rule": "若实际入场距离止损超过计划 2R 则放弃"
      },
      "stop": {
        "initial_stop": 95.0,
        "invalidation": "跌破 95 或开盘跳空后无法收复"
      },
      "take_profit": {
        "tp1": 112.0,
        "management": "达到 +1R 后考虑减仓或上移止损"
      },
      "execution_rules": {
        "valid_time_window": "次日开盘前 90 分钟或清晰回踩后",
        "skip_conditions": ["大盘转 risk-off", "突破 K 线过度延伸", "成交量无法确认"]
      },
      "status": "planned",
      "notes": "仅观察，等待明日确认"
    }
  ]
}
```

【质量门槛】
- 未标注 setup 文件名或明确 `NO VALID SETUP` -> 该标的复盘视为无效
- 未给失效/放弃条件 -> 该标的复盘视为无效
- 值得明日重点观察的标的未写入 post-market-signals.json，或 post-market-signals.json 与 Markdown 观察清单不一致 -> 视为无效
- post-market-signals.json 中 actionable signal 必须有结构化 trigger.price / invalidation.price / risk
- execution_status=conditional_executable 必须有 entry.trigger_price、stop.initial_stop、take_profit.tp1、risk.max_account_risk_pct、risk.risk_per_share、execution_rules.skip_conditions，且 TP1 的 RR >= 2
- 缺少完整 Trade Plan Card 的标的只能标记为 watch_only 或 no_trade
- regime 无法识别时，默认 `NO TRADE / 仅复盘不计划`
- 缺少 `## 深度研究复盘`，或引用未完成/未记录 provenance 的 Swarm 结论 -> 视为无效
- 使用外部价格行为方法时，Markdown 必须保留统一方法卡路径；raw 视频和字幕不得进入运行期报告输入
- 缺少 `## 持仓与组合风险复盘`，或在插件持仓存在时未披露券商覆盖/重复暴露/数据限制 -> 视为无效
- 缺少 `## 当日交易复盘`，或将 orders 当成 executions、未披露失败券商来源、未区分事实与推断 -> 视为无效
