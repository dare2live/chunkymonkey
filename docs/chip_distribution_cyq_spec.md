# 筹码分布 (CYQ) 算法与应用规格书

> 基于 2026-05-27 session 的完整研究：算法验证、数据源评估、应用场景设计。
> 接手人读完本文档可直接实现，无需额外上下文。

---

## 目录

| 章节 | 内容 |
|------|------|
| 1 | 什么是筹码分布 |
| 2 | 算法实现（已验证） |
| 3 | 验证结果（两只股票 vs 通达信） |
| 4 | 数据依赖与现状 |
| 5 | 派生指标定义 |
| 6 | 应用场景：持仓监控（主用途） |
| 7 | 应用场景：主力行为画像 |
| 8 | 应用场景：回测假说 |
| 9 | 不做什么（边界） |
| 10 | 实现路径建议 |

---

## 1. 什么是筹码分布

筹码分布 (CYQ, Cost of Your Quantity) 是从历史 K 线 + 流通盘推算出的"全体持仓者成本价分布"。

**核心思想**：每天的成交量代表一部分筹码换手。旧持仓者以一定比例卖出（等比例衰减），新买入者按当日价格区间分布。逐日迭代后，得到截至当日的持仓成本分布曲线。

**本质**：CYQ 是 OHLCV + 流通盘的数学变换，不含独立新信息。它的价值在于把散落在数百天 K 线中的量价记忆压缩成一个直观的分布图。

---

## 2. 算法实现

### 2.1 输入

| 字段 | 来源 | 说明 |
|------|------|------|
| `date` | `price_kline_tdxhub` | 交易日 |
| `open, high, low, close` | 同上 | 前复权 (qfq) 日线价格 |
| `volume` | 同上 | 成交量（单位：手，1手=100股） |
| `amount` | 同上 | 成交额（单位：元） |
| `float_shares` | `fact_financial_derived` + `fact_holder_count_period` | 流通股本（单位：股），需要历史序列 |

### 2.2 核心算法：三角形分布 + 指数衰减

```python
import numpy as np

def compute_cyq(dates, opens, highs, lows, closes, volumes, amounts, float_shares_series):
    """
    计算截至最后一个交易日的筹码分布。

    Parameters:
        dates: array of dates
        opens/highs/lows/closes: array of float, 前复权价格
        volumes: array of float, 成交量（手）
        amounts: array of float, 成交额（元）
        float_shares_series: array of float, 每日对应的流通股本（股）

    Returns:
        prices: array, 价格网格（0.01 元步长）
        chips: array, 每个价格点的筹码占比（归一化到总和≈1）
    """
    TICK = 0.01

    # 价格网格
    price_min = min(lows) * 0.90
    price_max = max(highs) * 1.10
    prices = np.arange(price_min, price_max + TICK, TICK)
    n_prices = len(prices)
    chips = np.zeros(n_prices)

    def price_to_idx(p):
        return int(round((p - price_min) / TICK))

    for i in range(len(dates)):
        volume_shares = volumes[i] * 100  # 手 → 股
        turnover_rate = min(volume_shares / float_shares_series[i], 1.0)
        vwap = amounts[i] / volume_shares  # 当日成交均价

        # Step 1: 存量筹码等比例衰减
        chips *= (1.0 - turnover_rate)

        # Step 2: 新增筹码按三角形分布到 [low, high]，峰值在 vwap
        lo, hi = lows[i], highs[i]
        i_lo = max(0, price_to_idx(lo))
        i_hi = min(n_prices - 1, price_to_idx(hi))

        if i_lo >= i_hi:
            # 一字板或极窄振幅
            idx = max(0, min(n_prices - 1, price_to_idx(vwap)))
            chips[idx] += turnover_rate
            continue

        i_vwap = max(i_lo, min(i_hi, price_to_idx(vwap)))
        dist = np.zeros(i_hi - i_lo + 1)
        for j in range(len(dist)):
            p_idx = i_lo + j
            if p_idx <= i_vwap:
                dist[j] = (p_idx - i_lo) / max(1, (i_vwap - i_lo))
            else:
                dist[j] = (i_hi - p_idx) / max(1, (i_hi - i_vwap))

        s = dist.sum()
        if s > 0:
            chips[i_lo:i_hi + 1] += dist / s * turnover_rate

    return prices, chips
```

