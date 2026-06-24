# 数据血缘 + 路由中枢 — 数据管理模块功能设计 (2026-06-24)

> owner: 主会话 (控制面 design spec). 状态: 规划 (用户 2026-06-24 决: 先收 tdx 迁移尾巴再建).
> 触发: 用户洞察 — "拉血缘要拼 9 个 agent 重建 = 现在没有血缘管理; 做成数据管理模块功能,
> 相当于数据血缘和路由中枢, 从数据获取开始, 源-表-全部字段-字段组合进哪个表-被谁用-在哪展示"。
> 关联: master plan §1.5 (4 不变量含"血缘", 一直没物化) · CLAUDE §4.3 (删源铁律11 fan-in) · 本次 tdx 迁移反复手动 grep 消费方+判错语义的根因。

## -1. 北极星愿景 (用户 2026-06-24 升级定调) — 数据模块的 codegraph + 路由器 + 字典 + 总指挥

> 用户原话: "像数据管理模块的 codegraph, 像路由器, 每次增删改都先在这个路由中枢做, 然后才能具体工作, 就是数据模块的字典和总指挥"。
> + "变量加工: raw 进来经怎样计算得出什么变量被谁消费, 没加工标'未加工'"。
> + "不只满足需求, 帮我顶层设计: 分工合理/边界明确/可维护/可扩展/可追踪"。

这把本设计从**被动只读目录**提升到**主动控制平面**。一个东西四个面:
- **字典**: 数据模块每元素 (源/表/字段/变量/消费方/血缘) 唯一登记处 (= codegraph 索引面)
- **路由器**: 双向查 (从哪来/流去哪/改它炸什么) = 影响 + 溯源
- **总指挥**: **任何增删改先在中枢声明, 才能动手** = 强制闸
- **变量加工**: 中枢里 raw→计算→变量 的边; 无加工的透传字段显式标 "未加工"

**核心架构判断 — 声明先行 + 派生对账 闭环 (解 prescriptive vs derived 张力)**:
纯派生 (代码为真, 中枢只描述) = 炸了才报, 不防错; 纯声明 (中枢手填为真) = 漂移 + 可绕过。正解闭环:
1. **声明先行 (总指挥)**: 增删改先写中枢声明 (源X/字段Y/变量/消费Z/owner)。
2. **机器对账现实 (derive-verify)**: 从 code/schema 反推真实结构 diff 声明, 漂移=门 FAIL (中枢自诚实)。
3. **不可绕过闸**: 碰任何数据表/字段的 commit, 无匹配且对账通过的中枢声明=拦 (mio §7: 执法沉到提交者够不到处, 本地 hook 可跳=真闸在转正门/CI, 非礼节)。
= codegraph(索引) + moth(claims-vs-reality) 融合, 应用到**数据结构**。中枢既是源头(先写它)又诚实(现实必须匹配)。
直接根治本 session 痛点: tdx 迁移先过中枢→直接列 fact_common_major_holder_stock 全 fan-in + 真实语义(跨公司持股网)→不会判错+不用手 grep。

> 注: 下方 §1-§9 是初版"派生只读目录"框 (P1 起步仍成立, 是闭环的 derive-verify 半边); 顶层设计正由 design panel (wf_0ac4f5b0) 综合, 将以本节"字典+总指挥+声明先行闭环"为北极星, 产出完整模块蓝图 (子模块分工/边界/可维护扩展追踪机制/迁移路径), 届时本文件升级或新立 data_module_master_design。

## 0. 为什么 (gap 实证)

血缘**一直是半成品**: 碎片散在各段, 无统一可查的字段级端到端目录。本次重建用了 9-agent workflow (还失败 3 组), 正是缺失的实锤。现状:

| 血缘段 | 现状 | 在哪 | 缺什么 |
|---|---|---|---|
| 采集 源→表 | 部分 | sync_registry.yaml (tushare 41域); aif10 散在 acquire.py | aif10/非registry 源未纳统一登记 |
| SERVE 上游链 entity→db/表/vendor/asof | 部分 (D4门强制) | data_access.yaml (**仅 SERVE 读层**) | 非 SERVE 表/字段无声明 |
| L2 特征 表→特征 | 部分 | feature_registry.yaml | 字段级组合关系不显式 |
| 下游消费方 表→谁用 | **按需算非维护** | moth coupling --impact | 不是维护表, 每次重算 |
| **字段级端到端 源→字段→组合进哪表→消费方→展示** | **无** | — | 整条链无单一真相 |

