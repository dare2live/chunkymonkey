# BestChoice 目标方案：MACD 趋势策略与通达信选股公式重构

> 状态：第一阶段已部分落地并完成统一股票池推荐口径验证。后续使用 `/goal` 时从“统一池性能优化、详情页继续完善、审计补全”继续实施。
>
> 边界：只修改 `bestchoice` 项目。`/Users/dp/Documents/M/stock/chunkymonkey` 仅作为只读参考，不修改其代码、配置、数据库结构或任务链。

## 0. 当前恢复与并发规则（2026-05-20）

BestChoice 是独立项目根：`/Users/dp/Documents/M/stock/bestchoice`。中断、终端崩溃或多 Codex 并发时，先运行：

```bash
bash scripts/bc_resume.sh
```

该命令会刷新 `analysis/workflow_checkpoint.json`、`analysis/workflow_checkpoint.md`、`analysis/recovery_snapshot/latest/` 和根目录 `SESSION_HANDOFF.md`；不会探测或操作 GCP，除非用户在当前对话中明确授权。

恢复规则：

- 如果 `next_action=wait_active_worker`，说明已有 BestChoice worker 正在跑，禁止再启动相同 batch；等待进程结束后重跑 `bash scripts/bc_resume.sh`。
- 如果 `consistency.ready=True` 且没有 active worker，则执行 checkpoint 给出的 `next_action.command`。
- 如果 `consistency.ready=False`，按 checkpoint 给出的 `next_action.commands` 串行重建 adoption / merge plan / research cache / incremental eval / drift trigger。
- 不写生产 `analysis/stock_formula_best.csv`，直到全市场覆盖和 aggregate audit 通过。
- `chunkymonkey` 只作为行情只读源；不修改其代码、配置、数据库结构或任务链。
- GCP 使用硬约束：除非用户在当前对话中明确说明可以使用 GCP，否则 BestChoice 恢复和推进流程一律只在本地运行；不主动启动、停止、监控或占用任何 GCP VM / GCS / 远端训练任务。本地任务耗时很长时，只能先向用户说明情况并等待明确授权。

2026-05-20 19:03 CST 实测恢复状态：

- 第 204 批已完成，覆盖 `4096` 只股票，`next_offset=4096`。
- `analysis/research_cache.duckdb`、`analysis/incremental_eval.duckdb`、`analysis/drift_trigger.duckdb` 均刷新到 `40787` 行。
- `consistency.ready=True`、`missing_without_reason=0`、`formula_caches=5/5 ready`。
- `SESSION_HANDOFF.md` 已能在根目录直接说明下一步与 active worker 状态。
- `scripts/workflow_checkpoint.py` 已加入 active worker 检测，覆盖 `formula_local_optuna_batch.py`、`research_cache_build.py`、`incremental_eval_build.py`、`drift_trigger_build.py`，防止中断恢复时重复启动同一批任务。
- 历史 GCP 监控记录已废止为 BestChoice 恢复动作：除非用户在当前对话中明确授权，否则不再探测、监控、拉取、停机或占用任何 GCP 资源。

当前推进优先级：

1. 继续全市场 local Optuna dry-run batch，直到覆盖全部股票。
2. 每批结束后串行刷新 adoption、merge plan、research cache、incremental eval、drift trigger，并刷新 checkpoint/handoff。
3. 继续使用 CodeGraph + complexity optimizer 审计热点；优先选择不改变策略语义的纯计算优化，例如 `formula_engine.py::ma_base_breakout_signals` prefix counter。
4. 全市场覆盖后做 aggregate audit，再决定是否进入受控生产合并；生产参数表不得自动覆盖。

## 1. 总目标

将当前“MACD 参数组 + 公式过滤器”混合展示的选股台，重构为一个统一股票池，内部由 MACD 与通达信公式共同提供信号、回测和推荐依据。

核心方向：

- `MACD 趋势策略` 与 `通达信选股公式` 都作为股票的信号来源，而不是多个互相割裂的股票列表。
- 每个公式仍必须独立回测、独立参数寻优、独立生成买卖点和持仓周期。
- 但最终展示层优先整合为一个股票列表：
  - 每只股票显示命中的 MACD/公式信号集合。
  - 明细页展示该股票所有有效策略的参数、信号、历史表现和当前买卖窗口。
  - `今日推荐` 只展示多重信号命中、且处于最佳买入窗口、且历史有效性达标的股票。

最终用户不需要理解“参数组 M / F1 / F3 / F5”这些技术编号，也不需要在多个公式列表里来回切换。页面直接展示可执行结论、信号来源、买卖点、持仓周期、风险和历史有效性。

## 2. 主标签与信息架构

第一阶段可保留两个主视角，便于调试和逐步迁移：

```text
综合股票池
策略研究
```

### 2.1 综合股票池

最终主入口。

列表是一份完整股票池，不按单一公式拆成多个列表。

默认子视图：

```text
今日推荐
买入窗口
持仓观察
全部股票
```

字段：

- 股票代码/名称
- 当前综合动作
- 命中信号数
- 命中策略标签：
  - MACD 趋势
  - GS回调确认
  - GS原始买点
  - 均线筑底突破
  - 活跃度大牛突破
  - 巨量蓄势启动
- 最强信号
- 信号日期
- 建议买入日
- 买入价 VWAP
- 最优持仓周期
- 计划卖出日
- 最近收益
- 综合胜率
- 综合均收益
- 综合均回撤
- 综合评分
- 风险提示

`今日推荐` 入选规则：

- 当前至少一个策略处于最佳买入窗口。
- 多策略命中优先，但不是绝对必要条件。
- 历史胜率、均收益、回撤、有效性评分达标。
- 不可成交率不过高。
- 信号不能明显陈旧。
- 若多策略互相冲突，必须降权或排除。

### 2.2 策略研究

用于展开研究和调试，不作为日常主要入口。

内部包含：

```text
MACD 趋势策略
通达信选股公式
参数寻优
回测审计
```

新增待办：策略研究需要增加一个 `Optuna 管理台`，把“全量初始化 -> 缓存结果 -> 每日增量验证 -> 漂移触发局部重跑 -> 定期全量复审”做成可观测、可审计的管理流程，而不是只展示离线 CSV 结果。

新增待办：在 `Research Cache` 之上增加 `Parameter Knowledge Base` 参数知识库，把单次 Optuna 最优结果沉淀为可复用的参数经验，而不是只记录某只股票某个公式的一组最佳参数。

参数知识库最小范围：

- 为每个 `(formula_id, param_name, param_value, stock_code, industry/regime, version_key)` 记录该参数影响的收益、回撤、胜率、平均持仓周期、信号数量、成交可行性、稳定性和适用市场状态。
- 把参数作用显式建模：窗口类参数影响信号频率和滞后，量能阈值影响信号质量和漏选概率，趋势/均线参数影响持仓周期和回撤，卖出规则参数影响收益分布和资金占用。
- 为后续新增公式和调参提供 warm-start 搜索空间：先从相似公式、相似股票、相似行情状态读取高概率参数区间，再局部 Optuna，不从全空间盲搜。
- 支持多目标参数推荐，而不是只按收益最大：同时输出 `max_return_params`、`low_drawdown_params`、`short_holding_params`、`balanced_params`、`industry_default_params` 和 `market_regime_params`。
- 综合评分必须惩罚高回撤、长持仓、低信号数、跨时间段不稳定和过拟合；不能把单次历史收益最高直接视为生产最优。
- 前端管理台需要展示参数影响地图、参数分布、生产参数 vs Optuna 参数 vs 参数库推荐参数、参数漂移记录和参数复用来源。

管理台最小范围：

- `Research Cache`：为 `(stock_code, formula_id, version_key, data_end_date)` 持久化局部 Optuna 最优结果，记录公式版本、执行模型版本、评分函数版本、行情数据版本/hash、Optuna trials、seed、best_params、sell_rule、holding_days、训练/验证/全样本指标、baseline 对比、采纳结论、缺失调查原因和 run_id。
- `Incremental Evaluator`：每日只对已有 best params 应用新增行情区间，更新 out-of-sample 表现；历史区间、公式版本、执行模型版本、评分函数版本和行情 hash 未变化时不得重复全量 Optuna。
- `Drift Trigger`：仅在最近 N 天胜率/收益显著下降、信号数量异常、回撤超过阈值、新增数据长度达到阈值（默认 60 个交易日）、公式/执行模型/评分函数版本变化时，触发局部重跑。
- `Run Registry`：记录每次批处理/增量验证的 run_id、起止时间、offset、覆盖股票数、候选数、拒绝数、缺失调查数、dry-run replacement 数、命令参数和验证结果。
- `Frontend Console`：在策略研究页展示全量初始化进度、cache 命中/待建数量、增量验证状态、漂移触发队列、最近 run 结果、dry-run 合并状态和生产合并阻塞原因。

建议落地顺序：

- 先新增 `analysis/research_cache.duckdb`，从 `analysis/formula_local_optuna_batch_adoption.csv`、`analysis/formula_local_optuna_batch_merge_plan.csv`、`analysis/stock_formula_best.csv` 导入版本化研究结果。
- 再扩展 `/api/parameter-search`，返回 `source_files`、`artifact_fingerprints`、`management.full_initialization`、`research_cache_status`、`incremental_eval_status`、`drift_status` 和 batch top candidates。
- 然后新增 `analysis/incremental_eval.duckdb`，记录 `(stock_code, formula_id, params_hash, sell_rule)` 的最近增量验证日期、最新信号/交易边界、dirty_reason 和 source_cache_key。
- 最后新增 `analysis/drift_trigger.duckdb`，记录 stock/formula 级 `none/watch/reevaluate/reoptimize/disable_candidate` 漂移状态，并把重跑 Optuna 从人工 CSV 检查改成可查询队列。
- 前端管理台先做只读状态面板，再做任务启动按钮；启动按钮必须有后端 run registry 和单任务锁/错误输出后才能开放。

验收口径：

- 前端能明确区分 `全量初始化`、`Research Cache`、`Incremental Evaluator`、`Drift Trigger`、`生产合并` 五个状态。
- API/UI 不允许把缺失 cache 或缺失增量验证结果显示为正常；必须显示阻塞原因。
- 未完成全市场覆盖和全量审计前，生产 `analysis/stock_formula_best.csv` 仍不得自动覆盖。
- 增量方案落地后，下一次验证应优先复用未失效的历史 Optuna 结果，只对新增数据或漂移股票重跑。
- `Research Cache` 第一版已落地：新增 `scripts/research_cache_build.py`，默认重建 `analysis/research_cache.duckdb`，当前导入 32202 行、覆盖 5143 只股票，其中 local Optuna 10900 行、生产基线 21302 行、候选 536 行，数据日期 `2026-05-19`。导入脚本不允许空数据日期；无法从 `compute.get_latest_data_date()` 或 `market.duckdb` 得到有效日期时必须失败，不得静默写空。
- 管理台前端已改为“缺失即阻塞/查因”：`management.full_initialization`、`research_cache_status`、`incremental_eval_status`、`drift_status`、`production_merge` 缺失时显示 API 缺失原因，不再用本地推算伪装正常状态。
- `Incremental Evaluator` 状态库第一版已落地：新增 `scripts/incremental_eval_build.py`，从本项目 `analysis/research_cache.duckdb` 生成 `analysis/incremental_eval.duckdb`。当前状态 32202 行、覆盖 5143 只股票，`clean=32202`、`dirty=0`、`pending=0`，目标行情日期 `2026-05-19`；`/api/parameter-search` 与前端管理台已读取真实 clean/dirty/target date 状态。
- 顶层模块化方案已新增到 `analysis/top_level_architecture_plan.md`：采用最小模块边界 `Source Adapters`、`Signal and Execution Core`、`Research Pipeline`、`Research State Store`、`API Aggregation`、`UI Console`。`chunkymonkey` 只读提供行情/画像，BestChoice 自己维护 `analysis/research_cache.duckdb`、`analysis/incremental_eval.duckdb`、未来 `analysis/drift_trigger.duckdb`。
- `CodeGraph + complexity optimizer` 后续工作流已纳入顶层方案：先用 `.codegraph/codegraph.db` 定位热点函数依赖半径，再用 `codex-complexity-optimizer` 做 report-only 复杂度/性能报告，最后只对单一热点做受控改动并跑项目门禁。2026-05-20 已确认本机存在 `/opt/homebrew/bin/codegraph`、`/opt/homebrew/bin/codex-complexity-optimizer`，并已把 BestChoice CodeGraph 索引从 6 个 Python 文件同步到 23 个 Python 文件，覆盖 `scripts/formula_local_optuna_batch.py`、`scripts/formula_local_optuna.py`、`formula_engine.py`、`execution_model.py`、`main.py` 和相关 research state store 脚本。当前限制：CodeGraph 仅索引 Python，尚未覆盖 `index.html`；复杂度扫描结果必须作为线索，不得未经验证直接改策略语义。详见 `analysis/complexity_codegraph_audit.md`。
- `Drift Trigger` 状态库第一版已落地：新增 `scripts/drift_trigger_build.py`，从 `analysis/research_cache.duckdb` 与 `analysis/incremental_eval.duckdb` 生成 `analysis/drift_trigger.duckdb`。当前状态 32202 行、覆盖 5143 只股票，`none=27052`、`watch=5150`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`；watch 队列仅作为观察/降权提示，不自动重跑 Optuna，不写生产。`/api/parameter-search` 与前端管理台已读取真实 watch/reevaluate/reoptimize 状态。

### 2.3 MACD 趋势策略

内部候选方案：

- `通达信默认`：EMA(12,26,9)
- `基准`：EMA(10,22,8)
- `短周期 S`：EMA(10,22,8)
- `中周期 M`：EMA(12,26,9)
- `长周期 L`：EMA(14,30,11)
- `Optuna 全局优选`
- 后续可加入 `每股局部 Optuna 优选`

页面不再把这些作为一排策略按钮，而是在每只股票内部自动比较，并把最优结果汇总到综合股票池。

### 2.4 通达信选股公式

公式研究视图可按公式筛选，但这不是最终主要列表形态：

```text
全部公式
GS回调确认
GS原始买点
均线筑底突破
活跃度大牛突破
巨量蓄势启动
```

公式来源与命名：

| 展示名称 | 来源 | 当前/历史对应 | 定位 |
|---|---|---|---|
| `GS回调确认` | 用户“左侧GS选股公式” | 旧 F3 | GS 体系下叠加历史质量、趋势、回撤过滤的稳健确认 |
| `GS原始买点` | 用户“gs买卖点选股” | 新增 | 原始 GS 买点 `CROSS(X36, X3)`，更敏感 |
| `均线筑底突破` | 用户“筑底90 145公式” | 旧 F1 | MA5 长期低于 MA90 后突破 MA145 的筑底突破 |
| `活跃度大牛突破` | 用户“活跃度选股” | 新增 | K 线活跃度强度突破大牛线，偏短线异动 |
| `巨量蓄势启动` | 根据 301511 德福科技案例新设计 | 新增 | 巨量换手后缩量横盘，再温和放量启动 |

旧 `F5：止跌金叉回升` 暂不删除。若后续用户确认它不是常用公式，可降级为归档或隐藏。

### 2.5 后续探索方向：单列表多策略融合

后续重点探索并优先落地：

- 只保留一个主要股票列表。
- MACD 与所有通达信公式都作为该股票的策略证据。
- 每个股票行展示：
  - 命中了哪些公式。
  - 哪些公式处于买入窗口。
  - 哪些公式已进入持仓观察。
  - 哪些公式历史有效但当前未触发。
- 股票明细页按股票聚合展示全部策略：
  - 策略卡片矩阵。
  - 每个策略的最佳参数。
  - 每个策略的最近信号、买入日、卖出日、收益、回撤。
  - 多策略共振时间轴。
- 今日推荐定义为：
  - 多重命中或高质量单信号。
  - 当前处于最佳买入窗口。
  - 综合回测质量通过。
  - 成交模型未被涨停/停牌等条件阻断。

### 2.6 2026-05-19 已验证推荐口径

统一股票池已经引入 10 个策略源，并完成“达标买入窗口共振”修正：

- 多个 MACD 参数组只计为一个 `macd` 信号家族，不能互相刷共振。
- 公式策略按公式 id 计为各自信号家族。
- `confluence_score` 保留为当前信号家族数，仅用于诊断。
- 今日推荐与“多策略共振”入口使用 `qualified_buy_family_count`：
  - 必须处于买入窗口。
  - 胜率至少 `0.55`。
  - 均收益为正。
  - 策略有效性分数至少 `50`。
- 2026-05-19 验证结果：
  - 全池 `5201` 只。
  - 今日推荐 `97` 只。
  - 达标多策略共振 `56` 只。
  - 普通当前多家族命中 `4455` 只，仅作为诊断字段 `current_multi_family`。
- 样例 `301511`、`301658`、`688700`、`002718` 均未因普通当前共振被误推荐。

详见 `analysis/recommendation_confluence_audit.md`。

### 2.7 2026-05-19 统一池性能优化记录

统一股票池新增持久化快照，解决进程重启后必须重新聚合 10 个策略源的问题：

- 新增 `cache_unified.json.gz`，保存已聚合的统一股票池。
- 快照签名包含统一池策略集合、底层策略缓存签名、数据新鲜度和 schema 版本。
- 若执行模型、公式逻辑、底层缓存或最新数据日期变化，快照自动失效。
- 列表聚合读取历史缓存时跳过完整 `trade_series_json`，详情图表仍按需读取完整交易序列。
- 2026-05-19 验证结果：
  - 首次完整构建约 `134.3s`。
  - 第二个新进程从快照读取约 `0.638s`。
  - `/api/status`、`/api/unified`、`/api/chart/301511`、`/api/chart/688700` 均返回 `200`。

剩余风险：底层缓存或行情数据变化后的首次完整构建仍约 `134s`，后续应继续优化密集公式的当前信号计算。

详见 `analysis/unified_pool_performance_audit.md`。

2026-05-19 追加优化：

- 公式引擎滚动求和/高低点改为向量化实现。
- `巨量蓄势启动` 当前信号计算预先计算 MA10/MA20，避免内层循环重复计算。
- 当前状态 K 线窗口按公式缩短：
  - `巨量蓄势启动`：150 根。
  - `活跃度大牛突破`：90 根。
  - `GS回调确认` 保持 220 根，因为缩短窗口会改变当前持仓行数。
- 统一池构建并发硬上限设为 2，避免 DuckDB 并发读争用。
- 首次完整构建从约 `134s` 改善到 `83.1s`；快照读取约 `1.303s`。
- 推荐摘要保持不变：全池 `5201` 只，今日推荐 `97`，达标多策略共振 `56`。

剩余风险：首次构建仍不够快，后续应合并 MACD 多参数当前状态扫描，避免重复读取/计算相同 K 线。

2026-05-19 再追加优化：

- 当前状态最新 K 线查询增加进程内共享缓存，按查询窗口复用 DuckDB 结果数组。
- 统一池中 5 个 MACD/Optuna 策略不再重复读取同一批最新 K 线。
- 统一池快照 schema 升到 `3`，避免旧快照掩盖新当前状态路径。
- 首次完整构建从 `83.1s` 进一步降到 `50.3s`。
- 快照读取约 `0.62s`。
- 推荐摘要仍保持：全池 `5201` 只，今日推荐 `97`，达标多策略共振 `56`。

剩余风险：`巨量蓄势启动` 当前状态平台扫描仍约 `19s`，后续应先筛出近期疑似巨量平台股票，再运行完整平台结构扫描。

2026-05-19 第三次性能优化：

- `巨量蓄势启动` 当前路径增加 `latest_only` 内部模式，只查找统一列表需要的最近可行动信号；历史回测仍计算完整日信号序列。
- profile 显示该公式热点主要是短切片 `np.nanmean/nanmax/nanmin`，当前行情 OHLCV 不含 NaN，因此当前路径改用普通 `mean/max/min`。
- 统一池快照 schema 升到 `7`。
- `巨量蓄势启动` 当前状态从约 `19.5s` 降到 `11.1s`，行数和摘要保持不变：
  - 当前行数 `2361`。
  - 刚触发 `744`。
  - 持仓 `1617`。
- 首次完整统一池构建从 `50.3s` 降到 `43.9s`。
- 快照读取约 `0.584s`。
- 推荐摘要仍保持：全池 `5201` 只，今日推荐 `97`，达标多策略共振 `56`。

剩余风险：首次构建仍约 `44s`，后续可继续对 `巨量蓄势启动` 做向量化预筛，或进入详情页/策略研究视图完善。

2026-05-19 第四次性能优化：

- `巨量蓄势启动` 的平台扫描改为 spike 索引预筛，不再每个候选日重复 `np.where(spike[start:i])`。
- 横盘后半段均量、巨量日前均量、近 5 日均量改为前缀和区间均值。
- 平台高/低、突破参考高点改为区间 max/min 查询，避免重复短切片扫描。
- 语义保持不变，关键样例图表输出一致：
  - `301511`：`entry=13`，`exit=76`，平台点 `170/170`。
  - `301658`：`entry=2`，`exit=120`，平台点 `40/40`。
  - `688700`：`entry=4`，`exit=95`，平台点 `60/60`。
  - `002718`：`entry=17`，`exit=58`，平台点 `160/160`。
- `formula_volume_base_breakout` 单策略当前构建降到约 `5.9s`，摘要保持：
  - 当前行数 `2361`。
  - 刚触发 `744`。
  - 持仓 `1617`。
  - 今日候选 `32`。
  - 强推荐 `10`。
- 完整统一池首次构建降到 `34.34s`，推荐摘要保持：
  - 全池 `5201`。
  - 今日推荐 `97`。
  - 买入窗口 `2425`。
  - 达标多策略共振 `56`。
- 快照读取约 `0.955s`。

剩余风险：首次构建仍有约半分钟，后续性能优化应先重新 profile，避免继续围绕已经明显降低的旧热点做局部优化。

### 2.8 2026-05-19 详情页策略卡片补强

统一股票详情页的策略信号矩阵已补齐为更接近目标的“按股票聚合策略卡片”：

- 每个 `strategy_signal` 新增：
  - 买入价方法。
  - 卖出/评估日期和价格。
  - 最近参考收益和最大回撤。
  - 是否已到参考卖点。
  - 是否等待 T+1 买入。
  - 买入阻塞/等待原因。
- 策略卡片展示：
  - 当前状态。
  - 信号日期。
  - 买入日期/价格。
  - 卖出或评估日期/价格。
  - 最优持仓周期。
  - 最近收益。
  - 胜率。
  - 均收益。
  - 均回撤。
  - Calmar。
  - 策略有效性。
  - 信号数。
  - 优选参数变体、优选分和参数摘要。
  - 当前是否处于买入窗口。
- 统一池快照 schema 升到 `8`，避免旧缓存缺字段。
- 样例 `301511`、`301658`、`688700`、`002718` 均验证到新增字段。
- `/`、`/api/status`、`/api/unified`、`/api/chart/301511` 本地 HTTP 验证通过。

剩余风险：公式专属指标图仍未完成；目前还没有独立展示 GS `X3/X36`、均线 `MA5/MA90/MA145`、活跃度 `X15`、巨量平台区间等指标模块。

详见 `analysis/strategy_detail_cards_audit.md`。

### 2.9 2026-05-19 公式专属指标图

详情页第二张图已从硬编码 MACD 图改为通用策略指标图：

- `/api/chart/{code}` 返回 `indicator_chart`。
- MACD 策略返回 `DIF`、`DEA`、`MACD`。
- GS 策略返回 `X3`、`X36`、历史快买率。
- 均线筑底返回短/中/长均线。
- 活跃度返回 `X15`、强势线、大牛线。
- 巨量蓄势返回平台低、平台高，并对平台区间做可视化延展。
- `/api/chart/{code}` 返回 `signal_points`，公式策略的价格图标记改用公式买点，而不是 MACD 金叉。
- 前端指标图标题、图例、数据集都从 `indicator_chart` 动态渲染。

验证：

- `formula_gs_pullback_confirm` + `002718` 返回 `gs` 指标，含 `X3/X36/历史快买率`。
- `formula_ma_base_breakout` + `000004` 返回 `ma_base` 指标。
- `formula_activity_breakout` + `301511` 返回 `activity` 指标。
- `formula_volume_base_breakout` + `301511` 返回 `volume_base` 指标，平台高/低可见点各 `170` 个。
- `/`、`/api/status`、`/api/unified`、公式 chart 接口本地 HTTP 验证通过。

剩余风险：价格图图例文案仍偏通用，公式策略下后续应把“金叉/死叉”动态改成“公式买点/卖点”。

详见 `analysis/formula_indicator_chart_audit.md`。

2026-05-19 追加修正：

- 价格图图例已按策略类型动态切换。
- MACD 策略继续显示 `金叉/死叉`。
- 公式策略显示 `公式买点`，不再展示 `死叉` 文案。
- 公式买点在价格图上使用菱形标记。
- 策略有效性无样本文案从“无已完成金叉交易”改为“无已完成交易”。
- `/api/chart/301511?strategy=formula_volume_base_breakout` 与 `/api/chart/301511?strategy=tdx_12_26_9` 本地验证通过。

剩余风险：后续可进一步在价格图上直接标注巨量平台区间、GS 买卖点等更丰富的公式结构说明。

2026-05-19 再追加：

- 巨量蓄势策略下，价格图已根据 `indicator_chart` 的平台高/低数据填充 `平台区间`。
- 价格图图例在存在平台带时显示 `平台区间`。
- 该叠加只用于可视化，不改变公式信号、回测、推荐或交易生成。
- `301511 + formula_volume_base_breakout` 验证：平台高/低可见点各 `170` 个，公式信号点 `13` 个。

剩余风险：后续可再补单个突破日期的文字标签，但平台结构核心可视化已具备。

2026-05-19 第三次追加：

- `/api/chart/{code}` 已返回公式 `exit` 卖点，写入 `signal_points.type=exit`。
- 公式策略价格图同时显示 `公式买点` 与 `公式卖点`。
- 公式买点使用红色菱形，公式卖点使用绿色方块；MACD 策略仍保留金叉/死叉逻辑。
- `301511 + formula_volume_base_breakout` 验证：`entry=13`，`exit=76`。
- `002718 + formula_gs_pullback_confirm` 验证：`entry=3`，`exit=7`。
- `/`、`/api/status`、上述两个公式 chart 接口本地 HTTP 验证通过。

剩余风险：公式买卖点语义已补齐，后续只剩可选的单个突破/卖出日期文字标注。

### 2.10 2026-05-19 推荐层成交可行性过滤

今日推荐已补入“不可成交率不过高”的硬约束：

- 历史缓存新增 `execution` 统计：
  - 信号总数。
  - 已完成交易数。
  - 买入跳过数。
  - 等待买入数。
  - 买入顺延数。
  - 卖出顺延数。
  - 不可成交事件数。
  - 成交率。
  - 买入跳过率。
  - 不可成交率。
- 策略缓存 schema 与统一池快照 schema 升到 `9`。
- 策略信号卡片新增 `成交率` 与 `不可成交`。
- 今日推荐达标信号新增过滤：
  - `untradable_rate <= 0.20`。
- 策略评分对不可成交率做扣分。

重建和修正：

- schema 升级后已重建 5 个公式缓存。
- 重建过程中发现 `巨量蓄势启动` 历史路径错误使用了当前路径的 `__latest_only`，会导致历史信号数为 0。
- 已修复为：历史回测计算完整日信号序列，`__latest_only` 仅用于当前状态路径。
- `formula_volume_base_breakout` 重建后恢复 `stocks_with_signal=5131`。

公式缓存审计：

- `GS回调确认`：`stocks_with_signal=3801`，`avg_untradable_rate=0.012736`。
- `GS原始买点`：`stocks_with_signal=5131`，`avg_untradable_rate=0.016698`。
- `均线筑底突破`：`stocks_with_signal=905`，`avg_untradable_rate=0.055801`。
- `活跃度大牛突破`：`stocks_with_signal=5131`，`avg_untradable_rate=0.025192`。
- `巨量蓄势启动`：`stocks_with_signal=5131`，`avg_untradable_rate=0.015992`。

统一池验证：

- 首次 schema 9 全策略重建耗时 `195.666s`。
- 快照读取 `0.84s`。
- 全池 `5201`。
- 今日推荐从 `97` 降到 `91`。
- 买入窗口 `2425`。
- 达标多策略共振从 `56` 降到 `54`。
- 普通当前多家族命中 `4439`。
- `301511`、`301658`、`688700`、`002718` 均未被误推荐。

剩余风险：`untradable_rate <= 0.20` 是第一版统一阈值，后续可根据真实推荐样本把阈值改成按策略/板块自适应；schema 9 首次重建较慢，但后续快照读取仍低于 1 秒。

### 2.11 2026-05-19 策略研究摘要

策略研究入口先以轻量摘要形式落地，不再只是展示参数寻优前三名：

- `/api/parameter-search` 合并返回：
  - `formula_variant_metrics.csv` 的参数变体表现。
  - `formula_parameter_search_summary.csv` 的公式级回测摘要。
  - `execution_model_audit.csv` 的执行模型审计计数。
- 前端参数寻优区域改为 `策略研究 · 参数寻优与回测审计`。
- 每个公式卡片展示：
  - 缓存是否就绪。
  - 有历史信号股票覆盖率。
  - 平均不可成交率。
  - 平均胜率。
  - 平均收益。
  - 汇总成交率。
  - 前 3 个参数变体。
  - 变体买入顺延率。

接口验证：

- `/api/parameter-search` 返回 `5` 个公式、`114` 组回测指标。
- 公式覆盖和不可成交率：
  - `activity_breakout`：`5131/5201`，`0.025192`。
  - `gs_pullback_confirm`：`3801/5201`，`0.012736`。
  - `gs_raw_buy`：`5131/5201`，`0.016698`。
  - `ma_base_breakout`：`905/5201`，`0.055801`。
  - `volume_base_breakout`：`5131/5201`，`0.015992`。

剩余风险：当前策略研究仍是摘要卡片，不是完整独立 tab 和可排序明细表；如果后续需要逐公式深挖，可继续补专用研究表。

详见 `analysis/strategy_research_audit.md`。

2026-05-19 追加：

- `/api/parameter-search` 已返回每个公式的全部参数变体 `variants`，不再只返回 top。
- 策略研究区域新增参数变体明细表。
- 明细表展示：
  - 公式。
  - 变体。
  - 持仓期。
  - 评分。
  - 交易数。
  - 胜率。
  - 均收益。
  - 均回撤。
  - Calmar。
  - 买入顺延率。
  - 卖出顺延率。
  - 参数摘要。
- `/api/parameter-search` 验证：`5` 个公式、`114` 组回测指标，`variant_total=114`。

剩余风险：明细表目前按评分排序但还不能点击列头排序；数据已经完整暴露，后续主要是交互便利性。

2026-05-19 再追加：

- 参数变体明细表已支持点击列头排序。
- 可排序字段包括：
  - 公式。
  - 变体。
  - 持仓期。
  - 评分。
  - 交易数。
  - 胜率。
  - 均收益。
  - 均回撤。
  - Calmar。
  - 买入顺延率。
  - 卖出顺延率。
- 排序只重绘策略研究区域，不影响统一股票主列表。

剩余风险：参数寻优行已完整并可交互审计；后续更深的策略研究工作应转向卖出规则对比和每股局部优化表。

2026-05-19 第三次追加：

- 新增 `scripts/formula_sell_rule_audit.py`。
- 新增 `analysis/formula_sell_rule_audit.csv` 与 `analysis/formula_sell_rule_audit.md`。
- 卖出规则第一版审计比较：
  - 固定持仓 `5/10/15/20/30/60`。
  - 公式自身 `exit` 卖点，最多持仓 `60` 个交易日。
- `/api/parameter-search` 已返回每个公式的 `sell_rules` 与 `best_sell_rule`。
- 策略研究卡片展示最佳卖出规则、评分、胜率、均收益和交易数。
- 全市场审计结果：
  - `activity_breakout`：`fixed_60`，score `44.819891`。
  - `gs_pullback_confirm`：`fixed_60`，score `46.568716`。
  - `gs_raw_buy`：`fixed_60`，score `47.146118`。
  - `ma_base_breakout`：`fixed_60`，score `23.074084`。
  - `volume_base_breakout`：`fixed_60`，score `42.918617`。

剩余风险：卖出规则对比已经有审计产物和 UI 摘要，但生产推荐缓存仍使用固定持仓口径；后续需要把每股/每公式最佳卖出规则接入缓存和策略卡片。

## 3. 统一成交价格与可交易性模型

当前回测不能再简单使用收盘价。所有 MACD 与通达信公式统一采用“可成交 VWAP 模型”。

### 3.1 买入规则

- 信号日不买入。
- 默认 `T+1` 买入。
- 买入价采用买入日当日 VWAP。
- 若买入日停牌、涨停、一字涨停，则不能买入。
- 对不能买入的信号提供两种可回测模式：
  - `skip`：信号作废。
  - `delay_n`：最多顺延 N 个交易日，直到出现可买日，否则信号作废。
- 第一版默认：`delay_n = 3`，同时记录被阻塞次数。

### 3.2 卖出规则

- 固定持仓到期、公式卖点、止盈止损、均线破位等卖出触发后，卖出价采用触发日 VWAP。
- 若卖出日停牌、跌停、一字跌停，则不能卖出，顺延到下一个可卖日。
- 一字涨停时卖出通常可成交，但为了保守，仍以 VWAP 或收盘价模型执行，不按理想目标价虚构成交。

### 3.3 VWAP 计算

参考 `chunkymonkey` 的只读实现思路，bestchoice 内部单独实现：

```text
候选1：amount / (volume * 100)  # volume 为手
候选2：amount / volume          # volume 为股
若候选值与 close 的比值在 [0.5, 1.5] 内，则认为合理。
优先选择合理候选；若都不合理，fallback close，并记录 pricing_method。
```

每笔交易记录必须包含：

- `signal_date`
- `planned_buy_date`
- `actual_buy_date`
- `buy_price`
- `buy_price_method`
- `buy_block_reason`
- `planned_sell_date`
- `actual_sell_date`
- `sell_price`
- `sell_price_method`
- `sell_block_reason`
- `delay_buy_days`
- `delay_sell_days`
- `holding_days_actual`
- `ret`
- `max_dd`
- `tradability_flags`

### 3.4 涨跌停与停牌识别

板块静态规则：

- 主板 `60/00`：±10%
- 创业板 `30/301`：±20%
- 科创板 `688/689`：±20%
- 北交所 `4/8/9`：±30%
- ST 先不做完整识别，后续可接入名称或上市状态表。

识别：

- `volume <= 0` 或 `amount <= 0` 或 `close <= 0`：停牌/不可交易。
- 买入日涨停：不能买入。
- 卖出日跌停：不能卖出。
- OHLC 全相等且达到涨跌停：一字板，单独标记。

## 4. MACD 趋势策略重构

### 4.1 内部候选参数

候选 MACD：

```text
EMA(10,22,8)
EMA(12,26,9)
EMA(14,30,11)
Optuna 全局参数
后续每股 Optuna 局部参数
```

候选过滤阈值：

- `amt_ratio_min`：0.8、1.0、1.2、1.5、2.0
- `vol_ratio_min`：0.8、1.0、1.2、1.5、2.0
- `price_pos_max`：0.50、0.60、0.70、0.80、1.00
- `dif_positive`：true / false

候选持仓周期：

```text
5, 10, 15, 20, 30, 45, 60
```

候选卖出规则：

- 固定持仓周期
- MACD 死叉卖出
- 固定持仓 + 止损
- 固定持仓 + 止盈
- 固定持仓 + 移动止盈

第一版先实现：

```text
固定持仓周期 + VWAP 成交 + 涨跌停/停牌顺延
```

### 4.2 每股最优 MACD 选择

每只股票对所有 MACD 候选方案独立回测，生成 `variant_metrics`。

选择最优方案时不能只看均收益，综合考虑：

- 样本数
- 胜率
- 均收益
- 均回撤
- Calmar
- 最近一年样本
- 策略有效性评分
- 样本是否陈旧
- 不可成交率
- 当前是否存在可执行买点或持仓机会

推荐评分：

```text
score =
30% 策略有效性
25% Calmar / 收益回撤比
20% 胜率
15% 最近一年表现
10% 当前信号质量
- 不可成交率惩罚
- 样本不足惩罚
- 样本陈旧惩罚
```

输出字段：

- `best_engine = macd`
- `best_variant_id`
- `best_variant_name`
- `best_macd_fast`
- `best_macd_slow`
- `best_macd_signal`
- `best_holding_days`
- `best_sell_rule`
- `best_score`
- `best_reason`
- `variant_metrics[]`
- `trade_series`
- `holding_intervals`

### 4.3 MACD 列表页

默认列表字段：

- 股票代码/名称
- 当前信号
- 推荐动作
- 最优方案
- 买入日
- 买入价 VWAP
- 计划卖出日
- 实际卖出日
- 最近收益
- 胜率
- 均收益
- 均回撤
- 最优持仓
- 有效性评分
- 不可成交提示

### 4.4 MACD 明细页

模块：

1. 顶部结论卡片
   - 推荐动作
   - 最优参数
   - 最优持仓周期
   - 买入价/买入日
   - 卖出价/卖出日
   - 最近收益
   - 有效性评分

2. 价格走势
   - 最近一年。
   - 按明确买入到卖出区间填充红/绿。
   - 金叉/死叉只作为辅助标记。

3. MACD 图
   - 默认展示最优参数方案。
   - 可展开查看其它参数方案。

4. 参数对比表
   - 每个候选方案的样本数、胜率、均收益、均回撤、Calmar、有效性、不成交率、当前信号。

5. 回测交易
   - 最近一年样本图。
   - 无样本时显示明确提示。

## 5. 通达信选股公式独立化

### 5.1 GS回调确认

来源：用户“左侧GS选股公式”。

核心：

- 构建 `X3` 与 `X36`。
- `GSB = CROSS(X36, X3)`。
- `GSS = CROSS(X3, X36)`。
- 筛选条件：
  - `INSELL`
  - `HISTOK`
  - `SELLQUAL`
  - `GREENOK`
  - `UP`
  - `PULL`

参数可探索：

- `insell_days`：1、2、3、5
- `hist_window`：60、90、120
- `rate_min`：30、40、50、60
- `maxrun_max`：6、8、10、12
- `sellpct_max`：50、60、70
- `maxlen_max`：15、20、30
- `longb_max`：1、2、3
- `ma_pull_low`：0.70、0.78、0.85
- `ma_pull_high`：0.98、0.995、1.02
- `m60_buffer`：0.95、0.97、1.00

买点：

- 公式 `XG` 成立日。
- 回测 T+1 VWAP 买入。

卖点候选：

- 固定持仓周期
- `GSS` 卖出
- 跌破 MA20
- 跌破 MA60
- 止损/止盈

第一版：固定持仓周期寻优 + 可选 `GSS` 卖出对比。

### 5.2 GS原始买点

来源：用户“gs买卖点选股”。

核心：

```text
X44 = CROSS(X36, X3)
```

买点：

- `CROSS(X36, X3)`。

卖点：

- 首选 `CROSS(X3, X36)`，即 GS 卖点。
- 同时对固定持仓周期做对比。

参数可探索：

- `x3_ma_windows`：
  - 原始：3、7、13、27
  - 探索：3/5/10/20、5/10/20/30、3/8/13/34
- `ema_fallback_span`：3、5、8
- `down_adjust`：0.96、0.97、0.98、0.99
- `up_adjust`：1.01、1.02、1.03、1.04
- `iterations`：6、8、10、12

### 5.3 均线筑底突破

来源：用户“筑底90 145公式”。

核心：

- MA5 长期低于 MA90。
- 收盘突破 MA145。
- 站稳 MA145。
- MA90/MA145 无近期交叉。
- 流通市值不低于阈值。

参数可探索：

- `short_ma`：3、5、8
- `mid_ma`：60、90、120
- `long_ma`：120、145、180
- `below_days_min`：30、45、60
- `ma5_rising_count_window`：8、10、15
- `ma5_rising_min`：5、7、10
- `breakout_lookback`：5、10、15
- `price_top_buffer`：1.03、1.06、1.10
- `price_long_ma_buffer`：1.05、1.10、1.15
- `mcap_min`：20亿、30亿、50亿

买点：

- 公式成立日。
- T+1 VWAP 买入。

卖点候选：

- 固定持仓周期
- 跌破 MA145
- 跌破 MA20
- 止损/止盈

### 5.4 活跃度大牛突破

来源：用户“活跃度选股”。

核心：

- 构造日内活跃度 `X15`。
- `突破 = CROSS(X15, 大牛线)`。

参数可探索：

- `生命线`：1.2、1.56、2.0
- `强势线`：2.5、3.0、4.0
- `大牛线`：5.0、6.0、7.0、8.0
- `x15_multiplier`：1.0、1.2、1.5
- `short_return_window`：3、4、5
- `short_return_threshold`：20、23、25、30
- `limit_like_count_window`：3、5
- `limit_like_min_count`：1、2
- `active_days_window`：4、5、8
- `active_days_min`：3、4、5

买点：

- `CROSS(X15, 大牛线)`。
- T+1 VWAP 买入。

卖点候选：

- 短周期固定持仓：3、5、8、10、15
- 冲高回落卖
- 止盈/止损
- 跌破触发日低点

### 5.5 巨量蓄势启动

来源：根据用户对 `301511 德福科技` 的结构描述新设计。

目标结构：

1. 前置巨量换手。
2. 巨量后缩量横盘。
3. 平台不破。
4. 温和放量启动。

初版公式：

```text
VOLUME_SPIKE:
    过去 30~60 日内存在 volume >= MA20(volume) * spike_ratio
    或 amount >= MA20(amount) * amount_spike_ratio

