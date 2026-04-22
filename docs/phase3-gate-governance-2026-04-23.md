# Phase 3：三可矩阵与动作结论主从决策书

日期：2026-04-23
依据：`docs/discussion-report-2026-04-22.md` §14.5 / §14.4

## 第一部分：三可矩阵

对进入最终 `stock_gate` 的 5 个关键变量逐一核验"可见 / 可追溯 / 可复核"。

| 变量 | 可见 | 可追溯 | 可复核 | 综合判定 |
| --- | --- | --- | --- | --- |
| `composite_priority_score` | ✅ [app.js:1211](assets/js/app.js:1211) 直接展示原始值（如 "54.3"） | ✅ [scoring.py:2579](backend/services/scoring.py:2579)，`compute_composite_priority` + 龟速叠加 + 封顶三层 | ❌ 权重/封顶规则黑盒，用户无从反推 | **可见但不可复核** |
| `priority_pool` | 🟡 [app.js:4663](assets/js/app.js:4663) 仅展示 A/B/C/D 标签，无原始文本 | ✅ [scoring.py:1465](backend/services/scoring.py:1465) `assign_priority_pool` | 🟡 规则公开但需 4 个子分复算 | **标签可见，规则半透明** |
| `qlib_rank` / `qlib_percentile` | 🟡 [app.js:4554](assets/js/app.js:4554) 展示 rank 与 percentile，不展示原始 score | ✅ `qlib_predictions` 表 → [updater.py:1665](backend/routers/updater.py:1665) | ❌ 外部模型黑盒 | **外部模型，内部不可解释** |
| `quality_score`（机构层） | ✅ [app.js:823](assets/js/app.js:823) + [app.js:3218](assets/js/app.js:3218) 展示原始值 | ✅ [scoring.py:1008](backend/services/scoring.py:1008)，百分位+权重+信心降权 | ❌ 需全市场排名 + 平方根信心因子 | **可见但不可复核** |
| `follow_gate`（MCR 层） | ✅ [app.js:3616](assets/js/app.js:3616) 展示"可跟/关注/观察/回避"标签 | ✅ [return_engine.py:178](backend/services/return_engine.py:178) `_suggest_follow_gate` | ✅ 规则纯粹基于 `event_type` + `premium_pct` 分段 | **完全合规** ✓ |

### 矩阵结论

1. **只有 `follow_gate` 一个变量完全满足三可原则**。它基于事件类型和溢价档位的分段规则，用户能从前端明细字段手算结论。
2. **`composite_priority_score` 和 `quality_score` 可见但不可复核**，这是 §14.5 指出的"业务可解释性上等于不存在"的典型样本——数字在 UI 上很显眼，但用户无法验证它在说什么。
3. **Qlib 类变量是外部黑盒**，内部不可解释属于设计取舍，但必须在用户侧标注"AI 预测"避免被误当作可复核事实。
4. `priority_pool` 是"半透明"，规则公开但依赖 4 个子分的组合门槛。

### 对 Phase 2 发现的直接佐证

Phase 2 发现：补跑机构 `quality_score` 后，股票 gate 分布完全未变（follow/watch/avoid 比例一致）。三可矩阵给出结构性解释：

- `quality_score` 在 composite 里只占 `0.15` 系数，且原 NULL→fallback 50 与真实均值（54.11 的 high confidence 组）接近，视觉上看不出差别。
- 换句话说：**机构评分在前端展示，但对最终动作结论几乎没有影响力**。这是"机构是主角"原则的系统性违背。

---

## 第二部分：动作结论主从决策书

### 问题

当前主仓存在**三条并行 gate 链**，且都叫 `stock_gate` 或语义接近：

| 链路 | 产出字段 | 生成位置 | 消费位置 | 语义 |
| --- | --- | --- | --- | --- |
| A. legacy 评分链 | `mart_stock_trend.stock_gate` | [scoring.py](backend/services/scoring.py) 批量写回 | 写入但未被主路由消费 | 基于 composite/pool 推导 |
| B. MCR 聚合链 | `stock_gate`（运行时计算） | [institution.py /api/inst/stock-trends](backend/routers/institution.py) 运行时聚合 | 前端 app.js 主列表直接消费 | 持仓机构 follow_count 聚合 |
| C. signals_v2 链 | 独立字段（不叫 stock_gate） | [signals_v2.py](backend/services/signals_v2.py) | `signal-adapter.js` 独立展示 | EV / 胜率 / 样本 |

三者**不是等价实现**，也**没有主从关系**，导致用户看到的"跟/不跟"无法定位到单一权威源。

### 决策

按 §14.4 "主从 ≠ 合并"原则，不合并三条链，**定义分工**：

#### 1. 机构持仓关系层（必须保留，唯一真相源）

