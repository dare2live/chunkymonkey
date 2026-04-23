# 讨论文档：项目实现现状、阻塞点、分支进展与后续路线

日期：2026-04-22

## 0. 这份文档的作用

这份文档用于替代此前已经散落在 `docs` 目录中的多份报告，并回答同一个核心问题：当前项目是否已经能够真正实现“机构先评估、股票再评估、最后给出跟与不跟结论，并朝收益最大化、回撤最小化、胜率最高化优化”的业务目标。

这份文档同时吸收四类材料：

1. 当前主仓代码的可验证事实。
2. 当前共享数据库 `data/smartmoney.db` 的可验证事实。
3. 已进入 Git 历史、但目前已从 `docs` 删除的归档报告。
4. 本轮会话中已经完成、但此前没有进入 Git 历史的前序研究结论重建稿。

为了避免把分支实验状态误写成主仓现实，全文统一使用以下状态标签：

| 标签 | 含义 |
| --- | --- |
| `Mainline current state` | 当前主仓代码直接可验证的事实 |
| `Shared DB current state` | 当前 `data/smartmoney.db` 直接可验证的事实 |
| `Archived report` | 已进入 Git 历史、但不是当前工作树文件的归档报告内容 |
| `Worktree / testing-only` | 已删除 worktree 或阶段性实验的历史结果，只能作为归档证据，不能直接当作主仓已落地能力 |
| `Session reconstruction` | 本轮会话中已完成但未入 Git 的研究结论重建 |

## 1. 结论先行

当前判断如下：

1. 项目已经具备“围绕机构持仓披露行为做研究”的主体骨架，也已经有股票侧列表、详情、评分、信号、Qlib、行业与外部关注等多条能力链。
2. 但项目还不能宣称已经完成了“机构是主角、股票是载体、最终给出统一跟与不跟结论并稳定优化收益/回撤/胜率”的闭环。
3. 限制当前业务目标落地的主要问题不是某一个前端页面或某一个字段，而是结构性问题：归档报告与当前主仓现实错位；机构评分链未闭环；最终动作结论存在多条并行口径；Qlib 当前标签与中期业务目标不一致；新因子的历史深度仍然太短。
4. `Phase A-G` 报告和相关 worktree 实验是有价值的，但它们更像是“候选未来方案的证据”，不是当前主仓已经拥有的现实能力。
5. 数据源与因子扩展方向是清晰的，且应当继续推进，但必须排在主业务闭环修复之后。否则会继续出现“因子更多了、IC 更好看了、但最终用户侧动作结论没有真正变得更可信”的问题。

## 2. 项目目标与判定标准

本项目的业务北极星不是“展示很多数据”，也不是“把所有因子都堆进模型”，而是：

1. 先把机构行为研究透。
2. 再把股票股性、阶段、行业背景、供给约束、预期差等因素放到统一框架里解释。
3. 最终给出一个可执行的跟、观察、回避结论。
4. 用收益、回撤、胜率、覆盖率来检验这个结论链是否真的有用。

因此，真正合格的系统必须同时满足五件事：

1. 机构层评分或决策必须真实参与到股票层判断，而不是只展示不控制。
2. 股票层必须只有一个明确的主动作结论系统，而不是多条平行逻辑并存。
3. Qlib 或其他模型链必须对齐业务目标，而不是只优化一个与业务动作无关的短期标签。
4. 所有进入最终动作结论的关键变量，至少要满足“可见、可追溯、可复核”。
5. 数据与标签的历史深度必须足够，否则任何统计结论都只能算探索性发现。

## 3. 当前主仓到底已经实现了什么

### 3.1 后端主骨架已经成型

`Mainline current state`

当前后端入口在 `backend/main.py`，已经注册了以下主路由：

1. `/api/inst` 机构与股票研究主链。
2. `/api/qlib` 股票侧 Qlib 训练与查询链。
3. `/api/etf` ETF 独立链。
4. `/api/screening` 筛选与行业概览链。
5. `/api/signals` `signals_v2` 并行信号链。

这说明系统在结构上并不是一个“只有单页展示”的原型，而是已经具备了较完整的后端研究框架。

### 3.2 更新 DAG 已经覆盖原始数据、事实层、画像层、评分层

`Mainline current state`

`backend/routers/updater.py` 当前实际 `STEPS` 列表包含 24 个步骤，已经覆盖：

1. 十大股东下载、机构匹配、行情、财务、调研、QFII、融资融券、龙虎榜等上游同步。
2. 事件生成、收益计算、财务派生。
3. 当前关系、机构画像、行业统计、股票列表、筛选、板块动量、外部关注、阶段特征、预测特征、海龟特征。
4. 机构评分和股票评分。

这意味着“从原始持仓到股票侧结论”这条链在工程结构上已经是完整的，而不是零散脚本。

### 3.3 股票研究页与信号页都已经成型，但它们不是同一套决策系统

`Mainline current state`

1. 股票研究页当前主用户路径仍由 `backend/routers/institution.py` 的 `/api/inst/stock-trends` 直接组装返回，尚未完全收口到 `stock_trends_read.py` 这一层共享读模型。
2. 前端 `assets/js/app.js` 直接消费 `stock_gate`、`stock_gate_reason` 等字段做主展示。
3. `assets/js/signal-adapter.js` 作为单独的数据适配层，专门服务 `/api/signals/*` 这条 `signals_v2` 并行证据链。

也就是说，主界面已经不是“没有结论”，而是同时存在 legacy 评分链、当前关系聚合链与 `signals_v2` 并排证据链。这正是当前系统的能力，也是当前系统的问题来源。

## 4. 当前主仓的真实决策链是什么

这一节只讨论当前主仓代码，不讨论 worktree 方案。

### 4.1 当前股票研究主列表的 gate 以 MCR 持仓关系聚合为主

`Mainline current state`

当前活跃的 `/api/inst/stock-trends` 路由在 `backend/routers/institution.py` 中直接查询 `mart_stock_trend` 与 `mart_current_relationship`，随后根据持仓机构 `follow_gate` 计数重新聚合股票级 `stock_gate`：

1. 只要 `holder_follow_count > 0`，股票就记为 `follow`。
2. 否则按 `watch -> observe -> avoid -> null` 顺序降级。
3. `stock_gate_reason` 直接由各档持仓机构数量生成。

这意味着当前用户在股票研究主列表看到的 `stock_gate`，主口径并不是直接读取 `mart_stock_trend.stock_gate`，也不是来自 `signals_v2`，而是当前关系层的聚合结果。

### 4.2 评分链推导的 gate 仍然存在于写层与共享 helper 中

`Mainline current state`

`scoring.py` 仍会基于以下三项推导一套 legacy `stock_gate` 并写回 `mart_stock_trend`：

1. `priority_pool`
2. `composite_priority_score`
3. `priority_pool_reason`

同时，`backend/services/stock_trends_read.py` 里也保留了基于同一套 `priority_pool/composite` 规则推导 `stock_gate` 的共享 helper。这说明系统当前不是“只有一条 gate”，而是：写层有一套基于综合分的 gate 语义，活跃列表路由又用 MCR 关系层重新聚合了一套用户可见 gate 语义。两者不是等价实现，而是不同业务含义。

### 4.3 `signals_v2` 是并排决策链，不是当前主仓唯一动作结论系统

`Mainline current state`

`backend/services/signals_v2.py` 的模块注释写得非常明确：

1. 它只围绕历史类似事件的 EV、胜率、样本数做判断。
2. 它明确不合成 `composite_priority`、`pool`、`setup_priority`、`gate` 等 legacy 综合评分概念。
3. 所有配置统一放在 `signals.v2.*` 命名空间，与 `scoring.py` 物理隔离。

前端 `assets/js/signal-adapter.js` 也验证了这一点：它单独请求 `/api/signals/today`、`/api/signals/cohort/recent`、`/api/signals/institution/*` 等接口，形成一个独立的标准化展示对象体系。这条链是“并排展示的第二证据链”，不是主仓股票研究页唯一的 gate 来源。

### 4.4 Qlib 当前仍然是股票侧评分链的一个输入维度，而不是主动作结论系统

`Mainline current state`

`backend/services/qlib_full_engine.py` 当前 `_QLIB_LABEL_CONFIG` 为：

1. `Ref($close, -2)/Ref($close, -1) - 1`
2. 标签名 `LABEL0`

这代表当前股票侧 Qlib 主标签仍是一个偏 next-day、偏截面排序的短周期标签。`scoring.py` 会读取 `qlib_rank`、`qlib_score`、`qlib_percentile` 作为股票评分的一个维度，但 Qlib 并不直接决定最终用户侧动作结论。

## 5. 当前共享数据库告诉我们的事实

`Shared DB current state`

2026-04-22 当前 `data/smartmoney.db` 的关键数字如下：

| 表 / 指标 | 当前状态 |
| --- | --- |
| `fact_stock_quality_features` | 30072 行，5 个快照日，覆盖 6018 只股票，日期区间 `2026-04-08` 至 `2026-04-17` |
| `fact_stock_stage_features` | 29458 行，9 个快照日，覆盖 3363 只股票，日期区间 `2026-04-08` 至 `2026-04-22` |
| `fact_stock_turtle_features` | 16357 行，5 个快照日，覆盖 3345 只股票，日期区间 `2026-04-13` 至 `2026-04-21` |
| `mart_institution_profile` | 231 行，`quality_score` 非空 231，`followability_score` 非空 231；但这些字段仍未成为当前用户主列表主结论链的直接驱动项 |
| `mart_stock_trend` | 3285 行，`stock_gate` 非空 3285，`composite_priority_score` 非空 3285 |
| `qlib_predictions` | 最新 `predict_date` 为 `2026-04-13`，存在多个模型分片，行数为 898、435 等，属于过期且非全市场覆盖 |
| `fact_northbound_daily` | 当前不存在 |

从这些数字可以直接看出三件事：

1. 股票侧阶段、质量、海龟等特征已经开始积累，但历史还很短。
2. 机构画像表里的核心评分字段当前已经补齐，但“字段非空”不等于“已经进入生产主动作链”。
3. 股票侧综合分和 gate 已经大量存在，因此用户侧看起来“系统已经有判断了”，但这个判断与机构评分闭环并不一致。

## 6. 当前主仓最核心的五个阻塞点

### 6.1 阻塞点一：归档报告与当前主仓现实错位

`Mainline current state` + `Shared DB current state` + `Archived report`

这是目前最根本的问题。

当前主仓代码与共享数据库在北向这件事上其实已经是一致的：

1. 当前主代码目录已经没有 `northbound`、`sync_northbound`、`use_northbound` 这类实现残留。
2. `data/smartmoney.db` 里也不存在 `fact_northbound_daily`。
3. 原来的 worktree 已经删除，`Phase A-G` 只能作为 Git 历史中的归档材料来理解。

真正错位的是历史材料：归档的 `Phase A-G` 报告仍然带着当时的分支上下文、北向退役叙述和替代源实验结论，而当前主仓已经在你写文档期间被同步清理过一轮。

这不再是“主仓代码还在引用不存在的 northbound 表”这种代码级 bug，而是“归档报告和当前现实没有同步收口”的文档级错位。

因此现在任何基于 `Phase A-G` 的正面能力叙述，都必须先加一句前提：那是归档实验材料，不是当前主仓现实。

### 6.2 阻塞点二：机构评分虽已补齐，但仍未闭环到主动作链

`Mainline current state` + `Shared DB current state`

`scoring.py` 中 `calculate_institution_scores()` 的职责非常明确：

1. 从 `mart_institution_profile` 取机构画像特征。
2. 做百分位归一化。
3. 写回 `quality_score` 和相关评分。

同时，`calculate_stock_scores()` 又会预加载 `mart_institution_profile` 中的 `quality_score`、`followability_score` 等信息，用于后续股票侧 leader/consensus/quality 的判断。

但是当前共享数据库的事实是：

1. `mart_institution_profile` 有 231 行。
2. `quality_score` 非空数为 231。
3. `followability_score` 非空数为 231。

这说明机构评分函数和写回本身当前已经跑通，问题不再是“字段全空”，而是“字段虽已存在，但没有真实进入当前用户主列表主结论链”。股票评分可以继续跑，股票 gate 可以继续展示，但这仍不是一个真正以机构层闭环为主的系统。

### 6.3 阻塞点三：最终动作结论不是一套系统，而是多条并行口径

`Mainline current state`

当前至少存在三条动作判断相关链路：

1. `scoring.py -> mart_stock_trend` 写回 `composite_priority_score / priority_pool / legacy stock_gate`
2. `institution.py -> /api/inst/stock-trends` 基于 `mart_current_relationship.follow_gate` 重算用户可见股票级 gate
3. `signals_v2.py -> /api/signals/* -> signal-adapter.js` 这条 EV/胜率/样本驱动的并排信号链
4. `stock_trends_read.py` 中还保留着基于 `priority_pool/composite` 推导 gate 的共享 helper，但它尚未接管当前主列表路由

其中第 1 条和第 2 条甚至连 `stock_gate` 这个词都共用，但业务语义并不相同；第 3 条又提供了一套更接近“事件跟随收益学”的动作判断框架；第 4 条则说明系统还有一层“计划中的共享读模型”尚未真正收口到活跃路由。

这会造成两个严重后果：

1. 系统很难向用户解释“当前跟与不跟到底以谁为准”。
2. 任意一条链路的改进都不一定会真实改善主界面最终结论。

### 6.4 阻塞点四：Qlib 当前标签与业务目标不一致

`Mainline current state`

当前 Qlib 主标签是 `Ref($close, -2)/Ref($close, -1) - 1`，这更适合用于：

1. 短周期截面排序。
2. next-day 或近似 next-day 的价格排名学习。

但本项目真正需要优化的是：

1. 一段持有期内的收益。
2. 同期回撤约束。
3. 最终跟、观察、回避结论的真实质量。

因此，当前 Qlib 即便 IC 提升，也不能直接证明最终业务目标提升。这个问题不是模型是否先进，而是标签定义从一开始就没有完全对齐最终动作结论。

### 6.5 阻塞点五：新因子的历史深度还不够支持强结论

`Shared DB current state`

当前质量、阶段、海龟等关键表的快照日分别只有 5、9、5 个。这说明这些链路刚刚开始形成时间序列，仍然不足以支持：

1. 稳定的样本外收益归因。
2. 多因子长期贡献判断。
3. 对收益、回撤、胜率进行有说服力的分层评估。

换句话说，系统当前更像“工程上已经把管线接好了，但统计上还没到能自信下结论的阶段”。

## 7. 已归档综合审计报告能为当前文档提供什么

`Archived report`

从 Git 历史可恢复的 `audit-report-2026-04-22.md`，可以提炼出三类仍然有价值的背景材料。

### 7.1 它确认了项目边界本来就不是“全市场统一择时系统”

归档审计报告的核心判断之一是：系统已经能覆盖 4612 只 A 股的数据研究范围，但真正进入“机构行为驱动的主决策链”的股票只有约 3285 只。这个结论与当前共享数据库中 `mart_stock_trend = 3285` 的量级仍然一致。

这说明项目的天然边界本来就是“机构覆盖池”，而不是“所有 A 股的统一决策器”。这一点在今天仍然成立，应该保留在总报告里，避免后续对能力边界产生错误期待。

### 7.2 它暴露了读写链不闭环和变量物化失效的问题

归档审计报告曾明确指出几个 P0 问题：

1. `win_rate_120d` 写层与读层口径不一致。
2. `historical_median_holding_days` 填充率为 0%。
3. 中位数相关算法与解释链存在问题。

这些问题不一定全部仍以同样形式存在，但它们属于和本轮主结论同一类的问题：写层和读层不闭环，导致“看起来有字段，实际上没有参与决策”或“写出来了但解释链断开”。

### 7.3 它提醒我们不要把行业粒度和变量覆盖问题当成小问题

归档报告还指出过 L3 行业缺失比例高、持有天数缺失、120 日成熟度不均等问题。这些都不是表面脏数据，而是会直接影响：

1. 行业技能判断是否可信。
2. 中长期收益和回撤标签是否完整。
3. 机构画像的时间维度是否可解释。

因此，归档综合审计报告虽然不能被当成“当前主仓已经修复完成”的证明，但它依然是理解当前项目风险画像的重要背景材料。

## 8. `Phase A-G` 分支报告到底贡献了什么

`Archived report` + `Worktree / testing-only`

从 Git 历史可恢复的 `phase-a-to-g-report-2026-04-22.md`，可以提炼出一组非常有价值、但必须明确标注为历史实验材料的结论。原始 worktree 已经删除，因此这些内容现在只能作为归档证据使用。

### 8.1 它的最大价值不是“指标变好了”，而是重新定义了哪些数据源是活的

`Worktree / testing-only`

`Phase A-G` 报告的关键贡献不是单个 IC 数值，而是承认并修正了若干现实偏差：

1. 北向数据并不适合作为当前这套系统的稳定生产真相源。
2. 需要用 QFII、两融、龙虎榜、供给相关变量等替代一些原规划假设。
3. 某些因子如果不做时间对齐，会产生 lookahead bias，得到不诚实的高 IC。

这类结论非常关键，因为它告诉我们：问题不是“模型调得不够好”，而是“源头上就有一部分真相源判断失误”。

### 8.2 它证明了“穿越修复后，指标会回落，但更诚实”

`Worktree / testing-only`

根据归档 `Phase A-G` 报告摘要：

1. 分支补回了 7 季 QFII 数据，约 7878 行。
2. 两融、龙虎榜等链路覆盖约 800 个交易日。
3. 在修复时序穿越后，IC 从更高但不诚实的数值回落到更低但可信的数值，报告记录的代表性结果为 IC 从 `0.0683` 回落到 `0.0499`，RankIC `0.0254` 略高于 baseline `0.0253`。

这件事的意义在于：未来如果要继续做 behavior、supply、expectation 因子，必须优先保证时间对齐，而不是优先追求报表上更大的数字。

### 8.3 它可以作为“下一阶段候选设计”，但不能直接写成主仓能力

`Worktree / testing-only`

`Phase A-G` 报告中涉及的以下内容，现阶段都只能写成“分支已验证 / 待决定是否吸收”，不能写成“主仓已经具备”：

1. 北向彻底退役并由替代源完全接棒。
2. QFII、两融、龙虎榜、Supply 等链已经成为主仓生产因子。
3. `signals_v2` 或其他链已经成为唯一主动作结论系统。
4. Qlib behavior / supply loader 已完全收口到主仓。

这部分材料的正确用法是：作为后续整合评审的证据库，而不是当前能力宣传稿。

## 9. 本轮重建的“数据源与因子路线报告”

`Session reconstruction`

此前会话里已经完成过两份没有进入 Git 历史、但内容值得保留的报告：

1. Tongdaxin-first 的数据源研究和 AkShare 补充策略。
2. 基于 raw -> dim -> fact -> mart -> Qlib 的因子 ETL 方案。

这两部分现在统一重建如下。

### 9.1 数据源策略：优先通达信，AkShare 做补充而不是主替代

`Session reconstruction`

基于对 `help.tdx` 与 `akshareindex` 离线文档的研究，当前最合理的数据源策略是：

1. 价格、成交、股本、基础财务、板块与行业关系，优先走 Tongdaxin / mootdx 能稳定提供的源。
2. 研报、盈利预测、估值、机构调研、分析师、新闻等“预期层”变量，优先用 AkShare 对接东财等公开接口做补充。

优先级最高的 Tongdaxin 数据族包括：

1. `get_financial_data`
2. `get_gpjy_value`
3. `get_market_data` + `get_gb_info`
4. `get_gp_one_data`
5. 关系 / 板块 / 市场交易相关数据族
6. `trackzs_etf_info`

AkShare 更适合承担的补充层包括：

1. `stock_research_report_em`
2. `stock_profit_forecast_em`
3. `stock_jgdy_tj_em` 与明细接口
4. `stock_analyst_rank_em` 与明细接口
5. `stock_news_em`
6. `stock_zh_valuation_comparison_em`
7. `stock_value_em`
8. `stock_zh_valuation_baidu`

这个分层思路的核心不是“哪个接口更多”，而是“哪个源更适合做长期稳定生产真相源”。对于本项目，Tongdaxin 更像底层事实，AkShare 更像预期与外部关注增强层。

### 9.2 本轮无代码实验说明了什么

`Session reconstruction`

此前会话里做过一次不改代码的 AkShare + 本地 Qlib 关联性实验，样本来自东财预测相关数据与本地 Qlib 数据对齐。实验的关键事实是：

1. 样本文件约 2704 行。
2. 能与本地 Qlib 数据对齐的约 2451 行。
3. 当时本地 Qlib 最新可用预测日已经滞后到 `2026-04-13`。

这个实验的意义不是“已经证明预测因子能上线”，而是三点：

1. 预期层数据可以与本地股票特征框架做对齐。
2. 不同持有期的相关性表现并不一致，说明它们不是一个“统一万能因子”。
3. 在当前主标签不对齐的情况下，任何相关性结果都只能视为探索性证据，不能直接当作生产收益证明。

### 9.3 因子 ETL 方案应该继续走统一分层，而不是业务端重复重算

`Session reconstruction`

前序方案里已经形成一个比较清晰的因子分族框架，建议继续保留：

1. `px_`：价格和路径类。
2. `fin_`：财务与经营质量类。
3. `beh_`：机构与交易行为类。
4. `exp_`：预期与研报类。
5. `sup_`：供给与股本约束类。
6. `ctx_`：行业与市场背景类。
7. `att_`：外部关注与拥挤度类。

建议的存储纪律仍然应该是：

1. `raw` 只追加，不覆盖。
2. `dim` 保存当前快照与中性映射。
3. `fact` 保存时间序列真相。
4. `mart` 保存面向研究和展示的复用层。
5. Qlib 只消费统一整理过的历史面板，而不是直接让前端或路由层各自拼装。

这个原则与项目原有的“单点计算、多处复用”是一致的，也能防止同一个业务事实在不同页面和不同模型链里被重复计算、重复解释。

### 9.4 未来 Qlib 的正确升级方向

`Session reconstruction`

当前 Qlib 最大的问题不是“因子还不够多”，而是“标签不够对齐”。因此后续升级的顺序应该是：

1. 先决定最终业务动作结论以哪一条链为主。
2. 再基于该业务动作定义训练标签，例如中期 forward return 与 drawdown 约束的组合。
3. 最后才去评估 behavior、supply、expectation 等新因子是否真的改善了最终结果。

如果顺序反过来，最终只会得到“模型指标更好，但主业务结论没有真正闭环”的局面。

## 10. 当前系统能不能实现目标

### 10.1 能实现到什么程度

当前系统已经能够做到：

1. 研究机构持仓披露行为及其后续收益表现。
2. 构建股票侧多维度列表与详情，包括质量、阶段、预测、外部关注、海龟等维度。
3. 生成股票级综合分、池子和一套 `stock_gate`。
4. 并排展示另一套基于历史类似事件 EV/胜率/样本的 `signals_v2` 结论。
5. 为未来的数据源扩展和因子升级保留足够的工程结构空间。

