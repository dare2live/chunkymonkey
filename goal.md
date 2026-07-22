# ChunkyMonkey Goal

> 状态：live controller board
> 手写：objective / 已裁决 / 禁令 / 下一步。状态投影见 `BOARD.md`（生成，勿手改）。
> 完成证据追加到 `analysis/project_state_ledger.md`。
> **跨账号交接全文**：`analysis/account_switch_handoff_20260720.md`
> **design notes（analysis 层 living roadmap；不改 north star）**：`analysis/MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md`（整体优化方案合一 + 底座关键路径/分阶段验收）· 索引 `analysis/DOC_AUTHORITY_20260722.md`
> **历史排序母体（superseded as roadmap）**：`analysis/plan_reeval_first_principles_20260720.md`
> **重评事实包（无裁决；superseded as roadmap）**：`analysis/plan_reeval_evidence_pack_20260720.md`
> **模块化缺口证据**：`analysis/data_foundation_modularity_gap_20260720.md`
> **DB 分层权威（逻辑 E0→R1 vs 物理 DuckDB；禁按加工阶段拆库）**：`analysis/db_layering_toplevel_design_20260721.md`
> **数据积木/变量分层权威（L0–L4、组合规则、模块 operability）**：`analysis/data_brick_architecture_20260721.md`
> **DB 存储卫生（free-block / archive 机制 + 2026-07-21 reclaim）**：`analysis/db_storage_hygiene_20260721.md`
> **吞吐瓶颈诊断（墙钟 vs 真相门；§15 adoption）**：`analysis/throughput_bottleneck_diagnosis_20260721.md`
> **§15 效率复测（measured validation）**：`analysis/process_efficiency_validation_20260721.md`
> **Gate 栈 Occam 重设计**：`analysis/gate_redesign_occams_20260721.md`
> **地基阶段重评（FND-GATE F1–F10 spec；roadmap 见 MASTER reeval）**：`analysis/foundation_phase_reeval_20260721.md`
> **§15-VERIFY / 地基 E2E·解阻·点击**（20260721）：`section15_verify` · `foundation_e2e_frontend_update` · `foundation_daily_update_unblock` · `foundation_daily_update_ui_click` · RCA `foundation_daily_update_degraded_rca`
> **活子权威**：积木/DB/三时钟/沪深A含ST/Cap defs/前端 L1–L3·facet/工作台增量 UX — 见 `DOC_AUTHORITY_20260722.md` §1–2；历史证据同索引 §3–4
> **跑步机控制面**：`architecture_fix_treadmill_first_principles_20260722.md`（Phases 0–3 FIXED；ops≠刀）

## 当前 objective

**轨道 = foundation solidify CLOSED**（2026-07-21；母体 = transport strangler S1–S7 + brick L0–L3 + E0 + DB 分层；**phase_closure_ready=true**）。模块化 **S1–S6 FIXED**；**S7 near-FIXED**（23 ssot = typed hard-stop 墙；B1+B2 done；禁假 COMPAT）。**E0-HIST / F6 PASS**（holders≥120 trading-day overlap）。**FND-GATE PASS**（F1–F10 全 PASS；F8 §15-VERIFY **PASS**）。**§15 behavior PASS**（连续 3 刀 commits/knife=1.0 + pre-knife）。**B5** registry/qfq **FIXED 子集**；Type-B enrichment **defer**。A→H = **后置研究地图**；**E/F remeasure paused**（可 schedule，未开）。WP0–WP4 闭合；WP6 shadow 开放。**§15 knife-merge binding 不变**。

