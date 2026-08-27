# Strategy Research And Release Contract

> 状态：live
> 生效：2026-07-16
> 作用：Tier 3 研究、策略验证、Tier 4 发布与纸面执行的唯一规则。当前不存在已发布生产策略，所有历史 KPI 均不得当作当前证书。

## 1. 风险优先

| 风险 | 阻断规则 |
|---|---|
| PIT / availability 泄漏 | 决策时点只能读取当时已经可获得的数据；仅有 `trade_date` 不足以证明可用 |
| 标签进入特征 | 未来涨幅、episode 结局、持有期收益只可作为 label/evaluation，不可进入特征或状态定义 |
| 当前复权视图充当成交价 | qfq 用于分析收益；订单和成交必须使用名义可成交价格与当日交易限制 |
| 一次试验改多个变量 | 每次消融只增加一个命名 feature block，其他数据/样本/成本/执行不变 |
| 只报胜率或 IC | 必须同时报告收益、盈亏比、回撤、换手、容量和稳定性 |
| 研究结果直接出候选 | 没有 `StrategyRelease` 的实验不得进入正式 `DecisionBatch` |
| 失败实验被丢弃 | 失败、无增益和负增益都必须登记，防止重复搜索和选择性报告 |
| 可疑高指标 | Sharpe、胜率、年化或相对提升异常时先做泄漏/样本/PIT/执行消融，不得庆祝式转正 |

## 2. 标准对象

| 对象 | 最小职责 |
|---|---|
| `DatasetSnapshot` | 冻结输入数据集、accepted partitions、universe、时间范围和内容 hash |
| `FeatureBlock` | 版本化输入列、计算定义、availability 和 config hash |
| `ExperimentRun` | prereg、假设、数据快照、fold、成本、执行、随机种子和 artifact manifest |
| `ExperimentVerdict` | 结果、反证、分层稳定性、PIT/成本 gate 和 `accept/reject/inconclusive` |
| `StrategySpec` | 候选生成、排序、仓位、退出和适用状态的版本化定义 |
| `StrategyRelease` | 指向一个获准 verdict 和完整构建物的不可变发布记录 |
| `DecisionBatch` | 某 decision time 使用某 release 和 snapshot 生成的一次决策 |
| `CandidateSignal` | 股票、方向、理由、风险、证据引用和有效期；不是成交 |
| `PaperOrder/Fill/Nav` | 按真实交易规则模拟的订单、成交和组合净值 |

任何对象缺少上游引用、时间语义或版本，不得用默认值伪装完整。

## 3. 研究基线与消融

所有策略包共用同一阶梯：

| Block | 内容 | 要回答的问题 |
|---|---|---|
| B0 | 裸 K 线基线 | 仅价格/成交量是否已有可交易 edge？ |
| B1 | + 股票阶段与形态 | 状态条件化是否提升收益/降低回撤？ |
| B2 | + 市场感知 | 市场活动、广度、价格响应是否改善时机？ |
| B3 | + 资金活动证据 | 某一明确 vendor/method 的不平衡代理是否有增益？ |
| B4 | + 机构或事件 | 披露可用时点后的机构/事件证据是否增益？ |
| B5 | + 单一公式/组合 | 公式在何种状态与市场上下文中有稳定增量？ |

同一轮 B0-B5 必须固定：

- `DatasetSnapshot` 与 eligible universe；
- label/episode 定义；
- train/validation/holdout 切分；
- 成本、T+1、停牌、涨跌停、容量和仓位规则；
- 决策时点与 availability policy；
- 指标计算和统计检验。

如果一个 block 只在少数行业、年份或状态有效，结论应是“条件性 feature”，不是全局 alpha。

### 3.1 跨族窗口对齐判据

构建冻结快照的公共时间窗时，**既不默认拉齐、也不默认缩短**。先判断缺口属于哪一类：

1. **该族在 D0 之前从未被供应，或轴本身在 D0 才存在** → 公共窗起点取 `max(requested, D0)`，
   或把该族在 D0 之前标为不适用；若某 block 硬依赖该族且无可替代 → verdict `inconclusive`，
   **禁止 pad 0**。