### 10.2 还不能实现到什么程度

当前系统还不能可靠宣称：

1. 机构层评分已经真实驱动最终股票动作结论。
2. 系统已经只有一套统一且权威的跟与不跟结论。
3. 当前 Qlib 训练目标已经对齐最终业务目标。
4. 新增阶段、海龟、供给、预期等因子已经拥有足够长的历史，可支撑稳定优化结论。
5. `Phase A-G` 分支的改造已经被主仓吸收并稳定运行。

### 10.3 因此当前最准确的表述应该是什么

当前最准确的项目状态表述应当是：

“系统已经具备较完整的机构事件研究和股票研究基础设施，也已经产出股票侧行动结论与并行信号链，但主业务闭环仍未完全形成。当前最主要的问题是机构评分未闭环、主仓与分支及共享数据库错位、最终动作结论存在多口径并存，以及 Qlib 标签与业务目标尚未对齐。”

这句话比“已经做成了”更准确，也比“还什么都没做成”更公平。

## 11. 单一优先级路线

在吸收了主仓证据、共享 DB 证据、归档报告和前序研究后，推荐的唯一行动顺序如下：

1. 先做主仓阻塞点证据包，不先盲目 merge worktree。
2. 再从 Git 归档中决定 `Phase A-G` 相关改动里哪些要选择性恢复，哪些要拒绝，哪些要重做。
3. 修复机构评分闭环，确保 `mart_institution_profile` 的核心评分字段真实写回且被后续链消费。
4. 收口最终动作结论系统，明确 legacy `stock_gate`、MCR 聚合 gate、`signals_v2` 三者的主从关系。
5. 在确定主动作结论语义后，重定义业务对齐的 Qlib 标签，再重新评估 behavior / supply / expectation 等因子价值。
6. 最后才继续扩大数据源、拉长历史、做多因子和多阶段统计验证。

如果顺序反过来，例如先 merge、先扩因子、先追求更高 IC，而不解决动作结论收口和机构评分闭环，项目会继续出现“看起来功能更多了，但真正的主业务判断并没有变得更可信”的问题。

## 12. 证据锚点

以下文件是本讨论文档的主要代码锚点：

1. `backend/main.py`：主仓路由注册，确认 `/api/signals` 已并行存在。
2. `backend/routers/updater.py`：当前真实 DAG 与步骤集合。
3. `backend/services/scoring.py`：机构评分、股票评分、综合分、池子、legacy `stock_gate` 来源。
4. `backend/routers/institution.py`：当前活跃的 `/api/inst/stock-trends` 路由，直接组装股票研究主列表，并按 `mart_current_relationship.follow_gate` 聚合用户可见 gate。
5. `backend/services/stock_trends_read.py`：计划中的共享读模型，以及基于 `priority_pool/composite` 推导 `stock_gate` 的 helper 语义。
6. `backend/services/signals_v2.py`：并行 EV/胜率/样本驱动决策链。
7. `assets/js/app.js`：股票研究页对 `stock_gate` 的主消费位置。
8. `assets/js/signal-adapter.js`：`signals_v2` 的前端唯一适配层。
9. `backend/services/qlib_full_engine.py`：当前 Qlib 主标签和训练基础配置。
10. `data/smartmoney.db`：当前共享数据库的真实成熟度、覆盖度和断裂点。

以下材料属于归档或会话重建材料，不应误认为当前工作树文件：

1. `audit-report-2026-04-22.md`：可从 Git 提交 `0465c50d` 恢复其内容，用于背景与历史审计结论。
2. `phase-a-to-g-report-2026-04-22.md`：可从 Git 提交 `027056a3` 恢复其内容，用于已删除 worktree 的历史实验背景。
3. Tongdaxin-first 数据源研究、AkShare + Qlib 相关性实验、因子 ETL 方案：本轮会话已重建其核心结论，但此前未进入 Git 历史，应视为 `Session reconstruction` 材料。

## 13. 最终一句话

当前项目不是“没做成”，而是“已经做出了一套很有潜力但尚未收口的研究系统”。真正决定它能不能成为你要的那套机构事件研究系统的，不是再多接几个因子，而是先把机构评分闭环、动作结论收口、主仓与分支现实对齐这三件事做扎实。

---

## 14. Claude 的评估意见（2026-04-22，基于主仓代码与共享 DB 实地核查）

这一节是独立评估，基于本轮实际跑 grep 和 sqlite 核验后的结论，不是对上文的简单复述。

### 14.1 事实核查结果：上文有一处需要修正的陈述

文档 §6.1 声称"主仓代码仍然保留了北向相关结构"，并列出 `sync_northbound`、`fact_northbound_daily` schema、`use_northbound` 三处代码锚点。本轮核查结果：

1. `backend/routers/updater.py`：无 `sync_northbound` 匹配。
2. `backend/services/qlib_full_engine.py`：无 `use_northbound` 或 `northbound` 匹配。
3. `backend/services/db.py`：无 `northbound` 匹配。
4. `backend/` 整体 grep `northbound`：0 处匹配。
5. `data/smartmoney.db`：`fact_northbound_daily` 表确实不存在。

也就是说，主仓代码和共享 DB 其实是**一致的**（两者都已经不含北向），不一致的只是 `Phase A-G` 等归档文档的叙述。这不改变"项目存在归档叙述与主仓现实错位"的大结论，但把阻塞点一的性质从"代码 bug"降级为"文档未同步"。**§6.1 的结论已经按这个方向改写**：主要问题是"归档文档未随主仓同步更新，导致后续接手的人会基于过时证据判断"，而不是"主仓仍在调用不存在的表"。

相应地，我之前准备写的"北向 P0 热修"建议取消——没有热修标的。

### 14.2 核心判断：同意的部分

以下几点是经过实地核验后的共识性判断：

1. **机构评分的当前问题已经从“字段全空”变成“字段已补齐但未驱动生产主结论”**。最新共享 DB 复核结果是：`mart_institution_profile` 231 行，`quality_score` 和 `followability_score` 非空计数均为 231。这意味着根因定位与补跑动作已经把“有没有分数”解决掉了，但“这些分数是否真正进入当前生产主动作链”仍然没有解决。对“机构是主角”的约束而言，这仍然是高优先级问题，但性质已经从数据缺失转成架构脱钩。
2. **动作结论多口径并存**是真实存在的架构问题，不能通过前端 UI 调整解决，必须在读写层完成收口。
3. **Qlib 当前标签与业务目标不一致**判断正确，但这个问题的优先级应当排在机构评分闭环之后（机构评分闭环才是根，Qlib 标签是叶）。

### 14.3 需要补充具体性的部分

文档在方向上准确，但若干"下一步"仍偏目标化，缺少可验证的交付物定义：

1. **"修复机构评分闭环"需要先根因定位，再谈修复**。在早前 `quality_score` 仍为空的阶段，可能成因至少有四种：(a) `calculate_institution_scores()` 未被 DAG 实际调用；(b) 调用了但上游 `mart_institution_profile` 的特征列全空，百分位归一化无输入；(c) 写回时的 `UPSERT` 键不匹配导致静默失败；(d) schema 字段名不一致导致写到了另一列。这四种场景修复路径完全不同。即使当前字段已补齐，后续若再出现评分断裂，第一步也仍应先写一段不超过 30 行的诊断 SQL + Python，输出 `{DAG 步骤执行时间, 上游特征非空率, 写回字段 NULL 率}` 三列对账表，而不是直接跳到"修复"。
2. **"Qlib 标签重新对齐"需要初版定义**。"中期 forward return 与 drawdown 约束组合"至少要明确三个参数：持有期（建议 20 交易日，与机构季度披露周期接近）、drawdown 阈值（建议从 -8% 起跳做敏感性）、标签形态（回归还是三分类）。没有初版参数，这条路线无法进入可审议状态，最终还是会退化为"知道要改，不知道改成什么"。
3. **"主仓阻塞点证据包"需要形式化**。建议具体交付：一份可重现的诊断脚本 + 一份针对 3 只代表性股票（大市值/中盘/小盘 各 1）的全链路对账结果，把三条 gate 链路在同一只股票上的**具体不一致案例**写出来。没有具体样例的"多口径并存"讨论容易流于抽象。

### 14.4 需要推翻或修正的判断

1. **`institution.py` 基于 MCR 聚合的 gate 不是死代码，而是当前股票列表主路径的一部分**。前端 `assets/js/app.js` 现在直接请求 `/api/inst/stock-trends`，而该路由仍在 `institution.py` 中手写组装，并基于 `mart_current_relationship.follow_gate` 重算用户可见 `stock_gate`。因此当前问题不是“它活不活着”，而是“它确实活着，并且与写层 legacy gate 语义并存”。
2. **`signals_v2` 作为"并排第二证据链"是良性设计，不应被合并进 legacy gate**。`signals_v2.py` 的模块注释明确写了"物理隔离 legacy 综合评分概念"，这是有意为之的防污染边界。"收口动作结论"的正确动作是**定义主从关系**（哪个是主结论、哪个是辅助证据、用户侧如何呈现），而不是把两者合并成一个数字。合并只会损失信息、增加 debug 难度。文档第 11 节第 4 步的"明确主从关系"措辞是对的，但需要在执行时明确：主从 ≠ 合并。
3. **历史深度不足是硬约束，不能只写成"阻塞点五"然后等它自然成熟**。当前 `fact_stock_stage_features` 9 个快照日、`fact_stock_turtle_features` 5 个快照日，这意味着任何统计结论都在冷启动期。建议两条并行动作：(a) 用 Tongdaxin / AkShare 回填 60~120 日历史特征，把冷启动期从 3 个月压缩到 1 个月以内；(b) 在用户侧主界面的 `stock_gate` 旁强制挂"历史深度不足，当前为探索期"标签，到 60 个交易日样本后再去掉。默默展示结论然后事后解释"其实当时数据还不够"比先标注再积累要失信得多。

### 14.5 三可原则没有被执行

文档提到"可见、可追溯、可复核"原则，但在诊断当前系统时并未把它当作审计清单使用。建议下一版讨论文档补一张矩阵：

| 进入最终 `stock_gate` 的变量 | 可见（在哪页展示） | 可追溯（源表+计算路径） | 可复核（用户能从明细复算吗） |
| --- | --- | --- | --- |
| `composite_priority_score` | | | |
| `priority_pool` | | | |
| `qlib_rank` / `qlib_score` | | | |
| `quality_score`（机构层） | | | |
| `follow_gate`（机构持仓关系聚合） | | | |

如果某行出现"否/否/否"，说明这个变量虽然在代码里存在，但在业务可解释性上**等于不存在**。这比"代码里接了多少因子"更能反映系统真实成熟度。考虑到 `quality_score` 当前虽已补齐但仍未进入当前主动作链、`qlib_predictions` 最新日期停留在 2026-04-13 且非全市场覆盖——这张表多半会很难看，但必须看。

### 14.6 路线优先级的微调

文档第 11 节 6 步方向正确，但建议两处调整：

1. **取消北向 P0 热修层**（按 §14.1 的核查结论，主仓代码已经清理干净，没有热修标的）。需要做的只是把归档报告中残留的 `sync_northbound` / `use_northbound` 类叙述标注"已退役，仅历史参考"。
2. **允许并行道路，但并行对象应来自 Git 归档中的 `Phase A-G` 历史改动，而不是已删除 worktree**。其中"时间对齐修正"和"Supply 归一化"这类属于"诚实性修复"（即纠正已有逻辑的 lookahead bias、口径错误），可以在主仓收口期间平行选择性恢复——它们不引入新因子权重、不改变动作结论语义，风险低于"等待主仓全部完工"的停滞成本。需要拒绝的是"扩因子、调权重"类改动，直到机构评分闭环和动作结论收口完成。

### 14.7 风险提示：决策瘫痪

"先闭环再扩展"的路线如果执行不当会退化为决策瘫痪——团队陷入"一直在讨论主仓该如何收口，但没有一项真正落地"的状态。建议设置硬窗口：

1. **14 天内**：机构评分闭环根因定位报告 + 修复 PR（不是"修好"，是"知道问题在哪、有修复路径"）。
2. **30 天内**：动作结论主从决策书（不超过 2 页，明确 legacy `stock_gate` / MCR gate / `signals_v2` 三者的主从关系和用户侧展示规则）。
3. **60 天内**：Qlib 标签对齐首版上线；归档 `Phase A-G` 变更的选择性恢复/舍弃决策完成；多口径 gate 的主从关系完成落地。

超过上述窗口仍未落地，说明路线本身有问题，需要重新评估是否应该反向操作（先 merge worktree 在稳定基础上修复，接受更高短期风险但避免长期停滞）。

### 14.8 一句话总结

这份讨论文档在"诊断"环节扎实，在"为什么有问题"环节清晰，但在"具体怎么修、按什么节奏修、怎么判断修完了"环节仍偏原则。建议以它为底座，再产出两份更聚焦的配套文档：**机构评分闭环根因定位报告**（1~2 天可完成，SQL + 代码走查即可）和**动作结论主从决策书**（≤2 页）。这两份做完，项目就从"知道有问题"进入"知道怎么做"。在此之前，不建议启动任何新数据源或新因子扩展——扩展只会放大当前多口径并存带来的解释困难。

---

## 15. 价值观纠偏：从"指标堆叠"到"业绩实证"（2026-04-23）

这一节是对 §14 及 Phase 1–4 执行后的**原则性反思**，触发自用户的一句追问：

> 评分再高业绩不行也没用。验证机构评分导致可跟、观察的结论变化，应该看这个机构历史数据回测的平均收益、最大回撤、整体风格、介入时机、退出时机等指标，而不是单纯的设置参数。把指标加工成数字但忽略了我们只是想跟投并赚钱同时减小回撤。

§14 的评估假定："如果机构评分字段有值、被正确加权进 composite，系统就闭环了"。这个假定是**错的**。闭环不是字段有值+权重合理；闭环是**"跟这家机构是不是真能赚钱"在决策链里有可查证的回答**。

### 15.1 `quality_score` 与真实业绩的实证相关性

`Shared DB current state`

把 231 家机构按 `quality_score` 分 5 桶，对真实业绩字段做均值，得到：

| 桶 | quality_score 区间 | n | buy_win_rate_60d | buy_avg_gain_60d | buy_dd_60d | exit_post_avg_gain_30d | safe_follow_win_rate_30d |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1_top | ≥ 70 | 25 | 63.3% | **+9.77%** | -15.93% | +3.15% | 65.0% |
| 2_high | 55–70 | 54 | 57.2% | +7.40% | -16.34% | +2.29% | 59.0% |
| 3_mid | 40–55 | 50 | 54.4% | +3.57% | -16.25% | +1.10% | 54.1% |
| 4_low | 25–40 | 47 | 50.7% | +2.36% | -16.41% | +1.12% | 50.9% |
| 5_worst | < 25 | 55 | 36.2% | **–1.26%** | -18.14% | 26.8% | 26.8% |

三个观察：

1. **收益维度 quality_score 有效区分**：top 桶 +9.77% vs worst 桶 -1.26%，差 11 个百分点，单调。
2. **回撤维度 quality_score 几乎无效**：top 桶 -15.93% vs worst 桶 -18.14%，差只有 2 个百分点。这意味着用户在乎的"减小回撤"目标上，`quality_score` 几乎不提供信号。
3. **退出时机维度反向或弱相关**：`exit_post_avg_gain_30d` 正值表示"机构卖飞了"（退出后股价继续涨）。top 桶 +3.15% 意味着 top 机构退出后股价平均继续涨 3.15%，比 worst 桶 -1.54% 更"卖飞"。这不是业绩质量，是退出时机偏差。当前 quality_score 不惩罚卖飞。

**判定**：`quality_score` 只解决了"找会赚钱的机构"一半问题，没解决"回撤小"和"退出时机准"的另一半。用户目标是"跟投赚钱 + 减小回撤"，当前评分只覆盖了收益维度。

### 15.2 指标链路事实：多数业绩字段是"事后展示墓地"

`Mainline current state`

实地 grep 结果：

| 字段族 | 加工位置 | 消费位置 | 进入业务决策？ |
| --- | --- | --- | --- |
| `avg_gain_*` / `win_rate_*` / `median_max_drawdown_*`（全事件） | `scoring.py` 构建 profile 时聚合 | `institution_scoring_read.py` UI 展示 | ❌ 仅展示 |
| `buy_avg_gain_*` / `buy_win_rate_*` / `buy_median_max_drawdown_*` | 同上 | `scoring.py` 算 `quality_score` + UI 展示 | 🟡 进入 quality_score，但 quality_score 不驱动 gate |
| `exit_post_avg_gain_*` / `exit_avoid_loss_rate_*` | `return_engine` 计算 | UI 展示 | ❌ 不进决策 |
| `signal_transfer_efficiency_30d` | `followability_score` 原料 | 同上 | ❌ 同样停在 followability_score |
| `safe_follow_*`（安全跟随回测字段） | `return_engine` | `followability_score` + UI | ❌ 同样不驱动 gate |
| `fact_institution_event.gain_*d`（事件级原始收益） | 已有 | `signals_v2.py` 实时 KNN | ✅ 真·驱动 follow/skip |
| `fact_stock_stage_features` 的 `dist_ma250_pct` / `return_1m/3m/6m/12m` / `above_ma250` / `max_drawdown_60d` | 已加工 | 只进 `composite_priority_score`（股票侧综合分） | ❌ 不进"机构事件"的 follow/skip 决策 |

**在当前几条并行链里，唯一直接消费事件级收益字段的是 `signals_v2`**。它不读 `mart_institution_profile` 任何字段，而是临时从 `fact_institution_event` 拉该机构历史 buy 事件、现场算 EV 和胜率；但它并不是当前用户主界面唯一的生产主链。这意味着：

- `mart_institution_profile` 中 95% 业绩字段无论怎么加工，都不会影响用户看到的 follow/skip 决策
- `quality_score` 即便补齐（Phase 1/2 已做），即便加进 composite（Phase 3 建议），依然不触及 `signals_v2` 这条真正起作用的链路
- 介入时机的原料（`dist_ma250_pct`、`return_3m`）已经被 `fact_stock_stage_features` 算出来，但 `signals_v2` 看不到

### 15.3 用户四个核心问题的系统回答状态

按用户原话拆成四个具体问题，实证回答状态：

| 问题 | 系统当前回答 | 回答质量 |
| --- | --- | --- |
| 这机构历史真的能赚钱吗 | `signals_v2` 用事件级 `gain_*d` 算 EV 和胜率 | 🟡 回答了，但不用聚合字段，可能信号稀疏 |
| 介入是低位还是追高 | 完全没有判断 | ❌ 原料已具备（stage features），决策链不读 |
| 退出时机准不准（是否常卖飞或误抛） | 完全没有判断 | ❌ 数据已算好（`exit_post_avg_gain_*`），决策链不读 |
| 机构风格稳不稳 | 只看当前季度 `concentration` 和 `top_industry_1` | 🟡 没有跨季度漂移度量 |

**四个问题里只有一个被部分回答**。这正是用户指出的"加工成指标但忽略了跟投目标"的实证：系统**有能力**回答这四个问题（数据和加工大多齐全），但**结论没接入**。

### 15.4 问题不是"调权重"，是"评估范式"选错了

当前评分范式是**加权打分**：

```
quality_score = Σ(percentile_rank(metric_i) × weight_i) × confidence_factor
```

这个范式有三个本质缺陷，无法通过调权重解决：

1. **百分位归一化丢失绝对信号**
   - 如果 231 家机构整体都差（全 buy_win_rate_30d < 50%），排名第一的那个仍拿到 100 分。
   - 用户需要的是"这家机构跟它过去 2 年能不能赚钱"的绝对信号，百分位答不了。

2. **加权可以让劣势维度被优势维度掩盖**
   - 某机构收益好（权重 40%）但退出时机很差（权重 0%），加权后仍 top 分。
   - 用户判断的是多条件的 AND（赚钱 AND 回撤小 AND 退出准），不是 OR。

3. **权重表没有业绩验证**
   - `INST_SCORE_DEFAULTS = {sample:10, gain_30d:15, gain_60d:15, gain_120d:10, win_rate_30d:15, win_rate_60d:10, win_rate_90d:5, drawdown:10, stability:10}` 是设计直觉，不是"跟投回测最优权重"。
   - 没有实验证明：换成 {gain:30, drawdown:30, exit_quality:30, sample:10} 一定更差。

**判定**：要解决的不是 "quality_score 该加权多少进 composite"，而是 "要不要把评分范式从加权改成业绩实证漏斗"。

### 15.5 新范式建议：三层业绩实证漏斗

不用评分数字，用**布尔条件**。通过全部条件的机构/事件/股票才有资格 follow。

#### 第一层：机构初筛（当前系统缺失）

用绝对阈值（不是百分位），目标是淘汰"业绩不行的机构"：

| 硬筛条件 | 阈值建议 | 理由 |
| --- | --- | --- |
| `buy_event_count` ≥ 30 | 样本充足度 | 少于 30 事件统计不显著 |
| `buy_win_rate_60d` ≥ 55% | 确实能赚 | 对应 top+high 两个桶的下沿 |
| `buy_avg_gain_60d` ≥ 5% | 收益有肉 | 交易成本+机会成本吸收后仍为正 |
| `buy_median_max_drawdown_60d` ≥ –15% | 回撤可控 | 单标的回撤是用户最痛的指标 |
| `exit_post_avg_gain_30d` 绝对值 ≤ 3% | 退出时机准 | 不常卖飞也不常误抛 |
| `concentration` ≥ 40% 或 top_industry_1 连续 4 季度稳定 | 风格稳定 | 专家型比漂移型可解释 |

通过以上全部条件的机构才进入"可跟池"。**注意：不是加权，是 AND**。

`Session reconstruction`：用当前 DB 跑这六条硬筛，初估通过机构数约 20–40 家（基于 §15.1 的 25 家 top + 部分 high），是"精选池"而不是"全覆盖池"。对"机构是主角"最贴切。

#### 第二层：事件层筛选（当前部分完成）

机构过了第一层后，每条新事件还要过：

| 硬筛条件 | 状态 |
| --- | --- |
| event_type ∈ {new_entry, increase} | ✅ 已做 |
| premium_pct ≤ 15% | ✅ 已做（signals_v2） |
| hold_ratio ≥ 阈值 | ✅ 已做 |
| 业绩预告不弱 | ✅ 已做 |
| **介入时机**：`return_3m` ≤ +50%（避免追高） | ❌ 待做 |
| **介入时机**：`dist_ma250_pct` ≥ –20%（避免深套补仓） | ❌ 待做 |
| **介入时机**：`above_ma250` = 1 或 `return_12m` > 0（避免长期下跌股） | ❌ 待做 |

