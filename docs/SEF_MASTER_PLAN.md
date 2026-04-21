# Signal Evolution Framework (SEF) · 机构跟投系统重构主计划

> 2026-04-21 制定 · 基于 session 全部讨论 + 业界最佳实践
> 这份文档是**自包含的**：下一轮对话即使 context 被压缩也能接着做

---

## 0 · 为什么要重构（一句话总结）

当前系统（V7）是**股票打分机**，跟投者拿到 A 池 291 只股票自选。
目标架构（SEF）是**每日组合生成器**，直接输出 `{stock: weight}` + 风险约束。
中间差了五个架构层级：**α 衰减建模 / 股性响应 / 贝叶斯共识 / 组合优化 / 探索机制**。

---

## 1 · 本 session 已达成的 7 个关键共识

| # | 共识 | 决策依据 |
|---|---|---|
| 1 | **行业源回到通达信 TDX** | SW L2 131 组过细（44% 组 <100 样本），TDX L2 56 组 稳定性全面胜出 |
| 2 | **L3 不用于建模** | L3 极差 151%，72% 组 <100 样本，噪音主导 |
| 3 | **L2 是最佳建模粒度** | 56 组，只 16% 组 <100 样本，极差可控 |
| 4 | **chain PnL 是真相，gain_60d 是采样偏差** | 15009 chain 全量可用（1131 closed + 13878 open 用浮动盈亏） |
| 5 | **预测不追 IC，归因 + 分层决策** | 之前 Qlib IC=-0.02 失败经验 |
| 6 | **机构退出后不算完整周期，机构 α 和跟投者 α 要分开** | `chain_inst_pnl` vs `chain_follow_pnl` 两个口径 |
| 7 | **Walk-Forward Rolling + Online Bayesian 是训练范式** | 单点 train-valid-test 对 A 股 non-stationary 不够 |

---

## 2 · 业界最佳实践（Lopez de Prado + DGTW + Sharpe 等）

本系统可借鉴的 **10 个被证明有效的技术**（按价值排序）：

### 2.1 Triple Barrier Method · Lopez de Prado 2018
- **替代**：`gain_60d` 固定时间收益
- **做法**：同时设 upper / lower / time barrier，label = 哪个 barrier 先触到
- **价值**：更符合真实交易（止损止盈 + 持有上限）
- **在 SEF 里的位置**：Layer 1 label 生成
- 参考：`Advances in Financial Machine Learning` Ch. 3

### 2.2 Meta-Labeling · Lopez de Prado 2018
- **做法**：不学"涨跌预测"，学"信号质量预测"
  - Primary Model（例如当前 V6 硬规则）→ 粗粒度筛选
  - Meta Model → 学"V6 命中的信号里哪些真赚钱"
- **价值**：解决小样本 + 高精度需求的 trade-off
- **在 SEF 里的位置**：Layer 3 贝叶斯更新的补充
- **A 股实测有效**：2023 年多篇中文券商研报确认

### 2.3 Purged K-Fold + Embargo · Lopez de Prado 2018
- **解决**：金融数据重叠（同一 chain 跨 train/test）
- **做法**：
  - Purged: 训练集删除那些 label 会延伸到测试集的样本
  - Embargo: train/test 之间留 N=5-10 天 buffer
- **在 SEF 里的位置**：所有训练阶段的 CV 策略
- **不做后果**：IC 严重虚高（论文 level）

### 2.4 Combinatorial Purged CV (CPCV) · Lopez de Prado 2018
- **比 walk-forward 更严格**
- 评估"多条历史路径"下的鲁棒性
- 阶段 III 可选，工程实现复杂

### 2.5 Fractional Differentiation
- **问题**：价格对数收益丢失记忆；价格本身非平稳
- **做法**：`d` 阶分数差分，保留记忆同时获得平稳性
- **价值**：LightGBM 等非时序模型可以吃价格特征
- **在 SEF 里的位置**：Alpha158 因子生成时可选

### 2.6 DGTW 归因 · Daniel-Grinblatt-Titman-Wermers 1997
- **机构 α 经典分解**：Selection + Timing + Style
- **做法**：
  - Selection α = 机构持仓 vs 同风格基准的 return 差
  - Timing α = 加/减仓时机 vs 被动持有的 return 差
  - Style α = 风格暴露贡献