2. **同轴同频的局部洞或截断**（bug / 分页上限 / drain 滞后 / accept 拒绝 / ops 未跑有界补洞）
   → 允许**有界拉齐**修复；修完该族才可进 intersect。
3. **不同频率或不同语义轴**（报告期 vs 公告日、季频 vs 日频）→ **禁止「拉齐成日频全历史」**；
   intersect 必须在决策时点语义上对齐，缺族用 NULL/unknown + 计数，verdict 可 `inconclusive`，
   **不得伪造日频**。
4. **历史深度从未按政策采集**（中间期只记录不回填、legacy 孤儿）→ 这不是错轴，走 DEFER 或
   族级覆盖声明；研究若依赖全历史则 `inconclusive`，或收窄到有 accepted 证据的区间；
   **禁止 mass invent 拉齐**。
5. **各 block 起始年份错配** → 先判断是否属于情形 2 可修复，否则归入 1/3/4。
   **禁止为了凑 lift 而静默缩短窗口** —— 公共窗与族排除理由必须写进预注册。

一句话判据：**缺口的成因决定处置方式**。把「供应商从来没有」和「我们漏采了」当成同一件事，
就会用拉齐掩盖真实的流程债，或者用缩窗换一个更好看的结果。

### 3.2 研究开门条件（BLOCKED → 可执行）

策略轨默认 **BLOCKED**。同时满足以下全部才可开，缺一即关：

1. 地基出口已 MET（后续若回退，本门**自动重关**，不需要人再判一次）；
2. `goal.md` 里 owner 的**显式一句**排期，并写入与 typed config 一致的授权 id；
3. **同 protocol**：development snapshot 只含 train/validation；sealed holdout 独立冻结且
   worker 不可见；同一 universe / folds / 成本 / 执行约束；
4. 因子族登记与其 continuity/live 门全 PASS；
5. §5 禁令未破。

开门那一刀必须同时具备：fresh development snapshot（accepted generation 严格截止在 holdout
之前）+ sealed holdout 的不透明引用 + canonical pointer/content、逐日 universe membership、
availability 与各层 generation/hash 证据 + verdict artifact 路径与**诚实标签**（reject 也算交付）。

### 3.3 研究计算准入：唯一数据入口与四级阶梯

```text
accepted canonical → 冻结 DatasetSnapshot（带 accepted/hash 证据）
  → 只读 ResearchInputBundle → prereg / folds / 成本 / 执行
  → 纯本地 trial evaluator → 不可变的逐 trial artifact
  → 单一 owner reducer → ExperimentVerdict
```

`ResearchInputBundle` **只接受 development snapshot**，构造时即拒绝任何 sealed-holdout 分区
或 Tier3 label 输入 —— 拒绝发生在构造时，不是使用时，因为「构造了但没用」无法证明。

算力四级阶梯，**逐级解锁不得跳级**：

1. **现在**：本地、手动、只读 smoke，`claimable=false`；
2. **RX**：须 `goal.md` 排期 + fresh freeze + sealed holdout ref + 全部证据 + purged walk-forward；
3. **Optuna**：在 RX 之外**另开**一个 phase；search space 非空且每项必须真的改变 behavior hash
   （否则就是白跑）；objective 只读 development validation；
4. **远程算力**：最后一步，前提是同 bundle/spec 的本地与远端结果 **hash 0 diff**、只读 bundle、
   不碰项目 DB。

跳级的代价都一样：拿一个没有证据链的结果去做真金白银的决定。

## 4. 标签、状态和特征边界

- `StockStateDaily` 与 `PatternEvent` 只使用截至 decision time 可见的数据；
- 主升浪 `底→顶`、未来涨幅、未来最大回撤等是研究标签；
- institution episode 的 report date、notice date、effective date 和 available time 必须分开；
- 市场感知必须从 `MarketContextSnapshot(decision_time)` 读取，禁止按 `trade_date=t` 直连展示 mart；
- 用未来收益优化状态阈值时，优化后的定义属于策略 feature，不得回写 Tier 1 canonical 状态；
- 任何 latest snapshot、`MAX(date)` fallback、缺失填 0、demo/mock fallback 都不能作为历史证据。