#### 第三层：股票层筛选（当前部分完成）

| 硬筛条件 | 状态 |
| --- | --- |
| company_quality_score ≥ 40 | ✅ 已做（通过 composite） |
| 行业不在黑名单 | ✅ 已做 |
| 无解禁悬顶 | 🟡 supply 因子在归档 Phase A-G，未主仓化 |

**三层全部通过 → `primary_action = follow`。任一层不过 → `watch` 或 `avoid`**。

这是漏斗思维，不是加权思维。优势：

- 每个决策步骤可以追溯到具体业绩字段，不是黑盒加权
- 用户可以手工复核："这家机构过了初筛吗？这条事件介入时机好吗？"全部可见
- 满足 §14.5 三可原则里"可复核"的要求

### 15.6 对已做工作的修订

| 原工作 | 状态 | 修订说明 |
| --- | --- | --- |
| Phase 1：机构评分根因定位 | 保留 | DAG 漏跑是表层事实，报告仍成立，但不再是阻塞点一 |
| Phase 2.1：补跑 CLI | 保留 | 基础设施，不伤业务 |
| Phase 2.2：补跑股票评分 | 保留 | 同上 |
| Phase 3：三可矩阵 | 保留 | 矩阵诊断正确 |
| Phase 3：主从决策书 | **部分推翻** | "把 quality_score 加进 composite" 不是答案；"硬门槛 ≥ 55" 在 95% 股票上失效；应当废弃"加权式 primary_action"设计 |
| Phase 4：Qlib 标签对齐 | **暂缓+重写** | 新标签应以"通过第一层机构初筛的事件"为训练样本，不是全市场截面 |

### 15.7 新的优先级（替代 §14.7）

`Session reconstruction`

**14 天内**（核心交付：业绩实证基础设施）：

1. 跑一次"跟投回测"：对每家机构，假设自 2023 年初每次 buy 事件都按规则跟入、持有 20 日、按规则卖出，汇总得到机构级 IRR、MaxDD、Sharpe、胜率。落表 `fact_institution_follow_backtest`。
2. 对 3–5 家代表机构（大型公募 / 知名私募 / 主题游资）做手工复核：回测结果与人直觉一致吗？
3. 用 §15.5 第一层初筛规则跑一遍，观察通过机构数。如果 < 10 家，放宽阈值；如果 > 60 家，收紧。目标 20–40 家。

**30 天内**（核心交付：决策链路收口）：

4. `signals_v2` 接入初筛：未过初筛的机构事件直接标记 `avoid`，不再跑 KNN。
5. 介入时机三条件落地：signals_v2 查询 `fact_stock_stage_features` 对应交易日的 `return_3m`、`dist_ma250_pct`、`above_ma250`，不达标打 `watch`。
6. `mart_stock_trend.primary_action` 首版上线，只用漏斗，不用加权。

**60 天内**（核心交付：Qlib 对齐业绩目标）：

7. 新 Qlib 标签：以"通过漏斗前两层的事件"为正样本，训练"条件胜率模型"，输出后验概率。
8. 前端改造：把 quality_score 从"跟投建议依据"降级为"历史参考指标"；主动作列只展示"通过/未通过 机构初筛"+"介入时机好/中/差"两个布尔。
9. 归档 Phase A-G 的 behavior/supply 因子选择性恢复（只选能进入漏斗条件的，不选只提升 Qlib IC 的）。

### 15.8 元批判：§15 本身也要被这个原则检验

§15 给出的三层漏斗条件（≥ 55%、≥ 5%、≥ –15% 等）是**设计直觉**，不是跟投回测优化后的值。严格按本节提出的原则，应当：

- 先做 14 天的 `fact_institution_follow_backtest`
- 然后用回测结果**反推**每个阈值：在哪个阈值上通过机构的集合 IRR 最高、MaxDD 最小
- 用回测数据定阈值，而不是用直觉定阈值

因此 §15 的阈值只是"**第一版起点**"，14 天内必须被跟投回测数据替换。这是唯一能避免本节自身也掉进"堆指标不回测"陷阱的办法。

### 15.9 一句话总结（替代 §14.8）

本项目真正的进展衡量不是"有多少指标、字段、模型、评分"，而是**"用户按系统推荐跟投后，是否真的赚到钱并控制住了回撤"**。在产出 `fact_institution_follow_backtest` 并用回测数据驱动漏斗阈值之前，所有评分权重调整、composite 公式改造、Qlib 标签重训，都是**方向可能对但证据不足的中间工作**。系统需要的下一步不是更多参数，而是**一张能直接告诉用户"跟这家机构过去能赚多少、亏多少"的回测表**。

## 16. 补充实验：不改代码，用真实数据仿真“把机构评分接入整体评分架构”后的影响

日期：2026-04-23

这一节回答一个更具体的问题：如果不改任何源码，只用当前真实库数据，按现有 `scoring.py` 的后半段规则把机构评分真正接入 legacy 综合评分链，结果到底会不会变，变化有多大。

### 16.1 实验目的与约束

本实验刻意遵守三个约束：

1. 不修改任何代码文件。
2. 不写回生产数据库，只在内存中重算。
3. 尽量复用当前 `scoring.py` 已存在的 helper 和阈值，而不是另造一套新算法。

因此，这不是"新方案上线效果"，而是"在当前架构内最保守地把机构评分接上以后，legacy 综合分 / 池子 / gate 会受到多大影响"的只读仿真。

### 16.2 仿真假设与口径

本轮仿真采用以下口径：

1. 股票级机构聚合分 `inst_score`：从 `mart_current_relationship` 取当前持仓机构，join `mart_institution_profile`，先算单机构对股票的 `pair_score = quality_score * 0.4 + followability_score * 0.6`；若单项为空则按 50 fallback；再按 `hold_market_cap` 加权平均到股票级，若 `hold_market_cap` 缺失或为 0，则权重按 1 处理。
2. 接入位置：不改 discovery / quality / stage / forecast 四层子分，只在 raw composite 这一层加入机构分。
3. 两档接入强度：
	- `15%` 档：`new_raw = old_raw * 0.85 + inst_score * 0.15`
	- `30%` 档：`new_raw = old_raw * 0.70 + inst_score * 0.30`
4. 其余逻辑尽量沿用当前实现：
	- 外部确认 boost 继续用 `scoring.py` 的 `_external_attention_boost()`
	- 拥挤惩罚继续沿用 `external_crowding_penalty`
	- 封顶继续用 `apply_composite_ceiling()`
	- 池子继续用 `assign_priority_pool()`
	- legacy `stock_gate` 继续用 `derive_stock_gate_from_priority()`

这个口径的意义是：只回答"机构分接入当前 legacy 综合评分架构后会怎样"，不回答"未来新的 `primary_action` 应该怎么设计"。后者已经在 Phase 3 / Phase 4 文档里讨论。

### 16.3 基线校验：仿真没有偏离当前实现

在跑接入仿真之前，先用库里的现有字段按上述 helper 重算一次当前 legacy `priority_pool` 和 legacy `stock_gate`。结果是：

1. `priority_pool` 匹配 `3285 / 3285`
2. legacy `stock_gate` 匹配 `3285 / 3285`

也就是说，本节仿真不是拍脑袋估算，而是建立在"现有规则可以被准确复算"这一前提上，可信度足够高。

### 16.4 当前真实覆盖情况

当前真实库的覆盖情况如下：

1. `mart_institution_profile` 机构总数 `231` 家。
2. `quality_score` 非空 `231` 家，`followability_score` 非空 `231` 家，当前机构评分字段已经是 100% 填充。
3. `mart_stock_trend` 中可计算股票级机构聚合分的股票数为 `3285` 只，与当前 legacy 股票评分覆盖量一致。
4. 股票级 `inst_score` 的分布为：均值 `56.89`，中位数 `56.38`，最小值 `3.94`，最大值 `76.48`。
5. 当前 legacy 池子的机构聚合分均值几乎是平的：A池 `56.21`，B池 `56.62`，C池 `57.31`，D池 `57.26`。

这组数字本身就说明一个关键事实：**当前 legacy 综合评分体系与机构评分几乎是脱钩的。** 如果它们本来是同一条主链，A/B/C/D 池在机构聚合分上不应该几乎没有梯度。

### 16.5 基线分布

当前 legacy `priority_pool` / legacy `stock_gate` 的基线分布为：

1. `follow / A池`：`290`
2. `watch / B池`：`1458`
3. `observe / C池`：`820`
4. `avoid / D池`：`717`

注意，这一节讨论的是写层 legacy gate，不是当前 `institution.py` 活跃路由按 MCR 聚合出来的用户可见 gate。

### 16.6 仿真结果：15% 权重接入

把机构聚合分以 `15%` 权重接入 current raw composite 后，结果如下：

1. `priority_pool` 变更 `326 / 3285`，变更率 `9.92%`。
2. legacy `stock_gate` 变更 `326 / 3285`，变更率同样为 `9.92%`。
3. 新分布变为：`follow 115`、`watch 1538`、`observe 937`、`avoid 695`。
4. 最主要的迁移路径是：
	- `follow -> watch`：`175`
	- `watch -> observe`：`110`
	- `avoid -> observe`：`24`
5. 综合分上修最明显的样本包括：`准油股份`（`+4.68`）、`襄阳轴承`（`+4.54`）、`深水规院`（`+4.48`）、`威帝股份`（`+4.43`）、`常铝股份`（`+4.40`）。
6. 综合分下修最明显的样本包括：`中芯国际`（`-9.79`，`watch -> observe`）、`中船特气`（`-7.20`，`observe -> avoid`）、`宏盛股份`（`-6.81`，`follow -> watch`）。

这个结果说明：即使只做最保守的 `15%` 接入，结果也绝不是"几乎不变"。只要机构评分真正进入综合分主链，约一成的股票池子 / 档位会发生变化。

### 16.7 仿真结果：30% 权重接入

把机构聚合分以 `30%` 权重接入 current raw composite 后，结果进一步放大：

1. `priority_pool` 变更 `604 / 3285`，变更率 `18.39%`。
2. legacy `stock_gate` 变更 `604 / 3285`，变更率同样为 `18.39%`。
3. 新分布变为：`follow 23`、`watch 1520`、`observe 1058`、`avoid 684`。
4. 最主要的迁移路径是：
	- `follow -> watch`：`267`
	- `watch -> observe`：`246`
	- `observe -> watch`：`42`
	- `avoid -> observe`：`41`
5. 综合分上修最明显的样本包括：`准油股份`（`+9.36`）、`襄阳轴承`（`+9.07`）、`深水规院`（`+8.97`）、`威帝股份`（`+8.86`）、`常铝股份`（`+8.79`）。
6. 综合分下修最明显的样本包括：`中芯国际`（`-19.58`，`watch -> avoid`）、`中船特气`（`-14.40`，`observe -> avoid`）、`宏盛股份`（`-13.61`，`follow -> watch`）。

这个结果也解释了为什么 A池 / `follow` 档收缩极快：当前很多高 composite 股票本来就在 `80+` 分附近，而股票级机构聚合分的中心区间只有 `50-60` 分。一旦把后者真正接进 raw composite，很多原来由 discovery / quality / stage / forecast 拉起来的高分股会发生明显的均值回归。

### 16.8 这组实验说明了什么

这组只读实验支持四个结论：

1. **"补跑机构评分后 gate 不变"不是因为机构评分太弱，而是因为当前主链根本没有接进去。** 一旦真正接入，即使只给 `15%` 权重，也会立刻改动约一成股票；给到 `30%` 时，改动接近两成。
2. **当前 legacy 综合分是明显 stock-centric，而不是 institution-first。** 证据不是主观判断，而是当前 A/B/C/D 池的机构聚合分均值几乎无梯度。
3. **如果以后要把机构评分接入 legacy composite，不能只做权重接线，还要重做池子阈值治理。** 否则会出现 `follow / A池` 大幅缩容，而这既可能是修正，也可能是简单的均值回归副作用。
4. **Phase 3 里提出的 `primary_action` 新字段路线是有必要的。** 因为如果直接把机构分硬塞回当前 legacy composite，会产生真实影响，但影响形态更像"把 stock-centric 系统往 institution-first 硬扳"，不一定是最优结构。

### 16.9 重要边界：为什么当前用户主列表仍然不会变

必须单独强调一件事：本节仿真改变的是**写层 legacy 综合分语义**，不是当前用户主列表的实际展示结果。

原因很简单：当前活跃的 `/api/inst/stock-trends` 路由仍然在 `institution.py` 中按 `mart_current_relationship.follow_gate` 聚合用户可见 `stock_gate`。因此：

1. 本次实验没有写回数据库，前端自然不会变化。
2. 就算未来把这套仿真真的写回 `mart_stock_trend.stock_gate`，只要活跃路由仍消费 MCR 聚合 gate，主列表也还是不会跟着变。

换句话说，本节实验说明的是：**把机构评分接入评分主链会显著改变 legacy 综合结论；但在当前主仓实现里，这仍然不会自动传导到用户主列表。** 这也再次证明，当前真正的问题不是单个权重，而是"评分主链"与"活跃展示主链"是两条不同的链。

### 16.10 本节结论

如果只问一句话，本节结论可以写成：

**用真实数据做只读仿真后可以确认，把机构评分真正接入当前 legacy 综合评分架构并不会得到"几乎没影响"的结果；相反，它会对约 10% 到 18% 的股票池子 / 档位产生实质影响。当前之所以补跑机构评分后用户侧几乎看不到变化，根本原因仍然是架构脱钩，而不是机构评分没有辨识力。**
---

## 17. Qlib 条件概率建模设计：机构×L2 行业、追高惩罚与多任务评级（2026-04-23）

这一节是对用户三个具体诉求的正面回答：

1. 机构擅长的 L2 行业，系统是否已经解决？
2. "机构可跟 + 画像可跟，但股价较报告期涨幅大"这种追高情形，胜率/收益是否做过回测？是否体现差异？
3. 充分利用 Qlib 各种能力（Alpha158、Handler 扩展、多任务、SHAP、回测）设计评级预测。

### 17.1 诉求一的现状核查：机构×L2 擅长行业

`Shared DB current state` + `Mainline current state`

数据侧**已经解决**，但决策链未消费。事实：

- `research_inst_industry_performance` 表存在，已有 L1 1338 行、L2 **3003 行**、L3 2223 行，覆盖 221 家机构 × 13/56/76 个行业。
- 该表有完整字段：`buy_event_count`、`avg_gain_*d`、`win_rate_*d`、`avg_max_drawdown_*d`、`avg_premium_pct`、`low_premium_win_rate_30d`、`high_premium_win_rate_30d`、`industry_edge_30d`。
- `mart_institution_industry_stat` 是同源展示表，填充完整。
- **grep 结果**：`signals_v2.py` 对上述表名与字段零引用。`scoring.py` 仅用 `industry_edge_30d` 作为机构层百分位归一化一项，不用于事件级决策。

**判定**：诉求一是"加工完成但决策链未接入"。修复成本低——signals_v2 只需新增一次查询并在硬规则里加一条"同机构同当前股票 L2 历史胜率低于阈值则 skip/降级"。

### 17.2 诉求二的现状核查：追高情形已回测过，但系统未分层使用

`Shared DB current state`

用真实数据验证用户假设：**同机构同行业里，低位入场 vs 追高入场的胜率、收益、回撤差异是否显著？**

实证 1：全部 new_entry + increase 事件按 `premium_bucket` 分层（29 678 样本）：

| premium_bucket | 样本数 | 60日胜率 | 60日平均收益 | 60日回撤 |
| --- | --- | --- | --- | --- |
| discount（溢价 -17.39%） | 15 791 | **52.2%** | **+4.87%** | -18.12% |
| near_cost（-0.40%） | 6 197 | 50.4% | +3.52% | -16.69% |
| premium（+9.47%） | 3 492 | 46.6% | +2.97% | -18.60% |
| high_premium（+34.39%） | 4 198 | **39.8%** | **+1.05%** | **-23.91%** |

从低到高单调劣化。低位入场比追高入场胜率高 12.4 个百分点、平均收益高 3.82 个百分点、回撤小 5.79 个百分点。

实证 2：限定"机构×L2 擅长行业"样本（`buy_event_count ≥ 5`，L2 层 941 个组合）：

| 统计维度 | low_premium 胜率 | high_premium 胜率 | 差值 |
| --- | --- | --- | --- |
| 机构×L2 层组合均值 | **52.7%** | **26.9%** | **25.8 pp** |

机构在其擅长的 L2 行业里，**低溢价事件胜率 52.7%**，**高溢价事件胜率只有 26.9%**——差距比全市场更大。用户的假设"追高就该降级"被数据完全佐证。

**系统当前怎么用这个事实**：

- `signals_v2` 的硬规则是 `premium_pct > 15% → skip`，单一硬阈值。
- 没有按机构分层：该机构在该 L2 行业历史高溢价胜率多少，当前事件是否例外，统统不问。
- 没有按行业分层：某个机构的消费板块高溢价事件可能胜率 40%，半导体板块可能 20%，硬规则一刀切。

**判定**：诉求二"数据已证但系统未用"。这是 §15 核心观点的又一个实例——指标加工完但决策路径不消费。

### 17.3 诉求三：Qlib 当前能力盘点

`Mainline current state`

已使用：

- **Alpha158**（技术面 158 因子）作为 base handler
- **自定义因子注入** `_inject_custom_factors_into_handler`，当前已注入：`inst_count_t0/t1/t2`、`inst_trend`、`inst_hold_ratio`、`inst_hold_ratio_change`、`inst_hold_market_cap`、`inst_hold_market_cap_change`——全部是**股票维度的机构持仓聚合量**，没有机构个体特征，没有机构×行业交互特征
- **LightGBM** 回归训练
- **Topk-Dropout** 策略回测（`qlib_backtest_result`，含 Sharpe/Calmar/MaxDD）
- **单任务 label**：`Ref($close, -2)/Ref($close, -1) - 1`（next-day 截面）

未使用但可用：

- **机构×L2 交互特征**（`research_inst_industry_performance` 已有数据）
- **事件级特征**（premium_bucket、event_type、notice_lag、inst_ref_cost）
- **阶段特征**（`fact_stock_stage_features` 已算出 dist_ma250_pct、return_1m/3m/6m、above_ma250，尚未注入 Qlib）
- **多任务**（目前单 label，可扩成 classification + regression 多头）
- **SHAP 可解释性**（LightGBM 原生支持，前端尚未接入）
- **Calibration**（Platt/Isotonic，LightGBM 输出转为可解释概率）
- **分群/层级模型**（按机构类型/行业分桶微调，qlib 不阻止）

### 17.4 设计方案：条件概率评级模型

设计目标：**让模型回答"给定这家机构在这个 L2 行业用这个溢价档位、此时介入，未来 20 日赚钱/回撤是多少、置信度多高"**，而不是"哪只股票在截面上排前 50"。

#### 17.4.1 样本单元与标签

**样本单元**：`(institution_id, stock_code, event_date)` 三元组。

- 每一条机构持仓披露事件都是一个训练样本
- 事件日 = `notice_date`（披露日，用户可见起点）
- 特征在事件日前（t-1）截断，避免 lookahead
- 标签在事件日后 20 交易日测量（`notice_date + 20d`）

**多任务标签**（三头并行）：

| 头 | 类型 | 定义 |
| --- | --- | --- |
| Head-A: `action_label` | 分类（3 类） | follow = 20d forward return ≥ +8% AND maxdd ≥ -8%；avoid = forward return ≤ -5% OR maxdd ≤ -15%；watch = 其他 |
| Head-B: `forward_ret_20d` | 回归 | 20日前复权收盘价收益率 |
| Head-C: `maxdd_20d` | 回归 | 20日最大回撤（负值） |

Head-A 对齐 §15.5 漏斗条件。Head-B/C 作为辅助标签提供连续信号，前端展示"期望赚多少、最多亏多少"。

**损失函数**：`total_loss = α·cross_entropy(A) + β·MSE(B) + γ·MSE(C)`，α=1, β=0.3, γ=0.3 作为起点（待验证）。

实施方式：训练 3 个独立 LightGBM 模型共享特征，而不是一个多头神经网（LGBM 上多任务更稳定、可解释更强）。

#### 17.4.2 特征族设计（9 族，约 250 特征）

| 特征族 | 来源表 | 举例 | 是否已有 |
| --- | --- | --- | --- |
| F1: 技术面（Alpha158） | Qlib 内置 | MA5/10/20、RSI、MACD、KDJ、RESI | ✅ |
| F2: 股票维度机构持仓 | `mart_current_relationship` 聚合 | inst_count_t0/t1、inst_trend、inst_hold_ratio_change | ✅ |
| **F3: 机构×L2 擅长度** | `research_inst_industry_performance` | `inst_l2_win_rate_30d/60d`、`inst_l2_avg_gain_30d/60d`、`inst_l2_drawdown`、`inst_l2_edge_30d`、`inst_l2_sample_count`、`inst_l2_low_vs_high_premium_gap` | ❌ **待注入** |
| **F4: 事件属性** | `fact_institution_event` | `premium_pct`、`premium_bucket_enc`、`event_type_enc`、`hold_ratio`、`hold_ratio_change`、`notice_age_days`、`report_to_notice_lag` | ❌ **待注入** |
| **F5: 介入时机（stage）** | `fact_stock_stage_features` | `dist_ma120_pct`、`dist_ma250_pct`、`above_ma250`、`return_1m/3m/6m/12m`、`max_drawdown_60d`、`volatility_20d`、`amount_ratio_20_120` | ❌ **待注入** |
| F6: 股票财务质量 | `fact_stock_quality_features` | `quality_score_v1`、子项 | 🟡 部分用 |
| F7: 机构层个体业绩 | `mart_institution_profile` | `buy_win_rate_60d`、`buy_avg_gain_60d`、`buy_median_max_drawdown_60d`、`exit_post_avg_gain_30d`、`safe_follow_win_rate_30d`、`concentration` | ❌ **待注入** |
| F8: 供给约束 | Phase A-G 归档，需恢复 | `ban_lift_days_30d`（解禁距离）、`major_holder_reduce_flag` | ❌ **从归档恢复** |
| F9: 预期/关注 | `fact_stock_forecast`、`mart_external_attention` | `forecast_type_enc`、`forecast_strength`、`research_visits_30d`、`analyst_upgrade_count` | 🟡 部分用 |

关键是 **F3/F4/F5/F7 四族**。F3 回答诉求一（机构擅长行业），F4+F5 回答诉求二（追高 + 介入时机），F7 把机构个体业绩从"展示墓地"接入决策。

#### 17.4.3 训练层级（三层渐进）

**层 1：全市场 baseline**
- 全部事件样本 → 一个 LGBM 多分类 + 两个 LGBM 回归
- 用途：整体 IC、特征重要性、回测基线
- 训练成本：低，每次全量 ≤10 分钟

