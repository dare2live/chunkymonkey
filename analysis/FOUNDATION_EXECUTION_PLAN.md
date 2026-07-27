# 数据底座执行方案（Foundation Execution Plan）

> **生命周期**：evidence-only execution roadmap（analysis 层；**非** owner bible）
> Authority chain: `AGENTS.md` → `goal.md` → `docs/*` owners → **本文件 = 底座「下一步做什么」**
> Companion: `analysis/STRATEGY_EXECUTION_PLAN.md`（策略轨；底座 exit 前 BLOCKED）
> Cleanup ledger: `analysis/DOC_CLEANUP_20260723.md`
> Label: **ACTIVE backlog**（有序 TODO；无「主方案 vs 支线」分类）

---

## 0. 定位

| 是 | 不是 |
|---|---|
| Tier0–ops / Continuity / Cap 产品面 / update-flow / DB hygiene 的**唯一执行 backlog** | 不替代 `docs/MASTER_TOPLEVEL_DESIGN.md` / `engineering_governance.md` |
| `goal.md`「下一步」底座侧的 living 指针目标 | 不发明第三本圣经；不与 STRATEGY 抢策略排期 |
| DONE 快照 + 有序 TODO + exit criteria | 不是 session 流水账；刀证据进 git commit / ledger |

**退出（foundation exit → 才可开 STRATEGY）**：下表 TODO 中带 **exit-gate** 的项全部 `FIXED|CLOSED`（或 owner 明示 skip），且禁令未破。

**「100% usable」≠ Continuity READY / 零 WARN**：见 §6a + `analysis/foundation_residual_rootcause_20260723.md`。
Owner 纠偏（2026-07-23）：禁止为清清单而清残留；先分 class-A 复发债 / B 诚实状态 / C 历史堆 / D 假残留。

---

## 1. Supersession（旧计划/审计 → 本文件）

| 旧文件 / 外部 | 角色曾是 | 处置 |
|---|---|---|
| `~/.cursor/plans/gap_analysis_audit_3cdd0f6e.plan.md` | Cursor「整体优化方案」 | 外部；底座执行面 → 本文件 |
| `MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md` | analysis 合一 roadmap | **superseded**；执行面拆成本文件 + STRATEGY |
| `DOC_AUTHORITY_20260722.md` | analysis 索引 | **deleted**；发现入口 → `docs/README.md` + `goal.md` |
| `global_cleanup_rebuild_plan_20260723.md` | cleanup 刀板 | **folded** |
| `foundation_residual_fix_plan_20260723.md` | margin residual 刀板 | **folded** |
| `overall_plan_completion_audit_20260723.md` | 完成度审计 | **folded**（数字进 §2） |
| `rewrite_mechanism_verdict_20260723.md` | rewrite 裁决 | **folded** §4 |
| `cx_closeout_rx_honesty_20260723.md` | CX 收口 | CX → §2 DONE；RX → STRATEGY |
| `plan_reeval_*` / `forward_program_efgh_*` / treadmill closeout 等 | 旧 roadmap / 支线 | **deleted**；git history |

仍 **living subordinate**（立法/契约引用，非第二 backlog）：
`foundation_phase_reeval_20260721.md`（FND-GATE）、`data_brick_architecture_20260721.md`、
`db_layering_toplevel_design_20260721.md`、`architecture_fix_treadmill_first_principles_20260722.md`、
`serve_derive_closed_loop_law_20260723.md`、`org_holding_incremental_loop_20260723.md`、
`shareholder_update_check_design_20260723.md`、`hs_a_whitelist_includes_st_20260722.md`、
`data_frontier_detection_system_20260723.md`、`product_decision_assist_backlog_20260721.md`、
`db_storage_hygiene_20260721.md`、`db_bloat_deep_dive_20260723.md`（yaml 删表证据锚）。

---

## 2. DONE 快照（勿回滚）