- **价值**：比 SHAP 更有经济意义
- **在 SEF 里的位置**：Layer 1 机构能力归因
- 论文：*Measuring Mutual Fund Performance with Characteristic-Based Benchmarks*

### 2.7 Returns-Based Style Analysis · Sharpe 1992
- **做法**：机构 chain 收益对行业指数做约束回归
  - `r_inst,t = Σ w_i × r_sector_i,t + α + ε`, s.t. `Σw=1, w≥0`
- **价值**：机构风格暴露向量（13 个 TDX L1 系数）
- **替代**：当前 main_industry 按计数，不反映真实 exposure
- **在 SEF 里的位置**：Layer 1 风格画像

### 2.8 Black-Litterman Portfolio Optimization
- **比 Mean-Variance 更稳健**
- **做法**：
  - 先验：市场均衡权重（反推自当前市值）
  - 信号作为 view：`P × μ = Q ± Ω`
  - 后验：结合先验和 view
- **价值**：跟投信号少/不确定时，后验自动贴近市场基准，不会生成极端组合
- **在 SEF 里的位置**：Layer 4 组合优化首选

### 2.9 Information Ratio · IR = IC / √(turnover × N)
- **替代**：纯 IC 或纯胜率
- **直觉**：IC 高但换手高也没用，被手续费磨光
- **A 股**：单边 0.12% 佣金 + 0.05% 印花税（卖方）
- **在 SEF 里的位置**：Layer 6 评估指标

### 2.10 Hidden Markov Model Regime Detection
- **A 股实测**：平均 regime 持续 ~9 个月
- **做法**：2-3 state HMM over 日收益 + 波动率
- **价值**：不同 regime 下 α 来源不同，模型选择要分 regime
- **在 SEF 里的位置**：Layer 0 底层特征

---

## 3 · SEF 完整架构（6 层）

```
Layer 0 · Data Lake (已有)
   fact_institution_event / price_kline / raw_gpcw_detail
   raw_margin_balance (待补) / raw_institution_surveys
   + HMM regime_state (新)

Layer 1 · Alpha Decay Survival Model
   对 (inst_id, industry_code) 学 α 衰减曲线
   method: Cox hazard regression + Weibull fit
   input: chain 级完整 PnL 时间序列（15009 chain 全量）
   output: τ*(inst, industry) = 最佳跟投持有期 + 衰减半衰期
   sampling: Triple Barrier labels
   CV: Purged K-Fold with 5 day embargo

Layer 2 · Stock Factor Response + Sharpe Style Analysis
   part A · Stock Idiosyncratic Beta:
     股票对 10 类事件的 β 向量（20 维嵌入）
     method: Event Study CAR + FF multi-factor control
   part B · Institution Style Exposure (Sharpe 1992):
     对每个机构做约束回归 → 13 维行业风格暴露
     method: Constrained quadratic programming
   frequency: monthly retrain

Layer 3 · Bayesian Signal Updater
   多机构持股 → 后验 α 分布
   method:
     prior α_ij ~ N(μ_ind, σ²_ind)
     likelihood: 每个机构 signal × 其 DGTW Selection α × 时效衰减
     posterior α_ij ~ N(μ', σ'²)
   online update: 每天 realized PnL → 更新 sufficient statistics
   Meta-Labeling: Primary (V6 硬规则) + Meta (贝叶斯后验信号质量)

Layer 4 · Portfolio Optimizer (Black-Litterman)
   input:
     Π (先验): 市值加权市场组合
     P (view matrix): 跟投信号 stock 选择
     Q (view alpha): L3 后验 μ' ⊙ L1 decay ⊙ L2 fit
     Ω (view uncertainty): L3 后验 σ'²
   constraints:
     β ∈ [0.7, 1.3]
     MaxDD 预期 < 15%
     sector ≤ 25%, 个股 ≤ 10%
     N_holdings ∈ [10, 30]
     turnover ≤ 30%/month
   output: daily weight vector w*

Layer 5 · Exploration Bandit (Thompson Sampling)
   预算: 10-20% 仓位给"先验不确定但潜力高"的新机构
   method: Beta-Binomial Thompson Sampling
     每机构每季度 Beta(α + wins, β + losses)
     抽样选 signal
   防止: 系统老龄化（只用历史最强 N 机构）

Layer 6 · Online Feedback + Counterfactual Eval
   每日: realized PnL 更新到 model_signals_realized
   每周: rolling IC / IR / Sharpe / MaxDD
   每月: 重训触发条件
     - IC < 0.01 连续 2 周 → 紧急重训
     - PSI > 0.25 → 全局重训
     - HMM regime 变化 → 重训 + prior reset
   每季度: Counterfactual eval
     (SEF 策略 PnL) vs (无 SEF 的 V6 硬规则 PnL)
     PSM 匹配或 Synthetic Control
   每年: 架构复盘 + 新因子入库
```

