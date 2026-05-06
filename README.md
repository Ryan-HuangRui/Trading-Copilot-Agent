# Trading Copilot Agent

为美股交易提供结构化支持：复盘、价格行为分析、行情数据获取、盘前计划、盘后复盘。

## 目录结构

- `script/`：数据获取、分析、报告生成脚本
- `report/`：自动生成的每日报告（Markdown）
- `raw_data/`：原始行情数据缓存（JSON/CSV）
- `knowledge/`：交易知识库（已导入 refined 规则）
- `agent/`：Codex App automation 实际读取的报告生成 Prompt
- `docs/`：Codex App automation 与人工操作 runbook
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

## 职责分层

- `script/`：只做确定性数据工作，包括交易日判断、行情拉取、限频、缓存、context 生成。
- `agent/`：Codex App 自动化生成报告时实际读取的 Prompt，目前只保留盘前和盘后两个执行 Prompt。
- `knowledge/refined/`：唯一交易规则源。
- `docs/`：调度流程和运维说明。
- `AGENTS.md`：Codex 维护本仓库时的工程约束，不作为交易分析 Prompt。

## 每日报告流程
- 交易日判断：`script/trading_day_guard.py`，默认按美股东部时间判断常规交易日
- 收盘后 daily snapshot：`script/prepare_market_snapshot.py` 拉取最新已完成日线，生成：
  - `raw_data/YYYY-MM-DD/1day/<SYMBOL>.json`
  - `report/YYYY-MM-DD/daily-snapshot.json`
- 盘后复盘：Agent 读取 `agent/post_market_analysis_prompt.md` + `knowledge/refined/` + snapshot，产出：
  - `report/YYYY-MM-DD/post-market.md`
- 次日盘前上下文：`script/prepare_daily_context.py` 读取上一交易日 snapshot，生成：
  - `report/YYYY-MM-DD/pre-market-context.json`
- 次日盘前分析：Agent 读取 `agent/daily_analysis_prompt.md` + `knowledge/refined/` + pre-market context，产出：
  - `report/YYYY-MM-DD/exec-brief.md`
  - `report/YYYY-MM-DD/pre-market.md`
- 分析过程由 Agent 完成，脚本只做交易日判断、数据准备、指标摘要与限频控制
- 详细 runbook：`docs/daily-report-workflow.md`

## 实时盯盘
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
# 1) 准备上下文（交易日判断 + 读取上一交易日 snapshot）
python script/prepare_daily_context.py --watchlist config/watchlist.json --skip-non-trading-day

# 2) 让 Agent 基于 context + knowledge 生成 report/YYYY-MM-DD/pre-market.md
# （在 Codex App automation 中触发即可）
```

### 生成收盘后复盘（Agent 分析）

```bash
# 1) 收盘后生成 daily snapshot（交易日判断 + 数据拉取 + 限频）
python script/prepare_market_snapshot.py --watchlist config/watchlist.json --skip-non-trading-day

# 2) 让 Agent 基于 snapshot + knowledge 生成 report/YYYY-MM-DD/post-market.md
# （在 Codex App automation 中触发即可）
```

### 单独检查是否交易日

```bash
python script/trading_day_guard.py
python script/trading_day_guard.py --date 2026-05-06 --format text
```

### Codex App Automation

第一版直接使用 Codex App automation 调度。盘后先生成 `daily-snapshot.json`，盘前复用上一交易日 snapshot。具体 prompt 和跳过规则见 `docs/daily-report-workflow.md`。

## 风险提示

本仓库仅用于交易研究与流程管理，不构成投资建议。