### 2.3 关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 价格数据 | 前复权 (qfq) | 使分布直接对应当前价格坐标，获利比例等指标可直接比较 |
| 分布核函数 | 三角形（峰在 VWAP） | 验证精度最高，TDX 官方也用类似方法 |
| 衰减模型 | `chips *= (1 - turnover_rate)` | 每个价位等比例衰减，即假设每个价位被卖出的概率相同 |
| 流通盘 | 使用历史序列（非固定值） | 科创板/次新股的锁定期会导致流通盘大幅变化，固定值会严重偏差 |
| TICK | 0.01 元 | 精度足够，内存可控（价格范围 100 元 = 10000 个 bin） |

### 2.4 流通盘历史序列构建

流通盘不是常数。必须按时间对应正确的流通股本，否则换手率计算失真。

**数据源**：`fact_holder_count_period` 表的 `holder_count * avg_float_shares` 可推算出每个报告期的流通盘。

**构建方法**：

```python
# float_schedule = [(date_str, float_shares_int), ...]
# 从 fact_holder_count_period 按 report_date 升序取
# 对每个 K 线 bar，使用最近一个 <= bar.date 的 float_shares

df['float_shares'] = float_schedule[0][1]  # 默认用最早的
for fdate, fval in float_schedule:
    mask = df['date'] >= fdate
    df.loc[mask, 'float_shares'] = fval
```

**实测影响**：688283 坤恒顺维 IPO 初期流通盘仅 1750 万股，后逐步增至 1.218 亿股。不做历史修正 → 早期换手率被低估 7 倍。

---

## 3. 验证结果

### 3.1 验证方法

用本项目 `data/market.duckdb` 中的 `price_kline_tdxhub` 数据计算 CYQ，与通达信手机端截图数值逐项对比。

### 3.2 验证股票 1：688283 坤恒顺维（科创板小盘，2190 万流通盘）

截止 2026-05-26，参考价 80.88 元。

| 指标 | 算法计算 | 通达信 | 偏差 |
|------|----------|--------|------|
| 获利比例 | 41.5% | 40.4% | +1.1% |
| 平均成本 | 43.63 元 | 45.00 元 | -1.37 元 |
| 90% 区间下限 | 32.63 元 | 33.00 元 | -0.37 元 |
| 90% 区间上限 | 50.76 元 | 50.76 元 | **精确匹配** |
| 30 周期内成本 | 51.1% | 51.1% | **精确匹配** |
| 60 周期内成本 | 61.5% | 62.1% | -0.6% |

### 3.3 验证股票 2：300124 汇川技术（创业板大盘，24 亿流通盘）

截止 2026-05-26，参考价 80.88 元。

| 指标 | 算法计算 | 通达信 | 偏差 |
|------|----------|--------|------|
| 获利比例 | 92.0% | 92.1% | **-0.1%** |
| 平均成本 | 73.62 元 | 74.50 元 | -0.88 元 |
| 90% 区间下限 | 64.30 元 | 64.30 元 | **精确匹配** |
| 90% 区间上限 | 81.85 元 | 81.80 元 | +0.05 元 |
| 60 周期内成本 | 58.7% | 58.7% | **精确匹配** |
| 100 周期内成本 | 76.7% | 76.9% | -0.2% |

### 3.4 已知偏差

| 偏差 | 原因 | 影响 |
|------|------|------|
| 平均成本偏低约 1 元 | qfq 价格 vs 通达信可能用不复权 | 小，不影响相对判断 |
| 短期（5/10天）内成本偏高 1-2% | 通达信可能对换手率做衰减系数处理 | 小，长期指标几乎无偏 |
| 集中度公式不匹配 | 通达信集中度的具体公式未公开 | 暂不使用该指标 |

### 3.5 结论

