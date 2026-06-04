# TradingAgents 融合路线图

本文档定义如何把 TradingAgents 风格的多角色投研能力融合进 Trading-Copilot-Agent，同时保留当前 Longbridge、校验器、journal、模拟盘执行安全边界。

这是规划文档，不是当前工作流契约。当前可执行行为仍以 `docs/contracts/workflows.md` 和 `docs/contracts/data-contracts.md` 为准。

## 目标

- 增加类似 TradingAgents 的投研栈：市场、技术面、基本面、新闻、情绪证据。
- 在角色推理前生成结构化中间报告。
- 增加分角色推理：Bull Researcher、Bear Researcher、Risk Manager、Portfolio Manager。
- 输出最终决策产物：`decision.md` 和 `decision.json`。
- 写入可复用记忆：先使用 Markdown，再可选扩展到 SQLite。
- 增强现有盘前、盘后工作流，而不是替换它们。
- 保留 Longbridge CLI、模拟盘工作流和现有安全契约。

## 非目标

- 不把 TradingAgents 整个代码库直接 vendor-copy 到本仓库。
- 不替换 `script/trading_copilot.py` 作为统一工作流入口。
- 不允许角色 agent 下单、撤单、改单或自动调仓。
- 不允许 `decision.json` 直接触发 broker 写操作。
- 不把 TradingAgents 风格评级直接写入 `knowledge/refined/`。
- 在轻量 artifact-first 路线被验证前，不把 LangGraph 变成必需依赖。

## 目标架构

```text
Codex / automation
  -> 本机确定性数据工具
       script/agent_market_data.py
       script/agent_technicals.py
       script/agent_fundamentals.py
       script/agent_news.py
       script/agent_sentiment.py
  -> 结构化中间报告
       report/<DATE>/agents/<SYMBOL>/market_report.json
       report/<DATE>/agents/<SYMBOL>/technicals_report.json
       report/<DATE>/agents/<SYMBOL>/fundamentals_report.json
       report/<DATE>/agents/<SYMBOL>/news_report.json
       report/<DATE>/agents/<SYMBOL>/sentiment_report.json
  -> 分角色推理
       Bull Researcher
       Bear Researcher
       Risk Manager
       Portfolio Manager
  -> 最终决策产物
       report/<DATE>/agents/<SYMBOL>/decision.md
       report/<DATE>/agents/<SYMBOL>/decision.json
  -> 记忆
       runtime/memory/trading_memory.md
       runtime/memory/trading_memory.sqlite
  -> 现有下游门禁
       validate-report
       validate-trade-plan
       paper-trade-preview
       paper-trade-submit --execute 仅在现有门禁满足后可用
```

## 核心设计决策

- 采用 artifact-first 融合方式：脚本先写 JSON 证据，角色 agent 再消费这些产物。
- LLM 推理前必须先有确定性数据；agent 不应在报告中凭空抓取或编造未支持的数据。
- 现有交易规则仍是最高优先级：交易结论必须服从 `knowledge/refined/`。
- 现有模拟盘执行保持隔离：决策产物可以辅助生成 Trade Plan Card，但不能绕过验证器或执行门禁。
- 结构化报告中的每条证据都必须包含 freshness、source、confidence、limitations。
- 第一版尽量保持标准库优先；新增第三方依赖必须有明确收益。

## Phase 0：契约与骨架

### 实现目标

先定义新增 artifact 契约和工作流入口，再添加 provider 或角色推理。

### 实现范围

- 新增 `docs/contracts/agent-research.md`。
- 在 `script/trading_copilot.py` 增加骨架命令：
  - `agent-research-context`
  - `agent-research-reports`
  - `agent-decision`
  - `agent-memory-review`
- 在 `tests/fixtures/agent_research/` 增加 schema fixture。
- 仅在契约中定义 `report/<DATE>/agents/<SYMBOL>/` 输出路径；不提交生成报告。

### 目标文件

- `docs/contracts/agent-research.md`
- `docs/contracts/workflows.md`
- `docs/contracts/data-contracts.md`
- `script/trading_copilot.py`
- `tests/test_agent_research_contracts.py`

