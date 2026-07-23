# Analysis 文档权威索引（2026-07-22）

> Status: evidence-only. Index of what to read vs historical; not a live control-plane owner.
> 目的：`analysis/` 已累积 60+ 篇 20260617–0722 笔记（sprawl）。本索引把它们分成
> **roadmap pointer（少数）· reference · superseded→evidence · 历史 evidence**，减少「活权威」数量。
> **不物删历史**：superseded 文档保留原文 + 顶部加一行指针；历史比较查 `project_state_ledger.md` 或 git。
> Roadmap 合一：`MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md`（analysis 层；不替代 docs/MASTER）。

---

## 0. 权威顺序（先读这些）

| 层 | 文件 | 职责 |
|---:|---|---|
| 1 | `../AGENTS.md` | 操作边界 / 技能调度 / 交付纪律 |
| 2 | `../goal.md` | 当前 objective / 优先级 / blocker / 下一步（north star，勿改） |
| 3 | `../docs/MASTER_TOPLEVEL_DESIGN.md` | 业务 Tier0–4 + transport **立法** |
| 4 | `../docs/strategy_validation_contract.md` | 研究 / PIT / 消融 / 发布 / 纸面 |
| 5 | `../docs/engineering_governance.md` | 启动 / 工具 / 测试 / 并行 / 删除 / 提交 |
| 6 | **`MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md`** | **analysis 层 living roadmap 合一权威**（整体优化方案 + 顶层重评 + 底座关键路径/分阶段验收） |

---

## 1. Living sub-authorities（subordinate to #6；仍需读）

| 文件 | 唯一职责 |
|---|---|
| `data_brick_architecture_20260721.md` | L0–L4 变量分层 + 积木组合 + depth cap |
| `db_layering_toplevel_design_20260721.md` | 物理 DuckDB 边界（逻辑 E0→R1） |
| `architecture_fix_treadmill_first_principles_20260722.md` | 三时钟控制面 + `run_outcome` + ops≠刀 |
| `foundation_phase_reeval_20260721.md` | FND-GATE F1–F10 spec（`foundation_done.yaml` 引用）；roadmap 部分由 #6 supersede |
| `hs_a_whitelist_includes_st_20260722.md` | 沪深A 白名单含 ST universe 护栏 |
| `workbench_incremental_orchestrator_ux_20260722.md` | acquire UX P0–P3；**P0.1 CX-4 / P1 CX-1 / P2 progress UX / P3 CX-2 PASS** |
| `org_holding_incremental_loop_20260723.md` | org = **incremental-check-every-run**；mass/by-date invent banned；非 forever BLOCKED |
| `shareholder_update_check_design_20260723.md` | holders/org 更新检查审计；**禁** daily 全市场逐公司扫公告；holders notice 稀疏 = 已 ship；org 期内晚披露 = repair 刀门前不进 daily |
| `data_frontier_detection_system_20260723.md` | 全项目 frontier 检测映射；`e040f4889` holders equal-wm 稀疏=系统路径；**无**统一人口检测框架；PARTIAL gaps (ann equal-day / org 期内 / typed policy 窄) |
| `serve_derive_closed_loop_law_20260723.md` | **Serve→derive 闭环立法**：existence≠population≠freshness；`integrity_observe`；机构档案挂 process；机读清单 `serve_derive_closed_loop.yaml` |
| `closed_loop_residual_closure_20260723.md` | 闭环残差收口证据：org local-raw repair + F6 人口地板 + as_of seed（live 5524 stocks） |
| `product_decision_assist_backlog_20260721.md` | Cap A–F 能力定义（近端 CLOSED；defs 有效） |

## 2. Living guardrails / 前端设计（读 as needed）

| 文件 | 角色 |
|---|---|
| `frontend_big_picture_minimal_20260722.md` | 前端 L1/L2/L3 披露 + facet registry/跳转图 |
| `frontend_complex_viz_plan_20260722.md` | viz metaphor（象限 + 地形 2.5D + Cap D 桑基/parcoords Enrich FIXED） |
| `../project_state_ledger` → `project_state_ledger.md` | 唯一历史账本（关键词查询，非启动全文） |

---

## 3. Superseded → evidence（roadmap 权威已并入 #6；保留为 point-in-time 证据）