PIT 截断测试是硬门：在 cutoff 后增加未来数据，cutoff 前的特征、候选和状态必须逐字段 0 diff。

## 5. 时间切分与搜索纪律

1. prereg 先于结果：冻结假设、搜索空间、主指标、停止条件和 holdout；
2. development snapshot 只含 train + purged walk-forward validation；train 只用于拟合，validation 用于模型/参数选择；
3. sealed holdout 是独立不可变 snapshot：开发 worker、Optuna objective 和远程 worker 均不可读取，只向 prereg 暴露 opaque hash；候选与参数完全冻结后，由唯一 evaluator / CAS owner 最多触碰一次；
4. 开发窗口尾部的 fold 指标只能命名为 validation/eval，不得消耗全局 holdout token，也不得作为多次读取 sealed holdout 的别名；
5. 使用 purged walk-forward，并按 label horizon 设置 embargo；
6. trial 数、公式数、状态 cell、特征组合全部计入多重搜索规模；
7. 搜索空间必须改变真实策略行为；无 search space 的公式不运行寻优；
8. 先跑最便宜的 B0/B1 和小样本烟测，再决定是否扩大本地计算；
9. 当前仅允许本地、人工触发、`claimable=false` 的策略实验室 smoke。退役 provider、已移除的 job dispatcher 和不存在的执行器不是可用入口；Optuna、远程计算和正式 RX 分别通过独立准入门。

复杂搜索应报告 DSR/PBO 或等价的多重比较与过拟合证据；这些统计量不能替代含成本组合结果。

## 6. 执行模型

纸面执行至少模拟：

- signal 后下一可交易时点入场，默认不能用同日收盘价假成交；
- T+1、停牌、涨停买不到、跌停卖不出和一字板；
- 佣金、印花税、滑点与明确的容量/冲击假设；
- top-K、持仓上限、同票去重、行业/概念集中度和换手；
- 名义价格订单与成交；qfq 仅用于收益分析或可比序列；
- 未成交、部分成交、取消和数据 unknown 的显式状态。

`CandidateSignal` 不是仓位，`StrategySpec` 不是 `StrategyRelease`，研究收益也不是组合可实现收益。

## 7. 指标与裁决

每个 experiment 至少报告：

- 总收益、年化收益、超额收益；
- 最大回撤、波动、Sharpe/Calmar；
- 单笔/按月胜率、平均盈利、平均亏损、盈亏比和期望；
- 换手、持有期、交易数、容量与集中度；
- 按年份、市场状态、股票阶段、行业、规模和流动性的稳定性；
- 与 B0、上一 block、基准指数和现有 challenger 的增量差；
- 数据覆盖、unknown、未成交和 gate failure 数量。

胜率是诊断量，收益与回撤是目标量；任何单指标都不能独立转正。未测量值写 `unknown/NULL`，不填 0。

`ExperimentVerdict` 只有三种：

- `accept`：PIT、执行、稳定性和增量均满足 prereg；
- `reject`：无增益、负增益、过拟合或不可交易；
- `inconclusive`：数据/覆盖/统计功效不足，不能按 accept 传播。

## 8. 策略包边界

接入顺序（已裁决）：**机构跟随 → 主升浪 → 公式**。消融阶梯 B0–B5 不变；共享 snapshot/universe/成本/执行。Tier0 硬门与研究运行时未闭合前，任何策略包不得宣称正式有效。

### 8.1 机构跟随（第一条正式闭环）

机构画像、episode 和历史表现是研究输入，不是“机构身份即买入”的证书。第一条正式闭环为 `institution_follow_v1`，且硬依赖 goal **E0**（披露域已进 landing→accept→canonical；miaoxiang/aif10 直写路径不得充当冻结 snapshot）。