### 验收标准

- 契约明确所有中间报告和最终决策的必填字段。
- 每个新 wrapper 命令返回统一状态 envelope：`status`、`workflow`、`date`、`artifacts`、`skipped`、`reason`。
- 骨架命令可用 fixture 输入运行，并写出确定性的占位 JSON。
- 如果骨架命令写出占位 `decision.json`，必须包含 `experimental=true` 和 `not_for_execution=true`，且不得包含可被下游解释为可执行交易的字段。
- 在 `validate-agent-decision` 落地后，占位决策必须校验失败；在此之前，占位决策不得注入盘前/盘后 `next_agent_inputs`。
- `python3 -m unittest discover tests` 通过。
- `python3 -m py_compile script/*.py` 通过。
- 不引入任何 broker 写路径。

### 阶段门禁

在 schema 足够稳定、盘前/盘后 prompt 可以无歧义引用前，不进入 Phase 1。

## Phase 1：本机数据工具层

### 实现目标

实现类似 TradingAgents dataflows 的确定性本机工具，同时保留 Longbridge 作为主要行情源。

### 实现范围

- 第一批只实现 `agent_market_data.py` 和 `agent_technicals.py`。
- `agent_market_data.py`：读取 `daily-snapshot.json`、`pre-market-context.json`，或复用 Longbridge/Twelve provider stack。
- `agent_technicals.py`：基于现有 OHLCV 计算常用指标。
- 基本面、新闻、情绪第一批只定义 provider contract 与 fixture 数据，不新增 live source 接入。
- 暂不把 `agent_fundamentals.py`、`agent_news.py`、`agent_sentiment.py` 作为第一批完整脚本目标；等 fixture contract 和 report schema 稳定后再拆成独立工具。
- 新增 `config/agent_research.json`。

### 目标文件

- `script/agent_market_data.py`
- `script/agent_technicals.py`
- `docs/contracts/agent-research.md`
- `config/agent_research.json`
- `tests/fixtures/agent_research/`
- `tests/test_agent_market_data.py`
- `tests/test_agent_technicals.py`
- `tests/test_agent_research_provider_contracts.py`

### 验收标准

- 行情数据复用现有 Longbridge/Twelve fallback 契约，并记录 provider/fallback metadata。
- 技术指标可在无网络 fixture OHLCV 上生成。
- 基本面、新闻、情绪只要求 provider contract 与 fixture 通过校验；不要求第一批 live fetch。
- 每条 evidence 包含 `source`、`source_type`、`published_at` 或 `as_of`、`symbol`、`summary`、`confidence`、`limitations`。
- fixture 测试不需要任何 API key。
- 现有盘前、盘后、monitor、paper 测试继续通过。

### 阶段门禁

在 fixture-mode report 稳定并能校验数据 freshness 前，不把 live 新闻或基本面接入定时工作流。

## Phase 2：结构化中间报告

### 实现目标

从本机数据工具层生成机器可读的 analyst reports。

### 实现范围

- 新增 `agent_research_reports.py`。
- 生成：
  - `market_report.json`
  - `technicals_report.json`
  - `fundamentals_report.json`
  - `news_report.json`
  - `sentiment_report.json`
- 增加可选 Markdown renderer，方便人工检查。
- 增加报告校验命令：`validate-agent-reports`。

### 目标文件

- `script/agent_research_reports.py`
- `script/validate_agent_reports.py`
- `docs/contracts/agent-research.md`
- `tests/test_agent_research_reports.py`
- `tests/test_validate_agent_reports.py`

### 验收标准

- 报告生成在 `report/<DATE>/agents/<SYMBOL>/` 下。
- 同一 fixture 输入生成确定性报告。
- 报告明确区分事实、派生指标、模型/启发式评分、limitations。
- 必填 evidence 字段缺失时，validation 必须失败。
- 可选 live source 缺失时可以 warning，但不应导致 fixture workflow 失败。
- 生成报告不得包含直接买卖指令。

