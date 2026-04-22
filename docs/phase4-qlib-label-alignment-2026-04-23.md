# Phase 4：Qlib 标签对齐首版设计

日期：2026-04-23
依据：`docs/discussion-report-2026-04-22.md` §14.3 + Phase 3 对 `primary_action` 的交付要求
范围：**设计方案**，不含实施代码。实施由后续独立 PR 承载。

## 1. 当前现状

- Label: `Ref($close, -2)/Ref($close, -1) - 1` —— next-day 截面收益
- 标签归一化：`CSZScoreNorm`（`qlib_full_engine.py:63`）
- 最近 backtest（4/15 lgb 模型）：Sharpe=2.09, Calmar=8.96, MaxDD=-3.8%, 年化=34%
- `qlib_predictions` 最新 `predict_date=2026-04-13`, 21 个模型快照

指标看起来不错，但这是 **next-day 截面排序**能力，**不对齐业务目标**。本项目要优化的是：**机构披露信号发出后的中期持有收益与回撤控制**。当前标签最优解，可能是"最早卖出的快刀手"策略，而不是"跟住机构行为的长期持有"策略。

## 2. 首版标签定义

### 2.1 持有期：**20 交易日**

理由：

- 机构披露周期约为一个季度（60 交易日），但披露触发后的最强 alpha 信号通常在前 1/3 消化完成（对应 20 天）
- 20 天既能观察到机构事件后续收益，也能看到回撤结构
- 避免与季度重叠导致"标签末日恰逢下一季披露"的噪声

备选方案：

| 持有期 | 优点 | 缺点 |
| --- | --- | --- |
| 10 交易日 | 信号新鲜，易学 | 太短，易被短期波动污染 |
| **20 交易日 ✓** | 与披露节奏匹配，信号含量高 | - |
| 60 交易日 | 全季度覆盖 | 股价受多因素影响过多，机构信号稀释 |

### 2.2 drawdown 约束：**-8% 起跳，做敏感性**

理由：

- A 股日频波动约 1.5-2%，20 日累计波动约 7-9%
- -8% 是"中性下跌"阈值：低于这个说明机构事件质量高，超过则可能已证伪
- 与业务目标"回撤最小化"直接对齐

敏感性扫描参数（实施时做）：`-5% / -8% / -10% / -12%`，观察哪个阈值下 follow 组样本量、年化收益、最大回撤三者 pareto 最优。

### 2.3 标签形态：**三分类**

选 **classification** 而不是 regression，原因：

- 三分类天然对齐 `primary_action` 的 `follow / watch / avoid` 三值
- 回归标签需要额外阈值才能对应业务动作，多一层人工决策
- CSZScoreNorm 归一化后的回归标签不稳定（高波动样本占优）

**三分类定义**：

```python
# 对每个样本日，计算未来 20 日的：
forward_ret_20d = Ref($close, -21) / Ref($close, -1) - 1
max_drawdown_20d = Min(Ref($close, 0..-19)) / Ref($close, -1) - 1  # 负值

# 分类（在同一天的截面上相对定义）：
label = 3 (follow)  if forward_ret_20d >= p60(同日) AND max_drawdown_20d >= -0.08
label = 1 (avoid)   if forward_ret_20d <= p40(同日) OR  max_drawdown_20d <= -0.15
label = 2 (neutral) otherwise
```

关键设计点：

- **follow 需要同时满足收益 & 回撤**：不是只看 20d 涨幅（避免"大涨大跌"样本混进 follow）
- **avoid 满足任一条件即触发**：任何一个负面证据都足以回避
- **截面阈值 p60/p40**：用同一天全市场样本的分位，避免绝对阈值在牛熊市不同步

### 2.4 辅助回归输出（并行保留）

除了三分类主标签外，保留：

- `forward_ret_20d_reg`：回归版本，用于对比和 IC 监控
- `max_drawdown_20d_reg`：回撤值，用于风险敞口展示

这两个不是训练目标，但作为预测产物一起输出到 `qlib_predictions`，供前端"展开计算明细"面板用。

## 3. `primary_action` 的权重方案

### 3.1 冷启动期（0-60 交易日样本）

目标：**不依赖回测标定**，用项目先验给出合理起点。权重如下：

| 输入维度 | 权重 | 来源字段 |
| --- | --- | --- |
| 机构层评分聚合 | **40%** | `quality_score` + `followability_score` 加权（q*0.4 + f*0.6，沿用 setup 里的比例） |
| signals_v2 信号 | **25%** | EV + 胜率 + 样本充足度 |
| 公司财务质量 | **20%** | `company_quality_score`（`fact_stock_quality_features.quality_score_v1`) |
| 阶段与预测 | **15%** | `stage_score` + `forecast_score_effective` 平均 |

