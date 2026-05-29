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
- `.codex/skills/trading-copilot/`：repo-local skill 入口，供 Codex/Claude/OpenClaw 类 agent 识别本仓库能力

## 快速开始

```bash
cd repo/trading-copilot-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 默认使用已登录的 Longbridge CLI 获取行情；如需 Twelve Data fallback，编辑 .env 填入 TWELVE_DATA_API_KEY
```

## 职责分层

- `script/`：只做确定性数据工作，包括交易日判断、行情拉取、限频、缓存、context 生成。
- `script/market_data_provider.py`：行情源入口，默认 Longbridge CLI，Twelve Data 作为 fallback。
- `script/trading_copilot.py`：面向 agent 的统一 workflow wrapper，返回 `status/date/artifacts/skipped/reason`。
- `agent/`：Codex App 自动化生成报告时实际读取的 Prompt，目前只保留盘前和盘后两个执行 Prompt。
- `knowledge/refined/`：唯一交易规则源。
- `docs/`：调度流程和运维说明。
- `AGENTS.md`：Codex 维护本仓库时的工程约束，不作为交易分析 Prompt。
- `.codex/skills/trading-copilot/SKILL.md`：交易研究 skill 的触发条件、安全边界、标准命令与输出契约。

## Agent Skill 用法

本仓库优先作为 agent skill/workflow 包使用，而不是独立产品。推荐入口：

```bash
python3 script/trading_copilot.py trading-day-check --date 2026-05-06
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols
python3 script/trading_copilot.py monitor-brief --state config/monitor_state.json --interval 5min
python3 script/trading_copilot.py validate-report --session pre-market --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <DATE>
python3 script/trading_copilot.py extract-report-signals --session pre-market --date <DATE> --require-validation --append
python3 script/trading_copilot.py backfill-signal-outcomes --date <DATE> --append
python3 script/trading_copilot.py plan-review --date <DATE> --append-lessons
python3 script/trading_copilot.py learning-review --lookback-days 20
python3 script/trading_copilot.py data-quality --date <DATE>
python3 script/trading_copilot.py feishu-summary --session pre-market --date <DATE>
python3 script/trading_copilot.py promote-lesson --pattern-id <PATTERN_ID> --dry-run
python3 script/trading_copilot.py daily-self-review --date <DATE> --append
python3 script/trading_copilot.py weekly-review --week <YYYY-Www> --append
python3 script/trading_copilot.py extract-monitor-signals --append
python3 script/trading_copilot.py account-snapshot --date <DATE>
python3 script/trading_copilot.py paper-account-snapshot --date <DATE>
python3 script/trading_copilot.py paper-trade-preview --date <DATE> --session pre-market --require-validation
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation
python3 script/trading_copilot.py paper-order-sync --date <DATE>
python3 script/trading_copilot.py paper-order-cancel --date <DATE>
python3 script/trading_copilot.py paper-trade-review --date <DATE> --session pre-market --append
python3 script/trading_copilot.py position-review --date <DATE> --config config/position_review.json --append
python3 script/workflow_smoke_test.py --date <DATE> --week <YYYY-Www>
```

契约文档：

- `docs/contracts/workflows.md`
- `docs/contracts/data-contracts.md`
- `docs/contracts/journal.md`
- `docs/cc-connect-scheduler.md`
- `docs/longbridge-account-setup.md`
- `docs/paper-execution-roadmap.md`
- `docs/workflows/`

## 每日报告流程
- 交易日判断：`script/trading_day_guard.py`，默认按美股东部时间判断常规交易日
- 收盘后 daily snapshot：`script/prepare_market_snapshot.py` 拉取最新已完成日线，生成：
  - `raw_data/YYYY-MM-DD/1day/<SYMBOL>.json`
  - `report/YYYY-MM-DD/daily-snapshot.json`
- 可选 S&P 500 扩池：收盘后加 `--sp500-screen --sp500-top 100 --sp500-candidates 15`，从 iShares IVV 官方持仓 CSV 获取 S&P 500 权重池，按权重/成交量/成交额/量价结构筛出 15 个动态观察候选，生成：
  - `report/YYYY-MM-DD/candidate-universe.json`
  - 并把动态候选与固定 `config/watchlist.json` 去重合并进 `daily-snapshot.json`
- 盘后复盘：Agent 读取 `agent/post_market_analysis_prompt.md` + `knowledge/refined/` + snapshot，产出：
  - `report/YYYY-MM-DD/post-market.md`
  - `report/YYYY-MM-DD/post-market-signals.json`
- 次日盘前上下文：`script/prepare_daily_context.py` 读取上一交易日 snapshot，生成：
  - `report/YYYY-MM-DD/pre-market-context.json`
- 次日盘前分析：Agent 读取 `agent/daily_analysis_prompt.md` + `knowledge/refined/` + pre-market context，产出：
  - `report/YYYY-MM-DD/exec-brief.md`
  - `report/YYYY-MM-DD/pre-market.md`
  - `report/YYYY-MM-DD/pre-market-signals.json`
- 报告校验：`python3 script/trading_copilot.py validate-report --session pre-market --date YYYY-MM-DD`
- 交易计划校验：`python3 script/trading_copilot.py validate-trade-plan --session pre-market --date YYYY-MM-DD`
- 行情质量检查：`python3 script/trading_copilot.py data-quality --date YYYY-MM-DD`
- 信号入 journal：`python3 script/trading_copilot.py extract-report-signals --session pre-market --date YYYY-MM-DD --require-validation --append`
- `--require-validation` 会同时跑报告校验和交易计划校验，任一失败都不应继续入 journal 或同步 Longbridge。
- 分析过程由 Agent 完成，脚本只做交易日判断、数据准备、指标摘要与限频控制
- 详细 runbook：`docs/daily-report-workflow.md`

