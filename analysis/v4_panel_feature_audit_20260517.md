# v4 Panel Feature Audit (2026-05-17)

Source: 100K reservoir sample (seed=42) of `mart_p0a_feature_label_panel_v4` (2,901,970 rows). Spearman vs `fwd_cost_after_20d` label.

## 总结

| 类别 | 数量 | 来源 |
|---|---:|---|
| 总 numeric 列 | 123 | DESCRIBE excluding stock_code/date/labels/vwaps |
| CONST (variance ~0) | **3** | tom_is_first_week, tom_is_last_week, tom_is_month_turn |
| HIGH NULL (0/100K non-null) | **13** | inst_* × 3, sm_* × 9, holder_count_change_q_pct |
| NOISE (\|spearman\| < 0.005) | **14** | formula_n_triggered, a158_v* / a158_std* / a158_kup* × 9, sector_ret_5d, roe_q, beta_60d / beta_60d_zscore |
| USEFUL (\|spearman\| >= 0.005) | 93 | a158 mom/sharpe/RSV + LHB + sector excess + mom_30d + sentiment + ... |

**总潜在 drop = 30 cols** (3 CONST + 13 NULL + 14 NOISE). 剩余 93 cols 应为 v5 panel.

## CONST cols (3)

```
tom_is_first_week
tom_is_last_week
tom_is_month_turn
```

来源: backend/services/features/time_of_month.py 把"是否月初/月末/月份切换"做成 boolean. 全表 ~3% True 但 LightGBM 拿不到边际信号 (单 split 学不出来). Drop.

## HIGH NULL cols (13, n=0 in 100K)

```
inst_quality_wavg                # 机构持仓加权质量 — 路径 A 缺数据
inst_total_holding_ratio         # 机构总持股比 — 路径 A 缺数据
top_inst_holding_ratio           # 头部机构持股比 — 路径 A 缺数据
holder_count_change_q_pct        # 季度股东户数变化 — 数据 gap
sm_ret_5d sm_ret_20d sm_ret_60d sm_ret_120d  # 主力收益 — 100% NULL
sm_excess_20d sm_excess_60d                  # 主力超额 — 100% NULL
sm_price_vs_ma20 sm_price_vs_ma60            # 主力均线偏离 — 100% NULL
sm_vol_60d                                   # 主力波动 — 100% NULL
```

来源:
- inst_* 3 列源自 `mart_institution_profile` PIT 路径 A 实施前. Codex Round 25 已修 `industry_pit.py` source_available_date, 但 inst_quality_wavg 等 3 列另有 [[project-pit-holder-data-gap]] 数据缺失 (`fact_top10_holder_period.notice_date` 100% NULL).
- holder_count_change_q_pct: 同源数据 gap.
- sm_* 9 列源自早期 smartmoney 实验 path, 全表 NULL = path 没启用. Wave 1 `v4_drop_dead_20d` 已 drop sm_* 9 列 + holder_count_change_q_pct = 10 cols.

**v4_drop_dead_20d 漏 drop**: 3 inst_* 列. v5 必须补.

## NOISE cols (14, |spearman| < 0.005)

| col | rho | 注释 |
|---|---:|---|
| formula_n_triggered | +0.0001 | 公式触发计数, 跟 fwd_20d 几乎无关 |
| a158_vma60 | -0.0001 | 60d 成交量 MA, 跟反转/收益弱 |
| sector_ret_5d | -0.0005 | 5d sector return, 短期 sector momentum 弱 |
| a158_kup | +0.0023 | K 线上影线/实体, 单 horizon |
| a158_std5 | -0.0030 | 5d 价格 std |
| a158_klen | +0.0032 | K 线长度 |
| a158_std10 | -0.0033 | 10d 价格 std |
| beta_60d | +0.0038 | 60d beta |
| a158_vstd5 | -0.0040 | 5d 成交量 std |
| roe_q | +0.0042 | ROE 季度 — 财务因子但对 20d 弱 |
| a158_kup2 | -0.0044 | K 线上影线 平方 |
| beta_60d_zscore | +0.0044 | 60d beta zscore |
| a158_vma5 | +0.0049 | 5d 成交量 MA |
| a158_vstd20 | +0.0049 | 20d 成交量 std |

来源:
- 8 个 a158_* (vma/std/kup/klen/vstd) — alpha158 短期价量, 对 20d horizon 弱
- 2 个 beta_60d* — Wave 1 `v4_a158_lhb_mc_20d` 已 drop
- 2 个 sector_ret_5d / formula_n_triggered — 弱信号
- 1 个 roe_q — 季度 ROE 对 20d horizon 弱

