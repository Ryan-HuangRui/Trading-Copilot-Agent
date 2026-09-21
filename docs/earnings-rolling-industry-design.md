# 财报行业滚动研究与季度封板设计

## 状态与触发

- `disclosed` 只由冻结 public cutoff 前实际公开、且能映射到目标经营季度的原始披露决定；`fetched`、`researched` 与可读 `publication` 分别计数，互不代替。
- 行业阶段研究默认在固定样本达到 60% 披露时触发；六家公司采用向上取整，因此是四家。任一预先配置的关键龙头已披露也可触发有限样本阶段版。两家非龙头披露不触发模型。
- 触发后若没有足够的已验收公司研究，scope 进入 `waiting_for_research`，优先完成必需公司事实；至少一份 cutoff 合法的已验收输入到位后，依次执行 gap audit、industry、challenge、synthesis、writer、checker 与可选 cloud。阶段版不绕过任何核查门。
- `full` 仍要求配置的完整门槛、关键成员、已披露成员研究与 gap disposition。`stage` 表示有限样本，不得宣称行业完整；`revision` 表示同一季度的实质新增输入。重复披露/hash 不产生 revision。

## 冻结边界与迁移

- 每个在途 revision 冻结 expected/key 样本、public cutoff、已验收公司报告清单与指纹。后续研究完成或新披露只写入 pending boundary；当前 DAG 不重启，完成后合并为下一 revision。
- public cutoff 判断原始披露是否可用；research cutoff 判断后来生成的报告何时可被消费。二者分列保存。旧库把现有 `cutoff` 安全迁移为 `public_cutoff`，新增 `research_cutoff`；不可变 scope/revision 文件不覆写，新边界写入内容寻址文件。
- 非 tail 日也会从 cutoff 合法的实际披露经营期间发现季度并创建 scope。错峰财年继续使用最大自然季度重叠映射，报告必须展示公司实际期间，不能把披露月份冒充经营季度。

## 更新、封板与迟到更正

- 样本全部到齐且必要 DAG/核查完成，或到达配置的季度收尾日时进入封板检查。完整度和封板状态独立：缺口仍在时只能封为 `stage_final_with_gaps`，不能标为 full。
- 封板 publication 不覆写。迟到的重大披露或更正以带 reason 的新 revision 进入；无实质 input hash 变化不调用模型。tail end 是封板/补缺检查点，不是首次启动门槛。
- 市场层只消费每行业当前、已核查的 publication，并显式展示 edition、finalization state、样本与缺口。阶段行业可读报告可独立交付，但不能伪装成全市场正式完整报告。

## 失败依赖恢复

- 新任务失败后，旧完成任务只有在源文档版本/hash、语义配置、方法版本、subject 与经营期间完全一致，且旧产物仍通过文件/hash 校验时，才能通过 preview/apply 显式复用。审计记录新旧 task、等价证明与操作者理由；失败记录和 attempts 保留，新失败任务标记 `superseded` 而不删除。
- 任一源修订、重大新事实、方法或语义配置差异均禁止回退。不能证明等价时，行业 scope 排除该样本形成有限结论，或以 terminal blocker 明示等待；不得永久 queued。

## 可读报告数值绑定

- Markdown 表格的单位可来自数值列头，期间可来自同一行其他单元格；解析器只接受明确的 duration/instant 日期表达，并继续与 catalog 的值、单位、币种、期间、会计口径逐项相等校验。
- 错数、错单位、错期间、模糊数量仍拒绝。真实 managed-care 六个数用于离线回归；修复后只允许一次有理由的 publication recovery，原失败 job 与 checker 记录保留。

## 生产恢复与回滚

- 消费零售：先备份 SQLite，preview public/research cutoff 迁移和 member audit，再 apply；不可变输入不改写。若核对异常，恢复 DB 备份并保留新内容寻址文件供审计。
- 半导体：对 AMAT 旧完成任务执行等价性 preview；只有全部证明通过才 apply supersession/reuse，否则保留失败并把行业版标为有限样本或 blocked。
- 医疗保险：部署 parser 后对原 repair manifest 做离线 deterministic recheck；确认六个绑定及负例后，才对精确 publication job 执行一次恢复。回滚代码不会删除任何失败/attempt 记录。