三层不得混称（混称会把展示战绩、隔夜动量消融和跟随纸面当成同一策略）：

1. **机构画像**：`institution_profile` / episode / `median_alpha`（机构自身 VWAP 持有期口径）。这是研究输入与展示，不是买入证书，也不是跟随者可实现 PnL。`recent_signals` 属于这一层。
2. **跟随 `StrategySpec`（`institution_follow_v1`）**：在真实 `notice_date` / `available_at` 之后的下一可交易 open 入场；在减持披露后的下一可交易 open，或 `max_hold_calendar_days`（当前烟测 90；180 只命名不跑）退出。跟随者纸面 PnL 必须独立计入披露延迟、追价、未成交与退出约束，**不得**复用 `alpha_c1` / `holder_median_alpha` / 机构 VWAP。
3. **E 阶梯 B0/B4 隔夜动量消融**：同一披露快照上的裸 K / 事件门控 overnight paper，是诊断消融，不是跟随 spec。不得把 B4「事件日」当成季报日，也不得把 OHLCV 全窗交易日数当成披露覆盖分母；覆盖分母是披露冻结分区日。

闭环步骤（与上面三层正交；完成纸面后才谈 Release，当前无 Release）：

1. 冻结披露类 `DatasetSnapshot`（含 notice/available 语义与沪深 PIT universe）；
2. 跑 B0 裸 K 基线；
3. 加 B1 股票状态、B2 市场感知（完整跟随策略所需；禁止跳过 Tier0/1/2 直接宣称 B4 有效）；
4. 在 B4 独立消融机构/事件块；需要时再单独加 B3 资金活动证据；
5. 区分机构自身历史表现与跟随者可实现收益；
6. 通过纸面执行后才产生 `StrategyRelease(institution_follow_v1)`。

当前跟随 spec 纸面已从冻结 snapshot 的 `stk_holdertrade` 公告行（`in_de`→increase/decrease，`ann_date`=`available_at`）接到下一 open 持有纸面；E/F `ExperimentVerdict` JSON 仍只是消融，不是该 spec。

硬约束：

- 一个持仓/调研事实最早只能在真实 `notice_date/available_at` 之后使用；**`notice_date` 为 NULL 的行契约级排除**（不得仅在某一查询面过滤后从别的读面漏进）；默认信号成交锚为披露后下一交易日 open，禁止回填 report/effective date。机构持仓明细供应商没有 NOTICE_DATE：PIT 轴 = 同股同期定期报告首次 `f_ann_date`（不足则十大股东 `notice_date`）；**禁止**用法定披露截止日或报告期末冒充已知日。历史 `canonical.available_date` 若仍是截止日，回测 asof 必须走公告 JOIN（`org_holding_announcement`），不得信那一列；
- 下一交易日若停牌、涨停买不到或数据 unknown，只能顺延到下一个真实可交易 open，并受版本化 `max_chase_days` 限制；过期记为未成交，不用未来价格选择“最佳”入场；
- 机构评级、白名单和历史胜率只能用 decision time 之前已披露 episode 做 expanding-window 计算；禁止用全期结果筛选机构后回测早期信号；
- 跟随者收益必须独立计入披露延迟、追价、未成交、容量和退出约束，不能复用机构自身持有期收益；覆盖与功效分母用披露冻结 `available`/`partition` 日，不用名义 K 的全历史交易日数；
- **t 日 project universe 的可证明语义默认是 EOD**：须在 t 日名义 K/ST accepted 且 `usable_at ≤ decision_time` 之后才能证明成员；`DecisionBatch.decision_time` 不得早于该证明点却声称已解析当日池（盘中决策需另立契约，禁止 silently 复用 EOD 成员）。

大单/超大单、龙虎榜席位或 vendor “主力”字段不得自动映射成机构身份。

### 8.2 主升浪猎手

