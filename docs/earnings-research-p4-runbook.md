# 财报研究 P4：季度研究、读者报告与用户云文档

P4 已实现但自动开关默认关闭。它复用每日 10:00 Asia/Shanghai 唯一入口，不新增高频任务。启用前必须在独立 NAS 状态目录完成真实公司、至少一个成熟行业和跨行业依赖门槛验收；fixture 只能证明离线工程行为。

## 状态和预算

- `runtime/earnings/state.sqlite` 增量增加 publication 索引和云交付状态；P3 表保持兼容。
- `runtime/earnings/quarterly.sqlite` 冻结 industry-quarter 成员和方法，并逐 revision 保存统一 cutoff、阶段状态与历史；新验收公司输入只在前一 revision 完成后开启新 revision。
- `runtime/earnings/daily.sqlite` 持久化 company、quarterly、writer、checker、cloud 的每日实际尝试次数；跨日重新获得额度，未完成任务保留。恢复队列先获得有界时间片，新研究和季度阶段也保留时间片。
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

真实值只写忽略的 `runtime/earnings/deployment.json`：

```json
{
  "lark_documents": {
    "enabled": true,
    "lark_cli_bin": "/absolute/path/to/lark-cli",
    "profile": "explicit-user-profile",
    "user_route": "opaque-user-destination-id",
    "as": "user",
    "parent_position": "my_space"
  }
}
```

adapter 的参数形状按官方 lark-cli 文档：它把 cwd 固定到准备目录，Markdown 文件使用 `docs +create --doc-format markdown --content @./prepared.md`，回读使用 `docs +fetch --doc <id>`，整篇受控更新使用 `docs +update --doc <id> --command overwrite --content @./prepared.md`。运行时仍应以已安装 1.0.92 的 `--help` 为准：[lark-doc create](https://github.com/larksuite/cli/blob/main/skills/lark-doc/references/lark-doc-create.md)、[lark-doc skill](https://github.com/larksuite/cli/blob/main/skills/lark-doc/SKILL.md)。

先保持跟踪配置 `delivery.lark_documents_enabled=false`。验证绝对二进制、profile、用户 docs/drive 授权及目标目录后再在部署副本开启。程序仅运行 docs create/update/fetch，使用 argv，不调用 shell，不登录、不授权、不回退 bot、不发送消息。

创建超时或非确定结果进入 `unknown`，不得再次 create。人工从 CLI/云空间确认 document_id 后，用 `--reconcile-document-id <ID> --reconcile-url <URL> --execute` 只读回查。`auth_failed` 只在部署配置写入更晚的 `credentials_refreshed_at` 后重试。远端正文不同进入 conflict；不要覆盖用户编辑。先只开本地 publication、以后再开 cloud 时，`archived` job 会直接续传，不重跑 writer/checker。

## 启用、验收与回滚

1. 运行语法和 earnings 全部单测，确认旧日报/通知回归。
2. 隔离状态目录运行真实公司 research → writer → checker → cloud readback；检查数字、期间、表格和引用。
3. 对材料充分行业运行 coverage → industry → challenge → synthesis → publication；不足则应保持 stage。
4. 只有五个冻结行业均为 full/revision 且核对通过，才允许正式“美股重点行业季度研究”。
5. 开启 tracked/runtime 的季度和文档开关后，仍只保留现有 10:00 muted cron。检查 daily-result、quarterly.sqlite、publication manifest、cloud state 和 outbox decision。

回滚只需关闭三个 activation flags；不删除 SQLite、不可变报告或远端文档。需要重新处理时修正缺口/身份后触发新输入或显式 reconcile，禁止删除状态后盲目重建。