已落地硬事实（勿回滚）：
- C + B-pit **`cutover_allowed=true`**（commit `b38e9ac5`）→ resolver `ACCEPTED_CUTOVER` / `MART_CUTOVER`
- daily+ST+form/qfq/**pulse** 前沿 **`20260721`**（E2E 模块化 + unblock 后 DC/moneyflow/limit_cpt catchup；见 `foundation_daily_update_unblock_20260721.md`）
- Phase D research_runtime **FIXED**（persist + fold + measured offline）
- Delivery-OS：eng_gov **§15** knife-merge binding（一刀=一次 Rule10+一次 safe_commit；异步 CI 禁 sync `gh watch`；L3 `chunkyctl pre-knife`；并行仅 moth 证非重叠；L1 docs skip CI）— **不**放宽 L3/Rule10/PIT/≤40d
- Tier1 accept **form enrich v1**：`stock_state_stage_pattern_v1` + exact-day `fact_stock_form_daily`；re-accept `20260717` (4989) + `20260720` (4991)；cutover yaml 未回翻
- Phase F **F0+F1 FIXED**：`main_rally_v1` DatasetSnapshot freeze + B0 setup-entry short-horizon measured → `reject` / `claimable=false`（非 full-episode；禁 Optuna / StrategyRelease）
- Phase F **F2 FIXED**：B1 = B0 + Tier1 stock-state FeatureBlock（同 B0 snapshot/folds/costs/paper，经 `resolve_tier12_production_read`/`load_stock_state_by_day`）→ 同窗口 measured **`reject`** / **`claimable=false`**（edge gates unmet；holdout vs B0 无 strict lift，`REQUIRE_HOLDOUT_LIFT_VS_B0` 生效）。F2 reject/`claimable=false` 为 protocol-complete 交付，非 stop。
- Phase F **F3 FIXED**：B2 = B0 + Tier2 market-sensing FeatureBlock（`MarketContextSnapshot` project-board breadth risk-on gate，mirrors `institution_follow_b2`；legacy `market_pulse` mart 遇 UNTRUSTED 拒绝、缺 `available_at` fail-closed；独立 ablate on B0，非叠加 B1）→ 同窗口 measured **`reject`** / **`claimable=false`**（coverage sufficient 121/121d, risk_on 53/121d；edge gates unmet + holdout lift vs B0 unmet）。**F0–F3 ladder 可 checkpoint**（三个 ablation 均诚实 reject，非叠加寻优）。
- **Dual-track 复核（2026-07-20 续作）**：`rg`+人工复查 `routers`/`services`/`scripts`/前端 API，residual **NONE**——无新旁路可删/退役；既有 resolver 边界（`resolve_tier12_production_read`、`resolve_b_pit_mart_production_read`）仍是唯一读路径。证据见 `data/lineage/legacy_retire_notes.md`「2026-07-20 re-audit」。
- **Accept frontier 复核（2026-07-20）**：当时墙钟仍 `2026-07-20`，`20260721` `operation_window_blocked`——已过时。
- **Accept frontier 复核（2026-07-21 E2E）**：收盘后模块化 `land_then_accept` 单日增量 daily/ST **`20260721` accepted**；当时 UI/`daily_update` 因缺按钮 + margin `scope_blocked` 预检 **FAIL**。
- **daily_update 解阻（2026-07-21 follow-up）**：margin 预检 **FIXED**（`on_demand`+frozen，禁 thaw）；编排器 formal daily/ST catchup **FIXED**；DC/pulse **`20260721`**；工作台「数据更新」**FIXED**（`#/workbench` → `POST /api/v3/ops/jobs/daily_update/run` + 状态/日志尾；证据 `foundation_daily_update_unblock_20260721.md` Knife 4）。
- **工作台可观测性（2026-07-21 晚）**：运行中展示 `current_activity`（阶段/进度行/日志时间/告警原因），不再只显示「更新中」；证据见 `foundation_daily_update_ui_click_20260721.md`。
- **Capability E（2026-07-21 / 22）**：**FIXED** — workbench「分步节点」+ `GET /api/v3/ops/pipeline/nodes`；可独立跑 `pipeline_acquire|clean|process|store` + `derive_qfq`；**S1/S2 参数化 UI** `POST /api/v3/ops/pipeline/land-accept/run`（daily/stock_st）；预检仍嵌链内；一键「数据更新」仍主路径。证据 `capability_e_pipeline_step_cards_20260721.md`。
- **完整地基 push（2026-07-22）**：formal **daily** 盘前 `zero_rows` → `pending_publish` soft-skip **已测**；相对完整目标仍 **PARTIAL** — 见 `foundation_full_goal_push_20260722.md`。form 读 typed hybrid residual；机构 deep-link `institution_link_status` 诚实。
- **acquire `--all-due` 解阻（2026-07-22）**：**FIXED（structural + live）** — RCA=formal catchup `raise Tier0` 串行硬门绑架 drain；重建=drain 先于 formal + formal 域内 degrade/pending 不 abort；typed `pending_publish` 保留为域契约。UI 09:52→10:37：drain 后 daily=`pending_publish`、stock_st=`20260722` accepted 209、`ths_hot` max=`20260721`（443 rows；当日 `pending_publish`@22:30）。证据 `foundation_acquire_all_due_unblock_20260722.md`。Continuity READY / 全链绿仍 open（DONE degraded）。
- **0r.1–0r.4 foundation（2026-07-21 / 22）**：**FIXED** — 沪深A serve whitelist + formal continuity/SLA + share_float bare BJ normalize；ths_hot typed `pending_publish`（非 known_empty；live `20260721` catchup=ops）。证据 `foundation_bj_dualpath_ashare_whitelist_20260721.md` + `plan_residual_reconcile_20260722.md`。
- **F 更长窗 / S7 daily expand**：accepted daily **`20190102`→`20260721`**；ST **`20220104`→`20260721`**。E/F remeasure 仍 paused。

启动：`scripts/chunkyctl agent-boot`；状态：`BOARD.md`。

## 下一步

**近端排序 authority** = `analysis/MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md`（整体优化方案合一；FND-GATE spec 仍 = `foundation_phase_reeval_20260721.md`）。积木分层 = `data_brick_architecture_20260721.md`。历史母体 = `plan_reeval_first_principles_20260720.md`（superseded as roadmap）。

**foundation-done 已闭合**（F1–F10 PASS；`phase_closure_ready=true`）。下一轨仅在 owner 显式 schedule 后开：

1. **scheduled E/F remeasure**（同 protocol；仍禁 Optuna/松门/Release）— **未开**；保持 paused

**已闭合（勿回滚）**：

- **S1–S6 FIXED** — transport modular；default sync caller-only；derive/serve 独立 CLI
- **S7 near-FIXED** — 23/46 ssot typed hard-stop wall（2 blocked + 7 serve_l0 + 14 sync_orphan）；B1+B2 done；daily **1829d** / ST **1099d**；**本阶段不再开 S7 刀** unless owner 新 publication/sunset block
- **E0 transport FIXED 子集** — S1/S2 CLI + stk/holders provider land；**org_holding provider land BLOCKED**
- **E0-HIST / F6 PASS** — local-raw chunked ≤40d empty_skip：holders **152**（`20251020`→`20260717`；**126** trading-day overlap daily）；stk **194**（`20251020`→`20260715`；**161** overlap）；org **2** unchanged
- **B5 FIXED 子集** — registry gate 绿 + qfq lineage + live derive；**Type-B enrichment defer**（registry in-scheme；enrichment 非近端）
- **FND-GATE FIXED** — `backend/scripts/check_foundation_done.py` + `backend/config/foundation_done.yaml`；doctor/moth/CI wired；typed walls（S7 23 / org BLOCKED / Type-B defer）PASS
- **§15-VERIFY / F8 PASS** — 连续 3 L3 刀 e0-hist→fnd-gate→section15-verify；commits/knife=1.0；pre-knife 全 true；证据 `analysis/section15_verify_20260721.md`

**近端 focus**：roadmap = `analysis/MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md` §7。**CX-1…CX-4 PASS**（含 `cx4_sla_quality_acceptance_20260723.md` SLA 去误报）。Foundation **0r.1–0r.4 FIXED**。跑步机控制面 **Phases 0–3 FIXED**；**默认禁止**再开「清 PARTIAL / Continuity READY」代码刀。下一步 = **用已 ship 产品面** + ops 观测；**RX / E/F remeasure BLOCKED until owner签字**（checklist `phase4_ef_schedule_gate_honesty_20260722.md`）；**Optuna = Phase N BANNED**。仍禁 Type-B enrichment / S7 假 COMPAT / 擅自 E/F / G/H/Release / org invent / 松 holdout / margin thaw。**SW L1 PIT exclusivity FIXED**；**drain 流式 + probe-first FIXED**。org `:memory:` upsert 2 例仍 optional-CI 残差。

**Deferred product（顺序已重评；roadmap 见 MASTER reeval）**：0r.5b→5B mandate CLOSED。Defs 见 backlog；历史排期证据见 `product_plan_reeval_stock_dossier_20260721.md`（superseded as roadmap）。禁 Optuna/Release/松 holdout；结论=Tier3/产品面，不融进 Tier0。

**护栏**：frontier=`20260721`（含 pulse sector/flow/strongest）；dual-track=NONE；PIT+≤40d；§15 不放宽 L3/Rule10；org BLOCKED 维持；**org/period 域 manual update = incremental-only**（见下裁决）；margin 禁 thaw；**市场感知/档案 serve = 沪深A 白名单含 ST**（serve filter `6afea30fc` + population denylist 纠偏见 `hs_a_whitelist_includes_st_20260722.md`）。

A→H 仍为后置地图；E/F remeasure **仅** owner schedule 后开。

## 禁令

- 静默 cutover / 无证据回翻 `cutover_allowed=false`；Optuna；E 松门；StrategyRelease
- margin thaw；mass backfill；plugin bus；第二 DB；agent 自降 commit tier
- **org_holding（及同类 by-period 域）在每次 manual/`daily_update` 上做全市场单期 ~830k mass re-pull / 无界翻页 refresh** — 只允许 check latest plannable vs local，**缺则拉一期，有则 skip**
- 随手重写 accepted canonical / 日历契约 / PIT-availability / `stage→validate→publish` / cutover 证据链；dual-write 迁移窗口；把「残破感」当 greenfield 重写许可证
- 后台 subagent 若再出现「仅 2 行 transcript、tool 无 result」：改用本会话直接做或 `shell` 子代理（见交接文档）
- S7 14 sync_orphan **blanket pre-accept as standby**；假 S7 COMPAT

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

**Formal daily/ST acquire（owner 2026-07-21；ST∈白名单澄清 2026-07-22）**：acquire = 全市场按 `trade_date`（`raw_evidence`），**禁止** exclude-then-fetch。**沪深A 白名单含 ST/*ST**；排除仅 B/BJ/三板/观察日无名义K。`stock_st` = accepted 日级 **membership 证据**（谁在何时是 ST），**不是** universe denylist，也不是 acquire 排除名单；同日 `zero_rows`=`pending_publish` 属发布窗，勿误读为「不要 ST」。BSE/三板 landing 可含，经 board 白名单过滤。Owner：`docs/MASTER_TOPLEVEL_DESIGN.md` §5.1 + `analysis/hs_a_whitelist_includes_st_20260722.md`。

**Gate pytest 分层（owner 2026-07-21 redesign #1 SHIPPED）**：`ci_pytest_surface.yaml` = `blocking_paths` + `nightly_paths` + optional；`run_ci_pytest.py --tier blocking|nightly|all`；L2/L3 safe_commit + CI = **`--tier blocking`**（非全量 985）；tier12 publish contract **promoted**；strategy-paused main_rally/institution_follow → **nightly**。详见 `analysis/gate_redesign_occams_20260721.md`。

**S7 sync_orphan standby（owner Q2）**：**NO** blanket pre-accept of 14 orphans（无 consumer / 无 contract / 大宗成本 / 假 readiness）。保持 ssot 墙；`legacy_raw_plane.yaml` **publication_watchlist** = 未来策略需要时的 publication 候选（非自动队列）；薄门：sync_orphan 进 DataAccess → `check_legacy_raw_plane` FAIL。**禁假 COMPAT**。

**Period-domain incremental（owner Q3 + hard lock）**：每次 `daily_update` / 显式 sync 对 org（及同类 period 域）**必须** check latest plannable vs local；**缺 → 拉一期；有 → skip + log**。**NEVER** 每次点击全市场单期 ~830k mass re-pull / unbounded page crawl refresh。中间历史洞 = log-not-fill（显式 backfill 刀另开，不进 pipeline）。实现：`org_holding_period_gap_report` + `sync_org_holding_incremental`；`sync_period(..., allow_existing_refresh=False)` fail-closed。

**Product 系统 + Agent-OS 演进裁决（owner，针对 Fable5 提案）**：后续演进 = **strangler + 聚焦**，非 greenfield 重写。仅三把杠杆：(1) 单一读 SSOT 经 resolver（禁旁路直读）；(2) 本地 L2/L3 pytest = CI test-list 唯一 SSOT；(3) god-seam strangler，按 blast radius 分步收编，不整体推倒。

手动 sync：`trigger_mode=manual` 不受 `same_day_at 18:00` 挡；自动更新与 consumer `available_at` 仍受 clock；交易日历对两者硬约束。见近端 focus（drain 流式 + probe-first FIXED）。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

交易所汇总≠沪深池。accepted 行数≠业务正确。continuity 非 READY≠代码不可提交。E measured reject ≠ StrategyRelease。函数存在/WARN/fixture 绿≠交付。BOARD≠执法输入。