**层 2：按机构类型分群微调**
- 按 `inst_type`（公募/私募/QFII/自营/险资）分 5 桶
- 每桶独立训练一个子模型（继承层 1 参数）
- 用途：识别不同机构类型的行为差异（公募偏左侧、游资偏右侧）
- 训练成本：中

**层 3：按 L2 行业分群（条件概率）**
- 56 个 L2 行业，每个行业独立训练子模型（样本少的行业合并到 L1 fallback）
- 推理时用"该股票当前所属 L2 的对应子模型"
- 用途：不同行业的均值回归强度/追高惩罚力度不同，分群更准
- 训练成本：高，但可并行；样本 < 100 的行业 fallback 到 L1

**集成**：推理时先层 3 子模型给预测，若该 L2 样本不足则回退层 2，再不足回退层 1。

#### 17.4.4 评级输出规范

每个样本产出：

```json
{
  "primary_action": "follow" | "watch" | "avoid",
  "confidence": 0.0 - 1.0,          // LGBM softmax max 概率，Isotonic 校准后
  "expected_return_20d": float,     // Head-B 预测
  "expected_maxdd_20d": float,      // Head-C 预测
  "shap_top3": [                    // 归因前 3 特征
    {"feature": "inst_l2_win_rate_60d", "value": 0.68, "contribution": +0.24},
    {"feature": "premium_bucket", "value": "high_premium", "contribution": -0.18},
    {"feature": "dist_ma250_pct", "value": 0.35, "contribution": +0.09}
  ],
  "used_model_layer": "L2_semiconductor" | "L1_fallback" | "baseline",
  "predict_date": "2026-04-23"
}
```

写入新表 `qlib_event_prediction`（事件级，区别于当前 `qlib_predictions` 的股票截面级）。

#### 17.4.5 业绩验证三层

**层 1：模型内部指标**
- Head-A：分类 accuracy、F1、ROC-AUC
- Head-B/C：IC、RankIC、MSE
- 目标：Head-A 在 follow 类的 precision ≥ 60%

**层 2：跟投回测（最关键）**
- 每日 follow 预测且 confidence ≥ 0.7 的股票，等权持仓 20 日
- 计算累计 IRR、MaxDD、Sharpe、胜率
- 基线对照：同期 legacy composite top-50、signals_v2 follow、沪深300
- 目标：新模型年化超越 legacy composite 且 MaxDD 改善

**层 3：条件分层验证（回应用户诉求二）**
- 按 premium_bucket 分层回测 follow 预测
- 按"机构在该 L2 是否擅长（样本≥10 AND win_rate≥50%）"分层回测
- 按"介入时机"（dist_ma250_pct 分位）分层回测
- 目标：追高样本的 follow 占比应显著低于低位样本；擅长行业的 follow 占比应显著高于非擅长行业

### 17.5 前端展示（落地诉求二的用户可见化）

事件详情页强制出现三类信号：

1. **追高警示**：若 `premium_bucket` ∈ {premium, high_premium}，主列表显示红色标签"追高 +X%"，悬停显示该机构该 L2 历史追高事件胜率。
2. **擅长度徽章**：若 `inst_l2_sample_count ≥ 10 AND inst_l2_win_rate_60d ≥ 55%`，显示金色"擅长"徽章；样本 < 10 显示灰色"陌生领域"。
3. **期望收益/回撤**：显示 `expected_return_20d` 和 `expected_maxdd_20d`，配合置信度条。

用户从事件详情页能直接看到：机构是否擅长这个行业、此时是不是追高、模型预期赚多少亏多少、为什么（SHAP top-3）。这四件事回答后，跟或不跟的判断才算"可复核"。

### 17.6 实施路线（替代 Phase 4）

Phase 4 原文只写了"20 日持有期 + -8% drawdown"的粗略定义；本节用 §17.4 的完整设计替换。

**第 1 阶段（14 天）：决策链路接入，不动模型**
- [ ] `signals_v2` 新增查询 `research_inst_industry_performance`；硬规则扩展：同机构该股票 L2 历史 `buy_event_count < 5` OR `win_rate_60d < 45%` → 降级 watch
- [ ] `signals_v2` 硬规则扩展：`premium_bucket = high_premium AND 机构该 L2 high_premium_win_rate_30d < 35%` → skip
- [ ] `signals_v2` 介入时机：事件日 `stage.dist_ma250_pct < -20%` OR `return_3m > +50%` → 降级 watch
- [ ] 输出诊断：每天记录 signals_v2 的跟/观察/回避分布，观察变化

**第 2 阶段（30 天）：Qlib baseline 搭建**
- [ ] 新 handler：继承 Alpha158，注入 F3/F4/F5/F7 四族特征（约 40 个新列）
- [ ] 新表 `qlib_event_prediction` schema 与写入脚本
- [ ] Head-A 分类器 + Head-B/C 回归器（层 1 全样本）
- [ ] 跟投回测脚本：按预测持仓 20 日，输出 IRR/MaxDD/Sharpe
- [ ] 与 legacy composite / signals_v2 的跟投回测对比

**第 3 阶段（60 天）：分群与生产化**
- [ ] 层 2（机构类型）与层 3（L2 行业）子模型训练
- [ ] SHAP 集成到 `qlib_event_prediction.shap_top3`
- [ ] 前端事件详情页追高警示 + 擅长度徽章 + 期望收益展示
- [ ] 每日增量训练/增量预测的 cron 调度
- [ ] 归档 Phase A-G 的 `supply_feat`（解禁、减持）选择性恢复进 F8

### 17.7 与已有工作的衔接

- Phase 1（RCA）：仍成立，`quality_score` 补跑是基础，但 §17 后，`quality_score` 的用途从"加权进 composite"转为"F7 特征之一"进 Qlib
- Phase 2（CLI）：保留
- Phase 3（主从决策书）：`primary_action` 不由加权分产生，由 §17.4.4 Head-A 产生；"硬门槛"被"模型 + confidence ≥ 0.7"替代
- Phase 4（原 Qlib 对齐）：被 §17.4 完整替换
- §15（漏斗）：漏斗仍在，但不是独立于 Qlib 的硬规则——漏斗条件同时作为 Qlib 特征（F3/F4/F5），让模型自己学会"通过漏斗 → follow"的联合分布，而不是人工写死
- §16（仿真）：证明了机构评分有区分力，为 F7 特征族的正当性兜底

### 17.8 一句话总结

Qlib 不是要当"股票打分器"，要当"事件条件概率引擎"——给定(机构, 股票, 时点, 溢价, 擅长度, 介入时机)，回答"跟这一笔事件 20 日能赚多少、亏多少、有多确信"。数据和加工的 90% 已就绪，真正缺的是**把 F3/F4/F5 特征注入 Qlib Handler** 和**多任务训练框架**，外加**跟投回测**做生死验证。§17 的实施能让系统第一次出现"机构是主角、追高被惩罚、介入时机被看见"的统一评级，而不是三条互相打架的 gate 链。

---

## 18. 基于 Qlib 的策略参数自动寻优设计（2026-04-23）

这一节回答用户问题：Qlib 能否根据数据自动寻优，针对机构画像和股票画像探索出某一股票最佳介入时机、持仓时间、建仓节奏、退出时机？

### 18.1 把用户的问题拆成可计算的形式

"最佳介入时机 / 持仓时间 / 建仓节奏 / 退出时机"在工程上等价于：**在一个参数化的交易策略空间里，找到使目标函数（年化收益、回撤、Sharpe）最优的参数组合，并按机构画像 × 股票画像分条件求解**。这是**多目标、条件式、约束下的超参数优化（HPO）问题**，不是模型层 HPO。

### 18.2 Qlib 原生支持状况（核查结果）

`Mainline current state` + pyqlib 0.9.x 官方文档

| Qlib 能力 | 是否可用 | 限制 |
| --- | --- | --- |
| `qlib.rl` RL 模块 | ❌ 场景不对 | 原生为**单资产订单执行**（拆单防冲击），不是策略级 RL。学"何时进 / 持多久 / 何时退"要自己写 env + policy，训练不稳定 |
| `qlib.backtest.BaseStrategy` 可插拔 | ✅ 可继承 | 当前项目 `qlib_full_engine.py:1385-1418` **写死 TopkDropoutStrategy**，仅开放 `topk / n_drop`；要自定义持仓期/止损需写新子类 |
| 原生策略参数 HPO | ❌ 没有 | yaml 手动配；必须外挂 Optuna / Ray Tune |
| `RollingGen` / `DDG-DA` | 🟡 部分可用 | 按**时间窗**滚动训练，**不是**按 cohort 分群寻优。cohort-wise 要自写外层循环 |
| 自定义因子注入（Alpha158 扩展） | ✅ 已用 | `_inject_custom_factors_into_handler`，§17 已依赖此能力 |

本地现状：

- `backend/` grep `optuna|ray.tune|hyperopt|skopt|bayes_opt` → **零命中**。无 HPO 依赖。
- `backend/scripts/run_backtest.py` 跑的是"机构事件研究"生成 `research_*` 表，不是策略参数扫描。
- `fact_institution_event` **无** `exit_date` / `hold_days` 字段；实际退出时点在 `research_holding_chains`：15 009 条链，但 `chain_status=closed` 只有 **1 131 条**（13 878 条 open 仍持仓中），`alpha_halflife_days` 字段存在但全空。

**判定**：可以做，不免费。需要三样东西：(1) 自定义参数化策略类，(2) 外挂 Optuna HPO，(3) 按 cohort 分群寻优器。外加一次数据层补齐（`chain_days` / `exit_date` 回填或用 open chain 的 proxy）。

### 18.3 策略参数空间

`Session reconstruction`

每一个机构事件按以下参数执行跟投：

```
策略 = {
  entry_lag_days: 0|1|2|3|5,              # 披露日 D 起多少个交易日后开始买
  entry_pacing:   single|twap3|twap5|dip, # 一次性 / 3日等权 / 5日等权 / 跌时加仓
  max_hold_days:  5|10|15|20|30|45|60,    # 最长持仓
  stop_loss:      -5%|-8%|-10%|-12%|-15%|none,
  take_profit:    +10%|+15%|+20%|+30%|none,
  trailing_stop:  3%|5%|8%|none,
  exit_trigger:   fixed_days|inst_exit|ma20_break|signal_reverse,
}
```

全组合约 5 × 4 × 7 × 6 × 5 × 4 × 4 ≈ **67 200 种**。Grid Search 全扫不现实，用 Bayesian TPE（Optuna）200–500 次 trial 可收敛到 Pareto 前沿。

### 18.4 条件分群策略

全市场一个平均参数没意义（半导体游资和医药公募策略差异巨大）。分群方案按粗到细：

| 方案 | 粒度 | cohort 数 | 可行性 |
| --- | --- | --- | --- |
| A. 全市场一个策略 | 1 | 1 | 基线，无区分 |
| B. (inst_type) | 5 | 5 | 太粗 |
| **C. (inst_type × L2)** | 5 × 56 | 约 280 | 推荐起点 |
| D. (inst_type × L2 × premium_bucket) | 5 × 56 × 4 | 约 1 120 | 过细，样本稀释 |
| E. KMeans(机构画像, K=5) × L2 | 5 × 56 | 约 280 | 机构画像降维后更稳健 |

**最小样本约束**：每 cohort 事件数 ≥ 参数维度 × 20（当前 7 维 → 140 事件）。低于阈值自动回退到父 cohort（L2 → L1 → 全市场）。

当前已有数据估算：机构事件 ~57 000 条，按 C 方案分 280 cohort，平均每 cohort ~200 条，**刚好够寻优但非常紧**。首版应该从 Top 20 个大样本 cohort 开始验证，而不是全 280 个同时上线。

### 18.5 寻优引擎三层结构

```
for cohort in cohorts_with_enough_samples:              # 外层：cohort 循环
    train_events, holdout_events = walk_forward_split(cohort.events, ratio=0.7)

    def objective(trial):                                # 中层：Optuna TPE
        params = {
            "entry_lag":     trial.suggest_int("lag", 0, 5),
            "entry_pacing":  trial.suggest_categorical("pacing", ["single","twap3","twap5","dip"]),
            "max_hold_days": trial.suggest_int("hold", 5, 60, step=5),
            "stop_loss":     trial.suggest_categorical("sl",[-0.05,-0.08,-0.10,-0.12,-0.15,None]),
            # ... 其余参数
        }
        result = simulate_events(train_events, params)   # 内层：事件级回测
        return result.annual_return, -result.max_drawdown  # 多目标

    study = optuna.create_study(directions=["maximize","maximize"], sampler=TPESampler())
    study.optimize(objective, n_trials=300)

    best = select_from_pareto(study.best_trials, by="sharpe")
    holdout_metrics = simulate_events(holdout_events, best)  # walk-forward 验证
    save_to_fact_cohort_optimal_strategy(cohort, best, holdout_metrics)
```

内层 `simulate_events` 就是参数化策略执行器：给一组事件和一组参数，按参数买入/持有/卖出，返回 PnL 曲线 / 年化收益 / MaxDD / Sharpe。

### 18.6 过拟合防护（这是难点，不是寻优本身）

样本稀疏 + 参数空间大 + 非 i.i.d. 金融时序 = 过拟合是几乎必然的。四层防护：

1. **Walk-forward split**：cohort 事件按时间排序，前 70% 寻优，后 30% 验证。仅报告 **holdout Sharpe**，而非 train Sharpe。
2. **最小样本硬约束**：cohort 事件数 < 7 维 × 20 = 140 时跳过，回退父 cohort。
3. **Top-K 稳健平均**：不选 Pareto 最优单点，选 Top-10 trials 参数的 **众数 / 中位数**。单点最优通常是噪声。
4. **稳健性分数**：把 train 期分 4 段，计算每段 Sharpe 的方差。方差大 → 稳健性低 → 该 cohort 不纳入生产。

### 18.7 数据层准备（§18 的前置工作）

| 需求 | 现状 | 补齐方式 |
| --- | --- | --- |
| 实际持仓天数 | `research_holding_chains.chain_days` 字段存在但**全空**（15 009 条 0 填充率） | 补一个 SQL UPDATE 用 `julianday(chain_end_date) - julianday(chain_start_date)` 回填 |
| 实际退出价 / 日期 | `chain_end_date` 有，`exit_price` 无 | 关联 `price_kline` 取对应日收盘价 |
| alpha 半衰期 | `alpha_halflife_days` 字段存在但**全空** | 现有事件级 `gain_*d` 序列可拟合指数衰减得到，需补一段计算脚本 |
| Open chain 的退出 proxy | `research_holding_chains` 13 878 条 open | 用 `follow_gain_60d` 作为"假设持 60d"的模拟退出收益；寻优时标注"proxy" |

没有这一步，寻优引擎跑起来后得到的"最佳持仓期"会被统计到"绝大多数 chain 还 open，数据截尾偏向短持仓" —— 得到假的最优。

### 18.8 落表 `fact_cohort_optimal_strategy`

```sql
CREATE TABLE fact_cohort_optimal_strategy (
  cohort_key          TEXT,     -- e.g. "mutual_fund|L2_medical|discount"
  parent_cohort_key   TEXT,     -- 样本不足时回退到哪个父 cohort
  optimization_date   TEXT,
  -- 策略参数
  entry_lag_days      INTEGER,
  entry_pacing        TEXT,
  max_hold_days       INTEGER,
  stop_loss           REAL,
  take_profit         REAL,
  trailing_stop       REAL,
  exit_trigger        TEXT,
  -- 业绩指标
  n_events_train      INTEGER,
  n_events_holdout    INTEGER,
  train_annual_return REAL,
  holdout_annual_return REAL,
  holdout_max_drawdown  REAL,
  holdout_sharpe      REAL,
  holdout_win_rate    REAL,
  robustness_score    REAL,     -- 稳健性（4 段时间 Sharpe 方差的倒数）
  uses_proxy_exit     INTEGER,  -- 是否用 open chain 的 proxy 数据
  top_k_config_json   TEXT,     -- Top-10 近优配置，前端可展示备选
  PRIMARY KEY (cohort_key, optimization_date)
);
```

### 18.9 与 §17 Qlib 建模的分工

- **§17 回答"跟不跟"**：事件分类器 + 期望收益/回撤预测（给出 primary_action 和 confidence）
- **§18 回答"怎么跟"**：给 follow 的事件，查其所属 cohort 的最优策略参数（entry_lag、hold_days、stop_loss 等）

完整 follow 指令的生成链路：

```
新事件
  → §17 Qlib 评级 → primary_action=follow, confidence=0.78, shap_top3=...
  → 查 fact_cohort_optimal_strategy[(inst_type, L2, premium)]
    → entry_lag=2, pacing=twap3, max_hold=20, stop_loss=-8%, take_profit=+15%
  → 前端展示："跟 / 置信 78% / D+2 起 3 日分批 / 持至 -8% 止损或 +15% 止盈或第 20 日"
```

用户看到的不再是一个 gate 字符串，而是**完整可执行策略**，且每个参数都能追溯到"这是由该类事件历史 200 条样本 + 30 条 holdout 验证得出的最优"。

### 18.10 为什么不用 RL

Qlib 的 RL 模块是订单执行级，不学策略。要用 RL 学"何时进 / 持多久 / 何时退"需要：
- 自定义 environment：state = 当前持仓 + 市场 + 事件特征，action = 买/持/卖，reward = PnL - λ·drawdown
- PPO 或 DQN 训练
- 样本量大（每条事件只产生一个 trajectory，57 000 条对 RL 不够）
- 解释性差（"为什么这时候卖"答不上来）

**判定**：第一版用 Optuna HPO + 可解释参数化策略，不上 RL。RL 可留给 60 天后当"Top-N cohort 已稳定"时的优化手段。

### 18.11 实施路线

**第 1 阶段（14 天）：单 cohort 可行性验证**
- [ ] 补齐 `chain_days` / `exit_price` / `alpha_halflife_days`（SQL + Python 脚本）
- [ ] 选样本最多的 cohort（例如"公募 × L2_医药 × discount 溢价"，预估 ~800 事件）
- [ ] 写参数化策略执行器 `simulate_events(events, params)`（一人日）
- [ ] 外挂 Optuna，跑 200 trial，得到该 cohort 的最优参数 + holdout Sharpe
- [ ] 对比固定基线（max_hold=20d, stop_loss=-10%），看是否显著超越
- [ ] 验收标准：holdout Sharpe ≥ baseline + 0.3 AND MaxDD ≤ baseline

**第 2 阶段（30 天）：Top-20 cohort 全扫**
- [ ] 按 §18.4 方案 C 分 280 cohort，筛选样本 ≥ 140 的 Top 20
- [ ] 并行跑 20 个 Optuna study（每个 300 trial）
- [ ] 写入 `fact_cohort_optimal_strategy`
- [ ] 稳健性分数 < 阈值 的 cohort 标记 `is_production = 0`

**第 3 阶段（60 天）：生产化 + 前端接入**
- [ ] `/api/signals` 查询返回时联查 `fact_cohort_optimal_strategy`
- [ ] 前端事件详情页展示"推荐策略参数"+"备选 Top-3"
- [ ] Weekly cron：每周重跑寻优，监控参数漂移（漂移大说明过拟合或市场结构变化）
- [ ] 扩展到全 280 个 cohort + L1 / 全市场三级回退

### 18.12 关键风险

1. **样本不足**：57 000 事件按 280 cohort 分，最小 cohort ≤ 30 事件。**必须回退到父级**，接受 cohort 粒度粗化。不能让过拟合参数进生产。
2. **Open chain 比例高**（93% 未平仓）：真实退出数据只有 1 131 条。短期必须用 `follow_gain_60d` 作 proxy，长期等 chain 自然平仓积累。
3. **市场结构变化**：A 股每 2-3 年一轮结构切换，寻优结果可能在新周期完全失效。缓解：walk-forward 分 4 段看稳健性；每季度强制重跑；保留多个历史版本做对比。
4. **多目标解选择**：Pareto 前沿上选哪个点是业务决策（收益 vs 回撤权衡），不能自动化。第一版默认"Sharpe 最高"，保留配置项让业务方覆盖。

### 18.13 §15 / §17 / §18 的完整拼图

| 层 | 回答什么 | 工具 | 何时落地 |
| --- | --- | --- | --- |
| §15 漏斗（机构初筛） | 这家机构值得跟吗 | SQL 硬规则（AND 逻辑） | 14 天 |
| §17 Qlib 评级 | 这笔事件值得跟吗、收益多少、置信度多少 | LGBM 多任务 + SHAP | 30-60 天 |
| **§18 策略寻优** | **一旦跟了，具体参数怎么设** | **Optuna HPO + 参数化策略** | **14-60 天** |
| §16 综合分接入仿真 | 机构分进 composite 影响多大 | 只读仿真 | 已完成 |

§18 是最后一块拼图：不仅告诉用户"跟谁跟什么"，还告诉用户"怎么跟"。

### 18.14 一句话总结

Qlib 原生不做业务策略参数寻优，但它的 Strategy 插拔点 + 自定义因子 + 回测引擎三件组件，加上外挂的 Optuna TPE + 按 cohort 分层结构，**可以**自动探索"某类机构在某类行业某种溢价下，介入时机 / 持仓期 / 止损止盈 / 退出触发"的最优组合。真正的风险不是 Qlib 的能力边界，而是**样本稀疏 + 过拟合**——必须用 walk-forward、Top-K 稳健平均、最小样本硬约束、cohort 回退树四层防护兜底。第 14 天交付单 cohort 可行性验证；第 30 天交付 Top-20 cohort 寻优表；第 60 天交付完整链路上线并每周自动迭代。

---

## 19. 三个工程决策的性价比评估（2026-04-23）

这一节回答三个具体工程问题的性价比：
1. 引入 Optuna 到底值不值？
2. 解除 TopkDropout 写死状态要花多少？
3. 过拟合用什么指标监控（KS 曲线之类）？

### 19.1 Optuna 性价比

#### 19.1.1 买什么

| 能力 | 说明 | 对本项目价值 |
| --- | --- | --- |
| TPE 采样器 | 贝叶斯优化，比 Random 收敛快 5-10x | ⭐ 67 200 组合参数空间必需 |
| 多目标优化（MOTPE / NSGA-II） | 原生支持 Pareto 前沿 | ⭐ 年化收益 + MaxDD 双目标必需 |
| Pruner（早停） | 无望 trial 提前终止 | 节省 30-50% 回测成本 |
| Study 持久化（SQLite backend） | 中断后续跑 | HPO 一跑几小时，必需 |
| optuna-dashboard 可视化 | Pareto 前沿/参数重要性 | 调试方便 |

#### 19.1.2 不买什么

- 小样本精度保证（< 50 trial 时 TPE 和 Random 差不多）
- 全局最优（TPE 是贪心近似，容易陷局部）

#### 19.1.3 成本

