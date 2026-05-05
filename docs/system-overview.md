# Chunky Monkey v2 · 系统现状简介

> 目标读者: Codex 或后续任何接手的 AI 协作者。写这篇不是介绍产品，而是让接手者能一口气把系统从上到下串起来。

**最近一次更新**: 2026-05-05 · 对应 Phase 0/1/3/4/6/7 生产化改造后的状态。

---

## 1. 项目目的

这是一个 **机构事件研究 + 多维量化评分 + ETF 策略研究** 的单机系统，不是生产交易系统。

核心假设: **机构是主角, 股票是机构行为的载体**。通过挖掘"哪些机构长期有正 alpha、他们在买哪些股"构建研究候选池; 配合 LightGBM 多维量化模型给全市场每天打分, 两条线交叉验证产出研究清单。

两条产品线:

- **股东挖掘**: 机构 track record + 股票信号 + 多维量化 topK 推荐
- **ETF 研究**: 网格交易自动寻优 / 对比长期持有 / 板块轮动预测 (纯量价, 不依赖 ML)

不做的事:
- 不是实盘交易系统, 不接券商
- 不是选股决策终审, 是"让用户更快形成观点"的研究助手
- 不追求 state-of-the-art ML, 因子工程和数据质量优先

---

## 2. 数据架构

### 2.1 三库结构 (DuckDB, 总 ~4GB)

| 文件 | 用途 | 表数 | 体量 |
|---|---|---|---|
| `data/smartmoney.duckdb` | 主库: 机构/事件/基本面/mart 层 | ~80 | 2.1GB |
| `data/market.duckdb` | K 线 + 除权除息 | 6 | 916MB |
| `data/etf.duckdb` | ETF 池 + K 线 + 策略回测 mart | ~10 | 111MB |
| `data/alpha158.duckdb` | 独立 Alpha158 因子库 | 1 | 1.9GB |

### 2.2 数据分层约定

```
raw_*   只追加, 从不覆盖 (按 batch_id 区分)
dim_*   维度表 (股票-行业、交易日历等, 小表)
fact_*  事实表 (事件流 / 特征面板 / 财务指标)
mart_*  集市层 (派生, 可重算, 带 schema 版本)
```

**重要不变量**:
- 数据起始点: **2023-01-01** (之前市场风格差异大, 全局截断)
- A 股习惯: `--stock-up: red`, `--stock-down: green` (涨红跌绿, 和国际反)
- 单点计算多处复用: 每个业务事实只允许一个 resolver
- 三可原则: 可见 / 可追溯 / 可复核

---

## 3. 主要数据表 (smartmoney.duckdb)

### 3.1 raw 层 (只追加, 来自外部 API)

| 表 | 说明 | 行数 | 来源 |
|---|---|---|---|
| `fact_top10_holder_period` | 十大流通股东 / 十大股东 (A/H 拆分 + holder_set + is_exit_row + source_tier) | 474k | tdxhub.holders.HolderFetcher (P7 起替代 market_raw_holdings) |
| `raw_tdx_f10_holder_research` | F10 「股东研究」原文 (raw_hash 链回 fact_top10_holder_period) | 5.2k | tdxhub F10 |
| `raw_executive_trade` | 高管股份变动 | 144k | akshare (stock_ggcg) |
| `raw_lhb_daily` | 龙虎榜明细 | 62k | akshare (stock_lhb_detail_em) |
| `raw_margin_daily` | 两融余额明细 | 2.9M | akshare (stock_margin_detail_szse/sse) |
| `raw_gpcw_detail` / `raw_tdx_gpcw_wide` / `raw_gpcw_financial` | 财务报表 | 62k / 62k / 17k | tdxhub gpcw 主供；akshare 仅兜底/遗留 |
| `raw_institution_surveys` | 机构调研 | 8.7k | akshare (stock_jgdy_*) |
| `raw_qfii_holding_quarterly` | QFII 季报持仓 | 7.9k | akshare (qfii_*) |
| `raw_capital_*` | 分红/解禁/回购 | ~55k | akshare |