### 阶段门禁

在中间报告能独立通过校验前，不添加角色推理。

## Phase 3：不依赖 LangGraph 的角色推理

### 实现目标

用 Codex prompts 和 artifact 输入实现 TradingAgents 风格角色推理，不把 LangGraph 作为硬依赖。

### 实现范围

- 新增角色 prompt：
  - `agent/roles/bull_researcher.md`
  - `agent/roles/bear_researcher.md`
  - `agent/roles/risk_manager.md`
  - `agent/roles/portfolio_manager.md`
- 新增 `agent_decision.py` 汇总角色输出和最终决策产物。
- 增加结构化 schema：
  - `bull_report.json`
  - `bear_report.json`
  - `risk_report.json`
  - `decision.json`
- 增加校验命令：`validate-agent-decision`。

### 目标文件

- `agent/roles/*.md`
- `script/agent_decision.py`
- `script/validate_agent_decision.py`
- `docs/contracts/agent-research.md`
- `tests/test_agent_decision.py`
- `tests/test_validate_agent_decision.py`

### 验收标准

- 角色输出必须引用中间报告中的 evidence id。
- Bull 和 Bear 报告都必须包含对立证据，不能只写单边结论。
- Risk Manager 必须包含失效条件、流动性/数据限制、组合暴露约束。
- Portfolio Manager 输出必须沿用现有信号语义：`plan_type=trade_plan/watch_only/no_trade`，`execution_status=conditional_executable/waiting_trigger/watch_only/no_trade`；不得新增 `conditional_plan` 之类的并行状态标签。
- `decision.json` 不得包含 broker order command。
- `validate-agent-decision` 必须对 `experimental=true` 或 `not_for_execution=true` 的占位决策返回失败，避免占位产物被下游误用。
- 只有在 trigger、invalidation、risk、TP1 都完整时，`decision.json` 才能转换成 draft Trade Plan Card。
- 校验器必须拦截确定性买卖指令和缺失风控的决策。

### 阶段门禁

在 `validate-agent-decision` 能拦截不安全或不完整决策前，不接入盘前/盘后主流程。

## Phase 4：接入盘前与盘后工作流

### 实现目标

用 agent research 增强现有日报工作流，同时保留当前报告输出和校验门禁。

### 实现范围

- 增加可选参数：
  - `pre-market-plan --include-agent-research`
  - `post-market-review --include-agent-research`
- `--include-agent-research` 只放在 `script/trading_copilot.py` wrapper 层：wrapper 调用独立 agent research 命令，再把 artifact path 注入 `next_agent_inputs`。
- `prepare_daily_context.py` 和 `prepare_market_snapshot.py` 保持确定性数据准备层，不混入 agent research 逻辑。
- 更新 `agent/daily_analysis_prompt.md` 和 `agent/post_market_analysis_prompt.md`：把决策作为证据输入，而不是订单输入。
- 保持现有输出契约不变：
  - `exec-brief.md`
  - `pre-market.md`
  - `pre-market-signals.json`
  - `post-market.md`
  - `post-market-signals.json`

### 目标文件

- `script/trading_copilot.py`
- `agent/daily_analysis_prompt.md`
- `agent/post_market_analysis_prompt.md`
- `docs/codex-automation-setup.md`
- `docs/cc-connect-scheduler.md`
- `tests/test_workflow_smoke_test.py`

### 验收标准

- 不传 `--include-agent-research` 时，现有工作流行为完全不变。
- 开启参数后，wrapper 的 `next_agent_inputs` 能引用 agent research artifacts。
- `prepare_daily_context.py` 和 `prepare_market_snapshot.py` 无行为变化，且不 import / 调用 agent research 模块。
- journal append 或 Longbridge sync 前仍必须通过 `validate-report` 和 `validate-trade-plan`。
- Agent decisions 可以把标的降级为 `watch_only` 或 `no_trade`，但不能单独作为升级为 `conditional_executable` 的理由；报告生成 LLM 可以在 refined rules、价格行为和完整 Trade Plan Card 同时满足时决定升级 session sidecar。
- fixture smoke tests 覆盖开启 agent research 的盘前和盘后流程。