---

## 4 · 训练调度细节（Walk-Forward + Online）

### 4.1 频率矩阵

| 层 | 训练类型 | 频率 | 数据窗口 |
|---|---|---|---|
| L1 Cox | Full retrain | 月度 | Exponential weighted, half-life 6 months |
| L2 Stock β | Full retrain | 季度 | 过去 24 个月 |
| L2 Inst Style | Full retrain | 季度 | 过去 24 个月 |
| L3 Bayesian | Online update | 每日 | Sufficient statistics 累加 |
| L4 Portfolio | 不需训练 | 每日求解 | 当日快照 |
| L5 Bandit | Online update | 每日 | Beta 分布增量 |
| HMM Regime | Full retrain | 月度 | 全样本 |

### 4.2 Walk-Forward 外层循环

```python
for month_end in months_from_2023_to_now:
    train_window = [month_end - 60m, month_end]
    sample_weights = exp(-0.5 * years_since / 0.5)  # 6m half-life
    
    # Inner Purged K-Fold for hyperparameter
    with PurgedKFold(n_splits=5, embargo_days=5):
        best_params = grid_search(train_window)
    
    model_v = train(train_window, best_weights, best_params)
    
    # A/B parallel for 1 week
    parallel_run(model_v, model_{v-1})
    select_winner_by_OOS_IR()
    
    save_model(model_v, metadata)
```

### 4.3 Drift Monitor（每周触发）

```python
for inst_id in active_institutions:
    recent_6m = get_chain_alpha(inst_id, -6m)
    historical_24m = get_chain_alpha(inst_id, -24m, -6m)
    
    ks_p = ks_2samp(recent_6m, historical_24m).pvalue
    psi = population_stability_index(recent_6m, historical_24m, bins=10)
    
    if psi > 0.25 or ks_p < 0.05:
        confidence_mult[inst_id] = 0.3  # 显著漂移
    elif psi > 0.10:
        confidence_mult[inst_id] = 0.7  # 轻微漂移
    else:
        confidence_mult[inst_id] = 1.0  # 稳定

# 应用到 Layer 3 先验
# μ'_ij 按 confidence_mult[inst_id] 缩放
```

### 4.4 A 股特有约束

- **T+1 限制**：Layer 4 turnover ≤ 30%/month（否则换仓成本吃掉 α）
- **涨跌停**：Layer 4 个股权重 ≤ 10%，涨停股当日不调仓
- **手续费**：单边 0.12% + 印花税 0.05%（卖方），grossalpha 必扣成本
- **流通股 vs 总股本**：hold_ratio 用流通股比例（当前口径正确）
- **幸存者偏差**：dim_active_a_stock 只有活股，训练时要用 dim_all_ever_listed（**要补表**）

---

## 5 · 数据表 Schema（新增 / 改造）

### 5.1 新增表