**获利比例、90% 筹码区间、N 周期内成本**精度足够，可直接用于生产。平均成本有约 1 元偏差但不影响趋势判断。

---

## 4. 数据依赖与现状

### 4.1 CYQ 计算所需

| 数据 | 表 | 库 | 截至日期 | 状态 |
|------|-----|-----|----------|------|
| 日线 OHLCV | `price_kline_tdxhub` | `market.duckdb` | 2026-05-26 | OK，daily sync |
| 流通股本历史 | `fact_holder_count_period` | `smartmoney.duckdb` | 季报级 | OK，但粒度粗 |
| 流通股本最新 | `fact_financial_derived` | `smartmoney.duckdb` | 2026-04 | OK |

### 4.2 主力画像辅助数据

| 数据 | 表 | 截至日期 | 状态 |
|------|-----|----------|------|
| 资金流向（主力/大单/中单/小单） | `raw_fund_flow_daily` | **2026-04-24** | **deprecated/stale，先 source probe + PIT/freshness gate，不直接恢复生产使用** |
| 资金行为代理 | `fact_capital_flow_pit_daily` | 2026-05-26 | OK，但只是 PIT proxy/事件聚合，不等同真实订单流 |
| 龙虎榜 | `fact_lhb_event` | 2026-05-29（84 codes, sparse-event） | OK / sparse |
| 高管增减持 | `fact_executive_trade_event` | 有 | OK |
| 股东增减持计划 | `fact_shareholder_plan` | 有 | OK |
| 机构调研 | `fact_jgdy_event` | 有 | OK |
| 解禁信息 | `raw_capital_unlock` | 有 | OK |
| 股东户数 | `fact_holder_count_period` | 季报 | OK，低频 |

> 2026-06-01 audit note: current `data_sources` registry exposes `tdxhub` / `aif10` / `akshare`; `need_027`'s declared fallback label `miaoxiang` maps to the `aif10` family, but the current aif10 adapter still does not expose `individual_fund_flow`, so that fallback remains conceptual until the route mapping and capability are made explicit. `akshare.stock_fund_flow_individual` now exists as a 10jqka research-side rank snapshot and can supplement behavior research, but it is not an exact `need_027` replacement.

### 4.3 资金流向字段说明（`raw_fund_flow_daily`）

| 字段 | 含义 |
|------|------|
| `main_net_amount` | 主力净流入额 = 超大单 + 大单净额 |
| `main_net_pct` | 主力净流入占比 (%) |
| `super_large_net_amount` | 超大单（>100万）净流入额 |
| `large_net_amount` | 大单（20-100万）净流入额 |
| `medium_net_amount` | 中单（5-20万）净流入额 |
| `small_net_amount` | 小单（<5万）净流入额 |

数据源为东财（akshare），非 tdxhub 原生。当前本地 `raw_fund_flow_daily` 已确认 stale/deprecated,
且无 active writer。后续不能简单“恢复 daily sync”后直接进入生产, 必须先完成 source probe、
字段/反爬稳定性、PIT availability、freshness gate 和数据资产登记。恢复前, CYQ 主力画像中的真实
订单流维度必须输出 `unknown`; 若使用 `fact_capital_flow_pit_daily`, 必须标记为 `proxy`。

---

## 5. 派生指标定义

从筹码分布和辅助数据计算以下指标。只算这几个，不存完整分布。

### 5.1 CYQ 基础指标

```python
def compute_cyq_metrics(prices, chips, current_price):
    total = chips.sum()
    cumsum = np.cumsum(chips)

    def cost_pct(pct):
        idx = np.searchsorted(cumsum, total * pct / 100)
        return prices[min(idx, len(prices) - 1)]

    return {
        # 获利比例：当前价以下的筹码占比
        'winner_rate': cumsum[price_to_idx(current_price)] / total,

        # 平均成本
        'avg_cost': np.dot(prices, chips) / total,

        # 90% 筹码区间
        'cost_5pct': cost_pct(5),    # 下界
        'cost_95pct': cost_pct(95),  # 上界

        # 中位成本
        'cost_50pct': cost_pct(50),
    }
```

