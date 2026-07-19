# Trading Copilot Agent

为美股交易提供结构化支持：复盘、价格行为分析、行情数据获取、盘前计划、盘后复盘、TradingAgents 风格证据增强、模拟盘 dry-run / 受控执行。

## 目录结构

- `script/`：数据获取、分析、报告生成脚本
- `report/`：自动生成的每日报告（Markdown）
- `raw_data/`：原始行情数据缓存（JSON/CSV）
- `knowledge/evolution/`：运行期候选规律与复盘证据；不是规则源
- `config/knowledge_source.json`：canonical Obsidian rulebook 的位置（可由 `TCA_KNOWLEDGE_ROOT` 覆盖）
- `agent/`：Codex App automation 实际读取的报告生成 Prompt
- `docs/`：Codex App automation 与人工操作 runbook
- `config/`：watchlist 与策略参数
- `.codex/skills/trading-copilot/`：repo-local skill 入口，供 Codex/Claude/OpenClaw 类 agent 识别本仓库能力
- `.codex/skills/intraday-tracker/`：盘中只读追踪 skill，供 Codex 定时任务记录盘前计划状态变化

## 快速开始

```bash
cd repo/trading-copilot-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 默认使用已登录的 Longbridge CLI 获取行情；如需 Twelve Data fallback，编辑 .env 填入 TWELVE_DATA_API_KEY
```

推荐验证：

```bash
python3 -m py_compile script/*.py
python3 -m unittest discover tests
python3 script/trading_day_guard.py --date 2026-05-06 --format text
```

## 职责分层

- `script/`：只做确定性数据工作，包括交易日判断、行情拉取、限频、缓存、context 生成。
- `script/market_data_provider.py`：行情源入口，默认 Longbridge CLI，Twelve Data 作为 fallback。
- `script/trading_copilot.py`：面向 agent 的统一 workflow wrapper，返回 `status/date/artifacts/skipped/reason`。
- `script/agent_market_data.py` / `script/agent_technicals.py` / `script/agent_research_reports.py` / `script/agent_decision.py`：TradingAgents 风格 artifact-first 证据链路。
- `agent/`：Codex App 自动化生成报告时实际读取的 Prompt，目前只保留盘前和盘后两个执行 Prompt。
- Obsidian vault 的 canonical rulebook：唯一交易规则源。
- `docs/`：调度流程和运维说明。
- `AGENTS.md`：Codex 维护本仓库时的工程约束，不作为交易分析 Prompt。
- `.codex/skills/trading-copilot/SKILL.md`：交易研究 skill 的触发条件、安全边界、标准命令与输出契约。

## Agent Skill 用法

本仓库优先作为 agent skill/workflow 包使用，而不是独立产品。推荐入口：

```bash
python3 script/trading_copilot.py trading-day-check --date 2026-05-06
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day --include-agent-research
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols --include-agent-research
python3 script/trading_copilot.py monitor-brief --state config/monitor_state.json --interval 5min
python3 script/trading_copilot.py intraday-tracker --date <DATE> --top-n 5
python3 script/trading_copilot.py intraday-dry-run --date <DATE>
python3 script/trading_copilot.py agent-research-context --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py agent-research-reports --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py validate-agent-reports --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py agent-decision --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py validate-agent-decision --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py agent-memory-review --date <DATE> --symbol <SYMBOL>
python3 script/trading_copilot.py agent-memory-append --decision report/<DATE>/agents/<SYMBOL>/decision.json --outcome-status <STATUS> --reflection "<TEXT>"
python3 script/trading_copilot.py agent-memory-export
python3 script/trading_copilot.py validate-report --session pre-market --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session pre-market --date <DATE>
python3 script/trading_copilot.py validate-trade-plan --session monitor --date <DATE>
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
python3 script/trading_copilot.py extract-monitor-signals --date <DATE>
python3 script/trading_copilot.py account-snapshot --date <DATE>
python3 script/trading_copilot.py paper-account-snapshot --date <DATE>
python3 script/trading_copilot.py paper-trade-preview --date <DATE> --session pre-market --require-validation
python3 script/trading_copilot.py paper-trade-preview --date <DATE> --session monitor --require-validation
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session pre-market --require-validation --execute
python3 script/trading_copilot.py paper-trade-submit --date <DATE> --session monitor --require-validation
python3 script/trading_copilot.py intraday-paper-entry --date <DATE> --require-validation
python3 script/trading_copilot.py intraday-paper-entry --date <DATE> --require-validation --execute
python3 script/trading_copilot.py paper-order-sync --date <DATE>
python3 script/trading_copilot.py paper-event-ledger --date <DATE>
python3 script/trading_copilot.py paper-execution-review --date <DATE>
python3 script/trading_copilot.py paper-learning-lessons --date <DATE> --append
python3 script/trading_copilot.py paper-strategy-review
python3 script/trading_copilot.py paper-order-cancel --date <DATE>
python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE>
python3 script/trading_copilot.py paper-protective-stop-plan --date <DATE> --execute
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE>
python3 script/trading_copilot.py paper-take-profit-plan --date <DATE> --execute
python3 script/trading_copilot.py paper-break-even-stop-plan --date <DATE>
python3 script/trading_copilot.py paper-trade-review --date <DATE> --session pre-market --append
python3 script/trading_copilot.py position-review --date <DATE> --config config/position_review.json --append
python3 script/workflow_smoke_test.py --date <DATE> --week <YYYY-Www>
```