现有 rally ground truth、negative、strata、embargo 和 continuity 资产可以保留，在机构首包之后接入同一研究运行时。当前可加载的 setup 信号是 `rally_setup_pivot_confirmed_base_days`；另有 setup 纸面：`bottom_date`（仅 entry_anchor）之后下一可交易 open 入场、命名短窗出场。**这不是 `StrategyRelease(main_rally_v1)`，也不是 full-episode 猎手**；禁止读 `peak_date` / `gain_to_peak_*`。full-episode 猎手尚未实现，不得把 setup 烟测写成主升浪策略。

1. 冻结并复核 ground truth 定义和数据快照；
2. 跑 B0 裸 K；
3. 加 B1 股票状态；
4. 加 B2 市场感知；
5. 只有增量成立才引入 B3/B4/B5；
6. 通过纸面执行后才产生 `StrategyRelease(main_rally_v1)`。

标签表不能被候选生成器直接读取。

### 8.3 选股公式 / BestChoice

每个公式是一个版本化 `FeatureBlock` 或 `StrategySpec`，不是独立数据平台。公式必须在本项目快照、PIT、成本和执行模型下重放。

BestChoice 保持冻结 challenger：

- 先冻结 artifact hash、数据截止日和 lineage；
- 首次接入只读 namespaced evidence，不覆盖主项目表；
- 先验证 daily trigger、纸面组合和互补性；
- 只有正式 `ExperimentVerdict` 支持时才吸收公式代码；
- 单股历史最优参数或旧 Optuna 报告不等于组合级可交易 edge。

当前第一刀（goal **S3**）只做合成 daily trigger + 信号日后下一可交易 open 烟测，`claimable=false`。纸面是一名一仓顺序开平，卖出不得早于买入次日（T+1）。旧 Optuna adoption CSV 与 `execution_model.vwap_tradable_v1` **不是**本项目纸面。单名重放必须是 typed offline fixture 或 live pointer 预检后的 **单 `ts_code` canonical 子集**；bar 日必须落在 snapshot 名义日集合并早于 holdout。live pointer 不是全日 `content_hash` bind，也不是全宇宙重放；holdout 日在 pointer 查询之前拒绝，并有单名测试。全宇宙 B5 / purged WF / holdout consume / `ExperimentVerdict` accept / 吸收公式代码均未实现。

## 9. 发布门

创建 `StrategyRelease` 前必须同时具备：

1. 完整 `DatasetSnapshot` 与 config/code hash；
2. PIT/availability 截断测试；
3. walk-forward + embargo + single-touch holdout；
4. 含成本、T+1、停牌/涨跌停的 paper execution；
5. B0→目标 block 的逐层消融；
6. 泄漏和异常高指标反证；
7. 可复现 artifact manifest；
8. 明确 `accept` verdict 和适用/失效条件；
9. forward/paper 监控与自动冻结规则。

任一项缺失，产品最多展示为 `research`，不得进入正式候选。

## 10. 当前裁决

- 当前没有可视为生产证书的策略 KPI；
- 现有 `holdout_guard.py` 执行 training end `< holdout_start`（含 **actual** max）+ `research_prereg_store` 原子 prereg / `param_hash` / stable `holdout_scope_id` single-touch（锚定 snapshot + strategy + universe + protocol + governed holdout policy；新 UUID、block、fold/holdout date 均不得重置；当前文件 ledger 仅为单节点 fail-closed evidence）。正式 B0 必须按 `pointer metadata preflight → consume → canonical outcome load/hash` 顺序执行；typed fixture 只能形成 non-claimable 诊断结果。这仍 **不是** 完整 StrategyRelease；跨节点正式 owner、合格 freeze 与 §9 其余发布门缺失时产品最多 `research`；

- rally ground truth、technical state、market pulse、institution profile 是可复用资产，不是发布策略；
- 现有 market pulse 缺 availability/method/config hash，暂只适合当前展示；
- 现有 paper portfolio 缺正式 release、订单/成交约束，不能作为执行证据；
- Tier 0 与分类契约闭合前，不启动机构全历史寻优、大规模公式搜索或付费计算；
- 每一切片边做边测：坏例先红、窄回归、PIT/截断对抗例可变红，才允许扩大范围。
