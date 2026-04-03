# Trading Copilot Agent

为美股交易提供结构化支持：复盘、价格行为分析、行情数据获取、开盘前日报。

## 目录结构

- `script/`：数据获取、分析、报告生成脚本
- `report/`：自动生成的每日报告（Markdown）
- `raw_data/`：原始行情数据缓存（JSON/CSV）
- `knowledge/`：交易知识库（已导入 refined 规则）
- `skill/`：Agent Skills 定义（复盘/分析/数据获取）
- `agent/`：Agent Prompt 与运行配置
- `config/`：watchlist 与策略参数

## 快速开始

```bash
cd repo/trading-copilot-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 TWELVE_DATA_API_KEY
```

## 功能说明

### 1) 复盘 Skill（`skill/review_skill.md`）
- 对单笔/多笔交易打分（流程质量优先，不以盈亏论英雄）
- 输出：做得好的点、待改进点、下一步行动

### 2) 分析 Skill（`skill/analysis_skill.md`）
- 基于价格行为学进行结构化分析（市场环境→结构→关键位→行为→交易逻辑）
- 默认日线（`1day`），必要时可切换周期

### 3) 数据获取 Skill（`skill/data_fetch_skill.md`）
- 单标的：可直接调用 Twelve Data API
- 批量标的：必须走脚本（内置限频：每分钟最多 8 次请求）

### 4) 每日报告 Cron（Agent 分析模式）
- 第一步：`script/prepare_daily_context.py` 拉取 watchlist 数据并生成 `report/YYYY-MM-DD-context.json`
- 第二步：由 Agent 读取 `agent/daily_analysis_prompt.md` + `knowledge/refined/` + context 文件，产出 `report/YYYY-MM-DD-pre-market.md`
- 这样“分析过程”由 Agent 完成，脚本只做数据准备与限频控制

### 5) 实时盯盘 Skill（`skill/monitoring_skill.md`）
- 支持多标的 5m 监控，默认只输出做多路径（可配置）
- 空仓：识别潜在交易信号（观察中/临近触发/可执行）
- 持仓：输出 R 值与风险动作（减仓/止损上移/退出）
- 执行脚本：`python3 script/monitor_scan.py --state config/monitor_state.json --interval 5min`
- 输出文件：`report/latest-monitor.json`

## 运行示例

### 单次批量拉取（日线）

```bash
python script/fetch_daily.py --symbols AAPL,MSFT,NVDA,TSLA --interval 1day --output raw_data
```

### 生成开盘前报告（Agent 分析）

```bash
# 1) 准备上下文（数据拉取 + 限频）
python script/prepare_daily_context.py --watchlist config/watchlist.json

# 2) 让 Agent 基于 context + knowledge 生成 report/YYYY-MM-DD-pre-market.md
# （在 OpenClaw 会话中触发即可）
```

### Linux Cron 示例（美股东部时间 08:30）

> 你当前系统是 UTC，08:30 ET 可能对应 12:30/13:30 UTC（夏令时会变化）。建议用支持时区的调度器，或手动按季节调整。

```cron
30 12 * * 1-5 cd /home/node/.openclaw/workspace/repo/trading-copilot-agent && /home/node/.openclaw/workspace/repo/trading-copilot-agent/.venv/bin/python script/pre_market_report.py --watchlist config/watchlist.json >> report/cron.log 2>&1
```

## 风险提示

本仓库仅用于交易研究与流程管理，不构成投资建议。