# 项目整合 Master Plan — 2026-05-23

> 用户指令: 主项目整合两子项目, **两子项目各保留独立 UI 入口 display 使用**.
> 主项目**吸收合并副本并优化**. 即:
> - **Track A (原版保留)**: BC 当前 chunkymonkey/bestchoice/ + Perception sibling repo → **冻结** + UI display 入口
> - **Track B (主项目副本)**: backend/services/bc_absorbed/ + perception_absorbed/ → **优化迭代**

---

## A. 当前状态总览 (78+ commits today)

### A.1 Done this session

| Layer | Item | Status |
|---|---|---|
| Model | v7 EXECUTED + registry candidate_forward_monitor | ✓ |
| Model | v8 (PIT historical) + v9b (stronger reg) tested | ✓ verdict mixed |
| Panel | v5c clean (4974 stocks PIT) + Pattern 10 fixed | ✓ |
| Audit | universe.get_active_universe() + 9 leakage checks + Pattern catalog | ✓ |
| Audit | 7/7 strategies CONTAMINATED report + 数字红线 check | ✓ |
| Enforcement (hooks) | PreToolUse + PostToolUse codegraph + complexity | ✓ |
| Enforcement (hooks) | R3 codex_frequent disabled | ✓ |
| Enforcement (hooks) | UserPromptSubmit pending-work reminder | ✓ |
| Enforcement (scripts) | check_universe_filter / check_kpi_redlines / check_panel_lineage | ✓ |
| Enforcement (scripts) | monitor_v7_forward / v7_weekly_aggregate | ✓ |
| Enforcement (wrapper) | safe_retrain.sh + SKIP_LEAKAGE_AUDIT evidence required (L12) | ✓ |
| Operational | v7 cron + daily_update Step 5d integration | ✓ |
| Decision | v7_forward_decision_framework.md (week-by-week) | ✓ |
| Doc | project_audit + synthesis + this plan | ✓ |

### A.2 Pending — model/data

| Item | Status | Cost |
|---|---|---|
| v9b paper_sim + Phase 4 gate verdict | running on VM ~5 min | $0.30 |
| BC universe clean (Phase A — wire get_active_universe to 10+ BC code locations) | TODO | 2-3h local |
| v8 booster save artifact for daily inference | TODO | 1h code change |
| Linear/factor model alternative (close Phase 4 IS-OOS gap) | TODO | days |
| Panel v3 base rebuild with universe filter (foundation-level fix) | TODO | 1-2h local |

### A.3 Pending — enforcement (L7/L9/L10/L14, 4 items remain)

| Lesson | Topic | ETA | Impact |
|---|---|---|---|
| L7 | Phase 4 `--require-true-train-log` default ON (not opt-in) | 15 min | promotes only with true train_log evidence |
| L9 | Retrain save LightGBM booster `.lgb.txt` | 30 min | enables daily inference forward |
| L10 | Registry promote validator (Phase 4 + train_log + forward evidence) | 1h | prevents stale promotions |
| L14 | Daily paper_sim vs forward reconcile | 30 min | auto-flag if divergence > 30% |

---

## B. 项目整合 Master Plan

### B.1 Track A — 子项目原版保留 + Main 展示入口 (Phase 1, ~1 周)

```
chunkymonkey/  (main project)
├── bestchoice/                            ← TRACK A 原版 (current state, frozen)
│   ├── compute.py                          - 5 formulas + Optuna
│   ├── scripts/                            - 现 BC scripts
│   └── (no further dev)
├── backend/
│   ├── routers/
│   │   ├── v3_bestchoice.py                ← existing entry (4 endpoints)
│   │   └── v3_perception_legacy.py         ← NEW entry, read-only display sibling Perception mart
└── design/
    ├── v3-tab-bestchoice.jsx               ← existing UI tab
    └── v3-tab-perception-legacy.jsx        ← NEW UI tab for Perception read-only display

/Users/dp/Documents/M/stock/perception/    ← TRACK A 原版 (sibling, frozen)
├── (full 9 modules P1-P7)
├── (own UI / router / mart)
└── (no further dev, just data updates if any)
```

**Track A 目标**: Display only. Data 持续 update OK (data engineering), but **no logic changes / no integration**.

### B.2 Track B — Main 吸收副本 + 优化迭代 (Phase 2-3, ~1-2 月)

