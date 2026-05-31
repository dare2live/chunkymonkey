# MSAF P1b: Institution Composite 4-class — 实测 finding (2026-05-18)

## 目标

Codex agent task-mpavrn1o-w0ro9l (b3or4kdgn) deliver 4-class institution composite, 接 ensemble 看 KPI 是否提升.

## Codex deliver

1. backend/scripts/build_institution_score_daily.py — 4-class SQL batch
2. backend/tests/strategies/test_institution_batch.py — 单测
3. run_msaf_ensemble_paper_sim.py — load_institution_scores 读 mart_institution_score_daily

实测 mart_institution_score_daily: 2,250,720 rows (432 dates × ~5210 stocks).
- 4 class 全 100% coverage (LHB / CapitalFlow / Survey / Northbound)
- avg_composite 0.065 (低)
- n_classes_eligible avg 4.0

## 实测 ensemble KPI 对比 (22 monthly obs)

| variant | mean ann | **median** | CAGR | max_dd | sharpe | **hit_rate** |
|---|---:|---:|---:|---:|---:|---:|
| LM-only | +63.21% | +34.88% | +69.15% | -21.38% | 1.347 | 63.64% |
| **LM + sniper (default)** | +41.49% | **+48.40%** | +34.24% | -24.28% | 0.809 | **68.18%** |
| LM + sniper + institution | +3.95% | **-9.76%** | -4.32% | **-39.08%** | 0.091 | 36.36% |

## Finding (实测 evidence)

**加 institution composite 严重恶化 KPI**:
- median **-58.16pp** (从 +48.40% → -9.76%)
- max_dd **-14.80pp 恶化** (从 -24.28% → -39.08%)
- hit_rate **-31.82pp** (从 68.18% → 36.36%)
- NAV 0.9258 < 1 (净亏损)

## 根因分析

1. avg_composite 0.065 表示多数股票 institution score 极低 (低 base rate signal)
2. ensemble.py min-max normalize per source → 极少数 institution active stocks 占满 0-1 范围
3. regime weight 默认 (LM 30% / sniper 40% / inst 30%) 给 institution 等权
4. → ensemble final ranking 被 institution 偏置选择 sparse-active 股, lambdamart strong alpha 被 dilute

类似 commit 1a671e52 raw lhb_inst_buy_30d test (-2.71%), 4-class composite finding 一致 — institution signal **不是 strong alpha source as currently designed**.

## 决策

**Default OFF** (--with-institution flag opt-in):
- LM + sniper (commit a58d22dd) 仍是当前最佳 ensemble baseline (median +48.40% / hit 68.18%)
- 留 institution score wire 给 Phase 5 Optuna 调优:
  - regime weight 不 equal (LM 60% / sniper 30% / inst 10% 等)
  - institution score 转换 (e.g. binary triggered 不 continuous, percentile threshold)
  - per-class weight 调优 (LHB / CapitalFlow / Survey / Northbound 各占)

## Phase 5 Optuna 联合调优 spec

```yaml
ensemble_regime_weights_optuna:
  bull:
    lambdamart: 0.40 ~ 0.70  # 当前 0.30 → 倾向 LM 主导
    sniper: 0.20 ~ 0.50
    institution: 0.00 ~ 0.20  # 当前 0.30 → dampen
    cash: 0.00 ~ 0.10
  neutral:
    lambdamart: 0.40 ~ 0.70
    sniper: 0.20 ~ 0.40
    institution: 0.00 ~ 0.20
    cash: 0.00 ~ 0.10
  # ... bear/crash 类似
```

约束: sum = 1.0, institution upper cap 20% 防 dilute.

## 引用

- Codex agent task-mpavrn1o-w0ro9l (deliver mart_institution_score_daily 2.25M rows)
- ensemble runner default 改 OFF (commit pending)
- 用户原则 [[feedback-kpi-target-no-cap]]: KPI 越高越好不封顶 — 实测 institution dilute 应 default off