映射到动作：

```
primary_action = follow  if primary_score >= 65 AND 机构层聚合分 >= 55
primary_action = avoid   if primary_score <= 40 OR  机构层聚合分 <= 30
primary_action = watch   otherwise
```

约束"机构层聚合分 >= 55" 是硬门槛——不满足就不能 follow，确保"机构是主角"。

### 3.2 成熟期（60+ 交易日样本）

当 Qlib 新标签训练 60 个交易日后：

1. **用 Qlib 三分类模型预测值** 作为 `primary_action` 的**主输入**（占 50%）
2. **机构层评分聚合** 权重降为 30%（仍然是次高）
3. **signals_v2、公司质量、阶段** 合计占 20%

这样做的理由：冷启动期用项目先验，成熟期让回测验证过的 Qlib 主导。先验权重是安全垫，实际权重由数据说话。

### 3.3 过渡策略

- **冷启动期** UI 标注：前端主动作列右上角显示灰色 "探索期" 徽章，悬停提示 "样本 < 60 日，结论仅供参考"
- **成熟期** 切换节点：当 `fact_stock_stage_features` 快照日数 ≥ 60 且 Qlib 新标签模型通过 IC > 0.03 验证，自动切换
- **切换不一刀切**：先在 `qlib_full_engine.py` 配置里加 `primary_action_mode = "cold" | "mature" | "hybrid"`，hybrid 阶段同时展示两种结果供对比

## 4. 实施路线

### 4.1 Phase 4a：新增标签配置（本 PR 不做，由后续独立 PR 承载）

- `backend/services/qlib_full_engine.py` 增加 `_QLIB_LABEL_V2_CONFIG`
- 保留老标签 `LABEL0`（对比组）+ 新三分类 `ACTION_LABEL` + 两个回归辅助
- 训练时同时跑两个模型（`lgb_legacy` 与 `lgb_action_v2`），对比 IC 和实际收益

### 4.2 Phase 4b：冷启动期 `primary_action` 实施

- `scoring.py` 新增 `calculate_primary_action(conn)` 函数
- 写入 `mart_stock_trend.primary_action` 字段
- 前端 `app.js` 新增主动作列（见 Phase 3 的双列改造规则）
- 不修改 legacy stock_gate 写入逻辑，保证向后兼容

### 4.3 Phase 4c：成熟期切换

- 实现 `primary_action_mode` 配置切换
- 添加健康检查：每天记录新标签 IC、MaxDD、准确率，观察权重切换的稳健性

## 5. 风险与防护

| 风险 | 触发条件 | 应对 |
| --- | --- | --- |
| 冷启动期假信号 | 样本少时先验权重偏移 | UI 显著标注"探索期"，用户不应据此下大仓位 |
| 新标签 IC 低于老标签 | Qlib 新训练 IC < 0.02 | 保留老标签，暂不切换到成熟期模式 |
| drawdown 阈值过严 | follow 样本 < 5% | 敏感性扫描自动选择样本覆盖 ≥ 10% 的阈值 |
| 机构层聚合硬门槛卡死 | 某些优质股因机构层低被拒 | 加"次要豁免"：若 company_quality + signals_v2 都极高（>75），允许 follow |

## 6. 与前序报告的关系

- §14.3 的"中期 forward return + drawdown 组合"：✓ 已实例化为 20 交易日 + -8% 基线
- §14.3 "标签形态（回归 or 三分类）"：✓ 选三分类 + 回归辅助
- §14.6 "允许从归档 Phase A-G 选择性恢复"：不覆盖本 Phase，Phase A-G 中的 behavior/supply 因子等新因子，**必须在新标签验证后再决定是否纳入**
- §14.7 60 天窗口：本设计文档属于"Qlib 标签对齐首版"的方案产出，实施在 60 天内完成

## 7. 本 Phase 不做的事情

- ❌ 不修改现有 `_QLIB_LABEL_CONFIG`——Phase 4a 单独 PR 添加新配置
- ❌ 不动 `scoring.py` 的 `composite_priority_score` 公式——`primary_action` 是新字段
- ❌ 不训练新模型——训练需要重跑数据流水线，风险大，留给实施 PR
- ❌ 不动前端——前端改造由 Phase 3 决策书的双列规则在 Phase 4b 带入

## 8. 验收标准

Phase 4 设计文档（本文件）交付后，被以下任一条件推翻应视为**设计需要修订**：

1. 业务方确认 20 交易日持有期不符合使用习惯（应明确改为 X 天）
2. 历史回测显示三分类标签 IC 显著低于回归标签（降到 < 0.5 倍）
3. 冷启动期先验权重在 5 只代表性股票的手工复核中明显违背直觉

否则本设计作为后续实施 PR 的蓝图锁定。
