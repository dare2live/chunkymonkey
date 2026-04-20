# signals_v2 · Baseline V4（2026-04-20 复核）

> 目的：**诚实披露 V6 在数据翻倍（fact_institution_event 29,684 → 56,326）且合同负债落库（68% → 88%）后的真实表现**，为后续 Step 5 视图整合（B + C 阶段）提供判断依据。

## TL;DR

1. V6 仍然有 edge，但**缩水 26%**：cohort follow EV edge 从 `+14.44pp` → `+10.64pp`，n 从 128 → 91。
2. **V6 在全量历史上 edge 是负的**（`-1.32pp`）— HANDOFF 从未披露这个事实。V6 的 alpha 主要来自近 180 日，不是稳定现象。
3. Qlib 第 4 次重训：IC `-0.025`（vs Phase 4c `-0.020`）。合同负债覆盖从 68% 提到 88% **对 IC 无改善**。Phase 4 结论锁死——不可用作合成评分。
4. D8（调研活跃度）仍持续负向：两口径都把 edge 拖到 -3~-7pp。默认禁用继续正确。

## 1. Phase A1 · V6 全量 backtest + cohort 双口径对照

数据基础：`fact_institution_event` 已成熟（gain_60d 非空）事件 = **29,679** buy，涵盖 2023-04 ~ 2026-01 的季报发布期。

### 全量 backtest_historical

| 配置 | follow_n | F_EV | F_WR | blind_EV | **edge** |
|---|---:|---:|---:|---:|---:|
| V3 baseline（无硬规则） | 3097 | +1.14% | 43.4% | +3.83% | **-2.69pp** |
| V5a（premium≤15 + 黑名单 + 持仓） | 1788 | +2.49% | 47.4% | +3.83% | **-1.34pp** |
| V6（V5a + D1+D3+D5） | 303 | +2.51% | 40.3% | +3.83% | **-1.32pp** |
| V6 + D8(survey≥1) | 230 | -3.47% | 29.1% | +3.83% | **-7.30pp** |

**关键观察**：
- 所有档位在全量历史上 edge 都是负的——`blind_buy` 盲跟效应 +3.83% 反而击败了任何筛选。
- V6 把 follow 档压缩到 1.0% 总事件（303/29684），**筛掉量大但没拉高 EV**。
- V5a 与 V6 全量 edge 几乎相同（-1.34 vs -1.32），说明 D1+D3+D5 在全量历史上贡献极小。

### cohort_recent_matured（近 180d 已成熟）

| 配置 | cohort | follow_n | F_EV | F_WR | blind_EV | **edge** |
|---|---:|---:|---:|---:|---:|---:|
| V3 baseline | 5696 | 1160 | +6.53% | 53.8% | +7.33% | **-0.80pp** |
| V5a | 5696 | 560 | +9.68% | 63.9% | +7.33% | **+2.35pp** |
| V6 | 5696 | **91** | **+17.97%** | **71.4%** | +7.33% | **+10.64pp** |
| V6 + D8 | 5696 | 18 | +4.35% | 55.6% | +7.33% | -2.98pp |

对比 HANDOFF 2026-04-19 数字：V6 follow 128 → 91 (-29%)，edge +14.44pp → +10.64pp (-26%)。

### 季度趋势揭示的真相

V6 follow 档按季度分布（节选）：

| 季度 | F_n | F_EV | F_WR | B_EV | EV 差 |
|---|---:|---:|---:|---:|---:|
| 2023-Q3 | 22 | -2.26% | 45.5% | +2.11% | **-4.37pp** |
| 2023-Q4 | 178 | -4.78% | 23.6% | -8.80% | +4.02pp |
| 2024-Q2 | 1 | -20.81% | 0% | -9.28% | -11.53pp |
| 2025-Q1 | 3 | -11.23% | 0% | +1.67% | -12.90pp |
| 2025-Q2 | 8 | +9.94% | 62.5% | +16.38% | -6.44pp |
| 2025-Q3 | 12 | +1.83% | 58.3% | +1.70% | +0.13pp |
| **2025-Q4** | **79** | **+20.43%** | **73.4%** | +12.34% | **+8.09pp** |

V6 的 +10.64pp cohort edge **几乎完全来自 2025-Q4 一个季度**（n=79 拿下 +20.43% EV）。其他季度要么样本太少，要么 edge 为负。

