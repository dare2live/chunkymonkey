# 架构文档 · 机构行为研究平台

**项目定位**（2026-04-23 更新）：**机构行为研究平台**。
在 PIT 口径下，事件级 ML 与 non-ML stable cohort 路线均未显超额信号（holdout IC 0.018 / Portfolio excess CAGR -0.51%，见讨论文档 §2 γ+α 方案段）。**平台不承诺 alpha，只提供数据采集 + 画像展示 + cohort 研究回测工具。**

原则：**没用就删除，用就显示**——任何组件要么活着并被使用，要么就不在仓库里。

---

## 1. 层次划分

```
raw 数据         →   事实层 fact_*          →   画像 mart_*          →   研究工具
(只追加, 可信)       (事件 / 回测结果)          (机构 / 行业 / 股票)      (portfolio / cohort)
```

## 2. 组件清单（按真实用途）

### 2.1 生产数据管线

| 组件 | 位置 | 作用 |
| --- | --- | --- |
| 数据采集 DAG | `backend/routers/updater.py` | 上游抓取（十大股东 / 两融 / 行情 / 调研 / QFII / 财务）|
| `fact_institution_event` | data/smartmoney.db | 机构事件真相表 + 单次披露收益字段（gain_*d / max_drawdown_*d）|
| `price_kline` | data/market_data.db | A 股日线 qfq 完整历史 |
| `raw_margin_daily` | data/smartmoney.db | 两融日数据完整历史 |
| `mart_institution_profile` | data/smartmoney.db | 机构画像聚合（展示用）|
| `mart_current_relationship` | data/smartmoney.db | 当前持仓关系表 |
| `dim_stock_tdx_industry` | data/smartmoney.db | 股票 × TDX L1/L2 行业映射 |

### 2.2 研究工具

| 组件 | 位置 | 作用 |
| --- | --- | --- |
| `v_institution_l2_score` | smartmoney.db view | PIT 版 cohort 评分（`institution_L2_pit_20240930`，cutoff 2024-09-30）|
| `v_l2_profile` | smartmoney.db view | L2 行业画像聚合（基于 v_institution_l2_score）|
| `fact_institution_follow_backtest` | smartmoney.db | 回测结果存档（含 cohort_scheme 字段）|
| `event_simulator.py` | backend/services | 单事件路径仿真（cohort 回测依赖）|
| `run_follow_backtest.py` | backend/scripts | cohort walk-forward 回测，支持 `--event-cutoff` PIT 截断 |
| `run_portfolio_mvp.py` | backend/scripts | Portfolio 级对照回测（stable_cohort / all_events / random / HS300）|
| `fact_policy_equity_curve` / `_trade` / `_eval` | smartmoney.db | Portfolio 回测结果存档 |

### 2.3 展示界面

| 组件 | 特征 |
| --- | --- |
| 机构详情页 | 含 "擅长 L2" Layer B 卡片，黄色 banner "研究参考"，数据源 `v_institution_l2_score`（PIT）|
| 股票详情页 | 含四维画像卡片（resonance / margin / forecast / survey），黄色 banner "研究参考"；stage 维度已删除 |
| L2 行业弹窗 | `/api/inst/industry/l2/{l2_name}`，数据源 `v_l2_profile` |

## 3. 已退役（已从仓库删除，git 历史可查）

2026-04-23 M1 清理，原因：lookahead bias（§1 P0.1 九处污染源）+ PIT 基线 holdout IC 0.018 证明无信号。

- 表：`fact_event_features` / `fact_event_features_pit` / `qlib_event_prediction` / `qlib_model_evaluation` / `fact_similar_events` / 旧版 `v_institution_l2_score` / `v_institution_l2_score_pit`
- 脚本：`train_event_qlib.py` / `tune_event_qlib.py` / `train_event_qlib_pit.py` / `build_event_features.py` / `build_event_features_pit.py` / `recall_similar_events.py` / `evaluate_model_health.py`
- 后端：`/api/inst/event-predictions` 端点 / `_latest_model_id` / `list_event_predictions`
- 前端：`renderEventPredictionCard` / 五维画像的 stage 维度（公式方向反）

## 4. 暂停方向（需前置条件才重启）

| 方向 | 重启前置条件 |
| --- | --- |
| β cohort × regime 条件 edge | 1) 新数据源接入；2) fact_stock_* 历史快照回填到 2021；3) 外部第三方 labels 交叉验证 |
| 多目标 Optuna / 生产化 cron | 依赖 portfolio 级回测出现真实 excess edge |

见讨论文档 §2 γ+α 方案段。

## 5. 运行命令

```bash
# 日常 DAG 更新
python3 -m backend.scripts.run_full_update   # 或 /api/updater/run

# cohort 回测（PIT 截断）
python3 -m backend.scripts.run_follow_backtest \
    --scheme institution_L2 --top 100 --min-samples 20 \
    --walk-forward 0.7 --event-cutoff 20240930

# Portfolio MVP 基线对照回测
python3 -m backend.scripts.run_portfolio_mvp

# 后端（开发模式）
cd backend && python3 -m uvicorn main:app --reload --port 8000
```

## 6. 数据新鲜度

| 数据 | 频率 | 备注 |
| --- | --- | --- |
| price_kline / raw_margin_daily | 每日 | 随 DAG |
| fact_institution_event | 季度 + 披露事件时 | 历史完整 |
| mart_institution_profile / _current_relationship | 与 DAG 同频 | 每次重算，无 snapshot_date |
| v_institution_l2_score (PIT cutoff 2024-09-30) | 手动重建 | 研究用，不随 DAG 自动更新 |

## 7. 讨论文档 / 决策追溯

所有架构决策与历史失败教训见 `docs/discussion-report-2026-04-22.md`。关键节点：

- §1 P0 任务清单 + 修正段
- §2 2026-04-23 P0.B C0 PIT 基线（holdout IC 0.018）
- §2 2026-04-23 P1.A Portfolio MVP No-Go（excess CAGR -0.51%）
- §2 2026-04-23 收口段（三条路径 α/β/γ）
- §2 2026-04-23 γ+α 方案段（当前执行方案）
- §2 2026-04-23 M1 清理段（本次清理记录）

讨论文档用 §0 规则规范 Claude 与 codex 的协作发言格式。