```sql
-- α 真相层
CREATE TABLE fact_chain_alpha_truth (
    chain_id              INTEGER PRIMARY KEY,
    institution_id        TEXT NOT NULL,
    stock_code            TEXT NOT NULL,
    entry_date            TEXT NOT NULL,
    exit_date             TEXT,  -- NULL = open
    eval_date             TEXT NOT NULL,  -- closed:exit_date; open:today
    status                TEXT NOT NULL,  -- 'closed' | 'open'
    
    chain_inst_pnl        REAL,  -- 机构视角 PnL
    chain_follow_pnl      REAL,  -- 跟投者视角 PnL (price_entry → price_eval)
    chain_follow_max_dd   REAL,  -- 跟投期最大回撤
    chain_days            INTEGER,
    
    -- Triple Barrier labels
    tb_upper_hit          INTEGER DEFAULT 0,  -- 1=touched
    tb_lower_hit          INTEGER DEFAULT 0,
    tb_time_hit           INTEGER DEFAULT 0,
    tb_label              TEXT,  -- 'upper' | 'lower' | 'time'
    tb_upper_level        REAL,  -- 止盈阈值 (e.g. 2×ATR)
    tb_lower_level        REAL,  -- 止损阈值
    tb_time_horizon_days  INTEGER,
    
    -- DGTW 归因
    dgtw_selection_alpha  REAL,
    dgtw_timing_alpha     REAL,
    dgtw_style_alpha      REAL,
    
    updated_at            TEXT
);

-- 机构能力（Layer 1 输出）
CREATE TABLE mart_institution_capability (
    institution_id        TEXT,
    industry_level        TEXT,  -- 'L1' | 'L2'
    industry_code         TEXT,
    
    alpha_median          REAL,
    alpha_se              REAL,
    alpha_ci_lower_90     REAL,
    sample_count          INTEGER,
    sharpe                REAL,
    max_dd_median         REAL,
    
    expert_level          INTEGER,  -- 0/1/2/3 (L1/L2/L3 擅长)
    
    -- Cox Survival 输出
    alpha_halflife_days   REAL,  -- α 半衰期
    alpha_decay_tau_star  INTEGER,  -- 最佳跟投期
    
    last_updated          TEXT,
    PRIMARY KEY (institution_id, industry_level, industry_code)
);

-- 机构风格（Layer 2B Sharpe Style Analysis）
CREATE TABLE mart_institution_style (
    institution_id        TEXT PRIMARY KEY,
    style_exposure_json   TEXT,  -- {"T01": 0.12, "T02": 0.08, ...} 13 L1 系数
    style_alpha_pure      REAL,  -- Sharpe Style Analysis 的纯 α
    style_r2              REAL,  -- 回归 R²
    drift_flag            INTEGER,  -- 1 = 近期风格漂移
    drift_psi             REAL,
    drift_ks_pvalue       REAL,
    last_updated          TEXT
);

-- 股性特征（Layer 2A）
CREATE TABLE fact_stock_character (
    stock_code            TEXT PRIMARY KEY,
    embedding_json        TEXT,  -- 20 维
    beta_inst_entry       REAL,  -- 机构入场的股价弹性
    beta_holder_decline   REAL,
    beta_margin_surge     REAL,
    beta_survey_surge     REAL,
    beta_northbound_in    REAL,
    noise_floor           REAL,
    info_lag_days         REAL,
    elasticity_sector     REAL,
    last_updated          TEXT
);

-- 信号日志（Layer 6 闭环）
CREATE TABLE model_signals_log (
    signal_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date           TEXT NOT NULL,
    stock_code            TEXT NOT NULL,
    institution_id        TEXT,
    source                TEXT,  -- 'inst_event' | 'holder_change' | 'margin_surge' | ...
    
    predicted_alpha       REAL,
    predicted_sigma       REAL,  -- uncertainty
    predicted_holddays    INTEGER,  -- 最佳持有期
    confidence            REAL,
    model_version         TEXT,
    feature_snapshot_json TEXT,
    
    recommended_weight    REAL,  -- Layer 4 组合权重
    tag                   TEXT,  -- 'follow' | 'watch' | 'observe' | 'avoid'
    
    INDEX idx_date (signal_date),
    INDEX idx_stock (stock_code, signal_date)
);

CREATE TABLE model_signals_realized (
    signal_id             INTEGER PRIMARY KEY,  -- FK to model_signals_log
    realized_pnl_1d       REAL,
    realized_pnl_5d       REAL,
    realized_pnl_20d      REAL,
    realized_pnl_60d      REAL,
    realized_pnl_to_now   REAL,
    realized_maxdd_to_now REAL,
    exit_trigger          TEXT,  -- 'upper_barrier' | 'lower_barrier' | 'time' | 'open'
    closed                INTEGER DEFAULT 0,
    last_updated          TEXT
);

-- 模型版本
CREATE TABLE model_state (
    model_id              TEXT PRIMARY KEY,
    model_type            TEXT,  -- 'L1_cox' | 'L2_stock' | 'L2_style' | 'L3_bayes'
    version               INTEGER,
    train_start           TEXT,
    train_end             TEXT,
    train_samples         INTEGER,
    hyperparams_json      TEXT,
    valid_ic              REAL,
    valid_ir              REAL,
    valid_sharpe          REAL,
    status                TEXT,  -- 'training' | 'active' | 'shadow' | 'retired'
    model_path            TEXT,
    created_at            TEXT,
    activated_at          TEXT
);

-- Regime 状态
CREATE TABLE fact_regime_state (
    trade_date            TEXT PRIMARY KEY,
    regime_id             INTEGER,  -- 0 / 1 / 2
    regime_label          TEXT,  -- 'bull' | 'bear' | 'sideways'
    regime_prob_json      TEXT,  -- [0.8, 0.15, 0.05]
    transition_signal     INTEGER DEFAULT 0  -- 1 = 高转移概率警告
);

-- 漂移日志
CREATE TABLE institution_drift_log (
    institution_id        TEXT,
    eval_date             TEXT,
    psi                   REAL,
    ks_pvalue             REAL,
    confidence_mult       REAL,
    alert_level           TEXT,  -- 'stable' | 'mild' | 'severe'
    PRIMARY KEY (institution_id, eval_date)
);

-- Walk-Forward 回测结果
CREATE TABLE backtest_walk_forward (
    model_id              TEXT,
    fold_id               INTEGER,
    fold_start            TEXT,
    fold_end              TEXT,
    n_samples             INTEGER,
    oos_ic                REAL,
    oos_rank_ic           REAL,
    oos_sharpe            REAL,
    oos_maxdd             REAL,
    oos_hit_rate          REAL,
    oos_turnover          REAL,
    oos_ir                REAL,  -- IC / sqrt(turnover)
    PRIMARY KEY (model_id, fold_id)
);

-- 组合输出（Layer 4）
CREATE TABLE portfolio_recommendation_daily (
    signal_date           TEXT,
    stock_code            TEXT,
    weight                REAL,
    expected_alpha        REAL,
    expected_sigma        REAL,
    sector                TEXT,
    rationale_json        TEXT,
    PRIMARY KEY (signal_date, stock_code)
);
```