契约文档：

- `docs/contracts/workflows.md`
- `docs/contracts/data-contracts.md`
- `docs/contracts/journal.md`
- `docs/contracts/agent-research.md`
- `docs/cc-connect-scheduler.md`
- `docs/longbridge-account-setup.md`
- `docs/paper-execution-runbook.md`
- `docs/paper-execution-roadmap.md`
- `docs/workflows/`

## 每日报告流程
- 交易日判断：`script/trading_day_guard.py`，默认按美股东部时间判断常规交易日
- 收盘后 daily snapshot：`script/prepare_market_snapshot.py` 默认通过长桥拉取最新 `1day`、`1h`、`15min`、`5min` 多周期数据，生成：
  - `raw_data/YYYY-MM-DD/1day/<SYMBOL>.json`
  - `raw_data/YYYY-MM-DD/{1h,15min,5min}/<SYMBOL>.json`
  - `report/YYYY-MM-DD/daily-snapshot.json`
- 可选 S&P 500 扩池：收盘后加 `--sp500-screen --sp500-top 100 --sp500-candidates 15`，从 iShares IVV 官方持仓 CSV 获取 S&P 500 权重池，按权重/成交量/成交额/量价结构筛出 15 个动态观察候选，生成：
  - `report/YYYY-MM-DD/candidate-universe.json`
  - 并把动态候选与固定 `config/watchlist.json` 去重合并进 `daily-snapshot.json`
- 盘后复盘：Agent 使用 repo-only `$tca-post-market-review` + canonical rulebook + snapshot，产出：
  - `report/YYYY-MM-DD/post-market.md`
  - `report/YYYY-MM-DD/post-market-signals.json`
- 次日盘前上下文：`script/prepare_daily_context.py` 读取上一交易日 snapshot，生成：
  - `report/YYYY-MM-DD/pre-market-context.json`
- 盘前 wrapper 默认采集特朗普 OGE/Open Cabinet 披露消息层 artifact：
  - `report/YYYY-MM-DD/external-disclosures/trump-trades.json`
  - 可用 `--no-external-disclosures` 关闭；该数据只作为消息层背景，不进入 watchlist 或执行信号
- 次日盘前分析：Agent 使用 repo-only `$tca-pre-market-analysis` + canonical rulebook + pre-market context，产出：
  - `report/YYYY-MM-DD/exec-brief.md`
  - `report/YYYY-MM-DD/pre-market.md`
  - `report/YYYY-MM-DD/pre-market-signals.json`
