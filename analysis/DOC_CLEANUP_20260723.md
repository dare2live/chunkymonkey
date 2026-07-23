# 文档清理台账 — 2026-07-23

> **生命周期**：evidence-only（**唯一** cleanup ledger；勿再开平行清理笔记）
> Mandate：收敛 analysis sprawl；废除「主方案 vs 支线」；执行面仅两份方案
> Label: **FIXED**（本刀完成 kept/deleted 登记）

---

## 1. Kept（活入口 / 活契约 / 必要证据）

| 路径 | 为何留 |
|---|---|
| `FOUNDATION_EXECUTION_PLAN.md` | 底座唯一执行 backlog |
| `STRATEGY_EXECUTION_PLAN.md` | 策略唯一执行 backlog（RX 前 BLOCKED） |
| `DOC_CLEANUP_20260723.md` | 本台账 |
| `project_state_ledger.md` | 唯一历史账本 |
| `account_switch_handoff_20260720.md` | 跨账号交接 |
| `foundation_phase_reeval_20260721.md` | FND-GATE spec（yaml 引用） |
| `data_brick_architecture_20260721.md` | L0–L4（brick_registry） |
| `db_layering_toplevel_design_20260721.md` | 物理 DuckDB 边界 |
| `architecture_fix_treadmill_first_principles_20260722.md` | 三时钟 / ops≠刀 |
| `serve_derive_closed_loop_law_20260723.md` | 闭环立法（code 引用） |
| `org_holding_incremental_loop_20260723.md` | org 增量硬锁 |
| `shareholder_update_check_design_20260723.md` | holders 更新检查 |
| `hs_a_whitelist_includes_st_20260722.md` | 沪深A含 ST |
| `data_frontier_detection_system_20260723.md` | frontier 映射 |
| `product_decision_assist_backlog_20260721.md` | Cap A–F defs |
| `dossier_100_usable_20260723.md` | Cap F 验收锚 |
| `db_storage_hygiene_20260721.md` | compact/archive 机制 |
| `db_bloat_deep_dive_20260723.md` | factor 删表 yaml 证据锚 |
| `gate_redesign_occams_20260721.md` | pytest 分层 |
| `section15_verify_20260721.md` | FND-GATE F8 artifact（yaml 硬依赖） |
| `workbench_incremental_orchestrator_ux_20260722.md` | acquire UX 契约 |
| `frontend_big_picture_minimal_20260722.md` | 前端 L1–L3 |
| `frontend_complex_viz_plan_20260722.md` | viz metaphor |

Owner contracts 未动：`docs/MASTER_TOPLEVEL_DESIGN.md` · `strategy_validation_contract.md` · `engineering_governance.md`。

---

## 2. Deleted（内容已折入两方案 / git history）

### 2.1 旧 roadmap / 索引 / 审计（执行面合并）

- `MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md`
- `DOC_AUTHORITY_20260722.md`
- `global_cleanup_rebuild_plan_20260723.md`
- `foundation_residual_fix_plan_20260723.md`
- `overall_plan_completion_audit_20260723.md`
- `plan_reeval_first_principles_20260720.md`
- `plan_reeval_evidence_pack_20260720.md`
- `product_plan_reeval_stock_dossier_20260721.md`
- `product_plan_execution_closeout_20260721.md`
- `plan_residual_reconcile_20260722.md`
- `foundation_full_goal_push_20260722.md`
- `forward_program_efgh_20260720.md`
- `why_patch_treadmill_20260722.md`
- `architecture_fix_treadmill_closeout_20260722.md`
- `cx_closeout_rx_honesty_20260723.md`
- `phase4_ef_schedule_gate_honesty_20260722.md`

### 2.2 Cleanup 刀笔记 / bloat / margin mid-flight（20260723）

- `rewrite_mechanism_verdict_20260723.md` → FOUNDATION §4
- `market_compact_knife3_20260723.md`
- `db_refill_after_delete_audit_20260723.md`
- `db_size_bloat_audit_20260723.md`
- `margin_catchup_live_20260723.md`
- `margin_v3_bounded_catchup_1b_20260723.md`
- `margin_calendar_catchup_blocker_20260723.md`
- `adversarial_acquire_process_review_A_20260723.md`
- `adversarial_acquire_process_review_B_20260723.md`
- `closed_loop_residual_closure_20260723.md`
- `inst_episode_rebuild_catchup_20260723.md`
- `inst_profile_coverage_lift_20260723.md`
- `holders_stock_coverage_alignment_20260723.md`
- `unified_frontier_detection_acceptance_20260723.md`
- `plan_residuals_frontend_enrich_p2_20260723.md`

### 2.3 CX / phase / foundation 执行证据（20260721–22；DONE 已进 FOUNDATION §2）

- `cx1_acquire_efficiency_acceptance_20260722.md`
- `cx2_state_sensors_acceptance_20260722.md`
- `cx3_capability_bricks_acceptance_20260722.md`
- `cx4_sla_quality_acceptance_20260723.md`
- `phase1_run_outcome_20260722.md`
- `phase2_orchestrator_assert_20260722.md`
- `phase3_latent_quadrant_mvp_20260722.md`
- `foundation_e2e_frontend_update_20260721.md`
- `foundation_daily_update_unblock_20260721.md`
- `foundation_daily_update_ui_click_20260721.md`
- `foundation_daily_update_degraded_rca_20260721.md`
- `foundation_bj_dualpath_ashare_whitelist_20260721.md`
- `foundation_holders_wm_ops_counters_20260721.md`
- `foundation_acquire_all_due_unblock_20260722.md`
- `foundation_ths_hot_ui_catchup_20260722.md`
- `foundation_ui_click_verify_after_drain_fix_20260722.md`
- `business_clock_and_drain_rework_20260722.md`
- `workbench_ui_truthfulness_fix_20260722.md`
- `daily_update_notification_spam_triage_20260722.md`
- `ci_failures_triage_20260722.md`
- ~~`section15_verify_20260721.md`~~ **RESTORED** — `foundation_done.yaml` F8 artifact 硬依赖
- `throughput_bottleneck_diagnosis_20260721.md`
- `process_efficiency_validation_20260721.md`
- `capability_a_moneyflow_assist_20260721.md`
- `capability_b_stock_screener_20260721.md`
- `capability_d_intersection_strongest_20260721.md`
- `capability_e_pipeline_step_cards_20260721.md`
- `decision_3a_moneyflow_assist_20260721.md`
- `decision_4d_intersection_strongest_20260721.md`
- `decision_5b_stock_screener_20260721.md`
- `holders_stock_dossier_lineage_audit_20260721.md`
- `data_foundation_modularity_gap_20260720.md`

外部 Cursor plan `gap_analysis_audit_3cdd0f6e`：**不在 repo**；已在两方案 supersession 表标注。

---

## 3. 指针更新

| 文件 | 变更 |
|---|---|
| `goal.md` | 下一步 → 两方案；BOARD=投影-only |
| `docs/README.md` | 发现入口加两方案 + 台账 |
| `PROJECT_INDEX.md` | 导航指向两方案；删悬空证据链 |
| yaml/py Authority 注释 | CX-3 指向 FOUNDATION §2（原 MASTER） |

---

## 4. 计数

| 指标 | 约数 |
|---|---|
| 清理前 `analysis/*.md` | 95 |
| 本刀删除 | **62**（cleanup-era / 旧 roadmap / 已折叠刀证；`section15_verify` 因 F8 硬依赖保留） |
| 清理后 | **36**（两方案+台账+ledger+活契约 + F8 证据 + 少量 202606 历史） |
