# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-21（**S1–S5 FIXED**；**S6 PARTIAL**；accepted daily/ST **`20220104`→`20260720`**（1099d；ST raw floor））
> 手写：objective / 已裁决 / 禁令 / 下一步。状态投影见 `BOARD.md`（生成，勿手改）。
> 完成证据追加到 `analysis/project_state_ledger.md`。
> **跨账号交接全文**：`analysis/account_switch_handoff_20260720.md`
> **现行排序权威（第一原理重评 → S1–S3 → 再策略）**：`analysis/plan_reeval_first_principles_20260720.md`
> **重评事实包（无裁决）**：`analysis/plan_reeval_evidence_pack_20260720.md`
> **模块化缺口证据**：`analysis/data_foundation_modularity_gap_20260720.md`
> **旧 A→H 研究轨附录**（非近端主线）：`analysis/forward_program_efgh_20260720.md`

## 当前 objective

**轨道 = transport strangler S1–S6**（owner 2026-07-20 第一原理重评）。模块化诉求（acquire≠accept≠derive≠serve；sync=caller-only；acquire 可换源）**S1–S5 FIXED** + **S6 PARTIAL**——见重评文 §2–§3 / modularity gap §8。A→H 骨架保留为**后置研究地图**；近端 residual = S6 drill raw 日落 + S7 legacy raw + optional daily-only 再向 2019 扩；不是 E/F remeasure。Agent-OS WP0–WP4 已闭合；WP6 仪式影子期仍开放。

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
- **F 更长窗**：accepted daily/ST chunked local-raw 扩至 **`20220104`→`20260720`（1099d）**（ST local-raw floor）；E/F **同 protocol remeasure** 窗长已 unblock（仍禁 Optuna/松门/Release；本轨不跑策略实验）。

启动：`scripts/chunkyctl agent-boot`；状态：`BOARD.md`。

## 下一步

**现行单一路径（排序权威）** =
`analysis/plan_reeval_first_principles_20260720.md`

顺序：**S1 → S2 → S3**（land-only / accept-from-landing / sync caller-only）→ **S4–S6 按需** → **R0 E0** → **R1 E/F 复测** → G/H；**H only after accept**。  
旧 `forward_program_efgh` P1 remeasure **superseded**；P0 自然 sync 护栏仍并行。

- **S1–S3 transport FIXED**：default `chunkyctl sync` daily/stock_st/trade_cal = caller-only S1→S2；`capture_and_publish_*` **非** sync 生产 fan-in；CLI `--land-only` / `--accept-from-landing` / `--land-then-accept` + `--from-local-raw`
- **S4 acquire FIXED（daily/ST land boundary）**：`security_day_acquire.resolve_security_day_acquire` — modes `provider_tushare` | `local_legacy_raw_materialize`；land/default sync 经 acquire；accept 零 acquire（禁重焊）；TDD `test_security_day_acquire_s4.py`；**不**复活多源 fallback registry
- **Accepted window（local-raw chunked）**：daily+ST **`20220104`→`20260720`（1099d）** — ST raw floor；holdout `20250601` in-window；optional daily-only further back to raw min `20190102`（ST 不可对称）
- **S5 derive FIXED**：`chunkyctl derive qfq|form --from-accepted` + `derive_runtime`（canonical-only nominal；零 acquire/fused publish；不进 accept 事务）；default qfq/form 仍可 canonical∪legacy fill
- **S6 serve PARTIAL**：SERVE 门 **D5** `door_router_no_ad_hoc_raw` 扫 `backend/routers/`；新 router 禁内联 raw；`market_pulse` drill/members 经 `# serve-exempt:` 跟踪 residual（form/sentiment 已 production_read）
- **近端 focus**：S6 drill→DataAccess 增量迁移；legacy `raw_tushare_*` = S7；**研究轨** E0 → E/F 同 protocol remeasure（窗已扩；本 session 不跑）；不开 G/H/Release 抢跑
- **护栏**：frontier=`20260720`；dual-track=NONE；PIT+≤40d+禁 mass backfill/第二 DB/plugin bus

A→H 降为地图；细节以重评文 §4、§9 为准。

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