### 5.2 应用级指标

```python
def compute_application_metrics(prices, chips, current_price, target_price=None):
    total = chips.sum()

    def chip_between(p1, p2):
        """p1 到 p2 之间的筹码占比"""
        i1 = max(0, price_to_idx(p1))
        i2 = min(len(prices) - 1, price_to_idx(p2))
        return chips[i1:i2 + 1].sum() / total

    metrics = {}

    # 上方套牢盘占比（阻力指标）
    metrics['overhead_pressure'] = chip_between(current_price, prices[-1])

    # 目标价阻力（如果有目标价）
    if target_price and target_price > current_price:
        metrics['target_overhead'] = chip_between(current_price, target_price)

    # 成本偏离率 = (当前价 - 平均成本) / 平均成本
    avg_cost = np.dot(prices, chips) / total
    metrics['cost_deviation'] = (current_price - avg_cost) / avg_cost

    # 底部峰锁仓率：cost_5pct 以下到 cost_25pct 区间的筹码占比
    # 用于判断主力是否还在
    cumsum = np.cumsum(chips)
    cost_25 = prices[np.searchsorted(cumsum, total * 0.25)]
    metrics['bottom_lock_rate'] = chip_between(prices[0], cost_25)

    return metrics
```

### 5.3 N 周期筹码年龄

```python
def compute_chip_age(turnover_rates, periods=[5, 10, 20, 30, 60]):
    """
    计算最近 N 个交易日内换手的筹码占总筹码的比例。
    turnover_rates: array, 按日期升序的每日换手率
    """
    n = len(turnover_rates)
    chip_weights = np.zeros(n)
    remaining = 1.0
    for k in range(n):
        idx = n - 1 - k  # 从最近一天往回
        tr = min(turnover_rates[idx], 1.0)
        chip_weights[k] = remaining * tr
        remaining *= (1 - tr)

    return {f'within_{N}d': np.sum(chip_weights[:N]) for N in periods}
```

---

## 6. 应用场景：持仓监控（主用途）

**定位**：买入后每日跑批，辅助判断是否提前卖出或延长持有。不改模型参数，不选股。

### 6.1 每日跑批流程

```
每日收盘后（15:30 之后）:
  for each 持仓股:
    1. 增量更新 CYQ（只需当日 K 线 + 前一日分布）
    2. 计算 5 个指标: winner_rate, overhead_pressure, cost_deviation,
                       bottom_lock_rate, within_5d
    3. 拉取当日资金流向 + 检查龙虎榜触发
    4. 输出: 阶段判定 + 信号标签
```

### 6.2 提前卖出信号

满足以下任意两条即触发提前卖出预警：

| # | 条件 | 逻辑 |
|---|------|------|
| S1 | `bottom_lock_rate` 连续 3 天下降 > 5% | 底部筹码在转移，主力可能在出货 |
| S2 | `winner_rate > 0.95` 且当日换手率 > 2 倍 20 日均值 | 获利盘集体兑现 |
| S3 | `main_net_amount` 连续 3 天为负 | 主力资金持续流出 |
| S4 | 龙虎榜触发 + `is_inst_net_buy = 0`（机构净卖） | 机构在跑 |
| S5 | 放量滞涨：换手率 > 1.5 倍均值但涨幅 < 1% | 大量换手但拉不动 |

### 6.3 延长持有信号

| # | 条件 | 逻辑 |
|---|------|------|
| H1 | `bottom_lock_rate` 稳定（日波动 < 2%） | 主力没动 |
| H2 | `overhead_pressure < 0.05` | 上方几乎无阻力 |
| H3 | 缩量回调但不破 `cost_50pct`（中位成本） | 正常洗盘 |
| H4 | `main_net_amount` > 0 持续 | 主力仍在加仓 |

### 6.4 实例：001225 和泰机电 (2026-05-26)

