# 财报研究：目标冻结与可续接执行设计

状态：2026-09-20 已批准实施。范围限定为当前 5 个行业、30 家公司，不引入新数据源、模型或交易工作流。

## 目标状态

- 每个研究轮次先冻结 `batch_date`、UTC `public_cutoff` 和单调递增 `revision`。轮次只处理该 cutoff 前已公开且可证明的材料；之后出现的材料进入下一轮，不能反复重建当前行业 DAG。
- 最新公司研究、当前行业证据缺口、行业 `industry → challenge → synthesis`、reader writer/checker 和 verified cloud 优先。历史独立研究及历史 publication backfill 默认暂停；历史披露仍可作为当前报告的同比、季节性、低基数和反证输入。
- `daily_company_limit` 等次数是单个执行窗口的软配额，不是整日完成判定。每个模型调用、缓存复用、用量缺失、窗口和轮次进度都保留审计记录。
- 年报、累计期和单季保持严格分离。当前季度成熟度由实际经营期间与冻结样本判断；最新错峰财季公司研究不受自然季度日历筛掉。

## 调度与续接

`earnings_continuation.py` 是 cc-connect wrapper 的唯一子入口。cron 进程只启动或确认一个受锁保护的后台 worker 后立即返回；worker 持久化 `runtime/earnings/continuation.sqlite`，并启动多个有界 `earnings_daily.py` 窗口：

1. 新轮次冻结 cutoff，并只在第一个窗口采集；后续窗口使用相同 cutoff 和 `--resume-only`。
2. 每个窗口有独立 `execution_window_id`，旧的日配额按窗口计数；任务 attempts、租约、季度 revision、publication/cloud 幂等状态仍由原 SQLite 持久化并跨日保留。
3. 中间窗口 `--defer-finalize`，安全完成当前子进程后 checkpoint。每代 worker 自身有界；期限到而仍有真实进展和待办时，先登记再启动下一代 worker，随后退出。这样可跨过 150 分钟 cron 外层 timeout 在同日自动续接，又不形成一个无界前台进程。
4. 只有完成、quota/capacity、永久失败或无进展等停止条件才使用 `--finalize-only` 做一次汇总/去重交付；代际 handoff 不发送中间通知。
5. 无待办、连续无进展、quota 熔断或永久失败只剩人工任务时停止。quota/capacity 不伪装为完成；暂停轮次可由下次 cron 继续同一 cutoff。完成后才关闭轮次，下一次 cron 冻结新 cutoff/revision。

该 runner 不空轮询、不保持无限前台进程，也不猜测 cc-connect 参数。wrapper 仍由现有 muted cron 调用；仅把内部 `earnings_daily.py` 替换为 continuation starter。单窗口、单模型调用 timeout 和有限 retry 保持不变。

## 完成、失败与公平性

- 轮次完成要求 cutoff 内 current company 队列、当前行业/季度可执行阶段、当前 publication writer/checker/cloud 均无可执行待办。terminal failure、unknown cloud、auth/conflict 和可操作期间缺口属于阻塞/人工状态，不得计为目标完成。
- 公司 current 队列可在多个窗口持续推进；不预留历史名额。行业和 publication 使用独立软配额及现有 phase reserve，历史工作不能消耗它们。
- 季度 scope 的 cutoff 在同 revision 内固定。只有下一轮合法新证据才触发新 revision，因此 challenge/synthesis 不会被同轮新时钟或逐公司到达反复饿死。
- 通知只在 outer finalizer 汇总。相同 publication/version/destination 沿用现有 outbox 去重；迟到完成可在后续轮次补充，同版本不重复。

## 迁移、部署与回滚

- SQLite 增量只新增 `daily.execution_windows` 和独立 continuation 状态；`reservations.day` 对新运行保存不可重复的窗口 scope，旧日期记录原样保留。不修改 research task、attempt、artifact 或 company semantic hash。
- tracked 配置把历史独立研究和 publication backfill 默认配额设为 0；这两个调度项不在 company research semantic hash 中，无需伪造旧配置等价。生产 runtime 配置应按键合并，保留身份、activation flags 和 frozen legacy snapshot registry。
- 部署时保持原 cron、`mute=true` 和 150 分钟 timeout，只更新 wrapper 指向 continuation starter；先备份 runtime SQLite/config，再运行离线测试和一次 `python3 script/earnings_continuation.py --status` 状态检查。确认 NAS 允许 wrapper 子进程以 `start_new_session` 留存；若宿主统一清理进程组，必须改用该主机已有的受管理 service，而不能退回“等次日”。
- 回滚可把 wrapper 恢复为直接调用 `earnings_daily.py`，并恢复两个软配额；保留 continuation SQLite 和所有不可变产物，不删除状态、不重置 attempts。

## 受影响模块

- `script/earnings_continuation.py`：轮次、窗口、进度/停止原因和最终汇总。
- `script/earnings_daily.py`：显式 cutoff/date/window、窗口软配额、延迟/仅 finalization、可审计 backlog/progress。
- `ops/cc-connect/tca-earnings-wrapper.sh`：调用 continuation runner。
- 财报 Skill、合同、P4 runbook、配置与离线测试：同步新运行契约。