### 阶段门禁

在带 agent research 的完整 dry run 通过现有 report validators 前，不接入生产定时任务。

## Phase 5：记忆层

### 实现目标

为角色推理增加持久化学习上下文，但不修改 approved trading rules。

### 实现范围

- 第一版使用 append-only Markdown：
  - `runtime/memory/trading_memory.md`
- 可选增加 SQLite：
  - `runtime/memory/trading_memory.sqlite`
- 新增命令：
  - `agent-memory-append`
  - `agent-memory-review`
  - `agent-memory-export`
- 把 decision 与现有 journal / paper execution review 中的 outcome 关联。

### 目标文件

- `script/agent_memory.py`
- `docs/contracts/agent-research.md`
- `docs/contracts/journal.md`
- `tests/test_agent_memory.py`

### 验收标准

- memory 记录包含 `date`、`symbol`、`decision_id`、`evidence_ids`、`decision_label`、`outcome_status`、`reflection`。
- 以 `decision_id` 实现幂等 append。
- Markdown memory 人类可读且 append-only。
- SQLite memory 可以从 Markdown 与 journal 记录重建或导出。
- memory 可以注入角色 prompt 作为上下文。
- memory 不得编辑 `knowledge/refined/`；规则晋升仍走现有人工 approval lesson 流程。
- memory 只能降低置信度或触发人工 review，不能提高 `execution_status` 或把 `watch_only/no_trade` 升级为可执行计划。

### 阶段门禁

在校验器能明确区分 memory 引用和当前 evidence 前，不允许 memory 影响 execution status。

## Phase 6：盘中候选闭环

### 实现目标

用新的 research/decision stack 增强盘中监控，但不立即开启自动盘中 broker 写操作。

### 实现范围

- 扩展 monitor 输出，生成 `monitor-signals.json`。
- 从 monitor evidence 生成 `agent-intraday-decision`。
- 增加可选 dry-run 路径：
  - `monitor-signals.json`
  - `validate-trade-plan --session monitor`
  - `paper-trade-preview --session monitor`
  - `paper-trade-submit --session monitor`，只能 dry-run；`--execute` 必须被 CLI 和执行配置双重拒绝
- 增加未来执行的独立 config gate：
  - `allow_intraday_entry_submit=false`
- monitor session 的 `--execute` 必须硬禁用；即使 config 打开，也不得提交盘中 broker write。

### 目标文件

- `script/monitor_scan.py`
- `script/extract_monitor_signals.py`
- `script/paper_trade_preview.py`
- `script/paper_trade_submit.py`
- `script/paper_execution_config.py`
- `config/paper_execution.json`
- `docs/workflows/monitor-brief.md`
- `tests/test_monitor_agent_decision.py`
- `tests/test_paper_trade_submit.py`
- `tests/test_paper_execution_config.py`

### 验收标准

- monitor 生成的候选默认仍是观察，不是交易指令。
- 盘中 dry-run 支持 cooldown、每日候选上限、symbol allowlist。
- 在明确契约前，不存在盘中 entry 的 `--execute` 路径。
- `paper_trade_submit.py` 必须有测试覆盖：`session=monitor` 时即使传入 `--execute` 也硬拒绝，不读取 broker credentials，不提交 broker write。
- `paper_execution_config.py` 必须有测试覆盖：monitor / intraday entry 的 capability 默认为 disabled，且不能被现有 `pre-market/post-market` gate 间接开启。
- Feishu summary 展示 candidate、blocked、skipped、data-quality reason。
- 现有 monitor workflow 保持向后兼容。

### 阶段门禁

在 paper execution state 能覆盖重复调度、部分成交、撤单、保护止损和 TP1 交互前，不实现盘中 broker writes。

## Phase 7：可选 LangGraph Backend

### 实现目标

在 artifact-first pipeline 稳定后，再评估 LangGraph 是否带来足够收益。

### 实现范围