```
获利比例:       96.7%     ← S2 前置条件满足
底部峰(52以下): 30.3%     ← 主力仍有底仓
上方套牢盘:     3.3%      ← 无阻力
5日换手:        98%       ← 极高
龙虎榜 05-25:   净卖 444万 ← S4 触发（但无机构席位）
龙虎榜 05-22:   净卖 1682万 ← S4 触发

判定: 试盘阶段，但龙虎榜连续净卖 → 需警惕
实际: 05-27 缩量跌停 → S5(放量滞涨) + S4(龙虎榜净卖) 双触发
      如果持仓，应在 05-26 收盘后收到预警
```

---

## 7. 应用场景：主力行为画像

### 7.1 四阶段模型

```
吸筹 → 试盘/拉升 → 出货 → 下跌
```

**注意**：这是 per-stock 时序判定，不是全市场截面模型。

### 7.2 判定规则

| 阶段 | CYQ 特征 | 量价特征 | 资金/事件 |
|------|----------|----------|-----------|
| **吸筹** | 低位单峰密集，集中度高 | 缩量横盘（日换手 < 2%），股价窄幅震荡 | 股东户数季度下降，偶现机构调研 |
| **试盘/拉升** | 底部峰不动 + 新峰出现 | 放量突破（换手骤升 3x+），连阳 | 主力净流入放大 |
| **出货** | 底部峰消失，高位新峰形成 | 放量滞涨，高位长上影 | 龙虎榜净卖，机构席位出现在卖方 |
| **下跌** | 筹码发散，无密集峰 | 缩量阴跌 | 高管减持，解禁压力 |

### 7.3 数据维度组合

```
主力画像 = f(筹码结构, 量价行为, 资金流向, 事件信号)

维度 1: 筹码结构 (CYQ) ← 从 K 线 + 流通盘每日计算
  · winner_rate         获利比例
  · cost_deviation      当前价 vs 平均成本偏离
  · bottom_lock_rate    底部筹码锁仓率
  · overhead_pressure   上方套牢盘占比
  · 90% 区间宽度        筹码集中度代理

维度 2: 量价行为 ← K 线直接计算
  · 量比（当日成交 / 5日均量）
  · N 日累计换手率
  · 上影线比例 = (high - close) / (high - low)
  · 放量滞涨检测 = 换手率 > 1.5x 均值 & 涨幅 < 1%

维度 3: 资金流向 ← raw_fund_flow_daily (恢复前为 unknown; fact_capital_flow_pit_daily 只能 proxy)
  · 主力净流入连续天数及金额
  · 超大单占比变化
  · 主力净额 vs 股价方向是否背离

维度 4: 事件信号 ← 多表
  · 龙虎榜: 机构席位方向 (fact_lhb_event)
  · 高管增减持 (fact_executive_trade_event)
  · 股东户数季度变化 (fact_holder_count_period)
  · 解禁日临近 (raw_capital_unlock)
  · 机构调研频次 (fact_jgdy_event)
```

### 7.4 按市值分层

| 市值档 | 特点 | 参数调整 |
|--------|------|----------|
| 小盘 (< 50 亿) | 换手高、筹码转换快、易操控 | 换手阈值放宽（如放量 = 2x 而非 1.5x） |
| 中盘 (50-300 亿) | 混合 | 标准参数 |
| 大盘 (> 300 亿) | 筹码稳定、机构主导 | 更侧重资金流向和机构行为，CYQ 权重降低 |

---

## 8. 应用场景：回测假说

以下假说可用历史数据验证，按优先级排序。

### P0: 上方筹码密度 vs 实际涨幅达成率

- **假说**：公式发出 buy signal 后，`target_overhead`（当前价到目标价间的套牢盘占比）越高，实际涨幅达成率越低。
- **方法**：取所有历史 buy signal，按 `target_overhead` 分组（0-5%, 5-15%, 15-30%, >30%），统计 N 日后实际涨幅。
- **预期价值**：如果显著，可对公式目标价做"筹码折扣"。

### P0: 获利比例极值反转