BASE_AFTER_SPIKE:
    巨量日后横盘至少 base_min_days
    横盘区间振幅 <= base_range_max
    当前 close >= spike_close * base_floor
    当前 close <= spike_high * base_ceiling

DRY_UP:
    横盘后半段均量 <= 巨量日前后均量 * dry_ratio
    或 MA10(volume) < MA20(volume)

WARM_REACCUMULATION:
    最近 5 日均量 > MA20(volume) * warm_vol_ratio
    最近 5 日涨幅在 [warm_ret_min, warm_ret_max]
    close 突破近 20 日平台高点
    或 close > MA20 且 MA20 上行
```

参数可探索：

- `spike_lookback`：30、45、60、90
- `spike_ratio`：2.5、3.0、4.0、5.0
- `amount_spike_ratio`：2.5、3.0、4.0
- `base_min_days`：10、15、20、30
- `base_max_days`：45、60、90
- `base_range_max`：0.20、0.25、0.30、0.35
- `base_floor`：0.80、0.85、0.90
- `base_ceiling`：1.15、1.25、1.35
- `dry_ratio`：0.35、0.45、0.55
- `warm_vol_ratio`：1.10、1.15、1.30、1.50
- `warm_ret_min`：0.02、0.03、0.05
- `warm_ret_max`：0.20、0.25、0.30
- `breakout_window`：10、20、30

买点：

- 平台后首次温和放量突破日。
- T+1 VWAP 买入。

卖点候选：

- 固定持仓周期：10、15、20、30、45、60
- 跌破平台下沿
- 跌破 MA20
- 止盈/止损

验证样例：

- `301511 德福科技`
- 目标：识别 2026 年 4 月初附近买点。

## 6. 回测与参数寻优

### 6.1 回测范围

- 默认全历史。
- 同时输出最近一年窗口表现。
- 图表展示固定最近一年。
- 对每个股票、每个公式、每个参数组独立计算。

### 6.2 训练/验证原则

- 不使用未来函数。
- 信号日只用当日收盘后可知数据。
- 交易日为 T+1 或顺延后的可交易日。
- 价格使用 VWAP 可成交模型。
- 每笔交易记录实际买卖日期和阻塞原因。

### 6.3 参数寻优模式

第一阶段用网格搜索：

- 公式自身参数网格。
- 持仓周期网格。
- 卖出规则网格。
- 可交易性模型固定。
- 第一阶段不能先人为把公式收窄到只剩少数股票。
- 正确顺序是：
  - 先尽量忠实实现原始公式或案例公式，让它能产生日信号并选出股票。
  - 再用全历史回测验证公式是否有效。
  - 再比较不同参数变体、持仓周期和卖出规则。
  - 最后由回测质量、当前买入窗口、多策略共振和成交可行性决定是否进入今日推荐。
- 参数搜索可以包含更宽和更窄的参数变体，但这些变体必须作为回测对照，不应在验证公式有效性之前被写死为唯一口径。
- 若某个公式全市场信号过密，先记录信号密度和交易数量；必要的冷却期或信号上限只能作为运行保护或可比较参数变体，不能替代公式有效性验证。
- 参数搜索输出必须包含每个变体的信号密度/交易数量，用于判断公式过宽还是过严。

第二阶段引入 Optuna：

- 每个公式全局 Optuna。
- 每个股票局部 Optuna。
- 参数空间以本文给出的范围为基础，不局限于原始通达信固定值。

### 6.4 每股最优选择

类似 MACD 模板，每只股票在每个公式下输出：

- `best_formula_variant`
- `best_params`
- `best_sell_rule`
- `best_holding_days`
- `best_score`
- `best_trade_series`
- `best_current_signal`
- `best_action`
- `best_reason`

评分因子：

- 样本数
- 胜率
- 均收益
- 均回撤
- Calmar
- 最近一年表现
- 不可成交率
- 最大单笔依赖度
- 收益稳定性
- 当前触发质量

### 6.6 公式验证与优选原则

所有公式先以“能否稳定产生可解释信号”为第一目标，再用回测筛出有效股票。

验证规则：

- `GS回调确认`：先验证原始条件能否产生合理回调确认信号，再比较 `rate_min`、`sellpct_max`、`ma_pull_low/ma_pull_high` 等参数。
- `GS原始买点`：原始信号本身敏感，应保留原始买点序列；冷却期只作为参数变体参与回测，不默认屏蔽信号。
- `均线筑底突破`：先验证 MA5/MA90/MA145 原始结构，再比较不同均线周期和突破窗口。
- `活跃度大牛突破`：先验证 `X15` 突破大牛线的原始信号，再用回测判断是否需要涨幅边界、冷却期或更高阈值。
- `巨量蓄势启动`：以 301511 案例为模板，先保证能识别巨量后横盘、缩量、温和放量启动的结构；两个月箱体、冷却期、箱体振幅等都作为可探索参数变体，由回测决定优劣。
- 今日推荐不能直接等于公式命中；推荐层必须叠加历史有效性、当前窗口、成交可行性和必要的多策略共振。

### 6.5 策略有效性

沿用并扩展当前“策略有效性”思路：

- 最近 N 笔 vs 历史 prior。
- 最近一年是否有样本。
- 是否样本陈旧。
- 是否退化。
- 是否由极端单笔收益支撑。
- 不可成交率惩罚。
- 顺延卖出造成的损失惩罚。

## 7. 数据结构设计

### 7.1 Strategy Profile

```json
{
  "strategy_id": "formula_gs_pullback_confirm",
  "engine": "tdx_formula",
  "display_name": "GS回调确认",
  "formula_id": "gs_pullback_confirm",
  "params": {},
  "execution_model": "vwap_tradable_v1"
}
```

MACD：

```json
{
  "strategy_id": "macd_integrated",
  "engine": "macd_integrated",
  "display_name": "MACD 趋势策略",
  "candidate_variants": ["tdx", "s", "m", "l", "optuna"]
}
```

### 7.2 Formula Signal

```json
{
  "stock_code": "301511",
  "formula_id": "volume_base_breakout",
  "signal_date": "2026-04-xx",
  "signal_strength": 0.82,
  "reason_codes": [],
  "condition_details": {}
}
```

### 7.3 Backtest Trade

```json
{
  "signal_date": "2026-04-xx",
  "planned_buy_date": "2026-04-xx",
  "actual_buy_date": "2026-04-xx",
  "buy_price": 28.33,
  "buy_price_method": "vwap_lot",
  "planned_sell_date": "2026-05-xx",
  "actual_sell_date": "2026-05-xx",
  "sell_price": 34.52,
  "sell_price_method": "vwap_lot",
  "ret": 0.2185,
  "max_dd": -0.052,
  "delay_buy_days": 0,
  "delay_sell_days": 0,
  "blocked_reasons": []
}
```

## 8. 前端展示方案

### 8.0 统一股票列表

最终主界面优先采用一份统一股票列表，而不是 MACD 一个列表、每个公式一个列表。

列表层负责回答：

- 哪些股票现在值得看。
- 为什么值得看。
- 是单策略触发还是多策略共振。
- 当前处于买入、观察、持仓、卖出、等待中的哪个阶段。
- 历史上这只股票在这些策略下是否真的有效。

主筛选：

```text
今日推荐
买入窗口
持仓观察
全部股票
```

策略筛选作为辅助条件：

```text
MACD
GS回调确认
GS原始买点
均线筑底突破
活跃度大牛突破
巨量蓄势启动
```

这表示“在统一股票池里筛选命中某策略的股票”，不是切换到另一套独立股票列表。

### 8.1 通达信公式列表页

该页面降级为策略研究视图，主要用于检查某个公式本身是否可靠，不作为日常主入口。

字段：

- 股票
- 命中公式
- 触发日
- 推荐动作
- 买入状态
- 买入价 VWAP
- 计划卖出日
- 实际卖出日
- 最近收益
- 胜率
- 均收益
- 均回撤
- 最优持仓
- 最优卖出规则
- 有效性评分
- 触发理由

### 8.2 通达信公式明细页

在统一股票详情页内以“策略卡片”的形式展示，不建议弹出完全独立的公式详情页。

模块：

1. 顶部结论。
2. 触发条件拆解。
3. 价格走势图：
   - 公式触发点。
   - 买入点。
   - 卖出点。
   - 红绿持仓填充。
   - 巨量/平台/突破区间标注。
4. 公式指标图：
   - GS 类显示 `X3/X36`。
   - 均线类显示 MA5/MA90/MA145。
   - 活跃度类显示 `X15/生命线/强势线/大牛线`。
   - 巨量蓄势显示量能、平台区间和突破。
5. 参数寻优表。
6. 回测交易图。
7. 不可成交统计。

### 8.3 MACD 明细页

保持当前页面风格，但改为一个整体策略：

- 默认显示最优 MACD 方案。
- 参数组对比放在折叠区。
- 不再要求用户单独切换“参数组 M / 通达信 / 基准”。

### 8.4 统一股票详情页

股票详情页按股票聚合，而不是按策略割裂。

顶部：

- 股票代码/名称/行业。
- 当前综合动作。
- 最强触发策略。
- 多策略命中数量。
- 推荐买入日/买入价/持仓周期/目标卖出日。
- 综合评分。

策略卡片区：

- MACD 趋势策略卡。
- GS回调确认卡。
- GS原始买点卡。
- 均线筑底突破卡。
- 活跃度大牛突破卡。
- 巨量蓄势启动卡。

每张卡展示：

- 当前状态。
- 最近信号日期。
- 最近买入/卖出价格。
- 最优参数。
- 最优持仓周期。
- 胜率、均收益、均回撤、有效性评分。
- 是否进入当前买入窗口。

图表区：

- 价格走势最近一年。
- 多策略买卖点时间轴。
- 当前选中策略的指标图。
- 策略有效性图。

## 9. 实施阶段

### Phase 0：准备与防回归

- 固化当前页面可用状态。
- 增加公式和执行模型单元测试。
- 确保不修改 `chunkymonkey`。
- 为缓存 schema 升级做版本号。

### Phase 1：统一执行模型

- 在 bestchoice 内实现 VWAP 计算。
- 实现涨跌停、停牌、一字板识别。
- 改 MACD 回测买卖价为 VWAP。
- 记录不可成交与顺延。
- 更新价格图和交易明细。
- 回归验证 301658、688700、002718。

### Phase 2：公式注册与信号计算

- 新增公式：
  - `GS回调确认`
  - `GS原始买点`
  - `均线筑底突破`
  - `活跃度大牛突破`
  - `巨量蓄势启动`
- 旧 F1/F3/F5 映射到新名称。
- 每个公式生成历史信号序列。
- 对 301511 检查 `巨量蓄势启动` 是否在 2026 年 4 月初附近触发。

### Phase 3：公式回测

- 每个公式独立回测。
- 使用 VWAP + 可交易性模型。
- 支持固定持仓、公式卖点、均线破位、止盈止损。
- 输出全历史和最近一年表现。
- 图表无样本时给文字提示。

### Phase 4：参数探索与寻优

- 对每个公式跑参数网格搜索。
- 对持仓周期和卖出规则寻优。
- 对每只股票选择最佳参数与最佳卖出规则。
- 生成 `formula_variant_metrics` 与 `stock_formula_best`。

### Phase 5：MACD 整合

- 将 MACD 多参数合并为一个 `MACD 趋势策略`。
- 每只股票自动选择最优 MACD 参数/持仓周期。
- 前端移除一排 MACD 参数策略按钮，改为内部对比。
- 保留参数对比表。

### Phase 6：前端重构

- 主入口改为统一股票池：
  - `今日推荐`
  - `买入窗口`
  - `持仓观察`
  - `全部股票`
- 增加 `策略研究` 视图，用于查看单策略回测、参数寻优和公式调试。
- 列表按股票聚合展示 `strategy_signals[]`，不再把每个公式做成最终主列表。
- 明细页按股票聚合展示全部策略卡片。
- 打印仍只打印股票详情页。
- 自动数据健康检查继续保留。

### Phase 7：验收

验收样例：

- `301511 德福科技`：验证巨量蓄势启动能否捕捉 2026 年 4 月初附近买点。
- `301658`：验证持仓填充按明确买入/卖出区间。
- `688700`：验证最近一年无样本时提示。
- `002718`：验证最近收益与均收益表达不混淆。

验收指标：

- 所有公式都有全历史回测。
- 所有公式都有每股最优参数与持仓周期。
- 所有策略都使用 VWAP 可成交模型。
- 一字板/涨跌停/停牌不会产生虚假成交。
- 页面可自动识别数据更新并重算。

## 10. 注意事项

- 不要把通达信公式继续当成 MACD 过滤器。
- 不要把金叉/死叉作为公式策略的解释核心，除非公式本身包含 MACD 条件。
- 不要再使用纯收盘价作为回测成交价。
- 不要因为某个参数组合收益最高就直接选它，要惩罚样本不足、不可成交、极端单笔依赖、回撤过大。
- `chunkymonkey` 只读参考，不做任何修改。

## 11. 执行口径

后续使用 `/goal` 执行时，按“先后端正确性，后前端展示”的顺序推进，不只做页面改名。

### 11.0 当前代码风险基线

执行前必须先承认并修复以下现状，不能在旧语义上继续叠页面：

- 当前 `FORMULA_PROFILES` 本质仍是 `MACD 回测 + 最新公式命中过滤`。
- 当前公式命中只读取每只股票最新筛选结果，不能支撑历史逐日回测。
- 当前历史回测、当前预测、图表局部交易各有一套交易生成逻辑，买卖价格、持仓区间、收益口径容易不一致。
- 当前图表可重新局部生成交易，同时又读取缓存 `trade_series`，存在标记与持仓填充不一致风险。
- 当前缓存签名主要覆盖 MACD/持仓/量价参数，对公式规则、公式参数、执行模型版本不够敏感。

因此第一步不是改标签，而是抽出统一信号与交易执行层。

### 11.1 必须落地的后端能力

- 统一 `execution_model`：
  - MACD 与全部公式共用同一套 VWAP、涨跌停、停牌、一字板、顺延成交逻辑。
  - 回测、当前预测、走势图持仓区间全部使用同一套交易结果，不允许各算各的。
- 统一 `signal_engine`：
  - MACD 信号来自 MACD 金叉及其过滤条件。
  - 通达信公式信号来自各自公式的逐日命中结果。
  - 公式策略不能再依赖 MACD 金叉作为入场核心，除非该公式自身明确包含 MACD 条件。
- 公式策略引擎：
  - 每个公式都能产出历史信号、当前信号、触发解释和可视化指标序列。
  - 旧 `F1/F3/F5` 编号只能作为兼容字段，页面展示必须使用中文策略名称。
- 公式历史命中：
  - 新增日期级公式信号加载或本地重算能力。
  - 若 `SMART_DB.mart_stock_screening` 只保存命中日期，则未出现日期默认视为未命中，并在报告里说明数据语义。
  - 如果现有表无法支持新增公式历史信号，必须在 bestchoice 内根据 K 线逐日重算公式。
- 回测引擎：
  - 每个股票、每个公式、每个候选参数组、每个候选卖出规则都独立回测。
  - 输出全历史指标和最近一年指标。
  - 输出交易明细，用于策略有效性柱状图和价格走势图持仓填充。
- 参数探索：
  - 第一版用网格搜索覆盖本文给出的参数空间。
  - 参数空间允许扩展，不局限于用户原始公式里的固定数值。
  - 每个公式必须给出全局最优参数、每股最优参数、最优持仓周期、最优卖出规则。
- 缓存与重算：
  - 缓存 schema 增加版本号。
  - 修改执行模型、公式逻辑、参数空间后必须强制失效旧缓存。
  - 数据更新到新交易日后，页面健康检查能触发重新计算。
  - 统一股票池预热必须有并发上限，默认最多同时计算 2 个策略，避免全量公式回测阻塞 `/api/status` 和页面健康检查。
  - 后台预热只预热统一股票池需要的策略，旧 MACD 参数组和单公式研究视图按需计算。
  - 缓存签名至少包含：
    - `engine`
    - `signal_source`
    - `entry_rule`
    - `formula_id`
    - `formula_params`
    - `sell_rule`
    - `holding_days`
    - `execution_model_version`
    - `data_latest_date`

### 11.2 必须落地的前端能力

- 主入口调整为统一股票池：
  - `今日推荐`
  - `买入窗口`
  - `持仓观察`
  - `全部股票`
- 策略维度作为筛选和明细信息：
  - `MACD`
  - `GS回调确认`
  - `GS原始买点`
  - `均线筑底突破`
  - `活跃度大牛突破`
  - `巨量蓄势启动`
- `策略研究` 可以保留单策略视图，用于参数寻优、公式验证、调试，但不作为最终日常主列表。
- MACD 不再把 `通达信/基准/S/M/L/Optuna` 作为用户主切换入口。
- 通达信公式按中文策略名称展示，不显示 `F1/F3/F5` 作为主要概念。
- 股票详情页按当前 engine 展示：
  - MACD：MACD 参数、DIF/DEA、金叉死叉、最优交易。
  - GS：`X3/X36`、GSB/GSS、买卖点。
  - 均线筑底：MA5/MA90/MA145、突破和站稳情况。
  - 活跃度：X15、生命线、强势线、大牛线。
  - 巨量蓄势：巨量日、平台区间、缩量、温和放量、突破点。
- 三张图固定最近一年自然月窗口：
  - 价格走势
  - 指标图
  - 策略有效性
- 最近一年没有回测样本时，图内显示明确说明，不展示无解释空白图。
- 红绿持仓填充只能来自实际买入日至实际卖出日，不能来自金叉死叉区间。
- 打印功能只打印股票详情页，并尽量保持所见即所得。

统一股票池的数据要求：

- 每只股票返回 `strategy_signals[]`。
- 每个 `strategy_signal` 包含策略名、当前状态、信号日期、买入日、卖出日、最优持仓、胜率、均收益、均回撤、有效性评分。
- 每只股票返回 `best_signal`，代表当前最值得关注的策略触发。
- 每只股票返回 `confluence_score`，代表多策略共振程度。
- 每只股票返回 `today_recommend_reason`，解释为什么进入或没有进入今日推荐。

### 11.3 探索性分析产物

执行过程中要生成可审计的分析产物，放在 `analysis/` 下：

- `formula_parameter_search_summary.csv`
  - 每个公式的候选参数组合数量、最佳参数、样本数、胜率、均收益、均回撤、Calmar、不可成交率。
- `formula_stock_best_params.csv`
  - 每只股票在每个公式下的最优参数、最优持仓周期、最优卖出规则、评分。
- `execution_model_audit.csv`
  - 买入受阻、卖出受阻、停牌、涨停、一字板、VWAP fallback 的统计。
- `strategy_rebuild_report.md`
  - 本轮实施内容、数据覆盖日期、缓存版本、关键样例验证结论、残留风险。

这些产物不是页面功能的替代品，而是用于验证公式参数探索和回测结果是否可信。

### 11.4 关键样例验收

执行完成后必须至少检查：

- `301511 德福科技`
  - `巨量蓄势启动` 能否在 2026 年 4 月初附近给出合理买点。
  - 若没有命中，报告是哪条条件阻断，并调整参数探索空间后再次回测。
- `301658`
  - 价格走势图持仓填充必须严格按实际买入/卖出区间。
  - 空仓期不允许填充颜色。
- `688700`
  - 策略有效性最近一年无样本时必须显示文字提示。
  - 有样本时横坐标显示年月。
- `002718`
  - `最近收益` 与 `均收益` 表达不能混淆。
  - 详情页顶部收益字段必须能追溯到最近一笔交易。
- `000571`、`002501`、`002691`、`600273`
  - 今日候选不能只因命中公式就进入列表。
  - 若胜率低、均收益负、评分低，必须解释进入原因或被过滤。

### 11.5 完成标准

只有同时满足以下条件，才视为 `/goal` 执行完成：

- 后端能完整重算 MACD 与全部通达信公式策略。
- 每只股票都能得到对应策略下的最优参数、最优持仓周期和回测指标；没有样本时有明确原因。
- 页面列表、详情、图表、打印都能正常使用。
- 至少通过 `python -m py_compile compute.py main.py` 和新增脚本/测试的语法检查。
- 本地服务接口 `/api/status`、`/api/strategies`、`/api/data`、`/api/chart/{code}` 可用。
- 生成 `analysis/strategy_rebuild_report.md` 并记录验收样例结果。

## 12. 推荐实施顺序

为了降低重构风险，实际开发按以下顺序落地：

1. 抽出 `execution_model`
   - 实现 VWAP、可买、可卖、顺延、交易明细。
   - 先替换 MACD 历史回测，保证原策略可跑。
2. 抽出 `signal_engine`
   - MACD 先作为第一个信号源接入。
   - `compute_historical()` 改为消费标准 `Signal` 列表，而不是内部固定找金叉。
3. 统一交易生成
   - `compute_historical()`、`compute_current()`、`get_chart_data()` 全部复用同一交易构造函数。
   - 图表持仓区间只来自交易明细。
4. 实现公式逐日信号
   - 先接入旧 F1/F3 的本地逐日计算。
   - 再新增 `GS原始买点`、`活跃度大牛突破`、`巨量蓄势启动`。
5. 实现公式回测和参数网格
   - 每个公式先跑基础参数。
   - 再扩展参数网格与持仓/卖出规则寻优。
6. 实现 MACD 整合策略
   - 内部比较多组 MACD 参数、持仓周期、成交结果。
   - 前端只把 MACD 暴露为统一股票池中的一个策略信号。
7. 前端重构
   - 统一股票列表。
   - 策略研究视图。
   - 股票详情策略卡片、图表、打印、空样本提示。
8. 验收与报告
   - 跑关键样例。
   - 生成分析 CSV 和 `strategy_rebuild_report.md`。
   - 清理或标记旧缓存。

## 13. 2026-05-20 当前参数寻优进展

- 公式策略参数寻优已完成到“参数变体 + 卖出规则”层：`analysis/formula_variant_metrics.csv` 覆盖 5 个公式、19 个参数变体、228 组参数/卖出规则组合。
- 每股公式最优参数与卖出规则已生成：`analysis/stock_formula_best.csv` 供统一池读取，`analysis/formula_stock_best_params.csv` 供审计查看。
- 公式生产回测已改为按 `(stock_code, formula_id)` 读取每股最优参数和最优持股周期，不再使用 profile 固定持股周期伪装为公式结果。
- 缺失每股寻优结果时，公式生产路径标记 `missing_optimized_result` / `optimization_missing=True`，并给出“缺少每股公式参数寻优结果，未使用默认参数回退”，不回退默认参数。
- 卖出规则审计已完成：`analysis/formula_sell_rule_audit.csv` 比较固定持仓 5/10/15/20/30/60 天与公式退出信号。
- 生产交易生成已支持按卖出规则执行：
  - `fixed_N`：按每股最优持股周期卖出。
  - `formula_exit_or_N`：买入后遇到公式退出信号提前卖出，否则最多持有 N 天。
- 当前每股最优结果的卖出规则按股票写入 `fixed_N` 或 `formula_exit_or_N`，不再用全市场 `fixed_60` 覆盖每股结果。
- 详情页策略卡片已展示“卖出规则”，策略研究接口 `/api/parameter-search` 返回 5 个公式、228 条参数/卖出规则指标。
- 策略研究参数明细表已展示并支持按 `sell_rule` 排序，Top 行也显示具体卖出规则；“全市场卖出审计”与“每股参数寻优卖出规则”在文案上已区分。
- 2026-05-19 复验后，统一池推荐摘要变为：全池 `5201` 只，今日推荐 `37`，买入窗口 `1857`，达标多策略共振 `35`。这是从固定 profile 周期切换到每股寻优周期后的新口径。
- 2026-05-20 复验后，统一池推荐摘要保持：全池 `5201` 只，今日推荐 `37`，买入窗口 `1857`，达标多策略共振 `35`。样例信号已显示每股卖出规则：`301511` 巨量蓄势 `fixed_20`，`301658` 巨量蓄势 `fixed_30`，`688700` 巨量蓄势 `fixed_5`，`002718` 巨量蓄势 `fixed_60`。
- 2026-05-20 全量卖出规则寻优完成：`analysis/stock_formula_best.csv` 共 `21302` 条每股最优记录，其中 `5083` 条选择 `formula_exit_or_N`。
- 新样例：`301511` 的 `GS原始买点` 和 `活跃度大牛突破` 均选择 `formula_exit_or_20`；`002718` 的 `GS原始买点` 选择 `formula_exit_or_30`。
- 每股局部 Optuna 试点已完成：新增 `scripts/formula_local_optuna.py`，对 `301511/301658/688700/002718` 与 5 个公式做 24 trials 局部连续寻优，生成 `analysis/formula_local_optuna_samples.csv` 与 `.md`。
- 试点结果显示局部连续寻优有增益样例：`301658 + activity_breakout` 分数从 `65.79` 到 `84.67`，`688700 + gs_pullback_confirm` 从 `-14.04` 到 `50.07`，`301511 + volume_base_breakout` 从 `72.82` 到 `76.16`。
- 新增局部 Optuna 采纳候选审计：`scripts/formula_local_optuna_adoption.py` 生成 `analysis/formula_local_optuna_adoption_candidates.csv` 与 `.md`，按 `baseline_score>=0`、`signal_count>=6`、`score_delta>=3`、`win_rate>=45%`、`avg_ret>0`、`trials>=20` 筛选。
- 局部 Optuna 缺失结果处理已修正：不再用 `-999` 或 `0` 代表缺失基线/缺失 Optuna 结果，CSV/API/UI 改为输出 `baseline_status`、`baseline_reason`、`optuna_status`、`optuna_reason`。
- 局部 Optuna 缺失结果处理再次收紧：新增 `baseline_investigation`、`optuna_investigation`，采纳脚本不再把空指标解析成 `0`，而是输出 `missing_metric=...` 或具体调查原因；缺失结果必须先查因，不能回退默认值。
- 局部 Optuna 已加入时间切分验证：Optuna 目标函数按前 70% 可成交交易的训练集分数寻优，后 30% 作为验证集；基线参数也用同一切分重算验证指标。
- 局部 Optuna 的 `score_delta` 口径已统一：`baseline_score` 是脚本用生产参数现场重算的全样本分数，`baseline_source_score` 仅保留 `stock_formula_best.csv` 原始分数作审计，不再混用不同来源分数。
- 采纳门槛已升级为“全样本 + 验证集”双门槛：除原全样本条件外，还要求验证集样本不少于 3 笔、验证胜率不低于 45%、验证均收益为正、验证分数不弱于基线。
- 当前 20 条局部 Optuna 试点中，2 条通过验证集采纳门槛，18 条被拒绝；通过样例为 `002718 + activity_breakout` 与 `688700 + activity_breakout`。其中 `missing_baseline_result=4`、`missing_optuna_result=2`。缺失行保留用于查因，但不能参与增益候选排序。
- 已新增可恢复的局部 Optuna 批量审计脚本：`scripts/formula_local_optuna_batch.py`。它支持 `--max-stocks`、`--offset`、`--codes`、`--formulas`、`--resume`，输出独立的 `analysis/formula_local_optuna_batch*.csv/md`，不覆盖样例文件，也不写入生产 `stock_formula_best.csv`。
- 批量采纳流程已参数化：`scripts/formula_local_optuna_adoption.py --input <batch.csv> --output <adoption.csv> --report <adoption.md>` 可对任意批次应用同一套验证集门槛。
- 小批量 smoke 已完成：前 3 只股票、2 个公式、8 trials 生成 `analysis/formula_local_optuna_batch_smoke.csv`，`--resume` 二次运行 `new_rows=0`，采纳脚本输出 6 行全拒绝，主要原因包括 `trials<20`、验证样本不足和验证收益/增益不达标。
- 已新增局部 Optuna dry-run 合并预案脚本：`scripts/formula_local_optuna_merge_plan.py`。它读取通过采纳门槛的候选，输出 `analysis/formula_local_optuna_merge_plan.csv/md` 和与 `stock_formula_best.csv` schema 兼容的 `analysis/formula_local_optuna_stock_best_replacements.csv`，但不修改生产表。
- 当前样例合并预案给出 2 条可替换行：`002718 + activity_breakout`、`688700 + activity_breakout`，variant 标记为 `local_optuna_t24_vsplit`。replacement 行已包含真实 `delay_buy_rate/delay_sell_rate`，不再用空值或默认值代替。
- 策略研究接口和 UI 已展示局部 Optuna dry-run 合并预案：`/api/parameter-search` 返回 `local_optuna.merge_plan`，页面显示可替换数量、schema 行数、旧/新 variant、分数增益和验证增益，并明确标记 dry-run 不写生产表。
- 局部 Optuna 第一批真实 batch 已完成：前 20 只股票 × 5 个公式 = 100 组 `(stock, formula)`，每组 24 trials，耗时约 20.4 秒，输出 `analysis/formula_local_optuna_batch.csv/md`。
- 第一批 batch 采纳结果：100 行中 6 条通过验证集门槛、94 条拒绝，dry-run replacement 6 行，输出 `analysis/formula_local_optuna_batch_adoption.csv/md`、`analysis/formula_local_optuna_batch_merge_plan.csv/md`、`analysis/formula_local_optuna_batch_stock_best_replacements.csv`。
- 第一批通过样例包括 `000001 + activity_breakout`、`000010 + activity_breakout`、`000028 + activity_breakout`、`000026 + volume_base_breakout`、`000006 + activity_breakout`、`000026 + gs_pullback_confirm`。主要拒绝原因是验证收益/验证增益不达标、验证样本不足、全样本增益不足。
- 策略研究接口和 UI 已新增“局部 Optuna 批量”卡片，展示累计 batch 行数、候选数、dry-run replacement 数和前几条替换预案。
- 局部 Optuna 第二批真实 batch 已完成：offset 20 后再跑 20 只股票，累计覆盖前 40 只股票、200 组 `(stock, formula)`。累计采纳结果为 9 条通过、191 条拒绝，dry-run replacement 9 行；`--resume` 复跑确认 `new_rows=0`。
- 累计 batch 新增通过样例包括 `000058 + activity_breakout`、`000035 + activity_breakout`、`000037 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 6 条，`volume_base_breakout` 1 条，`gs_raw_buy` 1 条，`gs_pullback_confirm` 1 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 200 行、9 个候选、9 个 dry-run replacement，页面“局部 Optuna 批量”卡片展示当前累计 batch 进度。
- 局部 Optuna 第三批真实 batch 已完成：offset 40 后再跑 20 只股票，累计覆盖前 60 只股票、300 组 `(stock, formula)`。累计采纳结果为 13 条通过、287 条拒绝，dry-run replacement 13 行；`--resume` 复跑确认 `new_rows=0`。
- 第三批新增通过样例包括 `000156 + activity_breakout`、`000065 + activity_breakout`、`000153 + gs_raw_buy`、`000089 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 8 条，`gs_raw_buy` 3 条，`volume_base_breakout` 1 条，`gs_pullback_confirm` 1 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 300 行、13 个候选、13 个 dry-run replacement。
- 局部 Optuna 第四批真实 batch 已完成：offset 60 后再跑 20 只股票，累计覆盖前 80 只股票、400 组 `(stock, formula)`。累计采纳结果为 17 条通过、383 条拒绝，dry-run replacement 17 行；`--resume` 复跑确认 `new_rows=0`。
- 第四批新增通过样例包括 `000409 + activity_breakout`、`000404 + gs_pullback_confirm`、`000408 + volume_base_breakout`、`000301 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 9 条，`gs_raw_buy` 4 条，`gs_pullback_confirm` 2 条，`volume_base_breakout` 2 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 400 行、17 个候选、17 个 dry-run replacement。
- 局部 Optuna 第五批真实 batch 已完成：offset 80 后再跑 20 只股票，累计覆盖前 100 只股票、500 组 `(stock, formula)`。累计采纳结果为 19 条通过、481 条拒绝，dry-run replacement 19 行；`--resume` 复跑确认 `new_rows=0`。
- 第五批新增通过样例包括 `000425 + activity_breakout`、`000516 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 10 条，`gs_raw_buy` 5 条，`gs_pullback_confirm` 2 条，`volume_base_breakout` 2 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 500 行、19 个候选、19 个 dry-run replacement。
- 局部 Optuna 第六批真实 batch 已完成：offset 100 后再跑 20 只股票，累计覆盖前 120 只股票、600 组 `(stock, formula)`。累计采纳结果为 22 条通过、578 条拒绝，dry-run replacement 22 行；`--resume` 复跑确认 `new_rows=0`。
- 第六批新增通过样例包括 `000530 + activity_breakout`、`000519 + activity_breakout`、`000537 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 13 条，`gs_raw_buy` 5 条，`gs_pullback_confirm` 2 条，`volume_base_breakout` 2 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 600 行、22 个候选、22 个 dry-run replacement。
- 2026-05-20 已迁移当前 600 行 batch 到缺失调查 schema：`analysis/formula_local_optuna_batch.csv` 包含 `baseline_investigation` / `optuna_investigation`。当前累计缺失原因包括 `missing_baseline_result: stock_formula_best.csv has no row for this stock/formula = 105`，以及 `missing_optuna_result: no_entry_signal = 31`；采纳拒绝原因中有 `missing_metric=optuna_validation_* = 71`，这些均作为查因清单，不参与候选排序。
- 局部 Optuna 第七批真实 batch 已完成：offset 120 后再跑 20 只股票，累计覆盖前 140 只股票、700 组 `(stock, formula)`。累计采纳结果为 28 条通过、672 条拒绝，dry-run replacement 28 行；`--resume` 复跑确认 `new_rows=0`。
- 第七批新增通过样例包括 `000553 + gs_pullback_confirm`、`000543 + gs_raw_buy`、`000550 + activity_breakout`、`000545 + activity_breakout`、`000548 + activity_breakout`、`000558 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 16 条，`gs_raw_buy` 6 条，`gs_pullback_confirm` 4 条，`volume_base_breakout` 2 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 700 行、28 个候选、28 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 84`、`missing_baseline_result/missing_optuna_result = 33`、`ok/missing_optuna_result = 2`，均保留调查字段，不回退默认值。
- 局部 Optuna 第八批真实 batch 已完成：offset 140 后再跑 20 只股票，累计覆盖前 160 只股票、800 组 `(stock, formula)`。累计采纳结果为 37 条通过、763 条拒绝，dry-run replacement 37 行；`--resume` 复跑确认 `new_rows=0`。
- 第八批新增通过样例包括 `000596 + activity_breakout`、`000567 + activity_breakout`、`000582 + activity_breakout`、`000573 + activity_breakout`、`000572 + activity_breakout`、`000565 + activity_breakout`、`000573 + gs_raw_buy`、`000595 + activity_breakout`、`000571 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 24 条，`gs_raw_buy` 7 条，`gs_pullback_confirm` 4 条，`volume_base_breakout` 2 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 800 行、37 个候选、37 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 96`、`missing_baseline_result/missing_optuna_result = 35`、`ok/missing_optuna_result = 2`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第九批真实 batch 已完成：offset 160 后再跑 20 只股票，累计覆盖前 180 只股票、900 组 `(stock, formula)`。累计采纳结果为 43 条通过、857 条拒绝，dry-run replacement 43 行；`--resume` 复跑确认 `new_rows=0`。
- 第九批新增通过样例包括 `000623 + gs_pullback_confirm`、`000623 + volume_base_breakout`、`000612 + volume_base_breakout`、`000603 + activity_breakout`、`000629 + activity_breakout`、`000607 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 27 条，`gs_raw_buy` 7 条，`gs_pullback_confirm` 5 条，`volume_base_breakout` 4 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 900 行、43 个候选、43 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 109`、`missing_baseline_result/missing_optuna_result = 35`、`ok/missing_optuna_result = 2`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第十批真实 batch 已完成：offset 180 后再跑 20 只股票，累计覆盖前 200 只股票、1000 组 `(stock, formula)`。累计采纳结果为 48 条通过、952 条拒绝，dry-run replacement 48 行；`--resume` 复跑确认 `new_rows=0`。
- 第十批新增通过样例包括 `000637 + gs_pullback_confirm`、`000650 + activity_breakout`、`000663 + activity_breakout`、`000665 + activity_breakout`、`000663 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 30 条，`gs_raw_buy` 8 条，`gs_pullback_confirm` 6 条，`volume_base_breakout` 4 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 1000 行、48 个候选、48 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 120`、`missing_baseline_result/missing_optuna_result = 38`、`ok/missing_optuna_result = 2`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第十一批真实 batch 已完成：offset 200 后再跑 20 只股票，累计覆盖前 220 只股票、1100 组 `(stock, formula)`。累计采纳结果为 52 条通过、1048 条拒绝，dry-run replacement 52 行；`--resume` 复跑确认 `new_rows=0`。
- 第十一批新增通过样例包括 `000672 + activity_breakout`、`000680 + activity_breakout`、`000678 + activity_breakout`、`000688 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 33 条，`gs_raw_buy` 8 条，`gs_pullback_confirm` 7 条，`volume_base_breakout` 4 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 1100 行、52 个候选、52 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 135`、`missing_baseline_result/missing_optuna_result = 41`、`ok/missing_optuna_result = 2`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第十二批真实 batch 已完成：offset 220 后再跑 20 只股票，累计覆盖前 240 只股票、1200 组 `(stock, formula)`。累计采纳结果为 59 条通过、1141 条拒绝，dry-run replacement 59 行；`--resume` 复跑确认 `new_rows=0`。
- 第十二批新增通过样例包括 `000702 + volume_base_breakout`、`000709 + activity_breakout`、`000719 + activity_breakout`、`000719 + volume_base_breakout`、`000708 + activity_breakout`、`000700 + volume_base_breakout`、`000703 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 36 条，`volume_base_breakout` 8 条，`gs_raw_buy` 8 条，`gs_pullback_confirm` 7 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 1200 行、59 个候选、59 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 150`、`missing_baseline_result/missing_optuna_result = 42`、`ok/missing_optuna_result = 3`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第十三批真实 batch 已完成：offset 240 后再跑 20 只股票，累计覆盖前 260 只股票、1300 组 `(stock, formula)`。累计采纳结果为 65 条通过、1235 条拒绝，dry-run replacement 65 行；`--resume` 复跑确认 `new_rows=0`。
- 第十三批新增通过样例包括 `000757 + activity_breakout`、`000739 + activity_breakout`、`000733 + gs_raw_buy`、`000755 + activity_breakout`、`000738 + gs_raw_buy`、`000725 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 39 条，`gs_raw_buy` 11 条，`volume_base_breakout` 8 条，`gs_pullback_confirm` 7 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 1300 行、65 个候选、65 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 165`、`missing_baseline_result/missing_optuna_result = 43`、`ok/missing_optuna_result = 3`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第十四批真实 batch 已完成：offset 260 后再跑 20 只股票，累计覆盖前 280 只股票、1400 组 `(stock, formula)`。累计采纳结果为 71 条通过、1329 条拒绝，dry-run replacement 71 行；`--resume` 复跑确认 `new_rows=0`。
- 第十四批新增通过样例包括 `000786 + activity_breakout`、`000792 + gs_pullback_confirm`、`000778 + activity_breakout`、`000779 + gs_raw_buy`、`000776 + activity_breakout`、`000766 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 43 条，`gs_raw_buy` 12 条，`gs_pullback_confirm` 8 条，`volume_base_breakout` 8 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 1400 行、71 个候选、71 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 182`、`missing_baseline_result/missing_optuna_result = 47`、`ok/missing_optuna_result = 3`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第十五批真实 batch 已完成：offset 280 后再跑 20 只股票，累计覆盖前 300 只股票、1500 组 `(stock, formula)`。累计采纳结果为 74 条通过、1426 条拒绝，dry-run replacement 74 行；`--resume` 复跑确认 `new_rows=0`。
- 第十五批新增通过样例包括 `000798 + volume_base_breakout`、`000807 + gs_raw_buy`、`000819 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 43 条，`gs_raw_buy` 14 条，`volume_base_breakout` 9 条，`gs_pullback_confirm` 8 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 1500 行、74 个候选、74 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 192`、`missing_baseline_result/missing_optuna_result = 49`、`ok/missing_optuna_result = 4`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第十六批真实 batch 已完成：offset 300 后再跑 20 只股票，累计覆盖前 320 只股票、1600 组 `(stock, formula)`。累计采纳结果为 76 条通过、1524 条拒绝，dry-run replacement 76 行；`--resume` 复跑确认 `new_rows=0`。
- 第十六批新增通过样例包括 `000820 + volume_base_breakout`、`000850 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 44 条，`gs_raw_buy` 14 条，`volume_base_breakout` 10 条，`gs_pullback_confirm` 8 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 1600 行、76 个候选、76 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 204`、`missing_baseline_result/missing_optuna_result = 51`、`ok/missing_optuna_result = 4`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第十七批真实 batch 已完成：offset 320 后再跑 20 只股票，累计覆盖前 340 只股票、1700 组 `(stock, formula)`。累计采纳结果为 80 条通过、1620 条拒绝，dry-run replacement 80 行；`--resume` 复跑确认 `new_rows=0`。
- 第十七批新增通过样例包括 `000876 + activity_breakout`、`000889 + activity_breakout`、`000892 + activity_breakout`、`000892 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 47 条，`gs_raw_buy` 14 条，`volume_base_breakout` 11 条，`gs_pullback_confirm` 8 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 1700 行、80 个候选、80 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 218`、`missing_baseline_result/missing_optuna_result = 59`、`ok/missing_optuna_result = 5`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第十八批真实 batch 已完成：offset 340 后再跑 20 只股票，累计覆盖前 360 只股票、1800 组 `(stock, formula)`。累计采纳结果为 86 条通过、1714 条拒绝，dry-run replacement 86 行；`--resume` 复跑确认 `new_rows=0`。
- 第十八批新增通过样例包括 `000917 + activity_breakout`、`000912 + activity_breakout`、`000900 + gs_raw_buy`、`000908 + gs_raw_buy`、`000906 + gs_raw_buy`、`000899 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 49 条，`gs_raw_buy` 18 条，`volume_base_breakout` 11 条，`gs_pullback_confirm` 8 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 1800 行、86 个候选、86 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 227`、`missing_baseline_result/missing_optuna_result = 65`、`ok/missing_optuna_result = 5`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第十九批真实 batch 已完成：offset 360 后再跑 20 只股票，累计覆盖前 380 只股票、1900 组 `(stock, formula)`。累计采纳结果为 94 条通过、1806 条拒绝，dry-run replacement 94 行；`--resume` 复跑确认 `new_rows=0`。
- 第十九批新增通过样例包括 `000950 + volume_base_breakout`、`000931 + activity_breakout`、`000930 + activity_breakout`、`000925 + activity_breakout`、`000926 + activity_breakout`、`000936 + activity_breakout`、`000923 + activity_breakout`、`000937 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 55 条，`gs_raw_buy` 19 条，`volume_base_breakout` 12 条，`gs_pullback_confirm` 8 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 1900 行、94 个候选、94 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 242`、`missing_baseline_result/missing_optuna_result = 66`、`ok/missing_optuna_result = 5`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第二十批真实 batch 已完成：offset 380 后再跑 20 只股票，累计覆盖前 400 只股票、2000 组 `(stock, formula)`。累计采纳结果为 95 条通过、1905 条拒绝，dry-run replacement 95 行；`--resume` 复跑确认 `new_rows=0`。
- 第二十批新增通过样例为 `000967 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 56 条，`gs_raw_buy` 19 条，`volume_base_breakout` 12 条，`gs_pullback_confirm` 8 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 2000 行、95 个候选、95 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 257`、`missing_baseline_result/missing_optuna_result = 68`、`ok/missing_optuna_result = 5`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第二十一批真实 batch 已完成：offset 400 后再跑 20 只股票，累计覆盖前 420 只股票、2100 组 `(stock, formula)`。累计采纳结果为 103 条通过、1997 条拒绝，dry-run replacement 103 行；`--resume` 复跑确认 `new_rows=0`。
- 第二十一批新增通过样例包括 `000989 + activity_breakout`、`000980 + gs_pullback_confirm`、`000999 + gs_pullback_confirm`、`000980 + activity_breakout`、`001202 + gs_raw_buy`、`000989 + gs_raw_buy`、`001207 + volume_base_breakout`、`000988 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 59 条，`gs_raw_buy` 21 条，`volume_base_breakout` 13 条，`gs_pullback_confirm` 10 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 2100 行、103 个候选、103 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 268`、`missing_baseline_result/missing_optuna_result = 73`、`ok/missing_optuna_result = 6`，继续作为调查清单，不回退默认值。
- 局部 Optuna 第二十二批真实 batch 已完成：offset 420 后再跑 20 只股票，累计覆盖前 440 只股票、2200 组 `(stock, formula)`。累计采纳结果为 108 条通过、2092 条拒绝，dry-run replacement 108 行；`--resume` 复跑确认 `new_rows=0`。
- 第二十二批新增通过样例包括 `001222 + activity_breakout`、`001226 + volume_base_breakout`、`001208 + volume_base_breakout`、`001215 + activity_breakout`、`001227 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 62 条，`gs_raw_buy` 21 条，`volume_base_breakout` 15 条，`gs_pullback_confirm` 10 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 2200 行、108 个候选、108 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 283`、`missing_baseline_result/missing_optuna_result = 82`、`ok/missing_optuna_result = 7`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第二十三批真实 batch 已完成：offset 440 后再跑 20 只股票，累计覆盖前 460 只股票、2300 组 `(stock, formula)`。累计采纳结果为 111 条通过、2189 条拒绝，dry-run replacement 111 行；`--resume` 复跑确认 `new_rows=0`。
- 第二十三批新增通过样例包括 `001278 + activity_breakout`、`001266 + activity_breakout`、`001260 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 64 条，`gs_raw_buy` 21 条，`volume_base_breakout` 16 条，`gs_pullback_confirm` 10 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 2300 行、111 个候选、111 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 297`、`missing_baseline_result/missing_optuna_result = 91`、`ok/missing_optuna_result = 7`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第二十四批真实 batch 已完成：offset 460 后再跑 20 只股票，累计覆盖前 480 只股票、2400 组 `(stock, formula)`。累计采纳结果为 115 条通过、2285 条拒绝，dry-run replacement 115 行；`--resume` 复跑确认 `new_rows=0`。
- 第二十四批新增通过样例包括 `001311 + activity_breakout`、`001299 + activity_breakout`、`001313 + activity_breakout`、`001288 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 67 条，`gs_raw_buy` 22 条，`volume_base_breakout` 16 条，`gs_pullback_confirm` 10 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 2400 行、115 个候选、115 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 317`、`missing_baseline_result/missing_optuna_result = 106`、`ok/missing_optuna_result = 7`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第二十五批真实 batch 已完成：offset 480 后再跑 20 只股票，累计覆盖前 500 只股票、2500 组 `(stock, formula)`。累计采纳结果为 124 条通过、2376 条拒绝，dry-run replacement 124 行；`--resume` 复跑确认 `new_rows=0`。
- 第二十五批新增通过样例包括 `001337 + activity_breakout`、`001314 + activity_breakout`、`001318 + activity_breakout`、`001336 + activity_breakout`、`001332 + activity_breakout`、`001336 + gs_raw_buy`、`001319 + activity_breakout`、`001326 + gs_raw_buy`、`001339 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 74 条，`gs_raw_buy` 24 条，`volume_base_breakout` 16 条，`gs_pullback_confirm` 10 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 2500 行、124 个候选、124 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 329`、`missing_baseline_result/missing_optuna_result = 118`、`ok/missing_optuna_result = 7`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第二十六批真实 batch 已完成：offset 500 后再跑 20 只股票，累计覆盖前 520 只股票、2600 组 `(stock, formula)`。累计采纳结果为 130 条通过、2470 条拒绝，dry-run replacement 130 行；`--resume` 复跑确认 `new_rows=0`。
- 第二十六批新增通过样例包括 `001366 + activity_breakout`、`001378 + activity_breakout`、`001376 + activity_breakout`、`001368 + activity_breakout`、`001359 + activity_breakout`、`001367 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 79 条，`gs_raw_buy` 25 条，`volume_base_breakout` 16 条，`gs_pullback_confirm` 10 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 2600 行、130 个候选、130 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 347`、`missing_baseline_result/missing_optuna_result = 138`、`ok/missing_optuna_result = 7`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第二十七批真实 batch 已完成：offset 520 后再跑 20 只股票，累计覆盖前 540 只股票、2700 组 `(stock, formula)`。累计采纳结果为 136 条通过、2564 条拒绝，dry-run replacement 136 行；`--resume` 复跑确认 `new_rows=0`。
- 第二十七批新增通过样例包括 `002011 + activity_breakout`、`002004 + gs_pullback_confirm`、`002005 + activity_breakout`、`002001 + volume_base_breakout`、`002010 + activity_breakout`、`001696 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 82 条，`gs_raw_buy` 26 条，`volume_base_breakout` 17 条，`gs_pullback_confirm` 11 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 2700 行、136 个候选、136 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 366`、`missing_baseline_result/missing_optuna_result = 146`、`ok/missing_optuna_result = 7`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第二十八批真实 batch 已完成：offset 540 后再跑 20 只股票，累计覆盖前 560 只股票、2800 组 `(stock, formula)`。累计采纳结果为 140 条通过、2660 条拒绝，dry-run replacement 140 行；`--resume` 复跑确认 `new_rows=0`。
- 第二十八批新增通过样例包括 `002026 + activity_breakout`、`002025 + activity_breakout`、`002030 + activity_breakout`、`002019 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 85 条，`gs_raw_buy` 27 条，`volume_base_breakout` 17 条，`gs_pullback_confirm` 11 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 2800 行、140 个候选、140 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 377`、`missing_baseline_result/missing_optuna_result = 149`、`ok/missing_optuna_result = 7`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第二十九批真实 batch 已完成：offset 560 后再跑 20 只股票，累计覆盖前 580 只股票、2900 组 `(stock, formula)`。累计采纳结果为 141 条通过、2759 条拒绝，dry-run replacement 141 行；`--resume` 复跑确认 `new_rows=0`。
- 第二十九批新增通过样例为 `002046 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 86 条，`gs_raw_buy` 27 条，`volume_base_breakout` 17 条，`gs_pullback_confirm` 11 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 2900 行、141 个候选、141 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 393`、`missing_baseline_result/missing_optuna_result = 152`、`ok/missing_optuna_result = 8`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第三十批真实 batch 已完成：offset 580 后再跑 20 只股票，累计覆盖前 600 只股票、3000 组 `(stock, formula)`。累计采纳结果为 145 条通过、2855 条拒绝，dry-run replacement 145 行；`--resume` 复跑确认 `new_rows=0`。
- 第三十批新增通过样例包括 `002061 + activity_breakout`、`002072 + gs_pullback_confirm`、`002057 + gs_raw_buy`、`002055 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 87 条，`gs_raw_buy` 29 条，`volume_base_breakout` 17 条，`gs_pullback_confirm` 12 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 3000 行、145 个候选、145 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 408`、`missing_baseline_result/missing_optuna_result = 156`、`ok/missing_optuna_result = 8`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第三十一批真实 batch 已完成：offset 600 后再跑 20 只股票，累计覆盖前 620 只股票、3100 组 `(stock, formula)`。累计采纳结果为 151 条通过、2949 条拒绝，dry-run replacement 151 行；`--resume` 复跑确认 `new_rows=0`。
- 第三十一批新增通过样例包括 `002093 + activity_breakout`、`002080 + volume_base_breakout`、`002096 + activity_breakout`、`002084 + activity_breakout`、`002085 + volume_base_breakout`、`002077 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 91 条，`gs_raw_buy` 29 条，`volume_base_breakout` 19 条，`gs_pullback_confirm` 12 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 3100 行、151 个候选、151 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 422`、`missing_baseline_result/missing_optuna_result = 159`、`ok/missing_optuna_result = 8`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第三十二批真实 batch 已完成：offset 620 后再跑 20 只股票，累计覆盖前 640 只股票、3200 组 `(stock, formula)`。累计采纳结果为 155 条通过、3045 条拒绝，dry-run replacement 155 行；`--resume` 复跑确认 `new_rows=0`。
- 第三十二批新增通过样例包括 `002105 + activity_breakout`、`002107 + activity_breakout`、`002104 + gs_pullback_confirm`、`002119 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 94 条，`gs_raw_buy` 29 条，`volume_base_breakout` 19 条，`gs_pullback_confirm` 13 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 3200 行、155 个候选、155 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 432`、`missing_baseline_result/missing_optuna_result = 163`、`ok/missing_optuna_result = 8`；新增缺失调查主因包括 `stock_formula_best.csv has no row for this stock/formula`、`formula produced no entry signals` 和 `entry signals produced no executable trades`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第三十三批真实 batch 已完成：offset 640 后再跑 20 只股票，累计覆盖前 660 只股票、3300 组 `(stock, formula)`。累计采纳结果为 156 条通过、3144 条拒绝，dry-run replacement 156 行；`--resume` 复跑确认 `new_rows=0`。
- 第三十三批新增通过样例为 `002126 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 95 条，`gs_raw_buy` 29 条，`volume_base_breakout` 19 条，`gs_pullback_confirm` 13 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 3300 行、156 个候选、156 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 442`、`missing_baseline_result/missing_optuna_result = 167`、`ok/missing_optuna_result = 8`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第三十四批真实 batch 已完成：offset 660 后再跑 20 只股票，累计覆盖前 680 只股票、3400 组 `(stock, formula)`。累计采纳结果为 160 条通过、3240 条拒绝，dry-run replacement 160 行；`--resume` 复跑确认 `new_rows=0`。
- 第三十四批新增通过样例包括 `002162 + volume_base_breakout`、`002155 + volume_base_breakout`、`002160 + activity_breakout`、`002157 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 97 条，`gs_raw_buy` 29 条，`volume_base_breakout` 21 条，`gs_pullback_confirm` 13 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 3400 行、160 个候选、160 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 450`、`missing_baseline_result/missing_optuna_result = 174`、`ok/missing_optuna_result = 9`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第三十五批真实 batch 已完成：offset 680 后再跑 20 只股票，累计覆盖前 700 只股票、3500 组 `(stock, formula)`。累计采纳结果为 164 条通过、3336 条拒绝，dry-run replacement 164 行；`--resume` 复跑确认 `new_rows=0`。
- 第三十五批新增通过样例包括 `002179 + volume_base_breakout`、`002170 + gs_raw_buy`、`002170 + activity_breakout`、`002173 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 99 条，`gs_raw_buy` 30 条，`volume_base_breakout` 22 条，`gs_pullback_confirm` 13 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 3500 行、164 个候选、164 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 459`、`missing_baseline_result/missing_optuna_result = 177`、`ok/missing_optuna_result = 9`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula` 和 `formula produced no entry signals`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第三十六批真实 batch 已完成：offset 700 后再跑 20 只股票，累计覆盖前 720 只股票、3600 组 `(stock, formula)`。累计采纳结果为 168 条通过、3432 条拒绝，dry-run replacement 168 行；`--resume` 复跑确认 `new_rows=0`。
- 第三十六批新增通过样例包括 `002185 + activity_breakout`、`002195 + activity_breakout`、`002191 + gs_raw_buy`、`002193 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 101 条，`gs_raw_buy` 32 条，`volume_base_breakout` 22 条，`gs_pullback_confirm` 13 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 3600 行、168 个候选、168 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 472`、`missing_baseline_result/missing_optuna_result = 177`、`ok/missing_optuna_result = 9`；新增缺失调查主因仍是 `stock_formula_best.csv has no row for this stock/formula`，继续作为查因清单，不回退默认值。
- 局部 Optuna 第三十七批真实 batch 已完成：offset 720 后再跑 20 只股票，累计覆盖前 740 只股票、3700 组 `(stock, formula)`。累计采纳结果为 174 条通过、3526 条拒绝，dry-run replacement 174 行；`--resume` 复跑确认 `new_rows=0`。
- 第三十七批新增通过样例包括 `002211 + activity_breakout`、`002126 + activity_breakout`、`002157 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 105 条，`gs_raw_buy` 33 条，`volume_base_breakout` 23 条，`gs_pullback_confirm` 13 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 3700 行、174 个候选、174 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 481`、`missing_baseline_result/missing_optuna_result = 182`、`ok/missing_optuna_result = 9`；缺失项继续通过 `baseline_investigation` / `optuna_investigation` 记录查因，例如生产基线缺行、公式无入场信号，不作为默认参数或默认收益回填。
- 局部 Optuna 第三十八批真实 batch 已完成：offset 740 后再跑 20 只股票，累计覆盖前 760 只股票、3800 组 `(stock, formula)`。累计采纳结果为 177 条通过、3623 条拒绝，dry-run replacement 177 行；`--resume` 复跑确认 `new_rows=0`。
- 第三十八批新增通过样例包括 `002236 + activity_breakout`、`002226 + activity_breakout`、`002244 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 108 条，`gs_raw_buy` 33 条，`volume_base_breakout` 23 条，`gs_pullback_confirm` 13 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 3800 行、177 个候选、177 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 493`、`missing_baseline_result/missing_optuna_result = 185`、`ok/missing_optuna_result = 9`；687 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第三十九批真实 batch 已完成：offset 760 后再跑 20 只股票，累计覆盖前 780 只股票、3900 组 `(stock, formula)`。累计采纳结果为 178 条通过、3722 条拒绝，dry-run replacement 178 行；`--resume` 复跑确认 `new_rows=0`。
- 第三十九批新增通过样例为 `002256 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 108 条，`gs_raw_buy` 33 条，`volume_base_breakout` 23 条，`gs_pullback_confirm` 14 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 3900 行、178 个候选、178 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 509`、`missing_baseline_result/missing_optuna_result = 189`、`ok/missing_optuna_result = 9`；707 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第四十批真实 batch 已完成：offset 780 后再跑 20 只股票，累计覆盖前 800 只股票、4000 组 `(stock, formula)`。累计采纳结果为 185 条通过、3815 条拒绝，dry-run replacement 185 行；`--resume` 复跑确认 `new_rows=0`。
- 第四十批新增通过样例包括 `002268 + activity_breakout`、`002274 + activity_breakout`、`002284 + activity_breakout`、`002273 + activity_breakout`、`002274 + gs_raw_buy`、`002271 + gs_raw_buy`、`002281 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 113 条，`gs_raw_buy` 35 条，`volume_base_breakout` 23 条，`gs_pullback_confirm` 14 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 4000 行、185 个候选、185 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 522`、`missing_baseline_result/missing_optuna_result = 195`、`ok/missing_optuna_result = 9`；726 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第四十一批真实 batch 已完成：offset 800 后再跑 20 只股票，累计覆盖前 820 只股票、4100 组 `(stock, formula)`。累计采纳结果为 189 条通过、3911 条拒绝，dry-run replacement 189 行；`--resume` 复跑确认 `new_rows=0`。
- 第四十一批新增通过样例包括 `002303 + activity_breakout`、`002303 + gs_raw_buy`、`002307 + gs_raw_buy`、`002290 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 115 条，`gs_raw_buy` 37 条，`volume_base_breakout` 23 条，`gs_pullback_confirm` 14 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 4100 行、189 个候选、189 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 533`、`missing_baseline_result/missing_optuna_result = 198`、`ok/missing_optuna_result = 9`；740 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第四十二批真实 batch 已完成：offset 820 后再跑 20 只股票，累计覆盖前 840 只股票、4200 组 `(stock, formula)`。累计采纳结果为 193 条通过、4007 条拒绝，dry-run replacement 193 行；`--resume` 复跑确认 `new_rows=0`。
- 第四十二批新增通过样例包括 `002320 + gs_pullback_confirm`、`002328 + volume_base_breakout`、`002324 + gs_raw_buy`、`002310 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 116 条，`gs_raw_buy` 38 条，`volume_base_breakout` 24 条，`gs_pullback_confirm` 15 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 4200 行、193 个候选、193 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 550`、`missing_baseline_result/missing_optuna_result = 201`、`ok/missing_optuna_result = 9`；760 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第四十三批真实 batch 已完成：offset 840 后再跑 20 只股票，累计覆盖前 860 只股票、4300 组 `(stock, formula)`。累计采纳结果为 195 条通过、4105 条拒绝，dry-run replacement 195 行；`--resume` 复跑确认 `new_rows=0`。
- 第四十三批新增通过样例包括 `002339 + activity_breakout`、`002335 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 118 条，`gs_raw_buy` 38 条，`volume_base_breakout` 24 条，`gs_pullback_confirm` 15 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 4300 行、195 个候选、195 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 565`、`missing_baseline_result/missing_optuna_result = 202`、`ok/missing_optuna_result = 9`；776 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第四十四批真实 batch 已完成：offset 860 后再跑 20 只股票，累计覆盖前 880 只股票、4400 组 `(stock, formula)`。累计采纳结果为 200 条通过、4200 条拒绝，dry-run replacement 200 行；`--resume` 复跑确认 `new_rows=0`。
- 第四十四批新增通过样例包括 `002367 + volume_base_breakout`、`002363 + activity_breakout`、`002361 + activity_breakout`、`002360 + gs_raw_buy`、`002358 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 121 条，`gs_raw_buy` 39 条，`volume_base_breakout` 25 条，`gs_pullback_confirm` 15 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 4400 行、200 个候选、200 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 578`、`missing_baseline_result/missing_optuna_result = 202`、`ok/missing_optuna_result = 9`；789 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第四十五批真实 batch 已完成：offset 880 后再跑 20 只股票，累计覆盖前 900 只股票、4500 组 `(stock, formula)`。累计采纳结果为 204 条通过、4296 条拒绝，dry-run replacement 204 行；`--resume` 复跑确认 `new_rows=0`。
- 第四十五批新增通过样例包括 `002393 + volume_base_breakout`、`002380 + activity_breakout`、`002383 + activity_breakout`、`002375 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 124 条，`gs_raw_buy` 39 条，`volume_base_breakout` 26 条，`gs_pullback_confirm` 15 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 4500 行、204 个候选、204 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 588`、`missing_baseline_result/missing_optuna_result = 209`、`ok/missing_optuna_result = 9`；806 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第四十六批真实 batch 已完成：offset 900 后再跑 20 只股票，累计覆盖前 920 只股票、4600 组 `(stock, formula)`。累计采纳结果为 206 条通过、4394 条拒绝，dry-run replacement 206 行；`--resume` 复跑确认 `new_rows=0`。
- 第四十六批新增通过样例包括 `002408 + gs_raw_buy`、`002395 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 125 条，`gs_raw_buy` 40 条，`volume_base_breakout` 26 条，`gs_pullback_confirm` 15 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 4600 行、206 个候选、206 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 604`、`missing_baseline_result/missing_optuna_result = 211`、`ok/missing_optuna_result = 10`；825 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第四十七批真实 batch 已完成：offset 920 后再跑 20 只股票，累计覆盖前 940 只股票、4700 组 `(stock, formula)`。累计采纳结果为 209 条通过、4491 条拒绝，dry-run replacement 209 行；`--resume` 复跑确认 `new_rows=0`。
- 第四十七批新增通过样例包括 `002424 + activity_breakout`、`002419 + activity_breakout`、`002415 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 128 条，`gs_raw_buy` 40 条，`volume_base_breakout` 26 条，`gs_pullback_confirm` 15 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 4700 行、209 个候选、209 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 618`、`missing_baseline_result/missing_optuna_result = 213`、`ok/missing_optuna_result = 10`；841 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第四十八批真实 batch 已完成：offset 940 后再跑 20 只股票，累计覆盖前 960 只股票、4800 组 `(stock, formula)`。累计采纳结果为 214 条通过、4586 条拒绝，dry-run replacement 214 行；`--resume` 复跑确认 `new_rows=0`。
- 第四十八批新增通过样例包括 `002453 + activity_breakout`、`002446 + gs_pullback_confirm`、`002446 + volume_base_breakout`、`002455 + activity_breakout`、`002438 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 131 条，`gs_raw_buy` 40 条，`volume_base_breakout` 27 条，`gs_pullback_confirm` 16 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 4800 行、214 个候选、214 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 630`、`missing_baseline_result/missing_optuna_result = 217`、`ok/missing_optuna_result = 10`；857 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第四十九批真实 batch 已完成：offset 960 后再跑 20 只股票，累计覆盖前 980 只股票、4900 组 `(stock, formula)`。累计采纳结果为 216 条通过、4684 条拒绝，dry-run replacement 216 行；`--resume` 复跑确认 `new_rows=0`。
- 第四十九批新增通过样例包括 `002480 + activity_breakout`、`002462 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 132 条，`gs_raw_buy` 41 条，`volume_base_breakout` 27 条，`gs_pullback_confirm` 16 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 4900 行、216 个候选、216 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 637`、`missing_baseline_result/missing_optuna_result = 223`、`ok/missing_optuna_result = 10`；870 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第五十批真实 batch 已完成：offset 980 后再跑 20 只股票，累计覆盖前 1000 只股票、5000 组 `(stock, formula)`。累计采纳结果为 222 条通过、4778 条拒绝，dry-run replacement 222 行；`--resume` 复跑确认 `new_rows=0`。
- 第五十批新增通过样例包括 `002485 + gs_pullback_confirm`、`002494 + gs_pullback_confirm`、`002494 + activity_breakout`、`002492 + gs_pullback_confirm`、`002496 + activity_breakout`、`002491 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 135 条，`gs_raw_buy` 41 条，`volume_base_breakout` 27 条，`gs_pullback_confirm` 19 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 5000 行、222 个候选、222 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 653`、`missing_baseline_result/missing_optuna_result = 226`、`ok/missing_optuna_result = 10`；889 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第五十一批真实 batch 已完成：offset 1000 后再跑 20 只股票，累计覆盖前 1020 只股票、5100 组 `(stock, formula)`。累计采纳结果为 228 条通过、4872 条拒绝，dry-run replacement 228 行；`--resume` 复跑确认 `new_rows=0`。
- 第五十一批新增通过样例包括 `002515 + gs_pullback_confirm`、`002508 + activity_breakout`、`002513 + activity_breakout`、`002523 + activity_breakout`、`002528 + activity_breakout`、`002517 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 140 条，`gs_raw_buy` 41 条，`volume_base_breakout` 27 条，`gs_pullback_confirm` 20 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 5100 行、228 个候选、228 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 665`、`missing_baseline_result/missing_optuna_result = 234`、`ok/missing_optuna_result = 10`；909 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。
- 局部 Optuna 第五十二批真实 batch 已完成：offset 1020 后再跑 20 只股票，累计覆盖前 1040 只股票、5200 组 `(stock, formula)`。累计采纳结果为 234 条通过、4966 条拒绝，dry-run replacement 234 行；`--resume` 复跑确认 `new_rows=0`。
- 第五十二批新增通过样例包括 `002531 + activity_breakout`、`002535 + activity_breakout`、`002541 + activity_breakout`、`002546 + gs_pullback_confirm`、`002540 + gs_raw_buy`、`002536 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 144 条，`gs_raw_buy` 42 条，`volume_base_breakout` 27 条，`gs_pullback_confirm` 21 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 5200 行、234 个候选、234 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 680`、`missing_baseline_result/missing_optuna_result = 235`、`ok/missing_optuna_result = 10`；925 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。接口已补充 `metric_count_source` 与 batch 级 `status_counts` / `missing_investigation_counts`，避免把缺失结果误读为默认值。
- 局部 Optuna 第五十三批真实 batch 已完成：offset 1040 后再跑 20 只股票，累计覆盖前 1060 只股票、5300 组 `(stock, formula)`。累计采纳结果为 238 条通过、5062 条拒绝，dry-run replacement 238 行；`--resume` 复跑确认 `new_rows=0`。
- 第五十三批新增通过样例包括 `002558 + gs_pullback_confirm`、`002553 + gs_pullback_confirm`、`002561 + activity_breakout`、`002569 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 145 条，`gs_raw_buy` 42 条，`volume_base_breakout` 27 条，`gs_pullback_confirm` 24 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 5300 行、238 个候选、238 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 691`、`missing_baseline_result/missing_optuna_result = 238`、`ok/missing_optuna_result = 11`；940 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 929`、`missing_optuna_result = 249`，`missing_investigation_counts` 合计 1178 个单侧缺失原因计数。
- 局部 Optuna 第五十四批真实 batch 已完成：offset 1060 后再跑 20 只股票，累计覆盖前 1080 只股票、5400 组 `(stock, formula)`。累计采纳结果为 242 条通过、5158 条拒绝，dry-run replacement 242 行；`--resume` 复跑确认 `new_rows=0`。
- 第五十四批新增通过样例包括 `002589 + activity_breakout`、`002578 + activity_breakout`、`002573 + gs_raw_buy`、`002575 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 148 条，`gs_raw_buy` 43 条，`volume_base_breakout` 27 条，`gs_pullback_confirm` 24 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 5400 行、242 个候选、242 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 700`、`missing_baseline_result/missing_optuna_result = 244`、`ok/missing_optuna_result = 13`；957 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 944`、`missing_optuna_result = 257`，`missing_investigation_counts` 合计 1201 个单侧缺失原因计数。
- 局部 Optuna 第五十五批真实 batch 已完成：offset 1080 后再跑 20 只股票，累计覆盖前 1100 只股票、5500 组 `(stock, formula)`。累计采纳结果为 248 条通过、5252 条拒绝，dry-run replacement 248 行；`--resume` 复跑确认 `new_rows=0`。
- 第五十五批新增通过样例包括 `002593 + activity_breakout`、`002600 + activity_breakout`、`002609 + activity_breakout`、`002594 + activity_breakout`、`002592 + activity_breakout`、`002595 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 153 条，`gs_raw_buy` 44 条，`volume_base_breakout` 27 条，`gs_pullback_confirm` 24 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 5500 行、248 个候选、248 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 710`、`missing_baseline_result/missing_optuna_result = 247`、`ok/missing_optuna_result = 13`；970 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 957`、`missing_optuna_result = 260`，`missing_investigation_counts` 合计 1217 个单侧缺失原因计数。
- 局部 Optuna 第五十六批真实 batch 已完成：offset 1100 后再跑 20 只股票，累计覆盖前 1120 只股票、5600 组 `(stock, formula)`。累计采纳结果为 257 条通过、5343 条拒绝，dry-run replacement 257 行；`--resume` 复跑确认 `new_rows=0`。
- 第五十六批新增通过样例包括 `002616 + volume_base_breakout`、`002615 + activity_breakout`、`002629 + gs_pullback_confirm`、`002632 + activity_breakout`、`002620 + activity_breakout`、`002623 + activity_breakout`、`002633 + volume_base_breakout`、`002632 + volume_base_breakout`、`002628 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 157 条，`gs_raw_buy` 45 条，`volume_base_breakout` 30 条，`gs_pullback_confirm` 25 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 5600 行、257 个候选、257 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 723`、`missing_baseline_result/missing_optuna_result = 254`、`ok/missing_optuna_result = 13`；990 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 977`、`missing_optuna_result = 267`，`missing_investigation_counts` 合计 1244 个单侧缺失原因计数。
- 局部 Optuna 第五十七批真实 batch 已完成：offset 1120 后再跑 20 只股票，累计覆盖前 1140 只股票、5700 组 `(stock, formula)`。累计采纳结果为 261 条通过、5439 条拒绝，dry-run replacement 261 行；`--resume` 复跑确认 `new_rows=0`。
- 第五十七批新增通过样例包括 `002641 + activity_breakout`、`002637 + activity_breakout`、`002637 + gs_raw_buy`、`002642 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 159 条，`gs_raw_buy` 47 条，`volume_base_breakout` 30 条，`gs_pullback_confirm` 25 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 5700 行、261 个候选、261 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 732`、`missing_baseline_result/missing_optuna_result = 260`、`ok/missing_optuna_result = 13`；1005 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 992`、`missing_optuna_result = 273`，`missing_investigation_counts` 合计 1265 个单侧缺失原因计数。
- 局部 Optuna 第五十八批真实 batch 已完成：offset 1140 后再跑 20 只股票，累计覆盖前 1160 只股票、5800 组 `(stock, formula)`。累计采纳结果为 266 条通过、5534 条拒绝，dry-run replacement 266 行；`--resume` 复跑确认 `new_rows=0`。
- 第五十八批新增通过样例包括 `002662 + activity_breakout`、`002660 + activity_breakout`、`002672 + gs_pullback_confirm`、`002664 + activity_breakout`、`002670 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 163 条，`gs_raw_buy` 47 条，`volume_base_breakout` 30 条，`gs_pullback_confirm` 26 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 5800 行、266 个候选、266 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 745`、`missing_baseline_result/missing_optuna_result = 261`、`ok/missing_optuna_result = 13`；1019 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1006`、`missing_optuna_result = 274`，`missing_investigation_counts` 合计 1280 个单侧缺失原因计数。
- 局部 Optuna 第五十九批真实 batch 已完成：offset 1160 后再跑 20 只股票，累计覆盖前 1180 只股票、5900 组 `(stock, formula)`。累计采纳结果为 271 条通过、5629 条拒绝，dry-run replacement 271 行；`--resume` 复跑确认 `new_rows=0`。
- 第五十九批新增通过样例包括 `002678 + activity_breakout`、`002695 + activity_breakout`、`002696 + gs_raw_buy`、`002679 + volume_base_breakout`、`002689 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 165 条，`gs_raw_buy` 49 条，`volume_base_breakout` 31 条，`gs_pullback_confirm` 26 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 5900 行、271 个候选、271 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 757`、`missing_baseline_result/missing_optuna_result = 263`、`ok/missing_optuna_result = 13`；1033 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1020`、`missing_optuna_result = 276`，`missing_investigation_counts` 合计 1296 个单侧缺失原因计数。
- 局部 Optuna 第六十批真实 batch 已完成：offset 1180 后再跑 20 只股票，累计覆盖前 1200 只股票、6000 组 `(stock, formula)`。累计采纳结果为 276 条通过、5724 条拒绝，dry-run replacement 276 行；`--resume` 复跑确认 `new_rows=0`。
- 第六十批新增通过样例包括 `002707 + activity_breakout`、`002707 + gs_pullback_confirm`、`002719 + activity_breakout`、`002715 + activity_breakout`、`002718 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 169 条，`gs_raw_buy` 49 条，`volume_base_breakout` 31 条，`gs_pullback_confirm` 27 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 6000 行、276 个候选、276 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 768`、`missing_baseline_result/missing_optuna_result = 266`、`ok/missing_optuna_result = 13`；1047 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1034`、`missing_optuna_result = 279`，`missing_investigation_counts` 合计 1313 个单侧缺失原因计数。
- 局部 Optuna 第六十一批真实 batch 已完成：offset 1200 后再跑 20 只股票，累计覆盖前 1220 只股票、6100 组 `(stock, formula)`。累计采纳结果为 280 条通过、5820 条拒绝，dry-run replacement 280 行；`--resume` 复跑确认 `new_rows=0`。
- 第六十一批新增通过样例包括 `002725 + activity_breakout`、`002729 + activity_breakout`、`002742 + activity_breakout`、`002738 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 172 条，`gs_raw_buy` 49 条，`volume_base_breakout` 31 条，`gs_pullback_confirm` 28 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 6100 行、280 个候选、280 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 781`、`missing_baseline_result/missing_optuna_result = 268`、`ok/missing_optuna_result = 14`；1063 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1049`、`missing_optuna_result = 282`，`missing_investigation_counts` 合计 1331 个单侧缺失原因计数。
- 局部 Optuna 第六十二批真实 batch 已完成：offset 1220 后再跑 20 只股票，累计覆盖前 1240 只股票、6200 组 `(stock, formula)`。累计采纳结果为 284 条通过、5916 条拒绝，dry-run replacement 284 行；`--resume` 复跑确认 `new_rows=0`。
- 第六十二批新增通过样例包括 `002747 + volume_base_breakout`、`002766 + activity_breakout`、`002745 + activity_breakout`、`002746 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 174 条，`gs_raw_buy` 49 条，`volume_base_breakout` 33 条，`gs_pullback_confirm` 28 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 6200 行、284 个候选、284 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 795`、`missing_baseline_result/missing_optuna_result = 271`、`ok/missing_optuna_result = 15`；1081 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1066`、`missing_optuna_result = 286`，`missing_investigation_counts` 合计 1352 个单侧缺失原因计数；本批新增查因包括 `entry signals produced no executable trades`。
- 局部 Optuna 第六十三批真实 batch 已完成：offset 1240 后再跑 20 只股票，累计覆盖前 1260 只股票、6300 组 `(stock, formula)`。累计采纳结果为 290 条通过、6010 条拒绝，dry-run replacement 290 行；`--resume` 复跑确认 `new_rows=0`。
- 第六十三批新增通过样例包括 `002778 + activity_breakout`、`002777 + activity_breakout`、`002772 + activity_breakout`、`002769 + gs_raw_buy`、`002778 + gs_pullback_confirm`、`002786 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 177 条，`gs_raw_buy` 51 条，`volume_base_breakout` 33 条，`gs_pullback_confirm` 29 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 6300 行、290 个候选、290 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 808`、`missing_baseline_result/missing_optuna_result = 274`、`ok/missing_optuna_result = 16`；1098 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1082`、`missing_optuna_result = 290`，`missing_investigation_counts` 合计 1372 个单侧缺失原因计数。
- 局部 Optuna 第六十四批真实 batch 已完成：offset 1260 后再跑 20 只股票，累计覆盖前 1280 只股票、6400 组 `(stock, formula)`。累计采纳结果为 296 条通过、6104 条拒绝，dry-run replacement 296 行；`--resume` 复跑确认 `new_rows=0`。
- 第六十四批新增通过样例包括 `002805 + activity_breakout`、`002793 + activity_breakout`、`002806 + volume_base_breakout`、`002799 + activity_breakout`、`002815 + activity_breakout`、`002806 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 181 条，`gs_raw_buy` 52 条，`volume_base_breakout` 34 条，`gs_pullback_confirm` 29 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 6400 行、296 个候选、296 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 821`、`missing_baseline_result/missing_optuna_result = 277`、`ok/missing_optuna_result = 17`；1115 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1098`、`missing_optuna_result = 294`，`missing_investigation_counts` 合计 1392 个单侧缺失原因计数。
- 局部 Optuna 第六十五批真实 batch 已完成：offset 1280 后再跑 20 只股票，累计覆盖前 1300 只股票、6500 组 `(stock, formula)`。累计采纳结果为 302 条通过、6198 条拒绝，dry-run replacement 302 行；`--resume` 复跑确认 `new_rows=0`。
- 第六十五批新增通过样例包括 `002827 + gs_pullback_confirm`、`002820 + activity_breakout`、`002828 + activity_breakout`、`002817 + activity_breakout`、`002816 + gs_pullback_confirm`、`002827 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 185 条，`gs_raw_buy` 52 条，`volume_base_breakout` 34 条，`gs_pullback_confirm` 31 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 6500 行、302 个候选、302 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 836`、`missing_baseline_result/missing_optuna_result = 279`、`ok/missing_optuna_result = 17`；1132 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1115`、`missing_optuna_result = 296`，`missing_investigation_counts` 合计 1411 个单侧缺失原因计数。
- 局部 Optuna 第六十六批真实 batch 已完成：offset 1300 后再跑 20 只股票，累计覆盖前 1320 只股票、6600 组 `(stock, formula)`。累计采纳结果为 309 条通过、6291 条拒绝，dry-run replacement 309 行；`--resume` 复跑确认 `new_rows=0`。
- 第六十六批新增通过样例包括 `002849 + activity_breakout`、`002848 + volume_base_breakout`、`002847 + activity_breakout`、`002839 + gs_raw_buy`、`002856 + volume_base_breakout`、`002842 + gs_pullback_confirm`、`002852 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 187 条，`gs_raw_buy` 53 条，`volume_base_breakout` 36 条，`gs_pullback_confirm` 33 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 6600 行、309 个候选、309 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 853`、`missing_baseline_result/missing_optuna_result = 281`、`ok/missing_optuna_result = 17`；1151 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1134`、`missing_optuna_result = 298`，`missing_investigation_counts` 合计 1432 个单侧缺失原因计数。
- 局部 Optuna 第六十七批真实 batch 已完成：offset 1320 后再跑 20 只股票，累计覆盖前 1340 只股票、6700 组 `(stock, formula)`。累计采纳结果为 315 条通过、6385 条拒绝，dry-run replacement 315 行；`--resume` 复跑确认 `new_rows=0`。
- 第六十七批新增通过样例包括 `002876 + activity_breakout`、`002866 + volume_base_breakout`、`002868 + gs_pullback_confirm`、`002868 + volume_base_breakout`、`002866 + activity_breakout`、`002877 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 189 条，`gs_raw_buy` 54 条，`volume_base_breakout` 38 条，`gs_pullback_confirm` 34 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 6700 行、315 个候选、315 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 867`、`missing_baseline_result/missing_optuna_result = 284`、`ok/missing_optuna_result = 18`；1169 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1151`、`missing_optuna_result = 302`，`missing_investigation_counts` 合计 1453 个单侧缺失原因计数。
- 局部 Optuna 第六十八批真实 batch 已完成：offset 1340 后再跑 20 只股票，累计覆盖前 1360 只股票、6800 组 `(stock, formula)`。累计采纳结果为 321 条通过、6479 条拒绝，dry-run replacement 321 行；`--resume` 复跑确认 `new_rows=0`。
- 第六十八批新增通过样例包括 `002887 + activity_breakout`、`002886 + gs_pullback_confirm`、`002893 + activity_breakout`、`002886 + activity_breakout`、`002880 + gs_pullback_confirm`、`002890 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 192 条，`gs_raw_buy` 55 条，`volume_base_breakout` 38 条，`gs_pullback_confirm` 36 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 6800 行、321 个候选、321 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 879`、`missing_baseline_result/missing_optuna_result = 287`、`ok/missing_optuna_result = 18`；1184 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1166`、`missing_optuna_result = 305`，`missing_investigation_counts` 合计 1471 个单侧缺失原因计数。
- 局部 Optuna 第六十九批真实 batch 已完成：offset 1360 后再跑 20 只股票，累计覆盖前 1380 只股票、6900 组 `(stock, formula)`。累计采纳结果为 326 条通过、6574 条拒绝，dry-run replacement 326 行；`--resume` 复跑确认 `new_rows=0`。
- 第六十九批新增通过样例包括 `002903 + activity_breakout`、`002901 + gs_raw_buy`、`002905 + activity_breakout`、`002906 + activity_breakout`、`002909 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 196 条，`gs_raw_buy` 56 条，`volume_base_breakout` 38 条，`gs_pullback_confirm` 36 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 6900 行、326 个候选、326 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 893`、`missing_baseline_result/missing_optuna_result = 290`、`ok/missing_optuna_result = 18`；1201 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1183`、`missing_optuna_result = 308`，`missing_investigation_counts` 合计 1491 个单侧缺失原因计数。
- 局部 Optuna 第七十批真实 batch 已完成：offset 1380 后再跑 20 只股票，累计覆盖前 1400 只股票、7000 组 `(stock, formula)`。累计采纳结果为 329 条通过、6671 条拒绝，dry-run replacement 329 行；`--resume` 复跑确认 `new_rows=0`。
- 第七十批新增通过样例包括 `002927 + activity_breakout`、`002932 + activity_breakout`、`002941 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 198 条，`gs_raw_buy` 57 条，`volume_base_breakout` 38 条，`gs_pullback_confirm` 36 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 7000 行、329 个候选、329 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 904`、`missing_baseline_result/missing_optuna_result = 290`、`ok/missing_optuna_result = 18`；1212 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1194`、`missing_optuna_result = 308`，`missing_investigation_counts` 合计 1502 个单侧缺失原因计数。
- 局部 Optuna 第七十一批真实 batch 已完成：offset 1400 后再跑 20 只股票，累计覆盖前 1420 只股票、7100 组 `(stock, formula)`。累计采纳结果为 335 条通过、6765 条拒绝，dry-run replacement 335 行；`--resume` 复跑确认 `new_rows=0`。
- 第七十一批新增通过样例包括 `002949 + volume_base_breakout`、`002956 + gs_pullback_confirm`、`002953 + activity_breakout`、`002950 + gs_raw_buy`、`002958 + gs_raw_buy`、`002946 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 199 条，`gs_raw_buy` 60 条，`volume_base_breakout` 39 条，`gs_pullback_confirm` 37 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 7100 行、335 个候选、335 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 916`、`missing_baseline_result/missing_optuna_result = 291`、`ok/missing_optuna_result = 19`；1226 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1207`、`missing_optuna_result = 310`，`missing_investigation_counts` 合计 1517 个单侧缺失原因计数。
- 局部 Optuna 第七十二批真实 batch 已完成：offset 1420 后再跑 20 只股票，累计覆盖前 1440 只股票、7200 组 `(stock, formula)`。累计采纳结果为 339 条通过、6861 条拒绝，dry-run replacement 339 行；`--resume` 复跑确认 `new_rows=0`。
- 第七十二批新增通过样例包括 `002987 + activity_breakout`、`002979 + activity_breakout`、`002970 + volume_base_breakout`、`002972 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 201 条，`gs_raw_buy` 61 条，`volume_base_breakout` 40 条，`gs_pullback_confirm` 37 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 7200 行、339 个候选、339 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 925`、`missing_baseline_result/missing_optuna_result = 294`、`ok/missing_optuna_result = 19`；1238 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1219`、`missing_optuna_result = 313`，`missing_investigation_counts` 合计 1532 个单侧缺失原因计数。
- 局部 Optuna 第七十三批真实 batch 已完成：offset 1440 后再跑 20 只股票，累计覆盖前 1460 只股票、7300 组 `(stock, formula)`。累计采纳结果为 345 条通过、6955 条拒绝，dry-run replacement 345 行；`--resume` 复跑确认 `new_rows=0`。
- 第七十三批新增通过样例包括 `003003 + activity_breakout`、`002998 + gs_pullback_confirm`、`002995 + activity_breakout`、`003008 + gs_raw_buy`、`002989 + activity_breakout`、`003002 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 205 条，`gs_raw_buy` 62 条，`volume_base_breakout` 40 条，`gs_pullback_confirm` 38 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 7300 行、345 个候选、345 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 939`、`missing_baseline_result/missing_optuna_result = 297`、`ok/missing_optuna_result = 19`；1255 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1236`、`missing_optuna_result = 316`，`missing_investigation_counts` 合计 1552 个单侧缺失原因计数。
- 局部 Optuna 第七十四批真实 batch 已完成：offset 1460 后再跑 20 只股票，累计覆盖前 1480 只股票、7400 组 `(stock, formula)`。累计采纳结果为 352 条通过、7048 条拒绝，dry-run replacement 352 行；`--resume` 复跑确认 `new_rows=0`。
- 第七十四批新增通过样例包括 `003011 + activity_breakout`、`003028 + activity_breakout`、`003029 + activity_breakout`、`003027 + gs_raw_buy`、`003015 + gs_raw_buy`、`003017 + gs_raw_buy`、`003013 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 208 条，`gs_raw_buy` 66 条，`volume_base_breakout` 40 条，`gs_pullback_confirm` 38 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 7400 行、352 个候选、352 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 951`、`missing_baseline_result/missing_optuna_result = 299`、`ok/missing_optuna_result = 20`；1270 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1250`、`missing_optuna_result = 319`，`missing_investigation_counts` 合计 1569 个单侧缺失原因计数。
- 局部 Optuna 第七十五批真实 batch 已完成：offset 1480 后再跑 20 只股票，累计覆盖前 1500 只股票、7500 组 `(stock, formula)`。累计采纳结果为 359 条通过、7141 条拒绝，dry-run replacement 359 行；`--resume` 复跑确认 `new_rows=0`。
- 第七十五批新增通过样例包括 `300007 + gs_pullback_confirm`、`003042 + activity_breakout`、`003040 + activity_breakout`、`003037 + gs_pullback_confirm`、`300007 + activity_breakout`、`300006 + gs_raw_buy`、`003816 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 211 条，`gs_raw_buy` 68 条，`gs_pullback_confirm` 40 条，`volume_base_breakout` 40 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 7500 行、359 个候选、359 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 964`、`missing_baseline_result/missing_optuna_result = 304`、`ok/missing_optuna_result = 20`；1288 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1268`、`missing_optuna_result = 324`，`missing_investigation_counts` 合计 1592 个单侧缺失原因计数。
- 局部 Optuna 第七十六批真实 batch 已完成：offset 1500 后再跑 20 只股票，累计覆盖前 1520 只股票、7600 组 `(stock, formula)`。累计采纳结果为 364 条通过、7236 条拒绝，dry-run replacement 364 行；`--resume` 复跑确认 `new_rows=0`。
- 第七十六批新增通过样例包括 `300021 + activity_breakout`、`300022 + gs_pullback_confirm`、`300026 + activity_breakout`、`300018 + gs_pullback_confirm`、`300017 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 214 条，`gs_raw_buy` 68 条，`gs_pullback_confirm` 42 条，`volume_base_breakout` 40 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 7600 行、364 个候选、364 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 979`、`missing_baseline_result/missing_optuna_result = 309`、`ok/missing_optuna_result = 20`；1308 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1288`、`missing_optuna_result = 329`，`missing_investigation_counts` 合计 1617 个单侧缺失原因计数。
- 局部 Optuna 第七十七批真实 batch 已完成：offset 1520 后再跑 20 只股票，累计覆盖前 1540 只股票、7700 组 `(stock, formula)`。累计采纳结果为 370 条通过、7330 条拒绝，dry-run replacement 370 行；`--resume` 复跑确认 `new_rows=0`。
- 第七十七批新增通过样例包括 `300035 + activity_breakout`、`300045 + gs_pullback_confirm`、`300040 + activity_breakout`、`300032 + activity_breakout`、`300040 + gs_raw_buy`、`300036 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 218 条，`gs_raw_buy` 69 条，`gs_pullback_confirm` 43 条，`volume_base_breakout` 40 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 7700 行、370 个候选、370 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 993`、`missing_baseline_result/missing_optuna_result = 311`、`ok/missing_optuna_result = 21`；1325 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1304`、`missing_optuna_result = 332`，`missing_investigation_counts` 合计 1636 个单侧缺失原因计数。
- 局部 Optuna 第七十八批真实 batch 已完成：offset 1540 后再跑 20 只股票，累计覆盖前 1560 只股票、7800 组 `(stock, formula)`。累计采纳结果为 371 条通过、7429 条拒绝，dry-run replacement 371 行；`--resume` 复跑确认 `new_rows=0`。
- 第七十八批新增通过样例为 `300068 + activity_breakout`，`score_delta=18.246659`、`validation_score_delta=28.898956`、卖出规则 `formula_exit_or_20`。当前通过候选按公式分布：`activity_breakout` 219 条，`gs_raw_buy` 69 条，`gs_pullback_confirm` 43 条，`volume_base_breakout` 40 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 7800 行、371 个候选、371 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1008`、`missing_baseline_result/missing_optuna_result = 311`、`ok/missing_optuna_result = 22`；1341 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1319`、`missing_optuna_result = 333`，`missing_investigation_counts` 合计 1652 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 15 条，以及 Optuna 公式无入场信号 1 条。
- 局部 Optuna 第七十九批真实 batch 已完成：offset 1560 后再跑 20 只股票，累计覆盖前 1580 只股票、7900 组 `(stock, formula)`。累计采纳结果为 377 条通过、7523 条拒绝，dry-run replacement 377 行；`--resume` 复跑确认 `new_rows=0`。
- 第七十九批新增通过样例包括 `300075 + volume_base_breakout`、`300075 + activity_breakout`、`300093 + gs_pullback_confirm`、`300074 + activity_breakout`、`300079 + activity_breakout`、`300083 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 222 条，`gs_raw_buy` 70 条，`gs_pullback_confirm` 44 条，`volume_base_breakout` 41 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 7900 行、377 个候选、377 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1025`、`missing_baseline_result/missing_optuna_result = 314`、`ok/missing_optuna_result = 22`；1361 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1339`、`missing_optuna_result = 336`，`missing_investigation_counts` 合计 1675 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 17 条，以及生产基线缺行且 Optuna 公式无入场信号 3 条。
- 局部 Optuna 第八十批真实 batch 已完成：offset 1580 后再跑 20 只股票，累计覆盖前 1600 只股票、8000 组 `(stock, formula)`。累计采纳结果为 384 条通过、7616 条拒绝，dry-run replacement 384 行；`--resume` 复跑确认 `new_rows=0`。
- 第八十批新增通过样例包括 `300099 + volume_base_breakout`、`300096 + activity_breakout`、`300115 + activity_breakout`、`300097 + activity_breakout`、`300105 + gs_raw_buy`、`300096 + gs_raw_buy`、`300098 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 225 条，`gs_raw_buy` 73 条，`gs_pullback_confirm` 44 条，`volume_base_breakout` 42 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 8000 行、384 个候选、384 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1038`、`missing_baseline_result/missing_optuna_result = 318`、`ok/missing_optuna_result = 23`；1379 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1356`、`missing_optuna_result = 341`，`missing_investigation_counts` 合计 1697 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 13 条、基线缺行且 Optuna 无入场信号 3 条、基线缺行且存在无可执行成交 1 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第八十一批真实 batch 已完成：offset 1600 后再跑 20 只股票，累计覆盖前 1620 只股票、8100 组 `(stock, formula)`。累计采纳结果为 388 条通过、7712 条拒绝，dry-run replacement 388 行；`--resume` 复跑确认 `new_rows=0`。
- 第八十一批新增通过样例包括 `300128 + activity_breakout`、`300126 + activity_breakout`、`300128 + gs_pullback_confirm`、`300124 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 227 条，`gs_raw_buy` 74 条，`gs_pullback_confirm` 45 条，`volume_base_breakout` 42 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 8100 行、388 个候选、388 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1051`、`missing_baseline_result/missing_optuna_result = 324`、`ok/missing_optuna_result = 23`；1398 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1375`、`missing_optuna_result = 347`，`missing_investigation_counts` 合计 1722 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 13 条，以及生产基线缺行且 Optuna 无入场信号 6 条。
- 局部 Optuna 第八十二批真实 batch 已完成：offset 1620 后再跑 20 只股票，累计覆盖前 1640 只股票、8200 组 `(stock, formula)`。累计采纳结果为 396 条通过、7804 条拒绝，dry-run replacement 396 行；`--resume` 复跑确认 `new_rows=0`。
- 第八十二批新增通过样例包括 `300142 + activity_breakout`、`300151 + activity_breakout`、`300143 + activity_breakout`、`300141 + gs_pullback_confirm`、`300148 + activity_breakout`、`300155 + activity_breakout`、`300145 + activity_breakout`、`300161 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 233 条，`gs_raw_buy` 75 条，`gs_pullback_confirm` 46 条，`volume_base_breakout` 42 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 8200 行、396 个候选、396 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1068`、`missing_baseline_result/missing_optuna_result = 327`、`ok/missing_optuna_result = 23`；1418 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1395`、`missing_optuna_result = 350`，`missing_investigation_counts` 合计 1745 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 17 条，以及生产基线缺行且 Optuna 无入场信号 3 条。
- 局部 Optuna 第八十三批真实 batch 已完成：offset 1640 后再跑 20 只股票，累计覆盖前 1660 只股票、8300 组 `(stock, formula)`。累计采纳结果为 399 条通过、7901 条拒绝，dry-run replacement 399 行；`--resume` 复跑确认 `new_rows=0`。
- 第八十三批新增通过样例包括 `300165 + activity_breakout`、`300165 + volume_base_breakout`、`300176 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 234 条，`gs_raw_buy` 75 条，`gs_pullback_confirm` 46 条，`volume_base_breakout` 44 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 8300 行、399 个候选、399 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1080`、`missing_baseline_result/missing_optuna_result = 331`、`ok/missing_optuna_result = 23`；1434 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1411`、`missing_optuna_result = 354`，`missing_investigation_counts` 合计 1765 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 12 条，以及生产基线缺行且 Optuna 无入场信号 4 条。
- 局部 Optuna 第八十四批真实 batch 已完成：offset 1660 后再跑 20 只股票，累计覆盖前 1680 只股票、8400 组 `(stock, formula)`。累计采纳结果为 400 条通过、8000 条拒绝，dry-run replacement 400 行；`--resume` 复跑确认 `new_rows=0`。
- 第八十四批新增通过样例为 `300191 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 235 条，`gs_raw_buy` 75 条，`gs_pullback_confirm` 46 条，`volume_base_breakout` 44 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 8400 行、400 个候选、400 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1091`、`missing_baseline_result/missing_optuna_result = 337`、`ok/missing_optuna_result = 24`；1452 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1428`、`missing_optuna_result = 361`，`missing_investigation_counts` 合计 1789 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 11 条、生产基线缺行且 Optuna 无入场信号 6 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第八十五批真实 batch 已完成：offset 1680 后再跑 20 只股票，累计覆盖前 1700 只股票、8500 组 `(stock, formula)`。累计采纳结果为 404 条通过、8096 条拒绝，dry-run replacement 404 行；`--resume` 复跑确认 `new_rows=0`。
- 第八十五批新增通过样例包括 `300206 + activity_breakout`、`300207 + activity_breakout`、`300224 + gs_raw_buy`、`300221 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 237 条，`gs_raw_buy` 76 条，`gs_pullback_confirm` 47 条，`volume_base_breakout` 44 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 8500 行、404 个候选、404 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1105`、`missing_baseline_result/missing_optuna_result = 343`、`ok/missing_optuna_result = 24`；1472 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1448`、`missing_optuna_result = 367`，`missing_investigation_counts` 合计 1815 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 14 条，以及生产基线缺行且 Optuna 无入场信号 6 条。
- 局部 Optuna 第八十六批真实 batch 已完成：offset 1700 后再跑 20 只股票，累计覆盖前 1720 只股票、8600 组 `(stock, formula)`。累计采纳结果为 409 条通过、8191 条拒绝，dry-run replacement 409 行；`--resume` 复跑确认 `new_rows=0`。
- 第八十六批新增通过样例包括 `300239 + activity_breakout`、`300241 + volume_base_breakout`、`300228 + activity_breakout`、`300235 + activity_breakout`、`300241 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 241 条，`gs_raw_buy` 76 条，`gs_pullback_confirm` 47 条，`volume_base_breakout` 45 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 8600 行、409 个候选、409 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1110`、`missing_baseline_result/missing_optuna_result = 347`、`ok/missing_optuna_result = 26`；1483 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1457`、`missing_optuna_result = 373`，`missing_investigation_counts` 合计 1830 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 5 条、生产基线缺行且 Optuna 无入场信号 4 条，以及 Optuna 单侧无入场信号 2 条。
- 局部 Optuna 第八十七批真实 batch 已完成：offset 1720 后再跑 20 只股票，累计覆盖前 1740 只股票、8700 组 `(stock, formula)`。累计采纳结果为 415 条通过、8285 条拒绝，dry-run replacement 415 行；`--resume` 复跑确认 `new_rows=0`。
- 第八十七批新增通过样例包括 `300247 + activity_breakout`、`300266 + activity_breakout`、`300267 + activity_breakout`、`300261 + activity_breakout`、`300259 + activity_breakout`、`300264 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 247 条，`gs_raw_buy` 76 条，`gs_pullback_confirm` 47 条，`volume_base_breakout` 45 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 8700 行、415 个候选、415 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1120`、`missing_baseline_result/missing_optuna_result = 350`、`ok/missing_optuna_result = 27`；1497 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1470`、`missing_optuna_result = 377`，`missing_investigation_counts` 合计 1847 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 10 条、生产基线缺行且 Optuna 无入场信号 3 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第八十八批真实 batch 已完成：offset 1740 后再跑 20 只股票，累计覆盖前 1760 只股票、8800 组 `(stock, formula)`。累计采纳结果为 419 条通过、8381 条拒绝，dry-run replacement 419 行；`--resume` 复跑确认 `new_rows=0`。
- 第八十八批新增通过样例包括 `300281 + activity_breakout`、`300283 + activity_breakout`、`300286 + activity_breakout`、`300278 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 251 条，`gs_raw_buy` 76 条，`gs_pullback_confirm` 47 条，`volume_base_breakout` 45 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 8800 行、419 个候选、419 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1131`、`missing_baseline_result/missing_optuna_result = 352`、`ok/missing_optuna_result = 27`；1510 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1483`、`missing_optuna_result = 379`，`missing_investigation_counts` 合计 1862 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 11 条，以及生产基线缺行且 Optuna 无入场信号 2 条。
- 局部 Optuna 第八十九批真实 batch 已完成：offset 1760 后再跑 20 只股票，累计覆盖前 1780 只股票、8900 组 `(stock, formula)`。累计采纳结果为 425 条通过、8475 条拒绝，dry-run replacement 425 行；`--resume` 复跑确认 `new_rows=0`。
- 第八十九批新增通过样例包括 `300291 + activity_breakout`、`300313 + activity_breakout`、`300303 + gs_pullback_confirm`、`300295 + activity_breakout`、`300302 + activity_breakout`、`300305 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 255 条，`gs_raw_buy` 77 条，`gs_pullback_confirm` 48 条，`volume_base_breakout` 45 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 8900 行、425 个候选、425 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1142`、`missing_baseline_result/missing_optuna_result = 358`、`ok/missing_optuna_result = 27`；1527 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1500`、`missing_optuna_result = 385`，`missing_investigation_counts` 合计 1885 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 11 条，以及生产基线缺行且 Optuna 无入场信号 6 条。
- 局部 Optuna 第九十批真实 batch 已完成：offset 1780 后再跑 20 只股票，累计覆盖前 1800 只股票、9000 组 `(stock, formula)`。累计采纳结果为 428 条通过、8572 条拒绝，dry-run replacement 428 行；`--resume` 复跑确认 `new_rows=0`。
- 第九十批新增通过样例包括 `300326 + gs_pullback_confirm`、`300320 + gs_raw_buy`、`300333 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 255 条，`gs_raw_buy` 79 条，`gs_pullback_confirm` 49 条，`volume_base_breakout` 45 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 9000 行、428 个候选、428 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1152`、`missing_baseline_result/missing_optuna_result = 361`、`ok/missing_optuna_result = 28`；1541 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1513`、`missing_optuna_result = 389`，`missing_investigation_counts` 合计 1902 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 10 条、生产基线缺行且 Optuna 无入场信号 3 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第九十一批真实 batch 已完成：offset 1800 后再跑 20 只股票，累计覆盖前 1820 只股票、9100 组 `(stock, formula)`。累计采纳结果为 434 条通过、8666 条拒绝，dry-run replacement 434 行；`--resume` 复跑确认 `new_rows=0`。
- 第九十一批新增通过样例包括 `300349 + activity_breakout`、`300352 + activity_breakout`、`300346 + gs_raw_buy`、`300358 + activity_breakout`、`300351 + gs_raw_buy`、`300341 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 259 条，`gs_raw_buy` 81 条，`gs_pullback_confirm` 49 条，`volume_base_breakout` 45 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 9100 行、434 个候选、434 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1158`、`missing_baseline_result/missing_optuna_result = 364`、`ok/missing_optuna_result = 29`；1551 条缺失/异常组合均有 `baseline_investigation` / `optuna_investigation`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1522`、`missing_optuna_result = 393`，`missing_investigation_counts` 合计 1915 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 6 条、生产基线缺行且 Optuna 无入场信号 3 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第九十二批真实 batch 已完成：offset 1820 后再跑 20 只股票，累计覆盖前 1840 只股票、9200 组 `(stock, formula)`。累计采纳结果为 437 条通过、8763 条拒绝，dry-run replacement 437 行；`--resume` 复跑确认 `new_rows=0`。
- 第九十二批新增通过样例包括 `300368 + gs_pullback_confirm`、`300359 + activity_breakout`、`300382 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 261 条，`gs_raw_buy` 81 条，`gs_pullback_confirm` 50 条，`volume_base_breakout` 45 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 9200 行、437 个候选、437 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1171`、`missing_baseline_result/missing_optuna_result = 366`、`ok/missing_optuna_result = 29`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1537`、`missing_optuna_result = 395`，`missing_investigation_counts` 合计 1932 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 13 条、生产基线缺行且 Optuna 无入场信号 2 条。
- 修正策略研究 API 的 merge plan 摘要解析：`_optional_int` 现在正确返回整数，批次 merge plan 暴露 `source_row_count=9200`、`replacement_schema_rows=437`、`replacement_fields_ok=True`，避免 UI/API 把已有 replacement 表误判为 schema 缺失。
- 局部 Optuna 第九十三批真实 batch 已完成：offset 1840 后再跑 20 只股票，累计覆盖前 1860 只股票、9300 组 `(stock, formula)`。累计采纳结果为 441 条通过、8859 条拒绝，dry-run replacement 441 行；`--resume` 复跑确认 `new_rows=0`。
- 第九十三批新增通过样例包括 `300404 + volume_base_breakout`、`300405 + activity_breakout`、`300393 + gs_pullback_confirm`、`300399 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 262 条，`gs_raw_buy` 81 条，`gs_pullback_confirm` 52 条，`volume_base_breakout` 46 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 9300 行、441 个候选、441 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1185`、`missing_baseline_result/missing_optuna_result = 369`、`ok/missing_optuna_result = 30`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1554`、`missing_optuna_result = 399`，`missing_investigation_counts` 合计 1953 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 14 条、生产基线缺行且 Optuna 无入场信号 3 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第九十四批真实 batch 已完成：offset 1860 后再跑 20 只股票，累计覆盖前 1880 只股票、9400 组 `(stock, formula)`。累计采纳结果为 444 条通过、8956 条拒绝，dry-run replacement 444 行；`--resume` 复跑确认 `new_rows=0`。
- 第九十四批新增通过样例包括 `300422 + activity_breakout`、`300408 + activity_breakout`、`300418 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 265 条，`gs_raw_buy` 81 条，`gs_pullback_confirm` 52 条，`volume_base_breakout` 46 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 9400 行、444 个候选、444 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1193`、`missing_baseline_result/missing_optuna_result = 372`、`ok/missing_optuna_result = 30`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1565`、`missing_optuna_result = 402`，`missing_investigation_counts` 合计 1967 个单侧缺失原因计数；本批新增查因为生产基线 `stock_formula_best.csv` 缺股票/公式行 8 条、生产基线缺行且 Optuna 无入场信号 3 条。
- 局部 Optuna 第九十五批真实 batch 已完成：offset 1880 后再跑 20 只股票，累计覆盖前 1900 只股票、9500 组 `(stock, formula)`。累计采纳结果为 447 条通过、9053 条拒绝，dry-run replacement 447 行；`--resume` 复跑确认 `new_rows=0`。
- 第九十五批新增通过样例包括 `300445 + activity_breakout`、`300444 + gs_pullback_confirm`、`300442 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 266 条，`gs_raw_buy` 81 条，`gs_pullback_confirm` 53 条，`volume_base_breakout` 47 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 9500 行、447 个候选、447 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1199`、`missing_baseline_result/missing_optuna_result = 379`、`ok/missing_optuna_result = 30`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1578`、`missing_optuna_result = 409`，`missing_investigation_counts` 合计 1987 个单侧缺失原因计数；本批新增查因为生产基线缺股票/公式行且 Optuna 无入场信号 7 条，以及仅生产基线 `stock_formula_best.csv` 缺股票/公式行 6 条。
- 局部 Optuna 第九十六批真实 batch 已完成：offset 1900 后再跑 20 只股票，累计覆盖前 1920 只股票、9600 组 `(stock, formula)`。累计采纳结果为 451 条通过、9149 条拒绝，dry-run replacement 451 行；`--resume` 复跑确认 `new_rows=0`。
- 第九十六批新增通过样例包括 `300458 + activity_breakout`、`300461 + activity_breakout`、`300448 + gs_raw_buy`、`300461 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 268 条，`gs_raw_buy` 82 条，`gs_pullback_confirm` 53 条，`volume_base_breakout` 48 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 9600 行、451 个候选、451 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1207`、`missing_baseline_result/missing_optuna_result = 384`、`ok/missing_optuna_result = 30`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1591`、`missing_optuna_result = 414`，`missing_investigation_counts` 合计 2005 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 8 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 5 条。
- 局部 Optuna 第九十七批真实 batch 已完成：offset 1920 后再跑 20 只股票，累计覆盖前 1940 只股票、9700 组 `(stock, formula)`。累计采纳结果为 453 条通过、9247 条拒绝，dry-run replacement 453 行；`--resume` 复跑确认 `new_rows=0`。
- 第九十七批新增通过样例包括 `300481 + activity_breakout`、`300471 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 269 条，`gs_raw_buy` 82 条，`gs_pullback_confirm` 54 条，`volume_base_breakout` 48 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 9700 行、453 个候选、453 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1220`、`missing_baseline_result/missing_optuna_result = 385`、`ok/missing_optuna_result = 30`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1605`、`missing_optuna_result = 415`，`missing_investigation_counts` 合计 2020 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 13 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 1 条。
- 局部 Optuna 第九十八批真实 batch 已完成：offset 1940 后再跑 20 只股票，累计覆盖前 1960 只股票、9800 组 `(stock, formula)`。累计采纳结果为 457 条通过、9343 条拒绝，dry-run replacement 457 行；`--resume` 复跑确认 `new_rows=0`。
- 第九十八批新增通过样例包括 `300501 + activity_breakout`、`300487 + activity_breakout`、`300490 + gs_pullback_confirm`、`300487 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 271 条，`gs_raw_buy` 83 条，`gs_pullback_confirm` 55 条，`volume_base_breakout` 48 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 9800 行、457 个候选、457 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1233`、`missing_baseline_result/missing_optuna_result = 387`、`ok/missing_optuna_result = 32`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1620`、`missing_optuna_result = 419`，`missing_investigation_counts` 合计 2039 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 13 条、生产基线缺股票/公式行且 Optuna 无入场信号 2 条，以及 Optuna 单侧无入场信号 2 条。
- 局部 Optuna 第九十九批真实 batch 已完成：offset 1960 后再跑 20 只股票，累计覆盖前 1980 只股票、9900 组 `(stock, formula)`。累计采纳结果为 462 条通过、9438 条拒绝，dry-run replacement 462 行；`--resume` 复跑确认 `new_rows=0`。
- 第九十九批新增通过样例包括 `300514 + gs_pullback_confirm`、`300508 + activity_breakout`、`300509 + gs_pullback_confirm`、`300512 + gs_raw_buy`、`300521 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 273 条，`gs_raw_buy` 84 条，`gs_pullback_confirm` 57 条，`volume_base_breakout` 48 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 9900 行、462 个候选、462 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1241`、`missing_baseline_result/missing_optuna_result = 391`、`ok/missing_optuna_result = 32`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1632`、`missing_optuna_result = 423`，`missing_investigation_counts` 合计 2055 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 8 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 4 条。
- 局部 Optuna 第一百批真实 batch 已完成：offset 1980 后再跑 20 只股票，累计覆盖前 2000 只股票、10000 组 `(stock, formula)`。累计采纳结果为 468 条通过、9532 条拒绝，dry-run replacement 468 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百批新增通过样例包括 `300546 + activity_breakout`、`300542 + activity_breakout`、`300530 + gs_raw_buy`、`300538 + gs_pullback_confirm`、`300546 + gs_raw_buy`、`300538 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 275 条，`gs_raw_buy` 87 条，`gs_pullback_confirm` 58 条，`volume_base_breakout` 48 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 10000 行、468 个候选、468 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1249`、`missing_baseline_result/missing_optuna_result = 395`、`ok/missing_optuna_result = 32`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1644`、`missing_optuna_result = 427`，`missing_investigation_counts` 合计 2071 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 8 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 4 条。
- 局部 Optuna 第一百零一批真实 batch 已完成：offset 2000 后再跑 20 只股票，累计覆盖前 2020 只股票、10100 组 `(stock, formula)`。累计采纳结果为 471 条通过、9629 条拒绝，dry-run replacement 471 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百零一批新增通过样例包括 `300562 + activity_breakout`、`300566 + gs_raw_buy`、`300562 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 276 条，`gs_raw_buy` 88 条，`gs_pullback_confirm` 59 条，`volume_base_breakout` 48 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 10100 行、471 个候选、471 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1260`、`missing_baseline_result/missing_optuna_result = 399`、`ok/missing_optuna_result = 33`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1659`、`missing_optuna_result = 432`，`missing_investigation_counts` 合计 2091 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 11 条、生产基线缺股票/公式行且 Optuna 无入场信号 4 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第一百零二批真实 batch 已完成：offset 2020 后再跑 20 只股票，累计覆盖前 2040 只股票、10200 组 `(stock, formula)`。累计采纳结果为 475 条通过、9725 条拒绝，dry-run replacement 475 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百零二批新增通过样例包括 `300581 + gs_raw_buy`、`300572 + gs_pullback_confirm`、`300576 + gs_raw_buy`、`300585 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 277 条，`gs_raw_buy` 90 条，`gs_pullback_confirm` 60 条，`volume_base_breakout` 48 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 10200 行、475 个候选、475 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1274`、`missing_baseline_result/missing_optuna_result = 405`、`ok/missing_optuna_result = 33`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1679`、`missing_optuna_result = 438`，`missing_investigation_counts` 合计 2117 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 14 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 6 条。
- 局部 Optuna 第一百零三批真实 batch 已完成：offset 2040 后再跑 20 只股票，累计覆盖前 2060 只股票、10300 组 `(stock, formula)`。累计采纳结果为 478 条通过、9822 条拒绝，dry-run replacement 478 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百零三批新增通过样例包括 `300604 + gs_pullback_confirm`、`300599 + volume_base_breakout`、`300609 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 277 条，`gs_raw_buy` 90 条，`gs_pullback_confirm` 62 条，`volume_base_breakout` 49 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 10300 行、478 个候选、478 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1282`、`missing_baseline_result/missing_optuna_result = 411`、`ok/missing_optuna_result = 34`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1693`、`missing_optuna_result = 445`，`missing_investigation_counts` 合计 2138 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 8 条、生产基线缺股票/公式行且 Optuna 无入场信号 6 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第一百零四批真实 batch 已完成：offset 2060 后再跑 20 只股票，累计覆盖前 2080 只股票、10400 组 `(stock, formula)`。累计采纳结果为 480 条通过、9920 条拒绝，dry-run replacement 480 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百零四批新增通过样例包括 `300625 + activity_breakout`、`300629 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 278 条，`gs_raw_buy` 91 条，`gs_pullback_confirm` 62 条，`volume_base_breakout` 49 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 10400 行、480 个候选、480 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1296`、`missing_baseline_result/missing_optuna_result = 412`、`ok/missing_optuna_result = 35`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1708`、`missing_optuna_result = 447`，`missing_investigation_counts` 合计 2155 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 14 条、Optuna 单侧无入场信号 1 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 1 条。
- 局部 Optuna 第一百零五批真实 batch 已完成：offset 2080 后再跑 20 只股票，累计覆盖前 2100 只股票、10500 组 `(stock, formula)`。累计采纳结果为 487 条通过、10013 条拒绝，dry-run replacement 487 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百零五批新增通过样例包括 `300650 + activity_breakout`、`300636 + activity_breakout`、`300645 + activity_breakout`、`300647 + activity_breakout`、`300633 + activity_breakout`、`300643 + gs_raw_buy`、`300650 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 283 条，`gs_raw_buy` 93 条，`gs_pullback_confirm` 62 条，`volume_base_breakout` 49 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 10500 行、487 个候选、487 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1308`、`missing_baseline_result/missing_optuna_result = 418`、`ok/missing_optuna_result = 35`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1726`、`missing_optuna_result = 453`，`missing_investigation_counts` 合计 2179 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 12 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 6 条。
- 局部 Optuna 第一百零六批真实 batch 已完成：offset 2100 后再跑 20 只股票，累计覆盖前 2120 只股票、10600 组 `(stock, formula)`。累计采纳结果为 492 条通过、10108 条拒绝，dry-run replacement 492 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百零六批新增通过样例包括 `300670 + activity_breakout`、`300663 + activity_breakout`、`300669 + volume_base_breakout`、`300670 + gs_raw_buy`、`300656 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 285 条，`gs_raw_buy` 95 条，`gs_pullback_confirm` 62 条，`volume_base_breakout` 50 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 10600 行、492 个候选、492 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1322`、`missing_baseline_result/missing_optuna_result = 425`、`ok/missing_optuna_result = 36`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1747`、`missing_optuna_result = 461`，`missing_investigation_counts` 合计 2208 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 14 条、生产基线缺股票/公式行且 Optuna 无入场信号 7 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第一百零七批真实 batch 已完成：offset 2120 后再跑 20 只股票，累计覆盖前 2140 只股票、10700 组 `(stock, formula)`。累计采纳结果为 496 条通过、10204 条拒绝，dry-run replacement 496 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百零七批新增通过样例包括 `300687 + activity_breakout`、`300679 + activity_breakout`、`300675 + activity_breakout`、`300690 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 288 条，`gs_raw_buy` 96 条，`gs_pullback_confirm` 62 条，`volume_base_breakout` 50 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 10700 行、496 个候选、496 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1334`、`missing_baseline_result/missing_optuna_result = 428`、`ok/missing_optuna_result = 37`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1762`、`missing_optuna_result = 465`，`missing_investigation_counts` 合计 2227 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 12 条、生产基线缺股票/公式行且 Optuna 无入场信号 3 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第一百零八批真实 batch 已完成：offset 2140 后再跑 20 只股票，累计覆盖前 2160 只股票、10800 组 `(stock, formula)`。累计采纳结果为 499 条通过、10301 条拒绝，dry-run replacement 499 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百零八批新增通过样例包括 `300711 + activity_breakout`、`300708 + gs_raw_buy`、`300713 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 289 条，`gs_raw_buy` 98 条，`gs_pullback_confirm` 62 条，`volume_base_breakout` 50 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 10800 行、499 个候选、499 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1349`、`missing_baseline_result/missing_optuna_result = 432`、`ok/missing_optuna_result = 37`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1781`、`missing_optuna_result = 469`，`missing_investigation_counts` 合计 2250 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 15 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 4 条。
- 局部 Optuna 第一百零九批真实 batch 已完成：offset 2160 后再跑 20 只股票，累计覆盖前 2180 只股票、10900 组 `(stock, formula)`。累计采纳结果为 501 条通过、10399 条拒绝，dry-run replacement 501 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百零九批新增通过样例包括 `300725 + activity_breakout`、`300732 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 290 条，`gs_raw_buy` 98 条，`gs_pullback_confirm` 63 条，`volume_base_breakout` 50 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 10900 行、501 个候选、501 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1359`、`missing_baseline_result/missing_optuna_result = 439`、`ok/missing_optuna_result = 38`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1798`、`missing_optuna_result = 477`，`missing_investigation_counts` 合计 2275 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 10 条、生产基线缺股票/公式行且 Optuna 无入场信号 7 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第一百一十批真实 batch 已完成：offset 2180 后再跑 20 只股票，累计覆盖前 2200 只股票、11000 组 `(stock, formula)`。累计采纳结果为 508 条通过、10492 条拒绝，dry-run replacement 508 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百一十批新增通过样例包括 `300743 + activity_breakout`、`300760 + activity_breakout`、`300757 + activity_breakout`、`300745 + gs_pullback_confirm`、`300740 + activity_breakout`、`300741 + gs_raw_buy`、`300746 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 295 条，`gs_raw_buy` 99 条，`gs_pullback_confirm` 64 条，`volume_base_breakout` 50 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 11000 行、508 个候选、508 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1374`、`missing_baseline_result/missing_optuna_result = 444`、`ok/missing_optuna_result = 38`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1818`、`missing_optuna_result = 482`，`missing_investigation_counts` 合计 2300 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 15 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 5 条。
- 局部 Optuna 第一百一十一批真实 batch 已完成：offset 2200 后再跑 20 只股票，累计覆盖前 2220 只股票、11100 组 `(stock, formula)`。累计采纳结果为 513 条通过、10587 条拒绝，dry-run replacement 513 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百一十一批新增通过样例包括 `300761 + activity_breakout`、`300767 + activity_breakout`、`300770 + volume_base_breakout`、`300775 + activity_breakout`、`300769 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 299 条，`gs_raw_buy` 99 条，`gs_pullback_confirm` 64 条，`volume_base_breakout` 51 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 11100 行、513 个候选、513 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1385`、`missing_baseline_result/missing_optuna_result = 447`、`ok/missing_optuna_result = 40`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1832`、`missing_optuna_result = 487`，`missing_investigation_counts` 合计 2319 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 11 条、生产基线缺股票/公式行且 Optuna 无入场信号 3 条，以及 Optuna 单侧无入场信号 2 条。
- 局部 Optuna 第一百一十二批真实 batch 已完成：offset 2220 后再跑 20 只股票，累计覆盖前 2240 只股票、11200 组 `(stock, formula)`。累计采纳结果为 519 条通过、10681 条拒绝，dry-run replacement 519 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百一十二批新增通过样例包括 `300797 + gs_pullback_confirm`、`300792 + activity_breakout`、`300802 + activity_breakout`、`300785 + activity_breakout`、`300788 + activity_breakout`、`300790 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 303 条，`gs_raw_buy` 99 条，`gs_pullback_confirm` 66 条，`volume_base_breakout` 51 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 11200 行、519 个候选、519 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1401`、`missing_baseline_result/missing_optuna_result = 448`、`ok/missing_optuna_result = 41`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1849`、`missing_optuna_result = 489`，`missing_investigation_counts` 合计 2338 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 16 条、生产基线缺股票/公式行且 Optuna 无入场信号 1 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第一百一十三批真实 batch 已完成：offset 2240 后再跑 20 只股票，累计覆盖前 2260 只股票、11300 组 `(stock, formula)`。累计采纳结果为 528 条通过、10772 条拒绝，dry-run replacement 528 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百一十三批新增通过样例包括 `300818 + activity_breakout`、`300823 + activity_breakout`、`300818 + gs_pullback_confirm`、`300812 + gs_pullback_confirm`、`300811 + gs_raw_buy`、`300805 + gs_raw_buy`、`300814 + gs_pullback_confirm`、`300812 + activity_breakout`、`300805 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 306 条，`gs_raw_buy` 101 条，`gs_pullback_confirm` 70 条，`volume_base_breakout` 51 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 11300 行、528 个候选、528 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1409`、`missing_baseline_result/missing_optuna_result = 454`、`ok/missing_optuna_result = 42`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1863`、`missing_optuna_result = 496`，`missing_investigation_counts` 合计 2359 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 8 条、生产基线缺股票/公式行且 Optuna 无入场信号 6 条，以及 Optuna 单侧无入场信号 1 条。
- 局部 Optuna 第一百一十四批真实 batch 已完成：offset 2260 后再跑 20 只股票，累计覆盖前 2280 只股票、11400 组 `(stock, formula)`。累计采纳结果为 536 条通过、10864 条拒绝，dry-run replacement 536 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百一十四批新增通过样例包括 `300827 + activity_breakout`、`300832 + volume_base_breakout`、`300841 + activity_breakout`、`300837 + activity_breakout`、`300832 + activity_breakout`、`300837 + gs_pullback_confirm`、`300834 + gs_raw_buy`、`300835 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 310 条，`gs_raw_buy` 103 条，`gs_pullback_confirm` 71 条，`volume_base_breakout` 52 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 11400 行、536 个候选、536 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1422`、`missing_baseline_result/missing_optuna_result = 458`、`ok/missing_optuna_result = 42`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。API 批次级状态计数为 `missing_baseline_result = 1880`、`missing_optuna_result = 500`，`missing_investigation_counts` 合计 2380 个单侧缺失原因计数；本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 13 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 4 条。
- 局部 Optuna 第一百一十五批真实 batch 已完成：offset 2280 后再跑 20 只股票，累计覆盖前 2300 只股票、11500 组 `(stock, formula)`。累计采纳结果为 538 条通过、10962 条拒绝，dry-run replacement 538 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百一十五批新增通过样例包括 `300863 + activity_breakout`、`300854 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 311 条，`gs_raw_buy` 103 条，`gs_pullback_confirm` 71 条，`volume_base_breakout` 53 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 11500 行、538 个候选、538 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1432`、`missing_baseline_result/missing_optuna_result = 462`、`ok/missing_optuna_result = 42`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。第 115 批后 `Research Cache` 已刷新为 32298 行、local Optuna 10996 行、候选 538 行；`Incremental Evaluator` 为 32298 行且 `dirty=0`；`Drift Trigger` 为 32298 行，`watch=5169`、`reevaluate=0`、`reoptimize=0`。
- 已新增可恢复工作流 checkpoint：`scripts/workflow_checkpoint.py` 会写入 `analysis/workflow_checkpoint.json` 与 `analysis/workflow_checkpoint.md`。Mac 重启或 Terminal 崩溃后，先运行 `python scripts/workflow_checkpoint.py --print`，按输出的 `resume_commands[0]` 继续；当前 checkpoint 指向下一 offset `2300`，并记录 CodeGraph 与 complexity optimizer skill 是否可用。
- 局部 Optuna 第一百一十六批真实 batch 已完成：offset 2300 后再跑 20 只股票，累计覆盖前 2320 只股票、11600 组 `(stock, formula)`。累计采纳结果为 539 条通过、11061 条拒绝，dry-run replacement 539 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百一十六批新增通过样例为 `300883 + gs_raw_buy`，`score_delta=7.018143`、`validation_delta=13.157025`、`sell_rule=fixed_20`、`holding_days=20`。当前通过候选按公式分布：`activity_breakout` 311 条，`gs_raw_buy` 104 条，`gs_pullback_confirm` 71 条，`volume_base_breakout` 53 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 11600 行、539 个候选、539 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1443`、`missing_baseline_result/missing_optuna_result = 465`、`ok/missing_optuna_result = 43`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 11 条、生产基线缺股票/公式行且 Optuna 无入场信号 3 条，以及 Optuna 单侧无入场信号 1 条。
- 第 116 批后 `Research Cache` 已刷新为 32394 行、local Optuna 11092 行、生产基线 21302 行、候选 539 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 32394 行且 `dirty=0`；`Drift Trigger` 为 32394 行，`none=27203`、`watch=5191`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为覆盖 2320 只股票，下一 offset `2320`。
- 已将 `complexity-optimizer + CodeGraph` 协作审计纳入长期治理：新增 `analysis/complexity_codegraph_audit.md`，记录当前 CodeGraph 覆盖状态、复杂度扫描热点、优化队列和验收规则。当前 CodeGraph 可用于核心 Python 文件的依赖检查，但索引只覆盖 6 个 Python 文件、192 个节点、375 条边，尚未覆盖 `scripts/formula_local_optuna_batch.py` 与 `index.html`，因此后续重大优化前必须先刷新/扩展图谱。
- 复杂度治理待办：优先做 `main.py` API 聚合单遍计数和排序复用；随后评估 `formula_engine.py` 的 `ma_base_breakout_signals` 前缀和优化、`compute.py` 交易汇总 helper 抽取、`scripts/formula_local_optuna.py` benchmark-only 仪表化。每次优化必须带 CodeGraph 覆盖检查、complexity audit、语义变化声明、验证命令和无生产 merge 副作用说明。
- 局部 Optuna 第一百一十七批真实 batch 已完成：offset 2320 后再跑 20 只股票，累计覆盖前 2340 只股票、11700 组 `(stock, formula)`。累计采纳结果为 542 条通过、11158 条拒绝，dry-run replacement 542 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百一十七批新增通过样例包括 `300903 + activity_breakout`、`300887 + gs_raw_buy`、`300887 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 313 条，`gs_raw_buy` 105 条，`gs_pullback_confirm` 71 条，`volume_base_breakout` 53 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 11700 行、542 个候选、542 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1454`、`missing_baseline_result/missing_optuna_result = 471`、`ok/missing_optuna_result = 44`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 11 条、生产基线缺股票/公式行且 Optuna 无入场信号 6 条，以及 Optuna 单侧无入场信号 1 条。
- 第 117 批后 `Research Cache` 已刷新为 32487 行、local Optuna 11185 行、生产基线 21302 行、候选 542 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 32487 行且 `dirty=0`；`Drift Trigger` 为 32487 行，`none=27279`、`watch=5208`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为覆盖 2340 只股票，下一 offset `2340`。
- 局部 Optuna 第一百一十八批真实 batch 已完成：offset 2340 后再跑 20 只股票，累计覆盖前 2360 只股票、11800 组 `(stock, formula)`。累计采纳结果为 544 条通过、11256 条拒绝，dry-run replacement 544 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百一十八批新增通过样例包括 `300911 + activity_breakout`、`300910 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 315 条，`gs_raw_buy` 105 条，`gs_pullback_confirm` 71 条，`volume_base_breakout` 53 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 11800 行、544 个候选、544 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1463`、`missing_baseline_result/missing_optuna_result = 474`、`ok/missing_optuna_result = 44`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 9 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 3 条。
- 第 118 批后 `Research Cache` 已刷新为 32584 行、local Optuna 11282 行、生产基线 21302 行、候选 544 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 32584 行且 `dirty=0`；`Drift Trigger` 为 32584 行，`none=27355`、`watch=5229`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为覆盖 2360 只股票，下一 offset `2360`。
- 局部 Optuna 第一百一十九批真实 batch 已完成：offset 2360 后再跑 20 只股票，累计覆盖前 2380 只股票、11900 组 `(stock, formula)`。累计采纳结果为 551 条通过、11349 条拒绝，dry-run replacement 551 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百一十九批新增通过样例包括 `300928 + activity_breakout`、`300932 + volume_base_breakout`、`300939 + volume_base_breakout`、`300938 + activity_breakout`、`300942 + activity_breakout`、`300947 + activity_breakout`、`300936 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 319 条，`gs_raw_buy` 105 条，`gs_pullback_confirm` 72 条，`volume_base_breakout` 55 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 11900 行、551 个候选、551 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1472`、`missing_baseline_result/missing_optuna_result = 478`、`ok/missing_optuna_result = 45`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 9 条、生产基线缺股票/公式行且 Optuna 无入场信号 4 条，以及 Optuna 单侧无入场信号 1 条。
- 第 119 批后 `Research Cache` 已刷新为 32679 行、local Optuna 11377 行、生产基线 21302 行、候选 551 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 32679 行且 `dirty=0`；`Drift Trigger` 为 32679 行，`none=27425`、`watch=5254`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为覆盖 2380 只股票，下一 offset `2380`。
- 中断恢复机制已升级为 `checkpoint + latest-only snapshot`：`scripts/workflow_checkpoint.py --brief` 输出人类可读恢复摘要，`--next` 只输出下一条命令；每次 checkpoint 会删除旧 `analysis/recovery_snapshot/latest/` 并重建最新 snapshot。snapshot 只复制 `goal.md`、`agent.md`、顶层方案、checkpoint 与恢复脚本，大型 CSV/DuckDB 只记录在 `artifact_manifest.json`，不复制旧版本以节约空间。
- 局部 Optuna 第一百二十批真实 batch 已完成：offset 2380 后再跑 20 只股票，累计覆盖前 2400 只股票、12000 组 `(stock, formula)`。累计采纳结果为 556 条通过、11444 条拒绝，dry-run replacement 556 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百二十批新增通过样例包括 `300962 + volume_base_breakout`、`300967 + volume_base_breakout`、`300959 + activity_breakout`、`300969 + volume_base_breakout`、`300964 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 321 条，`gs_raw_buy` 105 条，`gs_pullback_confirm` 72 条，`volume_base_breakout` 58 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 12000 行、556 个候选、556 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1488`、`missing_baseline_result/missing_optuna_result = 480`、`ok/missing_optuna_result = 45`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 16 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 2 条。
- 第 120 批后 `Research Cache` 已刷新为 32777 行、local Optuna 11475 行、生产基线 21302 行、候选 556 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 32777 行且 `dirty=0`；`Drift Trigger` 为 32777 行，`none=27502`、`watch=5275`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为覆盖 2400 只股票，下一 offset `2400`。
- 中断后恢复检查发现 `formula_activity_breakout` 与 `formula_volume_base_breakout` 缓存 stale；已按 checkpoint 只重建这两个公式缓存，`python scripts/strategy_rebuild_audit.py` 现报告 `ready_formula_caches=5`，`python scripts/workflow_checkpoint.py --brief` 现报告 `consistency.ready=True`、`next_action=run_next_batch`。磁盘仍偏低，继续批处理前需保持小批量执行并监控剩余空间。
- 修复 `scripts/research_cache_build.py` 的相对路径处理：CLI 传入 `analysis/...csv` 时先解析到项目根目录，避免 `Path.relative_to(ROOT)` 报错；该修复已通过 `python -m py_compile scripts/research_cache_build.py scripts/workflow_checkpoint.py scripts/strategy_rebuild_audit.py` 验证。
- 局部 Optuna 第一百二十一批真实 batch 已完成：offset 2400 后再跑 20 只股票，覆盖 `300970` 至 `300990` 区间内 20 只股票，累计覆盖前 2420 只股票、12100 组 `(stock, formula)`。累计采纳结果为 565 条通过、11535 条拒绝，dry-run replacement 565 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百二十一批新增通过样例包括 `300983 + gs_pullback_confirm`、`300970 + activity_breakout`、`300980 + activity_breakout`、`300976 + activity_breakout`、`300977 + activity_breakout`、`300971 + activity_breakout`、`300976 + gs_raw_buy`、`300981 + volume_base_breakout`、`300975 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 326 条，`gs_raw_buy` 106 条，`gs_pullback_confirm` 74 条，`volume_base_breakout` 59 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 12100 行、565 个候选、565 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1500`、`missing_baseline_result/missing_optuna_result = 483`、`ok/missing_optuna_result = 45`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 12 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 3 条。
- 第 121 批后 `Research Cache` 已刷新为 32874 行、local Optuna 11572 行、生产基线 21302 行、候选 565 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 32874 行且 `dirty=0`；`Drift Trigger` 为 32874 行，`none=27579`、`watch=5295`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为覆盖 2420 只股票，下一 offset `2420`。
- 局部 Optuna 第一百二十二批真实 batch 已完成：offset 2420 后再跑 20 只股票，覆盖 `300991` 至 `301010` 区间内 20 只股票，累计覆盖前 2440 只股票、12200 组 `(stock, formula)`。累计采纳结果为 570 条通过、11630 条拒绝，dry-run replacement 570 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百二十二批新增通过样例包括 `300998 + gs_pullback_confirm`、`300992 + activity_breakout`、`301003 + gs_pullback_confirm`、`301010 + activity_breakout`、`301007 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 329 条，`gs_raw_buy` 106 条，`gs_pullback_confirm` 76 条，`volume_base_breakout` 59 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 12200 行、570 个候选、570 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1513`、`missing_baseline_result/missing_optuna_result = 488`、`ok/missing_optuna_result = 46`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 13 条、生产基线缺股票/公式行且 Optuna 无入场信号 5 条，以及 Optuna 单侧无入场信号 1 条。
- 第 122 批后 `Research Cache` 已刷新为 32968 行、local Optuna 11666 行、生产基线 21302 行、候选 570 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 32968 行且 `dirty=0`；`Drift Trigger` 为 32968 行，`none=27655`、`watch=5313`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为覆盖 2440 只股票，下一 offset `2440`。
- 局部 Optuna 第一百二十三批真实 batch 主运行已完成：offset 2440 后再跑 20 只股票，覆盖 `301011` 至 `301031` 区间内 20 只股票，累计批处理产物达到 12300 组 `(stock, formula)`，主运行新增 `new_rows=100`。本批 adoption 与 dry-run merge plan 已刷新为 572 个候选、11728 条拒绝、572 个 replacement；新增候选为 `301026 + activity_breakout` 与 `301012 + gs_raw_buy`。累计候选按公式分布：`activity_breakout` 330 条，`gs_raw_buy` 107 条，`gs_pullback_confirm` 76 条，`volume_base_breakout` 59 条。
- 第 123 批尚未完成最终闭环：`--resume` 幂等复跑、`Research Cache`、`Incremental Evaluator`、`Drift Trigger` 与 checkpoint/snapshot 刷新被另一个后台仿真进程持有 `market.duckdb` 锁阻塞。锁来源为 `backend/scripts/run_paper_sim_v2.py --variant champion_baseline_20260520T102611 ...` 的 Python 进程；不要直接跑第 124 批，待锁释放后先从 offset `2440` 做 `--resume` 复跑，再刷新状态库和 checkpoint。
- 已增强中断恢复 checkpoint 的外部锁识别：`scripts/workflow_checkpoint.py --brief` 现在会检测 `market.duckdb` 的 `lsof` 持有者，并在有外部锁时把 `next_action` 标记为 `wait_external_duckdb_lock`，输出持锁 PID/进程和等待命令，避免误提示直接重建缓存或继续跑批。当前锁仍由 PID `571` 的 `backend/scripts/run_paper_sim_v2.py` 持有，因此仍不能继续第 124 批。
- 外部 paper simulation 锁已释放后，第 123 批最终闭环已补完：`Research Cache` 刷新为 33064 行、local Optuna 11762 行、生产基线 21302 行、候选 572 行；`Incremental Evaluator` 为 33064 行且 `dirty=0`；`Drift Trigger` 为 33064 行，`none=27729`、`watch=5335`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已恢复为 `consistency.ready=True`、下一 offset `2460`。
- 局部 Optuna 第一百二十四批真实 batch 已完成：offset 2460 后再跑 20 只股票，覆盖 `301032` 至 `301053` 区间内 20 只股票，累计覆盖前 2480 只股票、12400 组 `(stock, formula)`。累计采纳结果为 579 条通过、11821 条拒绝，dry-run replacement 579 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百二十四批新增通过样例包括 `301047 + activity_breakout`、`301046 + activity_breakout`、`301047 + gs_raw_buy`、`301037 + activity_breakout`、`301039 + gs_raw_buy`、`301050 + gs_raw_buy`、`301049 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 334 条，`gs_raw_buy` 110 条，`gs_pullback_confirm` 76 条，`volume_base_breakout` 59 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 12400 行、579 个候选、579 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1535`、`missing_baseline_result/missing_optuna_result = 498`、`ok/missing_optuna_result = 46`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 9 条，以及生产基线缺股票/公式行且 Optuna 无入场信号 6 条。
- 第 124 批后 `Research Cache` 已刷新为 33158 行、local Optuna 11856 行、生产基线 21302 行、候选 579 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 33158 行且 `dirty=0`；`Drift Trigger` 为 33158 行，`none=27805`、`watch=5353`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为覆盖 2480 只股票，下一 offset `2480`。
- 局部 Optuna 第一百二十五批真实 batch 已完成：offset 2480 后再跑 20 只股票，覆盖 `301055` 至 `301076` 区间内 20 只股票，累计覆盖前 2500 只股票、12500 组 `(stock, formula)`。累计采纳结果为 584 条通过、11916 条拒绝，dry-run replacement 584 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百二十五批新增通过样例包括 `301059 + activity_breakout`、`301061 + activity_breakout`、`301065 + activity_breakout`、`301069 + activity_breakout`、`301069 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 338 条，`gs_raw_buy` 111 条，`gs_pullback_confirm` 76 条，`volume_base_breakout` 59 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 12500 行、584 个候选、584 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1550`、`missing_baseline_result/missing_optuna_result = 500`、`ok/missing_optuna_result = 47`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 15 条、生产基线缺股票/公式行且 Optuna 无入场信号 2 条，以及 Optuna 单侧无入场信号 1 条。
- 第 125 批后 `Research Cache` 已刷新为 33255 行、local Optuna 11953 行、生产基线 21302 行、候选 584 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 33255 行且 `dirty=0`；`Drift Trigger` 为 33255 行，`none=27882`、`watch=5373`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为覆盖 2500 只股票，下一 offset `2500`。
- 局部 Optuna 第一百二十六批真实 batch 已完成：offset 2500 后再跑 20 只股票，覆盖 `301077` 至 `301098` 区间内 20 只股票，累计覆盖前 2520 只股票、12600 组 `(stock, formula)`。累计采纳结果为 589 条通过、12011 条拒绝，dry-run replacement 589 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百二十六批新增通过样例包括 `301087 + gs_pullback_confirm`、`301095 + activity_breakout`、`301078 + activity_breakout`、`301083 + activity_breakout`、`301092 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 342 条，`gs_raw_buy` 111 条，`gs_pullback_confirm` 77 条，`volume_base_breakout` 59 条。
- 策略研究接口的 `local_optuna.batch` 已显示累计 12600 行、589 个候选、589 个 dry-run replacement。当前缺失状态组合为：`missing_baseline_result/ok = 1561`、`missing_baseline_result/missing_optuna_result = 506`、`ok/missing_optuna_result = 48`；全部缺失都有 `baseline_investigation` / `optuna_investigation`，`missing_without_reason = 0`，缺失仍作为查因项，不允许默认参数或默认收益回填。本批新增查因为仅生产基线 `stock_formula_best.csv` 缺股票/公式行 11 条、生产基线缺股票/公式行且 Optuna 无入场信号 6 条，以及 Optuna 单侧无入场信号 1 条。
- 第 126 批后 `Research Cache` 已刷新为 33348 行、local Optuna 12046 行、生产基线 21302 行、候选 589 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 33348 行且 `dirty=0`；`Drift Trigger` 为 33348 行，`none=27955`、`watch=5393`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为覆盖 2520 只股票，下一 offset `2520`。
- 第 126 批闭环后又检测到新的外部 paper simulation 持有 `market.duckdb` 锁：`backend/scripts/run_paper_sim_v2.py --variant champion_minhold5_20260520_105535 --config-path backend/config/paper_sim_ml_score_champion_minhold5.yaml`，PID `73446`。当前 checkpoint 会报告 `next_action=wait_external_duckdb_lock`；不要继续第 127 批，待锁释放后先运行 `python scripts/workflow_checkpoint.py --brief`，按 next_action 补齐缓存/状态一致性。
- 外部 paper simulation 锁释放后，局部 Optuna 第一百二十七批真实 batch 已完成：offset 2520 后再跑 20 只股票，覆盖 `301099` 至 `301120` 区间内 20 只股票，累计覆盖前 2540 只股票、12700 组 `(stock, formula)`。累计采纳结果为 601 条通过、12099 条拒绝，dry-run replacement 601 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百二十七批新增通过样例包括 `301102 + activity_breakout`、`301108 + activity_breakout`、`301100 + activity_breakout`、`301117 + activity_breakout`、`301118 + activity_breakout`、`301106 + activity_breakout`、`301115 + activity_breakout`、`301112 + activity_breakout`、`301110 + activity_breakout`、`301119 + gs_pullback_confirm`、`301105 + volume_base_breakout`、`301119 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 351 条，`gs_raw_buy` 112 条，`gs_pullback_confirm` 78 条，`volume_base_breakout` 60 条。
- 第 127 批后 `Research Cache` 已刷新为 33442 行、local Optuna 12140 行、生产基线 21302 行、候选 601 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 33442 行且 `dirty=0`；`Drift Trigger` 为 33442 行，`none=28027`、`watch=5415`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2540 只股票，下一 offset `2540`。
- 局部 Optuna 第一百二十八批主运行已完成：offset 2540 后再跑 20 只股票，覆盖 `301121` 至 `301149` 区间内 20 只股票，累计覆盖前 2560 只股票、12800 组 `(stock, formula)`；`--resume` 复跑确认 `new_rows=0`。adoption 与 dry-run merge plan 已刷新为 603 个候选、12197 条拒绝、603 个 replacement；本批新增候选为 `301138 + gs_pullback_confirm` 与 `301121 + gs_raw_buy`。当前候选按公式分布：`activity_breakout` 351 条，`gs_raw_buy` 113 条，`gs_pullback_confirm` 79 条，`volume_base_breakout` 60 条。
- 第 128 批尚未完成最终闭环：`Research Cache` 已刷新为 33539 行、local Optuna 12237 行、生产基线 21302 行、候选 603 行，数据最新日期 `2026-05-19`；但 `Incremental Evaluator` 构建因新的外部 paper simulation 持有 `market.duckdb` 而无法读取最新行情日期失败。当前外部锁来自 `backend/scripts/run_paper_sim_v2.py --variant champion_minhold15_20260520_111606 --config-path backend/config/paper_sim_ml_score_champion_minhold15.yaml`，PID `24961`。checkpoint 已报告 `next_action=wait_external_duckdb_lock`，不要继续第 129 批；待锁释放后先运行 `python scripts/workflow_checkpoint.py --brief`，再补跑 `python scripts/incremental_eval_build.py`、`python scripts/drift_trigger_build.py`、`python scripts/strategy_rebuild_audit.py` 和 checkpoint。
- checkpoint 外部锁语义已继续修正：当 `market.duckdb` 被其他进程锁住时，`scripts/workflow_checkpoint.py --brief` 不再把公式缓存显示成误导性的 `0/5 ready`，而是显示 `formula_caches: unknown (market.duckdb locked; freshness check skipped)`；`analysis/workflow_checkpoint.md` 和 latest snapshot 同步记录 `formula_caches_status=unknown_due_to_market_db_lock`，warning 名称为 `formula_caches_unknown_due_to_market_db_lock`。这表示缓存新鲜度未检查，不等于缓存缺失。
- 外部 paper simulation 锁释放后，第 128 批最终闭环已补完：`Incremental Evaluator` 为 33539 行且 `dirty=0`；`Drift Trigger` 为 33539 行，`none=28100`、`watch=5439`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已恢复为 `consistency.ready=True`，下一 offset `2560`。
- 局部 Optuna 第一百二十九批真实 batch 已完成：offset 2560 后再跑 20 只股票，覆盖 `301150` 至 `301171` 区间内 20 只股票，累计覆盖前 2580 只股票、12900 组 `(stock, formula)`。累计采纳结果为 608 条通过、12292 条拒绝，dry-run replacement 608 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百二十九批新增通过样例包括 `301156 + volume_base_breakout`、`301168 + gs_pullback_confirm`、`301162 + gs_raw_buy`、`301159 + gs_raw_buy`、`301162 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 351 条，`gs_raw_buy` 115 条，`gs_pullback_confirm` 80 条，`volume_base_breakout` 62 条。
- 第 129 批后 `Research Cache` 已刷新为 33635 行、local Optuna 12333 行、生产基线 21302 行、候选 608 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 33635 行且 `dirty=0`；`Drift Trigger` 为 33635 行，`none=28178`、`watch=5457`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2580 只股票，下一 offset `2580`。
- 局部 Optuna 第一百三十批真实 batch 已完成：offset 2580 后再跑 20 只股票，覆盖 `301172` 至 `301193` 区间内 20 只股票，累计覆盖前 2600 只股票、13000 组 `(stock, formula)`。累计采纳结果为 613 条通过、12387 条拒绝，dry-run replacement 613 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百三十批新增通过样例包括 `301186 + gs_pullback_confirm`、`301188 + activity_breakout`、`301191 + activity_breakout`、`301180 + activity_breakout`、`301179 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 354 条，`gs_raw_buy` 116 条，`gs_pullback_confirm` 81 条，`volume_base_breakout` 62 条。
- 第 130 批后 `Research Cache` 已刷新为 33729 行、local Optuna 12427 行、生产基线 21302 行、候选 613 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 33729 行且 `dirty=0`；`Drift Trigger` 为 33729 行，`none=28250`、`watch=5479`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2600 只股票，下一 offset `2600`。
- 局部 Optuna 第一百三十一批真实 batch 已完成：offset 2600 后再跑 20 只股票，覆盖 `301195` 至 `301216` 区间内 20 只股票，累计覆盖前 2620 只股票、13100 组 `(stock, formula)`。累计采纳结果为 622 条通过、12478 条拒绝，dry-run replacement 622 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百三十一批新增通过样例包括 `301195 + activity_breakout`、`301199 + activity_breakout`、`301203 + gs_raw_buy`、`301197 + gs_raw_buy`、`301207 + gs_raw_buy`、`301205 + activity_breakout`、`301201 + gs_raw_buy`、`301197 + activity_breakout`、`301196 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 358 条，`gs_raw_buy` 120 条，`gs_pullback_confirm` 82 条，`volume_base_breakout` 62 条。
- 第 131 批后 `Research Cache` 已刷新为 33823 行、local Optuna 12521 行、生产基线 21302 行、候选 622 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 33823 行且 `dirty=0`；`Drift Trigger` 为 33823 行，`none=28325`、`watch=5498`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2620 只股票，下一 offset `2620`。
- 局部 Optuna 第一百三十二批真实 batch 已完成：offset 2620 后再跑 20 只股票，覆盖 `301217` 至 `301237` 区间内 20 只股票，累计覆盖前 2640 只股票、13200 组 `(stock, formula)`。累计采纳结果为 624 条通过、12576 条拒绝，dry-run replacement 624 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百三十二批新增通过样例包括 `301219 + activity_breakout`、`301230 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 360 条，`gs_raw_buy` 120 条，`gs_pullback_confirm` 82 条，`volume_base_breakout` 62 条。
- 第 132 批后 `Research Cache` 已刷新为 33919 行、local Optuna 12617 行、生产基线 21302 行、候选 624 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 33919 行且 `dirty=0`；`Drift Trigger` 为 33919 行，`none=28402`、`watch=5517`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2640 只股票，下一 offset `2640`。
- 局部 Optuna 第一百三十三批真实 batch 已完成：offset 2640 后再跑 20 只股票，覆盖 `301238` 至 `301269` 区间内 20 只股票，累计覆盖前 2660 只股票、13300 组 `(stock, formula)`。累计采纳结果为 628 条通过、12672 条拒绝，dry-run replacement 628 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百三十三批新增通过样例包括 `301255 + activity_breakout`、`301266 + activity_breakout`、`301267 + gs_raw_buy`、`301255 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 362 条，`gs_raw_buy` 122 条，`gs_pullback_confirm` 82 条，`volume_base_breakout` 62 条。
- 第 133 批后 `Research Cache` 已刷新为 34014 行、local Optuna 12712 行、生产基线 21302 行、候选 628 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34014 行且 `dirty=0`；`Drift Trigger` 为 34014 行，`none=28474`、`watch=5540`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2660 只股票，下一 offset `2660`。
- 局部 Optuna 第一百三十四批真实 batch 已完成：offset 2660 后再跑 20 只股票，覆盖 `301270` 至 `301292` 区间内 20 只股票，累计覆盖前 2680 只股票、13400 组 `(stock, formula)`。累计采纳结果为 632 条通过、12768 条拒绝，dry-run replacement 632 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百三十四批新增通过样例包括 `301270 + gs_pullback_confirm`、`301288 + activity_breakout`、`301283 + gs_pullback_confirm`、`301282 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 363 条，`gs_raw_buy` 123 条，`gs_pullback_confirm` 84 条，`volume_base_breakout` 62 条。
- 第 134 批后 `Research Cache` 已刷新为 34108 行、local Optuna 12806 行、生产基线 21302 行、候选 632 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34108 行且 `dirty=0`；`Drift Trigger` 为 34108 行，`none=28549`、`watch=5559`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2680 只股票，下一 offset `2680`。
- 局部 Optuna 第一百三十五批真实 batch 已完成：offset 2680 后再跑 20 只股票，覆盖 `301293` 至 `301314` 区间内 20 只股票，累计覆盖前 2700 只股票、13500 组 `(stock, formula)`。累计采纳结果为 637 条通过、12863 条拒绝，dry-run replacement 637 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百三十五批新增通过样例包括 `301313 + activity_breakout`、`301295 + activity_breakout`、`301298 + gs_pullback_confirm`、`301314 + gs_raw_buy`、`301297 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 366 条，`gs_raw_buy` 124 条，`gs_pullback_confirm` 85 条，`volume_base_breakout` 62 条。
- 第 135 批后 `Research Cache` 已刷新为 34202 行、local Optuna 12900 行、生产基线 21302 行、候选 637 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34202 行且 `dirty=0`；`Drift Trigger` 为 34202 行，`none=28618`、`watch=5584`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2700 只股票，下一 offset `2700`。
- 局部 Optuna 第一百三十六批真实 batch 已完成：offset 2700 后再跑 20 只股票，覆盖 `301315` 至 `301336` 区间内 20 只股票，累计覆盖前 2720 只股票、13600 组 `(stock, formula)`。累计采纳结果为 641 条通过、12959 条拒绝，dry-run replacement 641 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百三十六批新增通过样例包括 `301332 + activity_breakout`、`301315 + gs_raw_buy`、`301328 + gs_raw_buy`、`301323 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 368 条，`gs_raw_buy` 126 条，`gs_pullback_confirm` 85 条，`volume_base_breakout` 62 条。
- 第 136 批后 `Research Cache` 已刷新为 34297 行、local Optuna 12995 行、生产基线 21302 行、候选 641 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34297 行且 `dirty=0`；`Drift Trigger` 为 34297 行，`none=28692`、`watch=5605`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2720 只股票，下一 offset `2720`。
- 局部 Optuna 第一百三十七批真实 batch 已完成：offset 2720 后再跑 20 只股票，覆盖 `301337` 至 `301368` 区间内 20 只股票，累计覆盖前 2740 只股票、13700 组 `(stock, formula)`。累计采纳结果为 645 条通过、13055 条拒绝，dry-run replacement 645 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百三十七批新增通过样例包括 `301363 + activity_breakout`、`301359 + activity_breakout`、`301362 + activity_breakout`、`301361 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 372 条，`gs_raw_buy` 126 条，`gs_pullback_confirm` 85 条，`volume_base_breakout` 62 条。
- 第 137 批后 `Research Cache` 已刷新为 34388 行、local Optuna 13086 行、生产基线 21302 行、候选 645 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34388 行且 `dirty=0`；`Drift Trigger` 为 34388 行，`none=28764`、`watch=5624`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2740 只股票，下一 offset `2740`。
- 局部 Optuna 第一百三十八批真实 batch 已完成：offset 2740 后再跑 20 只股票，覆盖 `301369` 至 `301392` 区间内 20 只股票，累计覆盖前 2760 只股票、13800 组 `(stock, formula)`。累计采纳结果为 650 条通过、13150 条拒绝，dry-run replacement 650 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百三十八批新增通过样例包括 `301378 + activity_breakout`、`301379 + activity_breakout`、`301386 + activity_breakout`、`301372 + activity_breakout`、`301389 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 376 条，`gs_raw_buy` 127 条，`gs_pullback_confirm` 85 条，`volume_base_breakout` 62 条。
- 第 138 批后 `Research Cache` 已刷新为 34480 行、local Optuna 13178 行、生产基线 21302 行、候选 650 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34480 行且 `dirty=0`；`Drift Trigger` 为 34480 行，`none=28838`、`watch=5642`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2760 只股票，下一 offset `2760`。
- 局部 Optuna 第一百三十九批真实 batch 已完成：offset 2760 后再跑 20 只股票，覆盖 `301393` 至 `301459` 区间内 20 只股票，累计覆盖前 2780 只股票、13900 组 `(stock, formula)`。累计采纳结果为 653 条通过、13247 条拒绝，dry-run replacement 653 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百三十九批新增通过样例包括 `301399 + gs_raw_buy`、`301446 + activity_breakout`、`301413 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 378 条，`gs_raw_buy` 128 条，`gs_pullback_confirm` 85 条，`volume_base_breakout` 62 条。
- 第 139 批后 `Research Cache` 已刷新为 34572 行、local Optuna 13270 行、生产基线 21302 行、候选 653 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34572 行且 `dirty=0`；`Drift Trigger` 为 34572 行，`none=28900`、`watch=5672`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2780 只股票，下一 offset `2780`。
- 局部 Optuna 第一百四十批真实 batch 已完成：offset 2780 后再跑 20 只股票，覆盖 `301468` 至 `301511` 区间内 20 只股票，累计覆盖前 2800 只股票、14000 组 `(stock, formula)`。累计采纳结果为 656 条通过、13344 条拒绝，dry-run replacement 656 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百四十批新增通过样例包括 `301489 + activity_breakout`、`301507 + activity_breakout`、`301503 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 381 条，`gs_raw_buy` 128 条，`gs_pullback_confirm` 85 条，`volume_base_breakout` 62 条。
- 第 140 批后 `Research Cache` 已刷新为 34655 行、local Optuna 13353 行、生产基线 21302 行、候选 656 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34655 行且 `dirty=0`；`Drift Trigger` 为 34655 行，`none=28960`、`watch=5695`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2800 只股票，下一 offset `2800`。
- 局部 Optuna 第一百四十一批真实 batch 已完成：offset 2800 后再跑 20 只股票，覆盖 `301512` 至 `301550` 区间内 20 只股票，累计覆盖前 2820 只股票、14100 组 `(stock, formula)`。累计采纳结果为 658 条通过、13442 条拒绝，dry-run replacement 658 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百四十一批新增通过样例包括 `301520 + gs_raw_buy`、`301533 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 381 条，`gs_raw_buy` 129 条，`gs_pullback_confirm` 86 条，`volume_base_breakout` 62 条。
- 第 141 批后 `Research Cache` 已刷新为 34742 行、local Optuna 13440 行、生产基线 21302 行、候选 658 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34742 行且 `dirty=0`；`Drift Trigger` 为 34742 行，`none=29019`、`watch=5723`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2820 只股票，下一 offset `2820`。
- 局部 Optuna 第一百四十二批真实 batch 已完成：offset 2820 后再跑 20 只股票，覆盖 `301551` 至 `301584` 区间内 20 只股票，累计覆盖前 2840 只股票、14200 组 `(stock, formula)`。累计采纳结果为 660 条通过、13540 条拒绝，dry-run replacement 660 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百四十二批新增通过样例包括 `301559 + activity_breakout`、`301567 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 383 条，`gs_raw_buy` 129 条，`gs_pullback_confirm` 86 条，`volume_base_breakout` 62 条。
- 第 142 批后 `Research Cache` 已刷新为 34823 行、local Optuna 13521 行、生产基线 21302 行、候选 660 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34823 行且 `dirty=0`；`Drift Trigger` 为 34823 行，`none=29077`、`watch=5746`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2840 只股票，下一 offset `2840`。
- 局部 Optuna 第一百四十三批真实 batch 已完成：offset 2840 后再跑 20 只股票，覆盖 `301585` 至 `301609` 区间内 20 只股票，累计覆盖前 2860 只股票、14300 组 `(stock, formula)`。累计采纳结果为 663 条通过、13637 条拒绝，dry-run replacement 663 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百四十三批新增通过样例包括 `301591 + activity_breakout`、`301607 + activity_breakout`、`301600 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 386 条，`gs_raw_buy` 129 条，`gs_pullback_confirm` 86 条，`volume_base_breakout` 62 条。
- 第 143 批后 `Research Cache` 已刷新为 34901 行、local Optuna 13599 行、生产基线 21302 行、候选 663 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34901 行且 `dirty=0`；`Drift Trigger` 为 34901 行，`none=29131`、`watch=5770`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2860 只股票，下一 offset `2860`。
- 局部 Optuna 第一百四十四批真实 batch 已完成：offset 2860 后再跑 20 只股票，覆盖 `301611` 至 `301666` 区间内 20 只股票，累计覆盖前 2880 只股票、14400 组 `(stock, formula)`。累计采纳结果为 664 条通过、13736 条拒绝，dry-run replacement 664 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百四十四批新增通过样例包括 `301616 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 386 条，`gs_raw_buy` 130 条，`gs_pullback_confirm` 86 条，`volume_base_breakout` 62 条。
- 第 144 批后 `Research Cache` 已刷新为 34971 行、local Optuna 13669 行、生产基线 21302 行、候选 664 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 34971 行且 `dirty=0`；`Drift Trigger` 为 34971 行，`none=29176`、`watch=5795`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2880 只股票，下一 offset `2880`。
- 局部 Optuna 第一百四十五批真实 batch 已完成：offset 2880 后再跑 20 只股票，覆盖 `301667` 至 `600016` 区间内 20 只股票，累计覆盖前 2900 只股票、14500 组 `(stock, formula)`。累计采纳结果为 664 条通过、13836 条拒绝，dry-run replacement 664 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百四十五批没有新增通过候选。当前通过候选按公式分布：`activity_breakout` 386 条，`gs_raw_buy` 130 条，`gs_pullback_confirm` 86 条，`volume_base_breakout` 62 条。
- 第 145 批后 `Research Cache` 已刷新为 35045 行、local Optuna 13743 行、生产基线 21302 行、候选 664 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 35045 行且 `dirty=0`；`Drift Trigger` 为 35045 行，`none=29232`、`watch=5813`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2900 只股票，下一 offset `2900`。
- 局部 Optuna 第一百四十六批真实 batch 已完成：offset 2900 后再跑 20 只股票，覆盖 `600017` 至 `600038` 区间内 20 只股票，累计覆盖前 2920 只股票、14600 组 `(stock, formula)`。累计采纳结果为 667 条通过、13933 条拒绝，dry-run replacement 667 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百四十六批新增通过样例包括 `600027 + gs_pullback_confirm`、`600017 + activity_breakout`、`600029 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 387 条，`gs_raw_buy` 131 条，`gs_pullback_confirm` 87 条，`volume_base_breakout` 62 条。
- 第 146 批后 `Research Cache` 已刷新为 35140 行、local Optuna 13838 行、生产基线 21302 行、候选 667 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 35140 行且 `dirty=0`；`Drift Trigger` 为 35140 行，`none=29309`、`watch=5831`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2920 只股票，下一 offset `2920`。
- 局部 Optuna 第一百四十七批真实 batch 已完成：offset 2920 后再跑 20 只股票，覆盖 `600039` 至 `600071` 区间内 20 只股票，累计覆盖前 2940 只股票、14700 组 `(stock, formula)`。累计采纳结果为 672 条通过、14028 条拒绝，dry-run replacement 672 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百四十七批新增通过样例包括 `600055 + gs_raw_buy`、`600071 + activity_breakout`、`600048 + activity_breakout`、`600064 + gs_raw_buy`、`600062 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 389 条，`gs_raw_buy` 134 条，`gs_pullback_confirm` 87 条，`volume_base_breakout` 62 条。
- 第 147 批后 `Research Cache` 已刷新为 35233 行、local Optuna 13931 行、生产基线 21302 行、候选 672 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 35233 行且 `dirty=0`；`Drift Trigger` 为 35233 行，`none=29387`、`watch=5846`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2940 只股票，下一 offset `2940`。
- 局部 Optuna 第一百四十八批真实 batch 已完成：offset 2940 后再跑 20 只股票，覆盖 `600072` 至 `600100` 区间内 20 只股票，累计覆盖前 2960 只股票、14800 组 `(stock, formula)`。累计采纳结果为 677 条通过、14123 条拒绝，dry-run replacement 677 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百四十八批新增通过样例包括 `600097 + activity_breakout`、`600095 + activity_breakout`、`600094 + activity_breakout`、`600094 + gs_raw_buy`、`600099 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 392 条，`gs_raw_buy` 136 条，`gs_pullback_confirm` 87 条，`volume_base_breakout` 62 条。
- 第 148 批后 `Research Cache` 已刷新为 35331 行、local Optuna 14029 行、生产基线 21302 行、候选 677 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 35331 行且 `dirty=0`；`Drift Trigger` 为 35331 行，`none=29470`、`watch=5861`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2960 只股票，下一 offset `2960`。
- 局部 Optuna 第一百四十九批真实 batch 已完成：offset 2960 后再跑 20 只股票，覆盖 `600101` 至 `600123` 区间内 20 只股票，累计覆盖前 2980 只股票、14900 组 `(stock, formula)`。累计采纳结果为 682 条通过、14218 条拒绝，dry-run replacement 682 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百四十九批新增通过样例包括 `600116 + volume_base_breakout`、`600120 + volume_base_breakout`、`600110 + activity_breakout`、`600101 + activity_breakout`、`600103 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 395 条，`gs_raw_buy` 136 条，`gs_pullback_confirm` 87 条，`volume_base_breakout` 64 条。
- 第 149 批后 `Research Cache` 已刷新为 35426 行、local Optuna 14124 行、生产基线 21302 行、候选 682 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 35426 行且 `dirty=0`；`Drift Trigger` 为 35426 行，`none=29550`、`watch=5876`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 2980 只股票，下一 offset `2980`。
- 局部 Optuna 第一百五十批真实 batch 已完成：offset 2980 后再跑 20 只股票，覆盖 `600125` 至 `600152` 区间内 20 只股票，累计覆盖前 3000 只股票、15000 组 `(stock, formula)`。累计采纳结果为 686 条通过、14314 条拒绝，dry-run replacement 686 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百五十批新增通过样例包括 `600133 + activity_breakout`、`600125 + volume_base_breakout`、`600128 + gs_raw_buy`、`600138 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 396 条，`gs_raw_buy` 138 条，`gs_pullback_confirm` 87 条，`volume_base_breakout` 65 条。
- 第 150 批后 `Research Cache` 已刷新为 35523 行、local Optuna 14221 行、生产基线 21302 行、候选 686 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 35523 行且 `dirty=0`；`Drift Trigger` 为 35523 行，`none=29628`、`watch=5895`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3000 只股票，下一 offset `3000`。
- 局部 Optuna 第一百五十一批真实 batch 已完成：offset 3000 后再跑 20 只股票，覆盖 `600153` 至 `600176` 区间内 20 只股票，累计覆盖前 3020 只股票、15100 组 `(stock, formula)`。累计采纳结果为 687 条通过、14413 条拒绝，dry-run replacement 687 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百五十一批新增通过样例包括 `600153 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 397 条，`gs_raw_buy` 138 条，`gs_pullback_confirm` 87 条，`volume_base_breakout` 65 条。
- 第 151 批后 `Research Cache` 已刷新为 35620 行、local Optuna 14318 行、生产基线 21302 行、候选 687 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 35620 行且 `dirty=0`；`Drift Trigger` 为 35620 行，`none=29702`、`watch=5918`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3020 只股票，下一 offset `3020`。
- 局部 Optuna 第一百五十二批真实 batch 已完成：offset 3020 后再跑 20 只股票，覆盖 `600177` 至 `600199` 区间内 20 只股票，累计覆盖前 3040 只股票、15200 组 `(stock, formula)`。累计采纳结果为 690 条通过、14510 条拒绝，dry-run replacement 690 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百五十二批新增通过样例包括 `600183 + activity_breakout`、`600195 + gs_pullback_confirm`、`600193 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 399 条，`gs_raw_buy` 138 条，`gs_pullback_confirm` 88 条，`volume_base_breakout` 65 条。
- 第 152 批后 `Research Cache` 已刷新为 35719 行、local Optuna 14417 行、生产基线 21302 行、候选 690 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 35719 行且 `dirty=0`；`Drift Trigger` 为 35719 行，`none=29781`、`watch=5938`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3040 只股票，下一 offset `3040`。
- 局部 Optuna 第一百五十三批真实 batch 已完成：offset 3040 后再跑 20 只股票，覆盖 `600201` 至 `600228` 区间内 20 只股票，累计覆盖前 3060 只股票、15300 组 `(stock, formula)`。累计采纳结果为 696 条通过、14604 条拒绝，dry-run replacement 696 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百五十三批新增通过样例包括 `600222 + activity_breakout`、`600221 + activity_breakout`、`600206 + gs_pullback_confirm`、`600201 + gs_pullback_confirm`、`600218 + gs_raw_buy`、`600228 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 401 条，`gs_raw_buy` 139 条，`gs_pullback_confirm` 90 条，`volume_base_breakout` 66 条。
- 第 153 批后 `Research Cache` 已刷新为 35816 行、local Optuna 14514 行、生产基线 21302 行、候选 696 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 35816 行且 `dirty=0`；`Drift Trigger` 为 35816 行，`none=29863`、`watch=5953`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3060 只股票，下一 offset `3060`。
- 局部 Optuna 第一百五十四批真实 batch 已完成：offset 3060 后再跑 20 只股票，覆盖 `600229` 至 `600255` 区间内 20 只股票，累计覆盖前 3080 只股票、15400 组 `(stock, formula)`。累计采纳结果为 701 条通过、14699 条拒绝，dry-run replacement 701 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百五十四批新增通过样例包括 `600250 + activity_breakout`、`600246 + gs_pullback_confirm`、`600238 + activity_breakout`、`600230 + activity_breakout`、`600229 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 404 条，`gs_raw_buy` 140 条，`gs_pullback_confirm` 91 条，`volume_base_breakout` 66 条。
- 第 154 批后 `Research Cache` 已刷新为 35913 行、local Optuna 14611 行、生产基线 21302 行、候选 701 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 35913 行且 `dirty=0`；`Drift Trigger` 为 35913 行，`none=29939`、`watch=5974`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3080 只股票，下一 offset `3080`。
- 局部 Optuna 第一百五十五批真实 batch 已完成：offset 3080 后再跑 20 只股票，覆盖 `600256` 至 `600282` 区间内 20 只股票，累计覆盖前 3100 只股票、15500 组 `(stock, formula)`。累计采纳结果为 703 条通过、14797 条拒绝，dry-run replacement 703 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百五十五批新增通过样例包括 `600267 + activity_breakout`、`600272 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 405 条，`gs_raw_buy` 141 条，`gs_pullback_confirm` 91 条，`volume_base_breakout` 66 条。
- 第 155 批后 `Research Cache` 已刷新为 36010 行、local Optuna 14708 行、生产基线 21302 行、候选 703 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36010 行且 `dirty=0`；`Drift Trigger` 为 36010 行，`none=30013`、`watch=5997`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3100 只股票，下一 offset `3100`。
- 局部 Optuna 第一百五十六批真实 batch 已完成：offset 3100 后再跑 20 只股票，覆盖 `600283` 至 `600310` 区间内 20 只股票，累计覆盖前 3120 只股票、15600 组 `(stock, formula)`。累计采纳结果为 708 条通过、14892 条拒绝，dry-run replacement 708 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百五十六批新增通过样例包括 `600305 + volume_base_breakout`、`600302 + gs_pullback_confirm`、`600309 + activity_breakout`、`600310 + activity_breakout`、`600292 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 408 条，`gs_raw_buy` 141 条，`gs_pullback_confirm` 92 条，`volume_base_breakout` 67 条。
- 第 156 批后 `Research Cache` 已刷新为 36108 行、local Optuna 14806 行、生产基线 21302 行、候选 708 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36108 行且 `dirty=0`；`Drift Trigger` 为 36108 行，`none=30090`、`watch=6018`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3120 只股票，下一 offset `3120`。
- 局部 Optuna 第一百五十七批真实 batch 已完成：offset 3120 后再跑 20 只股票，覆盖 `600312` 至 `600336` 区间内 20 只股票，累计覆盖前 3140 只股票、15700 组 `(stock, formula)`。累计采纳结果为 711 条通过、14989 条拒绝，dry-run replacement 711 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百五十七批新增通过样例包括 `600336 + volume_base_breakout`、`600329 + activity_breakout`、`600330 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 410 条，`gs_raw_buy` 141 条，`gs_pullback_confirm` 92 条，`volume_base_breakout` 68 条。
- 第 157 批后 `Research Cache` 已刷新为 36204 行、local Optuna 14902 行、生产基线 21302 行、候选 711 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36204 行且 `dirty=0`；`Drift Trigger` 为 36204 行，`none=30164`、`watch=6040`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3140 只股票，下一 offset `3140`。
- 局部 Optuna 第一百五十八批真实 batch 已完成：offset 3140 后再跑 20 只股票，覆盖 `600337` 至 `600363` 区间内 20 只股票，累计覆盖前 3160 只股票、15800 组 `(stock, formula)`。累计采纳结果为 713 条通过、15087 条拒绝，dry-run replacement 713 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百五十八批新增通过样例包括 `600350 + activity_breakout`、`600362 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 411 条，`gs_raw_buy` 141 条，`gs_pullback_confirm` 92 条，`volume_base_breakout` 69 条。
- 第 158 批后 `Research Cache` 已刷新为 36298 行、local Optuna 14996 行、生产基线 21302 行、候选 713 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36298 行且 `dirty=0`；`Drift Trigger` 为 36298 行，`none=30246`、`watch=6052`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3160 只股票，下一 offset `3160`。
- 局部 Optuna 第一百五十九批真实 batch 已完成：offset 3160 后再跑 20 只股票，覆盖 `600365` 至 `600388` 区间内 20 只股票，累计覆盖前 3180 只股票、15900 组 `(stock, formula)`。累计采纳结果为 714 条通过、15186 条拒绝，dry-run replacement 714 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百五十九批新增通过样例包括 `600375 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 412 条，`gs_raw_buy` 141 条，`gs_pullback_confirm` 92 条，`volume_base_breakout` 69 条。
- 第 159 批后 `Research Cache` 已刷新为 36395 行、local Optuna 15093 行、生产基线 21302 行、候选 714 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36395 行且 `dirty=0`；`Drift Trigger` 为 36395 行，`none=30319`、`watch=6076`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3180 只股票，下一 offset `3180`。
- 局部 Optuna 第一百六十批真实 batch 已完成：offset 3180 后再跑 20 只股票，覆盖 `600389` 至 `600419` 区间内 20 只股票，累计覆盖前 3200 只股票、16000 组 `(stock, formula)`。累计采纳结果为 717 条通过、15283 条拒绝，dry-run replacement 717 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百六十批新增通过样例包括 `600397 + gs_pullback_confirm`、`600405 + activity_breakout`、`600397 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 414 条，`gs_raw_buy` 141 条，`gs_pullback_confirm` 93 条，`volume_base_breakout` 69 条。
- 第 160 批后 `Research Cache` 已刷新为 36492 行、local Optuna 15190 行、生产基线 21302 行、候选 717 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36492 行且 `dirty=0`；`Drift Trigger` 为 36492 行，`none=30397`、`watch=6095`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3200 只股票，下一 offset `3200`。
- 局部 Optuna 第一百六十一批真实 batch 已完成：offset 3200 后再跑 20 只股票，覆盖 `600420` 至 `600456` 区间内 20 只股票，累计覆盖前 3220 只股票、16100 组 `(stock, formula)`。累计采纳结果为 720 条通过、15380 条拒绝，dry-run replacement 720 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百六十一批新增通过样例包括 `600452 + volume_base_breakout`、`600433 + volume_base_breakout`、`600428 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 414 条，`gs_raw_buy` 141 条，`gs_pullback_confirm` 93 条，`volume_base_breakout` 72 条。
- 第 161 批后 `Research Cache` 已刷新为 36586 行、local Optuna 15284 行、生产基线 21302 行、候选 720 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36586 行且 `dirty=0`；`Drift Trigger` 为 36586 行，`none=30476`、`watch=6110`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3220 只股票，下一 offset `3220`。
- 局部 Optuna 第一百六十二批真实 batch 已完成：offset 3220 后再跑 20 只股票，覆盖 `600458` 至 `600487` 区间内 20 只股票，累计覆盖前 3240 只股票、16200 组 `(stock, formula)`。累计采纳结果为 723 条通过、15477 条拒绝，dry-run replacement 723 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百六十二批新增通过样例包括 `600468 + volume_base_breakout`、`600461 + volume_base_breakout`、`600487 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 415 条，`gs_raw_buy` 141 条，`gs_pullback_confirm` 93 条，`volume_base_breakout` 74 条。
- 第 162 批后 `Research Cache` 已刷新为 36681 行、local Optuna 15379 行、生产基线 21302 行、候选 723 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36681 行且 `dirty=0`；`Drift Trigger` 为 36681 行，`none=30554`、`watch=6127`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3240 只股票，下一 offset `3240`。
- 局部 Optuna 第一百六十三批真实 batch 已完成：offset 3240 后再跑 20 只股票，覆盖 `600488` 至 `600510` 区间内 20 只股票，累计覆盖前 3260 只股票、16300 组 `(stock, formula)`。累计采纳结果为 727 条通过、15573 条拒绝，dry-run replacement 727 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百六十三批新增通过样例包括 `600491 + activity_breakout`、`600506 + gs_pullback_confirm`、`600510 + gs_raw_buy`、`600493 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 416 条，`gs_raw_buy` 142 条，`gs_pullback_confirm` 94 条，`volume_base_breakout` 75 条。
- 第 163 批后 `Research Cache` 已刷新为 36777 行、local Optuna 15475 行、生产基线 21302 行、候选 727 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36777 行且 `dirty=0`；`Drift Trigger` 为 36777 行，`none=30629`、`watch=6148`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3260 只股票，下一 offset `3260`。
- 第 164 批启动前曾检测到外部 `market.duckdb` 锁：`backend/scripts/build_market_perception_daily.py --start 2024-11-01 --end 2026-05-19` 持有读句柄，checkpoint 给出 `next_action=wait_external_duckdb_lock`；等待锁释放并重新确认 `consistency.ready=True` 后才继续 batch。
- 局部 Optuna 第一百六十四批真实 batch 已完成：offset 3260 后再跑 20 只股票，覆盖 `600511` 至 `600533` 区间内 20 只股票，累计覆盖前 3280 只股票、16400 组 `(stock, formula)`。累计采纳结果为 732 条通过、15668 条拒绝，dry-run replacement 732 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百六十四批新增通过样例包括 `600511 + activity_breakout`、`600529 + activity_breakout`、`600526 + activity_breakout`、`600516 + gs_raw_buy`、`600526 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 419 条，`gs_raw_buy` 143 条，`gs_pullback_confirm` 95 条，`volume_base_breakout` 75 条。
- 第 164 批后 `Research Cache` 已刷新为 36875 行、local Optuna 15573 行、生产基线 21302 行、候选 732 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36875 行且 `dirty=0`；`Drift Trigger` 为 36875 行，`none=30708`、`watch=6167`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3280 只股票，下一 offset `3280`。
- 局部 Optuna 第一百六十五批真实 batch 已完成：offset 3280 后再跑 20 只股票，覆盖 `600535` 至 `600560` 区间内 20 只股票，累计覆盖前 3300 只股票、16500 组 `(stock, formula)`。累计采纳结果为 736 条通过、15764 条拒绝，dry-run replacement 736 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百六十五批新增通过样例包括 `600560 + volume_base_breakout`、`600543 + activity_breakout`、`600545 + gs_pullback_confirm`、`600543 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 420 条，`gs_raw_buy` 143 条，`gs_pullback_confirm` 97 条，`volume_base_breakout` 76 条。
- 第 165 批后 `Research Cache` 已刷新为 36971 行、local Optuna 15669 行、生产基线 21302 行、候选 736 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 36971 行且 `dirty=0`；`Drift Trigger` 为 36971 行，`none=30783`、`watch=6188`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3300 只股票，下一 offset `3300`。
- 局部 Optuna 第一百六十六批真实 batch 已完成：offset 3300 后再跑 20 只股票，覆盖 `600561` 至 `600583` 区间内 20 只股票，累计覆盖前 3320 只股票、16600 组 `(stock, formula)`。累计采纳结果为 738 条通过、15862 条拒绝，dry-run replacement 738 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百六十六批新增通过样例包括 `600578 + volume_base_breakout`、`600570 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 421 条，`gs_raw_buy` 143 条，`gs_pullback_confirm` 97 条，`volume_base_breakout` 77 条。
- 第 166 批后 `Research Cache` 已刷新为 37067 行、local Optuna 15765 行、生产基线 21302 行、候选 738 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 37067 行且 `dirty=0`；`Drift Trigger` 为 37067 行，`none=30856`、`watch=6211`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3320 只股票，下一 offset `3320`。
- offset 3320 后出现一个短段：脚本只追加 `600584` 至 `600600` 区间内 16 只股票、80 组 `(stock, formula)`，`--resume` 复跑确认 `new_rows=0`；checkpoint 将当前状态记为覆盖前 3336 只股票、16680 组 `(stock, formula)`，下一 offset `3336`。
- offset 3320 短段新增通过样例包括 `600586 + activity_breakout`、`600593 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 423 条，`gs_raw_buy` 143 条，`gs_pullback_confirm` 97 条，`volume_base_breakout` 77 条。
- offset 3320 短段后 `Research Cache` 已刷新为 37145 行、local Optuna 15843 行、生产基线 21302 行、候选 740 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 37145 行且 `dirty=0`；`Drift Trigger` 为 37145 行，`none=30915`、`watch=6230`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3336 只股票，下一 offset `3336`。
- 局部 Optuna 第一百六十七批真实 batch 已完成：offset 3336 后再跑 20 只股票，覆盖 `600601` 至 `600622` 区间内 20 只股票，累计覆盖前 3356 只股票、16780 组 `(stock, formula)`。累计采纳结果为 743 条通过、16037 条拒绝，dry-run replacement 743 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百六十七批新增通过样例包括 `600613 + activity_breakout`、`600601 + gs_pullback_confirm`、`600620 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 424 条，`gs_raw_buy` 143 条，`gs_pullback_confirm` 99 条，`volume_base_breakout` 77 条。
- 第 167 批后 `Research Cache` 已刷新为 37243 行、local Optuna 15941 行、生产基线 21302 行、候选 743 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 37243 行且 `dirty=0`；`Drift Trigger` 为 37243 行，`none=30994`、`watch=6249`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3356 只股票，下一 offset `3356`。
- 局部 Optuna 第一百六十八批真实 batch 已完成：offset 3356 后再跑 20 只股票，覆盖 `600623` 至 `600649` 区间内 20 只股票，累计覆盖前 3376 只股票、16880 组 `(stock, formula)`。累计采纳结果为 747 条通过、16133 条拒绝，dry-run replacement 747 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百六十八批新增通过样例包括 `600636 + activity_breakout`、`600639 + activity_breakout`、`600645 + gs_raw_buy`、`600642 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 426 条，`gs_raw_buy` 145 条，`gs_pullback_confirm` 99 条，`volume_base_breakout` 77 条。
- 第 168 批后 `Research Cache` 已刷新为 37339 行、local Optuna 16037 行、生产基线 21302 行、候选 747 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 37339 行且 `dirty=0`；`Drift Trigger` 为 37339 行，`none=31072`、`watch=6267`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3376 只股票，下一 offset `3376`。
- 局部 Optuna 第一百六十九批真实 batch 已完成：offset 3376 后再跑 20 只股票，覆盖 `600650` 至 `600675` 区间内 20 只股票，累计覆盖前 3396 只股票、16980 组 `(stock, formula)`。累计采纳结果为 750 条通过、16230 条拒绝，dry-run replacement 750 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百六十九批新增通过样例包括 `600667 + activity_breakout`、`600666 + activity_breakout`、`600660 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 428 条，`gs_raw_buy` 146 条，`gs_pullback_confirm` 99 条，`volume_base_breakout` 77 条。
- 第 169 批后 `Research Cache` 已刷新为 37437 行、local Optuna 16135 行、生产基线 21302 行、候选 750 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 37437 行且 `dirty=0`；`Drift Trigger` 为 37437 行，`none=31148`、`watch=6289`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3396 只股票，下一 offset `3396`。
- 局部 Optuna 第一百七十批真实 batch 已完成：offset 3396 后再跑 20 只股票，覆盖 `600676` 至 `600699` 区间内 20 只股票，累计覆盖前 3416 只股票、17080 组 `(stock, formula)`。累计采纳结果为 753 条通过、16327 条拒绝，dry-run replacement 753 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百七十批新增通过样例包括 `600692 + activity_breakout`、`600697 + gs_raw_buy`、`600684 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 429 条，`gs_raw_buy` 147 条，`gs_pullback_confirm` 100 条，`volume_base_breakout` 77 条。
- 第 170 批后 `Research Cache` 已刷新为 37533 行、local Optuna 16231 行、生产基线 21302 行、候选 753 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 37533 行且 `dirty=0`；`Drift Trigger` 为 37533 行，`none=31221`、`watch=6312`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3416 只股票，下一 offset `3416`。
- 局部 Optuna 第一百七十一批真实 batch 已完成：offset 3416 后再跑 20 只股票，覆盖 `600702` 至 `600724` 区间内 20 只股票，累计覆盖前 3436 只股票、17180 组 `(stock, formula)`。累计采纳结果为 760 条通过、16420 条拒绝，dry-run replacement 760 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百七十一批新增通过样例包括 `600711 + activity_breakout`、`600710 + volume_base_breakout`、`600713 + gs_pullback_confirm`、`600721 + gs_raw_buy`、`600710 + gs_raw_buy`、`600716 + gs_pullback_confirm`、`600703 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 431 条，`gs_raw_buy` 149 条，`gs_pullback_confirm` 102 条，`volume_base_breakout` 78 条。
- 第 171 批后 `Research Cache` 已刷新为 37631 行、local Optuna 16329 行、生产基线 21302 行、候选 760 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 37631 行且 `dirty=0`；`Drift Trigger` 为 37631 行，`none=31296`、`watch=6335`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3436 只股票，下一 offset `3436`。
- 局部 Optuna 第一百七十二批真实 batch 已完成：offset 3436 后再跑 20 只股票，覆盖 `600725` 至 `600744` 区间内 20 只股票，累计覆盖前 3456 只股票、17280 组 `(stock, formula)`。累计采纳结果为 766 条通过、16514 条拒绝，dry-run replacement 766 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百七十二批新增通过样例包括 `600732 + gs_raw_buy`、`600742 + activity_breakout`、`600729 + gs_raw_buy`、`600744 + gs_pullback_confirm`、`600734 + activity_breakout`、`600739 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 433 条，`gs_raw_buy` 152 条，`gs_pullback_confirm` 103 条，`volume_base_breakout` 78 条。
- 第 172 批后 `Research Cache` 已刷新为 37726 行、local Optuna 16424 行、生产基线 21302 行、候选 766 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 37726 行且 `dirty=0`；`Drift Trigger` 为 37726 行，`none=31370`、`watch=6356`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3456 只股票，下一 offset `3456`。
- 局部 Optuna 第一百七十三批真实 batch 已完成：offset 3456 后再跑 20 只股票，覆盖 `600745` 至 `600769` 区间内 20 只股票，累计覆盖前 3476 只股票、17380 组 `(stock, formula)`。累计采纳结果为 770 条通过、16610 条拒绝，dry-run replacement 770 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百七十三批新增通过样例包括 `600757 + activity_breakout`、`600753 + gs_pullback_confirm`、`600761 + volume_base_breakout`、`600763 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 435 条，`gs_raw_buy` 152 条，`gs_pullback_confirm` 104 条，`volume_base_breakout` 79 条。
- 第 173 批后 `Research Cache` 已刷新为 37821 行、local Optuna 16519 行、生产基线 21302 行、候选 770 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 37821 行且 `dirty=0`；`Drift Trigger` 为 37821 行，`none=31454`、`watch=6367`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3476 只股票，下一 offset `3476`。
- 局部 Optuna 第一百七十四批真实 batch 已完成：offset 3476 后再跑 20 只股票，覆盖 `600770` 至 `600793` 区间内 20 只股票，累计覆盖前 3496 只股票、17480 组 `(stock, formula)`。累计采纳结果为 775 条通过、16705 条拒绝，dry-run replacement 775 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百七十四批新增通过样例包括 `600789 + activity_breakout`、`600785 + activity_breakout`、`600770 + activity_breakout`、`600771 + gs_raw_buy`、`600790 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 438 条，`gs_raw_buy` 154 条，`gs_pullback_confirm` 104 条，`volume_base_breakout` 79 条。
- 第 174 批后 `Research Cache` 已刷新为 37916 行、local Optuna 16614 行、生产基线 21302 行、候选 775 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 37916 行且 `dirty=0`；`Drift Trigger` 为 37916 行，`none=31533`、`watch=6383`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3496 只股票，下一 offset `3496`。
- 局部 Optuna 第一百七十五批真实 batch 已完成：offset 3496 后再跑 20 只股票，覆盖 `600794` 至 `600818` 区间内 20 只股票，累计覆盖前 3516 只股票、17580 组 `(stock, formula)`。累计采纳结果为 777 条通过、16803 条拒绝，dry-run replacement 777 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百七十五批新增通过样例包括 `600807 + activity_breakout`、`600795 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 439 条，`gs_raw_buy` 155 条，`gs_pullback_confirm` 104 条，`volume_base_breakout` 79 条。
- 第 175 批后 `Research Cache` 已刷新为 38015 行、local Optuna 16713 行、生产基线 21302 行、候选 777 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38015 行且 `dirty=0`；`Drift Trigger` 为 38015 行，`none=31616`、`watch=6399`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3516 只股票，下一 offset `3516`。
- 局部 Optuna 第一百七十六批真实 batch 已完成：offset 3516 后再跑 20 只股票，覆盖 `600819` 至 `600844` 区间内 20 只股票，累计覆盖前 3536 只股票、17680 组 `(stock, formula)`。累计采纳结果为 784 条通过、16896 条拒绝，dry-run replacement 784 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百七十六批新增通过样例包括 `600838 + activity_breakout`、`600839 + activity_breakout`、`600831 + activity_breakout`、`600841 + activity_breakout`、`600838 + volume_base_breakout`、`600844 + activity_breakout`、`600835 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 444 条，`gs_raw_buy` 156 条，`gs_pullback_confirm` 104 条，`volume_base_breakout` 80 条。
- 第 176 批后 `Research Cache` 已刷新为 38109 行、local Optuna 16807 行、生产基线 21302 行、候选 784 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38109 行且 `dirty=0`；`Drift Trigger` 为 38109 行，`none=31695`、`watch=6414`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3536 只股票，下一 offset `3536`。
- 局部 Optuna 第一百七十七批真实 batch 已完成：offset 3536 后再跑 20 只股票，覆盖 `600845` 至 `600867` 区间内 20 只股票，累计覆盖前 3556 只股票、17780 组 `(stock, formula)`。累计采纳结果为 789 条通过、16991 条拒绝，dry-run replacement 789 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百七十七批新增通过样例包括 `600850 + activity_breakout`、`600854 + gs_raw_buy`、`600858 + activity_breakout`、`600857 + activity_breakout`、`600848 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 447 条，`gs_raw_buy` 158 条，`gs_pullback_confirm` 104 条，`volume_base_breakout` 80 条。
- 第 177 批后 `Research Cache` 已刷新为 38205 行、local Optuna 16903 行、生产基线 21302 行、候选 789 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38205 行且 `dirty=0`；`Drift Trigger` 为 38205 行，`none=31771`、`watch=6434`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3556 只股票，下一 offset `3556`。
- 局部 Optuna 第一百七十八批真实 batch 已完成：offset 3556 后再跑 20 只股票，覆盖 `600868` 至 `600889` 区间内 20 只股票，累计覆盖前 3576 只股票、17880 组 `(stock, formula)`。累计采纳结果为 795 条通过、17085 条拒绝，dry-run replacement 795 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百七十八批新增通过样例包括 `600888 + volume_base_breakout`、`600876 + activity_breakout`、`600873 + volume_base_breakout`、`600888 + gs_raw_buy`、`600876 + gs_raw_buy`、`600881 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 449 条，`gs_raw_buy` 160 条，`gs_pullback_confirm` 104 条，`volume_base_breakout` 82 条。
- 第 178 批后 `Research Cache` 已刷新为 38303 行、local Optuna 17001 行、生产基线 21302 行、候选 795 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38303 行且 `dirty=0`；`Drift Trigger` 为 38303 行，`none=31850`、`watch=6453`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3576 只股票，下一 offset `3576`。
- 局部 Optuna 第一百七十九批真实 batch 已完成：offset 3576 后再跑 20 只股票，覆盖 `600892` 至 `600928` 区间内 20 只股票，累计覆盖前 3596 只股票、17980 组 `(stock, formula)`。累计采纳结果为 797 条通过、17183 条拒绝，dry-run replacement 797 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百七十九批新增通过样例包括 `600909 + volume_base_breakout`、`600895 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 450 条，`gs_raw_buy` 160 条，`gs_pullback_confirm` 104 条，`volume_base_breakout` 83 条。
- 第 179 批后 `Research Cache` 已刷新为 38399 行、local Optuna 17097 行、生产基线 21302 行、候选 797 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38399 行且 `dirty=0`；`Drift Trigger` 为 38399 行，`none=31924`、`watch=6475`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3596 只股票，下一 offset `3596`。
- 局部 Optuna 第一百八十批真实 batch 已完成：offset 3596 后再跑 20 只股票，覆盖 `600929` 至 `600968` 区间内 20 只股票，累计覆盖前 3616 只股票、18080 组 `(stock, formula)`。累计采纳结果为 805 条通过、17275 条拒绝，dry-run replacement 805 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百八十批新增通过样例包括 `600959 + activity_breakout`、`600929 + activity_breakout`、`600938 + activity_breakout`、`600933 + gs_raw_buy`、`600966 + activity_breakout`、`600955 + gs_raw_buy`、`600968 + gs_raw_buy`、`600962 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 454 条，`gs_raw_buy` 163 条，`gs_pullback_confirm` 105 条，`volume_base_breakout` 83 条。
- 第 180 批后 `Research Cache` 已刷新为 38493 行、local Optuna 17191 行、生产基线 21302 行、候选 805 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38493 行且 `dirty=0`；`Drift Trigger` 为 38493 行，`none=32003`、`watch=6490`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3616 只股票，下一 offset `3616`。
- 局部 Optuna 第一百八十一批真实 batch 已完成：offset 3616 后再跑 20 只股票，覆盖 `600969` 至 `600992` 区间内 20 只股票，累计覆盖前 3636 只股票、18180 组 `(stock, formula)`。累计采纳结果为 811 条通过、17369 条拒绝，dry-run replacement 811 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百八十一批新增通过样例包括 `600970 + activity_breakout`、`600969 + activity_breakout`、`600981 + activity_breakout`、`600985 + activity_breakout`、`600992 + activity_breakout`、`600971 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 459 条，`gs_raw_buy` 163 条，`gs_pullback_confirm` 106 条，`volume_base_breakout` 83 条。
- 第 181 批后 `Research Cache` 已刷新为 38588 行、local Optuna 17286 行、生产基线 21302 行、候选 811 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38588 行且 `dirty=0`；`Drift Trigger` 为 38588 行，`none=32080`、`watch=6508`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3636 只股票，下一 offset `3636`。
- 局部 Optuna 第一百八十二批真实 batch 已完成：offset 3636 后再跑 20 只股票，覆盖 `600993` 至 `601016` 区间内 20 只股票，累计覆盖前 3656 只股票、18280 组 `(stock, formula)`。累计采纳结果为 815 条通过、17465 条拒绝，dry-run replacement 815 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百八十二批新增通过样例包括 `600993 + gs_pullback_confirm`、`601011 + gs_pullback_confirm`、`601015 + gs_raw_buy`、`601001 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 460 条，`gs_raw_buy` 164 条，`gs_pullback_confirm` 108 条，`volume_base_breakout` 83 条。
- 第 182 批后 `Research Cache` 已刷新为 38687 行、local Optuna 17385 行、生产基线 21302 行、候选 815 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38687 行且 `dirty=0`；`Drift Trigger` 为 38687 行，`none=32159`、`watch=6528`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3656 只股票，下一 offset `3656`。
- 局部 Optuna 第一百八十三批真实 batch 已完成：offset 3656 后再跑 20 只股票，覆盖 `601018` 至 `601089` 区间内 20 只股票，累计覆盖前 3676 只股票、18380 组 `(stock, formula)`。累计采纳结果为 825 条通过、17555 条拒绝，dry-run replacement 825 行；`--resume` 复跑确认 `new_rows=0`。
- 第一百八十三批新增通过样例包括 `601069 + activity_breakout`、`601059 + activity_breakout`、`601018 + activity_breakout`、`601021 + gs_raw_buy`、`601018 + volume_base_breakout`、`601077 + volume_base_breakout`、`601068 + volume_base_breakout`、`601059 + gs_raw_buy`、`601061 + gs_pullback_confirm`、`601088 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 463 条，`gs_raw_buy` 167 条，`gs_pullback_confirm` 109 条，`volume_base_breakout` 86 条。
- 第 183 批后 `Research Cache` 已刷新为 38782 行、local Optuna 17480 行、生产基线 21302 行、候选 825 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38782 行且 `dirty=0`；`Drift Trigger` 为 38782 行，`none=32234`、`watch=6548`、`reevaluate=0`、`reoptimize=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3676 只股票，下一 offset `3676`。
- 局部 Optuna 第一百八十四批真实 batch 已完成：offset 3676 后再跑 20 只股票，覆盖 `601096` 至 `601136` 区间内 20 只股票，累计覆盖前 3696 只股票、18480 组 `(stock, formula)`。累计采纳结果为 831 条通过、17649 条拒绝，dry-run replacement 831 行。
- 第一百八十四批新增通过样例包括 `601117 + activity_breakout`、`601116 + activity_breakout`、`601108 + volume_base_breakout`、`601107 + gs_pullback_confirm`、`601111 + gs_raw_buy`、`601100 + activity_breakout`。
- 第 184 批后 `Research Cache` 已刷新为 38877 行、local Optuna 17575 行、生产基线 21302 行、候选 831 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38877 行且 `dirty=0`；`Drift Trigger` 为 38877 行，`none=32313`、`watch=6564`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3696 只股票，下一 offset `3696`。
- 局部 Optuna 第一百八十五批真实 batch 已完成：offset 3696 后再跑 20 只股票，覆盖 `601137` 至 `601208` 区间内 20 只股票，累计覆盖前 3716 只股票、18580 组 `(stock, formula)`。累计采纳结果为 832 条通过、17748 条拒绝，dry-run replacement 832 行。
- 第一百八十五批新增通过样例为 `601188 + gs_raw_buy`。
- 第 185 批后 `Research Cache` 已刷新为 38975 行、local Optuna 17673 行、生产基线 21302 行、候选 832 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 38975 行且 `dirty=0`；`Drift Trigger` 为 38975 行，`none=32391`、`watch=6584`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3716 只股票，下一 offset `3716`。
- 局部 Optuna 第一百八十六批真实 batch 已完成：offset 3716 后再跑 20 只股票，覆盖 `601211` 至 `601326` 区间内 20 只股票，累计覆盖前 3736 只股票、18680 组 `(stock, formula)`。累计采纳结果为 836 条通过、17844 条拒绝，dry-run replacement 836 行。
- 第一百八十六批新增通过样例包括 `601326 + volume_base_breakout`、`601226 + activity_breakout`、`601226 + gs_raw_buy`、`601212 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 468 条，`gs_raw_buy` 170 条，`gs_pullback_confirm` 110 条，`volume_base_breakout` 88 条。
- 第 186 批后 `Research Cache` 已刷新为 39071 行、local Optuna 17769 行、生产基线 21302 行、候选 836 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 39071 行且 `dirty=0`；`Drift Trigger` 为 39071 行，`none=32475`、`watch=6596`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3736 只股票，下一 offset `3736`，`missing_without_reason=0`。
- 局部 Optuna 第一百八十七批真实 batch 已完成：offset 3736 后再跑 20 只股票，覆盖 `601328` 至 `601518` 区间内 20 只股票，累计覆盖前 3756 只股票、18780 组 `(stock, formula)`。累计采纳结果为 837 条通过、17943 条拒绝，dry-run replacement 837 行。
- 第一百八十七批新增通过样例为 `601500 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 469 条，`gs_raw_buy` 170 条，`gs_pullback_confirm` 110 条，`volume_base_breakout` 88 条。
- 第 187 批后 `Research Cache` 已刷新为 39168 行、local Optuna 17866 行、生产基线 21302 行、候选 837 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 39168 行且 `dirty=0`；`Drift Trigger` 为 39168 行，`none=32555`、`watch=6613`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3756 只股票，下一 offset `3756`，`missing_without_reason=0`。
- 局部 Optuna 第一百八十八批真实 batch 已完成：offset 3756 后再跑 20 只股票，覆盖 `601519` 至 `601615` 区间内 20 只股票，累计覆盖前 3776 只股票、18880 组 `(stock, formula)`。累计采纳结果为 841 条通过、18039 条拒绝，dry-run replacement 841 行。
- 第一百八十八批新增通过样例包括 `601609 + gs_raw_buy`、`601555 + gs_raw_buy`、`601519 + activity_breakout`、`601599 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 470 条，`gs_raw_buy` 173 条，`gs_pullback_confirm` 110 条，`volume_base_breakout` 88 条。
- 第 188 批后 `Research Cache` 已刷新为 39266 行、local Optuna 17964 行、生产基线 21302 行、候选 841 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 39266 行且 `dirty=0`；`Drift Trigger` 为 39266 行，`none=32639`、`watch=6627`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3776 只股票，下一 offset `3776`，`missing_without_reason=0`。
- 局部 Optuna 第一百八十九批真实 batch 已完成：offset 3776 后再跑 20 只股票，覆盖 `601616` 至 `601700` 区间内 20 只股票，累计覆盖前 3796 只股票、18980 组 `(stock, formula)`。累计采纳结果为 850 条通过、18130 条拒绝，dry-run replacement 850 行。
- 第一百八十九批新增通过样例包括 `601669 + gs_pullback_confirm`、`601665 + activity_breakout`、`601666 + activity_breakout`、`601699 + activity_breakout`、`601669 + activity_breakout`、`601619 + gs_pullback_confirm`、`601668 + activity_breakout`、`601686 + gs_raw_buy`、`601628 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 475 条，`gs_raw_buy` 175 条，`gs_pullback_confirm` 112 条，`volume_base_breakout` 88 条。
- 第 189 批后 `Research Cache` 已刷新为 39362 行、local Optuna 18060 行、生产基线 21302 行、候选 850 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 39362 行且 `dirty=0`；`Drift Trigger` 为 39362 行，`none=32717`、`watch=6645`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3796 只股票，下一 offset `3796`，`missing_without_reason=0`。
- 局部 Optuna 第一百九十批真实 batch 已完成：offset 3796 后再跑 20 只股票，覆盖 `601702` 至 `601827` 区间内 20 只股票，累计覆盖前 3816 只股票、19080 组 `(stock, formula)`。累计采纳结果为 854 条通过、18226 条拒绝，dry-run replacement 854 行。
- 第一百九十批新增通过样例包括 `601788 + volume_base_breakout`、`601717 + activity_breakout`、`601789 + gs_raw_buy`、`601811 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 476 条，`gs_raw_buy` 177 条，`gs_pullback_confirm` 112 条，`volume_base_breakout` 89 条。
- 第 190 批后 `Research Cache` 已刷新为 39460 行、local Optuna 18158 行、生产基线 21302 行、候选 854 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 39460 行且 `dirty=0`；`Drift Trigger` 为 39460 行，`none=32795`、`watch=6665`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3816 只股票，下一 offset `3816`，`missing_without_reason=0`。
- 局部 Optuna 第一百九十一批真实 batch 已完成：offset 3816 后再跑 20 只股票，覆盖 `601828` 至 `601899` 区间内 20 只股票，累计覆盖前 3836 只股票、19180 组 `(stock, formula)`。累计采纳结果为 857 条通过、18323 条拒绝，dry-run replacement 857 行。
- 第一百九十一批新增通过样例包括 `601868 + activity_breakout`、`601865 + activity_breakout`、`601828 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 479 条，`gs_raw_buy` 177 条，`gs_pullback_confirm` 112 条，`volume_base_breakout` 89 条。
- 第 191 批后 `Research Cache` 已刷新为 39554 行、local Optuna 18252 行、生产基线 21302 行、候选 857 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 39554 行且 `dirty=0`；`Drift Trigger` 为 39554 行，`none=32874`、`watch=6680`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3836 只股票，下一 offset `3836`，`missing_without_reason=0`。
- 局部 Optuna 第一百九十二批真实 batch 已完成：offset 3836 后再跑 20 只股票，覆盖 `601900` 至 `601969` 区间内 20 只股票，累计覆盖前 3856 只股票、19280 组 `(stock, formula)`。累计采纳结果为 859 条通过、18421 条拒绝，dry-run replacement 859 行。
- 第一百九十二批新增通过样例包括 `601901 + volume_base_breakout`、`601956 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 479 条，`gs_raw_buy` 178 条，`gs_pullback_confirm` 112 条，`volume_base_breakout` 90 条。
- 第 192 批后 `Research Cache` 已刷新为 39651 行、local Optuna 18349 行、生产基线 21302 行、候选 859 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 39651 行且 `dirty=0`；`Drift Trigger` 为 39651 行，`none=32951`、`watch=6700`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3856 只股票，下一 offset `3856`，`missing_without_reason=0`。
- 局部 Optuna 第一百九十三批真实 batch 已完成：offset 3856 后再跑 20 只股票，覆盖 `601975` 至 `603009` 区间内 20 只股票，累计覆盖前 3876 只股票、19380 组 `(stock, formula)`。累计采纳结果为 866 条通过、18514 条拒绝，dry-run replacement 866 行。
- 第一百九十三批新增通过样例包括 `603002 + volume_base_breakout`、`603005 + activity_breakout`、`601999 + gs_pullback_confirm`、`603001 + volume_base_breakout`、`603008 + activity_breakout`、`601990 + gs_raw_buy`、`601995 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 481 条，`gs_raw_buy` 180 条，`gs_pullback_confirm` 113 条，`volume_base_breakout` 92 条。
- 第 193 批后 `Research Cache` 已刷新为 39748 行、local Optuna 18446 行、生产基线 21302 行、候选 866 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 39748 行且 `dirty=0`；`Drift Trigger` 为 39748 行，`none=33028`、`watch=6720`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3876 只股票，下一 offset `3876`，`missing_without_reason=0`。
- 局部 Optuna 第一百九十四批真实 batch 已完成：offset 3876 后再跑 20 只股票，覆盖 `603010` 至 `603030` 区间内 20 只股票，累计覆盖前 3896 只股票、19480 组 `(stock, formula)`。累计采纳结果为 869 条通过、18611 条拒绝，dry-run replacement 869 行。
- 第一百九十四批新增通过样例包括 `603028 + activity_breakout`、`603013 + activity_breakout`、`603015 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 484 条，`gs_raw_buy` 180 条，`gs_pullback_confirm` 113 条，`volume_base_breakout` 92 条。
- 第 194 批后 `Research Cache` 已刷新为 39843 行、local Optuna 18541 行、生产基线 21302 行、候选 869 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 39843 行且 `dirty=0`；`Drift Trigger` 为 39843 行，`none=33101`、`watch=6742`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3896 只股票，下一 offset `3896`，`missing_without_reason=0`。
- 局部 Optuna 第一百九十五批真实 batch 已完成：offset 3896 后再跑 20 只股票，覆盖 `603031` 至 `603055` 区间内 20 只股票，累计覆盖前 3916 只股票、19580 组 `(stock, formula)`。累计采纳结果为 873 条通过、18707 条拒绝，dry-run replacement 873 行。
- 第一百九十五批新增通过样例包括 `603050 + activity_breakout`、`603039 + activity_breakout`、`603036 + activity_breakout`、`603053 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 487 条，`gs_raw_buy` 181 条，`gs_pullback_confirm` 113 条，`volume_base_breakout` 92 条。
- 第 195 批后 `Research Cache` 已刷新为 39939 行、local Optuna 18637 行、生产基线 21302 行、候选 873 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 39939 行且 `dirty=0`；`Drift Trigger` 为 39939 行，`none=33180`、`watch=6759`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3916 只股票，下一 offset `3916`，`missing_without_reason=0`。
- 局部 Optuna 第一百九十六批真实 batch 已完成：offset 3916 后再跑 20 只股票，覆盖 `603057` 至 `603078` 区间内 20 只股票，累计覆盖前 3936 只股票、19680 组 `(stock, formula)`。累计采纳结果为 877 条通过、18803 条拒绝，dry-run replacement 877 行。
- 第一百九十六批新增通过样例包括 `603066 + activity_breakout`、`603059 + activity_breakout`、`603065 + gs_raw_buy`、`603073 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 490 条，`gs_raw_buy` 182 条，`gs_pullback_confirm` 113 条，`volume_base_breakout` 92 条。
- 第 196 批后 `Research Cache` 已刷新为 40032 行、local Optuna 18730 行、生产基线 21302 行、候选 877 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40032 行且 `dirty=0`；`Drift Trigger` 为 40032 行，`none=33254`、`watch=6778`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3936 只股票，下一 offset `3936`，`missing_without_reason=0`。
- 局部 Optuna 第一百九十七批真实 batch 已完成：offset 3936 后再跑 20 只股票，覆盖 `603079` 至 `603100` 区间内 20 只股票，累计覆盖前 3956 只股票、19780 组 `(stock, formula)`。累计采纳结果为 880 条通过、18900 条拒绝，dry-run replacement 880 行。
- 第一百九十七批新增通过样例包括 `603079 + gs_pullback_confirm`、`603090 + activity_breakout`、`603088 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 492 条，`gs_raw_buy` 182 条，`gs_pullback_confirm` 114 条，`volume_base_breakout` 92 条。
- 第 197 批后 `Research Cache` 已刷新为 40125 行、local Optuna 18823 行、生产基线 21302 行、候选 880 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40125 行且 `dirty=0`；`Drift Trigger` 为 40125 行，`none=33327`、`watch=6798`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3956 只股票，下一 offset `3956`，`missing_without_reason=0`。
- 局部 Optuna 第一百九十八批真实 batch 已完成：offset 3956 后再跑 20 只股票，覆盖 `603101` 至 `603122` 区间内 20 只股票，累计覆盖前 3976 只股票、19880 组 `(stock, formula)`。累计采纳结果为 884 条通过、18996 条拒绝，dry-run replacement 884 行。
- 第一百九十八批新增通过样例包括 `603110 + activity_breakout`、`603112 + activity_breakout`、`603118 + activity_breakout`、`603118 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 495 条，`gs_raw_buy` 182 条，`gs_pullback_confirm` 114 条，`volume_base_breakout` 93 条。
- 第 198 批后 `Research Cache` 已刷新为 40222 行、local Optuna 18920 行、生产基线 21302 行、候选 884 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40222 行且 `dirty=0`；`Drift Trigger` 为 40222 行，`none=33409`、`watch=6813`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3976 只股票，下一 offset `3976`，`missing_without_reason=0`。
- 局部 Optuna 第一百九十九批真实 batch 已完成：offset 3976 后再跑 20 只股票，覆盖 `603123` 至 `603156` 区间内 20 只股票，累计覆盖前 3996 只股票、19980 组 `(stock, formula)`。累计采纳结果为 891 条通过、19089 条拒绝，dry-run replacement 891 行。
- 第一百九十九批新增通过样例包括 `603151 + activity_breakout`、`603126 + activity_breakout`、`603125 + activity_breakout`、`603131 + volume_base_breakout`、`603139 + activity_breakout`、`603124 + activity_breakout`、`603135 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 500 条，`gs_raw_buy` 183 条，`gs_pullback_confirm` 114 条，`volume_base_breakout` 94 条。
- 第 199 批后 `Research Cache` 已刷新为 40319 行、local Optuna 19017 行、生产基线 21302 行、候选 891 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40319 行且 `dirty=0`；`Drift Trigger` 为 40319 行，`none=33482`、`watch=6837`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 3996 只股票，下一 offset `3996`，`missing_without_reason=0`。
- 局部 Optuna 第二百批真实 batch 已完成：offset 3996 后再跑 20 只股票，覆盖 `603158` 至 `603179` 区间内 20 只股票，累计覆盖前 4016 只股票、20080 组 `(stock, formula)`。累计采纳结果为 896 条通过、19184 条拒绝，dry-run replacement 896 行。
- 第二百批新增通过样例包括 `603168 + activity_breakout`、`603173 + activity_breakout`、`603172 + activity_breakout`、`603159 + gs_raw_buy`、`603178 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 503 条，`gs_raw_buy` 185 条，`gs_pullback_confirm` 114 条，`volume_base_breakout` 94 条。
- 第 200 批后 `Research Cache` 已刷新为 40413 行、local Optuna 19111 行、生产基线 21302 行、候选 896 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40413 行且 `dirty=0`；`Drift Trigger` 为 40413 行，`none=33558`、`watch=6855`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4016 只股票，下一 offset `4016`，`missing_without_reason=0`。
- 局部 Optuna 第二百零一批真实 batch 已完成：offset 4016 后再跑 20 只股票，覆盖 `603180` 至 `603200` 区间内 20 只股票，累计覆盖前 4036 只股票、20180 组 `(stock, formula)`。累计采纳结果为 904 条通过、19276 条拒绝，dry-run replacement 904 行。
- 第二百零一批新增通过样例包括 `603192 + activity_breakout`、`603191 + activity_breakout`、`603199 + gs_raw_buy`、`603197 + activity_breakout`、`603192 + gs_raw_buy`、`603190 + gs_raw_buy`、`603190 + activity_breakout`、`603196 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 508 条，`gs_raw_buy` 188 条，`gs_pullback_confirm` 114 条，`volume_base_breakout` 94 条。
- 第 201 批后 `Research Cache` 已刷新为 40510 行、local Optuna 19208 行、生产基线 21302 行、候选 904 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40510 行且 `dirty=0`；`Drift Trigger` 为 40510 行，`none=33633`、`watch=6877`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4036 只股票，下一 offset `4036`，`missing_without_reason=0`。
- 局部 Optuna 第二百零二批真实 batch 已由另一个本地 Codex/terminal 接续完成：offset 4036 后再跑 20 只股票，覆盖 `603201` 至 `603221` 区间内 20 只股票，累计覆盖前 4056 只股票、20280 组 `(stock, formula)`。累计采纳结果为 909 条通过、19371 条拒绝，dry-run replacement 909 行。
- 第二百零二批新增通过样例包括 `603207 + activity_breakout`、`603209 + activity_breakout`、`603211 + gs_pullback_confirm`、`603215 + gs_pullback_confirm`、`603214 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 511 条，`gs_raw_buy` 188 条，`gs_pullback_confirm` 116 条，`volume_base_breakout` 94 条。
- 第 202 批后 `Research Cache` 已刷新为 40603 行、local Optuna batch 19301 行、生产基线 21302 行、候选 909 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40603 行且 `dirty=0`；`Drift Trigger` 为 40603 行，`none=33710`、`watch=6893`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4056 只股票，下一 offset `4056`，`missing_without_reason=0`。
- 局部 Optuna 第二百零三批真实 batch 已由另一个本地 Codex/terminal 接续完成：offset 4056 后再跑 20 只股票，覆盖 `603222` 至 `603257` 区间内 20 只股票，累计覆盖前 4076 只股票、20380 组 `(stock, formula)`。累计采纳结果为 911 条通过、19469 条拒绝，dry-run replacement 911 行。
- 第二百零三批新增通过样例包括 `603235 + gs_raw_buy`、`603233 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 511 条，`gs_raw_buy` 190 条，`gs_pullback_confirm` 116 条，`volume_base_breakout` 94 条。
- 第 203 批后 `Research Cache` 已刷新为 40697 行、local Optuna batch 19395 行、生产基线 21302 行、候选 911 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40697 行且 `dirty=0`；`Drift Trigger` 为 40697 行，`none=33784`、`watch=6913`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4076 只股票，下一 offset `4076`，`missing_without_reason=0`。
- 局部 Optuna 第二百零四批真实 batch 已由另一个本地 Codex/terminal 接续完成：offset 4076 后再跑 20 只股票，覆盖 `603258` 至 `603281` 区间内 20 只股票，累计覆盖前 4096 只股票、20480 组 `(stock, formula)`。累计采纳结果为 913 条通过、19567 条拒绝，dry-run replacement 913 行。
- 第二百零四批新增通过样例包括 `603275 + activity_breakout`、`603259 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 512 条，`gs_raw_buy` 191 条，`gs_pullback_confirm` 116 条，`volume_base_breakout` 94 条。
- 第 204 批后 `Research Cache` 已刷新为 40787 行、local Optuna batch 19485 行、生产基线 21302 行、候选 913 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40787 行且 `dirty=0`；`Drift Trigger` 为 40787 行，`none=33853`、`watch=6934`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4096 只股票，下一 offset `4096`，`missing_without_reason=0`。
- 局部 Optuna 第二百零五批真实 batch 已完成：offset 4096 后再跑 20 只股票，覆盖 `603282` 至 `603307` 区间内 20 只股票，累计覆盖前 4116 只股票、20580 组 `(stock, formula)`。累计采纳结果为 917 条通过、19663 条拒绝，dry-run replacement 917 行。
- 第二百零五批新增通过样例包括 `603307 + gs_pullback_confirm`、`603289 + gs_pullback_confirm`、`603291 + gs_raw_buy`、`603290 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 513 条，`gs_raw_buy` 192 条，`gs_pullback_confirm` 118 条，`volume_base_breakout` 94 条。
- 第 205 批后 `Research Cache` 已刷新为 40875 行、local Optuna batch 19573 行、生产基线 21302 行、候选 917 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40875 行且 `dirty=0`；`Drift Trigger` 为 40875 行，`none=33923`、`watch=6952`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4116 只股票，下一 offset `4116`，`missing_without_reason=0`。
- 局部 Optuna 第二百零六批真实 batch 已完成：offset 4116 后再跑 20 只股票，覆盖 `603308` 至 `603328` 区间内 20 只股票，累计覆盖前 4136 只股票、20680 组 `(stock, formula)`。累计采纳结果为 923 条通过、19757 条拒绝，dry-run replacement 923 行。
- 第二百零六批新增通过样例包括 `603315 + activity_breakout`、`603308 + gs_pullback_confirm`、`603320 + activity_breakout`、`603319 + activity_breakout`、`603308 + activity_breakout`、`603313 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 517 条，`gs_raw_buy` 193 条，`gs_pullback_confirm` 119 条，`volume_base_breakout` 94 条。
- 第 206 批后 `Research Cache` 已刷新为 40968 行、local Optuna batch 19666 行、生产基线 21302 行、候选 923 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 40968 行且 `dirty=0`；`Drift Trigger` 为 40968 行，`none=34000`、`watch=6968`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4136 只股票，下一 offset `4136`，`missing_without_reason=0`。
- 历史 GCP 协调记录：2026-05-20 曾检查 `chunkymonkey-optuna` 实例并发现已有 `chunkymonkey` 的 `retrain_lambdamart_v6.py` / LightGBM Optuna 任务和 GCS watch 同步进程正在运行；该记录只保留为背景，已被当前硬约束覆盖。除非用户在当前对话中明确授权，否则 BestChoice 不再探测、监控、拉取、停机或占用任何 GCP 资源。
- 局部 Optuna 第二百零七批真实 batch 已完成：offset 4136 后再跑 20 只股票，覆盖 `603329` 至 `603355` 区间内 20 只股票，累计覆盖前 4156 只股票、20780 组 `(stock, formula)`。累计采纳结果为 927 条通过、19853 条拒绝，dry-run replacement 927 行；`--resume` 复跑确认 `new_rows=0`。
- 第二百零七批新增通过样例包括 `603353 + gs_pullback_confirm`、`603329 + activity_breakout`、`603351 + activity_breakout`、`603339 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 519 条，`gs_raw_buy` 194 条，`gs_pullback_confirm` 120 条，`volume_base_breakout` 94 条。
- 第 207 批后 `Research Cache` 已刷新为 41059 行、local Optuna batch 19757 行、生产基线 21302 行、候选 927 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41059 行且 `dirty=0`；`Drift Trigger` 为 41059 行，`none=34069`、`watch=6990`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4156 只股票，下一 offset `4156`，`missing_without_reason=0`。
- 局部 Optuna 第二百零八批真实 batch 已完成：offset 4156 后再跑 20 只股票，覆盖 `603356` 至 `603381` 区间内 20 只股票，累计覆盖前 4176 只股票、20880 组 `(stock, formula)`。累计采纳结果为 932 条通过、19948 条拒绝，dry-run replacement 932 行。
- 第二百零八批新增通过样例包括 `603378 + activity_breakout`、`603368 + activity_breakout`、`603360 + activity_breakout`、`603368 + gs_raw_buy`、`603359 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 522 条，`gs_raw_buy` 195 条，`gs_pullback_confirm` 121 条，`volume_base_breakout` 94 条。
- 第 208 批后 `Research Cache` 已刷新为 41148 行、local Optuna batch 19846 行、生产基线 21302 行、候选 932 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41148 行且 `dirty=0`；`Drift Trigger` 为 41148 行，`none=34142`、`watch=7006`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4176 只股票，下一 offset `4176`，`missing_without_reason=0`。
- 局部 Optuna 第二百零九批真实 batch 已完成：offset 4176 后再跑 20 只股票，覆盖 `603382` 至 `603416` 区间内 20 只股票，累计覆盖前 4196 只股票、20980 组 `(stock, formula)`。累计采纳结果为 937 条通过、20043 条拒绝，dry-run replacement 937 行。
- 第二百零九批新增通过样例包括 `603398 + gs_pullback_confirm`、`603390 + activity_breakout`、`603393 + volume_base_breakout`、`603386 + activity_breakout`、`603389 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 524 条，`gs_raw_buy` 196 条，`gs_pullback_confirm` 122 条，`volume_base_breakout` 95 条。
- 第 209 批后 `Research Cache` 已刷新为 41230 行、local Optuna batch 19928 行、生产基线 21302 行、候选 937 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41230 行且 `dirty=0`；`Drift Trigger` 为 41230 行，`none=34204`、`watch=7026`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4196 只股票，下一 offset `4196`，`missing_without_reason=0`。
- 局部 Optuna 第二百一十批真实 batch 已完成：offset 4196 后再跑 20 只股票，覆盖 `603418` 至 `603507` 区间内 20 只股票，累计覆盖前 4216 只股票、21080 组 `(stock, formula)`。累计采纳结果为 942 条通过、20138 条拒绝，dry-run replacement 942 行。
- 第二百一十批新增通过样例包括 `603429 + volume_base_breakout`、`603489 + activity_breakout`、`603488 + volume_base_breakout`、`603477 + gs_raw_buy`、`603466 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 525 条，`gs_raw_buy` 198 条，`gs_pullback_confirm` 122 条，`volume_base_breakout` 97 条。
- 第 210 批后 `Research Cache` 已刷新为 41322 行、local Optuna batch 20020 行、生产基线 21302 行、候选 942 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41322 行且 `dirty=0`；`Drift Trigger` 为 41322 行，`none=34274`、`watch=7048`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为覆盖 4216 只股票，下一 offset `4216`，`missing_without_reason=0`；但当前 `market.duckdb` 被既有本地 `chunkymonkey` 仿真进程锁住，`consistency.ready=False` 且 `next_action=wait_external_duckdb_lock`，解锁后需先重跑 `python scripts/workflow_checkpoint.py --brief`，ready 后再继续下一批。
- 局部 Optuna 第二百一十一批真实 batch 已完成：offset 4216 后再跑 20 只股票，覆盖 `603508` 至 `603558` 区间内 20 只股票，累计覆盖前 4236 只股票、21180 组 `(stock, formula)`。累计采纳结果为 946 条通过、20234 条拒绝，dry-run replacement 946 行。
- 第二百一十一批新增通过样例包括 `603551 + activity_breakout`、`603533 + activity_breakout`、`603530 + activity_breakout`、`603515 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 529 条，`gs_raw_buy` 198 条，`gs_pullback_confirm` 122 条，`volume_base_breakout` 97 条。
- 第 211 批后 `Research Cache` 已刷新为 41418 行、local Optuna batch 20116 行、生产基线 21302 行、候选 946 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41418 行且 `dirty=0`；`Drift Trigger` 为 41418 行，`none=34356`、`watch=7062`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为覆盖 4236 只股票，下一 offset `4236`，`missing_without_reason=0`。
- 局部 Optuna 第二百一十二批真实 batch 已完成：offset 4236 后再跑 20 只股票，覆盖 `603559` 至 `603598` 区间内 20 只股票，累计覆盖前 4256 只股票、21280 组 `(stock, formula)`。累计采纳结果为 948 条通过、20332 条拒绝，dry-run replacement 948 行。
- 第二百一十二批新增通过样例包括 `603586 + activity_breakout`、`603577 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 531 条，`gs_raw_buy` 198 条，`gs_pullback_confirm` 122 条，`volume_base_breakout` 97 条。
- 第 212 批后 `Research Cache` 已刷新为 41517 行、local Optuna batch 20215 行、生产基线 21302 行、候选 948 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41517 行且 `dirty=0`；`Drift Trigger` 为 41517 行，`none=34437`、`watch=7080`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为覆盖 4256 只股票，下一 offset `4256`，`missing_without_reason=0`。
- 局部 Optuna 第二百一十三批真实 batch 已完成：offset 4256 后再跑 20 只股票，覆盖 `603599` 至 `603628` 区间内 20 只股票，累计覆盖前 4276 只股票、21380 组 `(stock, formula)`。累计采纳结果为 951 条通过、20429 条拒绝，dry-run replacement 951 行。
- 第二百一十三批新增通过样例包括 `603617 + activity_breakout`、`603599 + volume_base_breakout`、`603608 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 532 条，`gs_raw_buy` 198 条，`gs_pullback_confirm` 123 条，`volume_base_breakout` 98 条。
- 第 213 批后 `Research Cache` 已刷新为 41614 行、local Optuna batch 20312 行、生产基线 21302 行、候选 951 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41614 行且 `dirty=0`；`Drift Trigger` 为 41614 行，`none=34511`、`watch=7103`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为覆盖 4276 只股票，下一 offset `4276`，`missing_without_reason=0`。
- 局部 Optuna 第二百一十四批真实 batch 已完成：offset 4276 后再跑 20 只股票，覆盖 `603629` 至 `603666` 区间内 20 只股票，累计覆盖前 4296 只股票、21480 组 `(stock, formula)`。累计采纳结果为 954 条通过、20526 条拒绝，dry-run replacement 954 行。
- 第二百一十四批新增通过样例包括 `603666 + activity_breakout`、`603656 + activity_breakout`、`603661 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 535 条，`gs_raw_buy` 198 条，`gs_pullback_confirm` 123 条，`volume_base_breakout` 98 条。
- 第 214 批后 `Research Cache` 已刷新为 41709 行、local Optuna batch 20407 行、生产基线 21302 行、候选 954 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41709 行且 `dirty=0`；`Drift Trigger` 为 41709 行，`none=34591`、`watch=7118`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为覆盖 4296 只股票，下一 offset `4296`，`missing_without_reason=0`。
- 局部 Optuna 第二百一十五批真实 batch 已完成：offset 4296 后再跑 20 只股票，覆盖 `603667` 至 `603697` 区间内 20 只股票，累计覆盖前 4316 只股票、21580 组 `(stock, formula)`。累计采纳结果为 960 条通过、20620 条拒绝，dry-run replacement 960 行。
- 第二百一十五批新增通过样例包括 `603690 + activity_breakout`、`603680 + volume_base_breakout`、`603676 + activity_breakout`、`603693 + activity_breakout`、`603676 + gs_pullback_confirm`、`603687 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 538 条，`gs_raw_buy` 198 条，`gs_pullback_confirm` 125 条，`volume_base_breakout` 99 条。
- 第 215 批后 `Research Cache` 已刷新为 41806 行、local Optuna batch 20504 行、生产基线 21302 行、候选 960 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41806 行且 `dirty=0`；`Drift Trigger` 为 41806 行，`none=34668`、`watch=7138`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为覆盖 4316 只股票，下一 offset `4316`，`missing_without_reason=0`。
- 局部 Optuna 第二百一十六批真实 batch 已完成：offset 4316 后再跑 20 只股票，覆盖 `603698` 至 `603726` 区间内 20 只股票，累计覆盖前 4336 只股票、21680 组 `(stock, formula)`。累计采纳结果为 963 条通过、20717 条拒绝，dry-run replacement 963 行。
- 第二百一十六批新增通过样例包括 `603717 + activity_breakout`、`603699 + gs_pullback_confirm`、`603712 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 539 条，`gs_raw_buy` 199 条，`gs_pullback_confirm` 126 条，`volume_base_breakout` 99 条。
- 第 216 批后 `Research Cache` 已刷新为 41900 行、local Optuna batch 20598 行、生产基线 21302 行、候选 963 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41900 行且 `dirty=0`；`Drift Trigger` 为 41900 行，`none=34746`、`watch=7154`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为覆盖 4336 只股票，下一 offset `4336`，`missing_without_reason=0`。
- 局部 Optuna 第二百一十七批真实 batch 已完成：offset 4336 后再跑 20 只股票，覆盖 `603727` 至 `603779` 区间内 20 只股票，累计覆盖前 4356 只股票、21780 组 `(stock, formula)`。累计采纳结果为 968 条通过、20812 条拒绝，dry-run replacement 968 行。
- 第二百一十七批新增通过样例包括 `603727 + activity_breakout`、`603729 + activity_breakout`、`603759 + activity_breakout`、`603767 + activity_breakout`、`603757 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 544 条，`gs_raw_buy` 199 条，`gs_pullback_confirm` 126 条，`volume_base_breakout` 99 条。
- 第 217 批后 `Research Cache` 已刷新为 41993 行、local Optuna batch 20691 行、生产基线 21302 行、候选 968 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 41993 行且 `dirty=0`；`Drift Trigger` 为 41993 行，`none=34825`、`watch=7168`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为覆盖 4356 只股票，下一 offset `4356`，`missing_without_reason=0`。
- 局部 Optuna 第二百一十八批真实 batch 已完成：offset 4356 后再跑 20 只股票，覆盖 `603786` 至 `603817` 区间内 20 只股票，累计覆盖前 4376 只股票、21880 组 `(stock, formula)`。累计采纳结果为 972 条通过、20908 条拒绝，dry-run replacement 972 行。
- 第二百一十八批新增通过样例包括 `603817 + gs_pullback_confirm`、`603810 + activity_breakout`、`603815 + activity_breakout`、`603803 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 546 条，`gs_raw_buy` 199 条，`gs_pullback_confirm` 128 条，`volume_base_breakout` 99 条。
- 第 218 批后 `Research Cache` 已刷新为 42090 行、local Optuna batch 20788 行、生产基线 21302 行、候选 972 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 42090 行且 `dirty=0`；`Drift Trigger` 为 42090 行，`none=34901`、`watch=7189`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为覆盖 4376 只股票，下一 offset `4376`，`missing_without_reason=0`。
- 局部 Optuna 第二百一十九批真实 batch 已完成：offset 4376 后再跑 20 只股票，覆盖 `603818` 至 `603861` 区间内 20 只股票，累计覆盖前 4396 只股票、21980 组 `(stock, formula)`。累计采纳结果为 978 条通过、21002 条拒绝，dry-run replacement 978 行。
- 第二百一十九批新增通过样例包括 `603819 + volume_base_breakout`、`603818 + activity_breakout`、`603822 + activity_breakout`、`603855 + activity_breakout`、`603856 + volume_base_breakout`、`603828 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 550 条，`gs_raw_buy` 199 条，`gs_pullback_confirm` 128 条，`volume_base_breakout` 101 条。
- 第 219 批后 `Research Cache` 已刷新为 42188 行、local Optuna batch 20886 行、生产基线 21302 行、候选 978 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 42188 行且 `dirty=0`；`Drift Trigger` 为 42188 行，`none=34978`、`watch=7210`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4396 只股票，下一 offset `4396`，`missing_without_reason=0`。
- 局部 Optuna 第二百二十批真实 batch 已完成：offset 4396 后再跑 20 只股票，覆盖 `603863` 至 `603890` 区间内 20 只股票，累计覆盖前 4416 只股票、22080 组 `(stock, formula)`。累计采纳结果为 984 条通过、21096 条拒绝，dry-run replacement 984 行。
- 第二百二十批新增通过样例包括 `603883 + activity_breakout`、`603866 + activity_breakout`、`603867 + activity_breakout`、`603889 + gs_raw_buy`、`603878 + activity_breakout`、`603890 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 554 条，`gs_raw_buy` 201 条，`gs_pullback_confirm` 128 条，`volume_base_breakout` 101 条。
- 第 220 批后 `Research Cache` 已刷新为 42285 行、local Optuna batch 20983 行、生产基线 21302 行、候选 984 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 42285 行且 `dirty=0`；`Drift Trigger` 为 42285 行，`none=35056`、`watch=7229`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4416 只股票，下一 offset `4416`，`missing_without_reason=0`。
- 局部 Optuna 第二百二十一批真实 batch 已完成：offset 4416 后再跑 20 只股票，覆盖 `603893` 至 `603922` 区间内 20 只股票，累计覆盖前 4436 只股票、22180 组 `(stock, formula)`。累计采纳结果为 987 条通过、21193 条拒绝，dry-run replacement 987 行。
- 第二百二十一批新增通过样例包括 `603893 + activity_breakout`、`603912 + activity_breakout`、`603901 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 557 条，`gs_raw_buy` 201 条，`gs_pullback_confirm` 128 条，`volume_base_breakout` 101 条。
- 第 221 批后 `Research Cache` 已刷新为 42383 行、local Optuna batch 21081 行、生产基线 21302 行、候选 987 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 42383 行且 `dirty=0`；`Drift Trigger` 为 42383 行，`none=35135`、`watch=7248`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4436 只股票，下一 offset `4436`，`missing_without_reason=0`。
- 局部 Optuna 第二百二十二批真实 batch 已完成：offset 4436 后再跑 20 只股票，覆盖 `603926` 至 `603967` 区间内 20 只股票，累计覆盖前 4456 只股票、22280 组 `(stock, formula)`。累计采纳结果为 988 条通过、21292 条拒绝，dry-run replacement 988 行。
- 第二百二十二批新增通过样例包括 `603937 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 558 条，`gs_raw_buy` 201 条，`gs_pullback_confirm` 128 条，`volume_base_breakout` 101 条。
- 第 222 批后 `Research Cache` 已刷新为 42479 行、local Optuna batch 21177 行、生产基线 21302 行、候选 988 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 42479 行且 `dirty=0`；`Drift Trigger` 为 42479 行，`none=35206`、`watch=7273`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4456 只股票，下一 offset `4456`，`missing_without_reason=0`。
- 局部 Optuna 第二百二十三批真实 batch 已完成：offset 4456 后再跑 20 只股票，覆盖 `603968` 至 `603995` 区间内 20 只股票，累计覆盖前 4476 只股票、22380 组 `(stock, formula)`。累计采纳结果为 995 条通过、21385 条拒绝，dry-run replacement 995 行。
- 第二百二十三批新增通过样例包括 `603978 + volume_base_breakout`、`603976 + volume_base_breakout`、`603968 + activity_breakout`、`603988 + activity_breakout`、`603987 + gs_raw_buy`、`603977 + volume_base_breakout`、`603970 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 560 条，`gs_raw_buy` 203 条，`gs_pullback_confirm` 128 条，`volume_base_breakout` 104 条。
- 第 223 批后 `Research Cache` 已刷新为 42575 行、local Optuna batch 21273 行、生产基线 21302 行、候选 995 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 42575 行且 `dirty=0`；`Drift Trigger` 为 42575 行，`none=35279`、`watch=7296`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4476 只股票，下一 offset `4476`，`missing_without_reason=0`。
- 局部 Optuna 第二百二十四批真实 batch 已完成：offset 4476 后再跑 20 只股票，覆盖 `603997` 至 `605058` 区间内 20 只股票，累计覆盖前 4496 只股票、22480 组 `(stock, formula)`。累计采纳结果为 1006 条通过、21474 条拒绝，dry-run replacement 1006 行。
- 第二百二十四批新增通过样例包括 `605028 + gs_pullback_confirm`、`605007 + activity_breakout`、`605018 + activity_breakout`、`603999 + gs_pullback_confirm`、`605058 + activity_breakout`、`605001 + activity_breakout`、`605008 + activity_breakout`、`605007 + gs_raw_buy`、`603999 + gs_raw_buy`、`603997 + gs_raw_buy`、`603997 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 566 条，`gs_raw_buy` 206 条，`gs_pullback_confirm` 130 条，`volume_base_breakout` 104 条。
- 第 224 批后 `Research Cache` 已刷新为 42672 行、local Optuna batch 21370 行、生产基线 21302 行、候选 1006 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 42672 行且 `dirty=0`；`Drift Trigger` 为 42672 行，`none=35355`、`watch=7317`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4496 只股票，下一 offset `4496`，`missing_without_reason=0`。
- 局部 Optuna 第二百二十五批真实 batch 已完成：offset 4496 后再跑 20 只股票，覆盖 `605060` 至 `605122` 区间内 20 只股票，累计覆盖前 4516 只股票、22580 组 `(stock, formula)`。累计采纳结果为 1013 条通过、21567 条拒绝，dry-run replacement 1013 行。
- 第二百二十五批新增通过样例包括 `605122 + volume_base_breakout`、`605081 + activity_breakout`、`605086 + activity_breakout`、`605111 + activity_breakout`、`605122 + gs_raw_buy`、`605090 + volume_base_breakout`、`605100 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 570 条，`gs_raw_buy` 207 条，`gs_pullback_confirm` 130 条，`volume_base_breakout` 106 条。
- 第 225 批后 `Research Cache` 已刷新为 42769 行、local Optuna batch 21467 行、生产基线 21302 行、候选 1013 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 42769 行且 `dirty=0`；`Drift Trigger` 为 42769 行，`none=35437`、`watch=7332`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4516 只股票，下一 offset `4516`，`missing_without_reason=0`。
- 局部 Optuna 第二百二十六批真实 batch 已完成：offset 4516 后再跑 20 只股票，覆盖 `605123` 至 `605188` 区间内 20 只股票，累计覆盖前 4536 只股票、22680 组 `(stock, formula)`。累计采纳结果为 1018 条通过、21662 条拒绝，dry-run replacement 1018 行。
- 第二百二十六批新增通过样例包括 `605186 + activity_breakout`、`605128 + activity_breakout`、`605177 + gs_raw_buy`、`605167 + gs_pullback_confirm`、`605179 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 572 条，`gs_raw_buy` 209 条，`gs_pullback_confirm` 131 条，`volume_base_breakout` 106 条。
- 第 226 批后 `Research Cache` 已刷新为 42862 行、local Optuna batch 21560 行、生产基线 21302 行、候选 1018 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 42862 行且 `dirty=0`；`Drift Trigger` 为 42862 行，`none=35515`、`watch=7347`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4536 只股票，下一 offset `4536`，`missing_without_reason=0`。
- 局部 Optuna 第二百二十七批真实 batch 已完成：offset 4536 后再跑 20 只股票，覆盖 `605189` 至 `605298` 区间内 20 只股票，累计覆盖前 4556 只股票、22780 组 `(stock, formula)`。累计采纳结果为 1022 条通过、21758 条拒绝，dry-run replacement 1022 行。
- 第二百二十七批新增通过样例包括 `605189 + volume_base_breakout`、`605268 + activity_breakout`、`605218 + volume_base_breakout`、`605258 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 574 条，`gs_raw_buy` 209 条，`gs_pullback_confirm` 131 条，`volume_base_breakout` 108 条。
- 第 227 批后 `Research Cache` 已刷新为 42959 行、local Optuna batch 21657 行、生产基线 21302 行、候选 1022 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 42959 行且 `dirty=0`；`Drift Trigger` 为 42959 行，`none=35593`、`watch=7366`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4556 只股票，下一 offset `4556`，`missing_without_reason=0`。
- 局部 Optuna 第二百二十八批真实 batch 已完成：offset 4556 后再跑 20 只股票，覆盖 `605299` 至 `605388` 区间内 20 只股票，累计覆盖前 4576 只股票、22880 组 `(stock, formula)`。累计采纳结果为 1027 条通过、21853 条拒绝，dry-run replacement 1027 行。
- 第二百二十八批新增通过样例包括 `605378 + activity_breakout`、`605377 + volume_base_breakout`、`605358 + activity_breakout`、`605377 + activity_breakout`、`605366 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 578 条，`gs_raw_buy` 209 条，`gs_pullback_confirm` 131 条，`volume_base_breakout` 109 条。
- 第 228 批后 `Research Cache` 已刷新为 43051 行、local Optuna batch 21749 行、生产基线 21302 行、候选 1027 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43051 行且 `dirty=0`；`Drift Trigger` 为 43051 行，`none=35666`、`watch=7385`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4576 只股票，下一 offset `4576`，`missing_without_reason=0`。
- 局部 Optuna 第二百二十九批真实 batch 已完成：offset 4576 后再跑 20 只股票，覆盖 `605389` 至 `688004` 区间内 20 只股票，累计覆盖前 4596 只股票、22980 组 `(stock, formula)`。累计采纳结果为 1033 条通过、21947 条拒绝，dry-run replacement 1033 行。
- 第二百二十九批新增通过样例包括 `605588 + activity_breakout`、`605555 + activity_breakout`、`605589 + activity_breakout`、`688002 + gs_pullback_confirm`、`605567 + gs_pullback_confirm`、`605567 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 582 条，`gs_raw_buy` 209 条，`gs_pullback_confirm` 133 条，`volume_base_breakout` 109 条。
- 第 229 批后 `Research Cache` 已刷新为 43142 行、local Optuna batch 21840 行、生产基线 21302 行、候选 1033 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43142 行且 `dirty=0`；`Drift Trigger` 为 43142 行，`none=35744`、`watch=7398`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4596 只股票，下一 offset `4596`，`missing_without_reason=0`。
- 局部 Optuna 第二百三十批真实 batch 已完成：offset 4596 后再跑 20 只股票，覆盖 `688005` 至 `688026` 区间内 20 只股票，累计覆盖前 4616 只股票、23080 组 `(stock, formula)`。累计采纳结果为 1038 条通过、22042 条拒绝，dry-run replacement 1038 行。
- 第二百三十批新增通过样例包括 `688015 + activity_breakout`、`688008 + activity_breakout`、`688010 + activity_breakout`、`688023 + gs_raw_buy`、`688013 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 586 条，`gs_raw_buy` 210 条，`gs_pullback_confirm` 133 条，`volume_base_breakout` 109 条。
- 第 230 批后 `Research Cache` 已刷新为 43238 行、local Optuna batch 21936 行、生产基线 21302 行、候选 1038 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43238 行且 `dirty=0`；`Drift Trigger` 为 43238 行，`none=35820`、`watch=7418`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4616 只股票，下一 offset `4616`，`missing_without_reason=0`。
- 局部 Optuna 第二百三十一批真实 batch 已完成：offset 4616 后再跑 20 只股票，覆盖 `688027` 至 `688051` 区间内 20 只股票，累计覆盖前 4636 只股票、23180 组 `(stock, formula)`。累计采纳结果为 1041 条通过、22139 条拒绝，dry-run replacement 1041 行。
- 第二百三十一批新增通过样例包括 `688036 + activity_breakout`、`688035 + activity_breakout`、`688046 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 588 条，`gs_raw_buy` 211 条，`gs_pullback_confirm` 133 条，`volume_base_breakout` 109 条。
- 第 231 批后 `Research Cache` 已刷新为 43329 行、local Optuna batch 22027 行、生产基线 21302 行、候选 1041 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43329 行且 `dirty=0`；`Drift Trigger` 为 43329 行，`none=35892`、`watch=7437`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4636 只股票，下一 offset `4636`，`missing_without_reason=0`。
- 局部 Optuna 第二百三十二批真实 batch 已完成：offset 4636 后再跑 20 只股票，覆盖 `688052` 至 `688073` 区间内 20 只股票，累计覆盖前 4656 只股票、23280 组 `(stock, formula)`。累计采纳结果为 1045 条通过、22235 条拒绝，dry-run replacement 1045 行。
- 第二百三十二批新增通过样例包括 `688065 + activity_breakout`、`688060 + activity_breakout`、`688058 + activity_breakout`、`688069 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 591 条，`gs_raw_buy` 211 条，`gs_pullback_confirm` 133 条，`volume_base_breakout` 110 条。
- 第 232 批后 `Research Cache` 已刷新为 43424 行、local Optuna batch 22122 行、生产基线 21302 行、候选 1045 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43424 行且 `dirty=0`；`Drift Trigger` 为 43424 行，`none=35966`、`watch=7458`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4656 只股票，下一 offset `4656`，`missing_without_reason=0`。
- 局部 Optuna 第二百三十三批真实 batch 已完成：offset 4656 后再跑 20 只股票，覆盖 `688075` 至 `688096` 区间内 20 只股票，累计覆盖前 4676 只股票、23380 组 `(stock, formula)`。累计采纳结果为 1048 条通过、22332 条拒绝，dry-run replacement 1048 行。
- 第二百三十三批新增通过样例包括 `688077 + activity_breakout`、`688079 + activity_breakout`、`688087 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 593 条，`gs_raw_buy` 211 条，`gs_pullback_confirm` 134 条，`volume_base_breakout` 110 条。
- 第 233 批后 `Research Cache` 已刷新为 43517 行、local Optuna batch 22215 行、生产基线 21302 行、候选 1048 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43517 行且 `dirty=0`；`Drift Trigger` 为 43517 行，`none=36043`、`watch=7474`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4676 只股票，下一 offset `4676`，`missing_without_reason=0`。
- 局部 Optuna 第二百三十四批真实 batch 已完成：offset 4676 后再跑 20 只股票，覆盖 `688097` 至 `688117` 区间内 20 只股票，累计覆盖前 4696 只股票、23480 组 `(stock, formula)`。累计采纳结果为 1048 条通过、22432 条拒绝，dry-run replacement 1048 行；本批未新增通过候选。
- 第二百三十四批后当前通过候选按公式分布：`activity_breakout` 593 条，`gs_raw_buy` 211 条，`gs_pullback_confirm` 134 条，`volume_base_breakout` 110 条。
- 第 234 批后 `Research Cache` 已刷新为 43612 行、local Optuna batch 22310 行、生产基线 21302 行、候选 1048 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43612 行且 `dirty=0`；`Drift Trigger` 为 43612 行，`none=36121`、`watch=7491`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4696 只股票，下一 offset `4696`，`missing_without_reason=0`。
- 局部 Optuna 第二百三十五批真实 batch 已完成：offset 4696 后再跑 20 只股票，覆盖 `688118` 至 `688139` 区间内 20 只股票，累计覆盖前 4716 只股票、23580 组 `(stock, formula)`。累计采纳结果为 1050 条通过、22530 条拒绝，dry-run replacement 1050 行。
- 第二百三十五批新增通过样例包括 `688126 + activity_breakout`、`688138 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 595 条，`gs_raw_buy` 211 条，`gs_pullback_confirm` 134 条，`volume_base_breakout` 110 条。
- 第 235 批后 `Research Cache` 已刷新为 43704 行、local Optuna batch 22402 行、生产基线 21302 行、候选 1050 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43704 行且 `dirty=0`；`Drift Trigger` 为 43704 行，`none=36192`、`watch=7512`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4716 只股票，下一 offset `4716`，`missing_without_reason=0`。
- 局部 Optuna 第二百三十六批真实 batch 已完成：offset 4716 后再跑 20 只股票，覆盖 `688141` 至 `688166` 区间内 20 只股票，累计覆盖前 4736 只股票、23680 组 `(stock, formula)`。累计采纳结果为 1056 条通过、22624 条拒绝，dry-run replacement 1056 行。
- 第二百三十六批新增通过样例包括 `688158 + gs_pullback_confirm`、`688159 + gs_pullback_confirm`、`688157 + activity_breakout`、`688156 + gs_raw_buy`、`688150 + activity_breakout`、`688159 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 598 条，`gs_raw_buy` 212 条，`gs_pullback_confirm` 136 条，`volume_base_breakout` 110 条。
- 第 236 批后 `Research Cache` 已刷新为 43799 行、local Optuna batch 22497 行、生产基线 21302 行、候选 1056 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43799 行且 `dirty=0`；`Drift Trigger` 为 43799 行，`none=36265`、`watch=7534`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4736 只股票，下一 offset `4736`，`missing_without_reason=0`。
- 局部 Optuna 第二百三十七批真实 batch 已完成：offset 4736 后再跑 20 只股票，覆盖 `688167` 至 `688187` 区间内 20 只股票，累计覆盖前 4756 只股票、23780 组 `(stock, formula)`。累计采纳结果为 1062 条通过、22718 条拒绝，dry-run replacement 1062 行。
- 第二百三十七批新增通过样例包括 `688168 + volume_base_breakout`、`688177 + gs_raw_buy`、`688169 + gs_raw_buy`、`688183 + gs_pullback_confirm`、`688186 + gs_pullback_confirm`、`688177 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 599 条，`gs_raw_buy` 214 条，`gs_pullback_confirm` 138 条，`volume_base_breakout` 111 条。
- 第 237 批后 `Research Cache` 已刷新为 43896 行、local Optuna batch 22594 行、生产基线 21302 行、候选 1062 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43896 行且 `dirty=0`；`Drift Trigger` 为 43896 行，`none=36340`、`watch=7556`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4756 只股票，下一 offset `4756`，`missing_without_reason=0`。
- 局部 Optuna 第二百三十八批真实 batch 已完成：offset 4756 后再跑 20 只股票，覆盖 `688188` 至 `688209` 区间内 20 只股票，累计覆盖前 4776 只股票、23880 组 `(stock, formula)`。累计采纳结果为 1070 条通过、22810 条拒绝，dry-run replacement 1070 行。
- 第二百三十八批新增通过样例包括 `688203 + volume_base_breakout`、`688190 + activity_breakout`、`688200 + activity_breakout`、`688200 + gs_raw_buy`、`688195 + gs_pullback_confirm`、`688199 + activity_breakout`、`688188 + gs_raw_buy`、`688193 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 603 条，`gs_raw_buy` 216 条，`gs_pullback_confirm` 139 条，`volume_base_breakout` 112 条。
- 第 238 批后 `Research Cache` 已刷新为 43990 行、local Optuna batch 22688 行、生产基线 21302 行、候选 1070 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 43990 行且 `dirty=0`；`Drift Trigger` 为 43990 行，`none=36415`、`watch=7575`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4776 只股票，下一 offset `4776`，`missing_without_reason=0`。
- 局部 Optuna 第二百三十九批真实 batch 已完成：offset 4776 后再跑 20 只股票，覆盖 `688210` 至 `688231` 区间内 20 只股票，累计覆盖前 4796 只股票、23980 组 `(stock, formula)`。累计采纳结果为 1072 条通过、22908 条拒绝，dry-run replacement 1072 行。
- 第二百三十九批新增通过样例包括 `688219 + volume_base_breakout`、`688222 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 604 条，`gs_raw_buy` 216 条，`gs_pullback_confirm` 139 条，`volume_base_breakout` 113 条。
- 第 239 批后 `Research Cache` 已刷新为 44085 行、local Optuna batch 22783 行、生产基线 21302 行、候选 1072 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 44085 行且 `dirty=0`；`Drift Trigger` 为 44085 行，`none=36489`、`watch=7596`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4796 只股票，下一 offset `4796`，`missing_without_reason=0`。
- 局部 Optuna 第二百四十批真实 batch 已完成：offset 4796 后再跑 20 只股票，覆盖 `688232` 至 `688258` 区间内 20 只股票，累计覆盖前 4816 只股票、24080 组 `(stock, formula)`。累计采纳结果为 1073 条通过、23007 条拒绝，dry-run replacement 1073 行。
- 第二百四十批新增通过样例包括 `688253 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 604 条，`gs_raw_buy` 217 条，`gs_pullback_confirm` 139 条，`volume_base_breakout` 113 条。
- 第 240 批后 `Research Cache` 已刷新为 44178 行、local Optuna batch 22876 行、生产基线 21302 行、候选 1073 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 44178 行且 `dirty=0`；`Drift Trigger` 为 44178 行，`none=36565`、`watch=7613`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4816 只股票，下一 offset `4816`，`missing_without_reason=0`。
- 局部 Optuna 第二百四十一批真实 batch 已完成：offset 4816 后再跑 20 只股票，覆盖 `688259` 至 `688281` 区间内 20 只股票，累计覆盖前 4836 只股票、24180 组 `(stock, formula)`。累计采纳结果为 1079 条通过、23101 条拒绝，dry-run replacement 1079 行。
- 第二百四十一批新增通过样例包括 `688266 + gs_pullback_confirm`、`688280 + activity_breakout`、`688277 + activity_breakout`、`688276 + gs_raw_buy`、`688281 + activity_breakout`、`688267 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 607 条，`gs_raw_buy` 219 条，`gs_pullback_confirm` 140 条，`volume_base_breakout` 113 条。
- 第 241 批后 `Research Cache` 已刷新为 44268 行、local Optuna batch 22966 行、生产基线 21302 行、候选 1079 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 44268 行且 `dirty=0`；`Drift Trigger` 为 44268 行，`none=36637`、`watch=7631`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4836 只股票，下一 offset `4836`，`missing_without_reason=0`。
- 局部 Optuna 第二百四十二批真实 batch 已完成：offset 4836 后再跑 20 只股票，覆盖 `688282` 至 `688303` 区间内 20 只股票，累计覆盖前 4856 只股票、24280 组 `(stock, formula)`。累计采纳结果为 1088 条通过、23192 条拒绝，dry-run replacement 1088 行。
- 第二百四十二批新增通过样例包括 `688299 + activity_breakout`、`688283 + activity_breakout`、`688292 + activity_breakout`、`688300 + activity_breakout`、`688285 + activity_breakout`、`688299 + volume_base_breakout`、`688286 + activity_breakout`、`688288 + activity_breakout`、`688282 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 614 条，`gs_raw_buy` 220 条，`gs_pullback_confirm` 140 条，`volume_base_breakout` 114 条。
- 第 242 批后 `Research Cache` 已刷新为 44359 行、local Optuna batch 23057 行、生产基线 21302 行、候选 1088 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 44359 行且 `dirty=0`；`Drift Trigger` 为 44359 行，`none=36705`、`watch=7654`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4856 只股票，下一 offset `4856`，`missing_without_reason=0`。
- 局部 Optuna 第二百四十三批真实 batch 已完成：offset 4856 后再跑 20 只股票，覆盖 `688305` 至 `688325` 区间内 20 只股票，累计覆盖前 4876 只股票、24380 组 `(stock, formula)`。累计采纳结果为 1092 条通过、23288 条拒绝，dry-run replacement 1092 行。
- 第二百四十三批新增通过样例包括 `688317 + activity_breakout`、`688314 + activity_breakout`、`688308 + gs_pullback_confirm`、`688311 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 616 条，`gs_raw_buy` 221 条，`gs_pullback_confirm` 141 条，`volume_base_breakout` 114 条。
- 第 243 批后 `Research Cache` 已刷新为 44455 行、local Optuna batch 23153 行、生产基线 21302 行、候选 1092 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 44455 行且 `dirty=0`；`Drift Trigger` 为 44455 行，`none=36780`、`watch=7675`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4876 只股票，下一 offset `4876`，`missing_without_reason=0`。
- 局部 Optuna 第二百四十四批真实 batch 已完成：offset 4876 后再跑 20 只股票，覆盖 `688326` 至 `688350` 区间内 20 只股票，累计覆盖前 4896 只股票、24480 组 `(stock, formula)`。累计采纳结果为 1094 条通过、23386 条拒绝，dry-run replacement 1094 行。
- 第二百四十四批新增通过样例包括 `688329 + gs_raw_buy`、`688349 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 616 条，`gs_raw_buy` 223 条，`gs_pullback_confirm` 141 条，`volume_base_breakout` 114 条。
- 第 244 批后 `Research Cache` 已刷新为 44550 行、local Optuna batch 23248 行、生产基线 21302 行、候选 1094 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 44550 行且 `dirty=0`；`Drift Trigger` 为 44550 行，`none=36854`、`watch=7696`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4896 只股票，下一 offset `4896`，`missing_without_reason=0`。
- 局部 Optuna 第二百四十五批真实 batch 已完成：offset 4896 后再跑 20 只股票，覆盖 `688351` 至 `688372` 区间内 20 只股票，累计覆盖前 4916 只股票、24580 组 `(stock, formula)`。累计采纳结果为 1099 条通过、23481 条拒绝，dry-run replacement 1099 行。
- 第二百四十五批新增通过样例包括 `688360 + activity_breakout`、`688360 + gs_pullback_confirm`、`688358 + gs_raw_buy`、`688359 + activity_breakout`、`688361 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 619 条，`gs_raw_buy` 224 条，`gs_pullback_confirm` 142 条，`volume_base_breakout` 114 条。
- 第 245 批后 `Research Cache` 已刷新为 44645 行、local Optuna batch 23343 行、生产基线 21302 行、候选 1099 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 44645 行且 `dirty=0`；`Drift Trigger` 为 44645 行，`none=36928`、`watch=7717`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4916 只股票，下一 offset `4916`，`missing_without_reason=0`。
- 局部 Optuna 第二百四十六批真实 batch 已完成：offset 4916 后再跑 20 只股票，覆盖 `688373` 至 `688395` 区间内 20 只股票，累计覆盖前 4936 只股票、24680 组 `(stock, formula)`。累计采纳结果为 1103 条通过、23577 条拒绝，dry-run replacement 1103 行。
- 第二百四十六批新增通过样例包括 `688393 + activity_breakout`、`688381 + activity_breakout`、`688378 + gs_raw_buy`、`688383 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 621 条，`gs_raw_buy` 225 条，`gs_pullback_confirm` 143 条，`volume_base_breakout` 114 条。
- 第 246 批后 `Research Cache` 已刷新为 44736 行、local Optuna batch 23434 行、生产基线 21302 行、候选 1103 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 44736 行且 `dirty=0`；`Drift Trigger` 为 44736 行，`none=37003`、`watch=7733`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4936 只股票，下一 offset `4936`，`missing_without_reason=0`。
- 局部 Optuna 第二百四十七批真实 batch 已完成：offset 4936 后再跑 20 只股票，覆盖 `688396` 至 `688433` 区间内 20 只股票，累计覆盖前 4956 只股票、24780 组 `(stock, formula)`。累计采纳结果为 1104 条通过、23676 条拒绝，dry-run replacement 1104 行。
- 第二百四十七批新增通过样例包括 `688409 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 622 条，`gs_raw_buy` 225 条，`gs_pullback_confirm` 143 条，`volume_base_breakout` 114 条。
- 第 247 批后 `Research Cache` 已刷新为 44822 行、local Optuna batch 23520 行、生产基线 21302 行、候选 1104 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 44822 行且 `dirty=0`；`Drift Trigger` 为 44822 行，`none=37072`、`watch=7750`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4956 只股票，下一 offset `4956`，`missing_without_reason=0`。
- 局部 Optuna 第二百四十八批真实 batch 已完成：offset 4956 后再跑 20 只股票，覆盖 `688435` 至 `688485` 区间内 20 只股票，累计覆盖前 4976 只股票、24880 组 `(stock, formula)`。累计采纳结果为 1110 条通过、23770 条拒绝，dry-run replacement 1110 行。
- 第二百四十八批新增通过样例包括 `688435 + activity_breakout`、`688472 + activity_breakout`、`688469 + activity_breakout`、`688443 + gs_raw_buy`、`688472 + gs_raw_buy`、`688456 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 626 条，`gs_raw_buy` 227 条，`gs_pullback_confirm` 143 条，`volume_base_breakout` 114 条。
- 第 248 批后 `Research Cache` 已刷新为 44913 行、local Optuna batch 23611 行、生产基线 21302 行、候选 1110 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 44913 行且 `dirty=0`；`Drift Trigger` 为 44913 行，`none=37144`、`watch=7769`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4976 只股票，下一 offset `4976`，`missing_without_reason=0`。
- 局部 Optuna 第二百四十九批真实 batch 已完成：offset 4976 后再跑 20 只股票，覆盖 `688486` 至 `688515` 区间内 20 只股票，累计覆盖前 4996 只股票、24980 组 `(stock, formula)`。累计采纳结果为 1113 条通过、23867 条拒绝，dry-run replacement 1113 行。
- 第二百四十九批新增通过样例包括 `688489 + activity_breakout`、`688515 + activity_breakout`、`688506 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 629 条，`gs_raw_buy` 227 条，`gs_pullback_confirm` 143 条，`volume_base_breakout` 114 条。
- 第 249 批后 `Research Cache` 已刷新为 45007 行、local Optuna batch 23705 行、生产基线 21302 行、候选 1113 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45007 行且 `dirty=0`；`Drift Trigger` 为 45007 行，`none=37215`、`watch=7792`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 4996 只股票，下一 offset `4996`，`missing_without_reason=0`。
- 局部 Optuna 第二百五十批真实 batch 已完成：offset 4996 后再跑 20 只股票，覆盖 `688516` 至 `688543` 区间内 20 只股票，累计覆盖前 5016 只股票、25080 组 `(stock, formula)`。累计采纳结果为 1117 条通过、23963 条拒绝，dry-run replacement 1117 行。
- 第二百五十批新增通过样例包括 `688517 + activity_breakout`、`688531 + activity_breakout`、`688535 + activity_breakout`、`688529 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 632 条，`gs_raw_buy` 228 条，`gs_pullback_confirm` 143 条，`volume_base_breakout` 114 条。
- 第 250 批后 `Research Cache` 已刷新为 45098 行、local Optuna batch 23796 行、生产基线 21302 行、候选 1117 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45098 行且 `dirty=0`；`Drift Trigger` 为 45098 行，`none=37284`、`watch=7814`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5016 只股票，下一 offset `5016`，`missing_without_reason=0`。
- 局部 Optuna 第二百五十一批真实 batch 已完成：offset 5016 后再跑 20 只股票，覆盖 `688545` 至 `688569` 区间内 20 只股票，累计覆盖前 5036 只股票、25180 组 `(stock, formula)`。累计采纳结果为 1123 条通过、24057 条拒绝，dry-run replacement 1123 行。
- 第二百五十一批新增通过样例包括 `688550 + activity_breakout`、`688565 + activity_breakout`、`688569 + volume_base_breakout`、`688566 + volume_base_breakout`、`688566 + gs_raw_buy`、`688557 + gs_pullback_confirm`。当前通过候选按公式分布：`activity_breakout` 634 条，`gs_raw_buy` 229 条，`gs_pullback_confirm` 144 条，`volume_base_breakout` 116 条。
- 第 251 批后 `Research Cache` 已刷新为 45192 行、local Optuna batch 23890 行、生产基线 21302 行、候选 1123 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45192 行且 `dirty=0`；`Drift Trigger` 为 45192 行，`none=37355`、`watch=7837`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5036 只股票，下一 offset `5036`，`missing_without_reason=0`。
- 局部 Optuna 第二百五十二批真实 batch 已完成：offset 5036 后再跑 20 只股票，覆盖 `688570` 至 `688592` 区间内 20 只股票，累计覆盖前 5056 只股票、25280 组 `(stock, formula)`。累计采纳结果为 1128 条通过、24152 条拒绝，dry-run replacement 1128 行。
- 第二百五十二批新增通过样例包括 `688586 + activity_breakout`、`688571 + activity_breakout`、`688571 + gs_raw_buy`、`688579 + activity_breakout`、`688588 + volume_base_breakout`。当前通过候选按公式分布：`activity_breakout` 637 条，`gs_raw_buy` 230 条，`gs_pullback_confirm` 144 条，`volume_base_breakout` 117 条。
- 第 252 批后 `Research Cache` 已刷新为 45288 行、local Optuna batch 23986 行、生产基线 21302 行、候选 1128 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45288 行且 `dirty=0`；`Drift Trigger` 为 45288 行，`none=37422`、`watch=7866`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5056 只股票，下一 offset `5056`，`missing_without_reason=0`。
- 局部 Optuna 第二百五十三批真实 batch 已完成：offset 5056 后再跑 20 只股票，覆盖 `688593` 至 `688615` 区间内 20 只股票，累计覆盖前 5076 只股票、25380 组 `(stock, formula)`。累计采纳结果为 1133 条通过、24247 条拒绝，dry-run replacement 1133 行。
- 第二百五十三批新增通过样例包括 `688602 + activity_breakout`、`688597 + activity_breakout`、`688611 + activity_breakout`、`688610 + activity_breakout`、`688605 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 641 条，`gs_raw_buy` 231 条，`gs_pullback_confirm` 144 条，`volume_base_breakout` 117 条。
- 第 253 批后 `Research Cache` 已刷新为 45383 行、local Optuna batch 24081 行、生产基线 21302 行、候选 1133 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45383 行且 `dirty=0`；`Drift Trigger` 为 45383 行，`none=37497`、`watch=7886`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5076 只股票，下一 offset `5076`，`missing_without_reason=0`。
- 局部 Optuna 第二百五十四批真实 batch 已完成：offset 5076 后再跑 20 只股票，覆盖 `688616` 至 `688646` 区间内 20 只股票，累计覆盖前 5096 只股票、25480 组 `(stock, formula)`。累计采纳结果为 1137 条通过、24343 条拒绝，dry-run replacement 1137 行。
- 第二百五十四批新增通过样例包括 `688616 + activity_breakout`、`688639 + activity_breakout`、`688620 + gs_raw_buy`、`688631 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 644 条，`gs_raw_buy` 232 条，`gs_pullback_confirm` 144 条，`volume_base_breakout` 117 条。
- 第 254 批后 `Research Cache` 已刷新为 45476 行、local Optuna batch 24174 行、生产基线 21302 行、候选 1137 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45476 行且 `dirty=0`；`Drift Trigger` 为 45476 行，`none=37567`、`watch=7909`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5096 只股票，下一 offset `5096`，`missing_without_reason=0`。
- 局部 Optuna 第二百五十五批真实 batch 已完成：offset 5096 后再跑 20 只股票，覆盖 `688648` 至 `688676` 区间内 20 只股票，累计覆盖前 5116 只股票、25580 组 `(stock, formula)`。累计采纳结果为 1142 条通过、24438 条拒绝，dry-run replacement 1142 行。
- 第二百五十五批新增通过样例包括 `688665 + activity_breakout`、`688653 + activity_breakout`、`688662 + activity_breakout`、`688652 + activity_breakout`、`688656 + gs_raw_buy`。当前通过候选按公式分布：`activity_breakout` 648 条，`gs_raw_buy` 233 条，`gs_pullback_confirm` 144 条，`volume_base_breakout` 117 条。
- 第 255 批后 `Research Cache` 已刷新为 45570 行、local Optuna batch 24268 行、生产基线 21302 行、候选 1142 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45570 行且 `dirty=0`；`Drift Trigger` 为 45570 行，`none=37641`、`watch=7929`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5116 只股票，下一 offset `5116`，`missing_without_reason=0`。
- 局部 Optuna 第二百五十六批真实 batch 已完成：offset 5116 后再跑 20 只股票，覆盖 `688677` 至 `688699` 区间内 20 只股票，累计覆盖前 5136 只股票、25680 组 `(stock, formula)`。累计采纳结果为 1143 条通过、24537 条拒绝，dry-run replacement 1143 行。
- 第二百五十六批新增通过样例包括 `688692 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 649 条，`gs_raw_buy` 233 条，`gs_pullback_confirm` 144 条，`volume_base_breakout` 117 条。
- 第 256 批后 `Research Cache` 已刷新为 45664 行、local Optuna batch 24362 行、生产基线 21302 行、候选 1143 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45664 行且 `dirty=0`；`Drift Trigger` 为 45664 行，`none=37721`、`watch=7943`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5136 只股票，下一 offset `5136`，`missing_without_reason=0`。
- 局部 Optuna 第二百五十七批真实 batch 已完成：offset 5136 后再跑 20 只股票，覆盖 `688700` 至 `688729` 区间内 20 只股票，累计覆盖前 5156 只股票、25780 组 `(stock, formula)`。累计采纳结果为 1145 条通过、24635 条拒绝，dry-run replacement 1145 行。
- 第二百五十七批新增通过样例包括 `688711 + activity_breakout`、`688700 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 651 条，`gs_raw_buy` 233 条，`gs_pullback_confirm` 144 条，`volume_base_breakout` 117 条。
- 第 257 批后 `Research Cache` 已刷新为 45746 行、local Optuna batch 24444 行、生产基线 21302 行、候选 1145 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45746 行且 `dirty=0`；`Drift Trigger` 为 45746 行，`none=37780`、`watch=7966`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5156 只股票，下一 offset `5156`，`missing_without_reason=0`。
- 局部 Optuna 第二百五十八批真实 batch 已完成：offset 5156 后再跑 20 只股票，覆盖 `688733` 至 `688783` 区间内 20 只股票，累计覆盖前 5176 只股票、25880 组 `(stock, formula)`。累计采纳结果为 1145 条通过、24735 条拒绝，dry-run replacement 1145 行；本批未新增通过候选。
- 第二百五十八批后当前通过候选按公式分布：`activity_breakout` 651 条，`gs_raw_buy` 233 条，`gs_pullback_confirm` 144 条，`volume_base_breakout` 117 条。
- 第 258 批后 `Research Cache` 已刷新为 45825 行、local Optuna batch 24523 行、生产基线 21302 行、候选 1145 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45825 行且 `dirty=0`；`Drift Trigger` 为 45825 行，`none=37830`、`watch=7995`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5176 只股票，下一 offset `5176`，`missing_without_reason=0`。
- 局部 Optuna 第二百五十九批真实 batch 已完成：offset 5176 后再跑 20 只股票，覆盖 `688785` 至 `688816` 区间内 20 只股票，累计覆盖前 5196 只股票、25980 组 `(stock, formula)`。累计采纳结果为 1146 条通过、24834 条拒绝，dry-run replacement 1146 行。
- 第二百五十九批新增通过样例包括 `688787 + activity_breakout`。当前通过候选按公式分布：`activity_breakout` 652 条，`gs_raw_buy` 233 条，`gs_pullback_confirm` 144 条，`volume_base_breakout` 117 条。
- 第 259 批后 `Research Cache` 已刷新为 45889 行、local Optuna batch 24587 行、生产基线 21302 行、候选 1146 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45889 行且 `dirty=0`；`Drift Trigger` 为 45889 行，`none=37875`、`watch=8014`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5196 只股票，下一 offset `5196`，`missing_without_reason=0`。
- 局部 Optuna 第二百六十批真实 batch 已完成：offset 5196 后跑完最后 5 只股票，覆盖 `688818` 至 `689009` 区间内 5 只股票，累计覆盖全市场 5201 只股票、26005 组 `(stock, formula)`。累计采纳结果为 1146 条通过、24859 条拒绝，dry-run replacement 1146 行；本批未新增通过候选。
- 第二百六十批后当前通过候选按公式分布：`activity_breakout` 652 条，`gs_raw_buy` 233 条，`gs_pullback_confirm` 144 条，`volume_base_breakout` 117 条。
- 第 260 批后 `Research Cache` 已刷新为 45908 行、local Optuna batch 24606 行、生产基线 21302 行、候选 1146 行，数据最新日期 `2026-05-19`；`Incremental Evaluator` 为 45908 行且 `dirty=0`；`Drift Trigger` 为 45908 行，`none=37885`、`watch=8023`、`reevaluate=0`、`reoptimize=0`、`disable_candidate=0`。恢复 checkpoint 已更新为 `consistency.ready=True`、覆盖 5201 只股票，下一 offset `5201`，`missing_without_reason=0`。
- 全市场局部 Optuna dry-run 覆盖、aggregate audit 与运营交付审计已完成：覆盖 5201/5201 只股票，`analysis/formula_local_optuna_aggregate_audit.md` 显示 `passed=True`，`analysis/operational_delivery_readiness.md` 显示 `operational_ready=True`。最终验证通过 `py_compile`、`execution_model_smoke.py`、`unified_data_smoke.py`、`strategy_rebuild_audit.py`、`formula_local_optuna_aggregate_audit.py`、`git diff --check` 和 checkpoint 复核；`workflow_checkpoint.py --brief` 已进入 `next_action=operational_ready`。当前已具备运营交付/受控生产合并评审条件，但仍未写入生产 `analysis/stock_formula_best.csv`；后续生产落表必须由人工确认 dry-run replacements 与运营窗口后执行，不允许自动覆盖。
