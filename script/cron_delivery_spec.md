# Cron Delivery Spec（Feishu）

目标：
- 每日 Cron 发送正文 = `report/<DATE>-exec-brief.md`
- 完整报告作为附件 = `report/<DATE>-pre-market.md`

## 推荐流程
1. 运行 `python script/prepare_daily_context.py --watchlist config/watchlist.json`
2. 调用 Agent（使用 `agent/daily_analysis_prompt.md`）生成两个报告文件：
   - `report/<DATE>-exec-brief.md`
   - `report/<DATE>-pre-market.md`
3. 发送到 Feishu：
   - message body: 精简执行版全文
   - media/filePath: 完整报告 markdown 文件

## 发送内容规范
- 正文只放执行必需信息（3分钟可读完）
- 完整附件保留完整推理和全部标的细节