| 块 | 状态 | 证据锚（commit / 门） |
|---|---|---|
| Transport S1–S6 / S7 typed wall | FIXED / near-FIXED | FND-GATE；`stk_factor_pro` sunset；**2026-07-23** orphan sunset；**2026-07-24** express/fina_mainbz lifecycle DROP；**holdernumber RESTORE** `by_ann_date`+DataAccess+dossier → **20 ssot / 8 serve_l0_declared / 3 retired**（证据 `stk_holdernumber_retire_evidence_20260724.md`） |
| FND-GATE F1–F10 / phase_closure | PASS | `check_foundation_done.py` |
| E0-HIST / F6 holders·stk·org | PASS | holders/stk overlap；org incremental |
| CX-1…CX-4 能力门 | PASS | commits under `cx*_acceptance_*` era；git |
| Cap A/B/D/E + Cap F dossier usable | FIXED | `dossier_100_usable` 证据保留；API blocking tests |
| Serve→derive 闭环 + org repair/population | FIXED | law + `serve_derive_closed_loop.yaml` |
| Margin 1a scope SSE+SZSE | FIXED | `e6b3e44c5` |
| Margin 1b v3 bounded catchup **in acquire** | FIXED path | `0f5af7e80` 一带；补跑 CLI≠正解 |
| Holders ACCEPTED+same payload_hash skip-land | FIXED path | `67cd81c27` |
| Holders notice-axis catchup（mid-period holes） | FIXED path | `542365446`；证据 `holders_ann_date_axis_20260724.md`；live drain 见 §3b |
| Shared `plan_partition_catchup` tip-leap law | FIXED path | `frontier_decision.plan_partition_catchup`；holders 迁入 + `stk_holdertrade` 接线；证据 `partition_leap_integrity_20260724.md` |
| Holdernumber RESTORE `by_ann_date` | FIXED | `9bde17735` |
| Market qfq post-CTAS in-module compact | FIXED | `8f36809bf` / `a49a99786` |
| Rewrite：删 canary + `rewrite_legacy` True 写回 | FIXED | git；见 §4 |
| 跑步机 Phases 0–3 / run_outcome / ops≠刀 | FIXED | treadmill first-principles |
| §15 knife-merge / gate pytest 分层 | FIXED | eng_gov §15；`gate_redesign_occams` |

完成度（审计折叠）：底座+纪律 ≈ **高**；策略轨见 STRATEGY（刻意低）。

---

## 3. 有序 TODO（唯一 backlog）

> 无支线标签。依赖序自上而下。每项交付标签 `FIXED|PARTIAL|BLOCKED|CLOSED`。