### 3.2 dim 层 (维度)

| 表 | 说明 | 行数 |
|---|---|---|
| `dim_stock_tdx_industry` | 股票 TDX 三级行业分类 | ~5500 |
| `dim_trading_calendar` | 交易日历 | 969 |
| `dim_stock_stage_latest` | 股票最新阶段特征 (path_state / stage_score / volatility / stock_gate 等) | — |
| `dim_stock_turtle_latest` | 海龟执行特征 (ATR / entry_level / exit_level / 信号) | — |

### 3.3 fact 层 (事件流 + 特征面板)

| 表 | 说明 | 行数 |
|---|---|---|
| `fact_institution_event` | 机构买入/增持/减持/退出事件 (raw 差分) | 57k |
| `fact_executive_trade_event` | 高管交易事件 (带 EV, win rate 指标) | 17k |
| `fact_lhb_event` | 龙虎榜事件 | 52k |
| `fact_jgdy_event` | 机构调研事件 | 21k |
| `fact_dzjy_event` | 大宗交易事件 | 56k |
| `fact_fundamental_quarterly` | 季度基本面快照 | 64k |
| `fact_stock_stage_features` | 阶段特征历史 | 29k |
| `fact_stock_quality_features` | 质量特征历史 | 30k |
| `fact_stock_turtle_features` | 海龟特征历史 | 16k |
| `fact_regime_state` | 市场 regime 日级标签 (up/flat/down) | 775 |
| **`fact_feature_panel`** | **训练用特征面板** (stock × date × 43 列 + label) | **4.02M** |

### 3.4 mart 层 (业务产出)

| 表 | 说明 | 行数 |
|---|---|---|
| `mart_current_relationship` | 机构↔股票当前关系 (去重后) | 5.1k |
| `mart_stock_trend` | 股票列表主数据 (discovery/quality/stage score + 事件聚合) | 3.3k |
| `mart_institution_profile` | 机构画像 (EV% / 胜率 / 稳定性) | 231 |
| `mart_institution_industry_stat` | 机构分行业表现 | 6.7k |
| `mart_multidim_model` | 已训练模型元数据 (IC/RankIC/L-S/win rate) | 4 |
| `mart_multidim_prediction` | 模型历史日级预测 | 2.36M |
| `mart_daily_recommendation` | 每日 topK 推荐 (最新模型) | 150 (3 天 × 50) |

**mart_stock_trend 评分字段**:
- `discovery_score` — 机构发现层 (权重 35%)
- `company_quality_score` — 公司质量层 (权重 30%)
- `stage_score` — 买入阶段层 (权重 20%)
- `composite_priority_score` — 综合分 (归一化到 0-100)
- `priority_pool` — A/B/C/D 池

### 3.5 market.duckdb 关键表

| 表 | 说明 | 行数 |
|---|---|---|
| `price_kline_tdxhub` | 主 K 线源 (code, date, freq, adjust, ohlcv, amount) | 5.1M |
| `price_kline` | 备用 K 线 (akshare 源) | 4.7M |
| `price_xdxr` | 除权除息 | 154k |

### 3.6 etf.duckdb 关键表

| 表 | 说明 | 行数 |
|---|---|---|
| `etf_asset_universe` | ETF 主数据 (code, name, category) | 1472 |
| `etf_price_kline` | ETF 日线 | 929k |
| `mart_etf_snapshot_latest` | ETF 策略快照 (每只 ETF 推荐策略 + 步长) | 1472 |
| `mart_etf_sector_rotation` | 板块轮动快照 (sector × rotation_score + 龙头 ETF) | 20 |
| `mart_etf_strategy_comparison` | Grid vs Buy-hold 三周期对比 (1Y/3Y/5Y) | 7.8k |

### 3.7 alpha158.duckdb

| 表 | 说明 | 行数 |
|---|---|---|
| `fact_alpha158_panel` | 64 个 Alpha158 因子 (stock × date) | 4.02M |

