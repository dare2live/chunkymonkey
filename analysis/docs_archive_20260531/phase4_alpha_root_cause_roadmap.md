# Phase 4 Alpha 根因回溯 Roadmap

按 `analysis/plan_v3_20260514_archived.md` §72 "任一失败 → 停止包装, 回到 alpha 根因, 不调目标".

## 触发条件

governance v1 framework 完成 (Phase 1-3 26 commits) 后真实数据 verdict:
- mart_p0b_oos_predictions RankIC = 0.0246 (< 0.03 Codex Q8.6 gate)
- paper_sim ann_ret_approx = -65.5% (87 days late window)
- P3 holdout 4 hard gate 全 FAIL (`analysis/plan_v3_20260514_archived.md` §99)

→ governance v1 数据干净后真实 alpha 不达用户终极目标 (年化 ≥30%).

## 6 候选路径优先级

| ID | 候选 | 实施成本 | 预期 RankIC 改善 | 启动状态 |
|---|---|---|---|---|
| #3 | Optuna 200 trials --full hyperparam 寻参 | ~6-8h Mac | 0.0246 → 0.04+ | **running** PID 25088 |
| #2 | Feature engineering (新 alpha factors) | 设计 + 实施 ~1-2 day | 0.04 → 0.06+ | audit script ready |
| #1 | exit_params PIT rebuild (1490 → 5210 codes) | ~12h × N cutoffs | paper_sim candidates ↑ | pending Optuna done |
| #4 | label horizon ablation (5d/10d/20d) | 训练 3 model ~24h | 选 RankIC 最强 horizon | pending |
| #5 | Universe ablation (60/00/30/68 vs 流动性 top-2000 vs sector neutral) | 设计 + 跑 3 model ~24h | 优化 universe alpha 比 | pending |
| #6 | Model 替代 (LambdaMART / CatBoost / XGBoost ranker) | 集成 + 跑 ~12h × N | 不同 model 学不同 pattern | pending |

## Phase 4 实施 plan

### Stage 1: hyperparam + feature analyze (current session)

1. **Phase 4 #3 Optuna 200 trials** ← running PID 25088
   - 完成后 best params 入 `mart_p1_optuna_trials`
   - 抽 top 10 by value, train final lgbm_20260517_governance_v1_20d_optuna200
   
2. **Phase 4 #2 feature importance audit** ← script ready
   - 跑 `audit_lgbm_feature_importance.py` 看 92 features 重要度分布
   - top 10 占 > 80% → 精简 / < 30% → 加新 alpha

3. **重 paper_sim + P3 holdout** with Optuna best params
   - 若仍 FAIL → Stage 2

### Stage 2: feature engineering (Phase 4 #2 深度)

新 alpha factors 候选 (Codex Q1 之前提到 + 业界 standard):

| Feature 类 | 候选 | source 表 |
|---|---|---|
| industry beta | stock_60d_ret - β × industry_60d_ret | fact_alpha158_panel + dim_stock_tdx_industry_history |
| time-of-month | day_of_month, days_to_month_end (月初/月中/月末效应) | -- |
| market-cap decile | log_cap, cap_decile (1-10) | fact_basic_indicator_pit_daily |
| 资金面 | 北向 5d 累计净买入 / 融资余额 5d 变化 | fact_capital_flow_pit_daily + 北向数据表 |
| 调研事件 | 调研次数 (7d / 30d), 调研后 5d 回报 | fact_inst_event_period |
| sector momentum | 28 行业 30d return rank | fact_sector_momentum_daily |

### Stage 3: exit_params PIT rebuild (Phase 4 #1)

`build_stage_opt_pit.py` 全 cutoffs + 5210 ever-listed codes:
- 12h × ~12 半年 cutoffs = ~144h?? 实际多 cutoff 并行
- Codex M4 reminder: --limit-stocks 只 ETL 阶段限, optimize subprocess 全量

完成后:
- mart_per_stock_stage_strategy_optimal_pit codes: 1490 → 5210
- paper_sim ml_score_loader INNER JOIN candidates 显著增加

### Stage 4: model + label horizon ablation (Phase 4 #4, #5, #6)

- 跑 3 label horizons × 3 universes × 4 model = 36 combinations
- 用 Phase 4 #3 best params as base
- 跨 ablation 比 RankIC + paper_sim ann_ret

### Stage 5: integration + final P3 acceptance

合并最强 features + best universe + best label + best model:
- 重 train final lgbm
- 重 paper_sim 全 window 2024-07 ~ 2026-04
- P3 holdout 4 hard gate verify + ann_ret sanity cap 0.5 verify
- 若 PASS → upgrade plan, 否则 `analysis/plan_v3_20260514_archived.md` §72 "不调目标" 继续 Phase 4 探索

## 估时

| Stage | 累计 |
|---|---|
| 1 hyperparam + audit | ~12h (current) |
| 2 feature engineering | 1-3 day |
| 3 exit_params rebuild | 6-12h |
| 4 ablation 36 combinations | 2-5 day |
| 5 integration + P3 | 1-2 day |
| **Total Phase 4** | **5-10 day** Mac CPU |

## 决策 gate

每 Stage 后 invoke `audit_lgbm_feature_importance.py` + `nightly_data_audit.py` + `audit_survivorship_gate.py` 验证:
- RankIC 改善 >= +0.005 才 promote
- P3 4 hard gate (governance v1) verify
- ann_ret_sanity_cap = 0.5 防 leakage 回归

## Codex review 节奏

每 Stage 完成 invoke `codex:rescue --resume ae17609dd33a9f9e0` 评估:
- governance v1 frame 仍 enforce 通过?
- 新 alpha 路径是否引入新 leakage / survivorship?
- 是否需要新 Codex review session (round 18+)?

## 失败 fallback (Phase 4 全失败时)

- 接受真实 alpha 弱 (年化 < 30%) — `analysis/plan_v3_20260514_archived.md` §72 verdict
- 调整目标 user 决定 (e.g. 年化 10-15% net 是 honest baseline, Codex round 15 Q5 给出)
- 转 risk-control alpha (低 dd + 稳定 monthly_win) 而非纯 alpha 增强