### 5.2 已有表扩列

```sql
-- fact_institution_event 扩列（Phase A）
ALTER TABLE fact_institution_event ADD COLUMN chain_id INTEGER;  -- 关联 fact_chain_alpha_truth
ALTER TABLE fact_institution_event ADD COLUMN follow_pnl_to_eval REAL;
ALTER TABLE fact_institution_event ADD COLUMN follow_maxdd_to_eval REAL;
ALTER TABLE fact_institution_event ADD COLUMN inst_pnl_to_eval REAL;
ALTER TABLE fact_institution_event ADD COLUMN eval_status TEXT;  -- 'closed' | 'open'

-- research_holding_chains 扩列（Phase B）
ALTER TABLE research_holding_chains ADD COLUMN chain_alpha REAL;  -- DGTW α
ALTER TABLE research_holding_chains ADD COLUMN chain_industry_beta REAL;
ALTER TABLE research_holding_chains ADD COLUMN chain_style_beta_json TEXT;  -- 13-dim
ALTER TABLE research_holding_chains ADD COLUMN chain_top_factors_json TEXT;  -- SHAP top 10
ALTER TABLE research_holding_chains ADD COLUMN alpha_halflife_days REAL;
```

---

## 6 · 实施路线图（26 天）

### Phase I · Foundation（6 天）

**交付物**：
- `fact_institution_event` 扩列 + open chain PnL 回填脚本
- `fact_chain_alpha_truth` 表 + Triple Barrier labels 计算
- Qlib 环境 + Alpha158 批量生成入库
- `model_signals_log` / `_realized` / `model_state` 空表建好
- `dim_all_ever_listed` 增量（含退市股）

**D1-D2**：扩列 + SQL 回填（不需 Qlib）
**D3-D4**：Qlib Alpha158 + `qlib_data` 完整度验证
**D5**：Triple Barrier label 生成（upper=2×ATR, lower=1×ATR, time=120d）
**D6**：Purged K-Fold + Embargo 工具函数

**测试 KPI**：
- chain_follow_pnl 回填后，closed chain 数据与 research_holding_chains 完全吻合
- Alpha158 对全 4500 股票生成率 > 95%
- Triple Barrier 触发分布合理（upper:lower:time 不应极度偏斜）