```
chunkymonkey/
├── backend/
│   ├── services/
│   │   ├── bc_absorbed/                    ← TRACK B 副本 + 优化
│   │   │   ├── core.py                      - copy + universe.get_active_universe() wired
│   │   │   ├── bank/                        - formula bank 扩展 50+ (新 categories)
│   │   │   ├── optuna_search.py             - walk-forward expanding_monthly governance
│   │   │   └── README.md                    - 标 "absorbed copy, optimization track"
│   │   ├── perception_absorbed/             ← TRACK B 副本
│   │   │   ├── p3_theme_boundary.py         - copy + PIT-strict joins
│   │   │   ├── p5_leader_follower.py        - copy + PIT historical theme membership
│   │   │   ├── p5_chain_diffusion.py        - copy + concept network expansion
│   │   │   ├── p6_style_rotation.py         - copy + cleaner mart with built_at
│   │   │   ├── p7_stock_context.py          - copy + integrate to unified panel
│   │   │   └── README.md
│   │   ├── ml_ranker/                       ← V4/v7 模型 service 化 (existing)
│   │   ├── universe.py                       ← ✓ single source done
│   │   └── ensemble/                         ← NEW cross-source combiner
│   │       ├── feature_builder.py
│   │       ├── combiner.py
│   │       └── risk_control.py
│   ├── scripts/
│   │   ├── build_panel_unified.py            - combine ml + bc_absorbed + perception_absorbed
│   │   ├── retrain_unified.py
│   │   └── run_daily_picks_unified.py
```

### B.3 Integration phased execution

#### Phase 1 — 子项目 freeze + 展示入口 (1 周)

| Task | Effort | Deliverable |
|---|---|---|
| 1.1 Lock chunkymonkey/bestchoice/ — no further dev (README 加 FROZEN tag) | 5 min | README warning |
| 1.2 BC UI tab read-only display existing mart (已 done) | done | v3-tab-bestchoice.jsx |
| 1.3 Build Perception legacy display entry — attach sibling DB OR copy mart to main DB | 2-3h | v3_perception_legacy.py + UI tab |
| 1.4 Confirm Track A no-op except data refresh | doc only | INTEGRATION_PLAN.md updated |

#### Phase 2 — BC absorbed copy + optimization (1-2 周)

| Task | Effort | Deliverable |
|---|---|---|
| 2.1 cp -r chunkymonkey/bestchoice/ → backend/services/bc_absorbed/ | 10 min | initial copy |
| 2.2 Replace dim_active_a_stock with universe.get_active_universe() (10+ locations) | 1h | clean universe in absorbed copy |
| 2.3 Add formula bank categories (technical/pattern/volume/multi_tf/event/sector/etc) | 1 周 | 50+ formulas vs original 5 |
| 2.4 Walk-forward expanding_monthly governance | 2h | governance.enforce_pre_insert wired |
| 2.5 Stage filter + regime gate (per BC optimization plan earlier) | 半 天 | improved BC v2 candidates |
| 2.6 Phase 4 gate on BC absorbed | 1h GCP | true train_log verdict |

#### Phase 3 — Perception absorbed copy + optimization (1-2 周)

| Task | Effort | Deliverable |
|---|---|---|
| 3.1 cp -r perception/src/ → backend/services/perception_absorbed/ | 10 min | initial copy |
| 3.2 PIT-strict feature joins (add built_at columns to perception marts) | 1 day | PIT integrity |
| 3.3 P5 LeaderFollower historical theme membership extension | 3 days | longer history |
| 3.4 P3 ChainDiffusion concept network expansion | 3 days | richer alpha |
| 3.5 Verify Perception marts joinable to unified panel | 1 day | feature merge OK |

#### Phase 4 — Unified panel + ensemble model (2-3 周)

| Task | Effort | Deliverable |
|---|---|---|
| 4.1 Build mart_p0a_feature_label_panel_unified_v1 (ml + bc_absorbed + perception_absorbed) | 1 week | unified panel |
| 4.2 Train unified LightGBM ranker on panel_unified | 1-2 day GCP | unified model |
| 4.3 paper_sim_v6 unified strategy | half day | KPI verdict |
| 4.4 Phase 4 gate with --require-true-train-log | half day | promotion criteria |
| 4.5 Registry update — single champion tracking | 1 day | unified registry |