- 报告校验：`python3 script/trading_copilot.py validate-report --session pre-market --date YYYY-MM-DD`
- 交易计划校验：`python3 script/trading_copilot.py validate-trade-plan --session pre-market --date YYYY-MM-DD`
- 行情质量检查：`python3 script/trading_copilot.py data-quality --date YYYY-MM-DD`
- 信号入 journal：`python3 script/trading_copilot.py extract-report-signals --session pre-market --date YYYY-MM-DD --require-validation --append`
- `--require-validation` 会同时跑报告校验和交易计划校验，任一失败都不应继续入 journal 或同步 Longbridge。
- 分析过程由 Agent 完成，脚本只做交易日判断、数据准备、指标摘要与限频控制
- 盘前 `exec-brief.md` / `pre-market.md` 必须包含 `## 消息层汇总`，并单独汇总 `### 特朗普持仓与交易变化`。该小节只作为 OGE/Open Cabinet/Quiver/InsiderCat 等披露来源的消息层背景；未获取到可核验输入时必须说明数据缺口，且不得提升任何标的执行等级。
- 详细 runbook：`docs/daily-report-workflow.md`

## TradingAgents 风格证据增强

本仓库现在支持 artifact-first 的多角色投研增强链路，默认不替代原有盘前/盘后报告，只作为可选证据输入：

```text
market/context snapshot
  -> agent_market_data.py
  -> agent_technicals.py
  -> agent_research_reports.py
  -> agent_decision.py
  -> validate-agent-reports / validate-agent-decision
  -> optional next_agent_inputs for pre/post report generation
```

单独运行：

```bash
python3 script/trading_copilot.py agent-research-context --date YYYY-MM-DD --symbol MU
python3 script/agent_market_data.py --date YYYY-MM-DD --symbol MU
python3 script/agent_technicals.py --date YYYY-MM-DD --symbol MU
python3 script/trading_copilot.py agent-research-reports --date YYYY-MM-DD --symbol MU
python3 script/trading_copilot.py validate-agent-reports --date YYYY-MM-DD --symbol MU
python3 script/trading_copilot.py agent-decision --date YYYY-MM-DD --symbol MU
python3 script/trading_copilot.py validate-agent-decision --date YYYY-MM-DD --symbol MU
```

其中 `agent-research-context` 是集成骨架与调度契约；真正用于报告证据增强的是后续 market data、technicals、reports、decision 和 validator 产物。盘前/盘后生产流程建议直接使用 `--include-agent-research`，由 wrapper 串联这些步骤并把 artifact path 注入 `next_agent_inputs`。

接入盘前/盘后：

```bash
python3 script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day --include-agent-research
python3 script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols --include-agent-research
```

产物默认写入：

- `report/YYYY-MM-DD/agents/market-data.json`
- `report/YYYY-MM-DD/agents/technicals.json`
- `report/YYYY-MM-DD/agents/<SYMBOL>/market_report.json`
- `report/YYYY-MM-DD/agents/<SYMBOL>/technicals_report.json`
- `report/YYYY-MM-DD/agents/<SYMBOL>/fundamentals_report.json`
- `report/YYYY-MM-DD/agents/<SYMBOL>/news_report.json`
- `report/YYYY-MM-DD/agents/<SYMBOL>/sentiment_report.json`
- `report/YYYY-MM-DD/agents/<SYMBOL>/bull_report.json`
- `report/YYYY-MM-DD/agents/<SYMBOL>/bear_report.json`
- `report/YYYY-MM-DD/agents/<SYMBOL>/risk_report.json`
- `report/YYYY-MM-DD/agents/<SYMBOL>/decision.json`
- `report/YYYY-MM-DD/agents/<SYMBOL>/decision.md`

边界：

- Agent research artifacts 是证据增强，不是订单输入。
- 若存在 `report/YYYY-MM-DD/external-disclosures/trump-trades.json`，`agent_research_reports.py` 会把匹配当前 symbol 的 `source_subtype=oge_disclosure` 证据合并进 `<SYMBOL>/news_report.json`。
- `decision.json` 必须复用现有 `plan_type` / `execution_status` 语义。
- `experimental=true` 或 `not_for_execution=true` 的占位决策会被 `validate-agent-decision` 拒绝。
- 任何 broker 写操作仍只能走专用 paper workflow。

### 记忆层

```bash
python3 script/trading_copilot.py agent-memory-append \
  --decision report/YYYY-MM-DD/agents/MU/decision.json \
  --outcome-status not_triggered \
  --reflection "观察未触发，后续降低优先级"

python3 script/trading_copilot.py agent-memory-review --date YYYY-MM-DD --symbol MU
python3 script/trading_copilot.py agent-memory-export
```