## USEFUL cols Top 15 (按 |rho| 排序)

| col | rho | 说明 |
|---|---:|---|
| sector_excess_60d | -0.1261 | sector 60d 超额 |
| a158_min60 | -0.1200 | 60d 最低价 |
| sector_ret_60d | -0.1182 | sector 60d 收益 |
| a158_ma60 | -0.1128 | 60d MA |
| a158_roc60 | -0.1091 | 60d ROC |
| a158_qtl60 | -0.1086 | 60d quantile |
| a158_sump60 | -0.1086 | 60d sum positive |
| sharpe_60d | -0.1073 | 60d sharpe |
| a158_rsv60 | -0.1019 | 60d RSV |
| a158_roc20 | -0.1009 | 20d ROC |
| a158_roc30 | -0.1005 | 30d ROC |
| a158_ma30 | -0.0995 | 30d MA |
| mom_30d | -0.0994 | 30d momentum |
| lhb_count_90d | -0.0964 | 90d LHB 上榜次数 |
| lhb_count_30d | -0.0961 | 30d LHB 上榜次数 |

**模式: 60d / 30d 中期反转 (rho 全负) 主导.** 跟 [[research-community-strategies]] (Round 28) 一致 — 中国 A 股残差反转 + 中期反转最稳.

## v5 panel 推荐 exclude-cols 列表

```python
V5_EXCLUDE_COLS = [
    # CONST (3)
    "tom_is_first_week", "tom_is_last_week", "tom_is_month_turn",
    # HIGH NULL inst (3)
    "inst_quality_wavg", "inst_total_holding_ratio", "top_inst_holding_ratio",
    # HIGH NULL holder (1)
    "holder_count_change_q_pct",
    # HIGH NULL sm (9)
    "sm_ret_5d", "sm_ret_20d", "sm_ret_60d", "sm_ret_120d",
    "sm_excess_20d", "sm_excess_60d",
    "sm_price_vs_ma20", "sm_price_vs_ma60", "sm_vol_60d",
    # NOISE a158 short (8)
    "a158_vma60", "a158_vma5", "a158_vstd5", "a158_vstd20",
    "a158_kup", "a158_kup2", "a158_klen", "a158_std5", "a158_std10",
    # NOISE other (5)
    "formula_n_triggered", "sector_ret_5d", "roe_q",
    "beta_60d", "beta_60d_zscore",
]
# 总 30 cols drop, 余 93 cols
```

Wave 2 配置建议 (跑完 Wave 1 后):
- `v5_clean_30d`: drop 全 30 cols, fwd_cost_after_20d
- `v5_clean_30d_horizon5`: 同 drop, fwd_cost_after_5d
- `v5_clean_30d_horizon60`: 同 drop, fwd_cost_after_60d
- `v5_keep_tom_signed`: drop 全 - 但保留 tom_day_of_month / tom_days_to_month_end / tom_days_from_month_start (continuous, 非 boolean) 测是否有 LightGBM-friendly time-of-month

## 触发条件

- Wave 1 跑完后跨 config 对比 (v3_all_20d / v4_all_20d / v4_drop_dead_20d / v4_a158_lhb_mc_20d) gate
- 如果 v4_a158_lhb_mc_20d (100 cols) >= v4_all_20d (123 cols), 进一步推进 v5 (93 cols) 测是否再升

## 反例 / 注意

- **不擅自 drop** 现在 panel 表里的物理列 (会破坏 Wave 1 in-flight 读). 用 `--exclude-cols` runtime drop.
- **drop list 用 100K sample 估计** — 全表跑 IC 会更准但慢. spearmanr 100K 经验跟 LightGBM 训练后 importance 排名一致 (Phase 4 多次验证).
- **roe_q** 在 20d horizon 弱不代表 60d horizon 也弱. 财务因子建议测 60d + 120d 才下结论. 暂保留作 60d horizon panel 候选.
- **beta_60d_zscore** vs **beta_60d**: 都弱, 但 zscore 标准化版本仍弱说明 beta 本身在 A 股 20d 无信号, 不是 scale 问题.

## 相关

- skill [[data-integrity-audit]] §3 (coverage audit pattern)
- skill [[pit-audit]] (per-col-group ablation Step 4)
- analysis/chunkymonkey_architecture_audit_20260517.md (Codex Round 26 §3 数据表管理)
- Wave 1 task 跑 4 configs 见 gcp/run_feature_ablation_grid.sh