**Alpha158 因子覆盖**: K 线形态 (kmid/klen/kup/klow/ksft) × 5/10/20/30/60 日 rolling (ma/std/max/min/rsv/qtl/cntp/sump/vma/vstd) + roc + corr(vol, price)。与主库通过 `ATTACH '...alpha158.duckdb' AS a158 (READ_ONLY)` + LEFT JOIN 集成, 不进主库。

---

## 4. 数据来源与更新链路

```
tdxhub (tdxhub fork)   → price_kline_tdxhub (日线、分钟线)
                       → financial raw (财务三张表 via python-tdxhub)
                       → fact_top10_holder_period + raw_tdx_f10_holder_research
                         (P7 替代 miaoxiang RPT_F10_EH_FREEHOLDERS, 99.6% 全市场覆盖)
akshare               → raw_lhb_daily / raw_executive_trade / raw_margin_daily
                       → raw_gpcw_* / raw_institution_surveys / raw_qfii_*
                       → raw_capital_* (分红/解禁/回购)
本地计算               → fact_institution_event (raw 差分)
                       → fact_*_event (高管/龙虎榜/调研/大宗)
                       → fact_feature_panel (43 特征 + regime + label)
                       → fact_alpha158_panel (64 因子, 独立库)
                       → mart_multidim_model (训练产出)
                       → mart_stock_trend (聚合打分)
```

**更新频率**:
- K 线 / 两融 / 龙虎榜: 日级
- 十大股东: 季度 (定期报告披露)
- 财务三张表: 季度
- 模型训练: 手动触发 (~4 小时全量)
- 日度 topK: 手动触发 `scripts/run_daily_topk.py` (几秒)

**数据源抽象**: `services/` 下有 `akshare_client.py` / `tdxhub_client.py` 等, `services/updater_chain.py` 负责编排管线。

**生产化约束 (2026-05-05 起)**:
- `mart_data_source_watermark` 按业务域记录主源/兜底源、`source_tier`、最新数据日、fallback 状态和失败计数；数据页直接读取该表展示源健康。
- `mart_pipeline_run_manifest` 记录训练、walk-forward、TopK、数据健康、source watermark 和 raw replay 的运行耗时、输入输出表、gate/blocker 和性能摘要。
- `build_price_kline_tdxhub.py --skip-existing` 按每只股票 `MAX(date)` 只写新增交易日，不再因为已有 code 就整只股票跳过。
- `sync_gpcw_files()` / `build_fundamental_quarterly.py` 写 `mart_tdx_gpcw_file_manifest`, 按 `filename/filesize` hash 跳过未变化 gpcw 文件, 并记录下载 sha256、解析状态、行数和错误；文件变化时只重建受影响报告期的 raw/detail/wide/auto-feature 切片。
- `ingest_holders_tdxhub.py --parse-raw-only [--replace-facts]` 可不联网重放 `raw_tdx_f10_holder_research.raw_text`，用于 parser 修复后重建 canonical 股东事实表。
- `services.duck_adapter.connect(..., timeout=N)` 遇到 DuckDB 文件锁会按 timeout 重试，并把 `duckdb_lock_wait_s` / `connect_mutex_wait_s` 写入 pipeline manifest 性能摘要。
- `start.command` 启动时只检查 akshare 本地版本；升级改为手动维护命令 `scripts/upgrade_akshare.sh`，避免生产启动被 pip/外网阻塞。
- 每日生产链路只跑增量数据、水位、champion TopK、健康和 outcome；全历史 Optuna/walk-forward/backtest 留给研究链路。

---

## 5. 模型逻辑 (lifecycle champion + challengers)

### 5.1 训练数据

- 生产特征面板: `fact_feature_panel`
- Alpha 实验面板: `alpha158.fact_alpha158_panel` 只在 `*_alpha158` / `legacy_full` 特征组显式启用
- 样本量: 约 **4.0M 行, 5.1k 只股票**
- 标签: `forward_ret_20d` — 20 交易日 forward return

### 5.2 特征工程 (production compact 54 列)