### Phase II · Attribution Models（7 天）

**交付物**：
- Layer 1 Cox 生存模型（L1/L2 级机构能力 + α 衰减）
- Layer 2A 股性回归（20 维嵌入）
- Layer 2B Sharpe Style Analysis（机构风格暴露）
- `mart_institution_capability` / `mart_institution_style` / `fact_stock_character` 填充
- HMM regime detection 基线

**D7-D9**：Cox 生存模型（Lifelines 库 + 自定义 loss）
**D10-D11**：股性 Event Study + CAR 面板回归
**D12-D13**：Sharpe Style Analysis（约束 QP，用 cvxpy）

**测试 KPI**：
- L2 级 500+ 机构有效擅长标签
- Cox 模型 AIC 好于 exponential baseline
- 股性 20 维嵌入通过 PCA 检查，无 collinearity

### Phase III · Bayesian + Portfolio（8 天）

**交付物**：
- Layer 3 Bayesian Updater（每日增量 + 先验初始化）
- Meta-Labeling 模型（V6 Primary + Meta 层）
- Layer 4 Black-Litterman Portfolio Optimizer
- `portfolio_recommendation_daily` 每日填充
- Qlib backtest 对比基线

**D14-D15**：贝叶斯先验初始化 + 共轭更新管道
**D16-D17**：Meta-Labeling（V6 signals → 贝叶斯后验 → Meta model）
**D18-D20**：Black-Litterman 实现（cvxpy + Qlib view 转换）
**D21**：Qlib `risk_analysis` 集成

**测试 KPI**：
- Black-Litterman 组合 ex-ante Sharpe > 1.2（回测历史）
- Meta-Labeling 比单纯 V6 硬规则 IR 提升 >20%
- 每日组合 turnover < 20%

### Phase IV · Closure + Production（5 天）

**交付物**：
- Layer 5 Thompson Sampling 探索机制
- Layer 6 闭环 cron jobs + Counterfactual evaluation
- Walk-Forward Rolling 训练调度器
- Institution Drift Monitor
- UI 归因面板（选做）

**D22-D23**：Thompson Sampling + exploration 预算分配
**D24**：每日/每周/每月 cron 脚本 + `model_state` 版本管理
**D25**：Drift Monitor + PSI / KS 计算
**D26**：Counterfactual eval 框架 + Dashboard

**测试 KPI**：
- 冷启动 10 个新机构 signal，3 个月后有 2+ 被 Thompson Sampling 升权
- Counterfactual eval 显示 SEF 策略 PnL > V6 基线 > 15%
- Drift alert 准确率（人工抽样）> 70%

---

## 7 · MVP 路径（如果时间/精力有限）

**最小可用 SEF**（10 天，交付"每日组合建议"）：

1. Phase I 前 4 天（chain 真相 + Qlib 因子）
2. Phase II 的 Cox 模型（2 天）—— 只做 L2，不做 L1
3. 简化 Layer 3（当前 quality_score 当 μ 先验，不训贝叶斯）
4. Phase III 的 Black-Litterman（3 天）—— 用简化 μ 和 cov
5. 1 天集成测试

**MVP 输出**：每日 `portfolio_recommendation_daily` 有 20-30 只股票 + 权重。

**放弃**：股性、贝叶斯后验、探索机制、Meta-Labeling

**升级路径**：MVP 跑通后，按 Phase II→III→IV 逐层替换精细实现。

---

## 8 · 风险点与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Layer 1 Cox 样本不足 | 中 | 高 | L2 级 sample_count ≥ 20，不够合并到 L1 |
| Alpha158 覆盖率低 | 低 | 高 | 先验证 qlib_data 完整度，缺失用 Alpha61 回退 |
| Meta-Labeling 过拟合 | 中 | 中 | 严格 Purged K-Fold + Embargo + 每月 drift check |
| Black-Litterman 输入 μ 不稳定 | 中 | 高 | shrinkage 到 market prior，`τ=0.05` |
| T+1 约束让 turnover 超预算 | 高 | 中 | 持仓期 ≥ 5 天硬约束 |
| 退市股幸存者偏差 | 高 | 中 | Phase I 必须补 dim_all_ever_listed |
| Thompson Sampling 前期亏损 | 中 | 中 | 探索预算上限 15%，新机构需 prior ≥ L1 同行业中位数 |
| 模型 drift 未及时发现 | 中 | 高 | PSI > 0.25 自动触发 + 人工审查 |