产物：

- `runtime/memory/trading_memory.md`
- `runtime/memory/trading_memory.sqlite`

Memory 只能降低置信度、增加限制或触发人工 review，不能提高 `execution_status`，也不能修改 canonical rulebook。

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
- 模拟盘是盘前/盘后工作流的执行扩展层，依赖 `pre-market-signals.json` / `post-market-signals.json`，但 broker 写操作必须单独显式启动，不能混入报告生成任务。详细操作见 `docs/paper-execution-runbook.md`。
- 模拟盘演进路线见 `docs/paper-execution-roadmap.md`：当前优先强化执行状态和 broker capability matrix，后续再接入新闻/财报情绪、盘中 dry-run 候选和 OCO/高级订单。
- 模拟盘接入当前支持快照、订单预览、受控提交、订单同步、保护/退出计划和复盘：
  - 配置：`config/paper_execution.json` 默认关闭所有 broker 写入；部署时可用 ignored 的 `config/paper_execution.local.json` 并通过 `--paper-execution-config` 指定，执行入场需同时设置 `paper_execution.broker_writes_enabled=true` 与 `paper_execution.allow_entry_submit=true`
  - `paper_execution.allow_intraday_entry_submit` 是独立的盘中模拟盘入场门禁；普通 `paper-trade-submit --session monitor --execute` 仍然硬禁用，必须通过 `intraday-paper-entry --execute` 才能进入该门禁
  - `python3 script/trading_copilot.py paper-account-snapshot --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-trade-preview --date YYYY-MM-DD --session pre-market --require-validation`
  - `python3 script/trading_copilot.py paper-trade-submit --date YYYY-MM-DD --session pre-market --require-validation`
  - `python3 script/trading_copilot.py paper-trade-submit --date YYYY-MM-DD --session pre-market --require-validation --execute`
  - `python3 script/trading_copilot.py paper-order-sync --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-event-ledger --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-execution-review --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-learning-lessons --date YYYY-MM-DD --append`
  - `python3 script/trading_copilot.py paper-strategy-review`
  - `python3 script/trading_copilot.py paper-order-cancel --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-order-cancel --date YYYY-MM-DD --execute`
  - `python3 script/trading_copilot.py paper-protective-stop-plan --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-protective-stop-plan --date YYYY-MM-DD --execute`
  - `python3 script/trading_copilot.py paper-take-profit-plan --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-take-profit-plan --date YYYY-MM-DD --execute`
  - `python3 script/trading_copilot.py paper-break-even-stop-plan --date YYYY-MM-DD`
  - `python3 script/trading_copilot.py paper-trade-review --date YYYY-MM-DD --session pre-market --append`
  - 产物：`runtime/paper/YYYY-MM-DD/paper-account-snapshot.json`、`report/YYYY-MM-DD/paper-trade-preview.json`、`report/YYYY-MM-DD/paper-trade-submission.json`、`runtime/paper/YYYY-MM-DD/paper-orders.jsonl`、`runtime/paper/YYYY-MM-DD/paper-execution-state.json`、`runtime/journal/events.jsonl`、`report/YYYY-MM-DD/paper-event-ledger.json`、`report/YYYY-MM-DD/paper-execution-review.json`、`report/YYYY-MM-DD/paper-execution-review.md`、`report/YYYY-MM-DD/paper-learning-lessons.json`、`runtime/learning/daily_lessons.jsonl`、`report/strategy/paper-strategy-review.json`、`report/strategy/paper-strategy-review.md`、`report/YYYY-MM-DD/paper-order-cancel-plan.json`、`report/YYYY-MM-DD/paper-protective-stop-plan.json`、`runtime/paper/YYYY-MM-DD/paper-stop-orders.jsonl`、`report/YYYY-MM-DD/paper-take-profit-plan.json`、`runtime/paper/YYYY-MM-DD/paper-take-profit-orders.jsonl`、`report/YYYY-MM-DD/paper-break-even-stop-plan.json`、`report/YYYY-MM-DD/paper-trade-review.json`
  - `paper-account-snapshot` 会校验 Longbridge 当前账户是 `lb_papertrading`；`paper-trade-preview` 输出 dry-run 订单预览；`paper-trade-submit` 默认 dry-run，只在 `--execute` 与 `config/paper_execution.json` 对应动作门禁同时满足后，通过独立 paper order adapter 提交模拟盘限价买入单；`paper-order-sync` 只读回放入场、保护止损、TP1 提交账本和模拟盘快照，并把 stop/TP1 状态回填到 entry；`paper-event-ledger` 将 paper 提交与成交状态投影到统一事件账本；`paper-execution-review` 从同步后的 state 生成执行质量 JSON/Markdown 复盘和候选 lessons，但不修改 refined rules；`paper-learning-lessons` 只把候选 lessons 写入 runtime 学习队列，后续仍需 `learning-review` 和人工 `promote-lesson`；`paper-strategy-review` 聚合多个执行复盘到 setup/symbol 维度；`paper-order-cancel` 默认 dry-run，只在对应配置门禁后撤销过期未成交入场单；`paper-protective-stop-plan` 默认 dry-run，只在对应配置门禁后为已成交 long entry 提交模拟盘 `sell MIT --trigger-price <stop>` 保护性止损单；`paper-take-profit-plan` 默认 dry-run，只在对应配置门禁后为已成交 long entry 提交默认 50% 仓位的模拟盘 `sell LO --price <tp1>` TP1 分批止盈单；`paper-break-even-stop-plan` 仅生成 dry-run 止损移动计划，不执行撤单、替换或提交；`paper-trade-review` 优先用 `broker_order_id` / `remark` / `intent_id` 匹配已观察到的模拟成交并回写 journal

