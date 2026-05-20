# Paper Sim 总览 — 历史 41 runs 留存 (2026-05-20)

> 用户 push: "把这些结果都留存, 最后做一个总览, 数据血缘 + 各种标签, 避免反复造轮子, 固化下来"
>
> 自动生成: `PYTHONPATH=backend python backend/scripts/paper_sim_overview.py`
> 自动留存: cron 每天 (可后续 install_resilience 加 entry).
>
> Schema (mart_paper_sim_kpi 加 column commit a2281696):
> - `sim_config_hash`: MD5(config yaml + model_id + start/end_date + panel_version) — 同 hash 直接 skip
> - `parent_sim_run_id`: 上一 sim_run, 形成参数变化曲线
> - `param_diff_json`: JSON diff vs parent_sim_run_id

## Critical Findings (2026-05-20 上午 4 critical runs, model=lgbm_phase5_session_20260518T160747)

| Variant | ann | dd | sharpe | win | turnover | Verdict |
|---|---|---|---|---|---|---|
| **minhold15**  | **108.18%** | -20.36% | **2.12** | 66.67% | 49.57x | **prod-candidate alpha 增强** (sharpe 达 perfect ladder ≥2.0) |
| champion_baseline | 67.79% | -20.81% | 1.66 | 71.43% | 54.88x | dd 用户接受, anti_churn FAIL |
| minhold5 | 53.50% | -17.38% | 1.56 | 66.67% | 48.82x | dd best 改善 |
| **maxpos10+minhold15** | 112.31% | **-26.13%** | 1.76 | 73.33% | 42.84x | **撤回 D** dd 突破死线 |

**机制 (minhold15)**: 强制持 ≥15d → stop_hit 假回调 18→9 减半 → trailing/hp_expired 长窗实现 alpha. min_holding 是**alpha 增强工具**不是 anti_churn fix.

**真金白银 verdict** (commit a0a17a0c PIT audit verified):
- 0 未来函数: walk_forward expanding_monthly + assert_pit_strict + T+1 paper_sim + current_close exit
- 0 absolute leakage: sharpe<5 / ann<100% / win<95% / uplift<50%
- 相对 +60% uplift 有机制解释 (exit timing 非 model feature leakage)
- 实盘 honest expect: ann ~70-80% (扣 frictions) / dd -20% / sharpe 2.12

## 历史最强 (verify 是否 leakage)

- `swap_v1_20260516_105028` ann **114.15%** / sharpe **2.57** / dd -7.41% / **win 100%** — 触发 leakage 警报 (win=100% 阈值), 需 ablation per col 群 PIT 干净度

## 4 历史灾难 (反例)

- `swap_v1_20260515_125633`: ann -80.56% / sharpe -8.65 / dd -6.92% / win 0% — 全失败
- `baseline_20260514_052949`: ann -26.54% / sharpe -0.61 / dd **-50.47%** — 灾难性 dd
- `swap_v1_20260516_071143`: ann -16.11% / sharpe -0.72 / win 28.57%
- `sizer_ablation_score_rank_diff_v1`: ann -2.84% / sharpe -0.002

## 41 Runs 完整 (按 built_at DESC)

## Raw mart_paper_sim_kpi dump (per overview script)

Database: `/Users/dp/Documents/M/stock/chunkymonkey/data/smartmoney.duckdb`
Rows: 41

## KPI Runs

