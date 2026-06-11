# 流派 D 策略蓝图 — 截面排序增强 (LambdaMART v6 → v7)

> 生成: 2026-06-11 | 作者: 策略研究员 (流派 D) | 状态: 设计稿, 待 grill gate
> 输入: /tmp/cm_checkup/domain_*.json (9 域深挖) + user_vision_hologram.md + samples/*.parquet (TuShare 深样本实测) + docs/implementation_plan.md + backend/config/{feature_registry,champion_registry,formula_*}.yaml + data/market.duckdb 只读盘点
> 注: 任务书所列 evidence_*.md / cross_*.md / tushare_full_probe.json 在本机不存在 (已核实), 本蓝图以 domain JSON + 深样本实测 + repo 文档为据; 所有小样本数字标 [初步]。

---

## 0. 一页摘要

| 项 | 内容 |
|---|---|
| 定位 | 现有 LambdaMART v6 (expanding_monthly walk-forward, OOS RankIC 干净基线 0.0108–0.0203, ~0.015 量级) 的特征族升级路径, 即 implementation_plan Phase 2+4 的 v7 执行细案 |
| 新特征族 | ① 资金流 (moneyflow, 2010 起 16 年) ② 筹码 (cyq_perf, 2018 起 8 年) ③ 涨停生态 (limit_list_d+stk_limit, 2020 起) ④ 卖方一致预期 (report_rc, 2010 起 16 年) ⑤ 估值/换手面板 (stk_factor_pro 15 列增量) |
| 核心承诺 | 逐族 ablation + 长历史 walk-forward (2010 起的 moneyflow/report_rc 是全项目唯一能覆盖 2015 崩盘 + 2018 熊市的新数据); 不承诺 RankIC 跳变 — 相对提升 ≥+50% 自动触发 pit-audit 而非庆祝 |
| 关键硬约束 (实测发现) | 本地 K 线 `market.duckdb::price_kline_tdxhub` 实测仅 2022-01-04 起 (5,294,854 行)。16 年 walk-forward 的 label/forward return 需要 TuShare `daily`+`adj_factor` 2010–2021 回填 (含退市股), 否则长历史族只能在 2022+ 窗口验证 |
| 初步实测警示 | 深样本 20 日窗口上, 资金流单日/5 日特征对 forward 5d 的截面 RankIC ≈ 0 (见 §6.4, [初步/不显著]) — 资金流不是显然 alpha, 必须靠 16 年 walk-forward 定方向, 这正是本流派坚持长历史的原因 |

---

## 1. 核心论点 — 这个策略赚的是谁的钱

**一句话**: v7 不创造新的入场时点, 它提高"同一天的候选股里, 谁更值得买"的排序质量。赚的是三类行为偏差的钱:

| 偏差 | 对应特征族 | 机制 | 自家证据状态 |
|---|---|---|---|
| **散户与主力的订单流不对称** | moneyflow 四档买卖拆分 | 小单追涨/大单派发的截面差异在 5–20 日尺度上未被价格完全吸收; moneyflow 是唯一给买卖两侧全拆分的口径 (可算主动买占比, dc/ths 算不出) | 未验证。[初步] 20 日窗口 RankIC≈0 (§6.4), 方向未定, 可能是正向跟随也可能是反向拥挤度因子 — 这本身就是 ablation 要回答的问题 |
| **套牢盘处置效应** | cyq_perf winner_rate + 成本分位 | 上方套牢密集区压制上行 (解套即卖), 获利盘极高 = 抛压临界; 处置效应是 A 股散户最稳定的行为偏差之一 | 自家先例: CYQ 入场 filter 实测无效, 唯一微弱有效是筹码集中度 +4.4pp (hologram A.3) — 所以本蓝图把筹码定位为**截面排序特征 + 卖侧 gate**, 不做入场 filter; [初步] 5 股 3 年样本 winner_rate 极值两端 forward 10d 均值高于中段 (U 形迹象, §6.4) |
| **涨停情绪传染与博弈拥挤** | limit_list_d 封单/炸板/连板 | 封单质量 (fd_amount/float_mv, 秒板 vs 烂板) 含次日溢价信息; 炸板率/连板高度是市场情绪温度计 → regime 降权 | 未验证。注意自家判决: 91% 主升浪起涨日无主力痕迹 (hologram 盲点 4) — 涨停生态特征预期主要贡献在**情绪 regime 与排除项**, 不在主模型正向 alpha |
| **卖方预期修正的扩散滞后** | report_rc EPS 一致预期 90 日修正 | 分析师上调 → 机构调仓有天级-周级扩散过程; 2010 起 16 年流水是经典强因子 (预期修正动量) 的全程回测载体 | 未验证。现库卖方预期域 = 0 (aif10 仅快照), registry 已注册 fact_profit_forecast_daily 但无表 — 接入即补 registry-DB 偏差 |

**为什么走 v6 升级而不是另起炉灶**: v6 的 expanding_monthly walk-forward / oos_* 列约定 / enforce_pre_insert / ROI gate 管线是全项目唯一走通过严格 PIT 验证的 ML 路径 (干净基线 0.0108–0.0203 是诚实数字)。新数据域的第一性问题不是"建什么新模型", 而是"这族数据在干净 PIT 下到底有没有截面信息" — v6 管线就是这台测量仪。

**诚实声明 (不可回避的代数账)**: hologram 盲点 1 已算过 — 5 持仓 ρ=0.3 时有效下注数 2.27, 30% 年化需要有效 RankIC≈0.20。v6 的 0.015 与 0.20 差一个数量级, **v7 单独不可能达成北极星 KPI**。v7 的贡献路径是给入场流派 (公式/主升浪/题材) 的候选池做排序与降权, 把"同日 30 个信号选哪 5 个"这一步做对 — 贡献是乘法因子, 不是独立策略 (详见 §7)。

---

## 2. 信号链 (数据表/接口 + PIT 锚 + 计算窗口)

约定: 全部盘后数据特征 JOIN t-1 (项目红线); 落库一律带 `built_at`, JOIN 恒带 `built_at <= t`; 信号在 t 收盘后生成, t+1 开盘执行。

### 2.1 特征层 (5 个新特征族, 每族 5–8 列, 控制维度膨胀)

| # | 特征 | 源接口/表 | PIT 锚 | 计算窗口 | 备注 |
|---|---|---|---|---|---|
| F1.1 | `mf_lg_net_ratio_5d/20d` 大单+特大单净买占比 | `moneyflow` → 新表 `fact_moneyflow_daily` | 18–19 点盘后 → JOIN t-1 | 5/20 日滚动和 / 同窗总成交额 | 单位万元; 唯一可算主动买占比口径 |
| F1.2 | `mf_buy_pressure_20d` 主动买占比 buy_total/(buy+sell) | 同上 | t-1 | 20 日 | moneyflow 独有 |
| F1.3 | `mf_retail_diverge_5d` zscore(特大单净买)−zscore(小单净买) | 同上 | t-1 | 5 日 | 散户接盘识别, 截面 z |
| F1.4 | `mf_price_flow_div_5d` 5 日净流入>0 ∧ 5 日 pct_chg<0 | moneyflow + 本地 K 线 | t-1 | 5 日 | 价跌钱进, 与 reversal 族正交性待测 |
| F1.5 | `mf_consensus_dc` sign 共识 (moneyflow vs dc) | + `moneyflow_dc` (2023-09 起) | t-1 | 1 日 | 仅 2023-09 后非 NULL; null_policy=excluded_until_backfilled 模式 |
| F2.1 | `cyq_winner_rate` / `cyq_wr_chg_5d` | `cyq_perf` → `fact_cyq_perf_daily` | 18–19 点盘后 → JOIN t-1 | 当日值 + 5 日差分 | 2018 起 |
| F2.2 | `cyq_cost_gap` close/weight_avg − 1 | 同上 + K 线 | t-1 筹码 + t-1 收盘 | 1 日 | 距主力成本 |
| F2.3 | `cyq_width` (cost_85−cost_15)/cost_50 + 其 120 日分位 | 同上 | t-1 | 120 日 rolling 分位 (左闭, 只用 ≤t-1) | 筹码集中度 — 自家唯一 +4.4pp 先例的对应物 |
| F2.4 | `cyq_overhead_room` close/cost_95pct − 1 | 同上 | t-1 | 1 日 | 上方套牢压强 proxy |
| F3.1 | `lmt_seal_quality_20d` 近 20 日涨停日的 fd_amount/float_mv 均值, 烂板率 (open_times≥3 占比) | `limit_list_d` → `fact_limit_list_daily` | 更新时点文档未写 → 强制 JOIN t-1 | 20 日事件窗 | 2020 起; 不含 ST (分母用 stock_st+stk_limit 补) |
| F3.2 | `lmt_market_temp` 炸板率 / 连板高度 / 晋级率 三合一 z | 同上 (市场级聚合) | t-1 | 当日聚合 + 20 日 z | **进 regime gate, 不进个股模型** — 替代 bear/sideways/bull 拍脑袋 (§4.5 反例) |
| F4.1 | `rc_eps_rev_90d` 90 日窗口 EPS 一致预期均值 vs 上一窗口 delta | `report_rc` → `fact_profit_forecast_daily` | 每晚 19–22 点更新 → report_date ≤ t-1, 且 `create_time` ≤ t 盘前 双锚 | 90 日滚动 | 2010 起; create_time 是第二道防御, 防补录研报泄漏 |
| F4.2 | `rc_coverage_chg` 90 日去重机构数环比 + 首次覆盖事件 (180 日内零覆盖→首覆) | 同上 | report_date ≤ t-1 | 90/180 日 | |
| F4.3 | `rc_rating_upgrade_20d` 评级上调事件计数 | 同上 | report_date ≤ t-1 | 20 日 | 事件编码 0/count, 无事件=0 不留 NULL (registry event 族惯例) |
| F4.4 | `rc_target_space` min_price/close_{t-1} − 1 的截面分位 | 同上 + K 线 | report_date ≤ t-1 | 当期最新 | 取目标价下限抵消卖方乐观偏差; 只用截面 rank 不用绝对值 |
| F5.1 | `turnover_rate_f` / `volume_ratio` / `pe_ttm` / `pb` / `circ_mv` (log) / `free_share` 等估值流动性 6–8 列 | `stk_factor_pro` (261 列只取 15 列增量块) | 更新时点 unknown → JOIN t-1 | 当日值 + pe/pb 的 ≤t-1 rolling 分位 | 自由流通换手比现 vol_z20d (总股本口径) 更真; 复权敏感列一律用 _hfq |

### 2.2 排序层

| 步 | 内容 | PIT 锚 |
|---|---|---|
| R1 | 特征面板: 现 v6 panel (feature_registry 6 组, ~46 model-input 列) + 新族列, 经 `build_feature_panel` 同一管线物化, 每列带 built_at | 面板行 (stock, t) 只含 ≤t-1 盘后信息 + ≤t 盘前信息 (stk_limit 8:40 类) |
| R2 | LambdaMART v7 训练: 复用 `run_p0b_lambdamart_v6.py` 的 `build_walk_forward_windows` (expanding_monthly), label = forward_ret_5/10/20d (label 不动, 与 v6 严格同口径才可比) | train 窗口日期严格 < signal_date (现行 pit_policy); purge/embargo ≥ 最长 forward 期 |
| R3 | 输出: `lambdamart_v7_predictions` 新表 (不覆盖 v6 表 — 验证 artifact 不许覆盖), score + rank + model_id + oos 窗口标记 | 预测行 built_at = 训练完成时 |
| R4 | 消费: paper_sim v2 selector 读 oos score; 其他流派可 JOIN score 做候选池二排 | selector 只读 oos_* 列 (Optuna 治理三防线现规) |

---

## 3. Universe 与可执行性

| 过滤 | 真相源 | PIT 锚 | 现状→改法 |
|---|---|---|---|
| 在市/在交易 | 本地 K 线 (有交易=在交易, 宪法第一真相源) | t | 不变 |
| ST/*ST 剔除 | `stock_st` (2016-01-01 起每日名单, 9:20 更新) + `namechange` 重建 2016 前 | t 日 9:20 可用; 竞价前决策用 t-1 | 替换 akshare 即时调用无历史落库的现状 — 回测期 ST 过滤第一次有 PIT 真相源 |
| 停牌 | `suspend_d` + K 线缺口交叉验证 | 执行层 t 日 | 兼做 sync-bug 哨兵 (K 线缺 ∧ 无停牌记录 = 自己 bug 警报) |
| 一字板不可买 | `stk_limit` 真实 up_limit (每日 8:40 盘前) | t 日 8:40, 无需滞后 | paper_sim reject_buy 判据: T+1 open ≥ up_limit×0.98 标记不可成交; 同时修复 limit_up_pct 参数作用域错配反例 (per-stock 属性运行时取, 不进全局 search space) |
| T+1 卖出锁死 | stk_limit down_limit | t 日 8:40 | 跌停无法卖 → 持有顺延, 成本模型计入 |
| 流动性下限 | stk_factor_pro turnover_rate_f / 本地 amount | t-1 | `min_amount_20d` 阈值进 search space |
| 次新股 | stock_basic list_date | 静态 | 上市 < `min_list_days` (search space) 剔除 |
| 板块覆盖 | validate_loaded_stocks 运行时校验 | — | 沿用 §4.5 max_stocks=200 反例的修复: 全量 universe + 板块覆盖断言 |
| 生存者偏差 | TuShare `daily` 含退市股 (2010–2021 回填) | t | 长历史窗口的 universe 必须含当时在市后来退市的股票 |

执行口径: 信号 t 收盘 → t+1 开盘价成交 (盲点 6 的 T+1 open 口径); 所有 KPI 数字只认含成本 paper_sim 及以上 (数字出口规则)。

---

## 4. 入场 / 出场 / 仓位 / 风控 — 参数全部进 Optuna search space

以下全部写入 `backend/config/optuna_config.yaml` + paper_sim yaml, 走 `services.optimization` 中央层 + `walk_forward.expanding_monthly` + `enforce_pre_insert`; 范围是 search space 声明, 不是调好的值, 一个都不拍死:

```yaml
v7_strategy_search_space:
  # 入场 (排序消费)
  top_k:                {low: 3, high: 10, type: int}        # 每日取 score 前 K 入候选
  score_min_pctile:     {low: 0.90, high: 0.99}              # 截面分位门槛
  score_blend_w_v7:     {low: 0.0, high: 1.0}                # v6/v7 分数混合权重 (w=0 即回退 v6, 内建保险)
  entry_open_gap_max:   {low: 0.03, high: 0.09}              # T+1 高开超过此值放弃 (执行滑点保护)
  # 出场
  hold_days_min:        {low: 3, high: 15, type: int}
  hold_days_max:        {low: 10, high: 60, type: int}
  stop_loss_atr_mult:   {low: 1.5, high: 4.0}                # ATR 来自 stk_factor_pro atr_hfq — 修复 vol-aware hardcode 反例
  trailing_atr_mult:    {low: 2.0, high: 6.0}
  exit_wr_high:         {low: 0.85, high: 0.98}              # cyq winner_rate 极高止盈 gate (卖侧, 自家定位筹码真价值所在)
  exit_hot_rank:        {low: 5, high: 50, type: int}        # 热榜进 topN 持仓降权/止盈 (若热榜历史深度实测足够)
  # 仓位
  max_positions:        {low: 4, high: 8, type: int}
  position_sizing:      {choices: [equal, wilson_kelly]}      # paper_sim v2 已有 Wilson-Kelly 模块
  sector_budget_pct:    {low: 0.25, high: 0.50}              # 同申万 L2 行业资金占比上限 — 盲点 5 的组合相关性炸弹防御
  # 风控 / regime
  regime_temp_quantile: {low: 0.10, high: 0.40}              # lmt_market_temp 低于历史分位 → 仓位降档
  regime_pos_scale:     {low: 0.0, high: 0.6}                # 降档后仓位系数 (0 = 空仓等待, "等待>操作")
  mkt_flow_ma_days:     {low: 10, high: 40, type: int}       # moneyflow_mkt_dc 大盘净流入均线窗 (regime 第二输入)
```

约束 (非搜索, 宪法/红线级): T+1; 一字板 reject_buy; 跌停顺延卖出; 含 tx_cost (现行 TradingCostConfig 单一真相源); 不加杠杆。

---

## 5. 数据需求清单 (回填规模 + modal 跑批估算)

### 5.1 接口回填 (按依赖序)

| 序 | 接口 | 回填深度 | 调用量估算 | 行数估算 | 备注 |
|---|---|---|---|---|---|
| 1 | `moneyflow` | 2010→今 (16 年) | ~3,900 次 (1 次/交易日, 实测 5,181 行/日 < 单次 6000 上限) | ~13–15M 行 [估算: 早年股票数少] | need_027 gate 已 PASS, writer 必须 0 行=失败重试; **MVP 可先回填 2022+ 匹配本地 K 线** |
| 2 | `daily` + `adj_factor` (2010–2021) | 12 年 | 各 ~2,900 次 | 各 ~7M 行 [估算] | **长历史 walk-forward 的硬前置** — 本地 K 线实测 2022-01-04 起; 含退市股; trade_cal 2010+ 同步补 |
| 3 | `cyq_perf` | 2018→今 (8 年) | ~5,400 次 (按股循环, 单股 1 次覆盖全史 ~1,920 行 < 6000) | ~10M 行 [估算] | 接入时实测 trade_date 单参能否拉全市场 (文档标 ts_code 必选, 不预设) |
| 4 | `report_rc` | 2010→今 (16 年) | unknown — 按月+分页循环, 首日先探 3 个月实测行量再定 | unknown | 8000 积分 10 万次/日, 总量够; 行量探明前不排期全量 |
| 5 | `limit_list_d` | 2020→今 (6 年) | ~1,460 次 (实测 ~480 行/日 < 2500) | ~0.7M 行 | + `stk_limit` (2,000 分) / `stock_st` (2016 起) / `suspend_d` 三个小表同批 |
| 6 | `stk_factor_pro` (列裁剪) | 2018→今, 只取 15 估值列 + atr_hfq/adx 等 ~25 列 | ~1,940 次 (1 次/日全市场) | ~10.5M 行 × 25 列 | 不贪 261 列全量 (奥卡姆); 指标列与本地自算交叉验证数据质量 |
| 7 | `moneyflow_dc` / `moneyflow_mkt_dc` | 2023-09 / 全史 (mkt 1 行/日) | ~700 / ~800 次 | ~3.5M / ~4k 行 | dc 做共识第二票; mkt_dc 是 ROI 最高的 regime 输入 (120 分可试用) |

存储增量合计 [估算]: ~45–60M 行, DuckDB 压缩后约 3–6 GB — 本地可承载, 不需要为存储上 modal。

### 5.2 modal 跑批规模

纪律: 禁止线性外推拍跑批时长。流程 = **local 实测 1 个 fold 的 wall time → plan_validator.enforce_optuna_plan() 验 search space 非空 → 再定 modal 规模** (2026-05-26 29/34 公式白跑反例)。

| 任务 | 规模 | backend |
|---|---|---|
| MVP ablation (2022+ 窗口, v6+资金流) | 1 族 × ~40 monthly folds × 复用 v6 现行 trial 数 | local (现行 active backend), 不动 modal |
| 全量 5 族 ablation (2018+ 窗口) | 5 runs (每族单独 +1 族) + 1 run (full v7) | local 优先; 单 fold 实测 >30 min 才升 modal |
| 16 年 walk-forward (Tier-A 两族) | ~156 monthly folds × 2 配置 | modal 候选 — 但 modal 仍 blocked (reviewed adapter + artifact-manifest 契约未过), $30/月额度下只排这一项, 排期在 adapter gate 之后 |

---

## 6. 验证计划

### 6.1 分层 walk-forward (按历史深度分 Tier — 本流派的核心卖点)

| Tier | 特征族 | 历史 | walk-forward 窗 | 覆盖的市场环境 |
|---|---|---|---|---|
| A (长史, 稀缺) | moneyflow, report_rc | 2010 起 16 年 | expanding_monthly, train 起点 2013 (3 年 warmup), OOS 2013-01→2026-05, ~156 folds | **2015 杠杆牛崩 / 2016 熔断 / 2018 单边熊 / 2020 流动性冲击 / 2024-09 牛** — 直接回应盲点 8 (现全部验证只落在 2022–2026) |
| B (中史) | cyq_perf, stk_factor_pro | 2018 起 8 年 | OOS 2020-01→2026-05, ~77 folds | 2022 阴跌 + 2024 牛 + 2018 尾部 |
| C (短史, 只做 add-on) | limit_list_d (2020), moneyflow_dc (2023-09), 热榜 (深度 unknown 待实测) | ≤6 年 | OOS 2022+ | 短史族**不单独立论**, 只在 Tier-B 面板上做增量 ablation; 热榜历史 <2 年则降级 live-only 因子 |

### 6.2 逐族 ablation 矩阵 (Phase 2 ROI gate 的执行形态)

每族先过 ROI 预检 (coverage ≥80% universe / 截面 Spearman 自相关 / 方差非退化), 再进 ablation:

| Run | 配置 | 判据 |
|---|---|---|
| A0 | v6 基线复跑 (同窗口同 label) | 锚点: OOS RankIC 必须落回 0.0108–0.0203 带, 否则管线先修 |
| A1–A5 | v6 + 单族 (资金流/筹码/涨停/卖方/估值 各一) | 每族报: ΔRankIC (5/10/20d) + top-decile spread Δ + 特征重要度 + fold 间 std |
| A6 | v6 + 全部通过族 = v7 候选 | 维度纪律: 46 维×789 样本已近样本/维度比红线 (hologram 盲点 7) — 每族限 5–8 列, 进面板前先族内去共线 |
| A7 | v7 候选 − 任一族 (留一法) | 确认无单族依赖 |

**红线自动化**: 任一 run 相对 A0 提升 ≥+50% (如 0.015→0.0225) → 自动触发 pit-audit 5 步, 不是庆祝; RankIC>0.3 / sharpe>5 / 胜率>95% 绝对红线同现行。

### 6.3 基线对比 (策略层)

paper_sim v2 同窗口跑四基准: HS300 / 等权 universe / 不换股持有 / **random-entry + same-exit** (盲点 3 的入场 alpha 真对照)。v7 策略的报告口径 = 相对 random-entry 同退出的增量, 不报绝对胜率。

### 6.4 MVP — 一周内能出的 (0 个新依赖被阻塞)

| 日 | 动作 | 产出 |
|---|---|---|
| D1–2 | moneyflow 回填 2022-01→今 (~1,070 次调用, walk gate 已 PASS 的 writer 路径); 建 fact_moneyflow_daily + built_at | 表 + freshness 证据 |
| D2–3 | F1.1–F1.4 四列特征进 panel (registry 注册, null_policy 声明); ROI 预检 | ROI gate 报告 |
| D3–5 | A0 复跑 + A1 (v6+资金流) walk-forward, 2023-01→2026-05 OOS (~40 folds), local backend | ΔRankIC + fold std + 特征重要度 |
| D5–7 | 若 ΔRankIC>0: top-decile spread + paper_sim 含成本一键回放; 若 ≤0: 写阴性报告归档 (失败先承认), 转下一族 | go/no-go 证据 artifact |

**已做的深样本前哨实测 (本蓝图执行中实测, 全可复现)**: 用 /tmp/cm_checkup/samples/moneyflow.parquet (98,610 行, 2026-05-13→06-10, ~5,181 股/日) JOIN 本地 K 线:
- 单日大单净买占比 vs forward 5d: 截面 RankIC 均值 **+0.0016** (15 个截面日), T+1 open 口径 −0.0037;
- 5 日滚动版 vs forward 5d: **−0.0121** (11 个截面日, 日间 std ~0.05, 统计上与 0 无法区分)。

[初步] 结论: 在这 20 天单一 regime 窗口里资金流特征无可见 alpha, 且滚动版偏负 — 提示该族可能在不同 regime 下正负翻转 (跟随 vs 拥挤), **必须长历史分 regime 验证, 严禁按 20 天样本定方向**。这是诚实的先验, 不是坏消息: 它把 MVP 的判据从"期待惊喜"校准为"测出真实量级"。
另: cyq 5 股×3 年样本 (4,144 行) winner_rate≥90 与 ≤10 两端的 forward 10d 均值 (+1.5% / +0.9%) 均高于 30–70 中段 (+0.3%) — U 形极值反转迹象, [初步/5 股不构成截面证据], 仅用于支持把 winner_rate 同时编码为极值距离特征而非线性特征。

### 6.5 验收口径

- 特征层: OOS RankIC (主) + top-decile spread (次) + fold 间 std (稳定性);
- 策略层: 含成本 paper_sim 的 年化/max_dd/月度胜率分布 vs 四基准 — 单分数 improve 不算 delivery, audit script + evidence artifact 同时同意 (文档纪律现规);
- 月胜率 KPI 按 walk-forward 月度分布验收 (目标稳定 ≥55%), 但研究优先级排序用 expectancy, 不用胜率 (盲点 1)。

---

## 7. 与北极星 KPI 的预期贡献路径 + 失败模式

### 7.1 贡献路径 (诚实版)

```
v7 RankIC 0.015 → 0.018~0.025 (期望区间, unknown 直到测出)
  ├─ 不是独立达成 30% 年化的路径 (需 RankIC≈0.20, 差一个量级 — 盲点 1 代数账)
  ├─ 路径①: 候选池排序质量 — 入场流派每日 N 个信号取 top-K 时的选择增益,
  │   top-decile spread 每 +1pp ≈ 5 持仓组合的单次期望直接 +, 经由 paper_sim 量化
  ├─ 路径②: 降权/排除 gate — 散户接盘 (F1.3) / 全员获利 (exit_wr_high) / 热榜拥挤,
  │   作用于 max_dd≥-20% 与月胜率分布的左尾, 不作用于均值
  ├─ 路径③: regime gate 数据化 (lmt_market_temp + mkt_dc 资金面) — 替代拍脑袋三档,
  │   服务"等待>操作": 弱市少入场是该 KPI 组合的第一防线
  └─ 路径④ (对全项目): 16 年面板第一次让任何策略能回答"2015/2018 你活得下来吗"
```

定量承诺只有一条: **每个数字以含成本/T+1 open/含重叠 paper_sim 口径交付, 与四基准并列**。MVP 前不预报年化贡献值 — 报了就是 estimate not measured。

### 7.2 失败模式 (什么情况下这个流派不工作)

| # | 失败模式 | 先兆/检测 | 应对 |
|---|---|---|---|
| 1 | 资金流族 16 年测完 RankIC≈0 (前哨实测已暗示此可能) | A1 ablation ΔRankIC ∈ [−0.002, +0.002] 且 fold std 大 | 阴性结果归档, 该族转 regime/卖侧用途或放弃; 不留恋 ("不合格就是不合格") |
| 2 | 新族与现 46 维共线, 加列只加噪声 | 族内/跨族相关矩阵 + 留一法 A7 无衰减 | 砍列; 周线特征共线无 lift 是先例 |
| 3 | 维度膨胀过拟合: fold 间 std 上升, 均值不动 | A6 vs A0 的 std 比 | 每族 5–8 列硬上限; 样本量随 16 年回填同步扩大是唯一正解 |
| 4 | 口径陷阱假 alpha: moneyflow_dc 的 buy_elg_amount 实为净额 (同名异义), 单位万元/元/亿元混 | ETL 改名隔离 + 单位归一 lint; ablation 异常好 → pit-audit | 接入即防, 字段语义表进 PROJECT_INDEX |
| 5 | report_rc 补录泄漏 (研报 report_date 早于实际入库) | create_time 双锚; 入库后抽查 report_date vs create_time 分布尾部 | create_time ≤ t 盘前硬条件 |
| 6 | 排序增益被执行成本吃光: top 股集中在高开/涨停不可买 | paper_sim reject_buy 率 + T+1 open 口径与 close 口径的 KPI 差 | entry_open_gap_max 进 search space; 可成交性即特征 (温和涨是 top5% 特征, hologram) |
| 7 | regime 翻转: 因子方向 2015 牛 vs 2018 熊翻号 | Tier-A 分 regime 子窗口 IC 符号表 | 因子×regime 交互进模型而非全局符号假设 |
| 8 | 短史族 (limit_list_d 6 年, dc 2.7 年) walk-forward 窗口不足以出显著结论 | fold 数 <40 | 短史族只做 add-on 不单独立论 (§6.1 已锁) |
| 9 | 数据断流复发 (cron 静默失败前科 2 次) | launchd wrapper 告警链 + 启动查 ALERT flag (已根治路径) | 新表全部纳入 freshness SLA + watermark, 不走裸 cron |
| 10 | v7 上线后实盘与 paper_sim 偏差 | Phase 5 小资金滑点校准 | score_blend_w_v7 保险丝: 实盘可一键回退 v6 (w=0) |

---

## 8. 与其他三流派的互补性自评

(其他三流派蓝图未见, 按 checkup 流派分工惯例自评: A=主升浪/突破事件, B=题材/涨停生态接力, C=退出引擎/组合层)

| 维度 | 流派 D 的角色 | 互补关系 | 边界 (D 不做什么) |
|---|---|---|---|
| 时间轴分工 | D 回答"买谁" (截面相对强弱), 不回答"何时买" | A/B 产生入场时点与候选池, D 给候选池排序 — 30 选 5 的那一步; D 的 score 表是公共资产, 任何流派 JOIN 即用 | 不做事件检测, 不做状态机, 不与公式工厂抢入场逻辑 |
| 数据底座 | D 是 5 个新数据族的第一个严格消费者, 接入的 fact 表 (moneyflow/cyq/limit/report_rc) + PIT 锚约定全项目共享 | B 的涨停传染、C 的 winner_rate 止盈直接复用 D 落的表, 零重复建表; D 的逐族 ablation 结论 = 其他流派 filter 选型的实证依据 (哪族有截面信息、哪族只配做 gate) | 表 owner 是 data 层不是 D, schema 按 data_product_contract 走 |
| 风险分工 | D 的 regime 温度计 (F3.2 + mkt_dc) 是组合层公共输入 | C (退出/组合) 拿温度计做仓位降档; D 自己只用它降权 | 组合层 NAV/相关性预算是 C 的 owner, D 只供特征 |
| 验证资产 | 16 年 Tier-A 窗口是全项目唯一覆盖 2015/2018 的验证场 | A/B 策略可在 D 回填的 2010–2021 K 线+宇宙上重放熊市生存测试 (盲点 8 的公共补法) | — |
| 失效相关性 | D 失效模式 (截面信息消失) 与 A/B (事件失效) / C (退出规则失效) 弱相关 — 多流派同时失效的共因只剩 regime 与数据断流, 两者都有独立 gate | 组合层面: D 增强的是选股質量这一乘法因子, 即使 D 全族阴性, A/B/C 退回 v6 排序照常运转 (score_blend_w_v7=0 保险丝) | — |

**自评一句话**: 流派 D 是四流派里唯一"结论可被其他三家直接复用"的 — 它测的不是一个策略, 是五族数据在干净 PIT 下的截面信息含量; 即使 v7 对 RankIC 的增量最终是阴性, 逐族 ablation 报告 + 16 年 PIT 面板 + 可执行性真相源 (stk_limit/stock_st) 也是全项目的净资产。风险是它单独扛不动北极星 KPI (盲点 1 代数账), 必须与入场流派和退出引擎拼装 — 这一点在 §1 和 §7 已经按"真金白银"纪律明示, 不留幻觉。

---

## 附: 执行前三问 (grill gate 自答)

1. **跑完产出什么, 谁消费?** 逐族 ΔRankIC/spread 证据 artifact (goal.md Phase 2 验收消费) + fact 表 5 张 (全流派消费) + v7 candidate (paper_sim/Phase 4 gate 消费)。不跑 = Phase 2 alpha 研究序无法推进, 但 MVP 阴性同样是合格产出。
2. **每步前提验证了?** moneyflow gate 已 PASS; K 线 2022 起实测确认 (MVP 不依赖 2010 回填); report_rc 行量 unknown → 先探后排期; 热榜深度 unknown → 实测前不进面板; modal blocked → MVP 全 local。
3. **成本 vs 产出?** MVP ~1,070 次 API 调用 + local 算力, 零现金成本; 16 年回填在 MVP 出阳性信号或 Tier-A 必要性确认后才扩大投入。