## 实时盯盘
- 支持多标的 5m 监控，默认只输出做多路径（可配置）
- 空仓：识别潜在交易信号（观察中/临近触发/可执行）
- 持仓：输出 R 值与风险动作（减仓/止损上移/退出）
- 执行脚本：`python3 script/monitor_scan.py --state config/monitor_state.json --interval 5min`
- 输出文件：`report/latest-monitor.json`
- Phase 1 盘中计划追踪：
  ```bash
  python3 script/trading_copilot.py intraday-tracker --date YYYY-MM-DD --top-n 5
  ```
- `intraday-tracker` 读取盘前 `pre-market-signals.json` 的 topN、可选 `config/intraday_watchlist.json` 人工观察列表、上一轮 `report/<DATE>/intraday.md` 和结构化 state。
- `intraday-tracker` 追加 `report/<DATE>/intraday.md`，更新 `runtime/intraday/<DATE>/state.json`，仅在重要状态变化时追加 `runtime/intraday/<DATE>/events.jsonl`。
- `events.jsonl` 是 cc connect 或其他通知层的候选输入，不是执行指令。
- 主动飞书通知：
  ```bash
  TCA_INTRADAY_SKIP_MONITOR=1 bash ops/cc-connect/tca-intraday-notify.sh YYYY-MM-DD
  # 生产轮询时去掉 TCA_INTRADAY_SKIP_MONITOR=1，让脚本先跑 monitor-brief 再跑 intraday-tracker
  ```
- `tca-intraday-notify.sh` 只会发送尚未发送过的 `notify=true` 事件，并把已发送事件记录到 `runtime/intraday/<DATE>/sent-events.json`。
- 可选生成 monitor sidecar：`python3 script/trading_copilot.py extract-monitor-signals --date YYYY-MM-DD`
- 可选写入 journal：`python3 script/trading_copilot.py extract-monitor-signals --append`
- monitor scan 原生输出 setup/risk_quality/journal_appendable；journal 记录仍只是观察，不是执行指令
- monitor dry-run 闭环：
  ```bash
  python3 script/trading_copilot.py intraday-dry-run --date YYYY-MM-DD
  # 等价拆解如下：
  python3 script/trading_copilot.py validate-trade-plan --session monitor --date YYYY-MM-DD
  python3 script/trading_copilot.py paper-trade-preview --date YYYY-MM-DD --session monitor --require-validation
  python3 script/trading_copilot.py paper-trade-submit --date YYYY-MM-DD --session monitor --require-validation
  python3 script/trading_copilot.py feishu-summary --session monitor --date YYYY-MM-DD
  ```
