# 数据模块顶层设计 (Data Module Constitution) — 定稿 v2.0

> **status**: 创世立法 (genesis)。v1 草稿(主会话手写)→ v2 经 13-agent Workflow (7 路现状调研 + 3 竞争提案 + 对抗盲点批判 + 磁盘事实核证) + 主会话 controller 亲验收编。
> **v1→v2**: 本稿是 v1 同名草稿的**超集定稿**, 非另起。(注: Workflow 综合 agent 的 disk check 失败、误判 v1"不存在"; 主会话已亲验 v1 在磁盘[11926B]、survey#5 亦引用之 — 此 agent 互矛盾已裁: v1 在, 本稿 supersede 它。)
> **authority**: 长期遵循的法; 管"数据模块跨层架构", 各 owner 文档管其域细则 (见 §12)。
> **核证脚注**: 所有"现状事实"已对磁盘核证 (非引述 agent)。被提案/批判误判、主会话亲验更正的事实见 §0.3。

---

## 0. 创世层 (为何存在 / 死亡条款 / 第一性三问)

### 0.1 北极星锚定 (一切设计的终极消费者)

**主升浪猎手**: 结果倒推选股 → 鱼头买点 / 鱼身延续 / 鱼尾出场, 含成本 OOS 可决策。
**KPI** (owner=`goal.md`, 不在此 hardcode): 年化 ≥30% / max_dd ≥ -20% / 超额基准 >0 / 月胜率 ≥55%, **含交易成本 + T+1 + 一字板剔除**。

> 数据模块存在的唯一理由 = 让北极星每一步 (form → 因子 → 选股 → paper_sim → 实盘) 在历史任意时刻 t **PIT 干净、可复现、可决策**。不服务此链的数据资产 = 待裁退役对象。

### 0.2 死亡条款 (违则整个模块判死, 不可协商)

| # | 死亡条款 | 机器执法点 (须沉到提交者够不到处) | 现状 (亲验) |
|---|---|---|---|
| D1 | **真金白银穿透**: 转正 (confirmed_by_owner=1) 必带含成本绝对收益证据 | `record_verdict` C-R1 raise | [OK] 已硬门 (8处C-R1/C-LEAK实测) |
| D2 | **leakage-clean 转正门** | `record_verdict` C-LEAK raise | [OK] 已硬门 |
| D3 | **第二真相源即死**: 同概念物理/算法多份=违宪 | moth 双真相源探针 + DataAccess 单一路由 | [WARN] 部分 (机构动向三口径; top10刚收敛#51) |
| D4 | **PIT 偏序**: 决策 t 只用 ≤t | 读层 asof 强制 + build-time PIT 单测 | [WARN] 散落, 待 SERVE 收口 |
| D5 | **地基不完整冻结上层** | A0 止血 gate (task#46) | [WARN] 进行中 |
| D6 | **感知死防线**: 实盘预测须 forward 回填对账 | forward_reconciliation reader | [NO] 缺 reader (真缺口, 非 verdict 空头) |

### 0.3 提案/批判被磁盘证伪、主会话亲验更正的事实 (立法前清账)

| 争议点 | agent 声称 | 主会话亲验 | 对设计影响 |
|---|---|---|---|
| v1 设计文档 | 综合 agent: "磁盘不存在" | **存在 (11926B)**; survey#5 亦引用 | 本稿=v2 supersede v1, 非创世首份; agent disk check 失败 |
| verdict store | 批判: "INSERT-only 零reader 空头支票" | **亲验: 4表存在全0行 + 8处C-R1/C-LEAK硬raise + 6 readers** | D1/D2 已是硬门; "armed-but-empty"(schema+门齐, 0数据); 真缺口仅 D6 reconciliation |
| **北极星 edge** | 综合: "无 confirmed edge, 据此 BLOCK 档B" | **亲验成立: confirmed_by_owner=1 共 0; 近期 beats random +4.6pp 远未达KPI** | **档B 全量基础设施 BLOCK 直到 edge confirmed = 本设计最承重裁决** |
| rally_stage | 提案: "含 peak leakage 绝不用" | yaml: POST-HOC 研究标签, 特征仍 ≤t PIT 合规 | §6.3 用途分离: 研究用 rally_stage / live 用 technical_stage |
| factor_registry | 提案: "新建" | **feature_registry.yaml 已存在 (10822B)** | §3 扩展既有, 禁新建并列 (能删必删) |

### 0.4 第一性原理三问 (每个新表/层/函数必答)

1. **真相源?** → K线 `price_kline_qfq_tushare` + 交易日历 + tushare L0 `raw_tushare_*`。不是中间派生表/快照。
2. **能删吗?** → 多一层=多一个 bug 处。**SERVE 读层是唯一被批准的新基质层** (它消灭 N 处散落=净删复杂度); 其余先 `rg`/codegraph 搜已有再建。
3. **规则写哪?** → YAML, 业务代码只读不 hardcode。

---

## 1. 一句话诊断 (七路现状统一根因)

> **写侧(ACQUIRE)+存侧(STORE)已成熟 config-driven; 读侧(SERVE)完全未建 = 所有 silo/双轨/第二真相源的同一根性 gap。**

| 面 | 同一病灶投影 (磁盘核证) |
|---|---|
| 股票档案 | dossier.py 内联 raw 表名 (top10×5/kline×4/...), data_loaders 仅 3 loader |
| 机构档案 | 机构动向三套口径; top10 双源刚收敛(#51) |
| 公式档案 | RUN 驱动被删(33f6b430); bestchoice/ 平行死代码 |
| 选股器 | evaluate_signal 零生产调用; strata 阈值 hardcode SQL |
| 实盘模拟 | paper_sim 停 JSON/print; 无 forward reconciliation reader |

**结论**: 不是缺 5 模块, 是缺 **1 个 SERVE 读层 + 1 个 signal_assembler + forward reconciliation reader**。修这三处 = 5 面同时受益。

---

## 1.5 四地基不变量 + 编排 + 血缘 (倒推定稿, 2026-06-22 用户收敛)

> 倒推: 未来全部细节 (cube/因子库/选股公式/多策略/实盘) 归结到 4 个地基不变量。现在做对=叶子即插即用; 做错=每个细节打架。叶子 (cube/具体因子/多策略) 全押后, 只建这 4 个根。

> **[2026-06-23 反推原话还原 + 现阶段重心锚]** (此前只在 session transcript, 文档仅留收敛结论, 易丢致跑偏; 本次找回锚此):
> - **反推触发** (用户原话, TX 29684): "评估现阶段是否需要深入到这种细节… 重点没放在数据源解析与获取、流程并发还是串行、变量计算在数据获取之后怎么算、一键更新还是手工分阶段、断点续传、每个节点中断怎么处理、长链条还是模块化子模块" → 深入即发现**无法穷举和预知**。
> - **反推裁决** (AI, TX 29687, 用户收敛): "不该把所有东西设计到同一深度, 按阶段裁颗粒度 (奥卡姆+avoid premature depth); cube/展示押后, **现阶段该深入的恰恰是运营编排核心**。" → 模块化子模块 + 薄DAG (非长链条); 节点三性。
> - **现阶段目标 = seed 的"现在最该做的 3件: 清洗 / 加工 / 展示"** (用户原始 seed, 2026-06-23 贴回令"当作目标")。清洗=SERVE单一读路(最不完整核心, 消费者58文件绕过)/ 加工=L2宽panel(feature_panel+signal_panel已建,(b)分表已落)/ 展示=read-model(stock,as_of)切片(未建P4)。**这 3 件是地基不是叶子; cube/因子/选股公式/多策略/实盘=叶子全押后**。
> - **A5 薄DAG编排器 = 运营机械(运行 acquire/derive/serve/audit 节点产出 3 件), 是"怎么运行"的操作支撑, 非 seed 的 3 件本身** (来自后续 TX29684 编排深挖, 非原始 seed 重心)。删源/清库=P2应用层, 排 3 件之后。**跑偏自检**: 重心应在 3件(尤其清洗 SERVE单一读路), 别让 A5 机械或删源喧宾夺主。
> - daily_update 工具定位 (用户原话 TX 32892): "daily_update 只是数据管理大模块的子模块/工具, 具体 update 哪些字段从哪些数据源下载由配置文件说明, 增删改也在配置文件里, 不带计算" — 计算交清洗+加工子模块。
> - 边界法 4 决议 (TX 33223): 数据源唯一 tushare 无热备删旧源 / 无合法直连豁免(只数据模块功能能连源) / 加工计算全在数据模块子模块 / 前端边界=语义 vs 展示。

### 1.5.1 四地基不变量 (现在必须做对, 改之昂贵)
| # | 不变量 | 防的打架 / 撑的未来 | 落地 |
|---|---|---|---|
| 1 | **统一主键+PIT锚** — 全数据 `(code6, trade_date/as_of)` + 统一日期格式 + 统一 as_of 语义 | cube/选股/因子全靠此键JOIN; 键不一致(report_date双格式反例)=跨表漏命中 | SERVE asof_gate + cleaner 写出/读入强制 |
| 2 | **读写边界=写锁边界=库分区** — writer独占库/表; reader永远 read_only 只走SERVE | "各模块打架"根因: DuckDB库级写锁; cube多库读/实盘live全靠不撞sync写 | DB分区(已建)+ SERVE read_only + attach.py |
| 3 | **可扩展分层** — 加因子=加列(非加表); 加展示=加切片(非重查) | 因子库/cube 零返工扩展 | L2宽panel + feature_registry + read-model切片 |
| 4 | **单概念单真相源** — 每概念1表1计算路径 | 防双源增殖(tdxhub qfq/三口径); 多消费者读同一真相不各算 | SERVE单一读路 + 4 moth门 |

### 1.5.2 编排层 (调度: 模块化子模块 + 薄DAG, 非长链条)
**节点=最小可独立运行单元, 强制三性**: 独立可跑(手工单跑)/ 幂等(MERGE on grain重跑安全)/ 可续(checkpoint断点续)。
- 节点类: acquire(一域=sync_runner --domain, 已具三性) · derive(build_* 算变量, 补checkpoint) · serve(read-model物化, 档B) · audit。
- **DAG依赖声明在 `pipeline.yaml`**(derive声明依赖哪些acquire域), 编排器拓扑排序; 非hardcode长链。
- **并发vs串行按约束自动定**: acquire走 `_RateLimiter`全局节流(tushare 120/接口·200/全·并发2) bounded-concurrent; derive按**写锁边界**(data_layers.yaml知写哪库)→写不同库可并行/同库串行。
- **一键vs手工同一DAG两入口**: `chunkyctl update --all`(全DAG) / `--stage acquire|derive` / `--node X`。当前手工分阶段, 成熟后一键。
- **中断处理**: 节点状态(pending/running/done/failed)=续跑真相源; 重跑跳done只跑pending+failed; 节点失败隔离(一挂DAG继续其他分支, 下游标blocked, 失败入队gap-replay补)=**不是一断全断**。
- `daily_update.sh` 退化为编排器 `--all` 入口, 不再内联9 heredoc长链。

### 1.5.3 数据血缘 (= 单一路径架构的自描述副产品, 非单独追踪系统)
"不各算各的" 与 "可追溯" 是同一机制两面: 每关注点有唯一声明处 + 4 moth门挡第二处 → 那条唯一路径本身=血缘。
- **声明血缘**(免费, config链): `data_access.yaml`(entity→源) ← `feature_registry`(factor→compute_fn+上游) ← `sync_registry`(域→tushare接口)。
- **携带溯源**(轻, per-entity非per-row): SERVE 返回带 `{value, source_entity, source_table, as_of, vendor, compute_fn, taxonomy_version}` 信封。
- **物化血缘**(已有, per-artifact): 复用 `pipeline_artifact_lineage`(artifact, 上游表, compute_fn, code commit, build_ts)。
- **追溯=确定性走链**(不猜): 展示字段→read-model切片→SERVE entity→[派生则]compute_fn+lineage记录→上游entity→sync域→tushare接口+PIT锚。`moth lineage-complete` 门: read-model每字段必能追到声明源, 追不到=FAIL。
- 奥卡姆守: 声明路径 + per-artifact记录, **非 per-value 重追踪器**(那会重+漂移)。

---

## 2. 完整四层架构 (ACQUIRE→STORE→SERVE→CONSUME→DISPLAY + 对称 config)

```
真相源: K线 price_kline_qfq_tushare · 交易日历 · raw_tushare_* (tushare 唯一外源)
  │  口径铁律: 行业=申万 / 概念=东财 / 资金 flow-vendor=membership-vendor
  ▼
① ACQUIRE 写侧 [已建·成熟] config=sync_registry.yaml(46域) owner=sync_runner.py
  │  一域一条目零专属代码; raw 镜像不加工; tdxhub/akshare 仅热备不进主链
  │  NOTE:债: daily_update.sh 9 内联 heredoc → 收编 registry
  ▼
② STORE 存侧 [已建·执法] config=database_manifest(7库)+data_layers(逐表L0-L4)+storage_retention
  │  按写锁边界分库 · moth data-layer-integrity 守
  ▼
══ ③ SERVE 读侧统一层 ★★唯一批准的新基质层★★ config=data_access.yaml(新) ══
  │  唯一取数+PIT执行+口径清洗点; consumer 禁内联 FROM raw_*
  │  内部分层(防god-module): resolver→asof_gate→cleaner→vendor_router→attach(跨库read_only)→drivers/<entity>
  ▼
④ CONSUME 用侧 [薄消费者, 全走 SERVE]
  │  纯函数派生层(technical_states/ form + factors/ alpha, 0 DB) ← 单一计算点
  │  DimensionInterpreter协议(档案13维度) · signal_assembler(新) · phaseD_signal_eval(含成本) · experiment_harness
  │  → experiment_store.record_verdict (D1/D2 转正硬门)
  ▼
⑤ DISPLAY 展示 [统一 read-model, 解"各展各的"]
     routers/* → 单一 read-model 契约 → 自包含 HTML; 禁 router 内联 SQL
```

### 2.1 存 — 7库按写锁边界分 (manifest 路由), 分层 L0/L1/L1k/L2/L3/L4。保持现状不新增库。

### 2.2 洗 — 两类两执法点 (收编批判 F2: PIT 不能一刀切沉读层)

| 清洗类型 | 在哪 | 执法门 | 例 |
|---|---|---|---|
| **物化清洗** (写时一次) | builder/view | build-time PIT 单测 + `moth feature-from-l2` | 复权qfq / v_sw_industry_pit / 因子物化进panel |
| **读时清洗** (PIT asof+口径换算) | **SERVE 读层** (data_access clean段) | `moth read-no-inline-table` + `moth read-no-self-asof` | YYYYMMDD→ISO / tp÷10000 / ann_date≤t 锁MAX版本 |

### 2.3 加工 (因子物化 L2) — 单一计算点铁律 (收编批判 1.2 HIGH)

- **因子唯一权威值 = 物化 panel** (`feature_store.fact_feature_panel`)。
- dossier 展示**读 panel** (尾部未物化增量按需现算, 用**同一** `factors/` 纯函数)。
- 禁 dossier 与 build_panel 各写一套同语义因子 (现 features.compute vs build_feature_panel 双轨=待消除)。
- 纯函数层 (`technical_states/`+`factors/`) **0 DB**, 只收 DataAccess 喂的 dict。

### 2.4/2.5 展示见 §7; 复用 = DataAccess(取数) + factors/(因子) + fact_holder_event(机构动向) + portfolio_execbacktest(回测) + experiment_harness(裁决) 各单一真相。

---

## 3. 对称 config 职责表

> 红线: 数值/阈值/路径/口径/分桶/PIT锚 全走 YAML。**新建只批准 data_access.yaml + rally_strata.yaml**(真新缺口); 其余扩展既有禁造并列。

| config | 层 | 职责 | 动作 |
|---|---|---|---|
| sync_registry.yaml | ACQUIRE | 46域采集契约 | [OK]; 收编 daily_update 9 heredoc + stk_surv 转正 |
| database_manifest / data_layers / storage_retention | STORE | 库路由 / 逐表层 / retention | [OK]; data_layers 加 dead-compute 退役标记(先codegraph查0消费者) |
| **data_access.yaml** | **SERVE** | entity→源表→asof锚→clean段→vendor路由→taxonomy_version | [NO] **新建(唯一批准基质)** |
| feature_registry.yaml | DERIVE | 因子注册(已含groups/model_input/production_ready) | [OK] **已存在**; **扩展** compute_fn/source_table/source_kind/dsr_overlap_group/taxonomy_version/blocked_by (禁新建factor_registry) |
| technical_states / rally_stage / technical_stage / optuna_config / rally_gt_columns | DERIVE | 态/研究阶段/live阶段/搜索空间/GT列契约 | [OK] 已建 |
| **rally_strata.yaml** | DERIVE | 市值桶(30/100/500亿)+长底桶(40/60/100日) (现hardcode SQL) | [NO] 新建(迁hardcode); **GT重建前就位** |
| universe_rules.yaml | 横切硬门 | universe 真相源(与日历同级) | [OK] (owner=services/universe.py) |

---

## 4. SERVE 读层设计 (最大缺口填法 + 防 god-module, 收编批判 1.1/3/C2 HIGH)

### 4.1 模块结构 (薄分发器 + 可插拔 driver)
```
services/data_access/
  __init__.py        # DataAccess.get(entity,codes,as_of) 薄分发器, 读yaml选driver, 本体零业务
  resolver.py        # entity名→物理表(manifest路由)+schema自校验
  asof_gate.py       # PIT asof 强制(≤t锁版本MAX); 只对 L0现算 entity 生效
  cleaner.py         # clean段执行(日期归一/单位换算/COALESCE) 单一执行点
  vendor_router.py   # 行业=申万/资金=东财 口径锁 + fallback 显式记录(禁静默掺源)
  attach.py          # 跨库JOIN 强制 read_only=True + 非sync写窗口
  drivers/<entity>.py # 每entity一driver(取数/asof/换算可独立单测)
```
**零摩擦判据**: 加 entity = 加 driver 文件 + yaml 一条目, DataAccess 本体零改。做不到=god-module。

### 4.2 data_access.yaml (含自校验防漂移)
```yaml
entities:
  kline_qfq:     {primary: market.price_kline_qfq_tushare, fallback: market.price_kline_tdxhub(显式告警禁静默掺), asof_anchor: trade_date, date_format: iso, columns: [...]}
  holders_top10: {primary: tushare_raw.raw_tushare_top10_floatholders, asof_anchor: ann_date, asof_version_lock: max}
  industry_pit:  {primary: smartmoney.v_sw_industry_pit, asof_anchor: in_date, taxonomy_version: sw_2021}  # 跨版本请求=raise
clean_rules:  {iso: {match: YYYYMMDD, to: YYYY-MM-DD}}   # 全局单一日期口径, 消 _iso 散落
preflight:    {assert_schema: true}                      # entity columns/anchor 真去schema核对, 漂移=FAIL
```

### 4.3 三道机器门
| 门 | 拦什么 |
|---|---|
| `moth read-no-inline-table` 硬FAIL | consumer 内联 FROM raw_*/price_kline* |
| `moth read-no-self-asof` 硬FAIL | consumer 自写 ann_date≤/as_of (PIT 只能读层执行) |
| `moth feature-from-l2` 硬FAIL | consumer 绕 panel 直读 L0 重算因子 |
| `data_access_preflight` 硬FAIL | yaml entity schema 漂移 |

---

## 5. 五功能面消费契约 (各读哪层/物化什么/纪律 + 模块化边界)

| 面 | 服务北极星哪步 | 读哪层 | 物化什么 | 解耦边界 |
|---|---|---|---|---|
| **股票档案** | 认识论地基 | DataAccess only (L0/L1/L3) on-the-fly | **不落库** | 13维度=13 DimensionInterpreter module(替 interpret_stock 硬串90行); 加维度=加模块+`dossier_dimensions.yaml`一行 |
| **机构档案** | L3维度+跟随alpha底座 | DataAccess + fact_holder_event | 单一 fact_holder_event | 三口径→1(§6.2带裁决); 物删旧tdx双份 |
| **公式档案** | episode标注工具 | DataAccess.load_kline + factors | verdict留档(4表) | RUN驱动重新设计(非裸git恢复§6.4); 物删 bestchoice/; 公式因子并入 factors/ |
| **选股器** | stage-conditional候选池+排名 | feature_panel + rally_*(attach封装) | rally 5表 + panel因子矩阵 | **新建 signal_assembler**(strata×stage×panel→PIT signal_by_code单一组装点); 阈值走 rally_strata.yaml |
| **实盘模拟** | 含成本paper_sim→实盘 | 复用回测引擎 | **NAV序列固化表**(现只JSON) | evaluate_signal 加 pluggable exit_fn(解G7条件化出场); 物删旧returnbacktest |

### 5.1 模块化边界裁决 (合/拆/删) — 解"不各算各 + 不耦合死"
拆: 取数(DataAccess)/派生(纯函数0DB)/编排(Interpreter list); 13维度硬串→协议化。
合: form因子 vs alpha因子→统一factors/; 机构三口径→fact_holder_event(带§6.2裁决)。
删: top10/旧源双物理份; bestchoice/; v3_bestchoice。
建: signal_assembler 单一组装点。
保持耦合(有意): experiment_harness(防漏法典, 加exit_fn钩子); 跨库分库(写锁隔离, attach封装 consumer 不见库边界)。

---

## 6. 收编对抗批判 HIGH 盲点

全 HIGH 已解或显式 unknown (god-module→§4.1分层; 双轨→§2.3铁律; factor_registry→§3扩展; taxonomy跨版本→§4.2 raise; 立方体selection-bias→§8硬上限+先验; L2清库无回退→§9快照对账; **edge未验证就大建→§8档A/B分档**)。

### 6.2 机构动向口径裁决 (解批判, 合并前先实测)
三套口径(符号/LAG/量对比)可能度量不同事。**禁直接"LAG为准"判死**(违 measured-not-estimated)。流程: 实测三口径对 forward 收益解释力→数据定主口径→有独立信息量留为不同列→删前断言"删了不影响选股EV"。**标 unknown 直到实测。**

### 6.3 rally_stage(研究POST-HOC, PIT特征合规) vs technical_stage(live conditioning) 用途分离。

### 6.4 公式 RUN 驱动 = 重新设计非裸恢复 (`git show 33f6b430~1` 是参考; 查删除上下文 误删vs淘汰; 在 SERVE 架构下重设计, 复用幸存 pit_guard/leakage_detect/oos_ic)。

---

## 7. 展示层 (统一前端 + 跨档案立体视图 + 写锁安全) — 用户 2026-06-22 补

**确认: 前端统一共用** (用户决)。但"共用"≠每个视图各自跨库裸查 (= 写锁争用 + 耦合)。三道设计:

### 7.1 read-model 服务契约 (解"各展各的" + 解耦)
每档案 (股票/机构/公式) 暴露一个 **read-model 切片** — 去规范化、以 `(stock_code, as_of)` 为键、由 SERVE 读层喂数。
- 单档案视图读自己的切片。
- **跨档案立体视图 (股票×机构×公式 cube) 在 read-model 层 JOIN 切片** (by stock_code+as_of), **不重查各档案内部 = 组合非耦合**。加一档案进 cube = 加一个 read-model 切片, cube 视图零改各档案模块。这是"共享 read-model 设计对不对"的试金石: 设计对 → cube 是切片组合; 设计错 → cube 又去 live 查 3 套档案内部 = 耦合复发。

### 7.2 写锁安全 (用户点名: DuckDB 写锁=库级)
daily_update 写 L0/部分表时, 前端若跨 7 库 live 裸查 = 与 sync 写窗争锁 (cube 同时读多库尤甚)。设计:
- **SERVE/DISPLAY 一律 read_only 连接** (attach.py 强制 read_only=True)。
- **展示读 read-model 服务快照, 不 live 读热写库**: read-model 在 **sync 后物化**成一张 serving 视图/库, 前端读稳定快照 → 与 sync 写窗**时序解耦**; cube 读单一 serving 源 → **无多库锁争用**。
- 写锁边界 = DB 分区边界 (已设计): sync 热写 L0; 展示读 L1/L2/L3 + read-model (不同库/不同写时点) → 物理隔离争用。

### 7.3 各面落地
| 展示面 | 方案 | 档 |
|---|---|---|
| 股票档案 dossier_view | 维度卡按 dossier_dimensions.yaml 动态渲染; 读股票 read-model 切片 | A(seam)/B(产品化) |
| 机构 (档案/工作台/市场概况) | 三薄视图读**同一机构 read-model**; stk_surv 调研接入 | A |
| 公式档案 | 公式定义/寻参/裁决 read-model; 喂裁决为主 | A |
| **跨档案立体视图 (cube)** | read-model 层 JOIN 股票×机构×公式 切片; **read-model 契约 P1 预留 seam, cube 视图 P4/档B 建** | seam=A / 建=B |
| 选股候选池 / NAV | 新建视图读固化表; 退役 v3_bestchoice | A |

统一接缝: `routers/<surface>.py → DataAccess(read_only) + read-model + Interpreter`; 前端禁裸SQL / 后端禁内联表名 (moth)。

---

## 8. 未来扩展机制 (插件契约 — 零摩擦但非零防线)

### 8.0 三类信号统一扩展契约 (因子 / 选股公式 / 维度 — 用户: 不只论文因子, 还有那么多选股公式)

扩展不只论文因子。三类"从数据产生信号"的东西走**同一扩展机制** (config 注册 + 纯函数计算 + PIT 干净 + signal_assembler 消费), 但信号类型不同, 不可混为一谈:

| 信号类型 | 是什么 | 注册 config | 计算 | signal_assembler 怎么用 |
|---|---|---|---|---|
| **因子 (factor)** | 连续值 (RankIC 评) | feature_registry (source_kind: vendor_precomputed/derived) | factors/ 纯函数 → 物化 panel | 候选池内**排名** |
| **选股公式 (formula)** | 事件/条件信号 (金叉/突破/形态序列) | formula_*.yaml (加 signal_kind 字段统一进 feature_registry 视图) | formula_engine PIT → event/bool | **条件门 / 候选池筛选** |
| **维度 (dimension)** | 解读上下文 (形态/筹码/机构) | dossier_dimensions.yaml | DimensionInterpreter | **conditioning + 展示**, promote 成因子须对齐 panel 口径 |

> 选股公式与因子是**不同信号类型非同物**: 因子给排序值, 公式给事件/布尔。共享: config 驱动 / PIT / 纯函数 / 同一 signal_assembler 组装 (assembler 同时吃 排名因子 + 公式条件门 + 维度 conditioning, 做 stage-conditional 选股)。公式档案 RUN 驱动 (P3 重设计) 是公式轴执行入口。**两者全量铺开都受 §8.1 edge-gating: 属档B, BLOCK 直到 edge confirmed** (单条公式/因子验证当前假设属档A)。

> **L2 schema 裁决 (用户 2026-06-22: b 分表)**: 连续因子与事件信号 **分两张同键并列 panel** — `feature_store.fact_feature_panel` (连续因子, float, RankIC评) + `feature_store.fact_signal_panel` (事件信号, bool/event, 公式产出), 均键 `(stock_code, trade_date)`。signal_assembler 同键 JOIN 两表取 (排名+条件门)。语义干净不混类型; 同库 (feature_store L2) 写锁内部串行协调。理由: 因子给排序、公式给布尔, 混在一张宽表的 dtype/语义会绊住后续。

| 扩展轴 | 零摩擦接入 | 防 over-fit/leakage 硬前置 |
|---|---|---|
| **因子库**(Alpha158/长江筹码/华泰筹码龄/基本面/stk_factor_pro 261列) | feature_registry 加条目, **区分 `source_kind: vendor_precomputed`(DataAccess取,PIT锚ann_date) vs `derived`(factors/纯函数,PIT核证)** — 防 261 vendor列误归 derived 造 261 leakage点 | DSR n_eff 重叠组 + 跨实验 cumulative_trials 多重比较 + taxonomy_version; **转正门(CPCV/PBO/DSR)硬前置** |
| **新档案维度** | dossier_dimensions.yaml + DimensionInterpreter module; **协议声明维度间依赖DAG**(浪型依赖筹码/形态非全独立) | promote 成因子先对齐 panel 口径 |
| **选股立方体**(形态×因子×policy×regime×execution) | 各轴走config引用既有真相源(禁内联重定义); regime 当一轴(自己PIT锚t-1可见)入registry 非if/else | **笛卡尔积=selection bias放大器**: 硬上限+每轴独立先验理由+plan_validator非空闸+holdout |
| **实盘模拟→实盘** | paper_sim 与 live 共用 evaluate_signal+含成本引擎 | **forward_reconciliation reader(D6)** + 连续负兑现冻结 |

### 8.1 ★北极星 edge 未验证 → 扩展规模硬闸 (最承重, 主会话亲验: confirmed=0)
> 当前**无任何 confirmed_by_owner=1 真 edge** (experiment_store 4表全0行; 近期 beats random +4.6pp 远未达KPI)。在未确认方法上建全套基础设施 = 为不存在的策略修地基。

**裁决 — 扩展分两档**:
- **档A (立即 P0-P4)**: 只建到"够验证当前主升浪假设"最小子集 — kline+moneyflow+holder 三 entity + signal_assembler + 含成本 tradability(G8)前置。
- **档B (押后, edge confirmed 后)**: factor 全量铺 / 立方体5轴 / 展示产品化。
- **前置验收**: 建 signal_assembler 前, 先用 sandbox 证"存在含成本可交易的 stage-conditional signal"。证不出 → SERVE 建得再干净也是数字游戏, **BLOCK 档B**。

---

## 9. 清库重建 vs 增量迁移 裁决 (用户授权清库, grill "清什么留什么")

> 用户"授权清库" ≠ 无脑全清。**裁决: L0/L1k tushare真相源保留(先质量探针) / 旧源双轨+污染派生清, 且与 M1-M4 渐进迁移并轨, 非 big-bang。**

| 层 | 裁决 | 理由 |
|---|---|---|
| L0 raw (8GB) | **保留**, 先质量探针(industry out_date/holder修正行/ST日历完整性)再确认 | 真相源镜像; 重拉=限流墙+钱+0收益; 但"raw干净"是假设须实测 |
| L1k K线 | **保留tushare版, 物删旧price_kline/tdxhub**(M3,删前grep全caller) | tushare已主源; 旧表=活的stale源 |
| L1 派生(rally_gt/stage/strata) | **清空+从L0重建**(走assert_universe_clean) | 含北交所3.1%+未滤ST污染; **但先确认builder已接universe硬门再重跑**(否则白删) |
| L2 feature_panel | **快照归档(非删)+重建** | 仅5列+BROKEN; **清前归档做对账基准**(新panel对干净参考RankIC 0.0108-0.0203 + 旧panel 5共有列行级diff); L2列少≠清库, ALTER ADD回填可达则优先 |
| L3 机构事件 | **从L0 tushare重建** | 注: L3 form档案维度已DONE且tushare切源完成, **不推倒** |
| smartmoney双物理源 / bestchoice/ / v3_bestchoice | **物删** | 双真相源红线+stale / 死代码; 这是清库**真正目标** |
| experiment_store verdict | **保留schema** | 唯一跨sandbox裁决留档 + D1/D2硬门载体 |

**不可逆铁律** (mio铁律11): 每物删前 `moth coupling --impact` 全fan-in + grep全caller + 备份commit + **重建dry-run验证 builder 能从L0重算相同schema+rowcount**(防builder也依赖被删层); 删后 orphan审计 + 0残留断言。

---

## 10. 分阶段执行 + 每阶段 gate

| 阶段 | 动作 | gate (绿才进下阶段) |
|---|---|---|
| **P0 地基止血** (BLOCKER #46) | feature_panel重建 + GT拆entry-PIT/outcome + 负样本 + cyq winner_rate口径裁决(**已验=复权CLEAN, 解除blocked**) | A0 gate + build-time PIT单测 |
| **P1 SERVE读层** (根性gap档A) **[2026-06-22 gate 全绿: DONE]** | data_access.yaml(21entity) + DataAccess(resolver/asof/cleaner/keys/generic driver) + dossier 18内联裸查全迁(duck_connect=0, 全parity验证) | **moth serve-read-layer-p1-doors 全绿** (check_serve_read_layer.py 单一执法点: D1 read-no-inline / D2 read-no-self-asof / D3 preflight-wired / D4 lineage-complete / D5 feature-from-l2; red→green 实证 + 5 pytest); 迁移同股同as_of数值一致(parity)。**注: read-no-inline/asof 门=dossier consumer 子集(P1只迁dossier); signals_v2/routers 等未迁 consumer 属 P2/P3 债** |
| **P2 清库重建** (并轨M1-M4 §9) | codegraph fan-in→物删bestchoice/v3/旧K线/双源→rally从L0重建→机构read-model | 每物删前impact+dry-run重建验证; assert_universe_clean; L2对账 |
| **P3 协议化+接通+转正门** | DimensionInterpreter(依赖DAG) + 三机构口径实测裁决→1套 + signal_assembler + exit_fn(G7) + 转正门(bootstrap/CPCV/PBO/holdout) + **forward_reconciliation reader(D6)** | 含成本tradability前置(G8); record_verdict已硬门 |
| **P4 展示产品化** (档A收尾) | 统一read-model + rally候选池视图 + NAV固化表+视图 | moth 前端禁裸SQL |
| **P5 扩展** (档B, edge confirmed后) | feature_registry全量(Alpha158/筹码龄) + 立方体5轴 + 实盘 | DSR/CPCV/PBO硬前置; 笛卡尔积硬上限+先验 |

---

## 11. 总裁决

- **档A 无条件 PROCEED**: SERVE 读层(P1) 是根性 gap 地基, 立即建。死亡条款 D1/D2 已硬门(亲验)。
- **档B 条件 PROCEED**: 因子全量/立方体/展示产品化, **BLOCK 直到** sandbox 证出含成本可交易 stage-conditional signal (北极星 edge 存在性前置)。
- **清库 REVISE**: 不全清; L0/L1k 保留(先探针), 旧源+污染派生清, 与 M1-M4 并轨非 big-bang。

---

## 12. authority 表

| 主题 | 本文件管 | owner 文档管细则 |
|---|---|---|
| 跨层数据架构 (ACQUIRE/STORE/SERVE/CONSUME/DISPLAY) | [OK] | docs/MASTER_TOPLEVEL_DESIGN.md |
| PIT/anti-leakage 细则 | 偏序原则 | docs/strategy_validation_contract.md |
| KPI 数值 | 引用不hardcode | goal.md |
| universe 规则 | 横切硬门定位 | services/universe.py + universe_rules.yaml |
| 死亡条款机器执法 | [OK] 定义 | experiment_store.py (C-R1/C-LEAK) |

---

**关键文件路径**:
- 待新建: `backend/config/data_access.yaml` · `config/rally_strata.yaml` · `services/data_access/`(内部分层包) · `services/signal_assembler.py`
- 待扩展(非新建): `backend/config/feature_registry.yaml` (加 compute_fn/source_kind/dsr_overlap_group/taxonomy_version)
- silo 源头: `services/dossier.py`(内联raw表名) · `services/data_loaders.py`(仅3 loader)
- 待迁 hardcode: `scripts/build_rally_episode_strata.py:30-33` (市值桶SQL字符串)
- 死代码待删: `bestchoice/`(实测存在, backend零import) · v3_bestchoice
- 死亡条款执法(已建勿误判): `services/experiment_store.py` (C-R1/C-LEAK 硬raise + 6 readers)
- live conditioning 正解: `config/technical_stage.yaml` (研究用 `config/rally_stage.yaml` POST-HOC)
