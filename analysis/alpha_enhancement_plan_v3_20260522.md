# Alpha Enhancement Plan v3.1 — 2026-05-22 (final, end-of-day)

> v1 (09:55) doc-driven, drop.
> v2 (10:10) evidence-driven, drop.
> v3 (11:15) true-verdict-driven, drop.
> **v3.1 (16:00) Phase A ablation + Phase D PIT finding → final**

## 今日 alpha 验证主线

1. **True verdict BLOCK** (stability model lgbm_phase5_stability_20260521T055800Z):
   - true train-log Phase4: IS=0.114 / OOS=**0.0086** / relative_drop **92.43%** > 30% FAIL
   - paper_sim Sharpe 2.09 / ann +71.9% misleading — ML OOS signal collapse

2. **Phase A feature ablation** (本地 + GCP, ~16 min):
   - fundamental drop OOS +0.0113 (+19%) — A 股 quant short-term 弱关联 fundamentals
   - survey / lhb / executive 各 +0.002-0.003 noise
   - sector drop OOS 崩 -0.0468 (per-col 0.0117, vs alpha158 per-col 0.00012, **100x 异常高**)

3. **Phase D PIT 致命发现**:
   - sector_*_tdx_l1_rel 用 `dim_stock_tdx_industry` (NON-PIT flat current mapping) 算历史 sector aggregate
   - = retrospective industry bias leakage (跟 CLAUDE.md §4.5 反例 99.978% fallback 同模式)
   - sector signal 90%+ 是 leakage artifact, 真 industry alpha 估 ~0.002-0.008

4. **用户决策**:
   - "不用行业历史" — drop sector 6 cols
   - "Phase D2 backlog 都不做" — defer ST/概念/指数/复权 PIT
   - "保持代码文档清洁"

## v6 retrain (running)

- model_id `lgbm_phase5_stability_v6_20260522T071500Z`
- pid 1845 on VM, Plan C config (1×32 + n_est=100, n_trials=50)
- exclude 30 cols (24 Phase A noise + 6 industry-related)
- 92 features (alpha158 64 + vol_mom 6 + calendar 7 + others ~15)
- ETA ~12h with possible spot preempt cycles

## v6 verdict 后路径

| 场景 | v6 OOS RankIC | v6 真 IS-OOS gap | 决策 |
|---|---|---|---|
| **成功** | > 0.005 (PIT-clean baseline 量级) | < 30% | Phase A/D 修法有效, 走 promote path: 看 paper_sim KPI / portfolio Sharpe |
| **部分** | 0.003-0.005 | 30-60% | partial fix, 仍有未发现 leakage 源, 考虑 Phase B portfolio-objective |
| **失败** | < 0.003 | > 60% | features 不是唯一根因, 转 Phase B + 大改算法 / label engineering |

## 已 drop (今日决策不再 pursue)

| 方向 | 原因 |
|---|---|
| Stability penalty weight sweep | 已证不解 IS-OOS overfit |
| 多 horizon label (multi-task) | v3 hypothesis 但未验证, defer |
| Regime-conditional | v3 hypothesis 但未验证, defer |
| Phase D2 (ST/概念/指数/复权 PIT) | 用户 15:30 决定不做 |
| BestChoice walk-forward audit | 跨 repo work, defer |
| Phase B portfolio-objective | 等 budget 6/1 reset 后看情况 |

## 保留可继续 (v6 出 verdict 后视情况)

| 路径 | 触发条件 |
|---|---|
| paper_sim KPI compare (v6 vs baseline / v5 stability) | v6 final fit 完成 |
| BestChoice ensemble 路径 | 主项目 verdict 确定后, 用 BestChoice 互补 |
| Phase B portfolio-objective | 若 v6 marginal 且 budget reset |

## v6 cost 估算

- pid 1845 elapsed ~5 min, ~$0.376/h × 12h estimated = ~$4.5
- 当前 projected ~$14 + $4.5 = **$18.5** (~23% over $15 budget, alert-only 允许)
- 若 preempt 多, actual VM uptime < 12h, 成本相应低

## 后续可能 work (但用户已 defer)

- BestChoice walk-forward OOS audit (跨 repo)
- Multi-horizon label retrain (v3 H2)
- Portfolio-objective Optuna (v3 Phase B, 等 budget reset)
- Regime-conditional (v3 Phase C)
- ST 状态 PIT 加 fact_st_status_daily (Phase D2 deferred)

## evidence

- `analysis/feature_ablation_results_20260522.log` Phase A 14-group ablation
- `data/reports/post_retrain/lgbm_phase5_stability_20260521T055800Z/phase4_gate_true_train_log_*.json` true verdict BLOCK
- `mart_paper_sim_lambdamart_v6_kpi_compare` paper_sim KPI 3 models
- `mart_daily_formula_candidate_bestchoice_v1` Phase 2 daily feed 25,684 signals
- `mart_stock_formula_optuna_bestchoice_v1` Phase 1 candidates 1146
- `backend/scripts/build_feature_panel_duck.py:1824-1844` Phase D leakage source code
- 今日 19 commits