## 复盘闭环
- 盘后复盘：Agent 生成 `post-market.md` 与 `post-market-signals.json` 后，先跑 `validate-report` 和 `validate-trade-plan`
- outcome 回填：`python3 script/trading_copilot.py backfill-signal-outcomes --date YYYY-MM-DD --append`
- 交易计划复盘：`python3 script/trading_copilot.py plan-review --date YYYY-MM-DD --append-lessons`
- 候选规律聚合：`python3 script/trading_copilot.py learning-review --lookback-days 20`
- 飞书摘要：`python3 script/trading_copilot.py feishu-summary --session pre-market --date YYYY-MM-DD`
- 人工晋升预览：`python3 script/trading_copilot.py promote-lesson --pattern-id <PATTERN_ID> --dry-run`
- 日度自我复盘：`python3 script/trading_copilot.py daily-self-review --date YYYY-MM-DD --append`
- 周度复盘：`python3 script/trading_copilot.py weekly-review --week YYYY-Www --append`
- journal 默认写入 ignored runtime 路径：`runtime/journal/signals.jsonl`、`outcomes.jsonl`、`trades.jsonl`、`reviews.jsonl`
- 持仓复核只读 Longbridge 账户快照，不下单、不撤单、不自动调仓：
  - `python3 script/trading_copilot.py account-snapshot --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py position-review --date YYYY-MM-DD --config config/position_review.json --append`
  - 产物：`runtime/account/YYYY-MM-DD/account-snapshot.json`、`report/YYYY-MM-DD/position-review.md`、`report/YYYY-MM-DD/position-review.json`
  - 配置：`config/position_review.json`
  - 若 `runtime/journal/trades.jsonl` 里有 `source_signal_id`，持仓复核会关联原始 signal、交易记录和估算 R
- 模拟盘接入当前支持快照、订单预览、受控提交、订单同步和复盘：
  - `python3 script/trading_copilot.py paper-account-snapshot --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-trade-preview --date YYYY-MM-DD --session pre-market --require-validation`
  - `python3 script/trading_copilot.py paper-trade-submit --date YYYY-MM-DD --session pre-market --require-validation`
  - `TRADING_COPILOT_PAPER_EXECUTION=enabled python3 script/trading_copilot.py paper-trade-submit --date YYYY-MM-DD --session pre-market --require-validation --execute`
  - `python3 script/trading_copilot.py paper-order-sync --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-order-cancel --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-trade-review --date YYYY-MM-DD --session pre-market --append`
  - 产物：`runtime/paper/YYYY-MM-DD/paper-account-snapshot.json`、`report/YYYY-MM-DD/paper-trade-preview.json`、`report/YYYY-MM-DD/paper-trade-submission.json`、`runtime/paper/YYYY-MM-DD/paper-orders.jsonl`、`runtime/paper/YYYY-MM-DD/paper-execution-state.json`、`report/YYYY-MM-DD/paper-order-cancel-plan.json`、`report/YYYY-MM-DD/paper-trade-review.json`
  - `paper-account-snapshot` 会校验 Longbridge 当前账户是 `lb_papertrading`；`paper-trade-preview` 输出 dry-run 订单预览；`paper-trade-submit` 默认 dry-run，只在 `--execute` 与 `TRADING_COPILOT_PAPER_EXECUTION=enabled` 同时满足后，通过独立 paper order adapter 提交模拟盘限价买入单；`paper-order-sync` 只读回放提交账本和模拟盘快照；`paper-order-cancel` 当前只生成过期未成交入场单撤单计划，不调用 broker cancel；`paper-trade-review` 优先用 `broker_order_id` / `remark` / `intent_id` 匹配已观察到的模拟成交并回写 journal

## 实时盯盘
- 支持多标的 5m 监控，默认只输出做多路径（可配置）
- 空仓：识别潜在交易信号（观察中/临近触发/可执行）
- 持仓：输出 R 值与风险动作（减仓/止损上移/退出）
- 执行脚本：`python3 script/monitor_scan.py --state config/monitor_state.json --interval 5min`
- 输出文件：`report/latest-monitor.json`
- 可选写入 journal：`python3 script/trading_copilot.py extract-monitor-signals --append`
- monitor scan 原生输出 setup/risk_quality/journal_appendable；journal 记录仍只是观察，不是执行指令

## 运行示例

### 单次批量拉取（日线）

```bash
python script/fetch_daily.py --symbols AAPL,MSFT,NVDA,TSLA --interval 1day --output raw_data
```

### 生成开盘前报告（Agent 分析）

```bash
# 1) 准备上下文（交易日判断 + 读取上一交易日 snapshot）
python script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day

# 2) 让 Agent 基于 context + knowledge 生成 report/YYYY-MM-DD/pre-market.md
# （在 Codex App automation 中触发即可）
```

### 生成收盘后复盘（Agent 分析）

```bash
# 1) 收盘后生成 daily snapshot（交易日判断 + 数据拉取 + 限频）
python script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols

# 可选：同时做 S&P 500 top 100 动态扩池，输出 15 个观察候选
python script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15 --include-journal-signals --include-position-symbols

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

在新机器上配置 Codex App automation 时，按 `docs/codex-automation-setup.md` 完成环境变量、两条 automation、首次运行和失败处理配置。

## 风险提示

本仓库仅用于交易研究与流程管理，不构成投资建议。
