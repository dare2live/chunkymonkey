# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-20（forward program pointer → P0+P1；dual-track residual NONE；frontier current）
> 手写：objective / 已裁决 / 禁令 / 下一步。状态投影见 `BOARD.md`（生成，勿手改）。
> 完成证据追加到 `analysis/project_state_ledger.md`。
> **跨账号交接全文**：`analysis/account_switch_handoff_20260720.md`
> **一体化前向程序（单一路径）**：`analysis/forward_program_efgh_20260720.md`（P0→P1→D1→P2/P3；WP6 旁路；H only after accept）

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
- **Dual-track 复核（2026-07-20 续作）**：`rg`+人工复查 `routers`/`services`/`scripts`/前端 API，residual **NONE**——无新旁路可删/退役；既有 resolver 边界（`resolve_tier12_production_read`、`resolve_b_pit_mart_production_read`）仍是唯一读路径。证据见 `data/lineage/legacy_retire_notes.md`「2026-07-20 re-audit」。
- **Accept frontier 复核（2026-07-20 续作）**：实测 `chunkyctl sync --domain daily|stock_st --start 20260721 --end 20260721` 均 `operation_window_blocked`（`wall_clock_preflight`，`eligible horizon=20260720`）——frontier 已 **current**，非落后；`20260721` 是交易日但尚未收盘（系统时钟仍 `2026-07-20`），无新数据可 accept。
- **F 更长窗 remeasure（2026-07-20 续作）**：**BLOCKED**（非代码可解）——accepted nominal 窗口本身就是 `20260116`→`20260720`（121d，与 F0 冻结窗口一致），往前扩窗=backfill（禁），往后扩窗=只能随自然交易日推进（无法加速）；250d full-episode 同理需数月自然日历。交给 owner：授权例外 backfill，或接受自然节奏。

启动：`scripts/chunkyctl agent-boot`；状态：`BOARD.md`。

## 下一步

**Integrated forward program（单一路径，非菜单）** =
`analysis/forward_program_efgh_20260720.md`

顺序：**P0 → P1 → D1 → P2/P3**；**WP6 旁路**（不洗绿 A→H）；**H only after accept**。  
当前程序指针：**P0 + P1**（未到 D1；不开 G/H/Release）。

- **P0** 数据币值：自然交易日收盘后 eligible 才 `sync` + Tier1/2 accept；frontier=`20260720` 已 current；dual-track residual=NONE（仅新增旁路时复扫）
- **P1** E/F 处置：在自然扩大的 accepted nominal 窗上、不改阈值、同 protocol 复测；更长窗 remeasure 当前 **BLOCKED**（禁 mass backfill；需日历推进或 owner 书面例外）
- **D1 及以后**：见程序文件；触发前不开 G、不开 H、不 StrategyRelease

**Current focus 旁路（地基模块化 — NOT SHIPPED）**：业主验收语义已澄清——acquire / process / compute / display 须**独立模块边界**；一键 sync **仅允许作编排器**（依次触发模块），**禁止** fetch 焊 accept 的融合龙。daily/ST 现状 = `sync_runner`→`capture_and_publish_*`（**NO，需求未交付**；库内 land/accept 函数缝不算 shipped）。前向：S1 acquire→landing / S2 accept-from-landing / S3 sync=caller-only / S4 本地 raw·换源进 acquire；PIT+≤40d+禁 mass backfill+禁 greenfield。证据：`analysis/data_foundation_modularity_gap_20260720.md`。

旧「owner choose」菜单项已并入上列；细节与硬退出条件以程序文件为准。

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