- 依赖：`pip install optuna`（纯 Python，无 C 扩展，安装秒级）
- 学习：核心 API 2 小时上手（study / trial / suggest_* / optimize）
- 集成代码：~50 行 `objective(trial)` + study 配置
- 维护：改参数空间改 objective 即可，无侵入

#### 19.1.4 替代方案对比

| 方案 | 集成代码 | 多目标 | 并行 | 持久化 | 本项目适配 |
| --- | --- | --- | --- | --- | --- |
| Grid Search | ~20 行 | 手工 | 手工 | 无 | ❌ 67 200 × 10秒 ≈ 186 小时/cohort |
| Random Search | ~30 行 | 手工 | 易 | 无 | 🟡 200 trial 可行但次优 |
| **Optuna TPE** | **~50 行** | ✅ 原生 | ✅ 易 | ✅ SQLite | ✅ **推荐** |
| Ray Tune | ~80 行 | ✅ | ✅ 集群 | ✅ | ⚠️ 更重，单机部署不值得 |
| scikit-optimize | ~40 行 | 弱 | 弱 | 弱 | ❌ 2022 后社区维护弱 |
| Hyperopt | ~40 行 | 弱 | 中 | 弱 | ❌ 社区转向 Optuna |

#### 19.1.5 结论

**买 Optuna，性价比明确**。不买的话 §18 寻优基本无法实施。唯一的替代是 Random Search，但多目标和持久化都要手写，最终代码量接近 Optuna 但收敛慢一倍。

### 19.2 解除 TopkDropout 写死的性价比

#### 19.2.1 写死现状

`backend/services/qlib_full_engine.py:1385-1418`：

```python
def _backtest_strategy_label(params):
    topk = int((params or {}).get("backtest_topk", 50) or 50)
    n_drop = int((params or {}).get("backtest_n_drop", 5) or 5)
    return f"TopkDropoutStrategy(topk={topk},n_drop={n_drop})"

def _build_backtest_config(params, benchmark_code):
    backtest_config = {
        "strategy": {
            "class": "TopkDropoutStrategy",                    # 硬编码
            "module_path": "qlib.contrib.strategy",            # 硬编码
            "kwargs": {"signal": "<PRED>",
                       "topk": int(params.get("backtest_topk", 50)),
                       "n_drop": int(params.get("backtest_n_drop", 5))},
        },
        ...
    }
```

只开放 `backtest_topk` / `backtest_n_drop` 两个参数。

#### 19.2.2 解除路径三档

| 档次 | 工作内容 | 工作量 | 换来什么 |
| --- | --- | --- | --- |
| A. 参数化 class + module_path + kwargs | 改 `_build_backtest_config` ~10 行，加 strategy 类型切换 | **1 人日** | 能切换到其他 Qlib 内置策略（EnhancedIndexing、SoftTopk 等） |
| B. 策略注册表 + 自定义 Qlib Strategy 类 | 写 `InstitutionEventStrategy(BaseStrategy)`，继承 Qlib backtest | 2-3 人日 | 事件驱动策略走 Qlib backtest pipeline |
| C. **跳出 Qlib backtest，自写 pandas 事件仿真器** | 写独立仿真器 `simulate_events(events, params)` | **2-3 人日** | 事件驱动仿真贴业务语义；Qlib backtest 保留给传统 topk 策略 |

#### 19.2.3 关键判断：档 C 优于 B

Qlib Backtest 框架是为**"每日 rebalance 持仓组合"**设计的：

- 输入：每日股票 signal panel（每天每只股票一个打分）
- 策略：按分排序，持仓 topk 股票，超出持仓下落到 n_drop 档外的卖出换新的
- 回测：按每日收盘价计算账户净值

本项目 §18 要的是**"按事件入场 + 持有 20 天 + 参数化止损止盈"**：

- 输入：事件流（机构披露日 + 对应股票 + 机构画像）
- 策略：D 日披露 → D+lag 日按 pacing 买入 → 最长持 max_hold 天 → 期间触发 stop_loss/take_profit 则退出
- 回测：事件级 PnL，而不是每日账户净值

这两种范式**在 Qlib Backtest 里强行兼容会非常累**——要把事件流转换成 daily signal panel，再用 Strategy 把 signal 翻译回"按事件持有 20 天"，来回两次翻译增加 bug 面。

**直接自写 pandas 事件仿真器**反而更轻：

```python
def simulate_events(events_df, params):
    positions = []
    for event in events_df.itertuples():
        entry_date = get_trading_day_offset(event.notice_date, params['entry_lag'])
        entry_price = get_close_price(event.stock_code, entry_date)
        # 遍历 max_hold 天，每天检查 stop_loss / take_profit / trailing_stop
        exit_date, exit_price, exit_reason = run_holding_logic(event, params)
        pnl = (exit_price / entry_price - 1)
        positions.append({...})
    return aggregate_metrics(positions)  # IRR / MaxDD / Sharpe / win_rate
```

~150 行代码，3 人日可完成。Qlib Backtest 保留原样，服务于"传统 topk 选股策略"的回测。

#### 19.2.4 结论

- **不做不是选项**：不解除等于 §18 无法实施
- **推荐档 C**：自写事件仿真器，2-3 人日，直接对接 Optuna，性价比最高
- **档 A 是附加项**：可以顺手做（1 人日），让 Qlib backtest 能切换其他策略，但不是 §18 依赖路径

### 19.3 过拟合监控：模型性能评估体系

#### 19.3.1 当前状况

已有：`qlib_model_state` 表的 `ic_mean` / `rank_ic_mean`，`qlib_backtest_result` 表的 `sharpe_ratio` / `calmar_ratio` / `max_drawdown` / `annual_return` / `turnover`。

缺失：**KS、AUC、AUC-PR、Calibration curve、PSI、Lift curve** —— 这些是风控和模型治理的标准组合。

#### 19.3.2 分层评估体系

**Layer 1：模型层（§17 Qlib 评级器）**

| 指标 | 作用 | 本项目价值 | 成本 |
| --- | --- | --- | --- |
| IC / RankIC / ICIR | 预测 vs 实际收益相关性 | ✅ 已有 | 0 |
| **KS statistic** | 二分类区分力（follow vs 非 follow 的累积分布最大差） | ⭐ **必加**（风控标准，KS ≥ 0.3 算好模型） | scipy.stats.ks_2samp，~10 行 |
| **AUC-ROC** | 二分类排序能力 | ⭐ **必加**（目标 ≥ 0.7） | sklearn.metrics，~5 行 |
| AUC-PR | 类别不平衡时更稳 | 建议（follow 只占 ~8%，PR 比 ROC 更能反映真实 precision） | ~5 行 |
| **Calibration curve** | 置信度是否真的可信 | ⭐ **必加**（§17 confidence 正当性的唯一验证手段） | sklearn.calibration + isotonic，~30 行 |
| Lift curve | 高置信 follow 的 business lift | 建议（给业务方看直观） | ~30 行 |
| Brier score | 概率预测的校准 + 准确性综合 | 可选 | ~5 行 |

**Layer 2：策略层（§18 寻优结果）**

| 指标 | 作用 | 成本 |
| --- | --- | --- |
| 分层 Sharpe（按预测分 decile） | top 层 Sharpe 应该显著高于中层 | 复用回测结果 |
| 换手率（turnover） | 策略是否过度交易 | 已有 |
| 稳健性分数（4 段时间 Sharpe 方差） | 防过拟合主要手段 | §18.6 已设计 |
| Pareto 前沿可视化 | 给业务方看多解备选 | optuna-dashboard 免费 |

**Layer 3：生产监控**

| 指标 | 作用 | 触发动作 | 何时加 |
| --- | --- | --- | --- |
| **PSI（Population Stability Index）** | 生产特征分布 vs 训练是否漂移 | PSI > 0.2 → 重训 | 60 天后生产化时 |
| Prediction drift（KS between train/prod predictions） | 预测分布是否偏移 | drift > 阈值 → 报警 | 60 天后 |
| SHAP top-3 稳定性 | 主驱动因子是否变化 | 变化率 > 30% → 人工介入 | 60 天后 |
| Data completeness | 特征缺失率 | 覆盖率 < 95% → 暂停预测 | 30 天后 |

#### 19.3.3 落地优先级与成本

| 时间 | 动作 | 成本 |
| --- | --- | --- |
| Phase 17 baseline 出来时（14 天内） | KS + AUC-ROC + AUC-PR 三件套 | ~50 行代码 + 1 人日 |
| Phase 17 分群微调阶段（30 天） | Calibration + Lift + 分层 Sharpe | ~100 行 + 2 人日 |
| Phase 18 寻优输出时（30 天） | 稳健性分数 + Pareto 前沿可视化 | ~50 行（复用 optuna 的） |
| 生产化阶段（60 天） | PSI + prediction drift + SHAP 稳定性 | ~150 行 + 2 人日 |

#### 19.3.4 新表 `qlib_model_evaluation`

```sql
CREATE TABLE qlib_model_evaluation (
  model_id           TEXT,
  eval_date          TEXT,
  eval_dataset       TEXT,     -- 'train' | 'valid' | 'holdout'
  ks_statistic       REAL,
  ks_pvalue          REAL,
  auc_roc            REAL,
  auc_pr             REAL,
  calibration_ece    REAL,     -- Expected Calibration Error
  brier_score        REAL,
  lift_top_decile    REAL,
  sharpe_top_decile  REAL,
  psi_score          REAL,     -- 生产监控阶段填
  notes              TEXT,
  created_at         TEXT,
  PRIMARY KEY (model_id, eval_date, eval_dataset)
);
```

单一落表，避免 metric 散落在多处。前端可做一个"模型健康面板"消费这张表。

#### 19.3.5 为什么 KS 特别重要

本项目在用户侧展示的是 `primary_action = follow / watch / avoid`，这本质是**二分类（或三分类）+ 概率**。KS 给出一个极简的"这个模型能不能区分好样本和坏样本"的回答：

- KS = 0.1 → 几乎无区分力（模型不用上）
- KS = 0.2-0.3 → 弱区分（探索期勉强）
- KS = 0.3-0.4 → 可用
- KS ≥ 0.4 → 好模型

KS < 0.3 说明不管 Sharpe 多高都是运气，必须推翻重来。这是一个**止损线**式的指标，比 IC 或 Sharpe 更适合做"模型能不能上线"的门槛判定。

### 19.4 三个决策综合

| 决策 | 买 / 不买 | 成本 | 不买的代价 |
| --- | --- | --- | --- |
| Optuna 引入 | ✅ 买 | 1 依赖 + 2 人日 | §18 寻优不能做 |
| TopkDropout 解除（档 C：自写事件仿真器） | ✅ 买 | 2-3 人日 | §18 无法启动，§17 评级结果无处落地 |
| KS / AUC / Calibration 三件套 | ✅ 买 | 1 人日起（持续扩展） | 过拟合靠裸眼判断，§17 / §18 上线依据不足 |

三件合计 5-8 人日，换来 §17 + §18 + 过拟合监控体系成型。这是系统从"研究原型"进入"生产评级"的最小工程投入。

### 19.5 与前几节的衔接更新

- §17 实施路线第 30 天"Qlib baseline 出来"时，必须同步交付 KS + AUC + Calibration（§19.3.3 一项）
- §18 第 14 天单 cohort 验证时，必须同步用 Optuna（§19.1）+ 事件仿真器（§19.2 档 C）—— 不能先跑一个"简化 Grid Search 版本"，那是技术债
- §18 第 30 天 Top-20 cohort 寻优时，必须同步交付 Pareto 前沿可视化 + 稳健性分数落表 `fact_cohort_optimal_strategy.robustness_score`
- §18 第 60 天生产化时，同步上 PSI + prediction drift 监控

### 19.6 一句话总结

Optuna、事件仿真器（替代档 A/B 的 Qlib Strategy 解耦）、KS+AUC+Calibration 评估三件套——**总成本 5-8 人日**，这是 §17 + §18 能落地的**最小工程门槛**。不做 Optuna 则 HPO 不可行；不自写事件仿真器则 §18 无法对接业务；不加 KS/Calibration 则模型上线没止损线，过拟合靠猜。三件必须一起上，缺一件拖累另外两件。

---

## 20. 对 §15-§19 的独立评估与修正（2026-04-23）

这一节对本文档 §15-§19（连同我上一轮自我纠偏）做**第二轮实地核查**。动机：§14 挑战过 §1-§13；§15-§19 是我写的，**没人挑战过**，需要自己打自己。全部结论基于实际数据，不是推演。

### 20.1 七个需要重新检视的主张

| # | 主张 | 位置 | 核查结果 |
| --- | --- | --- | --- |
| 1 | 漏斗 6 条 AND 可选出 20-40 家机构 | §15.5 | ❌ **实测只过 1 家** |
| 2 | 57000 事件分 280 cohort，平均 ~200/cohort | §18.4 | ❌ **实测 473 cohort，平均仅 63 事件；排北向后 417 cohort，平均 40** |
| 3 | chain_days 字段存在但全空，SQL UPDATE 可回填 | §18.7 | 🟡 **全空属实，但 closed chain 只有 1131/15009（7.5%），open chain 没 end_date 无法回填** |
| 4 | research_inst_industry_performance 数据完备 | §17.1 | 🟡 **存在但无 `updated_at` 字段，数据新鲜度无法追踪** |
| 5 | Head-A 标签三类分布可能严重不平衡 | §17 隐含担忧 | ✅ **实测 32.1% follow / 38.2% avoid / 29.7% watch，分布可用** |
| 6 | KS ≥ 0.3 是模型上线止损线 | §19.3 | 🟡 **信贷风控基准，不对齐金融时序典型 KS 0.10-0.20** |
| 7 | §17 与 §18 可并行 14 天内各自完成 POC | §17.6 + §18.11 | ❌ **§18 依赖 §17 的 follow 判定，不能并行** |

### 20.2 致命问题：§15.5 漏斗设计被数据证伪

`Shared DB current state`

漏斗逐条命中：

| 条件 | 通过机构数 |
| --- | --- |
| buy_event_count ≥ 30 | 95 / 231 |
| buy_win_rate_60d ≥ 55% | 210 / 231 |
| buy_avg_gain_60d ≥ 5% | 82 / 231 |
| buy_median_max_drawdown_60d ≥ -15% | 221 / 231 |
| \|exit_post_avg_gain_30d\| ≤ 3% | 88 / 231 |
| concentration ≥ 40% | 90 / 231 |
| **六条 AND 同时满足** | **1 / 231** ⚠ |

两个苛刻的条件：

- `|exit_post_avg_gain_30d| ≤ 3%`：实际分布均值 1.39，绝对值均值 4.64，多数机构都超 3。我写这个阈值时凭直觉觉得"3% 内算准"，但退出时机本身噪声大，阈值过严。
- `concentration ≥ 40%`：均值 48.1，看起来能过，但和其他五条叠加后大部分机构被多重条件同时否决。

**敏感性扫描**：

| 放宽方案 | 通过机构数 |
| --- | --- |
| 原版 6 条 AND | 1 |
| 去掉 concentration + exit，剩 4 条 AND | 33 |
| 核心 3 条 AND（样本 + 胜率 + 收益） | 33 |
| 放宽到 win_rate ≥ 50%, gain ≥ 3% | 45 |
| 仅样本 ≥ 30 | 95 |

**修正方案**：

- **主漏斗**用 3 条核心 AND：`buy_event_count ≥ 30 AND buy_win_rate_60d ≥ 55% AND buy_avg_gain_60d ≥ 5%`，过 33 家。
- `concentration` 和 `|exit_post_avg_gain_30d|` 退为 **警示标签**，不是淘汰条件。UI 上显示"行业漂移"、"退出偏差"徽章即可。
- `buy_median_max_drawdown_60d ≥ -15%`：221 家都过，约束不起作用，**删除该条**，用更严的 `-12%` 或改为评分分档。
- 最终阈值仍需靠"跟投回测"反推（§18 的 `fact_cohort_optimal_strategy` 跑出来后，按机构分层看跟投净值，确定哪种筛选收益最好）。

### 20.3 致命问题：§18 cohort 数与平均样本严重误估

`Shared DB current state`

原估（§18.4）：按 (inst_type × L2) 约 280 cohort，平均 ~200 事件/cohort，"紧但够用"。

实测：

| 分桶方案 | cohort 数 | ≥ 140 样本数 | 总事件 | 平均 |
| --- | --- | --- | --- | --- |
| inst_type × L2（含北向） | **473** | 57（12%） | 29 685 | 63 |
| inst_type × L2（排北向） | 417 | **28**（6.7%） | 16 488 | **40** |
| inst_type × L1（排北向） | **108** | 未查 | 16 488 | 153 |

关键事实：

1. 总 cohort 473 是估算的 1.7 倍；平均 63，是估算的 31%
2. **排除北向后样本几乎腰斩**（29 685 → 16 488），因为北向机构贡献了 44.5% 的 buy/increase 事件；但按 §14.1 北向已退役，不应纳入训练样本
3. 能上寻优生产的 cohort（≥ 140 样本）**只有 28 个**，不是估算的 57 个或"Top 20"

**修正方案**：

- **不按 L2 分群起步**。L2 粒度（56 行业 × 多类机构）样本密度不够。
- 改为 **L1 × inst_type_group**：
  - L1 取 Tongdaxin 一级 13 个行业
  - inst_type 合并成 3 类：稳健型（公募 + 险资 + QFII）、交易型（游资 + 私募）、其他（自营 + 信托）
  - 得 13 × 3 = 39 cohort，平均样本 ~420
  - `≥ 140` 的 cohort 可用数估计 ~25-30 个（远比 L2 的 28 个更均匀）
- **北向数据作为历史训练样本可用，但不作为"推理目标"**：把它纳入 cohort 样本库增加泛化，但推理时不对北向机构给建议。
- §17 分层训练同样需要改：从"L2 行业分群"改为"L1 行业分群"。

### 20.4 chain_days 全空 + 93% chain 未平仓：§18 数据层风险

`Shared DB current state`

- 15 009 条 chain 里，`chain_days` 字段 100% 空
- `chain_status = open` 13 878 条（92.5%），`closed` 只有 1 131 条（7.5%）
- `alpha_halflife_days` 字段存在但 100% 空

**§18 寻优的"真实持仓天数"数据严重不足**。§18.7 说"SQL UPDATE 回填 `julianday(end - start)`"——只能回填 closed 的 1 131 条，不够做 cohort × 参数空间的寻优。

**修正方案**：

1. **分阶段回填**：
   - Step A：SQL UPDATE closed chain 的 `chain_days`（可得 1131 条）
   - Step B：写 `alpha_halflife_days` 计算脚本（从事件级 `gain_*d` 拟合指数衰减）
   - Step C：open chain 用 `follow_gain_60d` 作 proxy，落到 `proxy_hold_days=60, proxy_gain=follow_gain_60d`；新增列 `uses_proxy` 标记
2. **§18 POC 接受 proxy**：第 14 天单 cohort 验证用 proxy 数据，第 60 天生产化前 closed chain 样本积累到一定量再切换
3. **UI 诚实标注**：`fact_cohort_optimal_strategy.uses_proxy_exit = 1` 的 cohort 在前端标"基于假设 60 天持仓回测"

### 20.5 §17 vs §18 耦合：不能并行 14 天

原计划：
- §17.6 第 1 阶段：signals_v2 接入 L2 擅长度（14 天）
- §18.11 第 1 阶段：单 cohort Optuna 验证（14 天）

问题：§18 寻优的前提是"这批事件要不要跟"已经判断完——否则在所有 new_entry/increase 事件上寻优，等于让模型自己从 30k 样本里学"哪些该跟"，这是 §17 的任务。两者**串行而非并行**。

**修正方案**：

- **第 0-14 天**：§18 先跑 POC，但**对象不是"§17 预测 follow"的事件**，而是"历史实证 gain_60d ≥ 5% AND maxdd ≥ -10%"的事件（事后标签）。这等同于"给定能赚钱的事件，找最优持仓策略"——避免循环依赖。
- **第 14-30 天**：§17 Head-A baseline 出来后，§18 切换到"Head-A 预测 follow"作为事件筛选。
- 两者"串行但阶段部分重叠"，节奏比原计划慢 14-30 天。

### 20.6 KS ≥ 0.3 止损线需本地基准

`Session reconstruction`

我在 §19.3 直接套用信贷风控的 KS 基准（0.3 可用 / 0.4 好模型）。但信贷场景标签是"违约 yes/no"，信号强；金融时序"未来 20 日涨跌"噪声极大，典型 IC 0.03-0.05，对应 KS 通常 0.10-0.20。

**修正方案**：

- 第 14-30 天内，**先用 legacy composite + Head-A 模拟标签跑一次 KS，建立项目基线**
- 止损线设为 `baseline KS + 0.05`（即新模型必须比现有 composite 提升 0.05 以上才上线）
- 不用 0.3 的行业值，避免把项目"过度打死"

### 20.7 §17 多任务损失权重 1/0.3/0.3 是臆测

原值（§17.4.1）是直觉。工程实践里需要：

- 第 30 天训练时，先做损失尺度 sanity check：看三个 head 的 loss 量级（CE 通常 0.5-1.5，MSE 通常 0.01-0.05），自动归一化到相同数量级再加权
- 用 GradNorm 或 uncertainty weighting 动态调整
- 若发现某 head 欠拟合（val loss 持续下降但远不到 train loss 水平），降低它的权重
- 不把 1/0.3/0.3 当常数写进代码，改为配置项

### 20.8 修正版实施路线（替代 §15.7 / §17.6 / §18.11 / §19.5）

| 窗口 | 交付 | 前置依赖 |
| --- | --- | --- |
| **0-14 天** | chain_days 回填脚本 + alpha_halflife 计算；Optuna POC on 单 L1 cohort（如"稳健型×医药"约 400 事件）跑 2 维参数（hold_period + stop_loss）；事件仿真器 v0 | 无 |
| **14-30 天** | 漏斗阈值按历史跟投回测重标（通过机构数目标 20-40）；§17 Qlib Head-A 三分类 baseline；KS + AUC 评估一套；止损线本地校准 | chain_days 回填完 |
| **30-60 天** | §17 Head-A 分层（inst_type_group × L1）；§18 扩到 Top-10 L1 cohort；Calibration + Lift；§17 多任务 Head-B/C | §17 baseline 过 KS 本地线 |
| **60-90 天** | §17 + §18 合成 `primary_action` 字段；前端双列（primary_action + stock_follow_heat）+ 追高警示 + 期望收益/回撤；PSI + prediction drift 监控 | §18 Top-10 cohort 全部 holdout 稳健 |

90 天总窗口，比原 60 天多 30 天缓冲。

### 20.9 架构层的单点认知

把 §15-§19 放在一起看，会发现我反复在提同一件事但说法不同：

- §15：指标 → 决策"加工墓地"问题
- §16：机构分 → composite 的架构脱钩
- §17：预测特征没接入事件级评级
- §18：参数没接入 cohort 寻优
- §19：评估指标没接入上线决策