---

## 9 · KPI（分阶段成功标准）

### Phase I 成功
- ✓ 15009 chain 全量有 `chain_follow_pnl` / `chain_follow_maxdd`
- ✓ Alpha158 覆盖率 > 95%
- ✓ 全部新表创建 + 空数据写入测试通过

### Phase II 成功
- ✓ Layer 1 Cox 模型 AIC 显著好于指数基线
- ✓ 至少 100 个机构有 L2 级擅长标签（`expert_level >= 2`）
- ✓ 至少 50 个机构有显著 `alpha_halflife_days`

### Phase III 成功
- ✓ 每日 `portfolio_recommendation_daily` 输出 10-30 只股票
- ✓ 历史 12 个月回测 Sharpe > 1.2（vs 沪深 300 基准）
- ✓ Max Drawdown 预期 < 15%（实际 < 20%）
- ✓ Meta-Labeling 相对 V6 硬规则 IR 提升 > 20%

### Phase IV 成功
- ✓ Walk-Forward 外层 10 fold，OOS IC 中位数 > 0.02
- ✓ 3 个月上线后 Counterfactual eval: SEF vs V6 收益差 > 15%
- ✓ 所有 cron 任务稳定运行 30 天无中断

### 最终生产指标（6 个月后）
- 跟投组合 **Sharpe > 1.5**
- 相对沪深 300 年化超额 > **10%**
- Max Drawdown < **15%**
- 月度换手 **< 30%**
- Information Ratio > **0.5**

---

## 10 · 下一轮对话 Onboarding（必读）

**如果你是新 claude 或 context 被压缩后接着做**，读完这 5 条：

1. **当前状态**：main 分支已回滚到通达信 TDX 方向（commit `f71b34a6`），`dim_stock_tdx_industry` 有 5604 行，派生表跑完全链。

2. **下一步动作**：按 Phase I D1 开始，先扩 `fact_institution_event` 列 + 回填 `chain_follow_pnl`。

3. **必读文件**（按顺序）：
   - 本文档（`docs/SEF_MASTER_PLAN.md`）
   - `docs/HANDOFF.md`（了解历史）
   - `backend/services/signals_v2.py`（理解当前决策逻辑）
   - `backend/services/backtest_engine.py::build_holding_chains`（链的数据结构）

4. **环境就位**：
   - `data/` → 软链到 `/Users/dp/Documents/M/stock/data`
   - Python 3.9，Qlib 已装
   - 启动：`python3 -m uvicorn main:app --port 8001` (worktree)
   - 测试：`cd backend && python3 -m pytest -q`

5. **不要做的事**：
   - 不要把行业源切回申万（已决策用 TDX L2）
   - 不要用 L3 做建模（噪音太大）
   - 不要追求单一 IC 指标（多指标 IR + Sharpe）
   - 不要碰 `cooldown_days=90`（防 look-ahead 底线）

---

## 11 · 参考文献

- Lopez de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
  - Triple Barrier, Meta-Labeling, Purged K-Fold, CPCV, Fractional Diff
- Daniel, K., Grinblatt, M., Titman, S., Wermers, R. (1997). *Measuring Mutual Fund Performance with Characteristic-Based Benchmarks*. Journal of Finance.
- Sharpe, W. (1992). *Asset Allocation: Management Style and Performance Measurement*.
- Black, F., Litterman, R. (1992). *Global Portfolio Optimization*. Financial Analysts Journal.
- Cohen, R., Polk, C., Silli, B. (2010). *Best Ideas*.
- Wermers, R. (2000). *Mutual Fund Performance*. Journal of Finance.
- Hamilton, J.D. (1989). *A New Approach to the Economic Analysis of Nonstationary Time Series*. (HMM regime)

---

## 12 · Changelog

- 2026-04-21: v1.0 · 首版，综合 session 全部讨论 + 10 项最佳实践

---

**本文档的作用**：

它是**完整的技术方案 + 实施路线 + 理论依据**。
即使下一次对话 context 完全清空，任何新手读完这份文档就能接着动手。