**43 基础特征** (fact_feature_panel):
- Pillar B 价量: ret_1d/5d/20d/60d, vol_z20d, ma_ratio_5/20/60/250, rz_balance, rz_chg_5d_pct
- Alpha158-inspired 子集: kmid/klen/kup/klow/ksft, vol_ratio_5_20, vol_std_5/20, range_pos_20/60, momentum_diff, amount_chg_5d
- Pillar A 事件: inst_event_count_30/60d, exec_buy_count_90d, exec_buy_ge1_count_90d, lhb_inst_buy_count_30/60d, jgdy_count_60d, dzjy_count_60d, days_since_exec_buy, days_since_lhb
- Pillar C 基本面: shareholder_count_qoq, inst_count_qoq, fund_count_qoq, qfii_count_qoq, yjyg_lower/upper_pct, roe, eps_basic
- Regime: hs300_ret_20d, hs300_ret_60d

**64 Alpha158 因子** (alpha158 库, 纯价量 rolling, 仅实验组):
- KBAR (9): k 线形态
- Price rolling (50+): ma/std/max/min/rsv/qtl/cntp/sump/roc × [5,10,20,30,60] 日
- Volume rolling (10): vma/vstd × [5,10,20,30,60]

**3 Regime one-hot**: 仅 `--regime-aware` 显式训练时加入

### 5.3 训练流程

```
fact_feature_panel (base/base_dense_v2 默认不加载 a158)
   ↓ split_time_series 70/15/15 date-ordered
   train (2023-01-03 ~ 2025-04-03)
   valid (2025-04-07 ~ 2025-09-22)
   holdout (2025-09-23 ~ 2026-03-24)
   ↓
Optuna trials 搜参 (LightGBM, objective=regression, metric=rmse)
  - num_leaves [15, 127]
  - learning_rate [0.01, 0.2]
  - min_data_in_leaf [50, 500]
  - feature_fraction / bagging_fraction [0.6, 1.0]
  - lambda_l1 / lambda_l2 [1e-4, 1.0]
  - max_depth [4, 10]
  - 目标: 最大化 valid RankIC
   ↓
best params 在 train+valid 合并上重训, holdout 评估
   ↓
写 mart_multidim_model (metadata)
写 mart_multidim_prediction (holdout 每日每股预测)
保存 data/multidim_models/{model_id}.pkl (LightGBM Booster)
```

训练主路径已从 `list[dict]` 改为 DuckDB `fetchnumpy()` + `PanelData` 列数组。2026-05-05 本地只读验证: `2023-01-01` ~ `2026-03-31` 共 3,910,880 行、54 特征, NumPy 加载 8.8s, 日期切分和三段 `float32` 矩阵构造 4.1s。holdout prediction 写库改为 NumPy temp view + `INSERT OR REPLACE ... SELECT`, 不再对 `mart_multidim_prediction` 逐行 `executemany`。

Walk-forward 主路径复用同一套 `PanelData` 数组，按 fold 日期边界取索引切片构造矩阵；prediction mode 保持 `metrics-only` / `topk` / `full` 分层，`mart_model_walkforward_prediction` 写库支持 NumPy temp view 批量插入。`mart_model_lifecycle` 汇总只有显式 `--update-lifecycle` 且所有 fold `quality=ok` 时才更新。

### 5.4 当前 lifecycle champion

`multidim_v2_base_dense_v2_20260425_144552` 是正式推荐 champion。`cleanup_full_multidim_v2_base_dense_v2_20260505_093800` 仍是 shadow challenger, 因 walk-forward 稳定性和 drift gate 未过, 不自动提升。

| 指标 | 值 | 说明 |
|---|---|---|
| IC (Pearson) | **0.0141** | 每日截面 IC 均值 |
| RankIC (Spearman) | **0.0374** | 排名相关性 |
| L-S spread | **1.02% / 20d** | holdout long-short spread |
| winrate top | **49.4%** | top decile winrate |
| 特征数 | **54** | base + dense_v2, 不含 Alpha158 |
| 推理耗时 | **约 1.3s / 4587 行** | 2026-05-05 manifest 记录 |