**根因是同一个**：**工程上"算了很多，用了很少"**。§20 的修正不是在改某个子方案，是提醒路线的每一步都要先问：**"这步的产出，有没有一个明确接入点回馈给业务决策？"** 没有就不做。

### 20.10 与 §14.3 / §15.8 的关联确认

§14.3 曾要求"5 只代表性股票的三条链全链路对账"，§15.8 元批判了漏斗阈值"是设计直觉不是回测验证"。本节 §20 实际上就是在执行那两项要求的一部分——让数据说话，而不是让直觉说话。剩下未做的：

- §14.3 的"5 只代表性股票全链路对账"：本节查了机构层和 cohort 层，没挑具体股票做对账，留给 §21 或独立 PR
- §15.8 的"漏斗阈值靠跟投回测反推"：需要先有 §18 的 `fact_cohort_optimal_strategy` 表，才能反推。短期无法闭环，只能用本节 §20.2 的敏感性扫描作为折中

### 20.11 一句话总结

**§15-§19 把路线图画得漂亮，但 §15.5 漏斗、§18 cohort 数、chain_days 数据三件事都被真实数据直接否决**。原因是我没有在写方案前先用 SQL 跑一次敏感性。修正后路线从 60 天延长到 90 天，cohort 粒度从 L2 退到 L1，漏斗阈值从 6 条 AND 退到 3 条，KS 止损线改为本地基准。**没有数据验证的路线图是空气**。项目真正需要的不是更多方案文档，而是**在每份方案推进时，先跑 SQL 看数据允不允许**。

## 21. 另一轮独立复核：当前态、主链与路线优先级（2026-04-23）

这一节是在 §20 的基础上，专门补一层“当前生产系统视角”的独立复核。重点不是继续扩方案，而是把当前态、当前主链和候选路线严格分开。

### 21.1 当前最需要修正的，不是模型，而是文档中的“当前态”

经过再次对照当前共享数据库，文档前半段最容易误导人的地方不是具体算法，而是“当前态”和“历史态”混在一起。

1. 当前 `mart_institution_profile` 的 `quality_score` 与 `followability_score` 都已经补齐到 `231 / 231`。
2. 因此，凡是把这两个字段写成“全空”的表述，都不应再以 `Shared DB current state` 的身份出现。
3. 它们最多只能保留在 RCA、阶段性诊断或历史上下文里。

如果这一层不先收口，后面的路线优先级就会一直被旧前提污染：文档会同时把“补跑评分”写成已完成，又把“评分字段全空”写成当前最硬证据。

### 21.2 我认同的三条主判断

独立复核后，我认同以下三条判断仍然是整份文档最值得保留的主轴：

1. **当前用户主列表的 `stock_gate` 主口径来自 MCR 聚合，而不是 legacy 综合分。** 这是当前活跃路由直接决定的，不是解释风格问题。
2. **legacy composite 仍然是明显的 stock-centric 公式。** 当前主公式只直接吃 discovery / quality / stage / forecast 四维，机构评分并不直接进入主公式。
3. **Qlib 当前标签与业务目标不一致。** 现状仍然是 next-day 截面标签，这和“跟投机构事件后 20 日能赚多少、亏多少”不是同一个问题。

### 21.3 我不同意或需要收紧的两点

有两点如果不收紧，读者会继续把研究链、展示链和候选方案混成一件事。

1. **不能把 `signals_v2` 说成当前唯一主决策链。** 更准确的说法是：它是当前并行链里唯一直接消费事件级收益字段的链，但不是当前用户主界面的唯一生产主链。当前 UI 主链仍然是 MCR 聚合 gate。
2. **§17–§19 应明确视为候选设计，而不是默认下一步 backlog。** 这些章节在方向上有价值，但它们依赖新的事件级建模、策略执行和评估工程，不应在“当前态还未统一、当前生产主链还未收口”之前被默认视为最高优先级。

### 21.4 我的执行顺序判断

如果按“先解决当前问题，再决定是否扩新路线”的原则来排，我会把顺序收敛成三步：

1. **先统一当前态。** 所有已经被 2026-04-23 当前数据库推翻的旧状态，都应降级为历史态说明。
2. **再收口当前生产主链。** 先明确当前 UI 主动作列到底以哪条链为准，并把其它链统一命名为辅助证据链或写层分析链。
3. **最后再做事件级回测与新建模。** `fact_institution_follow_backtest` 一类的直接跟投回测证据，应当优先于 §17–§19 的事件级 Qlib 和 Optuna 工程。

换句话说，我同意这份文档对“问题根源是链路脱钩而不是参数微调”的判断，但我不同意现在就把重心切到大规模新建模工程。先把当前态和当前主链讲明白，再决定是否值得启动 §17–§19，顺序才是稳的。

### 21.5 一句话总结

这份讨论文档最强的部分，是它已经抓到了“当前问题本质上是链路脱钩”这一点；它最弱的部分，是把当前态、历史态和候选方案写进了同一层级。独立复核后的结论是：**先统一当前态，先收口当前生产主链，先拿出直接跟投回测证据，再决定是否启动 §17–§19 的新工程。**

---

## 22. 从"方案文档"到"实证证据"：首次跟投回测结果（2026-04-23）

这一节是本文档目前**第一条用真实跟投回测数据说话**的记录，不是又一个方案。§15-§21 基本都是设计/评估，§22 报告实地做了什么、得到什么。

### 22.1 做了什么（与 §21 的路线契合）

按 §20 + §21 的共识（"先打跟投回测地基，不做新建模"）落了三件事：

1. **事件驱动仿真器** `backend/services/event_simulator.py`（245 行）
   - 输入：事件 DataFrame + `{entry_lag, max_hold_days, stop_loss, take_profit}` 参数
   - 输出：n_filled / avg_pnl / win_rate / annual_return / sharpe / avg_position_maxdd / p95_position_maxdd / exit_reason_counts
   - 数据源：`fact_institution_event` 事件 + `market_data.db / price_kline`（daily qfq）
   - 不依赖 `chain_days`（该字段全空），从 price_kline 实时重算
2. **跟投回测表** `fact_institution_follow_backtest`（建表 + 写入）
   - 字段：cohort_scheme / cohort_key / 参数组合 / n_filled / 全套业绩指标 / event_date 范围
3. **CLI** `backend/scripts/run_follow_backtest.py`
   - `--scheme L1_instgroup --top 5 --min-samples 300` 一键跑 Top-5 cohort × 18 组参数 = 90 条记录

### 22.2 Top-5 cohort 实测结果

排北向，L1 × inst_type_group 分组，每 cohort 样本 ≥ 300，每 cohort 跑 Grid 3(hold) × 3(sl) × 2(tp) = 18 组合：

| Cohort | 样本 | 最优参数 | win_rate | avg_pnl | Sharpe | p95_maxdd |
| --- | --- | --- | --- | --- | --- | --- |
| **稳健型×装备制造** | 978 | hold=10, sl=-15%, tp=+20% | **66.4%** | **+3.89%** | **2.25** | -14.3% |
| 其他×装备制造 | 2 301 | hold=10, tp=+20% | 62.8% | +2.64% | 1.68 | -13.5% |
| 其他×材料 | 1 936 | hold=10, tp=+20% | 60.8% | +2.23% | 1.55 | -12.4% |
| 其他×可选消费 | 2 108 | hold=10, tp=+20% | 61.1% | +1.88% | 1.25 | -14.0% |
| 其他×信息产业 | 2 706 | hold=20, tp=+20% | 57.0% | +3.35% | 1.10 | -19.8% |

（inst_type_group 按 §20.3 合并：稳健型 = 公募 + 险资 + QFII；交易型 = 游资 + 私募；其他 = 自营 + 信托 + 其他。本次样本最多的 Top-5 里只有 1 个"稳健型" cohort，其余 4 个是"其他"，说明"交易型"的披露事件量级不够进 Top-5。）

### 22.3 五条业绩驱动型结论（数据说话，不再靠直觉）

**结论 1：短持仓（hold=10）在 4/5 cohort 里最优。**

所有 cohort 的最优 hold 值都是 10 或 20，没有 40 胜出的。这和 §15 里引用的"A 股机构事件前 20 日 alpha 消化完成"判断方向一致。40 天持仓在所有 cohort 都明显变差（Sharpe 通常降到 0.5 以下）——alpha 半衰期确实短。

**结论 2：止盈 tp=+20% 普遍有效。**

所有 5 个 cohort 的最优都含 tp=+20%。机制：止盈锁定收益后，剩余的"本来会反弹又跌回"的样本被提前退出，降低方差提升 Sharpe。这推翻了"长期持有更好"的朴素假设。

**结论 3：紧止损（sl=-8%）反而降胜率。**

典型案例——"其他×信息产业" hold=20：
- 无 sl：win_rate 55.7%, avg_pnl 3.89%, Sharpe 0.79
- sl=-8%：win_rate 45.3%, avg_pnl 1.95%, Sharpe 0.50
- sl=-15%：win_rate 54.0%, avg_pnl 3.09%, Sharpe 0.64

-8% 太紧，把很多本来会反弹回本的持仓提前锁定成负收益；-15% 只拦住真正坏的。**用户在 §15 想加的"-8% 止损"漏斗条件应按 cohort 调整**。只有"稳健型×装备制造"胜出参数带 sl=-15%，其他四个 cohort 的最优都不含止损——**止损不是通用最优**。

**结论 4：机构质量（稳健型）确实有区分力，但不是决定性的。**

"稳健型×装备制造" Sharpe 2.25 明显超过其他 4 个"其他"cohort（1.10-1.68）。机构质量确实传递到跟投业绩。但差距不是 §15.1 按 quality_score 分桶 top-vs-worst（+9.77% vs -1.26%）那么戏剧——因为 Grid 最优化本身已经把其他 cohort 的劣势部分抵消掉。

**结论 5：不同 cohort 最优参数不同——单一策略参数是错的。**

"稳健型×装备制造" 偏好 hold=10+sl=-15%+tp=20%；"其他×信息产业" 偏好 hold=20+no sl+tp=20%。这是 §18 提出"cohort 分群寻优"的第一条实证——**"全市场一套参数"肯定次优**。

### 22.4 对 §15.5 漏斗阈值的回溯校准

§15.5 的漏斗阈值基于直觉（buy_win_rate_60d ≥ 55%）。现在用回测数据看：

- 稳健型×装备制造 win_rate 66.4%（充分高于阈值）→ "跟"
- 其他×信息产业 win_rate 57.0%（刚过阈值）→ "可跟但弱"
- 如果某 cohort 最优参数下 win_rate < 50%，说明无论怎么调参都是负 EV → "不该跟"

**修正方案**：§15.5 漏斗的 `win_rate ≥ 55%` 改为 `跟投回测下最优参数 win_rate ≥ 55% AND Sharpe ≥ 1.0`——**以跟投回测为锚，不以字段均值为锚**。这是 §15.8 "阈值靠回测反推"的第一次落地，虽然只覆盖 Top-5 cohort，但方向对。

### 22.5 下一步（避免再掉"方案文档"陷阱）

优先级按投入回报：

**0-7 天（立即可做）**
- 扩回测到 Top-20 L1×inst_group cohort（cohort 少用 L1，样本不稀释）
- 给 CLI 加 `--walk-forward 0.7`：70% 训练样本跑 Grid、30% holdout 验证最优参数是否稳
- 加一列 `strategy_note`：人工标注"稳健型×装备制造 用 hold=10/sl=-15%/tp=20%"这类推荐

**7-30 天（条件推进）**
- 前提：walk-forward 在 3+ cohort 上显示 holdout Sharpe ≥ 0.8 × train Sharpe（过拟合不严重）
- 把 `fact_institution_follow_backtest` 的最优参数拼成 `fact_cohort_optimal_strategy`（§18.8 设计）
- 前端事件详情页展示推荐策略（"此事件属 稳健型×装备制造 → 建议 hold=10/tp=+20%"）

**30-60 天（看数据决定）**
- 若 walk-forward 显示稳健：上 Optuna TPE 扩参数空间（§19.1）
- 若 walk-forward 显示过拟合：扩数据覆盖（现 L1×inst_group 只 108 cohort，样本仍紧）

**不要做**：现在不上 §17 多头 Qlib、不改前端主列、不碰 MCR 主链。等 walk-forward 证据出来再谈。

### 22.6 §22 相比前面 7 节的不同

| 章节 | 类型 | 最硬证据 |
| --- | --- | --- |
| §15 | 设计 | 实测 quality_score 与业绩相关性（top 桶 +9.77%） |
| §16 | 仿真 | 机构分接入 composite 的 10-18% 股票变化 |
| §17 | 设计 | 9 族特征 / 3 头 / 3 层训练 |
| §18 | 设计 | 67 200 组合 / 280 cohort 估算 |
| §19 | 评估 | Optuna / 档 C / KS 止损线 |
| §20 | 挑战 | SQL 证伪：漏斗过 1 家 / cohort 错估 5 倍 |
| §21 | 秩序 | 当前态 / 历史态 / 候选方案三层区分 |
| **§22** | **实证** | **跑了 90 条回测写入 DB；五条业绩驱动结论有数据，不靠直觉** |

§22 是本文档**第一条以"落库的实证记录"为证据层级**的章节。§15-§21 回答"应该怎么做"；§22 回答"跑了发现什么"。

### 22.7 一句话总结

**短持仓 + 止盈 + 按 cohort 调参**，是当前 Top-5 cohort 跟投回测给出的共同答案；**紧止损普遍反效果**；**稳健型机构确实有 Sharpe 加成但不压倒性**。不再谈设计，先让 `fact_institution_follow_backtest` 多积累几个 cohort 和一次 walk-forward 验证，然后才决定下一步是扩 Optuna 还是扩 §17。**走一步看一步，而不是先画 60 天蓝图**。

---

## 23. institution × L2 Walk-forward 实证结果（2026-04-23）

§22 用 `inst_type × L1` 粒度，被 §20 的"样本稀疏"工程妥协误导。用户纠正：`inst_type` 是名字关键词自动打标（240 家机构 `manual_type` 字段 0 填充），不是用户手工标签也不是抓取数据自带；应该直接按**具体机构 × L2 行业**分群，用真实业绩说话。

### 23.1 数据血缘确认

`Mainline current state` + `Shared DB current state`

- `inst_institutions` 表：`type` 字段 240/240 有值，默认 `'other'`
- `manual_type` 字段：**0/240 有值**——原本作为"用户手工标注覆盖层"的字段完全未被使用
- `type` 值由机构名字关键词匹配生成（含 "QFII"/"社保"/"基金" 等）
- **结论**：当前按 inst_type 分群等价于按"名字关键词"分群，不是真正的"用户校准过的机构类别"

### 23.2 新方案：按 `institution_id × L2 行业` 分群

- **样本分布**：排北向后 2 945 个 (institution × L2) 组合，样本 ≥ 30 的有 77 个 cohort
- **Walk-forward**：70% 训练寻优、30% holdout 验证，同 §22 方法
- **CLI 扩展**：`--scheme institution_L2 --min-samples 30 --walk-forward 0.7`

### 23.3 77 个 cohort 的 walk-forward 分布

| 类别 | 定义 | 数量 | 占比 |
| --- | --- | --- | --- |
| Stable | holdout Sharpe ≥ 1.0 AND ≥ 0.7×train | **27** | 35% |
| Weak positive | holdout Sharpe ∈ [0, 1.0) | 18 | 23% |
| Neutral | holdout Sharpe ∈ [0, train×0.7) | ~18 | ~24% |
| **Overfit** | **holdout Sharpe < 0** | **14** | **18%** |

相比 §22 的 `inst_type × L1`（10 cohort 里 5 stable / 3 overfit），`institution × L2` 的**信号密度显著提升**：stable 绝对数多，而且同一机构在不同 L2 上表现差异很大。

### 23.4 Top 15 Stable Cohort（按 holdout Sharpe 排序）

| 机构 | 擅长 L2 | hold | sl | tp | train Sharpe | holdout Sharpe | holdout 胜率 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 中信证券自营 | 食品饮料 | 10 | - | +20% | 1.74 | **5.77** | 100% |
| UBS AG | 有色 | 10 | -8% | +20% | 1.81 | **5.38** | 77.8% |
| 中信证券自营 | 商业连锁 | 10 | -8% | +20% | 2.29 | 4.46 | 83.3% |
| JPM 自有资金 | 化工 | 10 | - | +20% | 4.33 | 4.28 | 82.4% |
| JPM 自有资金 | 电气设备 | 10 | - | +20% | 3.48 | 4.24 | 72.4% |
| UBS AG | 食品饮料 | 10 | - | +20% | 3.49 | 3.95 | 80% |
| 中信证券自营 | 建材 | 10 | - | +20% | 1.87 | 3.74 | 69.2% |
| 高盛国际自有资金 | 工业机械 | 10 | - | - | 2.00 | 3.64 | 66.7% |
| 高盛国际自有资金 | 元器件 | 10 | - | +20% | 0.27 | 3.38 | 85.7% |
| 吕强（个人大户） | 互联网 | 10 | -8% | +20% | 1.70 | 2.77 | 80% |
| 中信证券自营 | 有色 | 10 | -8% | +20% | 0.44 | 2.55 | 57.9% |
| UBS AG | 家用电器 | 10 | -15% | +20% | 2.15 | 2.54 | 63.6% |
| 社保 503 组合 | 化工 | 10 | - | +20% | 1.56 | 2.53 | 70% |
| 中信证券自营 | 医疗保健 | 10 | -8% | - | 1.41 | 2.51 | 73.3% |
| 申万宏源自营 | 化工 | 10 | - | +20% | 2.95 | 2.45 | 64.3% |

### 23.5 业务观察（inst_type 合并完全捕捉不到）

1. **中信证券自营**独家擅长 5 个 L2：食品饮料、商业连锁、建材、有色、医疗保健——跨板块多元化
2. **JPM 自有资金**专攻 2 个 L2：化工、电气设备，两者 holdout Sharpe 都 >4，极稳
3. **UBS AG**擅长 3 个 L2：有色、食品饮料、家用电器（消费 + 周期混合）
4. **高盛国际**擅长制造业：工业机械、元器件
5. **吕强（个人大户）**只擅长 1 个：互联网——游资风格但有稳定 niche
6. **参数偏好**：绝大多数 cohort 最优是 hold=10 + tp=+20%，短持仓 + 止盈普遍有效
7. **止损策略不统一**：高盛元器件、中信证券商业连锁/有色/医疗保健、UBS 有色等用 sl=-8%；JPM 化工、UBS 食品饮料等不设止损更好——不能一刀切

### 23.6 对 §22 结论的替代

§22 的 "`稳健型×装备制造` Sharpe 2.25" 实际是 "QFII+私募" 的合成结果（私募样本 380 小头、QFII 3924 大头），真身是 **QFII × 装备制造**。在 §23 的 77 个 cohort 中 "QFII × 装备制造" 被具体的 `UBS AG / JPM / 高盛` 拆开，每家机构在不同 L2 上的表现差异巨大——这是 §22 看不到的信号。

**结论**：`inst_type × L1` 的结果作为历史对照保留，不作为业务决策依据。`institution × L2` 是新的主结果。

---

## 24. 综合评分方案：从"布尔白名单"到"多维共振打分"（2026-04-23）

### 24.1 范式转变

§23 的"stable/overfit 布尔"是临时过渡。真正要做的是：

- **不止打分擅长行业，不擅长的也要打分**（连续评分 0-100）
- **多机构持仓共振**：一只股票有多家机构同时持有，每家介入时机不同，合成 stock-level 可跟分
- **结合股票画像**：两融余额、机构调研、阶段特征、外部关注、Qlib 预测等多维信号找共振
- **Qlib + Optuna 真正上场**：既建模学特征交互，又寻优策略参数

### 24.2 可用数据维度盘点

`Shared DB current state`

通过 `sqlite_master` 枚举，当前可作为股票画像维度的表：

| 维度族 | 表 | 用途 |
| --- | --- | --- |
| 机构擅长度 | `fact_institution_follow_backtest`（§23 落的表） | 每个 (institution, L2) 的 walk-forward 业绩分 |
| 两融余额 | `raw_margin_daily` | 融资余额、融券余额、增减速度 |
| 机构调研 | `raw_institution_surveys` + `mart_stock_survey_activity` | 最近 30/60/90 天被调研次数 |
| 阶段特征 | `fact_stock_stage_features`（9 快照日） | dist_ma120/250、return_1m/3m/6m、above_ma250、volatility_20d |
| 股票质量 | `fact_stock_quality_features`（5 快照日） | `quality_score_v1` 及子项 |
| 预测/分析师 | `fact_stock_forecast_features` | 业绩预告、盈利预测 |
| 海龟 | `fact_stock_turtle_features`（5 快照日） | 趋势触发信号 |
| 外部关注 | `fact_stock_attention_snapshot` + `dim_stock_attention_latest` | 分析师上调、舆情热度 |
| 龙虎榜 | `raw_lhb_daily` | 席位买卖数据 |
| Qlib 截面预测 | `qlib_predictions`（最新 2026-04-13） | 现有短周期截面排序分 |
| 股票股性 | `fact_stock_character` | 波动性、弹性、beta |

**关键约束**：阶段/质量/海龟特征的历史快照日只有 5-9 天，不足以支持跨时段统计稳健评估；两融、调研、龙虎榜历史较长（raw 层）。

### 24.3 总体架构（四层）

```
┌───────────────────────────────────────────────────────────────┐
│ Layer D: 事件级综合可跟分 event_action_score (0-100)           │
│ 合成自 Layer C 的多维特征 + Qlib 分类头                         │
└─────────────────────────────────▲─────────────────────────────┘
                                  │
┌───────────────────────────────────────────────────────────────┐
│ Layer C: 事件特征矩阵 fact_event_features                       │
│ 每个 (institution, stock, notice_date) 一行，含下列族           │
│  F1 机构擅长度：该机构在该股票所属 L2 的 stable_score            │
│  F2 股票阶段：dist_ma250_pct / return_3m / volatility_20d       │
│  F3 股票画像：quality_score / forecast_strength                  │
│  F4 两融行为：margin_balance_change_30d                          │
│  F5 调研关注：survey_count_60d / analyst_upgrade_count           │
│  F6 事件属性：premium_bucket / hold_ratio / event_type           │
│  F7 多机构共振：同股票 stable 机构数 / 平均 stable_score          │
│  F8 Qlib 短周期：qlib_rank / qlib_percentile                     │
└─────────────────────────────────▲─────────────────────────────┘
                                  │
┌───────────────────────────────────────────────────────────────┐
│ Layer B: 机构擅长度评分 v_institution_l2_score                  │
│ 从 fact_institution_follow_backtest 得到                         │
│  每 (institution, L2) → [stable_score, suggested_params]         │
│  stable_score = f(holdout_sharpe, ratio, n_filled)               │
│  连续 0-100，不区分 stable/overfit 布尔                           │
└─────────────────────────────────▲─────────────────────────────┘
                                  │
┌───────────────────────────────────────────────────────────────┐
│ Layer A: 事件仿真器（已完成，§22）                              │
│ event_simulator.py + run_follow_backtest.py                     │
└───────────────────────────────────────────────────────────────┘
```