> 这些曾各自像「计划/重评/closeout」，易被误当 bible。roadmap 权威现统一为 #6。原文保留供审计。

| 文件 | 被谁 supersede（作为 roadmap） | 仍有用的残值 |
|---|---|---|
| `plan_reeval_first_principles_20260720.md` | #6 §7/§9 | S1–S7 排序母体（历史） |
| `plan_reeval_evidence_pack_20260720.md` | #6 §8 | 无裁决事实包（历史） |
| `product_plan_reeval_stock_dossier_20260721.md` | #6 §7/§9 | 产品排期 closeout（历史） |
| `product_plan_execution_closeout_20260721.md` | #6 §9 | 0r.5b→5B 执行证据 |
| `plan_residual_reconcile_20260722.md` | #6 §9 | 四项 residual closeout 证据 |
| `foundation_full_goal_push_20260722.md` | #6 §8/§9 | 完整目标 push PARTIAL 证据 |
| `why_patch_treadmill_20260722.md` | #6 §4/§10（judgment 已折入） | 跑步机诊断（judgment 原文） |
| `architecture_fix_treadmill_closeout_20260722.md` | #6 §4/§7 | Phases 0–3 closeout 证据 |
| `forward_program_efgh_20260720.md` | #6 §7 RX | 旧 A→H 研究轨附录 |

---

## 4. Point-in-time evidence（历史；查 ledger/git，不作 living 权威）

**Foundation / ops 执行证据（20260721–22）**：
`foundation_e2e_frontend_update_20260721` · `foundation_daily_update_unblock_20260721` ·
`foundation_daily_update_ui_click_20260721` · `foundation_daily_update_degraded_rca_20260721` ·
`foundation_bj_dualpath_ashare_whitelist_20260721` · `foundation_holders_wm_ops_counters_20260721` ·
`foundation_acquire_all_due_unblock_20260722` · `foundation_ths_hot_ui_catchup_20260722` ·
`foundation_ui_click_verify_after_drain_fix_20260722` · `business_clock_and_drain_rework_20260722` ·
`workbench_ui_truthfulness_fix_20260722` · `daily_update_notification_spam_triage_20260722` ·
`ci_failures_triage_20260722`

**Phase / gate 证据（20260721–22）**：
`phase1_run_outcome_20260722` · `phase2_orchestrator_assert_20260722` ·
`phase3_latent_quadrant_mvp_20260722` · `phase4_ef_schedule_gate_honesty_20260722` ·
`section15_verify_20260721` · `gate_redesign_occams_20260721` ·
`throughput_bottleneck_diagnosis_20260721` · `process_efficiency_validation_20260721` ·
`db_storage_hygiene_20260721`

**Capability / decision 证据（20260721）**：
`capability_a_moneyflow_assist` · `capability_b_stock_screener` · `capability_d_intersection_strongest` ·
`capability_e_pipeline_step_cards` · `decision_3a_moneyflow_assist` · `decision_4d_intersection_strongest` ·
`decision_5b_stock_screener` · `holders_stock_dossier_lineage_audit_20260721`

**较早期研究/审计（20260617–0720）**：
`account_switch_handoff_20260720` · `data_foundation_modularity_gap_20260720` ·
`comprehensive_data_module_audit_20260706` · `d1_gt_archaeology_20260702` ·
`data_foundation_root_causes_20260703` · `data_sources_registry_retirement_20260707` ·
`edge_builder_pit_audit_20260708` · `gap_root_cause_20260708` · `kline_completeness_crywolf_fix_20260624` ·
`market_pulse_design_20260702` · `miaoxiang_aif10_source_decision_20260624` · `r4_completion_20260704` ·
`rally_buyability_gonogo_20260620` · `tushare_alpha_potential_research_20260617` ·
`非tushare源_双轨_holders_20260623`

---

## 5. 规则

1. 新 roadmap 决策进 **#6**，不新建平行 plan 文档。
2. 引用「计划/排序」时引 #6；引「历史证据」时引 §3/§4 具体文件。
3. superseded 文档只加顶部一行指针，不物删、不改原判断（审计需要）。
4. `goal.md` 指针块可加 design-notes 链接，但 north star 措辞不改。