### 5.5 日度推理

`scripts/run_daily_topk.py`:
1. 默认读取 lifecycle champion, challenger 必须显式 `--mode shadow --model-id`
2. 加载 `{model_id}.pkl`
3. 从 `fact_feature_panel` 取最新一天行；仅模型 `feature_cols_json` 需要 `a158_*` 时 ATTACH Alpha158
4. `model.predict(X)` → 得分
5. 按 `pred_score` DESC 取 top-K, 写入前清理同日同模型旧快照
6. 写入 `mart_daily_recommendation` / `mart_daily_recommendation_risk` / `mart_pipeline_run_manifest`

---

## 6. 前端架构

### 6.1 技术栈

- 原生 HTML + Vanilla JS + CSS (无 React / Vue / 构建工具)
- 单页 `index.html` 动态注入所有 JS / CSS
- CSS 资源版本号 `CM_ASSET_VERSION` 手动 bump 作缓存 bust
- 无 emoji (已全局清理, 预留 lucide 图标位)

### 6.2 文件布局

```
index.html                          (417 行, 单页骨架)
assets/
├── css/
│   └── main.css                    (~6000 行, 统一 tokens + widget CSS)
└── js/
    ├── style-tokens.js             (CSS 变量 → JS CMTokens 桥)
    ├── app-cache.js                (API 缓存层)
    ├── app-nav.js                  (tab 导航)
    ├── app-list-state.js           (列表状态)
    ├── signal-adapter.js           (信号适配器)
    ├── stock-view.js               (股票视图 v2)
    ├── app.js                      (主逻辑 ~6800 行)
    └── widgets/
        ├── signal-params.js        (C6f 信号参数)
        ├── cohort-card.js          (Cohort 反馈)
        ├── backtest-panel.js       (历史回测)
        ├── screening-panel.js      (TDX 选股)
        ├── topk-strip.js           (多维量化 Top 20 条带)
        ├── multidim-badge.js       (股票详情 · 多维分徽章)
        ├── etf-sector-rotation.js  (ETF 板块轮动)
        ├── grid-optimizer.js       (ETF 网格自寻优)
        └── etf-strategy-compare.js (ETF Grid vs Buy-hold 3 周期对比)
```

### 6.3 Widget 契约

所有新 widget 都走统一 mount 接口:
```js
window.XxxWidget.mount(containerId, {
  /* widget-specific opts */
  onPick: function(id) { /* callback */ }
});
```
CSS 走 `var(--cm-*)` tokens, JS 需要色值时通过 `CMTokens.color('xxx')` 读。

### 6.4 配色 tokens (main.css :root)

两套灵感调色板 + 马卡龙奶油色合流:

```
品牌主色阶:
  --cm-brand-700  #112F4B  深墨蓝 (P2)
  --cm-brand-500  #60569A  紫 (P1, 主品牌)
  --cm-brand-400  #7AB2D4  天蓝 (P1)
  --cm-brand-50   #EEEBE6  奶白 (P1)
暖色强调:
  --cm-accent-warm   #EAABBC  粉玫瑰 (P1)
  --cm-accent-vivid  #6633BB  鲜紫 (P2)
自然色 (leaf):
  --cm-leaf-500  #0B9D6A  翠绿 (P2, = ok-500)
  --cm-leaf-300  #B3CEAB  sage (P2)
  --cm-leaf-100  #F1F1C6  浅米黄 (P2)
马卡龙奶油色 (卡片纯色底):
  --cm-macaron-cream #FEF6E6  奶油米
  --cm-macaron-mint  #E8F5E4  薄荷绿 (赢家)
  --cm-macaron-peach #FFE0D5  蜜桃粉 (警告)
  --cm-macaron-lilac #EEE5F5  雾紫 (强调)
  --cm-macaron-lemon #FDF6C8  柠檬黄 (高亮)
  --cm-macaron-sky   #E0F0F9  天蓝 (信息)
  --cm-macaron-rose  #FADDE7  玫瑰 (装饰)
语义色 (刚性, A 股约定):
  --stock-up   #ef4444  涨 (红)
  --stock-down #10b981  跌 (绿)
```

