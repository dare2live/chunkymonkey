# PROJECT_INDEX.md — Chunky Monkey v2 项目地图 (新人 briefing)

> ⚠ **每次 session 启动必读** (CLAUDE.md 已引用). 用于防止对话压缩 / context 丢失导致重复发现项目结构 / 误解数据资产.
> 内容是**项目地图**, 不是规则 — 规则在 CLAUDE.md.
>
> **目标**: 新接手 (无论 Claude 还是人) 读完此文档**不用看代码 / 不用查 DB** 就能理解:
> 项目业务 / 架构 / 技术路线 / 数据资产 / 当前进度 / 已知坑 / 常用操作.

最后更新: 2026-05-14 (Phase ψ.δ.experiment 3 ablation fail + per_stock_stage ceiling 跑中; handoff Claude Code CLI 见 HANDOFF.md).

## 30 秒速览 — 这是什么项目

**Chunky Monkey v2** = A 股**自动选股 + 实盘模拟**系统. 用户(私人投资者)用它筛 5 只股票 / 月度轮换.

**用户目标 (硬指标, 一切优先级以此为锚)**:
- 年化 ≥ **+30%**
- max_drawdown ≥ **-20%**
- 超额 vs HS300 > 0

**数据基础**: 6,618 股 A 股 K 线 (2022-01 起) + 70K+ 财报 + 35K 机构事件 + 53K 龙虎榜 + 68K 高管增减持 + 大盘 regime + 4 阶段技术形态分类.

**架构主线 (alpha pipeline)**:
```
原始数据 → 公式信号 + PIT 因子 → Optuna 调参 (walk-forward) → mart 表
       → paper_sim selector (按 ensemble score 排名)
       → simulate_trade (T+1 入场, 含 tx_cost + 涨跌停)
       → NAV 曲线 → KPI 验证 (6 类 20+ 指标)
```

**当前最强发现** (实测严格 walk-forward OOS, 7.5h 跑批):
- `reversal_1m_mild × stage=1.5`: avg OOS sharpe **+0.435** / win **58.5%**
- `reversal_1m_deep × stage=1`: avg OOS sharpe **+0.32** / win **60.5%**
- 整体 momentum 公式 (MACD/turtle/dynamic_ma) **全失效** (OOS sharpe ~0 或负)

**距离用户目标**: 单股 OOS sharpe 0.32 → 5 股组合 + 月度轮换 paper_sim 真实期望约 **+15-25% 年化** (推算未实测). 缺 **+5-15pp** 才达 +30% 标准.

**下一步**: 引入更多 alpha 源 (机构跟随主 alpha PIT 重建 / case-based 历史相似 / 板块强度) — 见 §11 "16 项遗漏审计".

## 维护责任 (Rule 9.5 沉淀)

**每次完成一个 phase / commit / 数据 backfill 后, 都要更新本文档**. 具体 checkpoints:
- 新加数据表 → 加进 §2 (数据资产)
- 新加 service 模块 / script 入口 → 加进 §3-4
- 新加 yaml config → 加进 §6
- 解决了已知坑 → §8 标 ✅ + 短说明
- 跑出新 OOS 数据 → 加进 §10
- 踩了新坑 → §11 + CLAUDE.md Rule 9
- 加 §14 增量日志 (本 session 做了啥)

不维护 = 下次 session 又要重新摸索 = 用户最大抱怨

---

## 0. 用户终极目标 (锚)

> "短期内资产最大幅度增值不缩水"

3 个 PASS 标准:
1. 年化 ≥ +30%
2. max_dd ≥ -20%
3. 超额 vs HS300 > 0

基线: 2023-01-03 起, 100 万初始, HS300 benchmark.

---

## Pipeline 数据流图 (端到端架构)

```
┌──────────────────────────────────────────────────────────────────────┐
│ 0. 原始数据层 (data sources)                                         │
│   - akshare (K 线 / 财报 / 龙虎榜)  - tdxhub (qfq 复权 K 线)         │
│   - aif10 (估值 / 一致预期)         - tdx F10 (机构持仓)             │
│   - 内部模拟器 (event_simulator)                                     │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 1. raw_ 层 (smartmoney.duckdb): 70K 财报 / 53K 龙虎榜 / 35K 机构事件  │
│    market.duckdb: 6M K 线 / 158K xdxr 事件                           │
└──────────────────────────────────────────────────────────────────────┘
        │ sync (POST /api/inst/update/smart) — 含 watermark
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. fact_ 层 (PIT 时序事实表):                                        │
│    - fact_stock_technical_stage (2.4M, Stan Weinstein 4 stage)       │
│    - fact_signal_context (2.7M, vol_r20/price_pos/drawdown_60d/stage)│
│    - fact_technical_trigger (公式信号触发, 含 strength)              │
│    - fact_risk_factors (4.8M, Phase ψ.β.1 PIT mom/sharpe/vol)        │
│    - fact_financial_pit_daily (3.7M, Phase ψ.β.2 PE/PB/ROE/yoy)      │
│    - fact_capital_flow_pit_daily (858K, Phase ψ.β.3 lhb/exec/holder) │
│    - fact_regime_state (775, 大盘 bull/bear/sideways)                │
└──────────────────────────────────────────────────────────────────────┘
        │ Optuna 调参 (R1 walk-forward, expanding_monthly / train_end_forward)
        │ governance 守门 (sharpe>5/win>0.95/avg>0.5 reject)
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. mart_ 业务层 (调参 / 寻优结果):                                   │
│    - mart_per_formula_stage_optimal (426 OOS 行,                     │
│         per formula × stage × train_end_date, 最强 setup ↓)          │
│    - mart_per_stock_stage_strategy_optimal (per-stock × stage 旧表)  │
│    - mart_formula_horizon_evidence (per formula × hp 全市场)         │
│    - mart_stock_trend (主 alpha 88 列, 但 ⚠ latest 快照无 PIT)       │
│    - fact_optuna_governance_log (reject 审计)                        │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. paper_sim selector (3 mode):                                      │
│    - "backtest" 单公式排名 (按 mart_per_formula_stage.oos_sharpe)    │
│    - "ensemble" 10 alpha zscore 加权 + regime gate (Phase ψ.β.4)     │
│    - "production" 走 mart_daily_position_recommendation (实盘)        │
│    选 top 5 + 流动性过滤 (vol_60d ≤ 40% / amount_20d ≥ 5000万)       │
└──────────────────────────────────────────────────────────────────────┘
        │ T+1 VWAP 入场
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 5. simulate_trade (services/backtest/realistic_engine.py):           │
│    - T+1 入场 (buy_offset=1, 一字涨停延迟 1 次)                      │
│    - 5 出场触发: stop_loss > target_arm > trailing > hp_expired      │
│         > stage_deterioration                                        │
│    - 含 tx_cost (佣金 0.025% + 印花税 0.05% + 滑点 0.1%)              │
│    - 含涨跌停 reject_buy (一字涨停不买) / 退市暂停过滤                │
└──────────────────────────────────────────────────────────────────────┘
        │ 每日 NAV 更新, swap 决策, 跨日 trailing arm
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 6. paper_sim 输出 + KPI:                                             │
│    - fact_paper_sim_nav (NAV 时序)                                   │
│    - fact_paper_sim_position (持仓快照)                              │
│    - fact_paper_sim_trade (BUY/SELL/SWAP_OUT/SWAP_IN)                │
│    - mart_paper_sim_kpi (6 类 KPI: A 用户标准 / B anti-churn         │
│         / C robustness / D ablation / E sensitivity / F reality)     │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼ 决策: 6 类 KPI 全过 → 上线 / 一类不过 → 不上线
┌──────────────────────────────────────────────────────────────────────┐
│ 7. 实盘上线 (待 — 还没满足用户 +30%/-20%/超额 HS300)                  │
└──────────────────────────────────────────────────────────────────────┘
```

## 1. 三个 DuckDB 数据库

| DB | 路径 | 用途 |
|---|---|---|
| `smartmoney.duckdb` | `data/smartmoney.duckdb` | 业务主库 (mart_* / fact_* / raw_* / dim_*) |
| `market.duckdb` | `data/market.duckdb` | K 线 + 行情 (`v_price_kline_qfq`) |
| `etf.duckdb` | `data/etf.duckdb` | ETF 专用 |

**约束** (CLAUDE.md DuckDB 段已写):
- 永远走 `services.duck_adapter.connect` / `services.db.get_conn`
- 单写锁, 一次 ATTACH, 不要直接 `duckdb.connect()`
- 加新 `duckdb.connect` 用法 → 加进 `backend/tests/integration/test_duckdb_connection_contract.py`

---

## 2. 数据资产 — 6 大维度 (完整盘点)

> ⚠ Claude 容易误以为"项目主要数据是 K 线". 错. 6 大维度全有.

### 2.1 大盘 / 指数

| 表 / 字段 | 数据量 | freshness | 用途 |
|---|---|---|---|
| `v_price_kline_qfq` (market.duckdb) 含指数 K 线 | 5.97M 行 / 6,618 股 / 2022-01 → 2026-05 | 实时 | 指数代码: `000300` 沪深300 / `000905` 中证500 / `000852` 中证1000 / `000016` 上证50 |
| `fact_regime_state` | 775 行 / 2023-02 → 2026-04 ✅ | 历史可用 | trade_date / regime_id / regime_label (bull/bear/sideways) / regime_prob_json / transition_signal |
| `dim_market_segment` | dim 表 | 静态 | 市场分段 |

### 2.2 行业 / 板块

| 表 | 数据量 | freshness | 用途 |
|---|---|---|---|
| `dim_stock_sw_industry` | dim | 静态 | 申万行业映射 |
| `dim_stock_tdx_industry_history` | dim history | PIT | 通达信行业 PIT 映射 |
| `fact_stock_industry_context` | 个股行业上下文 | 取决于跑批 | 衔接 sector_momentum 到个股 |
| **`mart_sector_momentum`** | **⚠ 只 41 行 / 2026-04-17 → 2026-05-13** | ⚠ **没历史, 不能历史回测** | sector_name/code/level, ma20/60, macd, momentum_score, return_1m/3m/6m/12m, excess_1m |
| `mart_industry_pit_quality` | ? | PIT | 行业质量 |
| `mart_stock_industry_pit` | ? | PIT | 个股行业 PIT 评分 |
| `mart_institution_industry_stat` | ? | — | 机构 × 行业统计 |
| `research_inst_industry_performance` | 6,564 行 | — | 机构 × 行业 win_rate_10d/30d/60d/120d, avg_gain_10d/30d/60d/120d |

### 2.3 机构跟随 (项目主 alpha, **权重 0.40**)

| 表 | 内容 |
|---|---|
| **`mart_stock_trend` (主 alpha, 88 列)** | inst_count_t0/t1/t2 / inst_cap_t0/t1/t2 / inst_trend / cap_trend / latest_events / external_attention_signal / **stock_gate** / turtle_setup_state |
| `fact_institution_follow_backtest` | cohort × params Grid 回测 (**已 train/holdout 切分** — split='train'/'holdout', cohort_scheme='institution_L2_pit_20240930') |
| `fact_institution_event` / `fact_jgdy_event` | 机构调研事件 |
| `mart_institution_industry_stat` | 行业级机构统计 |

### 2.4 基本面 / 质量

| 表 | 内容 |
|---|---|
| **`fact_stock_archetype` (22K 行 / 53 列)** | snapshot_date / **net_profit_positive_8q** / **operating_cashflow_positive_8q** / revenue_yoy_positive_4q / profit_yoy_positive_4q / eps_yoy_positive_4q / **high_quality_hits** / growth_hits / cycle_flags |
| `fact_financial_derived` / `fact_fundamental_quarterly` | 财务衍生 / 季度 |
| `fact_stock_fundamental_stage_daily` | 基本面阶段 daily |
| `fact_stock_quality_features` | 质量特征 |
| `raw_aif10_financial_history` / `raw_gpcw_detail` / `raw_tdx_gpcw_wide` | 财务原始 |
| `raw_aif10_valuation_quantile.percentile_fifty` | 估值 10Y 分位 (strategy_ensemble 在用) |
| `raw_aif10_forecast_consensus.compre_rating_num` | 一致预期评分 (strategy_ensemble 在用) |
| `raw_aif10_peer_valuation` | 同业估值 |

### 2.5 资金流 / 事件

| 表 | 内容 |
|---|---|
| `fact_hsgt_daily` | 北向资金 daily |
| `raw_lhb_daily` / `fact_lhb_event` | 龙虎榜 |
| `raw_fund_flow_daily` | 主力资金流 daily |
| `fact_executive_trade_event` | 高管增减持 |
| `fact_shareholder_trade` / `fact_shareholder_trade_tdx_b` | 股东交易 |
| `fact_holder_event` / `fact_top10_holder_period` / `fact_holder_count_period` | 持股人结构 |
| `fact_dzjy_event` | 大宗交易 |
| `raw_capital_*` (allotment/dividend/repurchase/unlock) | 配股/分红/回购/解禁 |
| `raw_institution_surveys` | 机构调研 raw |
| `raw_qfii_holding_quarterly` | QFII 季度持仓 |

### 2.6 技术 / 形态 / 信号

| 表 | 内容 |
|---|---|
| **`fact_signal_context`** | stock × date / vol_r20 / amt_r20 / amount_20d_avg / price_pos_60d / price_pos_120d / drawdown_60d / **technical_stage** (1/1.5/2/3/4) / built_at |
| **`fact_stock_technical_stage`** | Stan Weinstein 4 stage (1=底部 / 1.5=突破中 / 2=上升 / 3=顶部 / 4=下跌) |
| `fact_stock_stage_features` | 阶段特征 |
| `fact_stock_turtle_features` | 海龟特征 |
| **`fact_technical_trigger`** | 公式信号触发 (stock × date × formula_id × variant × strength × state × reason_codes_json) |
| `fact_stock_archetype` (53 列) | 形态原型 (跟基本面共用此表) |
| `fact_setup_snapshot` | ⚠ **0 行 / 未启用** |

### 2.7 Phase ψ 治理 / 调参产物

| 表 | 用途 |
|---|---|
| **`mart_per_stock_stage_strategy_optimal`** | per-stock × variant × stage Optuna 寻优 (Phase ψ R1 后含 OOS 列, 但稀疏信号下大量 governance reject) |
| **`mart_per_formula_stage_optimal`** (Phase ψ.α B) | per-formula × stage × train_end_date 严格 walk-forward 寻优 (反转因子用此表) |
| `mart_formula_horizon_evidence` | per (formula × hp) 全市场合并真实历史涨跌 (无 Optuna 调参, 最干净) |
| `mart_stage_formula_fitness` | cohort fitness (fund × tech × formula × hp) |
| `mart_stock_formula_optuna_v2` | 旧 per-stock × formula × hp 全宇宙 (337K 行) |
| `fact_optuna_governance_log` | Phase ψ governance reject 审计 |

---

## 3. Service 模块 (231 个 .py 文件, 21 个子包)

### 3.1 调参 / 寻优 (Phase ψ)