- 增加可选依赖组或独立安装说明。
- 新增 `script/agent_graph_runner.py` 作为可选 orchestrator backend。
- 复用现有数据工具和 JSON artifacts；graph node 不直接调用 vendor API，必须走仓库 provider。
- 在 ignored runtime path 下增加 checkpoint 支持。

### 目标文件

- `requirements.txt` 或可选依赖说明
- `script/agent_graph_runner.py`
- `runtime/agent_graph/checkpoints/` ignored path
- `tests/test_agent_graph_runner.py`

### 验收标准

- 未安装 LangGraph 时，标准工作流仍能运行。
- Graph backend 产出与轻量 runner 相同的中间/最终 artifact schema。
- checkpoint 文件被 ignore，且可安全删除。
- graph resume 有 fixture tests 覆盖。
- graph backend 不可触达任何 paper broker write 操作。

### 阶段门禁

除非 LangGraph 在重试、可观测性、多角色编排上明显优于 artifact-first runner，否则保持可选。

## Phase 8：高级订单类型融合

### 实现目标

只有在 decision、memory、paper state 稳定后，才扩展更复杂的订单规划。

### 实现范围

- 扩展 `decision.json`，表达 bracket intent，但不包含直接 broker write command。
- 增加本地 bracket model，用 `intent_id` 绑定 entry、protective stop、TP1。
- OCO 和 cancel/replace 在 broker capability 与 state recovery 契约完成前保持 dry-run。
- Market order planning 只有在 slippage / liquidity 规则明确后才能增加。

### 目标文件

- `script/paper_order_models.py`
- `script/paper_order_sync.py`
- `script/paper_break_even_stop_plan.py`
- `script/paper_execution_config.py`
- `docs/paper-execution-roadmap.md`
- `tests/test_paper_order_models.py`
- `tests/test_paper_order_sync.py`

### 验收标准

- bracket/OCO intent 先进入本地 state，再考虑 broker-native OCO。
- 部分成交、子订单拒绝、重复调度、撤单失败都有明确状态转换。
- `paper_execution_config.py` capability matrix 清楚标记不支持的 broker actions。
- 在 slippage cap 和 liquidity check 通过验证前，market order execution 继续禁用。

### 阶段门禁

在 event replay 证明生命周期正确前，不开启 native OCO、cancel/replace 或 market order execution。

## 跨阶段验证

每个阶段都必须保留以下检查：

```bash
python3 -m py_compile script/*.py
python3 -m unittest discover tests
python3 script/trading_day_guard.py --date 2026-05-06 --format text
```

涉及工作流的阶段还必须运行：

```bash
python3 script/workflow_smoke_test.py --date 2026-05-26 --week 2026-W22
```

涉及模拟盘的阶段还必须运行聚焦测试：

```bash
python3 -m unittest \
  tests.test_paper_execution_config \
  tests.test_paper_order_sync \
  tests.test_paper_trade_submit \
  tests.test_paper_order_cancel \
  tests.test_paper_protective_stop_plan \
  tests.test_paper_take_profit_plan
```

## Rollout 策略

1. 每个阶段先以未接入主流程的命令或显式 flag 落地。
2. 先跑 fixture mode，再跑 live data mode。
3. live data mode 先人工运行，再接入 scheduler。
4. Feishu summary 用于可见性，不用于自动批准。
5. 在带 agent research 的验证通过前，生产盘前/盘后流程保持不变。
6. 所有 broker writes 继续留在专用 paper workflows 中，并受现有 `--execute` 与 config gate 控制。

## 成功定义

融合成功应满足：

- 当前盘前、盘后、monitor、paper workflows 保持向后兼容。
- Agent research artifacts 能解释一个标的是 `plan_type=trade_plan/watch_only/no_trade`，以及 `execution_status=conditional_executable/waiting_trigger/watch_only/no_trade` 中的哪一种。
- 角色推理引用结构化 evidence 和 memory，而不是自由发挥。
- 最终决策增强现有报告，但不削弱 validators。
- 模拟盘执行仍然显式、受门禁控制、幂等、且只针对 paper account。