| # | 项 | 类型 | Exit criteria | 禁 | 状态 |
|---|---|---|---|---|---|
| **F1** | **Continuity** — dividend/hsgt typed gaps | L2/L3 | live overall PASS warn=0 via `hk_holidays`/`event_sparse`；豁免日历外**应有却缺**仍 FAIL | READY cosmetics / mute checker | **FIXED** 2026-07-23（`continuity_f1_typed_gaps_20260723.md`；Knife4+typed calendars） |
| **F2** | Margin **ops catchup** 推进 `local_max`→`eligible_end` | ops 轴② | token 下 bounded catchup 实测水位前进；Continuity 诚实 | all-due / mass / product thaw | **CLOSED** 2026-07-23：v3 `local_max=20260722` = 当时 `eligible_end`（accepted n=4 since `coverage_start=20260717`）；无 blocker |
| **F3** | Holders landing **retention/archive** + smartmoney compact | L3 | archive 非 latest ACCEPTED→parquet；landing≈1×；compact reclaim | bare DELETE landing 当去重 | **FIXED** 2026-07-23（`holders_landing_retention_f3_20260723.md`；7.17M→236k；6.7→4.3 GiB） |
| **F4** | Margin **1c** promote gate（shadow vs accepted） | L2/L3 | product-visible `promote_gate`；serve→accepted SSE+SZSE；READY as external_aggregate when criteria pass；应有却缺 UNTRUSTED；覆盖前 typed EMPTY | 无 shadow 假 TRUSTED / 把正常空 scare 成 fail-closed | **FIXED** 2026-07-23（`margin_f4_promote_gate_20260723.md`；gate=PROMOTED on accepted days；typed empty 2026-07-23 owner 纠偏） |
| **F5** | BOARD / codegraph / maps **sync** | hygiene | `build_agent_board` 重生；BOARD=投影非执法 | 手改 BOARD 当真相 | **FIXED** 2026-07-23：投影反映 §6 exit + §6a 100% 定义 |
| **F6** | S7 publication/sunset（按需）+ **org accepted pointer↔canonical** | Tier0 | **仅** owner 新 block；org pointer count 与 full canonical 一致（F6 live） | 假 COMPAT / blanket pre-accept；pointer 只记末 batch | **PARTIAL→path FIXED** 2026-07-27：accept 写 full-partition pointer；8/22 live repaired；证据 `org_holding_pointer_fix_20260727.md`；S7 orphan 仍 skip |
| **F7** | Type-B enrichment | L3 | feature_store_profiles ACCEPTED；`institution_profile_edge_v0` declared；legacy_only 仅补 canonical 缺期 | 假 FIXED / Optuna | **FIXED** 2026-07-23（E0-HIST 后 canonical enrichment 齐；`新进` null `hold_change_num` typed OK） |
| **F8** | qfq incremental/partitioned write | L3 | 默认 incremental：`f_latest` 值变 → 全历史 rewrite；值不变 → append；`--full` 保留 CTAS+compact | 用「定期 compact」代替语义；静默错历史 | **FIXED** 2026-07-23（`build_price_kline_qfq_tushare` auto/incremental/full） |
| **F9** | Residual hygiene SLA（滞后超限即红） | L2/L3 | YAML `residual_hygiene.yaml`；Type-B raw→fact + ann tip vs eligible；store 2.985 + type_b post-evaluate；FAIL→degraded+ALERT；禁 Continuity READY 化妆 | 清零诚实 WARN；平行第二仪表盘；safe_commit 绑数据红死锁 | **FIXED path** 2026-07-26（`analysis/residual_hygiene_f9_20260726.md`） |

**F1 逐项（Knife4）**：
| 信号 | 裁决 | 证据 |
|---|---|---|
| margin `warn_declared_drift` | **FIXED** typed | `coverage_start`≠表 MIN；`check_declared_vs_actual` 对 accepted_* pre-coverage retention 不再 WARN |
| moneyflow_ind_dc `warn_sparse_history` | **FIXED** reviewed | `data_start_reviewed` + 既有 coverage_note（分类扩容） |
| dividend `warn_sparse_history` / `warn_row_dip` | **FIXED** reviewed | `data_start_reviewed` + `row_dip_tolerance`；vendor grain 白名单 0 miss |
| moneyflow_hsgt 真缺口 20260708/10 | **FIXED** ops | bounded backfill 2 日；vendor-0 日进 `known_empty_days` |
| dividend/hsgt `warn_interior_gaps` | **FIXED typed** | `hk_holidays` + `event_sparse`；live PASS warn=0；非假期仍 FAIL |

**近端默认序**：F1/F3/F4/F7/F8/F9 FIXED path；无默认开放底座刀（STRATEGY 仍须 RX）。轴/频率评审见 §3b。

### 3b. 数据轴/频率评审残差（2026-07-24；非 class-A）

证据：`analysis/data_axis_frequency_review_20260724.md`。本轮 **无新错轴**；不改 update-flow。

| # | 项 | 类型 | Exit | 状态 |
|---|---|---|---|---|
| **A1** | holders fact→canonical notice 洞 drain（≈1271；含 600388/`20260613`） | ops | catchup 清空 fact-only | **CLOSED** 2026-07-24 live：fact_only=0；`20260613`∈canon+accepted |
| **A1b** | 共享 tip-leap catchup law + 跨域接线 | L2 | `plan_partition_catchup`；holders 迁入；≥1 披露域接线；证据 `partition_leap_integrity_20260724.md` | **FIXED path**（holdertrade wired；raw 锁后量化 interior） |
| **A2** | holdernumber `MAX(ann_date)` tip vs eligible | F9 gate | tip lag >fail SLA → residual_hygiene FAIL/degraded | **F9 门禁管辖**（稀疏合法；超限即红；非本刀 mass drain） |
| **A3** | Type-B fact publish 短滞后（moneyflow/limit/index/dc） | F9 gate | 同跑 catchup 后仍 raw≫fact 超 fail SLA → degraded | **F9 门禁管辖**（catchup 仍负责追平；本门防无限漂） |
| **A4** | org 中间历史季洞 | backfill knife | 仅显式刀；日常 incremental 不变 | **DEFER** |
| **A5** | cyq 消费口径（C0 历史 FAIL） | semantic | 消费前换算/弃用；非采集轴 | **DEFER** |