- `paper-trade-submit --session monitor --execute` 会被硬拒绝；盘中候选目前只支持 dry-run 和人工观察。
- Phase 3 独立模拟盘盘中入场：
  ```bash
  python3 script/trading_copilot.py intraday-paper-entry --date YYYY-MM-DD --require-validation
  python3 script/trading_copilot.py intraday-paper-entry --date YYYY-MM-DD --require-validation --execute --paper-execution-config config/paper_execution.local.json
  ```
- `intraday-paper-entry --execute` 只面向 `lb_papertrading`，且要求 `broker_writes_enabled=true` 和 `allow_intraday_entry_submit=true`；不要用它替代普通 monitor dry-run。

## cc connect 定时任务

生产调度以 cc connect 为边界：cc connect 负责定时触发、接收摘要和发送 Feishu；本仓库负责数据准备、报告产物、校验、journal、模拟盘 dry-run/受控执行。详细配置见 `docs/cc-connect-scheduler.md`。

推荐生产任务：

- **盘前报告**：`pre-market-plan` → Agent 生成 `exec-brief.md` / `pre-market.md` / `pre-market-signals.json` → `validate-report` → `validate-trade-plan` → `extract-report-signals --require-validation --append` → 可选只读账户/持仓复核 → `data-quality` → `feishu-summary`
- **盘后复盘**：`post-market-review` → Agent 生成 `post-market.md` / `post-market-signals.json` → `validate-report` → `validate-trade-plan` → outcome backfill → journal append → position review → `plan-review` / `learning-review` / `daily-self-review` → `data-quality` → `feishu-summary`
- **可选 Agent Research 增强**：把盘前/盘后的准备命令替换为带 `--include-agent-research` 的 wrapper 命令，cc connect 需要把 `validate-agent-reports` / `validate-agent-decision` 失败作为阻断状态展示。
- **Monitor dry-run**：`monitor-brief` → `extract-monitor-signals --date <DATE>` → `validate-trade-plan --session monitor` → `paper-trade-preview --session monitor` → `paper-trade-submit --session monitor` → `feishu-summary --session monitor`
- **盘中模拟盘入场**：必须是单独 Codex 任务，只能在 monitor dry-run 已验收、`config/paper_execution.local.json` 明确开启 `broker_writes_enabled=true` 和 `allow_intraday_entry_submit=true` 后，使用 `intraday-paper-entry --execute`。
- **模拟盘入场执行**：必须是单独任务，只能在 dry-run 已验收、`config/paper_execution.local.json` 明确开启 `broker_writes_enabled=true` 和 `allow_entry_submit=true` 后，对 `pre-market` 使用 `paper-trade-submit --execute`。

cc connect 禁止事项：

- 不要同时启用 Codex App automation 和 cc connect 跑同一条生产盘前/盘后任务。
- 不要把 broker write 混进报告生成任务。
- 不要自动运行 `promote-lesson --apply`。
- 不要调度 `paper-trade-submit --session monitor --execute`。

## 运行示例

### 单次批量拉取（日线）

```bash
python script/fetch_daily.py --symbols AAPL,MSFT,NVDA,TSLA --interval 1day --output raw_data
```

### 生成开盘前报告（Agent 分析）

```bash
# 1) 准备上下文（交易日判断 + 读取上一交易日 snapshot）
python script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day

# 可选：同时生成 TradingAgents 风格 research artifacts，作为报告证据增强
python script/trading_copilot.py pre-market-plan --watchlist config/watchlist.json --skip-non-trading-day --include-agent-research

# 2) 让 Agent 基于 context + knowledge 生成 report/YYYY-MM-DD/pre-market.md
# （在 Codex App automation 中触发即可）
```

### 生成收盘后复盘（Agent 分析）

```bash
# 1) 收盘后生成 daily snapshot（交易日判断 + 数据拉取 + 限频）
python script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols

# 可选：同时做 S&P 500 top 100 动态扩池，输出 15 个观察候选
python script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --sp500-screen --sp500-top 100 --sp500-candidates 15 --include-journal-signals --include-position-symbols

# 可选：同时生成 TradingAgents 风格 research artifacts，作为报告证据增强
python script/trading_copilot.py post-market-review --watchlist config/watchlist.json --skip-non-trading-day --include-journal-signals --include-position-symbols --include-agent-research

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
