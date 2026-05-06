# Cron Delivery Spec（Feishu / Codex Automation）

目标：
- 收盘后生成一次 daily snapshot = `report/<SNAPSHOT_DATE>/daily-snapshot.json`
- 盘后 Cron 附件/正文 = `report/<SNAPSHOT_DATE>/post-market.md`
- 次日盘前 Cron 正文 = `report/<PRE_MARKET_DATE>/exec-brief.md`
- 次日盘前完整报告附件 = `report/<PRE_MARKET_DATE>/pre-market.md`

## 盘后推荐流程
1. 运行 `python script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day`
2. 若脚本输出 `skipped=true`，直接结束，不发送报告
3. 调用 Agent（使用 `agent/post_market_analysis_prompt.md`）读取：
   - `report/<SNAPSHOT_DATE>/daily-snapshot.json`
4. 生成：
   - `report/<SNAPSHOT_DATE>/post-market.md`
5. 发送到 Feishu：
   - message body: 盘后复盘摘要
   - media/filePath: 盘后复盘 markdown 文件

## 次日盘前推荐流程
1. 运行 `python script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day`
2. 若脚本输出 `skipped=true`，直接结束，不发送报告
3. 调用 Agent（使用 `agent/daily_analysis_prompt.md`）读取：
   - `report/<PRE_MARKET_DATE>/pre-market-context.json`
4. 生成两个报告文件：
   - `report/<PRE_MARKET_DATE>/exec-brief.md`
   - `report/<PRE_MARKET_DATE>/pre-market.md`
5. 发送到 Feishu：
   - message body: 精简执行版全文
   - media/filePath: 完整报告 markdown 文件

## Codex App Automation Prompt 建议
- 盘后：先运行 daily snapshot 脚本；若非交易日跳过；否则基于 `agent/post_market_analysis_prompt.md`、`knowledge/refined/`、`report/<SNAPSHOT_DATE>/daily-snapshot.json` 生成盘后复盘。
- 次日盘前：先运行盘前上下文脚本；若非交易日跳过；否则基于 `agent/daily_analysis_prompt.md`、`knowledge/refined/`、`report/<PRE_MARKET_DATE>/pre-market-context.json` 生成盘前两份报告。
- 第一版将节假日判断放在脚本内，automation 只按周一到周五触发。

## 发送内容规范
- 正文只放执行必需信息（3分钟可读完）
- 完整附件保留完整推理和全部标的细节