规则:
- 硬编码 hex 在 CSS/JS 里数量: CSS 42 (都在 :root 源头), JS 0
- 已弃用 `linear-gradient`, 只保留骨架屏 shimmer 动画
- 全局无 emoji (Unicode Emoji_Presentation = 0 残留)

### 6.5 主要视图

```
股东挖掘
├── 股票      (主入口) → stock-view.js + topk-strip widget + 详情 drawer
├── 机构      → 机构列表 + 机构管理
├── 模型监控   → mart_multidim_model 评级 + daily_series 图 + feature importance
└── 工作台     → 数据管线 / 策略参数 / 选股扫描 / ETF 网格自寻优 / 全量重算

ETF 研究
├── 工作台     → ETF 快照 + 数据同步 + 策略分布
├── 机会发现   → 市场判断 + 挖掘建议 + 板块轮动 widget
└── 全量筛选   → 表格 + 深度分析 (含策略对比 widget)
```

---

## 7. 后端架构

### 7.1 技术栈

- FastAPI + Uvicorn (端口 8002 for main, 8001 for worktree)
- DuckDB 作主数据库 (`services/duck_adapter.py` 提供轻量 DB-API 适配)
- Records/native Python + NumPy + LightGBM + Optuna + SciPy + scikit-learn
- 无 Celery/Redis/队列: 长任务走 nohup + 简单文件状态

### 7.2 文件布局

```
backend/
├── main.py                         (FastAPI entry, 路由注册)
├── requirements.txt                (lightgbm/optuna/duckdb 等核心依赖)
├── services/
│   ├── db.py                       (主连接 + init_db)
│   ├── duck_adapter.py             (DuckDB DB-API 适配层)
│   ├── market_db.py                (market.duckdb 连接)
│   ├── etf_db.py                   (etf.duckdb 连接)
│   ├── analytics.py                (OLAP 查询, ATTACH 多库)
│   ├── updater_chain.py            (数据管线编排)
│   ├── akshare_client.py / tdxhub_client.py  (外部数据源)
│   ├── etf_engine.py               (ETF 主引擎)
│   ├── etf_grid_engine.py          (网格回测 / _optimize_grid)
│   ├── etf_mining_engine.py        (ETF 挖掘)
│   ├── etf_snapshot_manager.py     (ETF 快照)
│   ├── scoring.py                  (股票综合分)
│   ├── stock_stage_engine.py       (阶段特征)
│   ├── stock_turtle_engine.py      (海龟特征)
│   ├── signals_v2.py               (极简跟随信号)
│   ├── stock_detail_read.py / stock_trends_read.py / industry_overview_read.py
│   └── ...
└── routers/
    ├── institution.py              (/api/inst/*, 机构/股票列表)
    ├── updater.py                  (/api/inst/update/*)
    ├── market.py                   (/api/inst/market/*)
    ├── screening.py                (/api/screening/*)
    ├── signals.py                  (/api/signals/*)
    ├── recommendation.py           (/api/rec/*, 模型监控 + 推荐)
    └── etf.py                      (/api/etf/*)
```

### 7.3 关键 API

| 端点 | 说明 |
|---|---|
| `/api/rec/daily-topk` | 每日 topK 推荐 (multidim_v1) |
| `/api/rec/stock-prediction` | 单股最新预测 |
| `/api/rec/model-performance` | 模型性能详情 (daily_series / regime_breakdown / feature_importance) |
| `/api/rec/model-history` | 历史训练模型列表 |
| `/api/etf/workbench` | ETF 工作台快照 |
| `/api/etf/opportunity` / `/api/etf/mining` | 机会发现 |
| `/api/etf/sector-rotation` | 板块轮动 |
| `/api/etf/grid/optimize` | 网格自寻优 (on-demand) |
| `/api/etf/strategy-comparison/{code}` | Grid vs Buy-hold 对比 |
| `/api/etf/analysis/{code}` | ETF 深度分析 |
| `/api/inst/institutions` / `/stocks` | 机构 / 股票列表 |
| `/api/inst/update/status` / `/start` | 数据管线控制 |
| `/api/screening/*` | TDX 选股扫描 |
| `/api/signals/*` | signals_v2 信号 |

