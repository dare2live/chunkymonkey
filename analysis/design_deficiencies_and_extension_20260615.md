## 之前设计的缺陷 + 扩展 (架构师反思, 2026-06-15)

> 状态: live。owner=本文件。缘起: 用户"顺着我的思路扩展深挖, 看之前设计有哪些不足"。
> 上承 segment_taxonomy_design / conditional_stage_strategy_design / multidim_strategy_architecture / MASTER。
> 用 Phase B 真金白银实证 (IC 真但不可交易 / IC 选格误导) 反推设计根本缺陷, 修正而非推翻用户核心思路。

### 0. 你的思路哪里对 (不能丢的婴儿)
**第一性洞察对**: 不同股票/状态行为不同 → 不该一套参数打全市场, 该条件化分层。Phase B 也证实形态/换手确有结构
(突破中 reversal 远超全市场; 换手为主轴)。**错的不是"条件化", 是操作化时三个隐含假设。**

### 1. 根本缺陷: 优化了"相对排序"却交易"绝对收益" (最致命)
- 整个设计建在 **RankIC** 上: L0 标尺 = IC +0.064; 立方体按 IC 选 cell; 逐层解锁 by "IC lift"。
- 但 **long-only 赚的是 cohort 绝对收益, IC 测的是 cohort 内相对排序** —— 两者可剧烈背离。
- **铁证 (Phase B)**: IC 最高子格 (小盘×高换手 +0.195) backtest **gross -34.6%**; 全市场 Stage1.5 (IC 仅 +0.156)
  反而 gross +7.1%。IC 对 cohort 绝对漂移**数学上不变** (每日截面 spearman, 减掉了水平)。
- **后果**: 验证阶梯 Gate0(PIT)/Gate1(OOS IC)/Gate2(MC置换) 全过, cohort 仍可整体崩 —— 阶梯有**绝对收益盲点**,
  直到 Tier-2 才暴露。设计把"钱"放在了最后一关。

### 2. 缺失维度: 没有"绝对方向/择时/regime"轴
- 立方体 = Segment × Feature × Policy, **三轴全是 cross-sectional ("选哪只")**。
- **缺"该不该在场"轴** (regime / cohort-trend / 择时) = 绝对方向。A 股 long-only, 当 cohort 整体下行 (2024 微盘崩),
  再好的选股也救不了 (长"最不烂的下跌刀")。
- 设计里 regime 只作为 Segment 的一个子轴 (regime gate 调制), 没当**一等决策维** (long / flat / defensive)。

### 3. 缺陷: 信号衰减/换手/成本不是一等约束
- 设计按 edge (IC) 大小排序候选, 把成本/换手当事后扣减。但 Phase B: reversal ~5 天**快衰减** → 结构性高换手
  (turnover 1.93/周) → 成本拖累 31% → 杀死 +7.1% gross。
- **缺"可交易性"前置筛**: 信号该先按 (衰减 horizon → 换手预算 → 成本可活性 → 容量) 分类; 快衰减信号 long-only
  结构性不可交易, 无论 IC 多高。设计从未把"信号半衰期"当一等变量。

### 4. 缺陷: 验证阶梯把"绝对收益"放到最后 (Tier-2)
- Gate0-2 全是 IC/统计显著性, 廉价但只验"相对排序真不真"。到 Gate5/Tier-2 才验"含成本绝对收益"。
- **应在早期插一个廉价的绝对收益门**: long-only top-K basket 含成本年化是否 > 0 (甚至 > 无风险/基准), 在投入
  IC 精调/Optuna/Modal 前。否则像本轮: 大量功夫验出 +0.156/+0.195 IC, 一回测全负。

### 5. 缺陷: long-only 约束没被正面设计
- 横截面 rank edge 天然是 **long-short / 市场中性** 的 (做多 top、做空 bottom 赚 rank spread)。A 股**只能 long** (个股
  难融券)。设计默认 long-only 却用 IC (long-short 指标) 选 —— 错配。
- **正解二选一**: (a) 找能让 **cohort 整体上涨** 的信号 (这样 long-only 才赚绝对) — 即趋势/景气/资金面; 或
  (b) 指数对冲 (股指期货空) 把 long-only 变准市场中性, 捕 rank spread。设计没 grapple 这个约束。

### 6. 缺陷: "裸 K 线 base-edge 优先"前提过乐观
- Phase A→B 假设先证一个裸 K 线 base alpha (L0) 再分层加因子。但裸 K 线只给**短衰减的相对排序**信号
  (reversal/动量), 本质不可 long-only 交易。**base 不该是裸 K 线** —— 该一开始就找**绝对预测性 + 慢衰减**的源
  (财务质量驱动绝对收益、资金流 trend 驱动 cohort 方向、景气驱动行业 beta)。
- L0 标尺 (+0.064 IC) 作为"每个 alpha 要超越的地板"本身就是个 cross-sectional 地板, 不是"赚钱地板"。

### 7. 次级缺陷
- **horizon 固定 5 天**: 没把信号半衰期 × 持有期 × 换手 × 成本 联立优化 (不同信号最优持有期不同)。
- **selection 用 IC 排序选 top-K**: 应按"含成本边际贡献"选, 且考虑 capacity (小盘 top-K 容量小、冲击成本高)。
- **没有 cohort/universe 健康度监控**: cohort 整体崩时应自动降仓 (regime gate), 设计无此自动机制。

### 扩展设计 (修正后, 仍在你的条件化框架内)
1. **立方体加第四轴 = Regime/Timing (绝对方向门)**: 决定 long / flat / defensive; cohort-trend + 市场 regime 一等决策维,
   非 Segment 子轴。long-only 的钱主要来自"在对的时候在场", 选股是次要 alpha。
2. **验证范式反转: IC 降级为快筛 (necessary), 含成本 backtest 绝对收益升为 gate (sufficient)**。早期插廉价绝对收益门;
   选 cell/因子/分层一律按含成本 OOS backtest 绝对收益 (非 IC)。固化进 experiment_harness (已记 skill §2)。
3. **信号按可交易性分类先筛**: (半衰期 → 换手预算 → 成本可活性 → 容量); 快衰减 long-only 信号低优先。
4. **数据优先级反转 (Phase D)**: 慢衰减 + 绝对预测 (财务质量/资金流 trend/景气/筹码结构) > 快衰减相对 (裸 K 线 reversal)。
   绝对方向信号才是 A 股 long-only 的真 alpha 源。
5. **long-only 现实**: 优先找驱动 cohort 整体上涨的信号 (行业景气轮动 / 资金面 trend); 对冲 (股指空) 作为捕 rank-spread 的备选。
6. **每个数据/因子的验收**: 不止 IC, 必含 (含成本 long-only 绝对收益 + cohort 健康度 + 容量 + 半衰期)。