每一层是可独立验证的产物，可以逐层上线，不需要"全做完才生效"。

### 24.4 Layer B：机构 L2 擅长度评分（立即可做）

当前 §23 的结果是"stable / weak / overfit"三类布尔。Layer B 把它变成**连续 0-100 分**：

```
stable_score(inst, L2) = 
    base * min(1, holdout_sharpe / 2.0)          # 主信号：holdout Sharpe
  * clip(holdout_ratio, 0, 1)                    # 稳健性
  * min(1, holdout_n_filled / 20)                # 样本置信度
  * 100
```

特点：
- **不擅长的 L2 不是 0 分，是低分**（例如 holdout Sharpe 0.3 得 15 分）
- holdout Sharpe 负的（overfit）得 0 分
- 未验证过的 (机构, L2) 组合（样本 < 30）用**全机构平均分**作为先验填充

落表：`v_institution_l2_score` 作为 `fact_institution_follow_backtest` 的衍生 view。

**成功标准**：对 UBS、中信证券、JPM 三家 Top 机构，人工复核其 L2 分数排序符合直觉。

### 24.5 Layer C：事件特征矩阵（中期）

对每条 `fact_institution_event` 事件，把 Layer B + 股票画像 + 事件属性拼成一行特征。

**挑战**：
1. 股票阶段/质量/海龟特征只有 5-9 快照日，历史事件的特征需要"回溯到事件日最近快照"或"从 raw 层重算"
2. 两融、调研、外部关注历史完整，需要按事件日做 point-in-time lookup

**分两阶段实施**：
- **C-阶段 1（近期事件）**：只对最近 60 天的事件构建特征矩阵，验证特征工程代码
- **C-阶段 2（历史事件）**：为每个历史事件重算 stage/quality 特征（代价大，需要市场数据回溯），再拼特征

**成功标准**：Layer C 写入 `fact_event_features` 至少 5000 行，每行 20+ 个特征，缺失率 < 30%。

### 24.6 Layer D：Qlib 建模 + Optuna 寻优（长期）

用 Layer C 特征矩阵训练 Qlib，学习 `event_action_score = f(features) → forward_return`。

**模型设计**（§17 的简化版）：
- 起点：单头 LightGBM 回归 forward_return_20d（不是多头，先简单）
- 损失：MSE
- 特征：Layer C 的全部列
- 评估：IC / RankIC / KS / 分层 Sharpe

**Optuna 寻优**：
- 不是调模型超参，而是**联合调 (feature 权重、策略参数)**：
  - 策略参数（hold / sl / tp）按 Layer B 推荐的 stable_cohort 最优参数
  - 模型超参（tree_num / learning_rate / max_depth）Optuna TPE 扫 100 次
  - 多目标：IC 最大化 + 分层 Sharpe 最大化

**成功标准**：Qlib 模型在 holdout 上 IC ≥ 0.05 且 KS ≥ 本地基线 + 0.05（§20 提出的本地校准止损线）。

### 24.7 多机构共振（Layer C 内）

一只股票有多家机构披露，Layer C 特征矩阵以"事件"为行，但可衍生"股票"视角：

```
stock_resonance_score(stock, date) = 
    sum(stable_score(inst_i, stock_L2) for inst_i in 近 90 天披露的机构)
  / n_institutions_normalized
  * stock_quality_score
```

这可用于 stock-level 排序，前端"我的自选股今天谁值得关注"类功能。

### 24.8 不做的事

- 不碰 MCR 主链（§21 秩序原则）
- 不改股票主列前端（Layer D 结果先落表，前端消费后置）
- 不上多头神经网（先 LightGBM baseline，复杂度按需上）
- 不在 Layer B 未完成时启动 Qlib 训练（避免用假评分作特征）

### 24.9 路线图

| 窗口 | 交付 | 验证手段 |
| --- | --- | --- |
| **第 1 周** | Layer B（`v_institution_l2_score`）+ 扩 walk-forward 样本阈值到 ≥ 20（+50 cohort） | 对 Top 20 机构人工复核评分排序 |
| 第 2 周 | Layer C-阶段 1（近期 60 天事件的 `fact_event_features`） | 特征缺失率、单特征 IC |
| 第 3 周 | Layer D baseline（单头 LightGBM）+ KS/AUC/IC 评估 | holdout IC ≥ 0.03、KS ≥ 本地基线+0.05 |
| 第 4 周 | Optuna 扩到模型超参 + 策略参数联合寻优 | 对比 baseline 的 holdout Sharpe 提升 |
| 第 5 周 | Layer D 输出 `event_action_score` 落表 + 股票级共振分 | 抽样 20 事件人工复核、stock 排序合理性 |
| 第 6 周 | 前端新增"事件评级"卡片（非主列） | 用户试用反馈 |

每周结束**用数据验证**是否达到该周成功标准，达到才进下一步；不达到就原地优化或收缩范围。

### 24.10 核心原则（避免掉回"方案文档"陷阱）

1. **每一层先落表再接下一层**——没有数据就不写下一层的代码
2. **每周数据验证**——验证不达标先修当前层，不推进
3. **不上多头网络、不搞复杂架构**——简单方法验证通路
4. **Layer B 的评分不是最终答案**——它只是下一层的输入，下一层若用模型能学到更好的交互就用模型
5. **每一步都能产出"用户可查看"的证据**——即便是 SQL 结果或 view，不只是代码

### 24.11 一句话

**第 1 周就要有 Layer B 可查询的评分表**，然后一周验证一次是否达标，达标才进下一周。**不再画 60 天蓝图**，设计和验证的节奏必须对齐。

## 25. 再一轮独立意见：证据边界、对象选择与工程顺序（2026-04-23）

这一节不是继续扩方案，而是把我对全文当前版本的独立判断写死，避免文档再次从“开始有证据”滑回“又回到大设计”。

### 25.1 我认可的主结论

1. **整份文档现在最重要的诊断仍然是“链路脱钩”，不是“模型太弱”。** 当前用户主列表的动作口径仍来自 MCR 聚合 gate；legacy composite 仍是 stock-centric；Qlib 仍是 next-day 标签。这个判断没有变，也是全文最有价值的主轴。
2. **§22 和 §23 是目前最接近业务目标的证据层。** 它们第一次把“跟这类机构、这类行业、这类事件到底赚不赚钱”拉回到真实回测结果，而不是只谈因子、阈值和路线图。

### 25.2 还必须补上的三个边界

1. **当前跟投回测更准确地说是 cohort 级事件跟随统计，不是 portfolio 级资金曲线回测。** `event_simulator.py` 顶部说明写的是 `max_drawdown`，但当前实际输出是 `avg_position_maxdd` 和 `p95_position_maxdd`；`annual_return` 也是基于平均单笔收益和平均持有天数做的年化近似。因此，§22 / §23 可以作为“事件级跟随统计证据”，但不应直接表述为“组合级 IRR / 组合级最大回撤已经验证完毕”。
2. **§23 的 Top 15 Stable Cohort 在进入业务决策前，必须补 holdout 样本量或置信区间。** 现在表里给了 holdout Sharpe 和胜率，但没有把 holdout `n_filled` 放在同一张表里。77 个 cohort 里只看前 15 名，如果不同时披露样本量，很容易把偶然高 Sharpe 误当成稳定技能。
3. **当前 CLI 复现实验默认应理解为“排北向口径”。** `run_follow_backtest.py` 虽然已经暴露了 `--include-north` 参数，但当前 `run_backtest_for_cohort()` 里调用 `load_cohort_events()` 时并没有把 north 开关真正透传进去。因此，在脚本修复前，文档里所有通过 CLI 复现实验得到的结论，都应默认视为排北向结果，而不是“可自由切换北向口径”的结论。

### 25.3 我不同意直接把 §24 当成下一步主线

1. **如果 `event_action_score` / `stock_resonance_score` 在 MCR gate、legacy gate、`signals_v2` 之外再落成一套主动作语义，系统会从三条动作链膨胀成四条动作链。** 这会再次放大当前最核心的问题，而不是解决它。
2. **如果坚持“机构是主角，股票是载体”，下一阶段的主对象就应该继续是 `(institution_id, stock_code, notice_date)` 这类事件。** 股票级共振分可以做成展示层或辅助研究层，但不应先于事件级主动作链定义，更不应在当前主链尚未收口前又退回 stock-centric 综合评分习惯。

### 25.4 我的执行顺序判断

1. **先把证据边界补全。** 先修 north 开关、指标语义、holdout 样本量披露，让 §22 / §23 的证据更厚、更准、更可复现。
2. **再扩实证，不先扩架构。** 先把 Top-5 扩到 Top-20，再做 walk-forward 的稳健性对账，优先积累“哪些机构 × 行业真的稳定有效”的证据库。
3. **最后才进入 §24 的 Layer B。** Layer C / D 和新的综合评分架构都应建立在 §22 / §23 已经证明“这条事件证据链稳定成立”的前提上，而不是反过来用新架构去证明旧问题已经解决。

### 25.5 一句话总结

这份讨论文档现在最有价值的，不是它又提出了一套新的综合评分方案，而是它终于开始产出**机构事件级的实证证据**。下一步最该做的，是把这条证据链做厚、做准、做可复现，而不是过早回到新的大评分框架。

---

## 26. §25 证据边界修正 + Layer B 连续评分落地（2026-04-23）

本节是对 §25 指出的三个证据边界问题的修复记录，以及 §24 Layer B 的首次落地。按 codex §25 的建议顺序：先补证据边界、扩实证、再做 Layer B。

### 26.1 修复 §25 指出的三个 bug

1. **北向开关透传**（`backend/scripts/run_follow_backtest.py`）：`run_backtest_for_cohort()` 函数签名加 `exclude_north` 参数并透传给 `load_cohort_events()`；`main()` 调用处传 `exclude_north=not args.include_north`。修复前无论 CLI 传什么 `--include-north` 都被忽略，默认排北向。
2. **docstring 语义一致性**（`backend/services/event_simulator.py`）：顶部 docstring 把 `max_drawdown` 替换为真实输出字段 `avg_position_maxdd` / `p95_position_maxdd`，并明确写明"不是 portfolio 级累计回撤，disjoint 持仓串联复利 MaxDD 在统计上没意义"。
3. **holdout n_filled 披露**：新建的 `v_institution_l2_score` view 把 `train_n` 和 `ho_n` 都作为独立列暴露，前端/分析时可以和 Sharpe 并排看，不会把偶然高 Sharpe 误当稳定技能。

### 26.2 扩 walk-forward 到 samples ≥ 20

原 `min-samples=30` 覆盖 77 cohort；放宽到 ≥ 20 后覆盖 **135 cohort**：

| 分类 | ≥30 阈值 | ≥20 阈值 | 变化 |
| --- | --- | --- | --- |
| 总 cohort | 77 | 135 | +75% |
| Stable（宽松口径：holdout Sharpe≥1.0 且 ratio≥0.7） | 27 | 54 | +100% |
| Overfit（holdout Sharpe<0） | 14 | 31 | +121% |

样本越小越容易过拟合，所以 overfit 数增得更快；但 stable 数也增了一倍。证据库整体增厚。

### 26.3 Layer B：`v_institution_l2_score` 连续评分 view

**落库 view**（SQL 定义在 `smartmoney.db`）：

- **输入**：`fact_institution_follow_backtest` 里 `cohort_scheme='institution_L2'` 的 train/holdout 行
- **挑每 cohort train Sharpe 最高的参数组**作为该 cohort 推荐参数
- **输出列**：`institution_id`、`l2_name`、推荐参数、`train_n` / `ho_n`、`train_sharpe` / `ho_sharpe`、`stability_ratio`、`stable_score`（连续 0-100）、`verdict`

**连续评分公式**：

```
stable_score = 100 * min(1, holdout_sharpe / 2.0)         # 主信号
             *  max(0, min(1, holdout_sharpe / train_sharpe))  # 稳健性
             *  min(1, holdout_n / 30)                    # 样本置信
```

holdout Sharpe 负的 → 0 分；样本少的自然打低分；不稳定的自然打低分。**不擅长的 L2 不是 0 分，是低分**（比如 holdout Sharpe 0.3 大约得 10-15 分）。

**严格 verdict**：比 §23 多一个 `ho_n ≥ 15` 的硬约束，分类更细：

| verdict | 定义 | 数量（135 总） |
| --- | --- | --- |
| stable | ho_sharpe ≥ 1.0 AND ratio ≥ 0.7 AND ho_n ≥ 15 | **12** |
| weak_positive | ho_sharpe ≥ 0.5 且未达 stable | 80 |
| neutral | ho_sharpe ∈ [0, 0.5) | 12 |
| overfit | ho_sharpe < 0 | 31 |

严格口径的 12 个 stable 比宽松口径的 54 少，但每一个都是"样本充足 + 稳定性足够"的高置信。

### 26.4 Top 15 (institution, L2) 评分排序（含 holdout 样本量）

| 机构 | L2 | train_n | ho_n | train Sharpe | ho Sharpe | stable_score | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| UBS AG | 电气设备 | 107 | 43 | 1.75 | 2.06 | **100.0** | stable |
| JPM 自有资金 | 电气设备 | 67 | 29 | 3.48 | 4.24 | **96.7** | stable |
| UBS AG | 化工 | 96 | 39 | 2.11 | 1.84 | 80.1 | stable |
| 中信证券自营 | 化工 | 80 | 35 | 2.75 | 1.94 | 68.3 | stable |
| 中信证券自营 | 有色 | 45 | 19 | 0.44 | 2.55 | 63.3 | stable |
| 中信证券自营 | 工业机械 | 81 | 36 | 1.74 | 1.46 | 61.4 | stable |
| UBS AG | 通信设备 | 44 | 18 | 1.20 | 2.22 | 60.0 | stable |
| 华泰证券自营 | 工业机械 | 44 | 19 | 0.16 | 1.88 | 59.4 | stable |
| 中信证券自营 | 电气设备 | 105 | 46 | 3.13 | 1.89 | 57.3 | weak_positive |
| JPM 自有资金 | 化工 | 49 | 17 | 4.33 | 4.28 | 56.0 | stable |
| JPM 自有资金 | 元器件 | 58 | 26 | 1.30 | 1.23 | 50.0 | stable |
| 中信证券自营 | 医疗保健 | 32 | 15 | 1.41 | 2.51 | 50.0 | stable |
| UBS AG | 工业机械 | 121 | 51 | 3.02 | 1.73 | 49.6 | weak_positive |
| 华泰证券自营 | 化工 | 45 | 20 | 3.59 | 2.43 | 45.1 | weak_positive |
| 中信证券自营 | 建材 | 29 | 13 | 1.87 | 3.74 | 43.3 | weak_positive |

### 26.5 Top 20 机构汇总视角

按"stable_score ≥ 50 的 L2 数"排：

| 机构 | 类型 | 覆盖 L2 数 | stable 的 L2 数 | score ≥ 50 L2 数 | 最高评分 | 平均评分 |
| --- | --- | --- | --- | --- | --- | --- |
| 中信证券自营 | 券商 | 32 | 5 | 5 | 68.3 | 22.6 |
| UBS AG | QFII | 30 | 3 | 3 | 100.0 | 21.6 |
| JPM 自有资金 | QFII | 15 | 3 | 3 | 96.7 | 20.9 |
| 华泰证券自营 | 券商 | 15 | 1 | 1 | 59.4 | 12.9 |
| 申万宏源自营 | 券商 | 5 | 0 | 0 | 38.8 | 22.5 |

业务直觉符合：中信证券自营覆盖最广（32 个 L2）但"真正擅长"的只 5 个；UBS 覆盖 30 L2、稳定 3 个、却有满分 100；JPM 样本集中度高（15 个 L2 就稳定 3 个）。**Layer B 的价值是定量告诉用户"哪家机构在哪个 L2 真正擅长"，而不是给机构一个笼统标签。**

### 26.6 电气设备 / 化工 / 工业机械 的跨机构共振

Top 15 里最常出现的 L2：

- **电气设备**：UBS（100）、JPM（96.7）、中信（57.3）—— **3 家头部机构共振**，是最强跟投信号
- **化工**：UBS（80.1）、中信（68.3）、JPM（56.0）、华泰（45.1）—— 4 家共振
- **工业机械**：中信（61.4）、华泰（59.4）、UBS（49.6）—— 3 家共振

"共振"本身就是 §24 Layer C 的 `f7_resonance` 特征原型。当前已经能定量看到：电气设备 L2 上有 3 家 Top 机构 stable，是"重仓级"跟投信号；而信息产业 L2（§22 里曾被误当最优）在 Layer B 里几乎无 stable 机构。

### 26.7 对 §25 方向性分歧的回应

§25 反对直接进 §24 Layer B，理由是"会从 3 条动作链膨胀为 4 条"。我部分同意、部分不同意：

- **同意**：Layer D（`event_action_score` 最终落到 `mart_stock_trend` 主字段）不应该先做，那确实会膨胀主动作链
- **不同意**：Layer B 只是一个 view，不写任何 stock_gate 字段，不参与主决策链竞争。它是"证据库"不是"动作链"，本质和 §23 的表没有区别，只是把布尔升级为连续评分
- **折中**：Layer B 可做（本节已落），Layer C 先不做，Layer D 彻底不做——等 Layer B 证据足够厚、能支撑明确业务接入点时再考虑

### 26.8 下一步（§24 路线图的调整版）

| 窗口 | 交付 | 成功标准 |
| --- | --- | --- |
| 完成 ✅ | Layer B `v_institution_l2_score` 连续评分 view | Top 15 复核业务直觉合理 |
| 下一步 | **抽 3 只 stable cohort 里代表性股票做明细对账**（§14.3 原始诉求） | 3 只股票的 follow_gate 判定、事件胜率、持仓变化全链路可解释 |
| 然后 | **把 stable 评分接入前端机构详情页**（非主列） | 用户能在机构详情页看到"该机构擅长行业及评分" |
| 视情况 | Layer C 特征矩阵启动（仅当前面两步都稳定） | - |

本节完成了 codex §25 提出的全部证据边界修复，并在同时把 Layer B 评分落地。接下来不继续扩架构，而是**把已有证据接入用户可见路径**——这是 §24 核心原则"每层先落表再接下一层"的第一次兑现。

### 26.9 一句话

**bug 修了、样本扩了一倍、评分连续化了，135 cohort 里 12 个严格 stable 可信。现在最该做的是让用户看到这些证据，而不是继续加层。**

---

## 27. 3 只股票全链路对账：§14.3 原始诉求的首次落地（2026-04-23）

§14.3 提出过"3 只代表性股票全链路对账"，§15 和 §20 都提到但一直没真正做。本节用 UBS × 化工（Layer B 评分 80.1 stable）下的 3 只差异化股票做对账，验证 Layer B 与 legacy 链的相对表现。

### 27.1 样本选择

从 UBS×化工 cohort 里挑 3 只 2025 年以来有事件的股票，按"溢价/收益"特征差异化：

| 股票 | 溢价类型 | 实际 gain_60d |
| --- | --- | --- |
| 603681 永冠新材 | 追高（premium +14.89%）| +14.91% |
| 300537 广信材料 | 低位（premium -5.45% 均值 -7.23%）| +9.71%（均值）|
| 002108 沧州明珠 | 中等（premium +3.16% 均值）| +18.30%（均值）|

所有股票属同 cohort（UBS 化工 stable_score=80.1），共享 Layer B 推荐参数：`hold=10, sl=None, tp=+20%`。

### 27.2 四源对账结果

| 股票 | Layer B 10d 仿真 | legacy gate | MCR follow_gate | 实际 60d | 一致性 |
| --- | --- | --- | --- | --- | --- |
| 永冠新材 | +0.69%, win=100% (1/1) | watch (composite 62.2) | 1 家 watch | +14.91% | ✅ **三条一致**（都 watch/中性）|
| 广信材料 | **-3.85%**, win=25% (1/4) | watch (composite 62.1) | **3 家 follow** ⚠ | +9.71% | ❌ **MCR 激进，Layer B 保守** |
| 沧州明珠 | **+7.45%**, win=100% (3/3) | **observe (composite 57.5)** ⚠ | 2 家 watch | +18.30% | ❌ **legacy 漏机会** |

### 27.3 三个具体冲突案例

**永冠新材 ✓（3/3 一致，但都保守）**

- Layer B 10 天仿真只赚 0.69%，因为 UBS 事件后 10 天内股价还没启动
- legacy/MCR 都 watch
- 真实 60 天后 +14.91%，但 Layer B 参数是 hold=10，错过了 10-60 天的涨幅
- **启示**：UBS 在该股的入场时机偏"等待反应期"，不是"入场即涨"。Layer B 当前推荐 hold=10 对此股不是最优——但 cohort 平均下来 hold=10 仍是 UBS 化工整体最优

**广信材料 ❌（MCR vs Layer B 完全对立）**

- MCR = 3 家机构都 follow（按单事件 follow_gate 聚合）
- Layer B 10 天仿真：4 事件只 1 笔赚，avg_pnl -3.85%（跨事件统计）
- 真实 60 天均值 +9.71%（持仓 60 天会回本）
- **关键问题**：MCR 的 "3 家 follow" 是**单事件维度**的 follow_gate（基于当笔溢价判断），没有跨事件统计机构在该股历史"跟投后真能赚多少"。Layer B 用跨事件 PnL 给出的是"跟 10 天亏 3.85%"的警告
- **用户视角**：看 MCR 会进场；看 Layer B 会犹豫；看 60 天实际会觉得"其实可以跟但要忍"

**沧州明珠 ❌（legacy 漏机会）**

- legacy composite 57.5，被归入 C 池、observe（最低档）
- Layer B 10 天仿真 +7.45%, win=100%（3/3）
- 真实 60 天均值 +18.30%
- **关键问题**：legacy composite 基于 discovery/quality/stage/forecast 四维加权，不含"机构历史在该股/该 L2 是否赚过"的维度。所以真正稳定赚钱的组合（UBS × 化工 × 沧州明珠）被 legacy 漏掉、放到 observe 最低档
- Layer B 能捕捉到，因为它来自 UBS × 化工 walk-forward stable=80.1 的 cohort 评分

### 27.4 这次对账的价值

