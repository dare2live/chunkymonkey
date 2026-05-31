请先依次阅读以下文件，不要跳过任何一个：
1. `analysis/handoff_20260527.md` — 上个 session 的详细交接（改了什么、剩什么、怎么继续）
2. `analysis/codex_bootstrap_20260527.md` — 全量项目手册（审计工具×11、skill×8、hook×10、红线×8、反例×10、工作流）
3. `SESSION_HANDOFF.md` — 自动更新的运行时状态

任务：按 handoff §3 任务清单继续架构优化，直至验收标准（handoff §7）全部通过；当前优先级是第 55 刀后的 updater 架构收薄与 dirty bucket 化清理，不要回到已完成的 annotation 修复。

三条铁律：
- .py 改动必须 Codex review 再 commit（Rule 10 blocking）
- 每次改动后必跑 codegraph + complexity 双扫（bootstrap §C1 工作流）
- 执行前必须先与 Codex 讨论方案（/grill-with-docs）

第一个任务：重新跑 `git status --short`、`codegraph status .`、`codegraph context "updater architecture next slice"`，再按 `analysis/codex_worktree_organization_20260521.md` 的 bucket 顺序继续：先完成/review bucket C updater split，随后才处理文档 ledger、archive cleanup、governance bucket；不要裸 commit，不要 `git add .`。
