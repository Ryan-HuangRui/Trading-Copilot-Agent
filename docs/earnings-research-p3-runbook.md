# 财报研究 P3：NAS 日批次与静默交付

本手册描述已实现并通过工程回归的 P1/P2 与 P3 工作流。生产启用须同时具备 review、真实采集与模型角色验收、master 推送和 NAS 部署核验；将实际状态、cron 快照及发送回执记录在 NAS 的 `runtime/earnings/deployment-acceptance.json`，不以文档中的配置示例代替运行证据。

## 执行路径

`ops/cc-connect/tca-earnings-wrapper.sh` 将 stdout/stderr 全部重定向到 NAS 本地日志，解析本地 SEC 联系配置，然后调用 `script/earnings_continuation.py --send`。starter 只启动或确认一个后台 worker 后返回；worker 以冻结 cutoff、持久轮次和有界执行窗口运行 `earnings_daily.py`。每代 worker 到期但仍有进展时自动 handoff，同日可跨过 cron timeout 续接；完成或真实阻塞时才统一 finalization。全流程不使用交易日跳过，也不调用交易工作流。

日批次固定为 Asia/Shanghai 10:00，一天一次。当前真实时刻作为 UTC 资料截止；报告中的财务期间单独保留。初始化每天最多处理 5 家，采集限额与公司模型调用限额分开；普通日最多 10 次公司角色，强财报季 20 次，行业最多 5 次。实际调用前预留预算，进程失败和同日重启均不会重置预算。配置中的 token/currency 硬预算暂不支持：如非 null，runner 显式拒绝运行，不能声称订阅登录提供硬金额限额。

初始并发为 1。整个日批次默认最多 7200 秒，研究单角色最多 1800 秒；发布另行区分批次剩余预算、启动阈值和单次执行超时。默认完整 writer+checker 至少剩余 900 秒才启动，已完成 writer 的 checker 至少剩余 480 秒才启动，单次发布执行最多 900 秒。未达到阈值时保持排队。已完成 writer 可在次日以新的、有审计记录且最多一次的 checker attempt 继续，首次调用的绝对 deadline 和 timeout 不再永久锁死任务。未完成工作保留在状态库，后续日批次继续。P3 的日行业比较保留兼容；P4 已实现财政期间重叠映射与自动季度 DAG，但配置开关默认关闭，启用和恢复见 `docs/earnings-research-p4-runbook.md`。

`script/earnings_role_runner.py` 以显式 model/effort 启动独立 `codex exec`，使用 `--ignore-user-config --ephemeral --sandbox read-only`。模型返回 JSON，外层写报告、校验并登记；模型不负责写 state 或发送消息。CLI 事件中可用 usage 原样保存，缺失为 null。需要 NAS CLI 支持这些参数，不能悄悄降级模型或绕过沙箱。

## NAS 本地配置

以下两个文件属于忽略的 runtime，只在 NAS 配置，不提交 Git：

- `runtime/earnings/operator.json`：`{"sec_user_agent": "TradingCopilot operator <真实联系邮箱>"}`。真实邮箱须由操作者提供，不使用示例地址请求 SEC。
- `runtime/earnings/deployment.json`：schema_version=1、verified_repo（NAS 仓库绝对路径）、project、session、cc_connect_bin、codex_bin、verified_at、verified_from_cron_id、delivery_enabled、batch_timeout_seconds。

部署时从本仓库既有 cc-connect 定时任务核验 project/session、wrapper 所指仓库，再保存该路由。不能把“当前目录”当成通知路由，lark-cli 不能发送消息。P4 允许同一 runtime deployment 中显式配置用户身份 lark-cli，仅用于文档 create/update/fetch。`delivery_enabled` 初始为 false；真实研究验收通过后，先开启该开关完成一次明确标识的发送验收，再启用定时任务。

## 通知行为

正常日最多一条合并摘要。P3 首版只自动推送有证据、完整度至少 partial 的 thesis_state 新建或变化；普通数值更新和同一 thesis_state 下的细节变化先归档。摘要明确写“今日新增并回读全文 N 份”；N=0 时不得使用固定的全文交付成功话术。成功全文必须列真实云文档 URL，同日研究进展与问题尽量合并；没有正常摘要但连续失败时，失败通知只给稳定的问题分类和数量，同一问题指纹不重复发送，命令、内部路径和堆栈只留本地。

无工作或无值得推送的变化会落一份 suppressed 决策，不发送“执行成功”等消息。跨两个不同日批次持续失败才形成运行异常通知。达到重试上限的当前任务持续计入健康状态，不能因退出可执行队列而被误报为恢复；被新输入替代的旧失败保留为历史。公司/行业完整报告与通知状态分开；已登记报告即便此前外层进程中断，也可在下次 finalizer 恢复处理。

通知记录在 `runtime/earnings/outbox/<notification-id>/`：正文、冻结决策和发送回执分别存储。发送时复查目标、正文哈希和报告版本。成功后才写 sent；无法启动 sender 属 retryable_failed；已启动后超时、非零退出或进程中断均为 unknown，不自动再发。unknown 需人工核对飞书和本地回执后处理，不能重新跑研究作为发送重试。