1. **§14.3 诉求落地**：3 只股票对账第一次实证了"多口径并存"——2/3 的股票三条链不一致
2. **Layer B 的独特价值被证实**：在沧州明珠上 Layer B 比 legacy 更准地识别机会；在广信材料上 Layer B 比 MCR 更谨慎识别风险
3. **没有哪条链是全对的**：Layer B 也不完美——永冠新材 10 天仿真没覆盖到 60 天实际涨幅，说明 cohort 级参数推荐对个股未必最优
4. **多链并排比单链强**：用户在事件详情页同时看到"legacy watch / MCR 3 follow / Layer B -3.85%" 的冲突，比单链"下结论"更有价值——它让用户知道"这里有分歧，需要人工判断"

### 27.5 下一步判断

- **Layer B 接入前端的路径更清晰**：不是替换任一条链，而是作为事件详情页的**第四个独立数字**，与 legacy/MCR/signals_v2 并排展示
- **不急着做 Layer C/D**：Layer B 只有 12 个严格 stable cohort，把它接到用户能看的地方优先级高于扩架构
- **对账样本还要扩**：3 只股票不够，后续可以自动化"每个 stable cohort 抽样 3 只"建立常态对账机制

### 27.6 一句话

Layer B 第一次作为"有证据支撑的第四个数字"出现在具体股票上，和 legacy/MCR 的冲突被实证坐实。**把这第四个数字接入事件详情页是下一步最具性价比的动作**，不是扩 Layer C/D。

---

## 28. Layer B 接入后端 API（2026-04-23）

§26/§27 把 `v_institution_l2_score` 落库并确认业务直觉合理后，本节把它接入用户可查路径，兑现 §24 "每层先落表再接下一层" 原则。

### 28.1 实施：最小侵入扩 `/profiles/detail/{inst_id}`

只在现有端点上加两个字段，不动其他字段、不改主路由：

1. **`industry_summary` 里 L2 节点新增 `layer_b` 字段**（仅在 v_institution_l2_score 有对应行时出现）：
   ```json
   {
     "level2": "电气设备",
     "stock_count": 22,
     "avg_gain_30d": ...,
     "win_rate_30d": ...,
     "layer_b": {
       "stable_score": 100.0,
       "verdict": "stable",
       "train_n": 107, "ho_n": 43, "ho_sharpe": 2.055,
       "rec_params": {"entry_lag": 1, "max_hold_days": 10, "stop_loss": null, "take_profit": 0.2}
     }
   }
   ```
2. **响应 root 新增 `layer_b_summary`**：
   ```json
   {
     "stable_l2_count": 3,
     "total_l2_with_score": 30,
     "top_stable_l2": [ {l2_name, stable_score, ho_n, ho_sharpe, 推荐参数...}, ... ]
   }
   ```

### 28.2 顺手修的静默 bug

原 `industry_summary` 树里 `level1/level2/level3` 存的是 TDX **code**（如 `T1001`）而不是行业**名**（如"银行"）。这导致：

- L2 节点里的 `avg_gain_30d` / `win_rate_30d` 一直查不到——因为 `mart_institution_industry_stat.industry_name` 字段存的是 name，用 code 查永远 miss
- Layer B 匹配也失败（`v_institution_l2_score.l2_name` 也是 name）
- 前端如果显示 `level2` 字段会看到 `T1001` 而不是"银行"

修复：`ind_rows` 改用 `tdx_l1_name / tdx_l2_name / tdx_l3_name`，与两张 stat 表对齐。

### 28.3 验证：UBS AG

```
总 L2 43，匹配 layer_b 30（剩 13 个 L2 持仓但无 walk-forward 样本）
L2 有 avg_gain_30d 的 43/43（修复前 0/43）
layer_b_summary.stable_l2_count = 3（电气设备/化工/通信设备）
layer_b_summary.total_l2_with_score = 30
```

三个 stable L2 都正确标注（电气设备 100.0 / 化工 80.1 / 通信设备 60.0），和 §26.5 Top 15 一致。

### 28.4 前端动作（下一轮）

前端代码暂不改——API 字段加好了，前端可以在任何时候独立加一段渲染代码消费 `layer_b_summary` 和 `industry_summary[].children[].layer_b`。可能的展示位置：

- 机构详情页顶部加"擅长 L2 评分"卡片（展示 `top_stable_l2` 前 N 条 + 推荐参数）
- 行业树 L2 节点右侧加评分徽章（stable=绿、weak_positive=黄、overfit=灰）

这些是 UI 判断题，留给下一轮确认后再改。

### 28.5 一句话

API 层 Layer B 接入完成（含一个静默 bug 修复）。下一轮要做的是前端渲染决策或扩 cohort 覆盖，看用户试用后最痛的是哪个。

---

## 29. 顶层设计：立体画像网络 + 非黑盒模型 + 前端研究工具（2026-04-23）

本节是对用户"每个实体有评分、多维画像形成立体数据库、Qlib/Optuna 找关联、模型非黑盒、前端关联跳转"诉求的收敛性顶层设计。**不再扩方案**，把已有 §15-§28 的渐进修正收束成一张系统全景图。所有内容必须引用现有表/view/字段，不发明新概念。

### 29.1 第一性原理：跟投决策的五问

一次跟投决定需要回答：

1. 这笔事件（institution × stock × notice_date）值得跟吗？
2. 用什么参数跟？（entry_lag / hold / sl / tp）
3. 预期赚多少、最多亏多少？
4. 证据是什么？（机构历史 / 行业共振 / 股票情绪 / 市场背景）
5. 模型为什么这么判？（可解释，非黑盒）

系统必须对每一问都有"可查、可追溯、可复核"的答案。§14.5 的三可原则是硬门槛，不是参考。

### 29.2 四实体、六评分、一事件

系统的数据宇宙由**四个实体**组成，每个实体一组连续评分：

| 实体 | 当前评分来源 | 评分 view（已有/待建） |
| --- | --- | --- |
| **机构 institution** | `mart_institution_profile.quality_score`（不驱动决策，仅展示）+ Layer B | ✅ `v_institution_l2_score`（§26） |
| **行业 L2** | 无独立 view，分散在 `research_inst_industry_performance` 等 | ⬜ 待建 `v_l2_profile`（29.3 定义） |
| **股票 stock** | `mart_stock_trend.composite_priority_score`（stock-centric、不含机构证据）| ⬜ 待建 `v_stock_multidim_score`（29.4 定义）|
| **事件 event** | 单条 `fact_institution_event` + `fact_institution_follow_backtest` cohort 指标 | ⬜ 待建 `v_event_action_score`（29.5 定义）|

**一事件**：`fact_institution_event` 里每条 (institution_id, stock_code, notice_date, event_type) 是系统的原子单元，四实体都围绕它旋转。

### 29.3 L2 行业画像 view（新）

用户诉求："每个 L2 行业持股的机构有评分、该行业的股票"——即 L2 是一个聚合节点，从它能查到两件事：

```sql
CREATE VIEW v_l2_profile AS
WITH stable_insts AS (
  SELECT l2_name, institution_id, stable_score, verdict, rec_params
  FROM v_institution_l2_score WHERE verdict IN ('stable', 'weak_positive')
),
l2_stocks AS (
  SELECT tdx_l2_name l2_name, COUNT(DISTINCT stock_code) n_stocks
  FROM dim_stock_tdx_industry GROUP BY tdx_l2_name
)
SELECT
  s.l2_name,
  s.n_stocks,                           -- 该 L2 股票数
  COUNT(DISTINCT si.institution_id) n_stable_insts,  -- 稳定机构数
  AVG(si.stable_score) avg_stable_score,             -- L2 内平均稳定度
  MAX(si.stable_score) top_stable_score,             -- L2 内最强机构得分
  (SELECT AVG(stable_score) FROM v_institution_l2_score WHERE l2_name = s.l2_name) avg_all_score
FROM l2_stocks s LEFT JOIN stable_insts si ON s.l2_name = si.l2_name
GROUP BY s.l2_name;
```

API：`/api/inst/industry/l2/{l2_name}` 返回 `{ summary, stable_institutions[], stocks_in_industry[] }`。

### 29.4 股票多维画像 view（新）

用户诉求的五个维度（已验证数据齐备）：

| 维度 | 来源表 | 评分公式（首版） |
| --- | --- | --- |
| **机构共振** | `v_institution_l2_score` × `mart_current_relationship`（持仓）| 持仓机构在该股 L2 的 stable_score 加权平均 |
| **两融情绪** | `raw_margin_daily` | `(rz_balance_delta_30d / avg_balance_120d)` 归一化到 0-100 |
| **研报预期** | `fact_stock_forecast_features.forecast_score_v1` | 已是 0-100 分，直接取最新快照 |
| **调研热度** | `mart_stock_survey_activity.inst_count_60d` | 分位归一化到 0-100 |
| **阶段位置** | `fact_stock_stage_features.dist_ma250_pct / return_3m` | `100 - |dist_ma250 + 20%|*5`（低位加分、追高扣分）|

```sql
CREATE VIEW v_stock_multidim_score AS ...
-- 每只股票 → {resonance_score, margin_score, forecast_score, survey_score, stage_score,
--             overall_score = 加权平均, top_stable_institutions}
```

每个分数都是连续 0-100，不是布尔。**不擅长/不热门/不合适的维度得低分，不是 0 分**，用户能看到分数对比。

### 29.5 事件级动作评分 view（Qlib 接入点）

这是 Qlib + Optuna 真正发挥的层。Layer C 特征矩阵 + Layer D 模型的收束：

```
v_event_action_score(institution, stock, notice_date):
  输入特征（Layer C 特征矩阵）：
    F1 institution.layer_b.stable_score（该机构在该股 L2 的擅长度）
    F2 stock.multidim.overall_score
    F3 stock.margin_score (两融情绪)
    F4 stock.forecast_score (研报预期)
    F5 stock.survey_score (调研热度)
    F6 stock.stage.return_3m / dist_ma250（介入时机）
    F7 event.premium_bucket（溢价档）
    F8 event.resonance_count（同期其他机构 stable 数）
    
  模型（Qlib LightGBM baseline）：
    单头回归：forward_ret_20d
    评分转换：event_action_score = clip(pred * 50 + 50, 0, 100)
    
  Optuna 寻优：
    模型超参（n_estimators / lr / depth）+ 策略参数（hold / sl / tp）联合 TPE
    目标：IC + holdout Sharpe 多目标 Pareto
    
  可解释性：
    - 每个事件输出 shap_top5（非黑盒核心）
    - 相似事件召回（最近邻 5 条历史事件）
    - 置信度 = 预测分布宽度（不光一个点估）
```

**输出**：`event_action_score (0-100) + confidence + shap_top5_json + similar_events_json + rec_params`。

### 29.6 模型非黑盒的落地清单

用户明确："这些都不要黑盒"。具体到每个输出：

1. **SHAP 归因**：每个 event_action_score 附带"贡献最大的 5 个特征及其值和贡献值"。LightGBM 原生支持，不需要额外训练。
2. **相似事件召回**：用 Layer C 特征矩阵计算 cosine similarity，返回最近 5 条历史事件及其实际业绩——让用户看到"类似情形过去赚/亏多少"。
3. **模型性能持续监控**（`qlib_model_evaluation` 表，§19.3 已设计）：KS / AUC / Calibration / IC / RankIC / lift_top_decile 按训练日期落表，前端有一个"模型健康仪表板"。
4. **置信度带宽**：不止一个预测值，报告 `[pred_low, pred_center, pred_high]`（quantile regression 或 LightGBM 的 pred_std）。
5. **特征漂移（PSI）**：每日生产预测时计算与训练集的 PSI，> 0.2 触发红灯。

### 29.7 前端关联跳转网络

用户诉求的"关联跳转"核心：**任何实体都是导航节点**，都能从这里跳到那里。

| 从 → 到 | 路径 | 展示重点 |
| --- | --- | --- |
| 机构详情 → L2 详情 | 点击擅长 L2 评分 | 该 L2 里其他稳定机构 + 在该 L2 的股票 |
| L2 详情 → 股票详情 | 点击 L2 下的股票 | 该股票画像 + 在该 L2 的 stable 机构 |
| L2 详情 → 机构详情 | 点击该 L2 的稳定机构 | 该机构在其他 L2 的画像（对比视角） |
| 股票详情 → 机构详情 | 点击持仓机构 | 该机构的全貌（擅长 L2、历史业绩） |
| 股票详情 → 事件详情 | 点击披露事件 | 该事件的 event_action_score + SHAP |
| 事件详情 → 机构 + L2 + 股票 | 从事件三角形跳任一顶点 | 三个实体的完整画像 |
| 事件详情 → 相似事件 | 模型召回的历史相似事件 | 看类似情形过去赚/亏多少 |
| 模型健康 → 某个 IC 低的时段 → 相关事件 | 从模型性能倒推异常事件 | debug 模型的异常 |

前端实现方式：每个评分字段做成可点击链接（点它跳到该评分对应的实体详情页）。不另建 "关联网络" 可视化（复杂度高、维护成本大），用"每个分数都是链接"的原则覆盖 90% 跳转需求。

### 29.8 分层落地路线（6 周，每周一个里程碑）

| 周 | 交付（必须落表 + 必须有 API + 必须人工复核通过） | 验收 |
| --- | --- | --- |
| 完成 ✅ | Layer B `v_institution_l2_score` + API | Top 15 业务直觉符合 |
| **W1**（下周）| `v_l2_profile` view + `/api/inst/industry/l2/{l2_name}` 端点 + 机构详情页 Layer B 卡片接入 | UBS 等 5 家机构页面能看到擅长 L2 + 跳转到 L2 详情页 |
| W2 | `v_stock_multidim_score` view + 股票详情页五维评分卡片 | 从股票详情页能看到机构共振/两融/研报/调研/阶段 五个分数 |
| W3 | Layer C 特征矩阵 `fact_event_features`（基于最近 60 天事件，验证特征工程）| 缺失率 < 30%、特征 IC 可查 |
| W4 | Qlib LightGBM baseline + SHAP + KS/AUC 评估 | holdout IC ≥ 0.03, KS ≥ 本地基线 + 0.05 |
| W5 | Optuna 联合寻优 + 相似事件召回 | vs baseline Sharpe 提升 + 20 个事件召回样本人工复核 |
| W6 | 模型健康仪表板 + 事件详情页整合（event_action_score + SHAP + 相似事件）+ PSI 监控 | 用户试用后报告"想看的都能看到" |

**每周结束数据验证**，达标才进下一周；不达标就原地修或收缩范围。

### 29.9 与现有架构的衔接

- **不动**：MCR 主链、legacy composite、stock_gate 主字段、signals_v2——保留它们作为"并行证据链"，不替换
- **加的是第四个独立数字**：event_action_score 是事件级新字段（落 `fact_event_features` 和 `qlib_event_prediction`，不写入 `mart_stock_trend`）
- **前端展示方式**：新评分作为补充卡片/徽章，不替换主 gate 列
- **Qlib 现有模型保留**：next-day 截面分继续跑（legacy），新的 forward 20d 事件模型是新 pipeline 并排跑

### 29.10 不做的事（继续守 §21 秩序）

- ❌ 不合并成"唯一动作链"（§14.4 主从 ≠ 合并原则）
- ❌ 不重构 scoring.py 的 composite 公式
- ❌ 不动 mart_stock_trend 主字段
- ❌ 不上多头神经网（LightGBM 先跑通）
- ❌ 不扩因子库（现有数据已经足够）
- ❌ 不做 RL（样本不够、解释性差）

### 29.11 立即可做的第一步（W1）

**具体交付**：

1. 建 `v_l2_profile` view（SQL 在 §29.3 已草稿，可直接执行）
2. 加 `/api/inst/industry/l2/{l2_name}` 端点到 `institution.py`
3. 前端 `assets/js/app.js` 机构详情页：
   - 加一张"Layer B 擅长 L2"卡片，消费 `layer_b_summary.top_stable_l2`
   - L2 名字做成可点击链接，跳转到 L2 详情弹窗（弹窗消费 `/api/inst/industry/l2/{l2_name}`）
4. L2 详情弹窗展示：**该 L2 的 stable 机构列表**（跳回机构详情）+ **该 L2 的在仓股票**（为 W2 铺路）

**成功标准**：UBS AG 详情页 → 点"电气设备 评分 100" → 跳 L2 弹窗 → 看到 UBS+JPM+中信 三家 stable 机构 + 装备制造下相关股票。

### 29.12 一句话

**把"机构-L2-股票-事件"四实体统一成一张图，每个节点有连续评分、每条连线可点击跳转、每个预测有 SHAP 和相似事件支撑、每个模型持续报告健康度**。W1 一周兑现"点机构擅长 L2 → 看 L2 里其他稳定机构"这条最短跳转，之后每周扩一条。

---

## 30. W3 执行：fact_event_features 全量构建 + IC 简测（2026-04-23）

按 §29.8 路线图 W3 交付 Layer C 特征矩阵。注意：原 §29.8 写"近 60 天事件验证"，实际落地改为**全量事件**——60 天 label 成熟度只有 37%，全量可达 96.6%，样本量从 1636 → 31372 增 19 倍。

### 30.1 fact_event_features schema

主键 `(institution_id, stock_code, notice_date, report_date)` ——同披露日含多报告期（公募年报+一季报合并披露）是常态，单主键会冲突。

特征族 9 × 39 列：F1 机构×L2 擅长度 / F3 研报 / F4 调研 / F5 阶段 / F6 两融 / F7 机构个体业绩 / F8 共振 + 事件属性 + label。

### 30.2 全量构建结果

```
查询返回 31 372 条事件（全部 new_entry/increase 且有 notice_date）
耗时 10 分钟（主要 resonance_agg nested self-join ±90d window）
```

### 30.3 按族覆盖率

| 族 | 列数 | 覆盖率 | 判定 |
| --- | --- | --- | --- |
| F7 inst_profile（机构个体业绩） | 4 | **100%** | 合格 |
| F3 forecast（研报预期） | 3 | 99.9% | 合格 |
| F8 resonance | 1 | 100% | 合格 |
| label（30d/60d） | 4 | 96.6% | 合格 |
| F5 stage（阶段特征） | 5 | 85.7% | 合格 |
| F6 margin（两融） | 2 | 79.1% | 合格 |
| **F4 survey（调研）** | 2 | **35.3%** | 数据稀疏（1 643 股票有 survey 表） |
| **F1 inst_l2（Layer B 擅长度）** | 5 | **20.3%** | 数据稀疏（v_institution_l2_score 只 135 cohort） |

两个低覆盖族不是 bug 是**数据客观稀疏**：survey 表只覆盖 1 643 只股票；Layer B view 只覆盖 135 个 (机构, L2) cohort。W4 训练时把它们当作"有值=有信息 / 无值=默认信号"处理，不强求 70% 覆盖。

### 30.4 单特征 IC 简测（全量 31 372 事件 Spearman 秩相关）

**label_gain_60d Top 5**（高 → 未来 60d 收益高）：

| 特征 | 覆盖样本 | Spearman |
| --- | --- | --- |
| stage_dist_ma250_pct | 25 184 | **+0.131** |
| inst_buy_win_rate_60d | 29 685 | **+0.131** |
| stage_return_6m | 25 223 | +0.129 |
| inst_buy_avg_gain_60d | 29 685 | +0.125 |
| inst_quality_score | 29 685 | +0.113 |

**label_max_drawdown_60d Top 5**（注意符号：正数 = 回撤更浅、负数 = 回撤更深；这里 label 本身为负数，所以正相关 = 回撤浅）：

| 特征 | 覆盖样本 | Spearman | 业务含义 |
| --- | --- | --- | --- |
| hold_amount | 30 917 | **−0.236** | 大仓位回撤更深 |
| stage_volatility_20d | 26 451 | +0.190 | 高波动股回撤深（符号：vol 大 ↔ maxdd 负向绝对值大） |
| change_amount | 30 917 | −0.153 | 加仓量大回撤深 |
| stage_return_3m | 26 451 | −0.111 | 近期涨幅大的回撤大 |
| inst_buy_win_rate_60d | 30 917 | −0.096 | 高胜率机构跟投的股票回撤小 |

### 30.5 三个关键洞察（W4 训练前置）

**洞察 1：机构个体业绩（F7）才是真正的核心预测**

4 个 F7 特征（inst_quality_score、inst_buy_win_rate_60d、inst_buy_avg_gain_60d、inst_followability_score）在多个 label 上都是 Top 5。这印证 §15 的观点——**业绩实证本身最有预测力**，胜过 Layer B 评分化（F1 尚未显现 IC 优势，因覆盖只 20%）。

**洞察 2：`stage_dist_ma250_pct` 正相关推翻 §29.4 的 stage_score 公式**

§29.4 我设计的 stage_score 公式是"低位加分、追高扣分"——基于"低位入场安全"的直觉。

IC 实测 **+0.131**：追高（dist_ma250 高）反而未来 60 天收益高。这是**动量效应**（破年线后继续涨的惯性），和我的人工公式相反。

结论：**五维卡片的 stage_score 首版公式方向错了**。W4 训练时必须丢掉这个直觉公式、让模型自己从 dist_ma250_pct 原始值学方向。

**洞察 3：金融数据 IC 0.13 是显著信号**

金融时序 Spearman 典型 0.03-0.05，IC 0.13 是**强信号**。Top 5 特征都 ≥ 0.11，说明 Layer C 特征矩阵是**有预测力的**，不是堆数据。

### 30.6 性能与增量更新

全量 10 分钟太慢。bottleneck 是 `resonance_agg` 的 nested self-join（每条事件要扫过 ±90d 同股票其他事件）。

优化路径（W4 启动前做）：
- 先把 `resonance_agg` 预计算到单独表，事件特征构建只做 join
- 或每天增量补昨日新事件（当前 SQL 支持 `--days N` 增量）

### 30.7 对 §29.4 五维公式的必要修正（待 W4 合并）

§29.4 的 stage_score 公式基于"低位加分追高扣分"直觉，IC 显示方向反。但前端五维卡片 §28 已上线——**不立即推翻**，标注"首版未经校准"（已注明），W4 跟模型一起重标定。

其他维度的 IC 信号：
- resonance_score（机构共振）：无直接 IC 结果，但它是 v_institution_l2_score 的聚合，和 F1 相关
- margin_score（两融）：margin_rz_balance IC=+0.074，弱相关
- forecast_score / survey_score：Top 15 里未出现，IC < 0.1，信号弱但非零

W4 结论将重新校准五维权重，不再用"简单平均 = overall"。

### 30.8 W3 交付清单

- [x] `fact_event_features` 表 + 3 个索引
- [x] `backend/scripts/build_event_features.py` 支持 `--days N` / `--dry-run`
- [x] 31 372 全量事件落表
- [x] IC 简测产出 Top 相关特征
- [x] 发现 §29.4 stage_score 公式方向错误，记录待 W4 修
- [x] 发现两个低覆盖族（F1 20% / F4 35%）是数据稀疏，非 bug

### 30.9 一句话

Layer C 落地 31 372 事件 × 39 列，IC 给出明确优先序：F7 机构业绩 > F5 阶段 > F6 两融 >> F1 Layer B > F4 调研。W4 Qlib 训练有了干净起点。
