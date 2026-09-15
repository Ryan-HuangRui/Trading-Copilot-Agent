# 财报研究 P1/P2 本地运行手册

P1/P2 提供原文采集、财务规范化、SQLite 队列、角色输入、验证和登记。它们不会调用模型、部署定时任务或发送飞书。语义研究由显式选择模型与 effort 的独立 Codex 角色运行完成，初始并发为 1。

## 1. 显式采集模式

SEC live 模式需要包含真实联系人邮箱的 `TCA_SEC_USER_AGENT`，不会回退 fixture：

```bash
python3 script/trading_copilot.py earnings-collect \
  --mode live --symbol NVDA --max-filings 2 \
  --cutoff 2026-09-14T02:00:00Z
```

历史回填与增量分开标记。incremental 首次只发现 cutoff 前配置 overlap 窗口，之后从成功 checkpoint 回退 overlap；reconcile 使用独立持久 cadence，停机恢复只读取日期范围与缺口相交的历史 submissions 文件。`--collection-kind initialization` 使用配置中的初始化公司数和八季度窗口预算渐进处理；历史索引按日期范围由新到旧读取，达到所需实际期间后停止。年度报告只占一个预期年末位置，八份年度 20-F 不算八季度。`--max-filings` 仅限制本轮抓取；已发现项先全部进入持久 `source_items`，预算剩余不会因 checkpoint 推进而丢失：

```bash
python3 script/trading_copilot.py earnings-collect \
  --mode live --collection-kind initialization \
  --symbol NVDA --symbol AMD --cutoff 2026-09-14T02:00:00Z
```

公司 IR 不做不受控网页发现。将已核验的原文 URL 写入一个 ignored 路径的 JSON manifest，以 `--ir-manifest` 显式传入；manifest 必须声明 `source_mode=live`。SEC 负责 CIK/主体解析，ticker 只是待核验 seed。

离线模式只接受声明 `source_mode=fixture` 的固定资料包：

```bash
python3 script/trading_copilot.py earnings-collect \
  --mode offline --input tests/fixtures/earnings/sample_bundle.json \
  --cutoff 2026-09-14T02:00:00Z
```

fixture 会在全部下游产物中保持 fixture provenance，不能作为 live 研究。公开时间晚于 cutoff 的版本不会进入上下文；来源失败登记为失败，而不是“无披露”。

定期报告以申报的财务期归组；`8-K`/`6-K` 的 filing date 不能冒充季度。采集器会读取 filing index，优先使用 SEC document type，并对嵌入文件名中的 `ex991` 使用带 limitation 的受限启发式；EX-99.2、图片和无关 blob 不进入候选。14 位 SEC acceptance 按 America/New_York 解释并转 UTC。缺失的可选 SEC 元数据保留空值，不会错位拼接其他行。

## 2. 公司角色

领取发生输入变化的公司任务并生成不可变输入：

```bash
python3 script/trading_copilot.py earnings-context \
  --cutoff 2026-09-14T02:00:00Z --limit 1 --run-id manual-company-001
```

输出明确记录任务租约、来源版本、model/effort、输入哈希与唯一允许的产物路径。任务领取时冻结配置哈希、文档版本、Company Facts 版本和既有报告；每个尝试写入独立的 `attempt-N` 目录，并登记不可变 manifest 哈希。若输入未变化或没有可执行任务，命令返回 `skipped`，此时不能启动模型。公开时间未知、或只有日期且与 cutoff 同日的原文，不能证明在 cutoff 前可用：任务会延后或失败，不会交给模型。

使用 `ops/cc-connect/tca-earnings-role.prompt.md` 启动一个独立 Codex 运行，模型和 effort 必须精确使用 manifest 的 `profile`。完成后：

```bash
python3 script/trading_copilot.py validate-earnings-research --report <REPORT_JSON> --manifest <INPUT_MANIFEST>
python3 script/trading_copilot.py earnings-record --report <REPORT_JSON> --manifest <INPUT_MANIFEST>
```

## 3. 行业、反证与综合闭环

日频行业增量可使用 `--mode daily`。手工季度研究使用同一接口，但必须按顺序建立独立角色任务：

```bash
python3 script/trading_copilot.py earnings-industry-context \
  --industry semiconductors --role industry --mode quarterly \
  --period-start 2026-04-01 --period-end 2026-06-30 --cutoff 2026-09-14T02:00:00Z

python3 script/trading_copilot.py earnings-industry-context \
  --industry semiconductors --role challenge --mode quarterly \
  --period-start 2026-04-01 --period-end 2026-06-30 --cutoff 2026-09-14T02:00:00Z \
  --predecessor-report <REGISTERED_INDUSTRY_JSON>

python3 script/trading_copilot.py earnings-industry-context \
  --industry semiconductors --role synthesis --mode quarterly \
  --period-start 2026-04-01 --period-end 2026-06-30 --cutoff 2026-09-14T02:00:00Z \
  --predecessor-report <REGISTERED_INDUSTRY_JSON> \
  --predecessor-report <REGISTERED_CHALLENGE_JSON>
```

每一步均需由独立 Codex 角色完成、校验并登记，下一步才可领取。季度输入保留固定覆盖分母、关键缺口、平淡/负面和未入选样本；反证先读原文，综合必须处理全部重大反证。行业链只允许同一 `source_mode`，并冻结所有公司报告、源文档和前序报告的哈希。季度 `full` 还要求披露覆盖率、关键公司、必要公司研究和关键供应链缺口均满足；否则只能是 `partial` 或 `insufficient`。自动季度触发属于 P4，不在本阶段伪装实现。

## 4. 状态与恢复

```bash
python3 script/trading_copilot.py earnings-status
```

状态输出包括已核验主体、文档/事件数、持久发现队列、任务队列状态、最老任务年龄、独立来源水位与未解决来源失败。`running` 任务只有租约过期后才能被恢复；达到最大尝试数的过期任务转为 terminal，依赖未完成、预算未领取的任务保持 queued。登记在同一 SQLite 事务内复核租约、冻结输入、依赖、新旧任务和 attempt manifest；重复登记相同产物幂等，不同产物或过期租约会被拒绝。不同 run/task 使用隔离目录，已完成报告不可覆盖。

所有命令的默认批次日期均取 Asia/Shanghai；原文 cutoff 与公开时间继续使用 UTC。即使参数、JSON 或路径错误，也返回统一 `success/skipped/failed` envelope，不以空模型输入掩盖失败。所有输入输出路径在读写前约束到配置指定的 raw/runtime/report 根目录。

金额计算只在币种、单位、会计基础、分部和维度一致时执行；累计期间可差分为单季，年度可在同口径下减九个月得到 Q4。差分保留两个组件各自的 accession、tag、period、value 和 unit。Company Facts 是当前聚合接口，不是历史时点快照；filed-date 过滤不能单独证明无前视，历史结论仍须引用 manifest 中同期原始 filing。缺失值保留 null，亏转盈不计算无意义同比百分比。

P3 已提供日批次锁、只读模型 runner、预算与最终通知判定，见 `docs/earnings-research-p3-runbook.md`。季度自动到期检查仍属 P4；当前季度链由操作者显式触发。