| sim_run_id | ann_ret | sharpe | max_dd | win_rate | config_diff_vs_parent | parent_sim_run_id | sim_config_hash |
|---|---:|---:|---:|---:|---|---|---|
| champion_maxpos10_minhold15_20260520_121320_20260520_041321_2e4753 | 112.31% | 1.761 | -26.13% | 73.33% | N/A | N/A | legacy NULL |
| champion_minhold15_20260520_111606_20260520_031612_9137bf | 108.18% | 2.121 | -20.36% | 66.67% | N/A | N/A | legacy NULL |
| champion_minhold5_20260520_105535_20260520_025539_b968ac | 53.50% | 1.564 | -17.38% | 66.67% | N/A | N/A | legacy NULL |
| champion_baseline_20260520T102611_20260520_022612_4b63c0 | 67.79% | 1.660 | -20.81% | 71.43% | N/A | N/A | legacy NULL |
| sizer_ablation_score_rank_diff_v1_20260517_120049_656e66 | -2.84% | -0.002 | -22.25% | 50.00% | N/A | N/A | legacy NULL |
| sizer_ablation_equal_20260517_114503_0a11b0 | 68.31% | 0.907 | -21.70% | 45.00% | N/A | N/A | legacy NULL |
| baseline_20260517_004955_3da9b3 | 3.88% | 0.309 | -20.19% | 50.00% | N/A | N/A | legacy NULL |
| swap_v1_20260516_133642_3b9baa | 56.74% | 1.417 | -20.03% | 66.67% | N/A | N/A | legacy NULL |
| swap_v1_20260516_131621_6ba40e | 17.72% | 0.742 | -20.19% | 44.44% | N/A | N/A | legacy NULL |
| swap_v1_20260516_125455_7e7d39 | 46.64% | 1.276 | -16.19% | 62.50% | N/A | N/A | legacy NULL |
| swap_v1_20260516_124737_50afc2 | 44.98% | 1.472 | -13.85% | 75.00% | N/A | N/A | legacy NULL |
| swap_v1_20260516_123838_229dbc | 28.96% | 0.740 | -21.88% | 44.44% | N/A | N/A | legacy NULL |
| swap_v1_20260516_122705_4ce46c | 56.74% | 1.417 | -20.03% | 66.67% | N/A | N/A | legacy NULL |
| swap_v1_20260516_110311_ac9905 | 18.34% | 0.941 | -4.54% | 100.00% | N/A | N/A | legacy NULL |
| swap_v1_20260516_105028_cb9235 | 114.15% | 2.570 | -7.41% | 100.00% | N/A | N/A | legacy NULL |
| swap_v1_20260516_071143_049063 | -16.11% | -0.719 | -16.49% | 28.57% | N/A | N/A | legacy NULL |
| baseline_20260516_054035_eba079 | 19.32% | 0.755 | -23.90% | 64.29% | N/A | N/A | legacy NULL |
| swap_v1_20260516_011229_bede56 | 44.98% | 1.472 | -13.85% | 75.00% | N/A | N/A | legacy NULL |
| swap_v1_20260515_161006_bdacc9 | 43.42% | 0.771 | -24.31% | 44.44% | N/A | N/A | legacy NULL |
| swap_v1_20260515_155500_fae007 | 106.40% | 1.469 | -22.04% | 66.67% | N/A | N/A | legacy NULL |
| swap_v1_20260515_154702_a0e0d4 | 44.98% | 1.472 | -13.85% | 75.00% | N/A | N/A | legacy NULL |
| swap_v1_20260515_153710_7396f5 | 18.50% | 0.752 | -19.04% | 55.56% | N/A | N/A | legacy NULL |
| swap_v1_20260515_152602_67d5f0 | 59.78% | 1.485 | -20.03% | 66.67% | N/A | N/A | legacy NULL |
| swap_v1_20260515_151701_5b9c84 | 66.64% | 1.576 | -20.03% | 66.67% | N/A | N/A | legacy NULL |
| swap_v1_20260515_145920_5bb0e3 | 50.48% | 1.328 | -20.03% | 66.67% | N/A | N/A | legacy NULL |
| swap_v1_20260515_143433_633462 | 38.91% | 1.185 | -26.73% | 55.56% | N/A | N/A | legacy NULL |
| swap_v1_20260515_142249_ea0db5 | -6.93% | -0.053 | -24.84% | 44.44% | N/A | N/A | legacy NULL |
| swap_v1_20260515_135814_bda067 | 38.57% | 1.157 | -27.20% | 55.56% | N/A | N/A | legacy NULL |
| swap_v1_20260515_125708_29cfbe | 38.57% | 1.157 | -27.20% | 55.56% | N/A | N/A | legacy NULL |
| swap_v1_20260515_125633_564573 | -80.56% | -8.654 | -6.92% | 0.00% | N/A | N/A | legacy NULL |
| baseline_20260514_052949_2343f8 | -26.54% | -0.610 | -50.47% | 50.00% | N/A | N/A | legacy NULL |
| baseline_20260514_045654_413266 | -10.88% | -0.035 | -39.72% | 58.33% | N/A | N/A | legacy NULL |
| baseline_20260514_042350_9389e1 | -17.89% | -0.106 | -46.15% | 50.00% | N/A | N/A | legacy NULL |
| swap_v1_20260514_014754_e7bf02 | -9.85% | -0.425 | -34.09% | 50.00% | N/A | N/A | legacy NULL |
| baseline_20260514_012400_87c747 | 3.78% | 0.292 | -30.08% | 62.50% | N/A | N/A | legacy NULL |
| swap_v1_20260514_005652_aa6b5c | -15.78% | -0.478 | -41.80% | 37.50% | N/A | N/A | legacy NULL |
| baseline_20260514_003913_f3f729 | -39.73% | -0.218 | -79.30% | 62.50% | N/A | N/A | legacy NULL |
| baseline_20260513_165046_cdf132 | 0.00% | 0.000 | 0.00% | 0.00% | N/A | N/A | legacy NULL |
| baseline_20260513_164402_217afb | -52.14% | -1.086 | -31.17% | 20.00% | N/A | N/A | legacy NULL |
| swap_v1_20260513_120214_7fccb2 | 65.69% | 1.904 | -20.75% | 81.58% | N/A | N/A | legacy NULL |
| baseline_20260513_113142_0c8902 | 18.68% | 0.648 | -32.11% | 65.79% | N/A | N/A | legacy NULL |

