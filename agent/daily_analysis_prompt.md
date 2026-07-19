使用 repo-only Skill `$tca-pre-market-analysis` 执行 Trading-Copilot-Agent 盘前分析。

分析流程、输入规则、输出模板、深度研究升级条件和质量门槛均以该 Skill 及其直接引用的契约为准；本 Prompt 不定义分析逻辑。

严格保持实盘账户只读，不输出确定性买卖指令。若由 cc-connect wrapper 调用，飞书发送仍由外层 wrapper 负责。
