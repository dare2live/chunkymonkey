# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-20（Phase F F3 main_rally B2 +market sensing；F0–F3 checkpoint）
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
- Tier1 accept **form enrich v1**：`stock_state_stage_pattern_v1` + exact-day `fact_stock_form_daily`；re-accept `20260717` (4989) + `20260720` (4991)；cutover yaml 未回翻
- Phase F **F0+F1 FIXED**：`main_rally_v1` DatasetSnapshot freeze + B0 setup-entry short-horizon measured → `reject` / `claimable=false`（非 full-episode；禁 Optuna / StrategyRelease）
- Phase F **F2 FIXED**：B1 = B0 + Tier1 stock-state FeatureBlock（同 B0 snapshot/folds/costs/paper，经 `resolve_tier12_production_read`/`load_stock_state_by_day`）→ 同窗口 measured **`reject`** / **`claimable=false`**（edge gates unmet；holdout vs B0 无 strict lift，`REQUIRE_HOLDOUT_LIFT_VS_B0` 生效）。F2 reject/`claimable=false` 为 protocol-complete 交付，非 stop。
- Phase F **F3 FIXED**：B2 = B0 + Tier2 market-sensing FeatureBlock（`MarketContextSnapshot` project-board breadth risk-on gate，mirrors `institution_follow_b2`；legacy `market_pulse` mart 遇 UNTRUSTED 拒绝、缺 `available_at` fail-closed；独立 ablate on B0，非叠加 B1）→ 同窗口 measured **`reject`** / **`claimable=false`**（coverage sufficient 121/121d, risk_on 53/121d；edge gates unmet + holdout lift vs B0 unmet）。**F0–F3 ladder 可 checkpoint**（三个 ablation 均诚实 reject，非叠加寻优）。

启动：`scripts/chunkyctl agent-boot`；状态：`BOARD.md`。

## 下一步（给下一账号优先）

1. **A→H 下一业务刀**：F0–F3 main_rally ladder（B0/B1/B2）均诚实 reject/`claimable=false`，已 checkpoint；下一步为重新评估更长窗/新 accept 上复测（勿松门），或另起新 strategy_package/ablation（B3+ 需先证明必要性，勿无绿叠加寻优）
2. Accept Tier1/2 for days after `20260720` when builders/calendar allow
3. Dual-track 退役残余：补查任何仍绕过 resolver 的旁路（见 `data/lineage/legacy_retire_notes.md`）
4. Agent-OS WP6：影子期满检查单后再删旧 boot 仪式（owner-gated）

## 禁令

- 静默 cutover / 无证据回翻 `cutover_allowed=false`；Optuna；E 松门；StrategyRelease
- margin thaw；mass backfill；plugin bus；第二 DB；agent 自降 commit tier
- 随手重写 accepted canonical / 日历契约 / PIT-availability / `stage→validate→publish` / cutover 证据链；dual-write 迁移窗口；把「残破感」当 greenfield 重写许可证
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

**Product 系统 + Agent-OS 演进裁决（owner，针对 Fable5 提案）**：后续演进 = **strangler + 聚焦**，非 greenfield 重写。仅三把杠杆：(1) 单一读 SSOT 经 resolver（禁旁路直读）；(2) 本地 L2/L3 pytest = CI test-list 唯一 SSOT；(3) god-seam strangler，按 blast radius 分步收编，不整体推倒。

手动 sync：`trigger_mode=manual` 不受 `same_day_at 18:00` 挡；自动更新与 consumer `available_at` 仍受 clock；交易日历对两者硬约束。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

交易所汇总≠沪深池。accepted 行数≠业务正确。continuity 非 READY≠代码不可提交。E measured reject ≠ StrategyRelease。函数存在/WARN/fixture 绿≠交付。BOARD≠执法输入。
