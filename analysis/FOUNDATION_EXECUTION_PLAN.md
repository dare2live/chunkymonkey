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
| Transport S1–S6 / S7 typed wall | FIXED / near-FIXED | FND-GATE；`stk_factor_pro` sunset `a75288129` |
| FND-GATE F1–F10 / phase_closure | PASS | `check_foundation_done.py` |
| E0-HIST / F6 holders·stk·org | PASS | holders/stk overlap；org incremental |
| CX-1…CX-4 能力门 | PASS | commits under `cx*_acceptance_*` era；git |
| Cap A/B/D/E + Cap F dossier usable | FIXED | `dossier_100_usable` 证据保留；API blocking tests |
| Serve→derive 闭环 + org repair/population | FIXED | law + `serve_derive_closed_loop.yaml` |
| Margin 1a scope SSE+SZSE | FIXED | `e6b3e44c5` |
| Margin 1b v3 bounded catchup **in acquire** | FIXED path | `0f5af7e80` 一带；补跑 CLI≠正解 |
| Holders ACCEPTED+same payload_hash skip-land | FIXED path | `67cd81c27` |
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
| **F1** | **Continuity** — dividend/hsgt typed gaps | L2/L3 | live overall PASS warn=0 via `hk_holidays`/`event_sparse`；非假期空洞仍 FAIL | READY cosmetics / mute checker | **FIXED** 2026-07-23（`continuity_f1_typed_gaps_20260723.md`；Knife4+typed calendars） |
| **F2** | Margin **ops catchup** 推进 `local_max`→`eligible_end` | ops 轴② | token 下 bounded catchup 实测水位前进；Continuity 诚实 | all-due / mass / product thaw | **CLOSED** 2026-07-23：v3 `local_max=20260722` = 当时 `eligible_end`（accepted n=4 since `coverage_start=20260717`）；无 blocker |
| **F3** | Holders landing **retention/archive** + smartmoney compact | L3 | archive 非 latest ACCEPTED→parquet；landing≈1×；compact reclaim | bare DELETE landing 当去重 | **FIXED** 2026-07-23（`holders_landing_retention_f3_20260723.md`；7.17M→236k；6.7→4.3 GiB） |
| **F4** | Margin **1c** promote gate（shadow vs accepted） | L2/L3 | product-visible `promote_gate`；serve→accepted SSE+SZSE；READY as external_aggregate when criteria pass | 无 shadow 假 TRUSTED / 永久 UNTRUSTED 当终点 | **FIXED** 2026-07-23（`margin_f4_promote_gate_20260723.md`；gate=PROMOTED on accepted days；缺 accepted 仍 UNTRUSTED） |
| **F5** | BOARD / codegraph / maps **sync** | hygiene | `build_agent_board` 重生；BOARD=投影非执法 | 手改 BOARD 当真相 | **FIXED** 2026-07-23：投影反映 §6 exit + §6a 100% 定义 |
| **F6** | S7 publication/sunset（按需） | Tier0 | **仅** owner 新 block | 假 COMPAT / blanket pre-accept | 无 owner block → skip |
| **F7** | Type-B enrichment | DEFER | registry in-scheme 已够近端 | 当近端刀 | **DEFER / out of 100% bar（class-D 假残留若被算进）** |
| **F8** | qfq incremental/partitioned write | product later | 另开产品刀；今日 full CTAS+compact 已 ops-safe | 用「定期 compact」代替语义 | **later / out of 100% bar** |

**F1 逐项（Knife4）**：
| 信号 | 裁决 | 证据 |
|---|---|---|
| margin `warn_declared_drift` | **FIXED** typed | `coverage_start`≠表 MIN；`check_declared_vs_actual` 对 accepted_* pre-coverage retention 不再 WARN |
| moneyflow_ind_dc `warn_sparse_history` | **FIXED** reviewed | `data_start_reviewed` + 既有 coverage_note（分类扩容） |
| dividend `warn_sparse_history` / `warn_row_dip` | **FIXED** reviewed | `data_start_reviewed` + `row_dip_tolerance`；vendor grain 白名单 0 miss |
| moneyflow_hsgt 真缺口 20260708/10 | **FIXED** ops | bounded backfill 2 日；vendor-0 日进 `known_empty_days` |
| dividend/hsgt `warn_interior_gaps` | **FIXED typed** | `hk_holidays` + `event_sparse`；live PASS warn=0；非假期仍 FAIL |

**近端默认序**：F1/F3/F4 FIXED；F7/F8 非默认。

---

## 4. Rewrite / 存储卫生（折叠裁决）

| 类 | 裁决 |
|---|---|
| sync `replace_partition` / grain DELETE→INSERT | **KEEP**（幂等发布） |
| qfq DROP+CTAS + **模块内** post-CTAS compact | **KEEP**（latest-adj + 防 free-block 复发） |
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
| **class-B** rzrqye READY as external_aggregate on accepted days（缺日仍 UNTRUSTED） | **OK — 诚实** |
| **class-C** holders ×32 历史堆 | **FIXED** F3 reclaim |
| F7/F8 | **out of bar** |
| 禁 Continuity READY 化妆 / retention/shadow 为冲清单 | **binding** |

**100% usable status**：**MET**（≠ Continuity READY；≠ 零 WARN）。