## 2. Phase A2 · Qlib 第 4 次重训

### 特征填充率体检（已成熟样本 29,679）

| 特征 | 覆盖 | 备注 |
|---|---:|---|
| `premium_pct` / `event_type_is_new_entry` / 13 个 TDX one-hot / 解禁 / 调研 | 100% | 已知 |
| `contract_liabilities_yoy` | **46.9%** | HANDOFF 时 68%，但抽完 YoY 降为 47%（需上一年同期） |
| `holder_count_yoy` | 54.5% | 同上，YoY 有分母约束 |
| `forecast_profit_yoy_mid` | 97.9% | D3 不需要 YoY |
| `revenue_yoy` / `profit_yoy` | **6.6%** | dim_financial_latest 稀疏 |
| `contract_to_revenue` | 4.0% | 死特征 |
| `days_since_industry_latest_high` | **0.0%** | 死特征 |

### 训练结果

| 训练窗口 | n_train | n_valid | Valid IC | 备注 |
|---|---:|---:|---:|---|
| train_end=20260101 valid=202510-202601 | 26,624 | 3,055 | **-0.0252** | 对照 HANDOFF Phase 4c (-0.0196) |
| train_end=20250801 valid=202505-202508 | 23,911 | 122 | +0.0050 | valid 样本太小，不具说服力 |

**结论**：合同负债覆盖从 68% → 88% 对 IC 无改善（IC 甚至略微下跌）。Phase 4 判定的"Qlib follow 模型在 30K 样本密度下无法突破 IC=0"继续成立。

### 特征重要性 Top 5（稳定版 train_end=20260101）

1. `inst_recent_ev_60d`（D7）23
2. `return_20d_before` 21
3. `roe` 19
4. `forecast_profit_yoy_mid`（D3）17
5. `forecast_profit_yoy_mid_z`（行业 z-score）17

`contract_liabilities_yoy` 仅排第 14（重要性 5）—— 即便覆盖提升，模型也不把它当关键信号。

### qlib_follow_predictions 状态

**0 rows**。表已建但从未填充。前端若要挂"第二意见"tile，前提是批量预测。考虑到 IC ≈ 0（相关性显著性阈值 0.05 差一个数量级），**展示反而会误导用户**。

## 3. 决策建议（写给下一轮 claude 和用户）

### V6 硬规则 — 保留，但 UI 要诚实披露

- 当前 `DEFAULT_CONFIG` 保持：`max_premium_pct=15, max_holder_yoy_pct=30, min_forecast_profit_yoy=20, max_unlock_ratio_180d=5, min_survey_count_90d=0`
- **cohort 卡片上应该加一个提示**：「V6 的 +10.64pp edge 主要来自 2025-Q4 单季度。近 180d 样本 n=91，方差大。历史全量 edge 为负。」
- 不调阈值（避免继续过拟合 2025-Q4 这一季）

### Qlib follow — 封存骨架，不做前端集成

- 保留 `qlib_follow_engine.py` 代码 + model artifacts（作为"第二尝试"历史记录）
- **不填充 qlib_follow_predictions 表**
- **不挂前端 tile**
- 复跑时机：等 `fact_institution_event` 扩到 100K+ 事件后重评估

### D8 调研 — 继续默认禁用

两个口径都负向（全量 -7.30pp / cohort -2.98pp）。HANDOFF 的"已被关注 = 已被定价"假说继续成立。

### 下一阶段方向

Phase A 结论支持 **Step 5 视图整合（B + C）继续推进**：
- V6 主力地位不变，前端不需要推倒重来
- Qlib 封存意味着 detail 抽屉不需要新增 tile（省了一个事）
- 可以专注于删 legacy 视图 + 死代码清理

## 4. 核查命令

```bash
# 复跑 Phase A1（V6 cohort + 全量对照）
python3 scripts/phase_a_recheck.py

# 复跑 Phase A2（Qlib 重训）
python3 scripts/phase_a2_qlib_retrain.py

# 查 qlib 模型累积历史
sqlite3 data/smartmoney.db \
  "SELECT model_id, n_samples, valid_ic, finished_at \
   FROM qlib_follow_model_state ORDER BY finished_at DESC LIMIT 5"
```

—— Phase A 完
