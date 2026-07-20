# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-20（账号切换交接）
> 手写：objective / 已裁决 / 禁令 / 下一步。状态投影见 `BOARD.md`（生成，勿手改）。
> 完成证据追加到 `analysis/project_state_ledger.md`。
> **跨账号交接全文**：`analysis/account_switch_handoff_20260720.md`

## 当前 objective

**轨道 = A→H 恢复中**（owner 2026-07-20）。Agent-OS 核心 WP0–WP4 已闭合；WP6 仪式影子期仍开放（与数据 cutover 无关）。

已落地硬事实（勿回滚）：
- C + B-pit **`cutover_allowed=true`**（commit `b38e9ac5`）→ resolver `ACCEPTED_CUTOVER` / `MART_CUTOVER`
- daily+ST+form/qfq/pulse 前沿 **`20260720`**
- Phase D research_runtime **FIXED**（persist + fold + measured offline）
- Delivery-OS：eng_gov **§15**（并行 subagent、异步 CI、L1 docs skip CI）

启动：`scripts/chunkyctl agent-boot`；状态：`BOARD.md`。

## 下一步（给下一账号优先）

1. **完成 dual-track 退役收尾**（若本提交已含 pulse drill 单轨：验证 + 补删任何仍绕过 resolver 的旁路；见 `data/lineage/legacy_retire_notes.md`）
2. **Enrich Tier1 accept**：`20260717` accepted stock_states 仍为 scaffold（`form_name`/`axis_pos` null）— 富形态仍在 `fact_stock_form_daily`；cutover-ON 日暴露 scaffold 是诚实而非回退
3. **Accept Tier1/2 for `20260720`**（及之后 eligible 日）— 今日缺 accept → fail-closed LEGACY
4. **A→H 下一业务刀**：F 主升浪 B0–B2 挂共享 `research_runtime`（禁 Optuna / StrategyRelease）；或 E 在更长窗/新 accept 上复测（勿松门）
5. Agent-OS WP6：影子期满检查单后再删旧 boot 仪式（owner-gated）

## 禁令

- 静默 cutover / 无证据回翻 `cutover_allowed=false`；Optuna；E 松门；StrategyRelease
- margin thaw；mass backfill；plugin bus；第二 DB；agent 自降 commit tier
- 后台 subagent 若再出现「仅 2 行 transcript、tool 无 result」：改用本会话直接做或 `shell` 子代理（见交接文档）

## 已裁决（稳定）

| 层 | 目的 | 首个正式输出 |
|---|---|---|
| Tier 0A 市场数据 | 日历、身份、名义 K、公司行动、复权 | accepted canonical partition |
| Tier 0B 分类 | 版本化树/概念/成员/crosswalk | taxonomy node + membership |
| Tier 1 股票状态 | 阶段/形态/事件，不预测 | stock state + pattern event |
| Tier 2 市场感知 | 活跃度/不平衡代理/广度/价格响应 | market context snapshot |
| Tier 3 研究/策略 | B0→B5 消融 | experiment verdict + strategy spec |
| Tier 4 决策/产品 | 只消费已发布策略 | strategy release + decision batch |

依赖只向下。Ops 观察但不拥有业务事实。多源=契约可换 adapter（目标态）；首策略包=`institution_follow`；边做边测。Tier0 未闭合前禁止寻优、生产候选、自动跑批。

架构硬决定摘要：积木=`module+data+config+contract+evidence`；landing 保留供应商响应；日历与 universe 同级硬门；名义 OHLCV=成交真相；一数据集一 writer；`manual_only`；静态 PASS≠`live_readiness`。完整条文见 `docs/MASTER_TOPLEVEL_DESIGN.md`。

手动 sync：`trigger_mode=manual` 不受 `same_day_at 18:00` 挡；自动更新与 consumer `available_at` 仍受 clock；交易日历对两者硬约束。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

交易所汇总≠沪深池。accepted 行数≠业务正确。continuity 非 READY≠代码不可提交。E measured reject ≠ StrategyRelease。函数存在/WARN/fixture 绿≠交付。BOARD≠执法输入。