- **假说**：`winner_rate > 90%` 后 N 日跌幅大于随机；`winner_rate < 10%` 后 N 日涨幅大于随机。
- **方法**：全 A 股历史，按 winner_rate 分位统计 forward 5/10/20 日收益。
- **预期价值**：如果显著，可作为持仓出场的辅助信号。

### P1: 底部筹码锁仓 vs 上涨持续性

- **假说**：涨幅 > 30% 时，`bottom_lock_rate` 仍 > 20% 的股票后续继续上涨概率更高。
- **预期价值**：判断"还能不能追"。

### P1: 筹码密集区回调支撑

- **假说**：回调触及筹码密集峰附近时反弹概率更高。
- **预期价值**：优化止损位设定。

---

## 9. 不做什么（边界）

| 不做 | 原因 |
|------|------|
| **不把 CYQ 指标当 LightGBM feature** | 与现有价量特征高度共线，边际 RankIC 预估 < 0.005 |
| **不存完整分布到数据库** | 每只股票每天一条分布太重，只存 5-8 个派生指标 |
| **不做全市场截面排序** | 筹码分布是路径依赖的 per-stock 指标，截面排序无意义 |
| **不用于选股 alpha** | 本质是 OHLCV 的变换，真正的增量信息在机构行为/资金流向 |
| **不模仿通达信的"集中度"指标** | 公式未公开，验证不通过，暂不使用 |

---

## 10. 实现路径建议

### Phase 1: 基础设施（1-2 天）

1. **资金流向 source probe 与契约登记**
   - `raw_fund_flow_daily` 停在 2026-04-24，且已登记 deprecated/stale
   - 先在 `tdx_data_need_coverage.yaml` 保持主力/超大/大/中/小单资金需求, 再探测 akshare capability 与 miaoxiang/aif10 家族路由的字段、PIT availability、freshness 和反爬稳定性
   - 通过 gate 前, 真实订单流维度输出 `unknown`; `fact_capital_flow_pit_daily` 只能作为 proxy 辅助解释

2. **编写 `backend/services/chip_distribution.py`**
   - 实现 `compute_cyq()` 和 `compute_cyq_metrics()`（本文档第 2、5 节的代码）
   - 入口函数: `get_stock_cyq_metrics(stock_code, as_of_date) -> dict`
   - 内部缓存前一日分布，增量更新只需当日 K 线

3. **流通盘历史表**
   - 从 `fact_holder_count_period` 构建 `dim_float_shares_history`
   - grain: `(stock_code, effective_date, float_shares)`

### Phase 2: 持仓监控脚本（1 天）

1. **编写 `backend/scripts/run_position_monitor.py`**
   - 读取当前持仓列表（`fact_paper_position` 或手动配置）
   - 对每只持仓股计算 CYQ 指标 + 拉资金流向 + 检查龙虎榜
   - 输出信号标签（提前卖出 / 延长持有 / 正常）
   - 结果写入 `mart_position_cyq_monitor` 或直接输出 JSON

### Phase 3: 回测验证（2-3 天）

1. 实现全量历史 CYQ 计算（需优化性能：增量更新 + 可能做并行）
2. 跑 P0 假说：overhead_pressure vs 涨幅达成率
3. 跑 P0 假说：winner_rate 极值反转
4. 根据回测结果决定是否正式纳入日常流程

### 性能估算

| 操作 | 耗时估算 |
|------|----------|
| 单只股票 1000 天 CYQ | ~0.5 秒（Python 纯循环） |
| 全 A 股 5000 只 × 1000 天 | ~40 分钟（可 numpy 向量化优化到 5-10 分钟） |
| 每日增量更新 5000 只 | < 1 分钟（只算当日增量） |

---

## 附录 A: 验证复现命令

以下代码可在项目根目录直接运行，复现本文档第 3 节的验证结果。