---

## 8. 关键脚本

```
scripts/
├── run_full_pipeline.py            (一键跑完整管线)
├── build_feature_panel_duck.py     (DuckDB 版特征面板构建, <5min)
├── build_alpha158_duck.py          (Alpha158 因子库构建, <30s)
├── train_multidim_model.py         (LightGBM + Optuna 训练, 3-4 小时)
├── run_daily_topk.py               (日度推理, 几秒)
├── build_etf_sector_rotation.py    (板块轮动 mart, <5s)
├── backtest_etf_strategies.py      (ETF Grid vs Buy-hold 批量回测, ~1 分钟)
├── run_backtest.py                 (签名回测)
├── run_follow_backtest.py          (follow 策略回测)
└── build_akshare_panel.py          (akshare 因子面板)
```

---

## 9. 当前模型质量问题 (坦诚列表)

### 9.1 IC 偏弱
- 当前 IC = 0.0204, RankIC = 0.0363
- 对标: 机构可交易线 ≥ 0.03, 顶级公募因子 ≥ 0.05
- **结论**: 刚够学术发表, 实盘不够

### 9.2 winrate 低于 50%
- top-decile 胜率 49.1%, 意味着 top 内胜负五五开
- alpha 只来自非对称收益 (top-avg 2.11% vs bot-avg 0.92%)
- **结论**: 不能集中持仓, 必须 20+ 只分散, 才能兑现均值

### 9.3 holdout 样本不足
- 单次 holdout 只有 2025-09 ~ 2026-03, 约 6 个月、~120 交易日
- IC 0.02 的统计显著性 p ≈ 0.08, 不能拒绝零假设
- **结论**: 需 walk-forward 框架; 当前 baseline IC 有 overfit 嫌疑

### 9.4 没有交易成本建模
- L-S spread 1.19% / 20d 是理论值
- 换手成本 双边 0.2-0.3% × 每月调仓 → 年化吃掉 5-8%
- 冲击成本 小市值 1-2% / 年
- A 股不能做空, L-S 一半做不到
- **结论**: 实盘净 spread 大概率 < 10% 年化, 甚至跑输指数

### 9.5 底部 decile "不够烂"
- bot-avg +0.92% 而不是明显负值
- 说明模型对"会跌的股票"识别能力差
- 可能原因: 训练期是震荡市, 没有明显下跌股; 或者特征里对"风险"的表达不足

### 9.6 regime 分段表现未验证
- 只训了单一模型, 没有 up / flat / down 市场的 conditional IC
- 风格切换时模型可能完全失效

### 9.7 特征层面
- 缺龙虎榜营业部实盘买入行为解析 (只有 count, 没有营业部质量评分)
- 缺融资余额变化的细粒度特征 (只有 rz_balance + 5d_pct, 缺加速度)
- 缺宏观 / 利率 / 板块资金轮动
- 缺分析师预期数据 (盈利上调/下调)
- Alpha158 全是纯价量, 没有基本面拐点因子

---

## 10. 当前数据质量 / 数量问题

### 10.1 数据深度
- 训练从 2023-01-01 起, **只有 2.5 年历史**, 穿越的市场周期少
- hs300 regime 标签仅 775 天, 无法充分训练 regime-conditional

### 10.2 数据稀疏性
- Alpha158 覆盖 4.02M 行但和 fact_feature_panel 完全对齐 (LEFT JOIN 100%)
- 但 fact_fundamental_quarterly 63k 行对应 5500 股 × 10 季 ≈ 55k 理论, 实际 63k 说明有些重复/迟报
- exec_buy / lhb / jgdy / dzjy 事件计数特征在很多股票上为 0 (事件稀疏)

