# Test Plan — Codex Round 24 综合 (5 Waves, 32 experiments, $9.90 budget)

设计: Codex agent aa433dcf3968bbe6c, 2026-05-17.

资源约束:
- GCP n2-standard-32 spot $0.45/h
- $10/月 credit (≈22h/月)
- 32 cores 用满
- PIT 严格不折中 (RankIC > 0.3 / Sharpe > 5 / win > 95% / 年化 > 100% = leakage 红旗自动停)

## Wave 总览 (32 GCP experiments + 48 paper_sim)

| Wave | Jobs | Parallel | Cores/job | Wall | Cost | Gate |
|---|---|---|---|---|---|---|
| W1 Feature Ablation | 4 | 4 | 8 | 5-8h | $2.25-3.60 | Yellow 0.0275+ |
| W2 Horizon × Seed | 8 | 8 | 4 | 2-4h | $0.90-1.80 | top config seed median 稳定 |
| W3 Model Variants | 4 | 4 | 8 | 3h | $1.35 | model bump >= +2pp annual |
| W4 Data Contract | 8 | 8 | 4 | 3h | $1.35 | Green 0.030+ + KPI 接近目标 |
| W5 Champion Verify | 8 | 8 | 4 | 4h | $1.80 | seed median 达终极目标 (年化30/dd20/胜率55/超额>0) |
| **合计** | **32 jobs** | — | — | **~22h** | **~$9.90** | 完整 ≤ $10 credit |
| paper_sim ablation | 48 | local Mac | 8 | 数小时 | $0 | sizing × universe × cost × swap |

## W3 Model Grid (4 jobs × 8 cores)

| Job | Model | Priority | Expected alpha bump |
|---|---|---|---|
| model_lambdamart | LambdaMART | P0 | 年化 +4pp / dd +1pp / 胜率 +2pp |
| model_catboost | CatBoost | P0 | 年化 +3pp / dd +1pp / 胜率 +1pp |
| model_xgboost | XGBoost | P1 | 年化 +2pp / dd 0 / 胜率 +1pp |
| model_nn | NN | P2 (跑一次, 过拟合 → DROP) | 年化 +1pp / dd -2pp / 胜率 0 |

## W4 Data Contract Grid (8 jobs × 4 cores)

| Job | 调整 | Priority | Expected |
|---|---|---|---|
| mintrain_06 | 6 month train | P2 | 响应快但易过拟合 |
| mintrain_12 | 12 month train | P0 | baseline 锚 |
| mintrain_18 | 18 month train | P0 | 稳定 + 响应折中 |
| mintrain_24 | 24 month train | P1 | 低方差 |
| label_ret | fwd_ret_after | P2 | 原始收益 sanity |
| label_risk_adj | risk-adjusted | P0 | 直接改善 dd + 胜率 |
| universe_liq_topN | 流动性 top-N | P0 | dd +2pp / 胜率 +2pp |
| universe_sector_neutral | sector neutral | P0 | dd +4pp / 胜率 +3pp |

## W5 Champion Verification (8 jobs × 4 cores)

5 seeds × champion (1/2/3/4/5) + 3 feature config final (drop_dead / a158_lhb_mc / v3_all).

## paper_sim 48 ablations (本地 Mac, $0 cost)

| Sizer | × Universe × Cost × Swap = combinations |
|---|---|
| equal / kelly / wilson_kelly / score_rank_diff_v1 | 4 |
| KEEP / liq_topN / sector_neutral | 3 |
| base / stress 2x cost | 2 |
| default / low_turnover swap | 2 |
| **Total** | **4 × 3 × 2 × 2 = 48** |

P0 优先 12 项 (sizer ∈ {equal, wilson_kelly, score_rank_diff_v1} × universe 3 × cost=base × swap=default).

## Gate Criteria

| Gate | Metric | Threshold | 失败 action |
|---|---|---|---|
| **Leakage Red** | RankIC > 0.3 / Sharpe > 5 / win > 95% / 年化 > 100% | 任一超阈 | 立即停, audit |
| **Yellow** | after-cost ann >= 10% / dd >= -35% / 月胜率 >= 50% / 超额 > -5pp | 任一未过 | 停 W3, audit alpha |
| **Green** | after-cost ann >= 20% / dd >= -25% / 月胜率 >= 52% / 超额 > 0 | 任一未过 | 不进 paper_sim live |
| **Champion** | seed median ann >= 30% / dd >= -20% / 胜率 >= 55% / 超额 > HS300 + 5 seeds 稳健 | 通过 | promote |

## DROP 项 (避免)

- full cartesian 57,600 experiments (multi-comparison risk)
- all_model × all_horizon (W2 先定 horizon, W3 只在 top horizon 比模型)
- all_seed_nonchampion (seed 只验证冠军)
- deep_phase4_ablation (Round 20 已判定 Phase4 多 CONST/noise)
- train_sizer_grid_on_gcp (sizer 属 paper_sim 层, 不重训)

## 风险 + Fallback

| Risk | Detection | Action |
|---|---|---|
| Overfit | seed median vs single seed 差 > 20pp | 停模型族扩展 |
| Multi-comparison | 试图跑 DROP 项 | 禁止 |
| Leakage | 异常高数字 | 立即停, PIT audit |
| Budget overrun | wall > 22h or cost > $9.90 | 当前 wave 完即停 |
| Wave stall | 连续 wave 无配置过 gate | 停 GCP, alpha root cause audit |
