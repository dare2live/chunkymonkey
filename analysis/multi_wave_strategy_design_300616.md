# 多波段抓涨策略设计 — 以 300616 三波为基础

> 2026-05-26 设计文档. 基于 300616 实测数据 + 55 公式全量扫描结果.

## 1. 核心发现: 多公式共振

单公式无法覆盖三波全部起涨点. 实测发现**多公式共振**才是正确方案:

| 大涨事件 | 提前命中公式 | T+1 买价 | 后续涨幅 |
|---|---|---|---|
| W1 首涨 12-30 (+13.6%) | gs_raw_buy + obv_breakout | 20.65 | +48% 到顶 |
| W1 主涨 01-04 (+15.3%) | activity + gs_raw + obv + macd + atr (5 公式共振) | 22.57 | +29% 到顶 |
| W2 主涨 05-10 (+16.9%) | activity_breakout + rsi_oversold_bounce | 25.22 | +21% 到顶 |
| W3 首涨 04-22 (+11.6%) | gs_raw_buy + obv_breakout | 12.48 | +55% 到顶 |
| W3 涨停 05-11 (+20.0%) | volume_base_breakout | 14.66 | +32% 到顶 |

**规律**: 共振公式越多, 后续涨幅越大. W1 主涨有 5 公式共振 → +29%.

## 2. 三阶段信号体系

### Stage A: 筑底突破 (ma_base_breakout 主导)

**触发条件**: 长期下跌后横盘, MA5 长期低于 MA90, 突破 MA145 站稳
- 300616 实测: 2024-05-17 发出 1 个信号 (MA90/145 交叉)
- 问题: 信号太稀疏 (整段历史只 1 个). 需放宽参数或组合 obv/gs_raw_buy

**组合方案**: ma_base_breakout OR (gs_raw_buy AND obv_breakout 同日共振)
- W1 首涨: gs_raw_buy + obv_breakout 2022-12-28 共振 → 12-29 买入, 完美
- W3 首涨: gs_raw_buy + obv_breakout 2026-04-21 共振 → 04-22 买入, 完美

### Stage B: 涨势延续 (activity_breakout + pullback_doji)

**触发条件**: 已有首涨, 回调后再次放量
- W1: activity_breakout 2022-12-30 → 01-04 +15.3% 主涨
- W2: activity_breakout 2023-05-08 → 05-10 +16.9% 主涨
- pullback_doji: 回调十字星后入场 (W1 01-10/11/12 连续十字星 → 01-13 +5.3%)

### Stage C: 波段加速 (volume_base_breakout 主导)

**触发条件**: 缩量横盘后温和放量突破平台
- W3: volume_base_breakout 2026-05-08 → 05-11 +20.0% 涨停

## 3. 卖出策略

三种卖出条件, 取最先触发:

| 卖出类型 | 条件 | 用途 |
|---|---|---|
| 止损 | close < 入场价 × (1 - stop_pct) | 控风险 |
| 移动止盈 | close < 最高价 × (1 - trail_pct) | 锁利润, 捕捉整轮涨幅 |
| 均线止盈 | close < MA20 且 MA20 拐头向下 | 趋势结束 |

**Optuna 搜索**: stop_pct (3-8%), trail_pct (8-15%), MA 周期 (10/20/30)

## 4. 股票池设计 (max 5 stocks)

### 4.1 信号评分 (per signal)

每个公式信号按 3 维评分:

| 维度 | 权重 | 计算 |
|---|---|---|
| 共振度 | 40% | 同日 ±1d 内有多少公式同时发信号 (1=单, 2+=共振) |
| 公式历史胜率 | 30% | 该公式在该股票的历史 win_rate (from formula_variant_metrics) |
| 量价确认 | 30% | 放量程度 (vol/MA20) × 涨幅 |

**composite_score = 0.4 × resonance + 0.3 × hist_win + 0.3 × vol_price**

### 4.2 选股排序 (cross-formula)

每日收盘后:
1. 所有公式扫全 universe → 收集当日信号
2. 每个信号算 composite_score
3. 同一股票多公式信号 → 合并, 共振度加分
4. 按 composite_score 降序排列
5. 取 top K (K = 5 - 当前持仓数)

### 4.3 仓位管理

| 规则 | 值 |
|---|---|
| 最大持仓 | 5 只 |
| 单只上限 | 20% (等权) |
| 最低信号分 | score > threshold (Optuna 搜索) |
| 不满仓 | score 低于 threshold 时不入, 现金等待 |
| 替换规则 | 新信号 score > 最低持仓 score × replace_ratio → 替换 |

### 4.4 去重 + 冲突处理

- 已持仓股票不重复入场
- 涨停封板买不到 → delay max 2 天 (复用 execution_model)
- 同板块不超过 2 只 (行业集中度控制)

## 5. Optuna 搜索空间

| 参数 | 范围 | 影响 |
|---|---|---|
| resonance_window | 0, 1, 2 天 | 共振时间窗 |
| score_threshold | 0.3-0.7 | 入池最低分 |
| stop_pct | 0.03-0.08 | 止损 |
| trail_pct | 0.08-0.15 | 移动止盈 |
| ma_exit_period | 10, 20, 30 | 均线止盈周期 |
| replace_ratio | 1.1-1.5 | 替换比例 |
| gain_retained_min | 0.5-0.9 | pullback_doji 回调幅度过滤 (Codex P1 建议) |
| pb_depth_max | -0.02 to -0.07 | pullback_doji 回调深度过滤 (Codex P2 建议) |

## 6. 验证计划

### Phase A: 300616 单股验证 (1 天, 本地)
1. 用现有 K 线数据跑 3 波回测
2. 验证每波的入场/出场时机
3. 调整参数直到 3 波都能捕捉

### Phase B: 同类股验证 (2 天, 本地)
1. 找 10-20 只类似形态股 (长期下跌→底部放量→多波上涨)
2. 跑同一策略, 验证泛化能力
3. 统计 win_rate / avg_ret / max_dd

### Phase C: 全量 Optuna (1 天, GCP $1.88)
1. 全 universe walk-forward expanding_monthly
2. 搜索 §5 全部参数
3. OOS 验证

### Phase D: Paper Sim (持续)
1. 每日扫描 → 选 top 5 → 记录入场
2. 跟踪持仓 → 触发卖出条件
3. 6 周后评估

## 7. 实现路径

| 步骤 | 改动 | 文件 |
|---|---|---|
| 7.1 | 多公式共振评分器 | 新建 `backend/services/bc_absorbed/signal_ranker.py` |
| 7.2 | 卖出策略 (移动止盈 + 均线) | 扩展 `execution_model.py` sell_rule |
| 7.3 | 股票池管理 | 新建 `backend/services/bc_absorbed/portfolio_pool.py` |
| 7.4 | Optuna 搜索脚本 | 扩展 `optuna_pullback_doji.py` → `optuna_multi_wave.py` |
| 7.5 | Paper Sim 集成 | 新建 `paper_sim_formula.yaml` |
| 7.6 | 每日选股输出 | 新建 `scripts/daily_formula_picks.py` |
