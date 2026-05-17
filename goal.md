# Goal Ledger — ChunkyMonkey 持久执行账本

> 用户终极目标: **年化 ≥30% / max_dd ≥-20% / 月胜率 ≥55% / 超额沪深 300 > 0** (100万 CNY paper trading, 5-position cap).
> 本 ledger 滚动更新, 状态实时反映工程进度. 跟 PROJECT_INDEX.md 互补 — PROJECT_INDEX 是地图, goal.md 是任务流水.

## 当前阶段: Phase 4 (Feature Engineering + Forecast Upside Framework)

### 4.A Phase 4 feature modules — 28 单测全过, 已 push (2026-05-17 上午)

| # | 模块 | features | tests | commit |
|---|---|---|---|---|
| 1 | time_of_month | 7 | 6 | d07f5ebb |
| 2 | market_cap_decile | 6 | 6 | d07f5ebb |
| 3 | industry_beta | 4 | 3 | 9b46d57b |
| 4 | capital_flow (wrap PIT 858K) | 15 | 4 | c76e4283 |
| 5 | sector_momentum (PIT industry) | 11 | 5 | d1f64ec5 |
| 6 | institution_survey | 7 | 4 | 3cd8ac21 |
| **总** | **6 modules** | **50** | **28** | — |

### 4.B Forecast Upside Framework (Codex round 19+, 用户业绩预测+Optuna joint)

| 阶段 | 内容 | 状态 |
|---|---|---|
| 4.B.1 | `forecast_upside.py` 纯函数模块 (upside = fy1_eps × target_pe / current_price - 1) | done (commit 95b30089) |
| 4.B.1.fix | PIT winsorize fix (Codex CRITICAL: 之前全样本 quantile = forward leakage) | done |
| 4.B.2 | `ingest_profit_forecast_snapshot.py` daily immutable PIT snapshot | 待 |
| 4.B.3 | shadow validation mart (5d/20d hit_rate) | 数月累积后 |
| 4.B.4 | Optuna joint search space (forecast_year/target_pe_source/blend/upside_floor) | 数月后 |

### 4.B.fixes Codex CRITICAL fixes (本 session)

| # | bug | 修 | 状态 |
|---|---|---|---|
| 1 | forecast_upside.py 全样本 winsorize = forward leakage | 改 rolling window quantile, 加 test_winsorize_is_pit_safe | done commit 89aa9c3b |
| 2 | promote_champion.py rank_ic = ann_ret * 0.1 占位污染 champion register | 改 _load_p0b_rank_ic from mart_p0b_walkforward_eval, 无 → 拒 promote | done commit 89aa9c3b |
| 3 | Phase 4 features 全没 wire 到生产 panel — 设计 build_p0a_feature_panel_v4.py | feature_join_v4.py + driver script done (code only, 等 Optuna 完跑) | done code |
| 4 | run_v3_2_full_chain.py:97 silent bug (cmd 没传 --feature-panel v2 实际走 v1) | 待修 (script 可能 deprecate 整删) | 待 |

### 4.C Cleanup (Codex 重排: P0→P2, 因 train script 仍硬编码 v1)

| 优先级 | 任务 | 状态 |
|---|---|---|
| P2 | feature_join_v1 (0 fn caller) — defer 等 train_p0b_lightgbm.py:119 / run_p1_ablation.py:82 默认迁 v3 | defer |
| P2 | feature_join_v2 (1 caller orphan chain) — defer 同上 | defer |
| P1 | paper_sim score_loader 3 → 1 主 loader + strategy param | 待 |
| P1 | paper_sim_*.yaml 12 → 3 (active base + conservative + experiment) | 待 |
| P1 | run_v3_2_full_chain.py:97 silent bug 修 (cmd 没传 --feature-panel v2) | 待 |
| P2 | 物理 DROP v3 panel leakage 残列 (inst_quality_* / sector_ret_*) | 等 Optuna 完 |

### 4.D 决策出口闭环 (codegraph audit P0 gap)

| 任务 | 状态 |
|---|---|
| `scripts/run_daily_decision_pipeline.py` 串 sync→panel→train→sim→champion→alert | 待 |

## Optuna 当前: PID 25088

- run_id: `p0b_optuna200_governance_v1_20260517T085523`
- 启动: 2026-05-17 08:55
- 进度 (10:01): trial 1, window 10/16 (2 min/window)
- 估计完成: ~22:00 (~12h total, 9h remaining)
- 模型: 92 features (governance v1 PIT clean), label fwd_cost_after_20d
- baseline RankIC: 0.0246 (governance v1 honest)
- 目标: 优化超参 (lgbm leaves/lr/feature_frac) 看能否 push RankIC > 0.03

## 长期 v3.2 状态 (η+++++++ +45.4% baseline, 含 leakage 历史)

- [FAIL] +45.4% baseline 含 stage_optimal in-sample fit leakage (Codex acf48d35 标 CRITICAL)
- [OK] governance v1 框架部署完成 (yaml + sop + audit + check + lint + DELETE 16.5M leaked rows)
- [OK] Phase 3 honest verdict: RankIC=0.0246, ann=-65.5%, P3 FAIL (干净 PIT 实测)
- ⏳ Phase 4 alpha 提升: 50 new features (in progress) → 期望 RankIC 0.025 → 0.035+
- ⏳ score_rank_diff_v1 sizer 差异化仓位 (commit 71bb2189) — 待 paper_sim ablation

## 工作纪律

- 小步快跑, 每完成子项立即 commit + push (用户 [[feedback_git_commit_frequency]])
- 中文输出, 表格 > 段落, 不报喜不报忧
- Codex review gate: 每代码 commit 前 codex:rescue --model gpt-5.5 --effort xhigh
- PIT/leakage CRITICAL 不允许折中 (用户 [[feedback_codex_critical_no_compromise]])
- Phase 4 feature 模块拉完 → 待 Optuna 完成 → wire 进 panel → retrain → paper_sim ablation