若前一日轮次只剩通知债务，worker 先按原冻结摘要执行一次 finalizer，再把当日采集交给唯一的新轮次；sent、retryable_failed 和 unknown 都不会占掉当天的新 cutoff。失败或 unknown 继续保留在旧轮次，其中 unknown 不自动重发。交接期间旧 worker 在新 worker 记录落库前保持 owner，同日重复 starter 只复用现有轮次。

附件暂未启用，摘要必须自足。NAS 路径不包装成用户可直接访问的链接。

## 部署与验收顺序

1. 在隔离运行目录验证真实 SEC/IR 输入与实际 Codex 公司→行业→反证→综合角色；fixture 不得写入生产状态库。
2. review 通过，提交并推送 master；NAS 确认工作区干净后快进同步，核验 commit 与目标脚本。
3. 验证本地 deployment/operator 配置及两个模型 profile；先关闭 delivery，运行受限真实批次并审阅中文报告。
4. 用外层 finalizer 向已核验 session 发送一次明确标识的部署验收通知，确认回执。正常无变化批次必须零消息。
5. 用 cc-connect 新增独立 earnings 任务，先 disabled，设置 exec 为 wrapper，mute=true，session_mode=new_per_run，timeout_mins 大于外层批次超时。核验 NAS 时区 Asia/Shanghai 后采用 `0 10 * * *`。不得改变已有盘前/盘后/交易任务的 enabled 状态。
6. 启用后记录 cron id、commit、模型和发送验收证据；删除父任务的一小时兜底心跳。

手动命令：

```bash
python3 script/earnings_daily.py --collect-only
python3 script/earnings_daily.py --resume-only
python3 script/trading_copilot.py earnings-recovery --action resume-checker --job-id <EXACT_JOB_ID>
python3 script/trading_copilot.py earnings-recovery --action resume-checker --job-id <EXACT_JOB_ID> --execute
python3 script/trading_copilot.py earnings-recovery --config runtime/earnings/p4-config.json --deployment runtime/earnings/deployment.json --action reconcile-publication --job-id <EXACT_JOB_ID>
python3 script/trading_copilot.py earnings-recovery --config runtime/earnings/p4-config.json --deployment runtime/earnings/deployment.json --action reconcile-publication --job-id <EXACT_JOB_ID> --execute
python3 script/earnings_delivery.py --decision runtime/earnings/outbox/<ID>/decision.json
python3 script/earnings_delivery.py --decision runtime/earnings/outbox/<ID>/decision.json --execute
```

默认 daily CLI 不发送，只有 `--send` 或 NAS wrapper 会触发已开启的交付；delivery CLI 默认 preview。上述占位路径须替换成已生成文件。统一 CLI 已注册 earnings-daily 与 earnings-deliver，参数与对应脚本一致。

`earnings-recovery` 同样默认 preview，必须精确指定一个 job/task；`--execute` 前程序以 SQLite backup API 写入 `runtime/earnings/recovery/backups/`，并留下恢复审计，禁止对同一目标重复扩张尝试。`resume-checker` 只复用已冻结 writer，`schedule-repair` 只对已有 checker/validator 失败创建唯一修稿，`release-expired-task` 只处理已过期租约。`reconcile-publication` 必须显式传入本次生产实际采用的 `--config`；云模式还必须显式传入 `--deployment`，只读取其中的 user route 并绑定对应 route key，不初始化或调用 lark-cli。该动作不调用模型、不执行云操作、不改变 attempts；它只在 manifest/hash、clean checker、当前 synthesis 路径/hash、reader hash、job scope/version 以及当前 verified route（或显式配置的本地 archive 模式）全部精确匹配时补齐季度阶段，可安全重复执行。不要删除 SQLite、清空 publication 目录或全量重跑。

若 publication repair 的 semantic checker 已通过、仅因确定性解析器缺陷而终止，部署修复后先对精确 job 执行 `recheck-publication` preview，再加 `--execute`。该动作调用冻结 manifest 的零模型重检，成功后保留原 attempts 和失败记录，只把 job 推进到 `cloud_pending`；重检仍失败则不改变 job。

失败依赖复用必须先 preview，再对同一精确目标 apply：

```bash
python3 script/trading_copilot.py earnings-recovery --action reuse-dependency --task-id <FAILED_TASK> --reuse-task-id <OLDER_COMPLETED_TASK> --reason "source hash, semantic config and method are equivalent"
python3 script/trading_copilot.py earnings-recovery --action reuse-dependency --task-id <FAILED_TASK> --reuse-task-id <OLDER_COMPLETED_TASK> --reason "source hash, semantic config and method are equivalent" --execute
```

预览会拒绝 subject/期间、文档版本/hash、方法、模型配置或语义配置差异。执行保留失败 attempts，将其标为 `superseded`，重绑依赖并写审计；含修订或重大新事实时不得回退旧结果。

模型错误分为 quota 与 capacity：明确额度耗尽会立即熔断本批次后续模型调用，采集、状态和可恢复队列仍保留；容量不足只进入有限退避，不冒充额度耗尽，也不升级模型。`daily-result.json.usage_summary` 记录本批次真实调用数、缓存/非缓存输入、输出、缺失 usage 和缓存结果复用数；usage 缺失保持 null 语义，不填零。

公司研究任务的配置指纹只包含 source policy、daily profile 和公司研究重试/历史窗口；publication、cloud、通知和批次时间参数不再触发全量公司重研。升级时会复用除旧全量配置哈希外完全相同的 legacy frozen task；已运行任务继续使用创建时冻结的配置哈希。