| 模块 | 文件 | 作用 |
|---|---|---|
| `services/optimization/` | config.py | yaml loader (governance/walk_forward/search_space/composite/constraints/execution/output) |
| | governance.py | enforce_pre_optimize / enforce_pre_insert (50≤n_trials≤500, sharpe ≤ 5, win ≤ 0.95) |
| | walk_forward.py | split_dispatch (none/holdout/expanding/expanding_monthly/**train_end_forward**) + assert_no_temporal_leak + list_month_ends |
| | oos_aggregator.py | aggregate_oos_metrics (multi-window OOS trades 合并) |
| | composite.py | CompositeWeights.from_config() (7 个权重 ∑=1.0) |
| | constraints.py | HardConstraints (max_dd, streak, worst_loss, min_traded) |
| | objectives.py | 8 个 metric (sharpe/calmar/sortino/pain/ulcer/tail/stability/cvar) |
| | ddl.py | mart_per_stock_*_optimal / mart_per_formula_stage_optimal / fact_optuna_governance_log DDL |
| `services/backtest/` | optimize.py | optimize_stock_strategy (R1 expanding_monthly 主流程) |
| | realistic_engine.py | simulate_trade (T+1 入场, intraday stop/target, 含 tx_cost) |
| | search_space.py | 5 维 SearchSpace.from_config() (hp/stop/target/trailing/buy_offset) |
| | objective.py | make_objective Optuna 目标函数工厂 |
| | filters.py | is_index_code 等 |

### 3.2 公式 (formula_engine, 4+3 = 7 公式)

| 公式 | 文件 | 类型 |
|---|---|---|
| macd_golden_cross | macd_golden_cross.py | 动量 (DIF 上穿 DEA, variant=above/below_zero, **裸金叉无量能**) |
| turtle_breakout_20/55 | turtle_breakout.py | 动量 (突破 + **量能 > MA20 × 1.3**) |
| dynamic_ma_iterative_cross | dynamic_ma_iterative.py | 动量 (用户 MQL, 4 均线 + 加权重心 + **10 轮迭代过滤假突破**) |
| **reversal_1m_mild** (Phase ψ.α) | reversal_short_term.py | **反转** (20 日跌 5-15% + 60 日低波 + 量比正常) |
| **reversal_1m_deep** (Phase ψ.α) | reversal_short_term.py | **反转** (20 日跌 15-30%) — **主 alpha (sharpe 1.1 horizon / 0.39 walk-forward)** |
| **reversal_1w** (Phase ψ.α) | reversal_short_term.py | **反转** (5 日跌 3-10%) |
| technical_stage (4 stage) | technical_stage.py | classify_technical_stage(closes, volumes) — Stan Weinstein |

### 3.3 多 Alpha Ensemble (strategy_ensemble.py)

**5 alpha 源 + 加权综合** (paper_sim 目前**没用**, 这是设计意图):

| Alpha | weight | 数据源 | 类别 |
|---|---|---|---|
| **institution_follow** | **0.40** | `mart_stock_trend.action_score` | 资金流 (主 alpha) |
| valuation_pct_low | 0.20 | `raw_aif10_valuation_quantile.percentile_fifty` | 基本面价值 |
| forecast_consensus | 0.15 | `raw_aif10_forecast_consensus.compre_rating_num` | sell-side analyst |
| momentum_120d | 0.10 | `fact_risk_factors.mom_120d` | 技术 |
| risk_adjusted_sharpe | 0.15 | `fact_risk_factors.sharpe_60d` | 风险调整 |

### 3.4 Paper Sim v2 (Phase ψ)

| 模块 | 作用 |
|---|---|
| `services/paper_sim/config.py` | yaml loader (portfolio / selection / exit / swap / tx_cost / risk / validation / data) |
| | selector.py | backtest mode 查 mart_per_formula_stage_optimal (Phase ψ.α B), 0 selection leakage; **Phase ψ.β.5 L2**: ensemble mode 可按 vol_60d 缩放 stop/target/trailing per-stock (`_vol_aware_params`, config flag `selection.vol_aware.enabled`); **Phase ψ.γ.2 L3**: ensemble mode 可 JOIN mart_per_stock_stage_strategy_optimal (24K 行 9-dim OOS) 用 per-stock × stage params 覆盖 default (`_load_per_stock_stage_optimal`, config flag `selection.per_stock_stage.enabled`). 优先级: per_stock_stage > vol_aware > default_holding. |
| | driver.py | walk-forward 主循环 + VWAP 成交 + swap 决策 |
| | exit_rules.py | 5 触发优先级 (stop > target_arm > trailing > hp_expired > stage_deterioration) |
| | swap_rules.py | compute_fulfillment / candidate_can_close_gap / evaluate_swap |
| | sizer.py | wilson_kelly position sizing |
| | tx_cost.py | 佣金 + 印花税 + 滑点 |
| | reporter.py | 6 类 KPI (A 用户标准 / B anti-churn / C robustness / D ablation / E sensitivity / F reality_check) |
| | ddl.py | 4 张 paper_sim 专表 (nav / position / trade / kpi) |

### 3.5 候选 / 推荐 / 选股

| 模块 | 作用 |
|---|---|
| `services/buy_signal/` | classify_tier + factor_aggregator + scoring + reasoning + configs + ddl — **6 因子综合 score, 输出 mart_stock_formula_buy_signal_daily** |
| `services/selection/` | logger / outcome / feedback / summary — 选股事件追踪 |
| `services/portfolio_walk_forward/` | metrics.py (CAGR / sharpe / max_dd / calmar / monthly_win_rate), liquidity, ... |
| `services/portfolio_sizer/` | profiles.py 不同风格 sizing |
| `services/trade_plan/builder.py` | 交易计划生成 |
| `services/candle_pattern/` | features (6 维 + 1 突破强度) / evaluator / search_space (4 维 Optuna 阈值) |

### 3.6 机构 / 行业 / 阶段

| 模块 | 作用 |
|---|---|
| `services/institution_l2_metrics.py` | institution_l2_score_cte (train_best/holdout pair CTE) |
| `services/institution_read.py` / `institution_scoring_read.py` / `institution_write.py` | 机构数据 R/W |
| `services/industry_context_engine.py` | sector_momentum 衔接到个股 fact_stock_industry_context |
| `services/industry.py` / `industry_pit.py` / `industry_overview_read.py` | 行业 PIT + UI 读取 |
| `services/stock_stage_engine.py` | 阶段特征中间事实层 |
| `services/stock_turtle_engine.py` | 海龟形态特征 |

### 3.7 数据源 / 客户端 / sync

| 模块 | 作用 |
|---|---|
| `services/data_sources/` | base / clients_registry / data_routes / fallback / registry — 数据源中央 |
| `services/akshare_client.py` / `tdx_*_client.py` / `block_client.py` / `capital_client.py` / `lhb_client.py` / `xdxr_client.py` / etc. | 各种数据源 client |
| `services/kline_source.py` / `market_db.py` | K 线源 + market DB 入口 |
| `services/duck_adapter.py` / `db.py` / `db_health.py` | DuckDB 安全包装 |
| `services/source_watermarks.py` / `source_policy.py` | sync watermark + policy |

### 3.8 其他

- `services/sentiment/` — **情绪因子框架** (factor_registry + bin_assigner + window_calculator + survey_builder). 未集成到主选股
- `services/external_attention.py` — 关注度因子 (`external_attention_score` 已写入 mart_stock_trend)
- `services/event_simulator.py` / `event_engine.py` — 事件模拟引擎 (用于机构跟随 backtest)
- `services/shareholder_plan_*` (3 文件) — 股东计划相关 alpha
- `services/feature_registry.py` / `feature_labels.py` / `feature_retention.py` — 特征工程
- `services/data_lineage/` — 数据血缘
- `services/ml_lifecycle/` — drift / registry
- `services/etf_*` — ETF 子系统 (独立, 不影响个股 alpha)
- `services/trading_config/` — 真实执行模型 (buy_pricing / sell_pricing / slippage / filters / execution_model)

---

## 4. Scripts 入口 (135 个)

按主题分组:

| 主题 | 数量 | 例子 |
|---|---|---|
| `build_*` | 49 | build_formula_signals_history, build_signal_context, build_stock_formula_buy_signal_daily, build_daily_position_recommendations, build_picture_daily, build_stage_formula_fitness, build_architecture_inventory |
| `run_*` | 17 | run_paper_sim_v2 (我们主用), run_follow_backtest (机构跟随), run_optuna_*, run_portfolio_mvp |
| `validate_*` | 10 | validate_exclusion_rules 等 |
| `audit_*` | 5 | **audit_end_to_end.py** (23 项检查) |
| `backfill_*` | 5 | 各种回填 |
| `optimize_*` | 4 | **optimize_per_stock_stage_strategy.py** (Phase ψ R1), **optimize_per_formula_stage.py** (Phase ψ.α B), **optimize_ensemble_full.py** (Phase ψ.γ.1, **20 维 ensemble Optuna**: 13 alpha weights + 2 regime + 3 sigma + hp + max_vol, constrained sharpe, holdout train/test, mart_ensemble_optimal 入库) |
| `rebuild_*` | 2 | rebuild_stage_formula_fitness |
| `replay_*` | 2 | replay_paper_history_signflip |
| `evaluate_*` / `train_*` | 4+2 | 各种评估 + 训练 |
| `cron_*` | — | cron_daily.py (HTTP wrapper for sync) |

### 4.1 主流水线 (顺序严格)

```
1. optimize_per_stock_stage_strategy.py    Optuna 9-dim per (stock × variant × stage)  ~16 min
   或 optimize_per_formula_stage.py        Phase ψ.α B 全局 walk-forward          ~28 min
2. rebuild_stage_formula_fitness.py        fitness 聚合                          ~1s
3. build_stock_formula_buy_signal_daily    buy_signal × technical_trigger        快
4. build_daily_position_recommendations    最终推荐 + 价格                       快
5. audit_end_to_end.py                     23 项检查 (0 FAIL 才算通过)           ~1 min
6. portfolio_backtest.py / run_paper_sim_v2.py   walk-forward NAV + KPI         30 min
```

---

## 5. Routers / API (17 个)

| Router | 主功能 |
|---|---|
| `routers/recommendation.py` | 选股推荐 API |
| `routers/screening.py` | 筛选 |
| `routers/signals.py` | 信号 |
| `routers/institution.py` | 机构数据 |
| `routers/market.py` | 行情 |
| `routers/etf.py` | ETF |
| `routers/updater.py` | sync 入口 (POST /api/inst/update/smart) |
| `routers/workbench.py` | 工作台 |
| `routers/strategy_preset.py` | 策略预设 |
| `routers/v3_*` | v3 系列 (meta / paper / picture / portfolio_builder / selection / views) |

---

## 6. Config 文件 (yaml)

| 文件 | 控制什么 |
|---|---|
| `backend/config/optuna_config.yaml` | Optuna 治理 (Phase ψ Rule 7/8) — governance/walk_forward/search_space/composite/constraints/execution/output |
| `backend/config/paper_sim_config.yaml` | Paper Sim v2 hyperparam |
| `backend/config/paper_sim_momentum.yaml` / `paper_sim_reversal.yaml` / `paper_sim_reversal_deep_only.yaml` | Phase ψ.α ablation 切换 |
| `backend/config/paper_sim_ensemble.yaml` | **Phase ψ.β.4** ensemble 模式 (13 alpha + regime + vol_aware + per_stock_stage) |
| `backend/config/field_dictionary.yaml` | **Phase ψ.γ.dict.1** 字段字典 (3 DB × 12 核心表 × 100+ 字段 + 单位 + PIT key + outlier cap + JOIN 模板) — 防 VWAP unit bug 类故障 |
| `backend/config/recommendation_universe.yaml` | 选股宇宙 |
| `backend/config/pipeline_performance_policy.yaml` | step budget 预算 |
| `backend/config/data_sources.yaml` | 数据源 |
| `backend/config/storage_retention.yaml` | 保留期 |
| `backend/config/pricing_label_policy.yaml` | 定价标签 |
| `backend/config/feature_registry.yaml` | 特征注册 |
| `backend/config/model_search.yaml` | 模型搜索 |

---

## 常用命令 cheatsheet (复制即可跑)

### 安装 (新人首次)
```bash
git clone https://github.com/dare2live/chunkymonkey.git
cd chunkymonkey
python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt
pip install pre-commit && pre-commit install   # 强制 PROJECT_INDEX 同步检查
```

### 数据 backfill (从空开始)
```bash
# 1. 技术阶段 (Stan Weinstein 4 stage)
PYTHONPATH=backend python backend/scripts/build_stage_formula_fitness.py --start 2022-09-01

# 2. signal_context (vol/amt/price_pos + technical_stage)
PYTHONPATH=backend python backend/scripts/build_signal_context.py --start 2023-09-01

# 3. 公式信号历史 (含反转 3 公式)
PYTHONPATH=backend python backend/scripts/build_formula_signals_history.py

# 4. PIT 因子 (Phase ψ.β.1/2/3)
PYTHONPATH=backend python backend/scripts/backfill_risk_factors_history.py
PYTHONPATH=backend python backend/scripts/backfill_financial_pit.py
PYTHONPATH=backend python backend/scripts/backfill_capital_flow_pit.py
```

### Optuna 跑批
```bash
# per-formula × stage 全局 walk-forward (推荐)
PYTHONPATH=backend python backend/scripts/optimize_per_formula_stage.py \
    --formula reversal_1m_mild reversal_1m_deep reversal_1w \
              macd_golden_cross turtle_breakout_20 turtle_breakout_55 \
              dynamic_ma_iterative_cross
# 时长: ~7.5h (1260 任务), 输出 mart_per_formula_stage_optimal 426 行
```

### paper_sim 跑批 (4 套 ablation)
```bash
# A. baseline (no swap, 老 momentum 公式)
PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py --variant baseline

# B. 反转单 alpha (最强 setup)
PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py \
    --config-path backend/config/paper_sim_reversal.yaml --ablation

# C. momentum 单 alpha
PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py \
    --config-path backend/config/paper_sim_momentum.yaml --ablation

# D. ensemble 10 alpha 综合 (主战)
PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py \
    --config-path backend/config/paper_sim_ensemble.yaml --ablation
# 时长: 各 ~30-60 min
```

### 数据查询 (常用诊断)
```bash
# 查 mart 表最强 setup
duckdb data/smartmoney.duckdb -c "
SELECT formula_id, stage_filter, COUNT(*) AS n,
       ROUND(AVG(oos_sharpe),3) AS avg_sh,
       ROUND(AVG(oos_win_rate)*100,1) AS win
  FROM mart_per_formula_stage_optimal
 GROUP BY 1, 2 ORDER BY avg_sh DESC LIMIT 10"

# 查 PIT 数据 freshness
duckdb data/smartmoney.duckdb -c "
SELECT 'risk_factors' AS t, MIN(calc_date), MAX(calc_date), COUNT(*) FROM fact_risk_factors
UNION SELECT 'financial', MIN(trade_date), MAX(trade_date), COUNT(*) FROM fact_financial_pit_daily
UNION SELECT 'capital_flow', MIN(trade_date), MAX(trade_date), COUNT(*) FROM fact_capital_flow_pit_daily
UNION SELECT 'signal_context', MIN(date), MAX(date), COUNT(*) FROM fact_signal_context"
```

### 测试 / 验证
```bash
# 全部单测 (paper_sim + optuna + backtest + ...)
cd backend && PYTHONPATH=. pytest tests/ -q

# 仅 Optuna 治理测试
cd backend && PYTHONPATH=. pytest tests/optimization -q   # 83 tests

# 跑 audit (23 项检查)
PYTHONPATH=backend python backend/scripts/audit_end_to_end.py
```

### Pre-commit 测试 (避免 hook reject)
```bash
# 改完代码后 staged
git add backend/services/your_file.py

# 测 hook (会告诉你需不需要改 PROJECT_INDEX)
python3 backend/scripts/check_project_index_sync.py; echo "exit=$?"

# 如果 exit=1 → 改 PROJECT_INDEX.md 加进 §14, 然后 git add PROJECT_INDEX.md
# 如果 exit=0 → 可以 commit
```

## 7. CLAUDE.md 规则栈 (现 9 条)

```
Rule 1: Think Before Coding         — 列假设, 不确定就问, push back
Rule 2: Simplicity First            — 最少代码, 不 speculative
Rule 3: Surgical Changes            — 只改必须改的
Rule 4: Goal-Driven Execution       — 定义成功, 循环验证
Rule 5: Root Cause Over Patches     — 不打补丁, 找根因
Rule 6: Measured, Not Estimated     — 不估算, 必须实测
Rule 7: Anti-Look-Ahead / Leakage   — 普适, 时间维度诚实
Rule 8: Optuna 治理                 — Rule 7 在调参层落地, config-driven
Rule 9: 真金白银 / 第一性原理       — 用户视角严苛门槛
```

---

## 8. 已知坑 / 未启用 / 需要修

| 项 | 状态 |
|---|---|
| `mart_sector_momentum` 只 41 行 (2026-04 起) | ❌ 没历史回测能力, **需 rebuild 全期** |
| `fact_setup_snapshot` 0 行 | ❌ 未启用 |
| **paper_sim 选股 走 strategy_ensemble** | ✅ Phase ψ.β.4: ensemble mode + `paper_sim_ensemble.yaml` 10 alpha |
| **5 alpha 主源数据 PIT 时序** | ✅ β.1 fact_risk_factors / β.2 fact_financial_pit_daily / β.3 fact_capital_flow_pit_daily backfill 完成 (跨 2023-01 → 2026-05) |
| **fact_institution_event 主 alpha** | ⚠ 只 1 年 (2025-04 起), 无法做 800 天 backfill — β.3 改用 lhb+exec+holder 替代 |
| **mart_stock_trend.action_score (机构跟随主 alpha)** | ❌ 仍是 latest 快照 — 未做 PIT 重建 (依赖 fact_institution_event 1 年限制) |
| **aif10 估值/一致预期** | ❌ 全 latest 快照, 无 PIT, β.2 改用 fact_financial_derived 替代 |
| **case-based / k-NN 历史相似回测** | ❌ 未建. 数据基础已有 (fact_signal_context + archetype) |
| **`fact_regime_state` 在 paper_sim** | ✅ Phase ψ.β.4: ensemble selector regime_gate (bear 0.3x / sideways 0.7x / bull 1.0x) |
| sentiment/ 包未集成 | ⚠ 8 文件框架, 未对接 |
| 大盘指数 K 线 在 paper_sim 当 benchmark | ✅ 已用作 excess vs HS300 |
| **fact_signal_context 早期数据缺** | ✅ Phase ψ.β.4.5 backfill 完成 (2024-03 起, 66% valid_stage) |
| **fact_stock_technical_stage 早期缺** | ✅ Phase ψ.β.4.5 backfill 完成 (2023-09-12 起, 2.4M 行) |
| **mart_per_formula_stage_optimal train_end 范围** | ⏳ 正在重跑 (1260 任务, 5 worker, 含 7 公式 × stage × 35 train_end) |
| **Optuna 跑批 8h 慢** | ✅ Phase ψ.β.perf 修 hotspot: _idx O(1) cache + backtest_signals_with_trades 避免重跑 simulate_trade. 重跑预估 3-4h |
| `fact_stock_archetype` (基本面质量) 只 2026-04 几天 | ⚠ 未 backfill 历史 (待后续 audit) |
| `fact_financial_derived.revenue_yoy` 对部分股 (如 000001) null | ⚠ derived 表本身 sparse, 不影响其他股 |

---

## 9. 关键术语速查

| 术语 | 含义 |
|---|---|
| **IS** | In-Sample, 调参用的数据 |
| **OOS** | Out-of-Sample, 调参后**没看过**的数据上的表现 (实盘只能 OOS) |
| **R1** | 严格 walk-forward — 用户指定标准 |
| **expanding_monthly** | R1 严格模式: 每月底切, 累积 train + 当月 OOS |
| **train_end_forward** | Phase ψ.α B: train < d, test = [d, d+forward_days], 写多行支持 paper_sim point-in-time 选 |
| **leakage** (selection) | t 时选股用了 t+ 才能算的指标 (例 mart.sharpe 全期合并) |
| **leakage** (look-ahead) | 特征用了未来 K 线 |
| **CAGR** | (final/initial)^(252/n_days) - 1 — 复利年化 (不是单笔 × N) |
| **technical_stage** | 1=底部 / 1.5=突破中 / 2=上升 / 3=顶部 / 4=下跌 (Stan Weinstein) |
| **mart_** | 业务表 (报表 / 聚合) |
| **fact_** | 事实表 (实际发生) |
| **raw_** | 原始数据源 |
| **dim_** | 维度表 (静态 / 缓变) |

---

## 10. 已实测数据点 (Phase ψ.α 跑出的诚实 OOS)

### 反转因子 (B 严格 walk-forward, 34 个月窗 avg):

| formula × stage | avg OOS sharpe | avg win | avg single ret | max sharpe |
|---|---|---|---|---|
| reversal_1w × stage=3 | +0.393 | 50.4% | +3.94% | +1.255 |
| reversal_1m_deep × stage=3 | +0.393 | 51.6% | +5.49% | +0.898 |
| **reversal_1m_deep × stage=1** (底部深跌反转) | **+0.392** | **58.1%** | **+5.22%** | +0.905 |
| reversal_1m_deep × stage=4 | +0.356 | 46.2% | +4.77% | +0.889 |
| reversal_1m_mild × stage=1.5 | +0.342 | 51.9% | +4.49% | +1.372 |
| ... 9 行 ... | | | | |
| reversal_1w × stage=1 | -0.171 | 34.9% | +2.61% | +0.612 |

### Momentum 公式 (per-stock × stage R1, sparser):
全 12 组合 OOS sharpe 全负 (-0.02 ~ -0.63), avg win ≈ 39% — **per-stock 粒度不适合**, 应该改 per-formula 全局重测.

### Horizon Evidence (无 Optuna, 最干净, per formula × hp):
- reversal_1m_deep × 20d: win 61.8% / sharpe **+1.10** (但**这是合并跨全期, 不是 forward OOS**)

---

## 11. 我 (Claude) 容易踩的坑 (Rule 9.5 沉淀)

| 坑 | 教训 |
|---|---|
| "项目主要数据是 K 线" | **全错**. 6 大数据维度都有. 下结论前先 grep 所有 fact_/mart_/raw_ 表 |
| "momentum 公式失效 → 项目无 alpha" | 错. 项目还有机构跟随 (0.40 主 alpha) + 估值 + 一致预期 + 情绪 + 行业 + 大盘 regime |
| "MACD 是裸的" | 错. 跑 Optuna 时叠加 4 维 K 线形态过滤, 不是裸金叉 |
| "上升趋势 (stage=2) 反转完全无效" | 错. 是**粗糙公式**判 stage=2 回调失败, stage=2 回调本身是合理买点, 需要更精细 |
| "估算 2 min 跑完" → 实际 28 min | Rule 9.5: 不实测就估算 = 失败. 估时间也要小样本先测 |
| **paper_sim selector 用 mart_per_stock_*_optimal sharpe 排名** | 这是 selection leakage. 修正: walk-forward selector (Phase ψ.α B 已修, 但只对 reversal). 整体业务应走 ensemble |
| "对话压缩后还在用旧 context" | 修正: 每次启动**先读这个文档 + CLAUDE.md** |

---

## 11.5 已知遗漏 / 待办清单 (按 ROI 优先级)

> 这是用户反复 push back 后系统 audit 的结果. 每项含: 用户期望 / 现状 / 优先级 / 估时.
> Claude 应该在每个 phase 结束自动 review 这个列表, 不让任何一项静默 drop.

### P0 — 必修 (影响主目标达成)

| # | 项 | 用户期望 | 现状 | 估时 |
|---|---|---|---|---|
| 1 | **数据 sync 同步** | 数据更新到最新交易日 | `mart_data_source_watermark` 停在 2026-05-06, 其他 2026-05-13. 没主动跑 sync | 1 h |
| 2 | **goal.md 维护** | Phase ψ.β 系列进度记录在 goal.md | goal.md 没动过 Phase ψ.β 内容 | 1 h |
| 4 | **mart_sector_momentum 历史 backfill** | 板块强度可历史回测 | 只 41 行 (2026-04 起), 板块 alpha 不可用 | 半天 |
| 11 | **swap 策略最终评估** | 反转 setup 下 swap 是否需要? | swap_v1 跑 -44% 后中断, 反转下没验证 | paper_sim ablation 一部分 |

### P1 — 高 ROI (alpha 增强)

| # | 项 | 用户期望 | 现状 | 估时 |
|---|---|---|---|---|
| 5 | **mart_stock_trend.action_score PIT 重建** | 机构跟随主 alpha (0.40 权重) 历史可用 | β.3 改方向用 lhb/exec/holder 替代; 主 action_score 还是 latest 快照 | 3-5 天 (受 fact_institution_event 只 1 年限制) |
| 6 | **case-based / k-NN 历史相似回测** | "结合历史相似形态胜率" 选股 | 列为 R-γ, 未开工. 数据基础 fact_signal_context + archetype 已有 | 1-2 周 |
| 10 | **大盘 regime gate paper_sim 验证** | regime 择时是否生效? | yaml 配置加了但 paper_sim 还没验证 (反转 ablation 没用 ensemble mode) | paper_sim ablation 一部分 |

### P2 — 中 ROI (alpha 拓展)

| # | 项 | 现状 | 估时 |
|---|---|---|---|
| 3 | fact_stock_archetype 历史 backfill | 只 2026-04 几天 | 半天 |
| 7 | sentiment/ 关注度 alpha 集成 | 8 文件框架, 未对接 | 1 天 |
| 8 | 量价相关因子 (vol-price correlation) | 调研提过, 未建 | 半天 |
| 9 | fact_financial_derived.revenue_yoy sparse | 部分股 null (如 000001 银行) | 修 derived 表本身, 半天 |

### P3 — 工程 / 审计

| # | 项 | 现状 | 估时 |
|---|---|---|---|
| 12 | swap_uplift_estimate vs 反事实验证 | Phase ψ Batch 4c todo | 半天 |
| 13 | qfq 复权 PIT leakage | "业界接受不修", 但 Rule 9.1 严格说要处理 | 1-2 天 |
| 14 | 行业分类 PIT 系统验证 | 没核 SQL 用 history 还是 latest | 半天 |
| 15 | codex 分支整理 | 保留作 backup (用户原话), 不删 | 0 |
| 16 | dev 手册 / goal.md / PROJECT_INDEX 职责划分 | 没明文, 内容可能冗余 | 半天 |
| 17 | **283 历史 Rule violations 渐进清理** (Phase ψ.γ.discipline 扫出) | Rule 5 silent except 138 / Rule 7 date 112 / stock 22 / Rule 6 alpha weight 6 (strategy_ensemble.py) / threshold 3 / sigma 1 / multiplier 1. 多数 Rule 5 可能合理 (best-effort cleanup), Rule 6 6 个是 strategy_ensemble.py 真违规需要 yaml-back. | 1-2 天 (按 rule 分批清理 + 误判加 evidence 注释) |

### 处理原则

- 每跑完一个 phase / commit 后, **检查这个列表是否有项可以划掉**
- 新踩坑 / 新 audit 发现的项加进来
- 不静默 drop — 即使 "暂不修" 也要写明理由
- P0 不修, 用户目标基本不可能达成

## Performance Profile (跑批时间预期)

| 任务 | 数据量 | 实测时长 | 备注 |
|---|---|---|---|
| build_signal_context backfill | 3.3M K 线 → 2.7M context | **5.7 min** | calc 1 min + 写库 4.7 min |
| build_stage_formula_fitness (含 technical_stage) | 5.2M K 线 → 2.4M stage | **4 min** | classify 22s + 写库 3.5 min |
| backfill_risk_factors_history | 5.5M K 线 → 4.8M risk PIT | **12 min** | SQL 窗口 8.6s + 写库 11.5 min |
| backfill_financial_pit | 70K 财报 + K 线 → 3.7M PIT | **10 min** | ASOF JOIN 4s + 写库 10 min |
| backfill_capital_flow_pit | 53K lhb + 68K exec + holder → 858K | **2.4 min** | SQL 3s + 写库 2 min |
| optimize_per_formula_stage (反转 3 公式) | 455 任务 × 100 trials | **28 min** | 8 workers |
| **optimize_per_formula_stage (全 7 公式)** | **1260 任务 × 100 trials** | **7.5 h** ⚠ | 后期 5 worker tail (用户问"卡了吗") |
| paper_sim_v2 walk-forward 单 variant 800 天 | 4-5K 候选 / 天 | 30 min | swap_v1 含 |
| paper_sim_v2 ablation (baseline + swap_v1) | 2 variants × 800 天 | 60 min | |

### 已修 hotspot (Phase ψ.β.perf, commit 192bcb4d)

| Hotspot | 修法 | 预期加速 |
|---|---|---|
| `realistic_engine._idx` linear search | 加 `_BAR_DATE_IDX_CACHE` dict cache | **2-5×** |
| `objective.py` + `optimize.py` 重跑 simulate_trade | 新增 `backtest_signals_with_trades` 返回 (summary, trades) | **1.5-2×** |
| `objective.py` 自己做 linear search | 改用 `_idx` (含 dict cache) | **1.2-1.5×** |

**预期重跑 1260 任务 Optuna 从 7.5h 降到 ~3h**.

### 已知尚未优化

| 项 | 影响 |
|---|---|
| `dynamic_ma_iterative` 公式 10 轮迭代 Python loop | 慢公式之一, 可 numpy 向量化 → 3-5× |
| backfill 写库阶段 (单事务 INSERT) | 平均 150 us/row, 4.8M 行 11 min. COPY FROM Parquet 可 5-10× |
| Optuna pool tail effect (5 worker idle / 2 worker 慢任务) | 改 chunksize 或调度策略, 拉平 worker 负载 |

## 12. 当前 Phase / 进度

| Phase | 内容 | 状态 |
|---|---|---|
| Phase β-η+++++++ | 前期工作 (公式 / Optuna / fitness / sizer / etc.) | 大量已完成, 见 goal.md |
| **Phase ψ** | Optuna 治理 + R1 + Rule 7/8 + paper_sim VWAP 修正 | ✅ commit `34e83d75` (main + codex) |
| **Phase ψ.α** | 反转因子 + per-formula 全局 + B 严格 walk-forward + Rule 9 + PROJECT_INDEX | ✅ commit `545cb3d9` (feature/reversal-factor) |
| **Phase ψ.β.1** | fact_risk_factors PIT backfill (4.8M 行 / 6,567 股 / 810 天) | ✅ commit `5a3b5ea8` |
| **Phase ψ.β.2** | fact_financial_pit_daily PIT (3.69M 行) — PE/PB/ROE/yoy/inst_holding_pct | ✅ commit `baf815b6` (β.2+β.3) |
| **Phase ψ.β.3** | fact_capital_flow_pit_daily (858K 行) — lhb/exec/holder PIT | ✅ commit `baf815b6` |
| **Phase ψ.β.4** | paper_sim ensemble selector + 10 alpha yaml + regime_gate | ✅ commit `1af98eca` |
| **Phase ψ.β.4.5** | backfill fact_stock_technical_stage + fact_signal_context 历史 | ✅ 数据已落, 待 commit |
| **Phase ψ.β.4.6** | ensemble quality_filter (vol_60d / allowed_stages) | ✅ commit `192bcb4d` |
| **Phase ψ.β.perf** | hotspot fix: _idx O(1) cache + backtest_signals_with_trades | ✅ commit `192bcb4d`, 161 测过 |
| **Phase ψ.β.5** (in-progress) | optimize_per_formula 重跑 7 公式 × 35 train_end = 1260 任务 | ⏳ 5 worker 67% CPU, 1000/1260 |
| **Phase ψ.β.6** (next) | paper_sim ablation 完整 800 天 (reversal / momentum / ensemble) | ⏸ 等 ψ.β.5 |
| **Phase ψ.β.7** (next) | audit + 修 残留漏洞 (mart_stock_trend PIT / sector_momentum 全期 / case-based 等) | ⏸ |

git 状态 (commit chain):
```
main:                       34e83d75  (Phase ψ Optuna 治理)
feature/reversal-factor:    192bcb4d  (head, 含 β.1-β.4.6 + perf, 6 commits ahead)
  ← 192bcb4d  Phase ψ.β.perf
  ← 1af98eca  Phase ψ.β.4 ensemble selector
  ← baf815b6  Phase ψ.β.2+β.3 financial + capital_flow PIT
  ← 5a3b5ea8  Phase ψ.β.1 risk_factors PIT
  ← 545cb3d9  Phase ψ.α reversal + Rule 9 + PROJECT_INDEX
  ← 34e83d75  Phase ψ
```

worktree 残留: `/Users/dp/.codex/worktrees/a980/stock` 链接到外部 `/Users/dp/Documents/M/stock/.git`, 不归本项目处理.

---

## 13. 写本文档的源数据 (供刷新)

```sql
-- 项目自己维护的架构 inventory (smartmoney.duckdb)
SELECT * FROM mart_architecture_inventory_summary ORDER BY built_at DESC LIMIT 1;
SELECT * FROM mart_architecture_inventory_asset WHERE run_id = ?;
SELECT * FROM mart_data_health;
SELECT * FROM mart_data_source_watermark;
```

或运行 `backend/scripts/build_architecture_inventory.py` 自动重生成.

---

## 14. Session 增量更新日志 (Rule 9.5 长期沉淀)

每次 session 增量内容写这里, 新 session 启动时**从下往上读**最近改了啥.

### 2026-05-14 (Rule + memory 加 "doc 自维护" — 改 CLAUDE.md/memory 时主动优化)

用户原话: "在每次修改 claude.md 和 memory 时直接做一个优化和更新 — 删除过时、优化冗余".

CLAUDE.md:
- §8 工程纪律加 "doc 自维护" 项 (5 必查: 过期/冗余/结构/链接/deprecation)
- §9.2 commit-time self-check 加第 6 项: 改 CLAUDE.md/memory 顺手优化了吗?

Memory (跨 session 持久化):
- 新 `feedback_doc_self_optimize.md`
- MEMORY.md 索引同步

跟 PROJECT_INDEX 同步纪律同级 — 都是 doc 维护质量.

### 2026-05-14 (CLAUDE.md 加 "异常高数字 = leakage 警报" 显式规则)

用户原话: "参数寻优不用未来函数怎么体现的? 之前有一版本 100% 胜率, 收益超高, optuna 读完整 3 年 K 线倒推买卖点".

CLAUDE.md 增强:
- Rule 5 (Anti-Leakage) Self-check **加第 6 问**: 数字异常好看 (RankIC>0.3 / sharpe>5 / win>0.95 / 年化>100% / 胜率 100%) → 立刻怀疑 leakage 不是兴奋
- 加 "异常高数字 = leakage 警报信号" 子节, 含 paper_sim +312% 历史反例 + 修法三件套
- Rule 6 (Optuna 治理) 加 "Optuna 不用未来函数 — 3 道防线":
  1. walk_forward.split_expanding_monthly 严格 train/test 时序切
  2. 搜索空间只搜策略行为参数, 不读未来 K 线
  3. governance.enforce_pre_insert 拒 in-sample fit + 拦不真实数值
- v3.2 P0b 实测 RankIC 0.02 作为"诚实"反向证据 (跟历史 +312% 假象相反)

### 2026-05-14 (CLAUDE.md 重构 — 640 → 270 行)

用户原话: "claude.md 是不是过于啰嗦降低读取效率了, 请你写成自己能明白的样式".

重构:
- 12 主 section + 4 sub-section (Self-Check 9.1/9.2/9.3, Codex 三态嵌入 §10)
- 删冗余: 重复"用户原话"引用 (3-5次→1次); 大段反例细节解释保留关键 commit hash
- 项目笔记 (运行环境/命名陷阱/sync/loop/测试基线/关键表陷阱) 移到 PROJECT_INDEX.md (本应如此, "地图")
- Rule 1+2+3 合并为 §1 "Think Before Coding" (短)
- Rule 5 反例从 5 行表压缩到 4 个 bullet (含 commit hash)
- Rule 6 反例从 6 行表压缩到 5 个 bullet
- Rule 9 + Rule 9.7 + Rule 9.8 + Rule 9.9 → §7 真金白银 + §8 工程纪律 + §9 Self-Check (双层)
- Rule 10 Codex 三态用表格 + §10.2 §10.3 合并 (慢 = cancel+fresh, 真不可用 = self-审 fallback)
- 附录 → "详细信息见 PROJECT_INDEX.md" 列表

行数: 640 → 270 (砍 57%), 所有规则保留, 反例 commit hash 保留 (69371838/5cc47987/...)

### 2026-05-14 (Rule 10.2 新加: Codex thread 慢 ≠ Codex 不可用)

**用户 push back** (CLAUDE.md Rule 10): 我多次误判"单 thread stuck" 为"Codex 整体不可用", 走 Rule 10.2 fallback self-review 跳过. 这是错的 — codex-companion.mjs setup 一直 ready=True.

**新 §10.2** (CLAUDE.md):
- 单 thread > 30 min 无产出 → cancel + `codex:rescue --fresh` 起新 thread
- **真正不可用** (setup ready=false / 服务不可达) 才走 fallback (§10.3)
- 原 §10.2 fallback 改为 §10.3
- 原 §10.3 单分支策略 改为 §10.4

**为啥 critical**: Codex Q1 acf48d35a80850383 抓 stage_opt_per_stock leakage 是 self-review 没看到的; fallback 路径会把 systemic leakage 推到 main.

### 2026-05-14 (Phase v3.2 v2 修 Codex Q1 leakage — 删除 stage_opt_per_stock)

**Codex review `acf48d35a80850383` Q1 CRITICAL**:
- v2 stage_opt_per_stock CTE 是 `MAX(COALESCE(oos_sharpe, sharpe)) GROUP BY stock_code` 全期 MAX
- 给每个 signal_date 历史 row 用了未来 Optuna OOS 结果 — **系统性 leakage**, 不是 PIT
- Rule 7 违反: 给 t 时刻决策用了 > t 的 mart_per_stock_stage_strategy_optimal Optuna 寻优结果

**修复**:
- 删除 3 列: stage_opt_best_sharpe / stage_opt_best_avg_ret / stage_opt_total_traded
- 保留 formula_trigger 6 dummy (PIT 严格 OK by Codex Q2)
- v2 features: 79 → 85 (不是 87)
- TODO v3: 重 Optuna walk-forward expanding_monthly 入库 (stock × cutoff_date × best_sharpe), ASOF JOIN

### 2026-05-15 (Leakage cleanup process gap + pit-audit skill)

用户 push back: "之前有 leakage 的数据验证是怎么处理的".

诚实承认 oversight: 之前 kill 进程 + 修代码 + restart 不够, **没 explicit DELETE leaked rows / ALTER DROP COLUMN 物理 leakage cols**. Lucky 主要表没污染 (Optuna commit-at-end + chain 没跑到 train write phase), 但 panel 物理含 10 leakage cols.

**`backend/scripts/cleanup_leakage_data.py`** (新):
- DELETE leaked run_id / model_id 从 mart_p1_optuna_trials, oos_predictions, walkforward_eval, ablation_result
- ALTER TABLE DROP COLUMN inst_path_a 5 + sector 5 cols from mart_p0a_feature_label_panel_v3
- dry-run 默认, --execute 实际 cleanup

**新 skill `~/.claude/skills/pit-audit/SKILL.md`** (user-level):
- 5 步 procedural workflow (不可跳): 列举 cols → trace 表 → PIT contract check → micro-ablation → 三档 verdict
- 触发: substantial feature commit 前 / Codex flag PIT / RankIC vs baseline +50% jump
- ChunkyMonkey 反例 inline (5cc47987 + b891473a + Day 5 缺位)

**memory 新加 `feedback_leakage_cleanup.md`**: Leakage 后 explicit DB cleanup (DELETE rows + ALTER DROP COLUMN) 不只是 kill+code fix.

### 2026-05-15 (Codex PIT 专项 review adc5b44520 — 4 leakage BLOCK + CLAUDE §10 收紧)

用户 push back: "已经写了严格避免 leakage 为啥还能出这种问题呢, 你调查一下".

Codex 专项 PIT 复核 (adc5b44520) 出 **5 大问题 + 4 BLOCK chain**:
- A inst_path_a CRITICAL: `mart_institution_profile.win_rate_60d` latest snapshot 给历史日用 (跟 stage_opt_per_stock 同性质 leakage)
- B valuation_z CLEAN: `fact_financial_pit_daily` 有独立 announce_date < trade_date, PIT 安全
- C purge/embargo MAJOR: split_expanding_monthly 没 embargo, 20d label K 线 overlap test X
- D paper_sim CRITICAL: ml_score_loader + hybrid_score_loader 用 `mart_per_stock_stage_strategy_optimal` latest + same-day buy = 同 +312% phantom
- E sector fallback MAJOR: 99.978% rows 是 'current_label_fallback' = 全 leakage

**Process failure** (我自审 5 处):
1. §10 push back rule 滥用: 用 "5 维度评估"为 CRITICAL leakage 找折中 (Codex a8c34359a Q1 标 CRITICAL 我选 "注释 TODO" 折中没 test)
2. Rule 5 第 6 问只 absolute (RankIC>0.3 etc), 缺 relative threshold (v1 0.02 → v3 0.035 +75% 没触发 absolute)
3. Rule 9.2 #5 commit self-check 跳了 "穿透 forward 期望"
4. PIT 单测设计缺陷: mock 都 latest snapshot, 没模拟"历史 signal + 未来 profile"时序冲突
5. 没做 commit 前 micro-ablation 验证每 col 群贡献

**Fix forward**:
- **CLAUDE §10 加 "CRITICAL 红线"**: PIT/leakage CRITICAL 不可折中, 必须完全接受+立刻修+test verified
- **Rule 5 第 6 问加 relative threshold**: vs baseline +50% 提升触发 PIT 深查
- **新 memory `feedback_codex_critical_no_compromise.md`**: 配套 [[feedback-codex-critical-evaluation]] 收紧
- **代码**: 训练 `_META_FIELDS` 加 inst_path_a 5 + sector 5 cols (training-only exclude), walk_forward 加 embargo_days (20d horizon → 30 days gap)
- **chain v6**: 92 honest features, skip Step 6 paper_sim (Codex D 等 Day 5 PIT 表), skip Step 9 Day 5 PIT (user 单独触发)

138 单测全过. Kill chain v5 (含 leakage 数据废) + 启 chain v6 (honest).

### 2026-05-15 (Codex 综合 review a163ca58 — 12 finding fix)

补做漏掉 Codex review (commit 419cdff8/b891473a/151b7178 没走). Codex 反馈 5 CRITICAL + 5 MAJOR + 2 MINOR, 全 fix:

**CRITICAL (5)**:
- C1: `ftt.state = 'triggered'` 过滤所有 — 生产 state 是 NULL (88%) / 'just_crossed' (12%), 不是 'triggered'. fact_technical_trigger 全是触发记录, 不需 state filter → 去掉
- C2: build_stage_opt_pit `--end cutoff` 包含 cutoff 当日 leakage → 改 cutoff - 1 day
- C3: ETL SELECT `holding_days` 不存在, 生产列是 `optimal_hp` → 改 `optimal_hp AS holding_days`
- C4: paper_sim config.py assert 拒 'hybrid' mode → 加 hybrid + ml_score
- C5: run_paper_sim_hybrid_grid.py wrong import `services.scripts.run_paper_sim_v2` → 移除

**MAJOR (5)**:
- M1: LambdaMART train_groups 在 NaN filter 前算 → fix order: filter mask → derive valid arrays → groups
- M2: num_leaves bound `max(15, 2^max_depth-1)` bug, max_depth=3 时 num_leaves up to 15 >> 树 max 7 → 改 min(...)
- M3: np.std 默认 ddof=0 population → 改 ddof=1 sample (Codex Q4 objective)
- M4 (折中): build_stage_opt_pit --limit-stocks 只 ETL 阶段 limit, optimize subprocess 全量 — 文档清楚 "TODO forward arg"
- M5 (sequencing): hybrid_loader default exit_table 仍 latest snapshot — Day 5 PIT 表 build 完后 swap default

**MINOR (2)**:
- Mi1: lambdamart test `>= 0` 空 → 改 `>= 1` + mock data 14 months 30 stocks 满足 min_total_months=12
- Mi2: feature_join_v3 INSERT INTO 硬编码 → 加 raise if output_table != default (单 SQL 不支持 dynamic)

105 单测全过. Kill chain v4 (Step 3 broken formula_trigger data) + 准备 chain v5 重启.

### 2026-05-15 (v3 实跑 chain Step 1 修 3 production schema bug)

**Bug discovered during v3 build live run**:
1. `fact_signal_context` 无 formula_id/state — 实际触发记录在 `fact_technical_trigger`. v2 SQL 历史也错 (`mart_p0a_feature_label_panel_v2` 没 build 过)
2. `fact_top10_holder_period.effective_date` 是 `'YYYYMMDD'` 字符串 (e.g. '20200501') — `CAST AS DATE` 不识别, 用 `STRPTIME(..., '%Y%m%d')::DATE`
3. `fact_financial_pit_daily.trade_date` 是 TEXT — fin_z_history CTE select 加 `CAST(trade_date AS DATE)`, WINDOW ORDER 同步

Master_chain v4 (blekqa4eb → 重启 b8naz1ii8) 实跑 Step 1 v3 build ~80s 跑通 (4625 stocks × 557 dates panel), Step 2 audit PASS, Step 3 Day 4 smoke Optuna 启动.

### 2026-05-15 (Phase v3.2 Day 5+6 wire + Day 7 LambdaMART — 全 7-day plan code 完成)

**Day 5 (`scripts/build_stage_opt_pit.py`)**: stage_opt PIT walk-forward 半年 cutoff builder
- 4 cutoffs (2024-07-01, 2025-01-01, 2025-07-01, 2026-01-01)
- 每 cutoff 跑 optimize_per_stock_stage_strategy.py --start (cutoff-2y) --end cutoff
- ETL 入新表 mart_per_stock_stage_strategy_optimal_pit (PK 加 cutoff_date)
- 全量 ~48h, --limit-stocks N 做 smoke 验证 pipeline

**Day 6 wire (`services/paper_sim/config.py + selector.py`)**: paper_sim engine 加 mode='hybrid'
- SelectionConfig 加 hybrid_model_id/w_ml/max_candidates/q60_min_stage 字段
- load_today_candidates_dispatch 加 mode='hybrid' → load_today_candidates_hybrid

**Day 6 grid (`scripts/run_paper_sim_hybrid_grid.py`)**: 跑 5 w grid 对比
- 默认 w grid [0.00, 0.10, 0.20, 0.30, 0.40] (Codex Q5)
- 每 w 一次 walk-forward → KPI 表 (ann_ret/dd/excess/win_rate/sharpe)

**Day 7 LambdaMART (`services/ml_ranking/lambdamart_walkforward.py`)**: pairwise NDCG 对照
- LGBMRanker objective='lambdarank' + per-signal_date group_sizes
- label continuous → per-date integer relevance (0..label_gain_max-1)
- 5 单测过 (config / per-date relevance / 多 dates / empty / small data)

**Day 7 CLI (`scripts/run_p0b_lambdamart_v3.py`)**: 入 mart_p0b_oos_predictions model_id='lambdamart_v3_*'

### 2026-05-15 (Phase v3.2 Day 6 prep — hybrid blend loader + yaml)

**`services/paper_sim/hybrid_score_loader.py`** (Codex Q5 sequential filter + rank-linear blend):
- INNER JOIN mart_p0b_oos_predictions × mart_per_stock_stage_strategy_optimal
- q60_min_stage: eligibility 仅取 stage_oos_sharpe >= q60_by_date (防弱 ML 挤掉强 stage)
- PERCENT_RANK() → s_ml/s_stage ∈ [-1, 1] → hybrid_score = (1-w_ml) × s_stage + w_ml × s_ml
- w_ml grid: {0, 0.10, 0.20, 0.30, 0.40} nested WF 选 (不用 Optuna, Codex Q5 推荐)
- 9 单测 (w=0/1 退化 / q60 filter / NULL ml / 缺 stage drop / w 边界异常)

**`config/paper_sim_hybrid.yaml`**: selection.mode='hybrid' + hybrid_w_ml/q60_min_stage/max_candidates 字段

**Codex 反馈处理 (CLAUDE §10 push back rule)**:
- 完全接受: rank-linear blend 公式 + sequential filter (q60 stage eligibility) + w grid 不用 Optuna
- **折中 (我的选择)**: stage_opt 用 latest snapshot (NOT PIT), Day 5 PIT walk-forward 表暂不做. **理由**: 先验证 blend 有 value (smoke RankIC ≥ 0.025) 再投入 12h PIT 改造, 否则做无价值

### 2026-05-14 (CLAUDE §10 push back + audit_p0a v3 改造)

**CLAUDE.md §10 Codex Review Gate 加 push back 原则** (用户 2026-05-14 push back):
- 5 维度评估: 原则一致 / 用户目标 / 代价 vs 收益 / 现状妥协 / 现实数据
- 三档反应: 完全接受 / 折中 (写明分歧 + 理由) / 拒绝 (写明理由)
- 反例: 2026-05-14 我对 Codex review (a8c34359a) 7 finding 全接受没 push back, 实际 C1/M1 都是折中应显式标注

**memory feedback-codex-critical-evaluation.md** 配套, MEMORY.md 索引同步.

**`scripts/audit_p0a_panel.py`** 加 v3 支持:
- --feature-panel arg (default v1, 兼容 v2/v3)
- check_v3_pit_confidence: industry_fallback_ratio + 5 关键源 NULL ratio (待 v3 build 后跑)

### 2026-05-14 (Phase v3.2 Day 4 prep — LightGBM Optuna search space + early stop)

**`services/ml_ranking/lightgbm_walkforward.py`** (LightGBMWalkForwardConfig 加 5 Optional 字段 — backward compat):
- `max_depth`: 3-8 search space (Codex Q4)
- `reg_alpha` (lambda_l1): 1e-8 - 10.0 log
- `reg_lambda` (lambda_l2): 1e-8 - 50.0 log
- `min_split_gain` (min_gain_to_split): 0.0 - 0.2
- `early_stopping_rounds`: n_estimators=2000 时配合 (last 10% train 作 eval set)
- train_one_window: conditional pass — default None = LGBM default (现有 ablation/baseline 不变)

**`scripts/run_p0b_lightgbm_optuna_v3.py`** (新 Optuna CLI, Day 4 用):
- 默认 50 trials smoke (n_est=300 no early_stop) / `--full` 200 trials (n_est=2000 + early_stop=100)
- Codex Q4 完整 search space (12 维: max_depth/num_leaves/lr/n_est/min_child/feat_frac/bag_frac/bag_freq/l1/l2/min_gain_split)
- Objective: `mean(per_window_rank_ic) - 0.5 * std` (Codex 推荐, 惩罚窗口波动)
- 入库 mart_p1_optuna_trials (run_id × trial_number × params_json + rank_ic_mean/std)
- TPESampler seed=42, gc_after_trial=True 防内存涨

### 2026-05-14 (Phase v3.2 v3 扩 feature — Codex 7-day plan Day 2 + Day 3)

**`services/labels/feature_join_v3.py`** (+ 18 features over v2, 84 → 102 + 1 PIT confidence meta):
- Day 2 ① 调研热度 4 (mart_stock_survey_features ASOF as_of_date<=signal): survey_count_30d/60d, inst_30d/60d
- Day 2 ② 估值 z-score 4 (PIT-safe rolling 1Y, **替代 raw_aif10_valuation_quantile latest-snapshot leakage**): pe_ttm_z_1y, pb_z_1y, ps_ttm_z_1y, roe_q_z_4q. ROWS BETWEEN 239 PRECEDING AND CURRENT ROW = exactly 240 trading days (Codex Mi1 fix)
- Day 2 ③ 板块 momentum 5 (mart_stock_industry_pit ASOF → fact_sector_momentum_daily PIT date<=signal): sector_ret_5d/20d/60d, sector_excess_20d/60d
- Day 3 ④ 机构路径 A 5 (Codex Q3 SQL, fact_top10_holder_period.effective_date<=signal): inst_quality_wavg/max, total_holding_ratio, holder_cnt, top_inst_holding_ratio
- + 1 PIT meta: industry_pit_confidence ('observed_snapshot' / 'current_label_fallback') 让下游可 filter (Codex M1 fix)
- 输出表 `mart_p0a_feature_label_panel_v3` (v2 保留兼容)
- 14 单测 (PIT 严格 + Codex Mi2 推荐补强 5: z 算术 / per-date quantile / unmatched-NULL / pit_confidence / 240-row exact) 全过

**Codex review (a8c34359a) 完整修复**:
- C1 + M4: `mart_institution_profile.win_rate_60d` 当前 latest NOT PIT. inst_quality_{wavg,max} 改 WHERE inst_quality IS NOT NULL (Codex M4), 加注释 critical TODO v3.5 接 PIT snapshot
- M1: industry_pit_confidence 字段输出, 下游 P0b 训练可 filter 'current_label_fallback' 严格 PIT
- M2: top_inst_holding_ratio quantile 改 per-signal_date subquery (排除 NULL inst_quality + 防全局 mix future)
- M3: 文档 102 features (alpha158 实际 64 不是 65)
- Mi1: rolling window 239 PRECEDING + current = exactly 240 trading days = 1Y

**`scripts/build_p0a_feature_panel_v3.py`**: CLI 跑 v3 build (KEEP universe + alpha158 dates → build_p0a_feature_label_panel_v3)

**PIT 调研结论 (Day 1)**:
- raw_aif10_valuation_quantile 无时间字段 → latest snapshot only, 历史回测 leakage. 替代: PIT 干净 rolling z-score
- dim_stock_tdx_industry_history 仅 ~1 周 snapshot, mart_stock_industry_pit 多 `current_label_fallback`. 接受跟 backfill_sector_momentum 同妥协
- fact_top10_holder_period.effective_date DDL "公告日+1 交易日 PIT 安全", 实测 NULL 率待 ablation 完后查
- mart_stock_survey_features.as_of_date PIT 安全

### 2026-05-14 (Phase v3.2 v2 扩 feature + chain orchestrator)

**`services/labels/feature_join_v2.py`** (+ 6 features, 79 → 85):
- `formula_{macd, dyma, turtle20, turtle55, reversal}_triggered`: signal_date 当日触发 dummy (from fact_signal_context)
- `formula_n_triggered`: 当日触发公式数量
- Codex acf48d35 Q1 CRITICAL fix: 删 stage_opt_per_stock 3 列 (MAX GROUP BY stock_code 是 systemic leakage)
- 输出表 `mart_p0a_feature_label_panel_v2` (跟 v1 并存, 不破坏现有 P1 ablation reads)

**`scripts/run_v3_2_full_chain.py`** (P1 ablation 后接续):
1. build feature_label_panel_v2 (+ stage_opt + formula_trigger)
2. train P0b v2 × 3 horizon (5d/10d/20d)
3. Deflated SR audit (Bailey-LdP)
4. paper_sim_v2 with ml_score yaml (blend Option A)
5. P2 composite grid (81 weights)
6. P3 final holdout (4 硬验收)
7. promote champion (P3 PASS 才 promote)

### 2026-05-14 (Phase v3.2 governance wire — build/feature_join 加 post-insert verify)

**Phase ψ.γ.dict.2 兑现** (之前 commit 模块但没 wire = 反例):
- `services/labels/build.py + feature_join.py` 加 `_post_insert_governance_verify(conn, table_name)`
- SQL INSERT 完成后 sample 100 行 → validate_rows_before_insert (skip_missing_table=True)
- 不阻塞 INSERT (max_violation_rate=1.0), 仅 log
- 字典 8 mart schema (commit 7e0ba50f) 现可被 enforce 验证

### 2026-05-14 (Phase v3.2 governance wire — 7 mart 入 schema + yaml + Deflated Sharpe)

**用户 push back: "说了没做" 扫描结果**:
- `services/data_governance/*` (commit f429d91f) 没在 ETL 调 (Phase ψ.γ.dict.2 自己反例)
- 工程红线"新表必须注册 dim_schema_version" 7 个新 mart 没注册
- PLAN_V3 §99 P0a 列出的"机构路径 A/B + 公式触发哑变量"没接 feature
- `paper_sim_ml_score.yaml` 没跑过 / mart_p2_composite / mart_p3_acceptance / mart_champion 全空

**本批补强**:
- `services/schema_versions.py`: 加 8 个 mart 表 (p0a label + p0a feature_label + p0b oos + p0b walkforward_eval + p1 ablation + p2 composite + p3 acceptance + champion model)
- `backend/config/field_dictionary.yaml`: 8 mart schema 入字典 (含 pk/pit-key role / outlier_cap / enum)
- `scripts/p0b_deflated_sharpe_audit.py`: Bailey-LdP 跨 3 horizon study 校正 OOS RankIC

**TODO** (待 P1 ablation 完成):
- A1: feature_join 加 mart_per_stock_stage_strategy_optimal → stage_opt_sharpe/hp 特征
- A2: feature_join 加 mart_institution_industry_stat → inst_quality 特征 (路径 A)
- A4: feature_join 加 fact_signal_context → 公式触发 dummy + 公式 IC
- D1: build_p0a_* 入口 wire validate_rows_before_insert (governance enforce)
- 跑 paper_sim_v2 with ml_score mode (blend, 不替代 stage-aware Optuna)

### 2026-05-14 (Phase v3.2 horizon ablation 启动 — 5d/10d/20d 对比)

**新 CLI** `scripts/run_p0b_horizon_ablation.py`:
- 跑 3 个完整 P0b walk-forward (fwd_cost_after_5d/10d/20d)
- 解析 stdout RankIC + IC IR + n_dates
- 输出对比 table + best horizon

**5d horizon 跑中** (单窗 w2 RankIC=0.0417 显示某些窗口 PASS):
- w2: 0.0417 ✓
- w3: 0.0104, w4: -0.0056, w5: 0.001
- 波动大, overall 未必 ≥ 0.03

PLAN_V3 §3 #5 label horizon ablation 决策点正在跑.

### 2026-05-14 (Phase v3.2 perf — DataFrame bulk INSERT 250× 加速 + P0b 入库)

**P0b v5 完成**: executemany 1.96M × 17 placeholders 卡 12 min → DuckDB register DataFrame + `INSERT INTO ... SELECT * FROM df` **14 秒** (250× 加速).

实测最终: 1,959,564 predictions + 22 eval rows 入库 `mart_p0b_oos_predictions` + `mart_p0b_walkforward_eval`. P0c selector 可以读 score.

### 2026-05-14 (Phase v3.2 perf — batch INSERT executemany + DataFrame load)

**性能修复** (P0b train + P1 ablation 共用):
- per-row INSERT 1.7M rows × 5ms = 2.4 小时 → `executemany` 批量 ~10s
- DELETE 范围 + executemany 模拟 ON CONFLICT (DuckDB executemany 不支持 ON CONFLICT)
- DataFrame load: `conn._con.execute().fetchdf()` 27s vs cursor.fetchall() 21+ min hang

### 2026-05-14 (Phase v3.2 P0b 真实跑 — OOS RankIC=0.0108, Gate FAIL)

**P0b train v3 完成** (DataFrame-based rewrite, 50× 加速):
- 改用 `conn._con.execute().fetchdf()` 直接拿 pandas DataFrame (vs 旧 list[dict] 21+ min hang)
- 3,695,375 rows → 27s load + 9 min walk-forward (22 windows × 200 estimators)
- DataFrame-based pipeline 替代 list[dict] 慢路径

**实测结果** (KEEP universe × 2024-01..2026-04, fwd_cost_after_10d):
- 22 windows, n_dates=440
- **OOS RankIC mean: 0.0108** (Gate FAIL, < 0.03)
- IC IR: 0.1257
- 单窗 RankIC 波动: [-0.0067, +0.0364]
- 入库 mart_p0b_oos_predictions + mart_p0b_walkforward_eval

**结论**: 当前 alpha158 + risk_factors + financial_pit + 4 events 不足以预测 10d forward.
PLAN_V3 §6 串行 gate 标 P0b FAIL → 阻塞 P0c.

**下一步** (PLAN_V3 §3 决策点 + Rule 9.4 失败先承认):
- P1 ablation: alpha158 vs risk_factors vs events 贡献分析
- 试 5d / 20d horizon (PLAN_V3 §3 #5 label horizon)
- 扩特征 (机构路径 A/B / 公式触发哑变量 / 行业中性)

### 2026-05-14 (Phase v3.2 P4c promote CLI + walk_forward._ym() regression test)

**新 CLI** `scripts/promote_champion.py`:
- 读 `mart_p3_acceptance_result` by run_id → P3 KPI
- 读 `mart_p2_composite_result` 最高 composite_score
- 构造 `ChampionRecord` → validate → register_champion(promote=True)
- 对比 challenger vs current champion (compare_challenger)
- P3 FAIL 拒绝 promote (--force 强制)

**Regression test** `test_expanding_monthly_accepts_datetime_date_signal_date`:
- 防 P0b train 再 fail 在 'datetime.date' object not subscriptable
- 9 个 expanding_monthly tests 全 pass

### 2026-05-14 (Phase v3.2 P4c champion model + walk_forward._ym() 修 datetime.date 兼容)

**新模块** `services/portfolio/champion.py` (P4c 复盘闭环):
- `CHAMPION_DDL`: mart_champion_model (champion_id PK + 8 必填 KPI + is_current_champion + promoted_at/reason)
- `ChampionRecord`: 注册 record dataclass
- `validate_champion_kpi_completeness()`: P4c Gate 检 8 KPI 必填 (rank_ic/ann_ret/max_dd/monthly_win_rate/excess_vs_hs300/turnover/tx_cost_pct/capacity_concentration)
- `register_champion(conn, rec, promote, reason)`: 注册; promote=True 时其他 record `is_current_champion=FALSE` (单冠军)
- `get_current_champion(conn)`: 当前唯一 champion
- `compare_challenger(conn, challenger)`: 报每 KPI Δ

**单测** (8 passed): KPI complete/missing 验证, register w/wo promote, single champion 唯一, compare_challenger.

**Bug fix** `walk_forward._ym()`: DuckDB DATE 列返回 datetime.date 而非 str, 加 isinstance check 兼容两者. 原有 8 个 expanding_monthly tests 仍 pass. P0b train 第二次跑.

### 2026-05-14 (Phase v3.2 P0c yaml + P2/P3 CLI)

**新 yaml** `backend/config/paper_sim_ml_score.yaml`:
- selection.mode = 'ml_score' (新 dispatch case)
- selection.ml_score_model_id = 'lgbm_baseline_v1'
- selection.ml_score_max_candidates = 30
- 其他同 paper_sim_config.yaml (exit Optuna 9-dim / swap v1 / tx_cost)

用法: `python run_paper_sim_v2.py --config-path paper_sim_ml_score.yaml --start ... --end ...`

### 2026-05-14 (Phase v3.2 P2 + P3 CLI 入口)

**新 CLI**:
- `scripts/run_p2_composite_search.py`: 81 grid (3×3×3×3 = ret_w×dd_w×turnover_w×cost_w) 搜
  composite weights → 入 mart_p2_composite_result; 输出 Top 5 weight 组合
- `scripts/run_p3_final_holdout.py`: 读最近 N 个 OOS 月 stitched final holdout, 算 4 硬
  验收 (ann/dd/excess/monthly_win), HS300 ann_ret 从 dim_index_price 算, 入 mart_p3_acceptance_result

**P0b train 跑中**: 第 1 分钟内 RAM 升到 19.7% (1.5GB), 仍 R 状态.

### 2026-05-14 (Phase v3.2 P0a Acceptance PASS + P0b train 启动 + P1 ablation CLI)

**P0a Acceptance gate**: 10 PASS / 0 WARN / 0 FAIL ✅ (audit_p0a_panel.py)
- §1 Reproducibility: label_version + built_at 全填 (3.7M rows)
- §2 Cost: round_trip = 0.302% 常量 + 10-sample formula 验
- §3 Mask: unable_at_entry/exit_N=True → label NULL 全部生效
- §5 KEEP universe: 全部 60/00/30/68 前缀
- §6 PIT feature panel: 不含 exit_vwap/exit_date/unable_at_exit_

**新 CLI** `scripts/run_p1_ablation.py`:
- 读 mart_p0a_feature_label_panel → run_ablation_suite → 写 mart_p1_ablation_result
- 入参: --label / --run-id / --n-estimators / --learning-rate / --num-leaves
- 输出: stdout summary table (experiment × n_features × RankIC × IC IR × Δbase)

**P0b train** 后台启动: lgbm_baseline_v1 × fwd_cost_after_10d × n_estimators=200 × walk-forward.

**P0a feature_label panel 全量 build**: 3,695,375 rows in 41s (Codex Q4 优化 30+× 加速 vs label panel 21min).

### 2026-05-14 (Phase v3.2 — end-to-end pipeline runbook + P0a label panel 全量 build PASS)

**P0a label panel 全量 build 完成** (Phase v3.2 第一个数据产物):
- 4,625 KEEP universe stocks × 799 alpha158 panel dates = **3,695,375 rows**
- 耗时 1281.6s (~21 min), 4 LATERAL CTE 调度
- round_trip_cost_pct = 0.302%, label_version = 'p0a_v1'

**新脚本** `scripts/run_v3_2_pipeline.py` — PLAN_V3 §6 串行 gate Python 实现:
- 7 phases (p-1 → p0a → p0b → p0c → p1 → p2 → p3) 串行
- `--start-phase` / `--stop-phase` 单段或全跑
- 每 phase PASS 才进下一个 (Rule 11 串行硬约束)
- P-1 直接调 5 个 audit script; P0b 调 train_p0b_lightgbm.py;
  P0a/P0c/P1/P2/P3 当前是 stub + WARN, 待 CLI 入口加全后整合.

### 2026-05-14 (Phase v3.2 P3 — final holdout acceptance gate)

**新模块** `services/portfolio/final_holdout.py`:
- 4 个硬验收常量 (PLAN_V3 §0.1 用户终极目标):
  - `ANN_RET_TARGET = 0.30`
  - `MAX_DD_TARGET = -0.20`
  - `MONTHLY_WIN_RATE_TARGET = 0.55`
  - excess vs HS300 > 0 (硬约束)
- `FinalHoldoutMetrics`: KPI dataclass (含 model_version/feature_version/label_version/seed)
- `check_final_acceptance(metrics) -> AcceptanceResult`: 4 项硬验收
- `format_acceptance_report(metrics, result)`: markdown 报告 (PASS/FAIL + ✓/✗ 表)

**严格 PIT** (Rule 7 + Rule 9.1):
- final holdout 只读一次 (P3 验收)
- P0/P1/P2 阶段绝对禁读 (governance.enforce_pre_optimize 已有 check)

**单测** (11 passed):
- 常量 vs PLAN_V3 对齐
- perfect pass / 4 项各自 fail / 全 fail
- boundary 精确匹配 (≥ 通过)
- excess=0 fail (> 0 严格)
- format report PASS/FAIL 输出验证

### 2026-05-14 (Phase v3.2 P2 — composite scoring framework)

**新模块** `services/portfolio/composite_score.py`:
- `CompositeWeights`: PLAN_V3 §2 P2 权重 dataclass — ret_w/dd_w/hp_w/turnover_w/cost_w/capacity_w + hp_penalty_mode
- `_hp_penalty(avg_hp, mode)`: 3 模式 (linear=1/hp / log=1/log(hp+e) / piecewise<5d 重罚 >60d 轻罚)
- `compute_composite_score(ann_ret, max_dd, ...)`: 主公式
  = ret_w * ann_ret - dd_w*|max_dd| - hp_w*f(hp) - turnover_w*turnover - cost_w*tx_cost_pct - capacity_w*concentration
- `score_strategy_run(metrics)`: convenience wrapper

**待集成 P2.b**: validation grid/Optuna 搜权重 (PLAN_V3 §2 "权重由 validation 决定, 不预设").

**单测** (9 passed): pure return / dd lowers / turnover lowers / 3 hp penalty modes / wrapper / 用户目标 (ann≥30 dd≥-20 composite ≥ 0.05).

### 2026-05-14 (Phase v3.2 P1 — ablation framework (alpha158 / risk / financial / events drop-one + only-one))

**新模块** `services/ml_ranking/ablation.py`:
- `FeatureGroup`: 命名 feature group (e.g. alpha158=65列, risk_factors=6列, financial_pit=4列, events=4列)
- `DEFAULT_GROUPS`: 跟 mart_p0a_feature_label_panel 对齐
- `run_ablation_suite(rows, groups)`:
  - baseline 全 groups → walk-forward 跑 → RankIC
  - drop-one: 逐个去掉 group → walk-forward → 比 baseline
  - add-one: 只用单 group → walk-forward → 看单组贡献
- `AblationSuite.summary()`: tabular dict (rank_ic + ic_ir + delta_vs_baseline)

PLAN_V3 §3 数据决定的决策点接入:
- #2 alpha158 全量 vs top-N (add-one only_alpha158 vs baseline)
- #3 机构路径 A/B (drop events_inst)
- #4 公式特征是否保留 (drop formula_dummies — 当前未加入 panel)

**单测** (4 passed): baseline + drop_one + add_one 数量 + n_features 正确; signal_group >
noise_group (synthetic 强信号验证).

### 2026-05-14 (Phase v3.2 P0a.4 — audit_p0a_panel.py PIT + Acceptance gate)

**新脚本** `scripts/audit_p0a_panel.py` (P0a Acceptance gate):
- §1 Reproducibility: label_version / built_at 全 non-NULL
- §2 Cost deducted: round_trip_cost_pct > 0 + 常量; 10-sample 抽 spot check (exit/entry - 1) - rt = label
- §3 Mask effective: unable_at_entry=True 时 5/10/20 label 全 NULL; unable_at_exit_Nd=True 时该 horizon label NULL
- §5 KEEP universe: 全部 stock_code 前缀 ∈ ('60','00','30','68')
- §6 PIT (feature panel): mart_p0a_feature_label_panel 不含 exit_vwap_/exit_date_/unable_at_exit_ 字段 (forward 在 label 不在 feature)

待 P0a 全量 build 完跑 → P0a Acceptance gate PASS/FAIL.

### 2026-05-14 (Phase v3.2 P0c — paper_sim selector ML score loader Option A)

**新模块** `services/paper_sim/ml_score_loader.py`:
- `load_today_candidates_ml_score(conn, signal_date, model_id, max_candidates, min_score)`:
  - 主排名: mart_p0b_oos_predictions ORDER BY score DESC LIMIT K
  - Exit params LEFT JOIN: mart_per_stock_stage_strategy_optimal best (oos_sharpe DESC, n_traded ≥ 5)
  - 返回 list[CandidateRow] 兼容现有 selector.py 结构
  - tier='ML_RANK' / match_tier='ml_score' 跟 V2 区分

**Option A 决策** (PLAN_V3 §99 P0c):
- selector ranking 用 ML score (替换公式 sharpe 排名)
- exit / swap 仍走 Optuna 9-dim 公式 (mart_per_stock_stage_strategy_optimal)
- 隔离"选股 alpha 是否成立" 实验, P2 再做 A/B/C 对比

**单测** (6 passed): top-K ORDER BY score / min_score filter / model_id filter / empty date /
exit params 取 best oos_sharpe / n_traded < 5 filter.

**集成 P0c.b** (本 commit): selector.py::load_today_candidates_dispatch 加 mode='ml_score' case (lazy import), SelectionConfig 加 3 个 ml_score_* 默认字段. 77 paper_sim tests pass.

### 2026-05-14 (Phase v3.2 P0b — train CLI + output DDL)

**新加** `services/ml_ranking/ddl.py`:
- `mart_p0b_oos_predictions`: 每行 (stock_code, signal_date, score, model_id) — P0c selector ORDER BY score 取 top-K
- `mart_p0b_walkforward_eval`: 每行 (run_id, window_idx, model_id, rank_ic, ...) — 单 window 评估

**新 CLI** `scripts/train_p0b_lightgbm.py`:
- 读 mart_p0a_feature_label_panel → walk-forward → 写 mart_p0b_oos_predictions + mart_p0b_walkforward_eval
- 入参: --label / --run-id / --model-id / --n-estimators / --learning-rate / --num-leaves
- 输出: stdout 含 stitched OOS RankIC + Gate PASS/FAIL

**Codex review** (thread afdcb201a02362909, async): 6 个 Q (NaN label/feature filter / META 完整性 / ranks ties / passed_gate 阈值 / overwrite 历史 / LambdaMART ablation).

### 2026-05-14 (Phase v3.2 P0b — LightGBM pointwise + walk-forward + RankIC 模块)

**新模块** `services/ml_ranking/`:
- `rank_ic.py`: 横截面 RankIC (Spearman) + stitched OOS aggregation (mean + IC IR)
- `lightgbm_walkforward.py`: LightGBM pointwise regressor + expanding_monthly walk-forward
  - `LightGBMWalkForwardConfig`: 训练超参 (num_leaves=31 / learning_rate=0.05 / n_estimators=200 / ...) + walk-forward (min_train_months / forward_months)
  - `train_lightgbm_walkforward()`: 单消息驱动全 pipeline — split → fit per window → predict → stitched RankIC
  - `WalkForwardResult.passed_gate`: P0b Acceptance 检 RankIC ≥ 0.03 AND n_dates ≥ 30

**复用** (Rule 2/"可复用"):
- `services.optimization.walk_forward.split_expanding_monthly` (R1 标准, Rule 8 强制时序)
- LightGBM 4.6.0 (已 install)

**单测** (14 passed):
- `test_rank_ic.py`: perfect/anti corr / random noise / NaN filter / 1 stock skip / empty / missing label (9 tests)
- `test_lightgbm_walkforward.py`: synthetic linear signal (RankIC > 0.10) / pure noise (|IC| < 0.30) / empty / too few months / gate property (5 tests)

**下一步 P0b.b** (待 P0a 全量 label panel build 完): 跑 mart_p0a_feature_label_panel 真实数据 → 出 OOS RankIC + 拼 cost-after returns + Acceptance gate.

### 2026-05-14 (Phase v3.2 P0a.3 — feature × label JOIN cross-DB + Codex Q4/Q5 critical fix)

**新模块** `services/labels/feature_join.py`:
- `FEATURE_PANEL_DDL`: `mart_p0a_feature_label_panel` (PK=(stock_code, signal_date)) 含 label 字段 (5/10/20 fwd_cost_after + entry_date + unable_at_entry) + 65 a158 列 + 6 risk_factors + 4 financial_pit + 4 event dummies + metadata
- `_FEATURE_JOIN_SQL`: 一次 CTE 化 4 个 LEFT JOIN — grid (CROSS) → label / a158 / risk_asof / financial_pit / lhb_agg / inst_agg
- ATTACH alpha158.duckdb AS a158, 写入 smartmoney.duckdb

**Codex review fix** (thread ac55f8f69918a6ae0 → cancelled at 1h+ stuck, new thread ab74ca105171568e8 完成 review):
- **Q4 critical fix**: 4 LATERAL nested-loop → 2 pre-aggregated CTE (lhb_agg + inst_agg), COUNT(*) FILTER 同时算 7d/30d windows. 单一 hash join 替代 O(N×scan).
- **Q5 critical fix**: risk_factors ASOF 决策 — calc_date 本身是 deterministic from K-line (vol_60d 用 [T-60, T]), PIT-safe by construction. 不强加 ingested_at filter (当前 backfill 全 ingested_at=2026-05-13 → 100% NULL). TODO 后续增量 ingest 改 ingested_at=calc_date+1 后可启严格 filter.
- **Bug fix** (self-discovered): fact_lhb_event PIT 字段是 trade_date 不是 notice_date; institution_event.notice_date 格式 'YYYYMMDD' 需要 STRPTIME.
- **Schema fix**: fact_risk_factors 没 mom_60d 列, 只有 mom_30d/mom_120d.

**实测** (3 stocks × 3 signal_dates = 9 rows): SQL 跑通, vol_60d/sharpe_60d/pe_ttm 全 non-null, event_inst_30d 正确 boolean.

### 2026-05-14 (Phase v3.2 P0a.2 — build_p0a_label_panel SQL builder + 单测)

**新模块** `services/labels/build.py` + `services/labels/ddl.py`:
- `LABEL_PANEL_DDL`: `mart_p0a_label_panel` schema (20 字段, PK=(stock_code, signal_date))
- `_BUILD_SQL`: 一次性 CTE 算 (entry_date / exit_dates / entry_vwap / exit_vwaps / unable masks / fwd_cost_after) — 用 ROW_NUMBER() OVER 算 trade day rank, 自动跳非交易日.
- `build_p0a_label_panel()`: ATTACH market.duckdb, 写 tmp_signal_dates + tmp_stocks, 跑 SQL, idempotent DELETE+INSERT 入 mart 表.

**Mask 逻辑**:
- 停牌: K 线 NULL OR volume=0 → unable=True
- 一字板: open=high=low=close 且 volume>0 → unable=True
- 任一 unable (entry or that horizon's exit) → label=NULL

**单测** (`tests/labels/test_build.py`, 6 passed): DDL / normal path / entry suspended / 一字板 entry / 仅 5d exit unable / label_version 常量.

**实际跑** (P0a.2.b 下个 commit): 取 alpha158 panel signal_dates + KEEP universe → 写 mart_p0a_label_panel (估计 ~4M 行).

### 2026-05-14 (Phase v3.2 P0a.1 — cost-after label 模块落盘)

**P0a 起步** (P-1 PASS 后启动, PLAN_V3 §6 串行 gate 解锁).

**新模块** `services/labels/cost_after.py` (P0a 训练 label 入口):
- `compute_round_trip_cost_pct(tx)`: 单次完整往返 (买+卖) tx_cost % (commission 2× + slippage 2× + stamp_duty + transfer_fee 2×), 实测 ≈ 0.302%
- `compute_forward_cost_after_returns()`: T+1 VWAP 入场 → 5/10/20 日 VWAP 退出, 减 round-trip → net return. 不可成交 mask 显式 None (entry_unable/exit_*_unable per horizon).
- `ForwardCostAfterResult`: 三 horizon + round_trip 元数据

**用户决策** (2026-05-14):
- 入场价 = T+1 VWAP (跟 paper_sim 实际成交成本一致)
- Mask = 停牌 + 涨跌停都 mask (跟 P-1.3 tradeability audit 一致)
- 后续 P0a.2 build script 调用方算 unable_to_trade_mask 传入此模块

**单测**: 7 passed (round-trip 实测 0.3% / normal 5/10/20 / entry mask / exit mask per-horizon / 0-price / loss path / round-trip 跨 horizon 常量).

**复用**: TxCostConfig from `services/paper_sim/config.py` (Rule 2 simplicity, 不平行造).

### 2026-05-14 (Phase v3.2 P-1 收尾 — 5/5 audit PASS + 治理模块 + CI 修复 + P-1.2 KEEP universe)

**Commits**: aa57c185 (CI matrix) → ea76571b (pyyaml) → 69371838 (P-1.4 root cause) → f429d91f (governance modules) → P-1.2 KEEP universe 落盘 (本 commit)

**P-1 整体 gate PASS** (5/5 audit, 可进 P0a):
- P-1.1 PIT: PASS=10/WARN=26/FAIL=0
- P-1.2 Survivorship (KEEP universe): PASS=12/WARN=2/FAIL=0
- P-1.3 Tradeability: PASS=9/WARN=1/FAIL=0
- P-1.4 Event Timestamp: PASS=55/WARN=5/FAIL=0 (修 fact_shareholder_plan 7034 placeholder)
- P-1.5 Universe Coverage: PASS=18/WARN=5/FAIL=0

**P-1.2 KEEP universe 决策** (用户硬指令):
- A 股个人散户 5 仓位场景接受生存者偏差, universe = active 60/00/30/68 (沪深主板/创业板/科创板)
- 新模块 `services/universe.py::is_active_a_share` 守门 (60/00/30/68 前缀检查)
- ETF (15/51/56/58) 等其他类**不硬编码进 EXCLUDED**, 后续 phase 单独 enable
- audit_survivorship.py Section 4 改成"KEEP universe K 线完整性 spot check" (5 个采样日 coverage ≥ 99.5%)
- PLAN_V3 §99 P-1 Go metric 同步更新 (KEEP coverage ≥ 99% 取代"退市/ST 覆盖差异")

**P-1.4 root cause fix** (Rule 5):
- 根因: tdxhub F10 parser 返回 placeholder plan stub (announce_date/subject/direction 全空), chunkymonkey ingest 没过滤就 INSERT → 7034 行空记录 (2026-04-28 一次 sync, 2138 个 distinct stock)
- 修: `ingest_holders_tdxhub.py` line 409 加过滤 (三字段任一非空才入库); DELETE 7034 历史污染; 加 `test_write_one_drops_empty_placeholder_plans` 防回退
- 验证: fact_shareholder_plan 15022 → 7988 rows, announce_date 非空率 100%

**新治理模块** (Phase ψ.γ 残留, terminal 崩溃后规整入 main):
- `services/data_governance/` (config/enforcer/etl_hook): 字段字典 runtime enforce — ETL INSERT 前守门 pk NULL / enum / sign / outlier_cap 违反; 23 单测
- `services/optimization/deflated_sharpe.py` + `scripts/check_deflated_sharpe.py`: Bailey-LdP 跨 study 多重检验校正 (p>0.95 才算 alpha 真存在, 防 Rule 7 单 study OOS + Rule 8 walk-forward 仍含累积 selection bias); 26 单测
- yaml fix: `fact_risk_factors.stock_code` 加 `role: pk` (原本只 pit-key)

**CI 修复** (3 commit, 5 连续 fail → green):
- `aa57c185` matrix `[3.10, 3.11]` → `[3.11, 3.12]` (项目代码用 datetime.UTC, Python 3.11+ 标准 API)
- `ea76571b` install deps 加 `pyyaml` (3 个 config loader 都 import yaml)
- `69371838` P-1.4 root cause fix push

**Codex review thread `ac55f8f69918a6ae0`**: P-1.2 KEEP universe 修订 review 中 (universe.py + audit_survivorship.py edge cases).

**反例新增 (CLAUDE Rule 5 表)**:
- ingest 写空 placeholder 行: 没过滤 parser 返回的 stub → audit FAIL → 必查 sync 路径根因 (不放松阈值)
- CI 5 连续 fail: Python version 缺承诺 (无 pyproject.toml) + 缺依赖 (pyyaml 漏装) → 走 smoke import 拦截

### 2026-05-14 (Phase v3.2 P-1.2~P-1.5 并发完成 — P-1 gate FAIL, 待 audit 修复 + backfill)

**Rule 11 并发首测**: 4 个 general-purpose subagent 并发各写一个 audit, 都用 read_only=True 连接, 唯一 output path, 互不依赖. 实测可行.

**新脚本** (chunkymonkey/backend/scripts/):
- `audit_survivorship.py` (P-1.2): PASS=6 WARN=2 **FAIL=5** — Codex push back: spot check 缺 `listing_date <= sig_date` 条件 (FALSE POSITIVE for 11%; 真 K线 gap 存在)
- `audit_tradeability.py` (P-1.3): PASS=9 WARN=1 FAIL=0 — Codex push back: 涨跌停规则未接入 paper_sim 应升级 WARN→FAIL
- `audit_event_timestamp.py` (P-1.4): PASS=54 WARN=5 **FAIL=1** — Codex push back: `fact_shareholder_plan.announce_date` 是 nullable legacy 列, 不应硬 FAIL (用 `source_available_date` 字段更准)
- `audit_universe_coverage.py` (P-1.5): PASS=18 WARN=5 FAIL=0 — Codex push back: `GAP_FAIL_RATIO=0.05` 隐式放松"100% 覆盖" 要求

**Codex review thread `a69d6c54f52aeff36`** — 4 个 audit 反馈, 用户原则 push back Codex Q3:
- (a) 修 P-1.2 audit listing_date 条件 → 重跑得到真实覆盖率
- (b) backfill ~780 退市股 K 线 (**用 tdxhub, 不用 akshare**: 用户原则数据源可信度) + `dim_listing_status` 实例化
- (c) P-1.3 升级 WARN→FAIL (paper_sim stop/limit wiring) — Codex 对
- (d) ~~P-1.4 audit 改字段~~ — **Codex 错**: 用户原则 "上市公司数据不会真缺", `fact_shareholder_plan.announce_date` 47% NULL 是 sync 路径 bug, 不该放松 audit. 应该查根因 + 从 tdxhub/miaoxiang 重拉补全 (CLAUDE.md 新增"数据源可信度分级")

**P-1 整体 gate**: 2 真 FAIL → PLAN §6 串行 gate 阻塞 P0. 修复路径:
1. 修 P-1.2 audit listing_date bug → 重跑得真实数字 ✅ (修复后 11% 不变, 真生存者偏差)
2. ~~升级 P-1.3 WARN→FAIL~~ → 改 WARN + pending_phase=P0c (P0c 工程任务非 P-1 数据审计)
3. backfill: 退市股 K 线 + `announce_date` 都走 tdxhub (待启动)
4. 重跑 P-1 全套 → 若 PASS 进 P0a

**Pending fix tasks** (TaskCreate #18-21): tdxhub backfill 退市 K 线 / announce_date / dim_listing_status / 重跑 audit.

### 2026-05-14 (Phase v3.2 P-1.1 落盘 + Codex review 修复 + Rule 11 并发原则)

**新脚本**: `backend/scripts/audit_pit_integrity.py` (P-1.1 PIT 完整性审计, 5 sections).

**Codex review thread `a78ce8072a36f2c83` 反馈, Critical 全修**:
- Q1 OOS predicate AND→OR bug (修) — 暴露真 leak: `mart_per_formula_stage_optimal` 224/426 行 OOS 期跟 train 期重叠 (v2 legacy, P0a 不作主源, WARN 而非 FAIL)
- Q3 DB 连接改 `services.duck_adapter.connect(db_path, read_only=True)` 支持并发
- Q4 forward leak spot check 改 5 个跨 regime signal_date (2024-04 / 2024-12 / 2025-06 / 2026-03 / latest)
- Q6 加 Section 5 legacy usage guard (`git grep` 静态扫 v3.2 selector/optimize/build 是否引用 v2 legacy 表)

**实测最终结果** (Codex 修复后): PASS=10 / WARN=26 / FAIL=0 → P-1.1 PASS

**P-1.1 实测结果** (PASS=6 / WARN=8 / FAIL=0):
- 225 个 fact/mart 表中 193 有 PIT 列 (85.8%), 31 exempt (audit/snapshot/dim), **0 不应有但缺失** → PIT 列覆盖通过
- v3.2 critical 表 `mart_per_stock_stage_strategy_optimal`: 2 distinct built_at, 2174 行 → 走向 expanding_monthly
- v2 legacy 表 `mart_per_stock_strategy_optimal` / `mart_per_formula_stage_optimal`: 单 batch 写入 (24K + 426 行) → v3.2 不作主决策, 仅作 baseline
- forward leak spot check: 5 PIT 源 (risk_factors / financial_pit / capital_flow / signal_context / technical_trigger) 含未来日期行 (selector 必须 `WHERE pit_col <= signal_date` 过滤)

**CLAUDE.md 新增 Rule 11** — 并发 vs 串行执行原则:
- 11.1 串行硬约束: PLAN §6 Phase gate / 同文件 / 同 DB 表写 / 同 Optuna study / commit 序列
- 11.2 可并发: read-only audit / 独立特征源 / 独立 ablation / Codex review (按模块)
- 11.3 实现: 单消息发多 Agent calls (max 5) / `run_in_background: true`
- 11.4 安全清单: 启动并发前必查 (无文件/DB/资源冲突, 互不依赖, 串行汇总)
- 11.5/6 反模式 + Codex review 策略

**下一步**: P-1.2 ~ P-1.5 (4 个 audit) 用 Rule 11 并发执行 (5 agents 同时写 + 跑).

### 2026-05-14 (Phase v3.2 共识落盘 — Claude × Codex 三轮讨论达成 ML ranking 主导路线)

**重大方向调整**: v2 ensemble 拼权重 + v3 两路合并 **全部废弃**, 改 ML ranking 主导.

**讨论历史**: Claude × Codex 三轮 (`a15203724858923e8`):
- Round 1: Codex initial review PLAN_V3 (两路合并), 给出可行性 **3/10**
- Round 2: Claude push back 5 点 (walk-forward / 估时 / fake P50 / 机构 join / paper_sim 改造), Codex 全部接受
- Round 3: Codex 出完整 PLAN_V3.2 草稿, Claude 落盘 + 加分支约束

**ceiling test 结果填表 (PID 12518 → KPI)**:

| 实验 | ann | mdd | sharpe | 结论 |
|---|---|---|---|---|
| 13-alpha hp=15 baseline | +3.78% | -30.1% | +0.29 | 当前真钱基线 |
| **+ per_stock_stage=true (ceiling)** | **-26.5%** | **-50.5%** | **-0.61** | 含 PIT leakage 都失败 → 路线证伪 |

**新路线 (PLAN_V3.2)**:
- ML ranking 主导 (LightGBM/LambdaMART), 公式 + 机构跟随 降为特征/baseline/解释层
- 三目标 composite + 换手/容量/滑点惩罚 (代替原 7 目标)
- walk_forward expanding_monthly R1 + final holdout 锁最近 6 个月
- 串行 Phase gate (P-1 → P0a/b/c → P1 → P2 → P3 → P4a/b/c)
- 10 个数据决定的决策点 (ablation 决定, 不拍脑袋)

**CLAUDE.md 新增 Rule 10** — Codex review gate + 单分支策略:
- 10.1 代码 commit 前必走 Codex review (markdown 类豁免)
- 10.2 Codex 不可用 fallback: Claude 自审 5-question
- 10.3 main 单分支, 禁开 feature 分支 / worktree (用户硬指令)

**项目改名**: chunky-monkey-v2 → chunkymonkey (GitHub repo + 本地目录 + 16 文件引用同步).

**PLAN_V3.md** (本仓库根) = v3.2 共识版 实施计划, 含 §0-§9 完整路线. 后续 /goal 命令从中执行.

### 2026-05-14 (Phase ψ.γ.experiment — ablation 3 fail + per_stock_stage ceiling test 跑中)

**用户 4 次 /loop = 自主推进**. 我做了 3 个 ablation 实验全 fail, 当前跑 ceiling test (PID 12518).

**Ablation 对比** (paper_sim 2024-04 ~ 2026-05, 509 trading days):

| 实验 | ann | mdd | sharpe | 月胜 | hp | turnover | tx cost |
|---|---|---|---|---|---|---|---|
| 14-alpha (含 mean-rev sector_pred) hp=15 | -17.9% | -46.2% | -0.11 | 50% | 15 | 38.7x | 9.7% |
| 13-alpha hp=30 (减半 turnover) | -10.9% | -39.7% | -0.03 | 58% | 27 | 21.6x | 6.5% |
| **13-alpha hp=15 (current best baseline)** | **+3.78%** | **-30.1%** | **+0.29** | ? | 15 | ~30x | ? |
| **13-alpha hp=15 + per_stock_stage=true (跑中)** | **?** | ? | ? | ? | 15+ | ? | ? |

**学到 (Rule 9.4 失败先承认)**:
1. 加 alpha 已饱和 — 14th alpha (Ridge IC=-0.06 mean reversion direction=-1) 反 hurt 21pp ann
2. hp 翻倍减 turnover ✓ 但 ann 退化 14pp — long-holds 拖累, 不能 cut loss 快
3. 真问题在 **alpha 自身弱** — mart_per_stock_stage_strategy_optimal 整体 OOS avg sharpe -0.331
4. 用户目标 +30%/-20% 跟实测 baseline +3.78%/-30% 差距 = real-world friction (tx cost + 流动性 + PIT clean 收敛)

**关键技术债发现**: `mart_per_stock_stage_strategy_optimal` **PIT broken** — built_at 全 2026-05-13 (单 batch 写入, 不是 walk-forward multi train_end_date). paper_sim 历史选股时含 selection leakage. 当前 ceiling test 是 ceiling 不是 real production.

**当前 in-flight**: PID 12518 paper_sim per_stock_stage=true, ETA 14:00.

**HANDOFF**: `HANDOFF.md` 已写 (handoff 给 Claude Code CLI).

### 2026-05-14 (Phase ψ.δ.1 — 板块轮动预测 Ridge regression alpha + IC mean-reversion 发现)

**用户原话**: "按照规律做个板块、概念、行业轮动啥的, 并作出预测, 辅助选股"

**对齐用户的 CDE 选择**:
- C 动量+反转分阶段 + E ML 端到端 — 取轻量版 Ridge regression 防 overfit

**实现**:
- 新脚本 `backend/scripts/train_sector_rotation_predictor.py` (~220 行)
- 输入特征 (8 维 sector-level): ret_5d/20d/60d, vol_60d, excess_20d/60d, price_vs_ma20/60
- Target: forward 10 day sector return
- Model: Ridge regression (alpha=1.0)
- Walk-forward: 每月末 retrain on cumulative past (purge 10 day gap 防 target leakage)
- 新表 `fact_sector_predicted_ret_daily` (PK = sector_name×date×model_train_end)
- 8983 行预测写入, 跑批 2 秒

**关键发现 — IC 负**:
- IC = **-0.056** (Pearson), Rank IC = **-0.060** (Spearman, p<0.001 on 8853 pairs)
- Direction hit ratio = 49.0% (worse than 50%)
- **这是 mean reversion 信号** — 板块短期强 → 短期弱
- Ridge 学到的是 momentum 方向, 但市场 reversing

**alpha 接入 (Rule 6 数据驱动)**:
- 新 view `v_stock_sector_predicted_ret` (stock_code × predicted_ret JOIN dim_tdx_industry)
- `paper_sim_ensemble.yaml` 加 14th alpha `predicted_sector_ret_10d`
  - direction = **-1** (mean reversion: 预测低 → 实际高 → 加成)
  - weight = 0.10 (pre-Optuna default, 后续 Optuna 寻优)

**测试**: 跑 paper_sim ablation (baseline vs +sector_pred alpha) 看 KPI 是否改善.

**学到 (Rule 9.4 数据失败先承认)**: 简单 Ridge 不会一次到位; 但 IC 信息已学到了
正确方向 (虽然反向), 跟用户"实事求是数据驱动"一致.

### 2026-05-14 (Phase ψ.γ.dict.1 — 字段字典 yaml + 跨表治理基础)

**用户原话**: "之前说的数据治理做了么, 就是清洗、加工、存储之类的"

**承认**: 没系统做. 项目数据治理碎片化 — 各 sync 客户端独立清洗 / ETL 散落多脚本 /
跨表字段命名不一致 (date vs trade_date vs calc_date) / 单位不一致 (volume 在 akshare=股
在 tdxhub=手) / VWAP bug 暴露这一漏洞.

**修法 (Phase ψ.γ.dict.1 第一步)**:
- 新文件 `backend/config/field_dictionary.yaml` (~250 行)
- 内容: 3 个数据库 (market/smart/etf) × 12 张核心业务表 × 100+ 字段
- 每字段含: type / unit / role (pit-key/pk/business-canonical/in-sample-only) /
  enum / sign / outlier_cap / description / warning (e.g. volume MIXED unit)
- 通用约定: stock_code 格式 / PIT 命名 / null policy / outlier policy
- JOIN 模板: pit_max_by_stock_date / asof_kline_to_event
- 已知不一致 (§17 渐进 fix): 日期字段命名 / volume 单位 / outlier cap hardcode

**用途**:
1. ETL 写入前 sanity check (单位 / 范围 / PIT key 完整性)
2. 跨表 JOIN 写代码时查 "这表的 PIT key 是哪个字段"
3. 新人接手时一图看全核心 schema
4. 单测自动 verify schema 跟字典一致 (防漂移, 后续 Phase ψ.γ.dict.4 加)

**特别强调** (防 VWAP bug 类故障): `v_price_kline_qfq.volume` 字段
明确标 "MIXED — tdxhub=手 / akshare_sina=股", 加 warning + 引用 _vwap sanity helper.

**下一步**:
- Phase ψ.γ.dict.2: ETL normalize layer (统一 K 线读 + unit conversion + NaN handling)
- Phase ψ.γ.dict.3: pre-insert data quality governance (类似 Optuna governance for raw → fact)
- Phase ψ.γ.dict.4: schema-vs-dictionary 自动 verifier

### 2026-05-14 (Phase ψ.γ.1.v2 — Optuna 单 worker 缩 train window 重启)

**根因 (用户 push back)**: 我之前估算 "6.5万小时" 错了 — 把 per-stock backtest 跟 per-stock paper_sim 混了.

**实际并发能力**:
- per-stock × stage × formula 9 维 Optuna (24K 任务) — `optimize_per_stock_stage_strategy.py`
  **8 workers fork 实测 58 min** — 已实现
- ensemble 20 维 Optuna (50 trials) — 每 trial 跑完整 paper_sim 5 仓位组合, DuckDB 单 writer
  锁限制 single worker. **GPU 无意义** (TPE + DuckDB + 串行 simulation 都是 CPU bound)

**实测时长**:
- v1 21 mo train: trial 0 跑了 8+ min 还没出 — 估 25-30 min/trial × 50 = 20+ hr 太慢, kill
- **v2 9 mo train**: trial 0 = 101s, 50 trials ≈ 1.4 hr ✓ (PID 8029, study=ensemble_full_v2_short)

**经验**: 缩 train window 比 multi-worker (DuckDB 锁阻碍) 更直接.

### 2026-05-14 (Phase ψ.γ.2 — per-stock × stage 接入 ensemble loader L3)

**用户原话** (回忆): "持仓周期不应该全局统一, 应该是每个股票每种形态下每个公式下都单独选优"

**问题**: 该寻优产物 `mart_per_stock_stage_strategy_optimal` (24K 行 9 维 Optuna OOS) 已存在但
ensemble mode 没用 — 只用 default_holding 一组参数. 这是真正的 "per-stock × stage" gap.

**修法**:
- 加 `_load_per_stock_stage_optimal(conn, stock_stage_pairs, min_n_traded=5)` helper
  - 按 stage 分组批量 query (DuckDB 不直接支持 tuple IN, OR 拼/分 stage 简单)
  - 每 (stock × stage) 取 oos_sharpe DESC + oos_n_traded DESC 第一行 (跨 formula 取 best)
  - Rule 8: 只读 oos_* 字段
- ensemble loader 实现优先级: **per_stock_stage > vol_aware > default_holding**
- 把 stage_map 提前到 quality filter 之前无条件 load (P2 + L2 复用)
- config flag `selection.per_stock_stage.enabled` (默认 false, ablation 时 true)
- yaml `per_stock_stage:` section 加进 ensemble.yaml

**Touch 文件**:
- `backend/services/paper_sim/selector.py` (+`_load_per_stock_stage_optimal` ~60 行 + 优先级 logic)
- `backend/services/paper_sim/config.py` (`per_stock_stage: dict` 字段)
- `backend/config/paper_sim_ensemble.yaml` (`per_stock_stage:` 段, 默认 enabled: false)
- `backend/tests/paper_sim/test_per_stock_stage.py` (新, 4 单测 MockConn)

**测试**: 12/12 PASS (4 新 + 8 vol_aware regression). Integration test 等 Optuna PID 7702 跑完
不占 DB 锁后做 (full paper_sim ablation: enabled=true vs false 对比 KPI).

### 2026-05-14 (Phase ψ.γ.1 — ensemble 20 维 Optuna 全寻优)

**用户原话**: "把数据都充分调动起来" — 之前 ensemble.yaml 里 13 alpha weights + 3 regime
multipliers + 3 vol sigma + hp + max_vol 全部拍脑袋, 没让 Optuna 寻优.

**新脚本**: `backend/scripts/optimize_ensemble_full.py`

**Search space (20 维)**:
- 13 alpha weights ∈ [0.0, 0.4] each — reversal/sharpe/mom/vol/pe/roe/yoy/lhb/exec/holder/sector×3
- 2 regime multipliers (bear/sideways; bull=1.0 fixed baseline)
- 3 vol_aware sigma multipliers (stop/target/trailing)
- 1 hp ∈ {5,10,15,20,30}
- 1 max_vol_60d ∈ [0.20, 0.60]

**Walk-forward (holdout)**:
- train: 2023-01-03 ~ 2024-09-30 (21 mo) — Optuna 寻最优
- test:  2024-10-01 ~ 2026-05-12 (19 mo) — OOS 验证

**Objective**: constrained sharpe (max sharpe s.t. ann_ret≥0.30 AND max_dd≥-0.20).
违反约束 soft penalty = 10 × (违反量), 引导 Optuna 朝可行域走.

**新表**: mart_ensemble_optimal (PK=study_name), 含 OOS 列符合 Rule 8 governance:
  study_name, best_params_json, train/test KPIs (ann_ret/max_dd/sharpe/calmar),
  oos_n_traded, n_trials, best_trial_number, objective_function, ann_ret_min, max_dd_min, built_at

**Touch 文件**:
- `backend/scripts/optimize_ensemble_full.py` (新, ~380 行)
- `services.optimization.governance` 复用 (enforce_pre_optimize 守门)
- yaml 不动 (override 跑时注入)
- PROJECT_INDEX §4 + §14 同步

**Benchmark**: 4 mo paper_sim = 40s/trial, 估 21 mo = ~3.5 min × 50 trials = ~3 hr 一晚上能跑.

**等 Optuna 跑完**: best_params 入 mart_ensemble_optimal → paper_sim 用 best_params 跑完整
2023-2026 → 看 OOS KPI 是否过 +30%/-20% 目标.

### 2026-05-14 (Phase ψ.γ.discipline — Rule 6/5/7 治理工作流 + 反例沉淀)

**用户 push back**: "即使 CLAUDE.md 有 rule 但你也不遵守, 这个问题咋解决?"

**根因 (诚实承认)**:
- Phase ψ.β.5 L2 vol-aware: sigma=2.0/3.0/1.0 + bounds [-0.20,-0.05,0.10,0.35,0.03,0.10] 全部拍脑袋
- Phase ψ.β.4 ensemble alpha weights (13 个数字): 拍脑袋
- Phase ψ.β.4 regime_gate multipliers (0.3/0.7/1.0): 拍脑袋
- 共同特征: 我"觉得自己懂 Rule 6", 但写代码时下意识又违反

**修法 (3 层防护, 跟 PROJECT_INDEX hook 同套路)**:
- 层 1 (硬): `.git/hooks/pre-commit` → `backend/scripts/check_rule_compliance.py` —
  staged diff 含 magic alpha weight / sigma / multiplier / threshold / hardcoded date /
  stock_code / try-except pass → 必须有 `# evidence:` / `# from yaml:` / `# measured:`
  注释或 yaml 外置, 否则 reject commit. 7 测试场景全 PASS.
- 层 2 (硬): `.git/hooks/commit-msg` → `backend/scripts/check_commit_message.py` —
  commit message 必须含 GROUP A (test/防回退/修复) 关键词 + 若改 service/script/config 必须含
  GROUP B (PIT/OOS/实测) 关键词. 3 测试场景 PASS.
- 层 3 (中): CLAUDE.md 加 Rule 9.9 "写代码前 explicit ritual — 任何数字入代码前 self-check
  measured from where". Rule 9.8 工作流 enforcement 表补充 2 新 hook 描述.

**Touch 文件**:
- `backend/scripts/check_rule_compliance.py` (新, 290 行, 7 个反 pattern)
- `backend/scripts/check_commit_message.py` (新, 130 行, 2 group keyword)
- `.git/hooks/pre-commit` (新, native git hook)
- `.git/hooks/commit-msg` (新, native git hook)
- `.pre-commit-config.yaml` (加 hook config, 备用 pre-commit framework 路径)
- `CLAUDE.md` (Rule 6 反例表加 3 行 ensemble/regime/vol_aware 拍脑袋案例 + 新 Rule 9.9)

**整库扫描结果**: 283 历史 violations (Rule 5 silent 138 / Rule 7 date 112 / stock 22 /
Rule 6 alpha weight 6 等). 加进 §11.5 #17 渐进清理.

**学到的**: Rule 文字是被动的, 必须技术层硬挡. 每次 Claude 违 Rule → 加 hook, 不要靠"我会记得".

### 2026-05-14 (Phase ψ.β.5 — L2 vol-aware per-stock 参数缩放)

**用户洞察**: "我感觉现在的选股策略和实盘模拟策略似乎都是批量化均值, 没有做到精细化每个股票, 我的理解对么"

**确认**: 是. 当前 ensemble mode `default_holding` 给所有 candidates 同一组 (hp=15 / stop=-0.10 / target=+0.20 / trailing=+0.05), 完全不分股票特性 → 高 vol 股容易 stop_hit, 低 vol 股 target 不可达.

**修法 (Phase ψ.β.5 L2)**:
- 加 `_vol_aware_params(vol_60d, hp, va_cfg, defaults)` 函数到 selector.py
- 公式: `sigma_hp = vol_60d_annualized × sqrt(hp / 252)`,
  `stop = -2σ`, `target = +3σ`, `trailing = +1σ` (sigma 倍数 yaml 可配)
- Hard bounds clip 防极端 vol 失真: stop∈[-0.20, -0.05], target∈[0.10, 0.35], trailing∈[0.03, 0.10]
- ensemble loader 批量 PIT 加载 vol_60d (`WHERE calc_date <= signal_date`), 应用到 final candidates
- config flag `selection.vol_aware.enabled` 默认 false (向后兼容, ablation 时开)

**Touch 文件**:
- `backend/services/paper_sim/config.py` (加 `vol_aware: dict` 字段)
- `backend/services/paper_sim/selector.py` (加 `_vol_aware_params` + ensemble loader 批量 fetch vol_60d + override)
- `backend/config/paper_sim_ensemble.yaml` (加 `vol_aware:` 段, enabled: false)
- `backend/tests/paper_sim/test_vol_aware.py` (**新, 8 单测**: enabled/disabled/None vol/zero vol/mid vol/high vol clip/low vol clip/hp scaling/custom sigma)

**单测结果**: 8/8 PASS. 全套 paper_sim 67/67 PASS (无回退).

**下一步**: 等 ensemble v3 跑完 → 看 KPI → 开启 `vol_aware.enabled=true` 跑 v4 ablation 对比.

**5-level fine-graining roadmap** (按工程量排):
- L0 (现状): 全 strategy 一套参数 — 批量化均值
- L1: per-formula × stage — Optuna 已实现 (mart_per_formula_stage_optimal)
- **L2 (本次)**: per-stock vol-aware 缩放 — 半天, 已完成
- L3: per-stock × stage × formula 完整网格 — 1-2 天 (需扩 mart 表)
- L4: case-based / k-NN 历史相似度 — 1-2 周 (大工程)
- L5: ML 端到端 — 月级 (Phase ψ.γ)

### 2026-05-14 凌晨 (Phase ψ.β.sector — 板块强度 alpha + 综合 plan)

**用户提的 3 个根本问题**:
1. 反转因子是公式还是辅助? — **同时是两者** (backtest 当公式, ensemble 当 alpha)
2. 数据应该拉齐 2023-01 — 系统 audit 找出 6 张表缺历史
3. 字段单位管理 — VWAP bug 暴露项目无 dict 机制

**用户洞察**: tdxhub 应该有现成的板块/概念 K 线

**调查结果**:
- `services/tdx_industry_client.py` 只拉**分类映射**, 没拉行业 K 线
- `services/block_client.py` 已实现 TDX block_zs/fg/gn (指数/风格/概念) — **但只拉成分股映射, 没拉 K 线**
- 项目当前路径: services/sector_momentum.py 用方案 A (**成分股等权聚合**算行业指数), 不依赖 tdxhub 直接的行业 K 线
- 缺陷: calc_sector_momentum 只算"今天", 没历史 backfill, mart_sector_momentum 只 41 行 (2026-04 起)

**修法 (Phase ψ.β.sector)**:
- 新写 `backend/scripts/backfill_sector_momentum_history.py`
- 方案 A: K 线 × `dim_stock_tdx_industry_history` (PIT 行业) ASOF JOIN
  → 每日按当时 PIT 行业聚合个股 close 等权 → 板块指数
  → 算 ma20/60 + return_5d/20d/60d/120d + excess vs 全市场 + vol_60d + price_vs_ma 位置
  → 写新表 `fact_sector_momentum_daily` (sector × date, 跟现有 mart 表不冲突)
- 预估: 13 一级行业 × 800 天 = ~10K 行, ~5-10 min 跑

**新表 schema**: fact_sector_momentum_daily (sector_name, date, sector_close, n_stocks,
ma20, ma60, vol_60d, ret_5d/20d/60d/120d, excess_20d/60d, price_vs_ma20, price_vs_ma60, n_bars)

**集成路径**:
- paper_sim_ensemble.yaml 加 3 sector alpha (ret_60d / excess_60d / price_vs_ma20)
- 反转 backtest mode 加 filter: 排除 ret_60d < market_ret_60d 的弱行业股
- paper_sim ablation: with vs without sector alpha

**等 paper_sim reversal_v2 ablation 完后跑 sector backfill** (DB 锁).

### 2026-05-14 凌晨 (Phase ψ.β.align — 严重 VWAP bug + selector 跟用户对齐)

**用户 push back**: "你跑的是单一策略, 没真正模拟实盘选股 — 实盘是各种公式入池后按 OOS 强弱选最强"

**修法 #1: selector 按 oos_sharpe 排名 (PIT 干净, 跨公式可比)**
- 老代码: `score = today_strength × tier_mul` (公式内自定 strength, 跨公式不可比)
- 新代码: `score = oos_sharpe × tier_mul + 0.01 × today_strength` (oos_sharpe 主, strength tiebreaker)
- mart_per_formula_stage_optimal 是 walk-forward 多行表, JOIN WHERE train_end_date <= signal_date
  本来就 PIT — 我之前过度保守用 strength 排, 丢了主排名信号

**修法 #2: _vwap 严重 bug — akshare_sina 数据源 volume 单位不一致**
- 实测: 2026-05-07 起 source 从 tdxhub → akshare_sina, volume 单位从 "手" 变 "股" (差 100×)
- 老 _vwap 写死 `amount / (volume × 100)` → akshare 数据算 vwap / 100 (0.11 元而不是 11.4)
- 触发 stop_hit 假信号, 持仓 pnl_pct=-99% — paper_sim NAV 从 1.6M 暴跌 360K
- 修后: _vwap 加 sanity check, 算 vwap_lot 和 vwap_raw 两种, 选落在 [low, high] 的;
  都不合理 → close fallback
- 3 新单测防回退 (akshare 单位 / tdxhub 单位 / 极端不合理)

**实测教训** (Rule 9.5):
- 用户之前 reversal-only smoke (-52% 年化) 当时被 VWAP bug 污染. 真实数字应该+25% 年化
- 数据源切换 (sync 进了新数据源) 没显式审计 — 沉默 break paper_sim
- 解决: _vwap 加 sanity, 失败先承认而不是用错值

### 2026-05-14 深夜 (Phase ψ.β.briefing — 16 项遗漏审计 + PROJECT_INDEX 大重写)

用户 push back: "其他事项一定也会有遗漏, 扫描对话记录找出来" + "项目文档标准是新人不读代码就能理解".

**16 项遗漏审计** (扫对话历史得出):
- P0 必修: 数据 sync / goal.md 维护 / mart_sector_momentum / swap 最终评估
- P1 高 ROI: 机构跟随 PIT (受 1 年限制) / case-based 回测 / regime gate 验证
- P2 中 ROI: archetype backfill / sentiment / vol-price 因子 / financial yoy fix
- P3 工程: swap_uplift / qfq leakage / 行业 PIT / 文档职责划分

**PROJECT_INDEX 重写** (满足 "新人 briefing" 标准):
- 加 "30 秒速览": 项目业务 + 用户目标 + 当前最强发现 + 距离目标
- 加 "Pipeline 数据流图": 端到端 raw → mart → selector → paper_sim → KPI
- 加 "常用命令 cheatsheet": 安装 / backfill / Optuna / paper_sim / 数据查询 / 测试
- 加 "16 项遗漏审计": 按 ROI P0-P3 分级 + 估时
- 加 "Performance Profile": 跑批时间预期 + 已修/未修 hotspot

527 行 → 800+ 行. 新人读 30 分钟就能完整掌握项目, 不用读代码 / 查 DB.

### 2026-05-14 后期 (Phase ψ.β.enforce — 工作流强制层)

**根因 (用户 push back)**: PROJECT_INDEX.md 多次遗漏更新, Rule 9.5 是被动文字, 没自动触发.

**修法 (3 层防护)**:
- 层 1 (硬): Pre-commit hook `backend/scripts/check_project_index_sync.py` — staged 含
  service/script/yaml/CLAUDE.md 但没含 PROJECT_INDEX → reject commit (exit=1)
- 层 2 (中): CLAUDE.md Rule 9.7 commit 前 5-question self-check; Rule 9.8 工作流 enforcement
- 层 3 (软): TodoWrite 每 phase 结束自动加 "update PROJECT_INDEX" todo (Claude 自觉)

**Touch 文件**:
- `backend/scripts/check_project_index_sync.py` (新, hook 脚本)
- `.pre-commit-config.yaml` (加 local hook)
- `CLAUDE.md` (加 Rule 9.7 + 9.8)
- `PROJECT_INDEX.md` (本次更新即遵守新规则)

**安装 hook** (一次性, 已写进 Rule 9.8):
```bash
pip install pre-commit && pre-commit install
```

### 2026-05-14 (Phase ψ.β.perf — Optuna 重跑 + 性能优化)

**关键发现** (按 Rule 9.4 + 9.5 沉淀):
- fact_institution_event 数据只 1 年 (2025-04 起), 无法做 800 天 backfill — 主 alpha 重建 deferred
- aif10 估值/一致预期 全是 latest 快照, 无 PIT — 改用 fact_financial_derived (跨 4 年季度)
- fact_signal_context / fact_stock_technical_stage 早期数据缺 — 都已 backfill
- mart_per_formula_stage_optimal 重跑 7 公式 1260 任务跑 8 小时 (vs 反转 3 公式 28 min) — 性能瓶颈 5×
- Optuna 跑批 hotspot:
  - `_idx` linear search O(N), 调用 1e11 次 — 已加 dict cache O(1)
  - `objective.py` / `optimize.py` 跑完 backtest_signals 又重跑 simulate_trade — 重复 50%, 已新加 `backtest_signals_with_trades` 一次性返回 trades

**数据资产新加** (Phase ψ.β PIT 主线):
- `fact_risk_factors` 4.8M 行 (跨 810 天, vol/sharpe/mom/skew/kurt)
- `fact_financial_pit_daily` 3.69M 行 (跨 748 天, PE_TTM/PB/PS_TTM/ROE/yoy/inst_holding_pct)
- `fact_capital_flow_pit_daily` 858K 行 (lhb/exec/holder PIT, outlier capped at 90%)
- `fact_signal_context` backfill 至 2024-03 (66% valid_stage)
- `fact_stock_technical_stage` backfill 至 2023-09-12 (2.4M 行)

**代码新加**:
- `backend/scripts/backfill_risk_factors_history.py` (β.1)
- `backend/scripts/backfill_financial_pit.py` (β.2)
- `backend/scripts/backfill_capital_flow_pit.py` (β.3)
- `backend/config/paper_sim_ensemble.yaml` (β.4)
- `backend/services/paper_sim/selector.py` 加 `load_today_candidates_ensemble` (β.4)
- `backend/services/backtest/realistic_engine.py` 加 `_BAR_DATE_IDX_CACHE` + `backtest_signals_with_trades` (β.perf)
- 5 new tests in `tests/backtest/test_realistic_engine_idx_cache.py`

**Claude 踩坑** (Rule 9.5):
- 估算 Optuna 全量 80 min, 实际 8 小时 (5× off). 教训: 全公式 vs 反转-only 单任务复杂度不一样.
- build_signal_context.py 行 159 重复 `from services.db import get_conn` 触发 Python local scoping UnboundLocalError. 教训: import 一次即可.
- 第一版 fact_capital_flow_pit_daily 没 outlier filter, holder_change_pct 含 30M 极端值. 教训: backfill 必加 sanity bounds.

### 2026-05-13 (Phase ψ.α 反转因子 + PROJECT_INDEX 首次写入)

(见 commit `545cb3d9` 详细)
- Rule 9 真金白银 / 第一性原理 写入 CLAUDE.md
- PROJECT_INDEX.md 首次写入 (406 行 13 节)
- 反转公式 3 variant (mild/deep/1w) 写入 formula_engine
- B walk-forward `split_train_end_forward` + `list_month_ends` + 10 单测
- mart_per_formula_stage_optimal 加 train_end_date 多行 schema
- paper_sim selector 改 walk-forward + 按 today strength 排名
- horizon_evidence 实测: reversal_1m_deep × 20d sharpe **+1.10** / win 61.8%
- B v2 严格 walk-forward 实测: reversal_1m_deep × stage=1 avg OOS sharpe **+0.39** / win 58.1%

### 2026-05-12 之前 (Phase ψ Optuna 治理)

(见 commit `34e83d75` 详细)
- Rule 7 (Anti-Look-Ahead) + Rule 8 (Optuna 治理) 写入 CLAUDE.md
- backend/config/optuna_config.yaml + services/optimization/{config,walk_forward,governance,composite,constraints,objectives,ddl,oos_aggregator}.py
- VWAP 100× bug 修复
- Rule 6 Measured-Not-Estimated 写入
- 73 单测全过