### 10.3 数据不一致
- `mart_raw_holdings` 719k 行但只差分出 57k 机构事件, 说明差分逻辑可能漏
- `inst_institutions` 仅 240 家被跟踪机构, 全市场实际机构数应在 1000+
- `mart_current_relationship` 5.1k 关系, 对应 5500 股 × 平均持股机构数估计偏低

### 10.4 数据新鲜度
- K 线 / 两融 能做到 T+1
- 十大股东必须等季报发布 (披露延迟)
- 财务报表 T+45 到 T+90 (最晚)
- 导致 forward 20d 训练标签的最新训练截止日永远是 T-20 左右

### 10.5 数据陷阱 (已知)
- `rz_balance` 原先 100% NULL, 是两融日期格式 bug, 现已修 (Phase G)
- pre-2023 数据已全量物理删除 (用户明确要求 "直接清空")
- 旧预测/外部框架子系统及其 mart 表已清理；当前生产链路只保留 DuckDB + LightGBM/规则引擎
- 2026-04-25 复核: smart/etf 库中无旧预测/实验框架残留表

### 10.6 量级问题
- `fact_feature_panel` 4M 行其实很小, LightGBM 能轻松吃到 40M
- 但扩数据意味着往前到 2020 年, 用户已否决 (风格变了)
- 横向扩特征是主要突破口

---

## 11. 旧预测框架清理后的系统变更 (2026-04-24 / 2026-04-25 复核)

- 删除旧外部预测子系统及其路由、数据处理器、ETF 预测引擎
- 删除 `stock_forecast_engine.py` / `sector_forecast_engine.py`
- DROP 旧预测表与空壳 ETF 预测表；2026-04-25 复核 smart/etf 库均无残留表
- `mart_stock_trend` 删旧预测排名/分位列与 forecast 派生列
- 依赖收敛到 optuna + duckdb + scipy + scikit-learn；生产路径不再依赖外部预测框架
- 评分体系从 4 档 (discovery/quality/stage/forecast) 降到 3 档 (weights 35/30/20)
- 全局无 emoji (旧图标和符号已清理)
- 配色 tokens 换为 2 palette + 马卡龙纯色 (去 linear-gradient)

---

## 12. 运行方式

```bash
# 1) 启动后端
cd backend
python3 -m uvicorn main:app --host 127.0.0.1 --port 8002

# 2) 启动前端代理 (预览)
node .claude/preview-server.js   # 默认代理到 127.0.0.1:8002, 监听 8080

# 3) 全量数据管线 (手动)
python3 scripts/run_full_pipeline.py

# 4) 训练新模型 (~4 小时)
python3 scripts/train_multidim_model.py --start 2023-01-01 --trials 50 --regime-aware

# 5) 跑日度推理 (训练完后)
python3 scripts/run_daily_topk.py --top-k 50

# 6) ETF 板块轮动 + 策略对比 (每日可跑)
python3 scripts/build_etf_sector_rotation.py
python3 scripts/backtest_etf_strategies.py
```

---

## 13. 最小持续交付单元 (给 Codex 做事的心智模型)

做改动时建议遵循:

1. 新 mart 表 → 先在 `services/db.py` 的 `init_db` 或独立脚本里 DDL
2. 新 API → 放 `backend/routers/<domain>.py`, 不污染老 router
3. 新 widget → 走 `widgets/<name>.js` + `mount(id, opts)` 契约
4. 新色 → 只加到 `:root` 里的 `--cm-*` token, 业务代码只引用 var
5. 每次改 CSS/JS → bump `CM_ASSET_VERSION`
6. 不引入渐变 / emoji / 硬编码 hex
7. 训练脚本必须分两阶段释放 DuckDB 写锁 (见 `train_multidim_model.py` 范例)

**不要做的事**:
- 不要再引入外部预测框架 / 任何 AI 包装品牌语
- 不要用 linear-gradient (除骨架屏 shimmer)
- 不要硬编码颜色 (走 `var(--cm-*)` 或 `CMTokens.color('xxx')`)
- 不要写 `AI 分析 / AI 推荐 / 智能选股` 等误导性词 (用户明确拒绝)