**Breadth 诚实门（2026-07-23；owner 纠偏同 rzrqye）**：`attest_market_pulse_scope` 消费 B-pit `MART_CUTOVER` → `adv_dec_ratio` READY as `project_universe_pit`；**窗外/未到期 → typed EMPTY**（正常空，非 scare）；**窗内应有却缺 → UNTRUSTED**；禁假 READY。Shadow 窗 `20260121`–`20260722` MATCH 120/120。

---

## 4. Rewrite / 存储卫生（折叠裁决）

| 类 | 裁决 |
|---|---|
| sync `replace_partition` / grain DELETE→INSERT | **KEEP**（幂等发布） |
| qfq DROP+CTAS **or** incremental + **模块内** post-full compact | **KEEP**（latest-adj；增量按 `f_latest` 值；防 free-block 复发） |
| landing append + ACCEPTED 同 hash skip | **KEEP** |
| derive delta-gate / rare `--rebuild` | **KEEP** |
| orphan → **删能力**（非补丁防 refill） | **KEEP 模式**（factor 已示范） |
| `rewrite_legacy` True 写回 / canary CLI / 定期 dedupe fixer | **DELETED / BANNED** |
| Continuity READY 靠删检查 | **BANNED** |

机制细节：`db_storage_hygiene_20260721.md` · `db_bloat_deep_dive_20260723.md`。

---

## 5. 硬禁令（底座侧）

- 假 Continuity READY / margin product thaw / org mass·by-date invent
- S7 假 COMPAT；第二 DB / plugin / DAG / event-bus
- 静默 cutover；把 ops 残差默认翻成代码刀（须 `{owner block ∨ named consumer ∨ 轴① gate}`）
- 开 STRATEGY / Optuna / Release（见 STRATEGY 门）

---

## 6. 交付纪律

§15：一逻辑刀 = 一次 Rule10 + 一次 `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh`；L3 先 `chunkyctl pre-knife`；CI 异步；并行仅 moth 非重叠。

**Foundation exit（给 STRATEGY 的绿灯条件）**：F1 收口（或 owner skip）+ F2 无 blocker（或诚实 BLOCKED+owner）+ F5 投影不谎报下一轨 + 上表禁令未破。

**Exit status 2026-07-23**：F1 **FIXED** · F2 **CLOSED** · F5 **FIXED** · 禁令未破 → **foundation exit MET**（STRATEGY 仍须 `goal.md` 显式 schedule RX 才开；本轨不自动开 STRATEGY）。

### 6a. 100% usable（owner 纠偏 2026-07-23）

证据：`analysis/foundation_residual_rootcause_20260723.md`。

| 要求 | 状态 |
|---|---|
| 无开放 **class-A**（日常更新会再制造同类错误） | **MET**（本轮探针未发现） |
| **class-B** rzrqye READY as external_aggregate on accepted days（应有却缺 UNTRUSTED；正常空 EMPTY） | **OK — 诚实** |
| **class-C** holders ×32 历史堆 | **FIXED** F3 reclaim |
| F7/F8 | **FIXED**（仍非 Continuity READY 条件） |
| 禁 Continuity READY 化妆 / retention/shadow 为冲清单 | **binding** |

**100% usable status**：**MET for prior class-A probe (2026-07-23)**；**2026-07-27** 发现 org accepted pointer class-A → **repaired same day**（`org_holding_pointer_fix_20260727.md`）。策略 snapshot/holdout 洞仍挡 RX（见 STRATEGY），≠ Continuity READY。
