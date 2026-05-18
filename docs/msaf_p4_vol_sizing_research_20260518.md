# MSAF P4: Vol-aware Sizing Research (2026-05-18)

## 目标

降 max_dd 从 -21.38% (current) 到 ≤ -20% (target), 不大幅牺牲 ann_ret.

## Quick test: neutral regime partial cash

| neutral_cash % | n_obs | mean_ret ann | median ann | max_dd | sharpe | hit_rate |
|---:|---:|---:|---:|---:|---:|---:|
| 0% (current) | 22 | +63.21% | +34.88% | **-21.38%** | 1.347 | 63.64% |
| 20% | 22 | +54.64% | +13.69% | **-15.44%** ✓ | 1.276 | 54.55% |

**Finding**: neutral_cash=20% 让 max_dd 达标 (-15.44% < -20%), 但 median ann 大幅下降 (-21.19pp)
- mean ann 仅降 8.57pp (54.64% 仍达跨年中位 ≥ 25% 目标多倍)
- 但 median ann 13.69% < 25% target → 不达标
- hit_rate 54.55% < 55% target → 略不达

## 根因

- max_dd 来源单一月 2026-02-26 portfolio -20.82% (6 stock 全负)
- 加 cash 仓 → 整体波动降 → 头部反弹月 (+40.88% / +26.67% 等) 被 dampen → median 跌
- mean 跌少因 outlier dampening 是 proportional

## 决策

**不改 default REGIME_WEIGHTS** (current spec from msaf_top_design_doc R38).

**加 Phase 5 Optuna search space**: regime cash % 作 hp 之一, 与 lambdamart/sniper/institution weights 联合调优, Optuna 50 trials × walk-forward expanding_monthly cross-validate.

## Phase 5 Optuna hp 建议

```yaml
regime_weights_optuna:
  bull:
    lambdamart: 0.20 ~ 0.40
    sniper: 0.30 ~ 0.50
    institution: 0.20 ~ 0.40
    cash: 0.00 ~ 0.10
  neutral:
    lambdamart: 0.25 ~ 0.45
    sniper: 0.20 ~ 0.35
    institution: 0.20 ~ 0.35
    cash: 0.00 ~ 0.20
  bear:
    lambdamart: 0.05 ~ 0.15
    sniper: 0.15 ~ 0.30
    institution: 0.05 ~ 0.15
    cash: 0.50 ~ 0.75
```

Constraint: sum = 1.00, all in [0, 1].

## 替代方案 (未验): 单 stock concentration risk control

- 每 top-K 单 stock max 25% weight (5 仓 equal 20% per stock 已满足)
- 加 sector cap: 同 sector ≤ 2 stocks (用 dim_stock_sw_industry JOIN)
- 加 inverse-vol weighting (alpha158 VOL20 / VOL60 反比)
- 加 mid-month stop: 单 stock -10% intraday → exit (需 daily rebal, 当前 monthly 不适用)

Phase 5 retrain 时 Codex 设计完整 vol-sizing spec, 写入 backend/services/strategies/sizing/.

## 引用

- ensemble.py min-max normalize + regime weight 加权 (current)
- ann_ret_median +34.88% from c13086cc (lambdamart_only baseline)
- max_dd -21.38% from 2026-02 单月 portfolio
- 用户原则 [[feedback-kpi-target-no-cap]]: KPI 越高越好不封顶 — 这次降 ann 换 max_dd 改善需 user 确认