## Lineage Tree

- baseline_20260513_113142_0c8902
- baseline_20260513_164402_217afb
- baseline_20260513_165046_cdf132
- baseline_20260514_003913_f3f729
- baseline_20260514_012400_87c747
- baseline_20260514_042350_9389e1
- baseline_20260514_045654_413266
- baseline_20260514_052949_2343f8
- baseline_20260516_054035_eba079
- baseline_20260517_004955_3da9b3
- champion_baseline_20260520T102611_20260520_022612_4b63c0
- champion_maxpos10_minhold15_20260520_121320_20260520_041321_2e4753
- champion_minhold15_20260520_111606_20260520_031612_9137bf
- champion_minhold5_20260520_105535_20260520_025539_b968ac
- sizer_ablation_equal_20260517_114503_0a11b0
- sizer_ablation_score_rank_diff_v1_20260517_120049_656e66
- swap_v1_20260513_120214_7fccb2
- swap_v1_20260514_005652_aa6b5c
- swap_v1_20260514_014754_e7bf02
- swap_v1_20260515_125633_564573
- swap_v1_20260515_125708_29cfbe
- swap_v1_20260515_135814_bda067
- swap_v1_20260515_142249_ea0db5
- swap_v1_20260515_143433_633462
- swap_v1_20260515_145920_5bb0e3
- swap_v1_20260515_151701_5b9c84
- swap_v1_20260515_152602_67d5f0
- swap_v1_20260515_153710_7396f5
- swap_v1_20260515_154702_a0e0d4
- swap_v1_20260515_155500_fae007
- swap_v1_20260515_161006_bdacc9
- swap_v1_20260516_011229_bede56
- swap_v1_20260516_071143_049063
- swap_v1_20260516_105028_cb9235
- swap_v1_20260516_110311_ac9905
- swap_v1_20260516_122705_4ce46c
- swap_v1_20260516_123838_229dbc
- swap_v1_20260516_124737_50afc2
- swap_v1_20260516_125455_7e7d39
- swap_v1_20260516_131621_6ba40e
- swap_v1_20260516_133642_3b9baa

## Parameter Impact

No parent-child paper_sim pairs found.

