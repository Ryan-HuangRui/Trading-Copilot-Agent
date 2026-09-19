# 财报研究 P4：季度研究、读者报告与用户云文档

P4 已实现但自动开关默认关闭。它复用每日 10:00 Asia/Shanghai 唯一入口，不新增高频任务。启用前必须在独立 NAS 状态目录完成真实公司、至少一个行业研究链和跨行业依赖门槛验收；材料不足的行业保持阶段版，fixture 只能证明离线工程行为。

## 状态和预算

- `runtime/earnings/state.sqlite` 增量增加 publication 索引和云交付状态；P3 表保持兼容。
- `runtime/earnings/quarterly.sqlite` 冻结 industry-quarter 成员和方法，并逐 revision 保存统一 cutoff、阶段状态与历史；新的已验收公司输入可触发修订，无需等待前一阶段版或跨行业报告完成。没有任何公司研究证据的行业只记录待补资料，不启动模型。
- `runtime/earnings/daily.sqlite` 持久化 company、quarterly、writer、checker、cloud 的每日实际尝试次数；跨日重新获得额度，未完成任务保留。恢复队列先获得有界时间片，新研究和季度阶段也保留时间片。
- publication 的缓存结果不计为新模型调用；失败结果保留 semantic/deterministic 错误，不能进入 `cloud_pending`。每日最多一次自动定向 repair，仍占 writer/checker 与 repair 预算。当前公司报告先于历史发布，历史发布默认每日最多 1 份。
- `runtime/earnings/quarterly-scopes/<scope_id>/gap-reviews/<input_hash>/input.json` 是按 scope revision/cutoff/公司报告哈希生成的不可变审查输入；`gap-review-input.json` 只是当前指针。启用季度自动任务后，daily runner 使用 `review` profile、独立每日额度和有租约的 bounded attempt 自动审查，再调用 `record_gap_review` 验收。失败可跨日恢复，达到 `max_task_attempts` 后终止；证据不完整必须保持 unresolved。

默认关闭 `quarterly.automatic_trigger_enabled`、`quarterly.weekly_review_enabled`、`publication.enabled`、`delivery.lark_documents_enabled`。任务并发仍为 1；季度、writer、checker 各有独立每日尝试上限，usage 不可用时保存 null。

## 运维命令

```bash
python3 script/trading_copilot.py earnings-review-context --date 2026-09-16 --cutoff 2026-09-16T02:00:00Z
python3 script/trading_copilot.py earnings-review-context --date 2026-09-16 --cutoff 2026-09-16T02:00:00Z --manual-quarter 2026-Q2
python3 script/trading_copilot.py earnings-review-context --cutoff 2026-09-16T02:00:00Z --scope-id <scope_id> --record-gap-review runtime/earnings/<review-result>.json
python3 script/trading_copilot.py earnings-daily --resume-only --manual-quarter 2026-Q2
python3 script/trading_copilot.py earnings-publication-runner --type company --scope NVDA --quarter 2026-Q2 --source-report report/earnings/<accepted>.json
python3 script/trading_copilot.py earnings-lark-document --publication-manifest report/earnings/publications/<manifest> --execute
```

`earnings-publication-runner` 未加 `--execute` 只冻结输入；执行时还需 `--codex-bin`。日批次从 deployment 读取已核验 Codex 路径。财务事实按 Decimal 读取并保留 XBRL metric、currency、unit、accounting_basis、period、derivation；每次正文出现或表格单元格由 checker 以 occurrence 绑定到证据。runner 把原始 Markdown 的 Python codepoint span（保留千分位逗号）或一基表格行列 inventory 交给 checker，模型不自行数偏移；稿件/输入/source 哈希也由 runner 注入。daily 贯穿本次实际 `--config` 路径，runtime profile override 不会退回 tracked 默认配置。

季度覆盖允许从已验收公司报告的 evidence/fact period 解析 standalone period，但必须同时绑定 document id/version/hash；这不会修改 event、document 或公司报告。已公开元数据、有效本地原文和已验收研究分别计入 disclosed、fetched、researched。新证据可 supersede 未完成的 stage 版而无需等待跨行业 market；market 冻结各行业不同 cutoff，并以最晚合法输入作为整体 cutoff。

## 文档部署配置

部署应使用已通过用户身份读取并核验的云空间根目录 token；不要使用未验证的位置别名。真实值只写忽略的 `runtime/earnings/deployment.json`：

```json
{
  "lark_documents": {
    "enabled": true,
    "lark_cli_bin": "/absolute/path/to/lark-cli",
    "profile": "explicit-user-profile",
    "user_route": "opaque-user-destination-id",
    "as": "user",
    "parent_token": "verified-user-root-folder-token"
  }
}
```

