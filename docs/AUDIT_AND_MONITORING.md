# 综合审计与性能监测

**日期**：2026-04-24 · **版本**：v1（Phase 5 上线后）

---

## 1. 模型审计

| 模型 / 路径 | 状态 | 证据 | 保留/删除 |
| --- | --- | --- | --- |
| W1-W6 AI 事件评分（`qlib_event_prediction` 等）| ✅ 已于 M1 删除 | holdout IC 0.018（含 lookahead 前）| **已删除** |
| `train_event_qlib*.py` / `recall_similar_events.py` / `evaluate_model_health.py` | ✅ 已于 M1 删除 | 同上 | **已删除** |
| `qlib_follow_engine` / `qlib_follow_predictions` 表 | 🔴 代码保留 / 无活 router 引用 | M1 审计发现 | **建议删除**（见 §1.1）|
| `stable_cohort_pit` 策略 | 🟡 保留 `run_portfolio_mvp.py` 作对照 | CAGR 3.05% 低利用率失败 | 保留代码,不产品化 |
| `core_plus_overlay` δ 策略 | 🟡 保留 | CAGR 13.98% 改善量 ≈ 0 | 保留代码,不产品化 |
| `lhb_inst_net_buy` 及 _ge3/_ge5 | 🟡 保留 | 三档 CAGR -0.45 / 4.22 / 6.00 均未过门槛 | 保留代码,不产品化 |
| `exec_buy_ge0.5pct` 及 _ge1pct | ✅ **已验证通过** | IS+OOS 双期 Calmar>1.5, placebo 翻转 | **保留 + 作 feature 入 qlib panel** |
| `multidim_v1_20260424_002615` (本次主模型) | ✅ **当前主模型** | holdout IC 0.036 / RankIC 0.078 / WR 58.1% | **保留** |
| `qlib_full_engine` (Alpha158 股票预测) | ✅ 当前 DAG 在跑 | 日度 topK 用于股票详情页 | 保留 |
| `dim_stock_forecast_latest` / `fact_stock_forecast_features` | ✅ | 内部 Qlib 日度预测 | 保留（展示用）|

### 1.1 建议立即删除

1. **`backend/services/qlib_follow_engine.py`** + `qlib_follow_predictions` 表：M1 清理时曾留，审计确认**无活跃 router 引用**；仅测试文件 `test_qlib_follow_engine.py` 导入
2. **`data/qlib_follow_models/*.pkl` 中非最新的 multidim_v1 之前的 pkl**：占空间，保留最新 3 个即可
3. **空事实表**（0 行但代码已退役）：
   - `fact_setup_snapshot` (0 行)
   - `raw_fetch_batch` (0 行)
4. **`mart_counterfactual_eval` / `mart_exploration_bandit` / `mart_meta_label_*`**：属 M1 之前退役路径的残留，已无活引用

### 1.2 阶段性保留（Phase 5 后再评估）

- `fact_stock_character` / `fact_stock_archetype` / `fact_stock_industry_context` / `fact_regime_state`：**如果 Phase 5 qlib Alpha158 融合后仍不被模型使用**，考虑废弃
- `mart_bayesian_posterior` / `mart_meta_label_predictions`：属 W1-W6 meta-labeling 残留，Phase 5 后再评估

---

## 2. 变量 / 特征审计

### 2.1 当前 `fact_feature_panel` 41 列特征分类

**Pillar B 价量（新增 Alpha158-inspired 后 25 列）**

| 列 | 用途 | 训练 top 5? |
| --- | --- | --- |
| ret_1d/5d/20d/60d | 多期动量 | ret_20d 排名 6 |
| vol_z20d / vol_ratio_5_20 | 量能异动 | - |
| ma_ratio_5/20/60/250 | 均线偏离 | **ma_ratio_250 排名 5** |
| kmid/klen/kup/klow/ksft | K 线形态（Alpha158 核心）| - |
| vol_std_5d/20d | 波动率 | - |
| range_pos_20/60 | N 日区间位置 | - |
| momentum_diff | 短期超跌 | - |
| amount_chg_5d | 成交额变化 | - |
| rz_balance / rz_chg_5d_pct | 两融情绪 | - |

**Pillar A 事件（10 列）**

| 列 | 用途 | 训练 top 5? |
| --- | --- | --- |
| inst_event_count_30d/60d | 十大股东事件频率 | - |
| exec_buy_count_90d / exec_buy_ge1_count_90d | **M5 step 2 通过验证的高管增持**| - |
| lhb_inst_buy_count_30d/60d | 龙虎榜机构席位 | - |
| jgdy_count_60d | 机构调研密度 | - |
| dzjy_count_60d | 大宗交易密度 | - |
| days_since_exec_buy / days_since_lhb | 最近事件距离 | - |

**Pillar C 基本面（8 列，季度 forward-fill）**

| 列 | 用途 | 训练 top 5? |
| --- | --- | --- |
| shareholder_count_qoq | 股东户数 QoQ 变化 | **排名 10** |
| inst_count_qoq | 机构总数 QoQ | **排名 4** |
| fund_count_qoq | 基金数 QoQ | **排名 3** |
| qfii_count_qoq | QFII 数 QoQ | - |
| yjyg_lower/upper_pct | 业绩预告区间 | - |
| roe / eps_basic | 盈利基本面 | **eps_basic 排名 9** |

