# 财报研究 P3：NAS 日批次与静默交付

本手册描述已实现并通过工程回归的 P1/P2 与 P3 工作流。生产启用须同时具备 review、真实采集与模型角色验收、master 推送和 NAS 部署核验；将实际状态、cron 快照及发送回执记录在 NAS 的 `runtime/earnings/deployment-acceptance.json`，不以文档中的配置示例代替运行证据。

## 执行路径

`ops/cc-connect/tca-earnings-wrapper.sh` 将 stdout/stderr 全部重定向到 NAS 本地日志，解析本地 SEC 联系配置，然后调用 `script/earnings_daily.py --send`。外层 Python 持有独占批次锁，执行采集、公司角色、受影响行业角色和最终交付。全流程不使用交易日跳过，也不调用交易工作流。

日批次固定为 Asia/Shanghai 10:00，一天一次。当前真实时刻作为 UTC 资料截止；报告中的财务期间单独保留。初始化每天最多处理 5 家，采集限额与公司模型调用限额分开；普通日最多 10 次公司角色，强财报季 20 次，行业最多 5 次。实际调用前预留预算，进程失败和同日重启均不会重置预算。配置中的 token/currency 硬预算暂不支持：如非 null，runner 显式拒绝运行，不能声称订阅登录提供硬金额限额。

初始并发为 1。整个日批次默认最多 7200 秒，单角色最多 1800 秒；未完成工作保留在状态库，后续日批次继续。P3 的日行业比较保留兼容；P4 已实现财政期间重叠映射与自动季度 DAG，但配置开关默认关闭，启用和恢复见 `docs/earnings-research-p4-runbook.md`。

`script/earnings_role_runner.py` 以显式 model/effort 启动独立 `codex exec`，使用 `--ignore-user-config --ephemeral --sandbox read-only`。模型返回 JSON，外层写报告、校验并登记；模型不负责写 state 或发送消息。CLI 事件中可用 usage 原样保存，缺失为 null。需要 NAS CLI 支持这些参数，不能悄悄降级模型或绕过沙箱。

## NAS 本地配置

以下两个文件属于忽略的 runtime，只在 NAS 配置，不提交 Git：

- `runtime/earnings/operator.json`：`{"sec_user_agent": "TradingCopilot operator <真实联系邮箱>"}`。真实邮箱须由操作者提供，不使用示例地址请求 SEC。
- `runtime/earnings/deployment.json`：schema_version=1、verified_repo（NAS 仓库绝对路径）、project、session、cc_connect_bin、codex_bin、verified_at、verified_from_cron_id、delivery_enabled、batch_timeout_seconds。

部署时从本仓库既有 cc-connect 定时任务核验 project/session、wrapper 所指仓库，再保存该路由。不能把“当前目录”当成通知路由，lark-cli 不能发送消息。P4 允许同一 runtime deployment 中显式配置用户身份 lark-cli，仅用于文档 create/update/fetch。`delivery_enabled` 初始为 false；真实研究验收通过后，先开启该开关完成一次明确标识的发送验收，再启用定时任务。

## 通知行为

正常日最多一条合并摘要。P3 首版只自动推送有证据、完整度至少 partial 的 thesis_state 新建或变化；普通数值更新和同一 thesis_state 下的细节变化先归档。此规则便于控制初期通知质量，可在实际样本评估后增加重要数值变化规则。

无工作或无值得推送的变化会落一份 suppressed 决策，不发送“执行成功”等消息。跨两个不同日批次持续失败才形成运行异常通知。达到重试上限的当前任务持续计入健康状态，不能因退出可执行队列而被误报为恢复；被新输入替代的旧失败保留为历史。公司/行业完整报告与通知状态分开；已登记报告即便此前外层进程中断，也可在下次 finalizer 恢复处理。

通知记录在 `runtime/earnings/outbox/<notification-id>/`：正文、冻结决策和发送回执分别存储。发送时复查目标、正文哈希和报告版本。成功后才写 sent；无法启动 sender 属 retryable_failed；已启动后超时、非零退出或进程中断均为 unknown，不自动再发。unknown 需人工核对飞书和本地回执后处理，不能重新跑研究作为发送重试。

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
python3 script/earnings_delivery.py --decision runtime/earnings/outbox/<ID>/decision.json
python3 script/earnings_delivery.py --decision runtime/earnings/outbox/<ID>/decision.json --execute
```

默认 daily CLI 不发送，只有 `--send` 或 NAS wrapper 会触发已开启的交付；delivery CLI 默认 preview。上述占位路径须替换成已生成文件。统一 CLI 已注册 earnings-daily 与 earnings-deliver，参数与对应脚本一致。