adapter 的参数形状按官方 lark-cli 文档：它把 cwd 固定到准备目录，Markdown 文件使用 `docs +create --doc-format markdown --content @./prepared.md`，回读使用 `docs +fetch --doc <id>`，整篇受控更新使用 `docs +update --doc <id> --command overwrite --content @./prepared.md`。运行时仍应以已安装 1.0.92 的 `--help` 为准：[lark-doc create](https://github.com/larksuite/cli/blob/main/skills/lark-doc/references/lark-doc-create.md)、[lark-doc skill](https://github.com/larksuite/cli/blob/main/skills/lark-doc/SKILL.md)。

先保持跟踪配置 `delivery.lark_documents_enabled=false`。验证绝对二进制、profile、用户 docs/drive 授权及目标目录后再在忽略的 `runtime/earnings/p4-config.json` 部署副本开启。唯一财报 cron 的 exec 在现有 wrapper 后追加 `--config runtime/earnings/p4-config.json`，保留时间、静默与项目/会话绑定。程序仅运行 docs create/update/fetch，使用 argv，不调用 shell，不登录、不授权、不回退 bot、不发送消息。

创建超时或非确定结果进入 `unknown`，不得再次 create。人工从 CLI/云空间确认 document_id 后，用 `--reconcile-document-id <ID> --reconcile-url <URL> --execute` 只读回查。`auth_failed` 只在部署配置写入更晚的 `credentials_refreshed_at` 后重试。远端正文不同进入 conflict；不要覆盖用户编辑。先只开本地 publication、以后再开 cloud 时，`archived` job 会直接续传，不重跑 writer/checker。

## 启用、验收与回滚

1. 运行语法和 earnings 全部单测，确认旧日报/通知回归。
2. 隔离状态目录运行真实公司 research → writer → checker → cloud readback；检查数字、期间、表格和引用。
3. 对材料充分行业运行 coverage → industry → challenge → synthesis → publication；不足则应保持 stage。
4. 只有五个冻结行业均为 full/revision 且核对通过，才允许正式“美股重点行业季度研究”。
5. 开启 tracked/runtime 的季度和文档开关后，仍只保留现有 10:00 muted cron。检查 daily-result、quarterly.sqlite、publication manifest、cloud state 和 outbox decision。

回滚只需关闭四个 activation flags；不删除 SQLite、不可变报告或远端文档。需要重新处理时修正缺口/身份后触发新输入或显式 reconcile，禁止删除状态后盲目重建。

## 发布核对失败后的有限修复

数字目录由程序从已验收证据生成，保留发行人、指标、币种、口径、期间和来源定位。writer 只使用目录中的展示值；派生量仅允许同发行人且口径兼容的指定操作。checker 独立选择事实/派生 ID 并核对语义，程序补全已验证的来源元数据，原始 checker 输出保留用于审计。

已完成但未通过核对的稿件，可显式执行一次 `earnings-publication-runner --repair <failed-input-manifest.json> --execute --codex-bin <absolute-path>`。它复用冻结研究，将错误反馈给新 writer，再独立核对；每次修复至多两个模型调用，不重复研究，不覆盖父稿，不能嵌套修复。daily 会把首次完整 checker/validator 失败排入同一套唯一 repair；人工入口只用于审查后精确恢复旧生产卡单，不增加第二次 repair。失败或结果不明的模型调用不会自动重复；保留状态等待审查。修复也未通过时仍禁止发布。

九个月/半年累计事实不得混入单季语境，“本次资料未包含”不得扩大成“公司未披露”；期限类事实（例如 6.4 年）也必须出现在 occurrence inventory 并绑定目录事实。

独立语义核对已通过、仅程序校验误判时，修复 validator 后可使用 `earnings-publication-runner --recheck <input-manifest.json>`。该入口不调用模型，核对原稿、原 checker、研究输入和 manifest 哈希，重新校验并生成带代码哈希的独立审计记录；不能用于放行语义核对失败的稿件，也不覆盖旧失败记录。

### runtime 配置增量迁移

NAS 的 `runtime/earnings/p4-config.json` 应从新 tracked 配置合并下列预算键，保留现有 activation flags、路径和身份配置，不整文件覆盖：`company_history_limit=2`、`publication_repairs_per_day=1`、`publication_full_start_threshold_seconds=900`、`publication_checker_start_threshold_seconds=480`、`publication_stage_timeout_seconds=900`、`phase_reserve_seconds=1200`。`company_history_limit` 是每日公司总预算内的历史回补硬上限，其余容量优先当前期和关键公司缺口；已有 `daily_company_limit`/`strong_season_company_limit` 不扩大。部署前先用 `earnings-recovery` preview 精确列出待恢复 job；备份后只恢复所选任务。

升级 scoped company configuration hash 前，先把部署前 `p4-config.json` 的**原始字节**复制到 `runtime/earnings/`，不要格式化或重写；随后运行 `python3 script/earnings_config_migration.py --snapshot runtime/earnings/<原始副本>.json` 预览 raw SHA-256 与 semantic basis。核对该 raw hash 与旧任务的 `configuration_hash` 分布后，再加 `--execute` 注册。工具在日批次共享锁内按原始字节归档到 `runtime/earnings/config-migrations/snapshots/` 并写审计 registry。只有 raw hash、快照文件 hash、semantic basis、model/effort/method 和其余冻结证据全部一致时才复用旧 task/report；未知旧 hash 保持待迁移并报人工核对，不自动全量重研。该注册不修改 immutable `task_inputs`、任务 attempts 或已完成报告。
