# v5 Feature Plan — Drop CONST/Noise Phase 4 Cols (2026-05-17)

Based on AUDIT_2026_05_17.md (spearmanr on 100K sample of v4 panel).

## Cols to DROP (10 cols)

```python
V4_TRAINING_EXCLUDE = {
    # sector_momentum — CONST 0% coverage in all years (PIT industry observed_snapshot filter 太严)
    "sm_ret_5d", "sm_ret_20d", "sm_ret_60d", "sm_ret_120d",
    "sm_excess_20d", "sm_excess_60d", "sm_price_vs_ma20", "sm_price_vs_ma60", "sm_vol_60d",
    # holder_count_change_q_pct — 97% NULL, 季度 PIT 太 sparse
    "holder_count_change_q_pct",
}
```

## Cols to KEEP (21 cols) — Phase 4 useful

```python
V4_PHASE4_USEFUL = {
    # capital_flow — moderate signal (lhb 0.05, exec 低 0.01)
    "lhb_count_30d", "lhb_net_buy_pct_30d", "lhb_inst_buy_30d",
    "lhb_count_90d", "lhb_inst_buy_90d",
    "exec_buy_60d", "exec_sell_60d", "exec_buy_pct_60d", "exec_sell_pct_60d", "exec_net_signal",
    # mcap_decile — best Phase 4 (corr 0.074)
    "mcap_decile",
    # beta_60d — weak (corr 0.001) 但 PIT-safe + 不大
    "beta_60d", "beta_60d_zscore",
    # survey — coverage growing (44.7% in 2026), weak now (0.011) 但 future-promising
    "survey_count_30d", "survey_count_60d", "survey_inst_30d", "survey_inst_60d",
    # tom — cheap, marginal (corr 0.019)
    "tom_day_of_month", "tom_days_to_month_end", "tom_days_from_month_start",
    "tom_month_phase", "tom_is_first_week", "tom_is_last_week", "tom_is_month_turn",
}
```

## How to Apply

### Option A: 训练时 exclude (no panel rebuild) — 推荐

修改 `backend/scripts/run_p0b_lightgbm_optuna_v4.py` 的 `meta_cols` set:

```python
meta_cols = {
    "stock_code", "signal_date", ...
    # 已有
    "inst_quality_wavg", "inst_quality_max", ...
    # v5 exclude (Phase 4 dead features)
    "sm_ret_5d", "sm_ret_20d", "sm_ret_60d", "sm_ret_120d",
    "sm_excess_20d", "sm_excess_60d", "sm_price_vs_ma20", "sm_price_vs_ma60", "sm_vol_60d",
    "holder_count_change_q_pct",
}
```

Effect: 122 features → 112 features for next Optuna run. v4 panel 不动 (DB-friendly, 无写入冲突).

### Option B: build v5 panel — 大 work, 仅 cosmetic 提升

Run after Optuna v4 done:
1. CREATE TABLE mart_p0a_feature_label_panel_v5 LIKE v4 EXCLUDING 10 cols
2. INSERT SELECT v4.* EXCEPT (sm_*, holder_count_change_q_pct)
3. Update Optuna script --feature-panel v5
4. Re-run Optuna

Not necessary because LGBM ignores CONST cols. Defer.

## Expected Impact

- LGBM compute slightly faster (fewer cols to scan)
- Marginal RankIC improvement (cleaner feature space)
- Expected: 0-3% RankIC bump, NOT a game changer
- True alpha improvement requires NEW feature SOURCES (forecast EPS PIT 累积, sentiment, etc.)
