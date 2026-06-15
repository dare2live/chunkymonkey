# 多因子组合探索 — 预注册 (冻结于跑批前, 2026-06-15)

> 状态: PRE-REG FROZEN (跑前). owner=本文件 + 上承 analysis/feature_layer_and_test_plan_20260615.md §4。
> 缘起: /loop "对主升浪猎手及其他公式进行多因子探索"。判据 owner=docs/strategy_validation_contract.md (C-R1/C-R2/C-WinReturn)。
> 纪律: 跑前冻结判据防事后挪门柱 (mythos §12); plan_validator 搜索空间非空门 + 自 grill 三问 (本文件末)。

## 0. 北极星对齐 (跑了有用吗)
- 产出: 主升浪猎手 + L0 公式 × 多因子组合的**含成本 OOS 绝对收益**裁决 (能赚钱吗), 非 IC 排序分。
- 消费: experiment_store L4 留档 + trailing 多窗衰减表 + tradability/kpi verdict → 决定哪个组合进 paper_sim/转正候选。
- 不跑行不行: 5 因子已物化 L2 (本轮无需抓新数据; 菜单 90 接口无 high-edge, 验证优先于抓取)。**先榨干已有 5 因子组合空间**, 验出缺口再抓 P0 (cashflow/block_trade)。

## 1. 真相源 + 输入 (PIT)
- 因子: `feature_store.duckdb fact_feature_panel` (mom_60/reversal_20/vol_20/mf_trend_20/roe_dt_asof, code×date, PIT, 2019/2020+)。**读 L2 不读 L0 raw** (写锁隔离 + moth feature-layer-l2-bypass-ratchet 守)。
- 价格 (回测): `market.duckdb price_kline_qfq_tushare` (OHLCV, T+1 open 入场, 涨跌停判定)。
- 主升浪事件域: `fact_rally_ground_truth` (突破事件 + is_true_rally). **开放项 A: 该表 06-14 reset 已 wipe (L4), 跑前须 rally_ground_truth_scan.py --land 重建** (口径已显式化常量, 99.92% 锚吻合)。
- 基准: HS300 (raw_tushare_index_daily 000300.SH) 超额。

## 2. 策略臂 (2 类)
- **主升浪猎手**: 入场宇宙 = 突破事件 (rally ground truth t); 因子组合给事件打分 → top-K 进场 → 含成本 execution-aware 持有/退出。
- **L0 公式**: formula_candidates.yaml active 子集 (<=5, 防过拟合); 因子组合作为 overlay/filter 叠加公式信号。

## 3. 搜索空间 (config 驱动, plan_validator 非空门)
| 维 | 空间 |
|---|---|
| 因子子集 | 2^5−1=31 非空组合 (哪些因子入选) |
| 因子权重 | equal / rank-IC 加权 / Optuna 连续权重 (和=1) |
| regime 门 | on/off × MA{20,60} 市场代理趋势 (risk-on 在场) |
| top_k | {10,20,30,50} |
| rebal | {5,10,20} 交易日 |
| sizing | equal / inverse_vol / rank |
| horizon | {5,10,20} (IC 快筛) / 持有=rebal (回测) |

## 4. 目标函数 (R1 — 含成本绝对收益, 非 IC)
`objective = net_annual_return − λ·max_drawdown_penalty` (execution-aware: T+1 open / 涨跌停一字板剔篮 / 非对称成本栈[卖方印花] / 容量 / 停牌冻结)。
- 复用 `portfolio_execbacktest.run_execution_backtest` + `phaseD_signal_eval.evaluate_signal`。
- IC (RankIC) 只作**必要快筛** (cross_sectional_ic), 不作转正充分条件 (C-R1)。

## 5. 防过拟合 / 防泄露 (跑前冻结)
- **切分**: train 2019–2023 / holdout 2024–2026 **disjoint**; 或 walk-forward expanding_monthly OOS。选参只读 holdout/OOS, 绝不 in-sample。
- **DSR**: deflated sharpe, n_trials=实际 Optuna trial 数, n_eff=n_days/horizon (重叠校正)。
- **PIT**: 因子已 L2 PIT; label forward 收益 purge+embargo≥1×horizon; JOIN 带 as_of。
- **异常红线** (§4.2): RankIC>0.3 / sharpe>5 / 年化>100% / 相对+50% → 不兴奋, ablation 核查 (anomaly_verdict + tradability_verdict 对称门)。

## 6. 裁决 (跑后, 多判官)
1. `tradability_verdict(ic, net_annual)`: IC>0 但含成本 net≤0 → IC_POSITIVE_BUT_UNTRADABLE (R1 盲点门)。
2. `kpi_verdict`: 联合 年化≥30% AND max_dd≥−20% AND 月胜率≥55% AND 胜率×盈亏比期望>0 (C-WinReturn; 胜率=诊断量)。
3. trailing 多窗 (3m/6m/12m/18m/24m/3y/5y): 看"曾有效近期衰减"还是"跨 regime 稳健"。
4. DSR/PBO 去多重比较偏。
5. **转正 (confirmed_by_owner=1) 须带含成本绝对收益证据** (record_verdict C-R1 guard)。

## 7. 执行 (Optuna + Modal)
- Optuna TPE 搜 §3; 每 trial = 1 含成本 execution-aware backtest; 经 plan_validator.enforce_optuna_plan (搜索空间非空 + walk_forward)。
- **Modal map 并行**: 组合空间大 (31 子集 × 权重 × 参数 × 2 臂 × 多 regime) → Modal worker 并行跑 (本地串行慢)。结果聚合回 L4 experiment_store。先本地 smoke(小空间) 验逻辑, 再 Modal 全量。
- 读 L2 panel (无 L0 写锁竞争)。

## 开放项 (跑前必清)
- **A. fact_rally_ground_truth 已 wipe** → rally_ground_truth_scan.py --land 重建 (验 31,531 事件锚)。无此表则主升浪臂跳过, 只跑 L0 公式臂。
- **B. build_feature_panel.py executemany 慢** (本轮实测 ~30min/8M行 PK 逐行约束检查) → 加因子重建前优化为 Arrow/df 批量插入 (CREATE 无PK→bulk→后建唯一索引)。本轮 5 因子已建可用, 优化是下次加因子的前置。
- **C. data_layers.yaml 须声明 fact_feature_panel=L2_feature** (新表必声明, data-layer-integrity moth 门)。

## 自 grill (三问, 无交互工具时, skill §3.6)
1. **跑完产出什么谁消费?** 含成本 OOS 绝对收益裁决 → 决定组合是否进 paper_sim。不是 IC 分数游戏。[PASS]
2. **每步前提验证了?** 因子源齐 (K线2019+/moneyflow2020+/fina5202股); 切分 disjoint; DSR 去偏; 开放项 A/B/C 跑前清。[PASS] (A 须先重建表)
3. **成本 vs 产出合理?** 先本地 smoke 小空间验逻辑 (零成本), 再 Modal 全量; 不一上来打满。[PASS]
- **C-R1**: 充分证据=含成本绝对收益 (非 IC)。[PASS] 目标函数=net_annual_return。
- **C-R2**: execution-aware (涨跌停/非对称成本/容量/T+1 open)。[PASS] 复用 portfolio_execbacktest。
- **C-WinReturn**: 考核收益率+max_dd (目标量) 非只胜率。[PASS] kpi_verdict 联合。