- **主结论字段**：`mart_current_relationship.follow_gate`
- **语义**：单一持仓（机构 × 股票 × 报告期）的跟随建议，规则透明
- **用户展示**：机构详情、股票详情的"持仓表"一列
- **不可变更**：这是当前系统里唯一三可合规的 gate，不动

#### 2. 股票层动作结论（当前主列表 gate）

- **主结论字段**：保留 MCR 聚合链（链 B）作为用户当前看到的 `stock_gate`，**但明确重命名语义**
- **重命名为**：`stock_follow_heat`（股票持仓热度）
  - 原因：它的语义本来就是"有多少被跟档位的机构在持仓"，不是"综合评分后的跟与不跟"
  - 用户侧标签改为："热 / 温 / 冷 / 空"或"N 家可跟 / M 家关注"
- **保留但不主导**：legacy `mart_stock_trend.stock_gate`（链 A）降级为**内部分析字段**，前端不再展示；`scoring.py` 继续写回以便回测对比
- **signals_v2**（链 C）**保留并排展示**，作为"EV 证据链"，与 `stock_follow_heat` 并列而非合并

#### 3. 最终主动作字段（新增，Phase 4+ 实施）

当前五个主要变量都不符合"机构是主角、最终一套动作结论"的完整要求。决策：

- **新增字段**：`mart_stock_trend.primary_action`，取值 `follow / watch / avoid / null`
- **输入维度**（按权重降序）：
  1. 机构 `quality_score` + `followability_score` 聚合（必须是主输入，占 ≥ 40%）
  2. signals_v2 EV/胜率（占 ≥ 25%）
  3. `company_quality_score` 股票财务质量（占 ≤ 20%）
  4. stage/forecast 辅助（占 ≤ 15%）
- **可复核要求**：
  - 前端提供"展开计算明细"面板，展示各维度原始分 + 权重 + 加权结果
  - 保证用户能从展示面板手工加权复算出最终分
- **实施时机**：Phase 4 Qlib 标签对齐后再动，避免频繁变更接口

### 三条链的命运一览

| 链路 | 改名 | 用户侧展示 | 后端保留 | 退役时机 |
| --- | --- | --- | --- | --- |
| A. legacy 评分链 | ❌ 不改名 | 🚫 前端不展示 | ✅ 保留写回（回测对比用） | 不退役，但降级 |
| B. MCR 聚合链 | ✅ → `stock_follow_heat` | ✅ 改标签语义 | ✅ 保留 | 不退役 |
| C. signals_v2 | ❌ 不改名 | ✅ 并排证据链保留 | ✅ 保留 | 不退役 |
| D. **新** primary_action | N/A | ✅ 主动作列 | ✅ 新增 | Phase 4 后 |

### 可视化呈现规则（约束前端改动）

用户在股票主列表应当看到**两列 gate**而非一列：

1. **主动作列**：`primary_action`（Phase 4 后上线，空值时显示"未评估"）
2. **持仓热度列**：`stock_follow_heat`（当前 MCR 聚合 gate 改名）

旁边附 signals_v2 的 EV 信号作为**悬停面板**或**详情抽屉**，而非主列表主列，避免信息密度超载。

### 不做的事情

- ❌ 不合并三条链为一个数字——合并会损失信息、增加 debug 难度（§14.4 原则）
- ❌ 不删除 legacy `stock_gate`——它有回测价值，降级展示即可
- ❌ 不在 Phase 3 阶段修改任何前端代码——决策书先定型，Phase 4+ 再实施

### 决策采纳前置条件

本决策书的实施**依赖** Phase 4 的 Qlib 标签对齐完成，原因：

1. `primary_action` 的机构评分权重需要经过历史回测验证
2. signals_v2 的 EV 取向是否应当对齐"中期持有收益" 需由 Qlib 新标签决定
3. 历史深度不足（阶段特征 9 天、海龟 5 天）意味着权重调优没有足够样本

因此 Phase 3 的本份决策书**是结构设计**，不是立即实施清单。

## Phase 3 交付物清单

- ✅ 三可矩阵表（本文件第一部分）
- ✅ 三条链主从关系决策书（本文件第二部分）
- ✅ 新增 `primary_action` 设计初稿（Phase 4 后实施）
- ✅ 前端双列改造规则（Phase 4 后实施）

## 致 Phase 4 的要求

Phase 4 做 Qlib 标签对齐时必须同时产出：

1. 新标签定义（持有期 / drawdown 约束 / 回归 or 三分类）
2. 基于新标签的 `primary_action` 各输入维度的**建议权重**（通过历史回测标定）
3. 如果历史深度仍不足，给出"冷启动期权重 → 成熟期权重"的过渡方案