后果 (真金白银): 删/迁表前靠人 grep fan-in (本次 tdx 迁移判错 fact_common_major_holder_stock 语义 = 机构持仓 vs 实际是跨公司持股网络); PIT 锚溯源靠读代码注释; 花钱拉的数据没人用也无人知 (income/express/balancesheet/fina_mainbz/dividend 已落库 0 消费)。

## 1. 设计原则 (第一性 + 奥卡姆)

1. **派生, 不手维护** (手维护必漂移 = 正在反对的反模式)。血缘从**既有真相源 + 代码**自动缝合, 不新增手填表。
2. **真相源分段**: 每类边 (acquire/transform/consume/display) 有自己的权威源, 血缘是它们的**投影**, 不是第二真相。
3. **路由中枢双向可查**: 下游 (字段→流向→展示) + 上游 (展示/决策→溯源到源字段) + 影响 (删/改→全 fan-out)。
4. **漂移门** (moth 式): 目录 vs 现实, commit 时拦; 派生物连跑两次必稳 (mythos §13)。
5. **从获取开始分层落地**: 先把 acquire 段 (源→raw字段→表) 做全, 再向下游延 (transform→consume→display), 不一次吃 85 表全字段。

## 2. 数据模型 — 血缘图 (lineage graph)

**节点 (node)**:
- `source_interface` — 采集接口/函数 (tushare daily / aif10 RPT_MAIN_ORGHOLDDETAIL / 派生 builder)
- `raw_field` — 源字段 (table.column, 落库即有)
- `derived_field` — 加工字段 (feature panel 列 / 视图列, 由 ≥1 上游字段组合)
- `table` — 落库表 (raw / clean / feature / serving / dim), 带 layer + status(active/frozen/archived) + pit_anchor
- `consumer` — 消费点 (service / feature / backtest / strategy / gate)
- `display` — 展示面 (dossier 卡 / workbench / 报告)

**边 (edge, 有向)**:
- `acquire`: source_interface → raw_field (PIT 锚字段标注)
- `transform`: {raw_field|derived_field}+ → derived_field (组合: "这些字段算出这个特征", 含公式/函数引用)
- `consume`: {table|field} → consumer (FROM/JOIN/读取点)
- `display`: consumer → display (哪张卡/哪个 UI 用)

**字段级**: transform 边记**字段→字段**组合 (用户要的"全部字段→组合进哪个表"); consume/display 可表级 + 关键字段标注 (奥卡姆: 字段级展示边只在 SERVE/特征层=PIT/泄漏敏感区强制)。

**死字段检测**: raw_field 无任何出边 (无 transform/consume) = 已采集未用 → 报告 (停采候选 / 档B 待挖)。

## 3. 真相源映射 (每类边派生自哪, 不新增手填)

| 边类型 | 派生自 | 解析方式 |
|---|---|---|
| acquire (源→raw字段) | sync_registry.yaml (tushare) + acquire.py step (aif10/非registry) + schema_core (字段) | 读 yaml + AST/schema 抽列 + PIT 锚从 registry pit_anchor |
| transform (字段→组合字段) | build_feature_panel.py FACTOR_COLS / feature_registry.yaml source_tables+columns / 视图 DDL | 解析 builder 的列定义 + feature_registry 声明 |
| consume (表/字段→消费方) | codegraph / moth coupling (代码 FROM/JOIN) + feature_registry + data_access.yaml | 复用 codegraph 依赖图 + moth fan-in, 不重造 |
| display (消费方→展示) | dossier.py load_* 函数 + 前端卡注册 | 解析 dossier load 函数 → 卡映射 |

**关键复用**: codegraph (已有依赖图) + moth coupling (已有 fan-in) + data_access.yaml (已有 SERVE 声明链) + sync_registry (已有采集声明) + feature_registry (已有特征声明)。血缘 builder = **缝合器**, 不是新真相。