**Regime（2 列 + 3 列 one-hot）**

| 列 | 用途 | 训练 top 5? |
| --- | --- | --- |
| hs300_ret_20d | 市场 20d 动量 | **排名 2** |
| hs300_ret_60d | 市场 60d 动量 | **排名 1** |
| regime_up / regime_flat / regime_down | one-hot | - |

### 2.2 建议优化

- **drop 零重要性特征**：Phase 5 跑完后，按 feature_importance < 100 的列考虑去除（节约训练时间）
- **扩展 pillar A**：补 dzjy_premium_avg_60d（折价率均值）/ research_report_count_30d（研报发布密度）/ jgdy_regime 交互
- **regime × event 交互项**：`exec_buy_count_90d × regime_down` 作为独立特征（已经在 M5 step 2 证明 down regime WR 75%）

---

## 3. 评价方案

### 3.1 离线评价（每次训练时必做）

| 指标 | 说明 | 通过门槛 |
| --- | --- | --- |
| **Holdout IC** | 截面 Pearson 相关均值 | > 0.03 |
| **Holdout RankIC** | 截面 Spearman 相关均值 | > 0.05 |
| **Top decile 20d avg return** | 每日 top 10% 持 20d 均值 | > 1.5% |
| **Long-short spread** | top avg - bot avg | > 1.0% |
| **Top decile WR** | 每日 top 10% 收益为正占比 | > 55% |
| **Regime stability** | 三 regime 各自 top avg 均 > 0 | 必过 |
| **Placebo test** | 随机排序的 ghost model 结果 | IC < 0.01 |

### 3.2 在线评价（每日 cron + 每周回顾）

| 指标 | 采集 | 阈值 |
| --- | --- | --- |
| **每日 top-100 pred_score 分布** | run_daily_topk 输出 | std > 0.02（分辨度）|
| **20 天后 top-10 actual return** | fact_feature_panel.forward_ret_20d | avg > 1%，每周滚动窗口 |
| **top-100 行业分散度** | mart_daily_recommendation × dim_stock_tdx_industry | 不超过 40% 集中在单一 L1 |
| **Model IC 衰减** | 每日实测 IC vs holdout IC | 衰减幅度 < 50% 触发再训练 |

### 3.3 触发再训练条件

以下任一触发：
1. 连续 10 个交易日 `实测 IC < 0.015`
2. 新一个季度财报数据发布（shareholder_count / inst_count / yjyg 全量更新）
3. 市场 regime 发生状态跳变（up↔down 多于 3 次 / 月）
4. 手动命令 `python3 -m backend.scripts.run_full_pipeline`

---

## 4. 监测图表规格

### 4.1 API 端点

- `GET /api/rec/daily-topk?date=YYYY-MM-DD&limit=50&regime=up|flat|down`
- `GET /api/rec/model-performance?model_id=xxx`（返回 holdout + daily series + regime breakdown）
- `GET /api/rec/model-history?limit=20`

### 4.2 前端监测页（TODO）

**主看板 `/recommendation`**（规划）：
1. **当日 Top 50 推荐表**：rank / code / name / score / percentile / regime / 行业 L2 / 关键特征 hover
2. **模型指标卡片**：holdout IC / RankIC / top-decile avg / WR top / n_features
3. **每日实测 IC 走势图**（≈ 20 天后可算一期）：daily series from `/api/rec/model-performance`
4. **Regime breakdown 柱状图**：up / flat / down 各自 top-decile 实测 vs 预测
5. **Feature Importance 条形图**（降序 top 30）
6. **模型版本切换下拉**：`/api/rec/model-history` 列出所有 model_id + 创建时间

### 4.3 告警规则

| 告警 | 条件 | 动作 |
| --- | --- | --- |
| 模型失效 | 连续 10 日实测 IC < 0.015 | 邮件 + 标记 model_id.status='stale' |
| 数据缺口 | `price_kline_tdxhub` 当日 code 数 < 5000 | updater 重试 + 报警 |
| 特征突变 | 任意 Pillar 分项 QoQ 均值变化 > 2σ | 日志 INFO，不阻塞 |
| Regime 跳变 | 当日 regime_flag ≠ T-1 且 T-1 与 T-7 也不同 | UI 顶部 banner 提示"市场风格切换" |

---

## 5. 实施清单

### 已完成
- [x] Phase 1 数据层（price_kline_tdxhub / fact_fundamental_quarterly / fact_executive_trade_event / fact_lhb_event / fact_jgdy_event）
- [x] Phase 2 特征面板（fact_feature_panel 41 列）
- [x] Phase 3 Optuna + LightGBM 训练（holdout IC 0.036）
- [x] Phase 4 daily topK（mart_daily_recommendation）
- [x] Phase 5 Alpha158 因子扩展（+12 K 线 / 波动 / 区间特征）
- [x] API 端点（`/api/rec/daily-topk` / `/model-performance` / `/model-history`）
- [x] fact_dzjy_event 补齐（55 707 条）

### 待做

- [ ] 前端监测页 `/recommendation`
- [ ] 每日 cron 集成（run_daily_topk at 15:30 each trading day）
- [ ] 删除退役代码（qlib_follow_engine + meta_label_* + empty fact tables）
- [ ] Phase 6 qlib Alpha158 正统融合（用 pyqlib DataHandlerLP + LGBModel 正式训练）