#### Phase 5 — Linear/factor model alternative (2-3 周, optional if unified ensemble fails Phase 4)

| Task | Effort | Deliverable |
|---|---|---|
| 5.1 Design factor model architecture | 1 week | spec doc |
| 5.2 Implement factor model on unified panel | 1-2 week | model code |
| 5.3 Phase 4 gate — expected IS-OOS drop < 30% (linear natural) | half day | verdict (PASS likely) |

#### Phase 6 — Daily pipeline + production rollout (1 周)

| Task | Effort | Deliverable |
|---|---|---|
| 6.1 daily_update_unified.sh integration | 2 days | single pipeline |
| 6.2 forward monitor universal | 1 day | cross-strategy |
| 6.3 capital scaling rollout (5% → 10% → 20% based on forward weeks) | 1 day | scaling rule |
| 6.4 Sunset Track A development (data refresh continues) | 1 day | freeze enforcement |

---

## C. Total effort + GCP

| Phase | ETA | GCP | Risk |
|---|---|---|---|
| 1 | 1 周 | $0 | low |
| 2 (BC absorbed) | 2 周 | $5 | med (formula bank dev) |
| 3 (Perception absorbed) | 2 周 | $0 | med (cross-repo sync) |
| 4 (Unified ensemble) | 3 周 | $5-10 | high (combinatorial uncertainty) |
| 5 (Linear/factor) | 3 周 | $5 | med (model class change) |
| 6 (Daily pipeline) | 1 周 | $0 | low |
| **Total** | **~3 月** | **~$25** | overall medium |

## D. Tonight 完 vs 明天 vs sessions 后

### D.1 Tonight (within next 30 min)
- v9b paper_sim verdict + Phase 4 gate (running on VM ~5 min remaining)
- Commit this plan doc
- Stop VM after v9b verdict

### D.2 Next session (1-2 hours)
- L7/L9/L10/L14 enforcement (remaining 4 hooks/scripts)
- Phase 1.1 — lock BC + Perception sibling FROZEN tags + INTEGRATION_PLAN main doc
- Phase 1.3 — Perception legacy UI entry (read-only display sibling Perception mart)

### D.3 Week 1
- Phase 2.1-2.2 — BC absorbed copy + universe.get_active_universe wire
- Initial paper_sim verdict on absorbed BC

### D.4 Week 2-4
- Phase 2.3-2.6 — formula bank expansion + walk-forward + Phase 4 gate
- Phase 3.1-3.5 — Perception absorbed migration + PIT-strict

### D.5 Month 2
- Phase 4 — unified panel + ensemble model
- Phase 5 — linear/factor alternative if needed

### D.6 Month 3
- Phase 6 — daily pipeline + production rollout

---

## E. Critical decisions waiting

| Decision | Default | User input needed |
|---|---|---|
| Phase 4 IS-OOS threshold model-class-aware? | Keep 0.30 strict (no game) | accept default OR relax 0.50 for tree |
| v7 forward deploy 5% capital — paper account or real? | Paper account | broker selection |
| Track A freeze: data refresh OK 但 no logic changes? | Yes (freeze logic) | confirm |
| Linear/factor model — defer until ensemble Phase 4 fail OR build in parallel? | Defer | parallel preferred? |
| Total budget commitment — $25 GCP over 3 months? | Yes | confirm |

---

## F. Why this整合 is correct

1. **Track A 保留 prevents disruption** — UI users continue access without break
2. **Track B 副本 enables aggressive optimization** — main project iterates without breaking originals
3. **Phased rollout reduces risk** — each Phase deliverable independent
4. **Unified panel + Phase 4 gate** = single promotion criterion across V4/v7/BC/Perception
5. **Enforcement layer (15+ hooks/scripts ship today)** = doc-only mistakes prevented going forward

## G. Anti-patterns explicitly avoided

Per session lessons:
- [NO] Endless retrain (v7/v8/v9/v9b...) — stopped after v9b, accepted v7 + forward
- [NO] Threshold gaming (IS-OOS 30→70) — caught + reverted
- [NO] Per-trade vs portfolio Sharpe illusion — verified portfolio metrics only
- [NO] Doc-only enforcement (Codex pause) — converted to hook
- [NO] Context-switch on new user message — added UserPromptSubmit reminder hook