```bash
python3 -c "
import duckdb, numpy as np, pandas as pd

STOCK = '300124'  # 或 '688283'
con_m = duckdb.connect('data/market.duckdb', read_only=True)
df = con_m.execute(f'''
    SELECT date, open, high, low, close, volume, amount
    FROM price_kline_tdxhub
    WHERE code = '{STOCK}' AND freq = 'daily'
    ORDER BY date ASC
''').fetchdf()
con_m.close()

con_s = duckdb.connect('data/smartmoney.duckdb', read_only=True)
holder = con_s.execute(f'''
    SELECT report_date, holder_count * avg_float_shares as implied_float
    FROM fact_holder_count_period
    WHERE stock_code = '{STOCK}' AND avg_float_shares > 0
    ORDER BY report_date ASC
''').fetchdf()
con_s.close()

# Build float schedule
float_schedule = list(zip(
    holder['report_date'].astype(str).tolist(),
    holder['implied_float'].tolist()
))
df['date_dt'] = pd.to_datetime(df['date'])
df['float_shares'] = float_schedule[0][1]
for fdate, fval in float_schedule:
    df.loc[df['date_dt'] >= pd.Timestamp(fdate), 'float_shares'] = fval

df['volume_shares'] = df['volume'] * 100
df['turnover_rate'] = df['volume_shares'] / df['float_shares']
df['vwap'] = df['amount'] / df['volume_shares']

TICK = 0.01
price_min = float(df['low'].min()) * 0.90
price_max = float(df['high'].max()) * 1.10
prices = np.arange(price_min, price_max + TICK, TICK)
n_prices = len(prices)

def p2i(p):
    return int(round((p - price_min) / TICK))

chips = np.zeros(n_prices)
for i in range(len(df)):
    tr = min(float(df['turnover_rate'].iloc[i]), 1.0)
    lo, hi = float(df['low'].iloc[i]), float(df['high'].iloc[i])
    vw = float(df['vwap'].iloc[i])
    chips *= (1.0 - tr)
    i_lo, i_hi = max(0, p2i(lo)), min(n_prices-1, p2i(hi))
    if i_lo >= i_hi:
        chips[max(0, min(n_prices-1, p2i(vw)))] += tr
        continue
    i_vw = max(i_lo, min(i_hi, p2i(vw)))
    dist = np.zeros(i_hi - i_lo + 1)
    for j in range(len(dist)):
        px = i_lo + j
        dist[j] = ((px - i_lo) / max(1, i_vw - i_lo) if px <= i_vw
                    else (i_hi - px) / max(1, i_hi - i_vw))
    s = dist.sum()
    if s > 0:
        chips[i_lo:i_hi+1] += dist / s * tr

total = chips.sum()
cumsum = np.cumsum(chips)
cp = float(df['close'].iloc[-1])

def cost_pct(pct):
    return prices[min(np.searchsorted(cumsum, total*pct/100), n_prices-1)]

print(f'Stock: {STOCK}, Date: {df[\"date\"].iloc[-1]}, Close: {cp:.2f}')
print(f'Winner rate: {cumsum[p2i(cp)]/total*100:.1f}%')
print(f'Avg cost: {np.dot(prices,chips)/total:.2f}')
print(f'90%% range: {cost_pct(5):.2f} ~ {cost_pct(95):.2f}')
"
```

## 附录 B: 关键表速查

| 表 | 库 | 用途 |
|-----|-----|------|
| `price_kline_tdxhub` | `market.duckdb` | K 线 OHLCV（qfq） |
| `fact_financial_derived` | `smartmoney.duckdb` | 最新流通股本 |
| `fact_holder_count_period` | `smartmoney.duckdb` | 历史流通盘推算 + 股东户数 |
| `raw_fund_flow_daily` | `smartmoney.duckdb` | 主力/大单资金流向；当前 deprecated/stale, 不可作生产证据 |
| `fact_lhb_event` | `smartmoney.duckdb` | 龙虎榜事件 |
| `fact_executive_trade_event` | `smartmoney.duckdb` | 高管增减持 |
| `fact_jgdy_event` | `smartmoney.duckdb` | 机构调研 |
| `raw_capital_unlock` | `smartmoney.duckdb` | 解禁信息 |
| `fact_capital_flow_pit_daily` | `smartmoney.duckdb` | 龙虎榜/高管信号聚合（PIT proxy, 不是真实订单流） |