## 4. 路由中枢 — 查询能力 (killer use cases)

1. **删/迁前自动 fan-in** (自动化铁律11, 本 session 手动做的): `lineage impact <table|field>` → 全下游消费方 + 展示面 + 影响等级。**直接根治** tdx 迁移那种"手 grep 漏判"。
2. **PIT 溯源**: `lineage provenance <display_card>` → 这张卡每个数字从哪源哪字段来 + PIT 锚 + 经哪些 transform。回测/实盘出数能溯源到源 (真金白银可审计)。
3. **死数据报告**: 无出边 raw_field 清单 → 停采候选 (省 tushare 调用) / 档B 待挖 backlog。
4. **覆盖/新鲜度交叉**: 血缘 × data_quality 新鲜度 → "这张卡依赖的源里哪个 stale" 一眼看到 (断流影响面)。
5. **onboarding / 文档**: 新 session 一图看懂"源→展示"全链, 不靠考古。

## 5. 物化形态 + 生成机制

- **生成**: `scripts/chunkyctl lineage build` (照 FEATURE_MAP 机器地图模式, 派生只读不手改)。
- **存储**: 二选一 (建时定) — (a) 结构化 JSON/parquet (`data/lineage/graph.json`, 边表 node表) 供查询 CLI; (b) DuckDB 表 (`lineage_node` / `lineage_edge` in 一个 meta DB) 供 SQL 查。倾向 (a) 起步 (轻, git-friendly diff), 量大再上 (b)。
- **查询**: `chunkyctl lineage impact/provenance/dead/show <x>` CLI + 可选前端图 (D3/mermaid)。
- **漂移门**: `check_lineage_drift.py` (wired into safe_commit) — 重生 vs 提交版 diff≠0 = 拦 (剔时间戳等必然波动行, mythos §13); 连跑两次必稳。
- **刷新触发**: schema/registry/feature_registry/builder 改动后重生 (codegraph sync 配对)。

## 6. 分阶段落地 (从数据获取开始, 不一次吃全)

- **P1 acquire 段 (源→raw字段→表)**: 缝合 sync_registry + acquire.py + schema → 全 raw 表的 源/接口/字段/PIT锚/status。**先交付这段** (用户"从数据获取开始")。含死表/死字段首报。
- **P2 consume 段 (表→消费方)**: 接 codegraph/moth fan-in → 每表下游消费方。**直接产出删前 fan-in 能力** (替代手 grep)。
- **P3 transform 段 (字段→组合字段)**: 解析 feature builder + feature_registry → 字段级组合关系 (用户"字段组合进哪个表")。
- **P4 display 段 (消费方→展示)**: 解析 dossier load_* → 展示面映射 (用户"在哪里展示")。
- **P5 漂移门 + 查询 CLI + (可选)前端图**: 闭环成维护型功能。

每阶段独立可用 + 单测 + 漂移门; P1+P2 已能根治本 session 的删源痛点。

## 7. 与现有的边界 (不重造)

- **不替代** sync_registry / data_access.yaml / feature_registry / moth / codegraph — 血缘是它们的**统一投影 + 缺口补全**。
- **可能收编**: data_access.yaml 的 SERVE 声明链 (db+table+layer+vendor+asof_col+code_col) 是 acquire+部分 transform 的现成片段, P1 直接吸收。
- moth coupling 的 fan-in = P2 的现成引擎 (调它不重写)。

## 8. 待用户拍板 (建时)

- 存储形态 (a) JSON vs (b) DuckDB 表。
- 字段级 transform 边的深度 (全字段 vs 仅 SERVE/特征层字段级 + 其余表级)。
- 前端图是否要 (P5 可选)。
- 是否纳入 daily_update 后置自动重生 vs 仅 commit 门触发。

## 9. 本次 workflow 产出 = P1 首次物化样本

analysis/ 下 workflow 已产 61 血缘条目 (3 组失败重跑可补), 字段含 source/interface/table/key_fields/layer/status/pit_anchor/consumers/notes — 即 P1+P2 的数据形态样例, 验证了 builder 该产出什么。建 P1 时以它为种子 + 校验目标。
