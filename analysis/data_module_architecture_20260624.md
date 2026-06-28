> **[SUPERSEDED 2026-06-28 — 项目重建为纯数据平台]**: 本文档描述的策略/serving/edge/M1-M8 架构 (含 L2 加工/dossier/scoring/主升浪 D 因子矩阵) **已随 2026-06-28 重建退役** (代码 ~245 文件 git rm + ~40 加工表物删)。现行真相源 = `analysis/data_platform_architecture_20260628.md` (纯数据平台: 原始 tushare+aif10 + 四地基 + SERVE + 治理)。本文件留作**历史参考**, 勿当现行命令源。四地基不变量部分仍有效 (已并入新文档 §1)。

# 数据管理模块 顶层设计蓝图 (2026-06-24)

> owner: 主会话 (控制面). 状态: 设计定稿待用户拍板迁移顺序. 扩展宪法 `data_module_toplevel_design_20260622.md` §1.5 (四不变量) 落到子模块物理边界+契约+执法门.
> 产出: 4 视角 design panel (流水线分层/声明式registry/域纵切/血缘图为脊) → 对抗评分+综合 (wf_0ac4f5b0). 全文综合见本文件.
> 北极星: 用户 2026-06-24 定调 — "数据模块的 codegraph + 路由器 + 字典 + 总指挥, 增删改先过中枢再干活" + "变量加工 raw→计算→变量→消费, 无加工标未加工" + "分工合理/边界明确/可维护/可扩展/可追踪".

## 实施进度对账 (2026-06-27 — §9 完成 + 全量核对 workflow wajh30veq 22项逐条核真相源)

> 2026-06-27 更新: §9 Stage E 物删完成 = 四地基全 DONE; 全量核对 (22项查代码/DB/moth/registry/测试, 不信文档自报) 定状态如下。

| 组件 | 蓝图定位 | 现状 | 证据 |
|---|---|---|---|
| **四地基/四不变量** | 1主键PIT/2库分区/3分层/4单一源 | [DONE] **全 DONE** (核对全绿) | data_access asof强制 / reference 4dim / data_layer_audit / serve-bypass=0 |
| M1 ACQUIRE / M2 CLEAN | 保留+收编 | [DONE] 在用 (M1 acquire 44域 / M2 qfq tushare-only+data_audit 7/7) | M2 ETF迁tushare DONE; 债: 9 内联 aif10 step 未进 registry |
| M3 PROCESS | 收编 | [DONE] L2 panel 物化 (build_*_panel); 风格债: build_segment_panel 直连 market vs build_signal_panel 走 data_loaders (取数模式不统一, 非 leakage, moth bypass=0) | — |
| M4 SERVE (22 entity) | DONE | [DONE] DONE (薄分发器+generic driver+data_access.yaml 22 entity) | — |
| M6 DISPLAY / M7 ORCHESTRATE | DONE/保留 | [DONE] DONE | — |
| **§8 阶段独立化** | 四件套 | [DONE] **backend DONE** (a/b/c-lite: stage_runner+stage_status+upstream门); **前端卡片 [NO] 未起** (产品面待用户拍板) | commit 3af6d/df8d5/ef9f4 |
| **M5 LINEAGE (T2 acquire+consume)** | 新建脊柱 (North-Star) | [DONE] **T2 DONE** (services/lineage + chunkyctl lineage build/impact/provenance/dead + graph.json 472节点/1191边 + check_lineage_drift + 10单测) | commit 766a12ce |
| **§9 reference 拆库** | 9.5 alias-routing | [DONE] **Stage E 物删 DONE 2026-06-27** (4 dim 迁 reference.duckdb + smartmoney 物删 + 2进程锁解耦实测 PASS + moth section9-dims-in-reference/absent-smartmoney 防回潮断言) | commit a6b48eea; 见 §9.5 |
| **T0 Gap1 (builder→SERVE)** | 最高优先 (leakage) | [DONE] **闭合** (moth serve-consumer-bypass-zero=0 实跑; build_ 加工成员读 raw 非违规, goal.md 2026-06-26 重定性) | moth bypass=0 |
| **T1 变量三态** | feature_registry 4字段+factors/ | [WIP] **本轮推进中** (用户 2026-06-27 选"补平台完备项, T1 优先") | — |
| **T3/T4 血缘 transform/display + drift硬闸** | 完备化 | [NO] 未起 (T1 后) | drift 闸现 informational WARN, T4 才硬化 |
| **M8 +5门** | 补门 | [NO] 未起 (依赖 T1/T3) | — |
| 预存债 (非本session引入) | — | moth serve-bypass=0 (源退役临时=1) | stash-baseline 核证 |

**架构师定论 (本 session 新增, ultracode+第一性原理)**: §9 真 blast 源 = **4 套并行连接模型碎片化** (get_conn / 直连 duckdb.connect / 注入conn / bestchoice _attach_smart_db AS sm), 非搬表本身。**§9 Phase 0 (4 dim表收口走 SERVE/resolver) ≡ T0 (全读走 SERVE) 的 dim 切片** = 同一份连接收口工 → 做一次推进两个平台目标。DuckDB 跨进程文件锁是进程级(实测): 隔离连接不可能解, 拆库正当; 但机制走 alias-routing(无view) 非 view+ATTACH。

**下一步优先级 (reconciled, 2026-06-27 — §9+T0 完成后更新)**:
1. ~~T0/§9-Phase0 汇流~~ **[DONE 2026-06-27]**: §9 Stage E 物删完成 (alias-routing/dim_read_conn, 非 view+ATTACH) + T0 Gap1 闭合 (bypass=0)。
2. **T1 变量三态 (feature_registry 4字段 + services/factors/) — 本轮推进中** (用户 2026-06-27 选"补平台完备项, T1 最有价值: 因子加工可追踪+防双算")。阻塞 T3/M5 transform边。
3. T3/T4 血缘完备 (transform 字段级 + display + domain + drift 硬闸) — T1 后。
4. M8 补 5 门 (variable-single-compute-point 等) — 依赖 T1/T3。
5. §8 前端阶段卡片 (产品面待用户拍板)。
> 真金白银 caveat (§6 不变): 以上全平台债, **非 alpha 钱路**; 四地基 + T0(leakage) 已硬, **设计意图是转档B edge 确认**; 用户 2026-06-27 选先补 T1 完备项再转 edge (T3/T4/M8/前端可再缓)。

## 0. 北极星 — 数据模块的 codegraph + 字典 + 总指挥 (闭环)

中枢一个东西四个面: **字典**(每元素唯一登记=codegraph索引) · **路由器**(双向查 从哪来/流去哪/改它炸什么) · **总指挥**(增删改先声明才动手=强制闸) · **变量加工**(raw→计算→变量 的边, 无加工标"未加工").

**核心架构判断 — 声明先行 + 派生对账 闭环** (解 prescriptive vs derived 张力):
1. **声明先行 (总指挥)**: 增删改先写 registry 声明 (源/字段/变量/消费/owner)。registry = 字典 = 你先写它。
2. **派生对账 (自诚实)**: 从 code/schema 反推真实结构 diff 声明, 漂移=门 FAIL。中枢不撒谎。
3. **不可绕过闸**: 碰数据表/字段的 commit 无匹配且对账通过的声明=拦 (mio §7: 本地 hook 可跳, 真闸在转正门/CI)。
= codegraph(索引) + moth(claims-vs-reality) 融合到**数据结构**。既是源头(先写)又诚实(现实必匹配)。
直接根治本 session 痛: tdx 迁移先过中枢→直接列 fact_common_major_holder_stock 全 fan-in + 真语义→不判错不手 grep。

## 1. 子模块清单 (M1-M8, 按"对血缘图的一类操作"切 owner)

每子模块拥有图上一类边/节点的唯一写权 (V4 骨架)。**保留现有物理结构, 只补契约+登记**。

| 子模块 | 单一职责 | 拥有 (图元素+件) | 边界 (跨边界唯一通道) | 现状 |
|---|---|---|---|---|
| **M1 ACQUIRE** `pipeline/acquire.py`+`sync_registry.yaml` | vendor→L0 raw 镜像, **零计算** | `acquire` 边; raw_* 写权; 47 采集域 | 输出=L0 raw 表 (唯一载体) | 保留. 债: 9 内联 aif10 step 未进 registry |
| **M2 CLEAN** `pipeline/clean.py` | L0→L1 写时归一 (复权/格式/单位) | `transform(normalize)` 边; L1 PIT 表+qfq 真相源 | 输出=L1 物化表 | 保留 |
| **M3 PROCESS** `pipeline/process.py`+builders | L1→L2 **变量加工**, **单一计算点** | `transform(derive)` 边; L2 panel 写权; 纯函数层 | 输入必走 SERVE (D1门); 输出=L2 panel | 收编. **收 Gap1** (2 builder 绕读) |
| **M4 SERVE** `services/data_access/` (23 entity) | 唯一取数+PIT asof+口径锁+provenance | `consume` 边唯一闸 | `get(entity,codes,as_of)→DataResult{rows,provenance}` | **DONE** 保留 |
| **M5 LINEAGE** `services/lineage/`+`chunkyctl lineage` ★新 | 缝合 M1-M8 声明成单一可查 DAG, **派生不手维护** | 整图物化+CLI (impact/provenance/dead) | 只读各 registry 投影, 不产真相 | **T2 DONE 2026-06-26** (acquire+consume 缝合, graph.json 472节点/1191边, impact/provenance/dead 可查, drift门; 余 T3 transform字段级/T4 display+domain) |
| **M6 DISPLAY** `read_model.py` | `(stock,as_of)` 切片, 禁裸 SQL | `display` 边; read-model 切片 | 输入=SERVE; 输出=自包含 HTML | DONE 保留 |
| **M7 ORCHESTRATE** `pipeline/run.py`+`context.py` | 跑节点 (独立/幂等/可续), 薄编排 | 执行序; degraded 续跑+告警送达 | `chunkyctl update` | 保留, **不强行 DAG 化** (砍 premature) |
| **M8 GOVERN** `.moth/`+`check_*.py`+safe_commit | 所有门载体 (45 断言+漂移门) | 图不变量执法 | commit 时拦 | 保留+补 5 门 |

**P 平台共用内核纪律 (graft V3 最重要一条)**: M4/M5/M7 是各数据域**共用内核**, 数据域只声明+提供纯函数, 不重写内核 (拦纵切陷阱: 每域各造取数/PIT=第二真相源增殖)。

## 2. 变量加工血缘模型 (raw→计算→变量→消费, 未加工显式标注)

**奥卡姆决策**: **不新建 variable_registry.yaml**, 扩展现有 `feature_registry.yaml` (已是 L2 变量 owner), 每 variable 补 4 字段。少一层真相源。

**三态枚举 = 加工模型核心 (防 leakage)**:

| source_kind | 含义 | PIT 锚 | 血缘图表现 |
|---|---|---|---|
| `derived` | 我方纯函数算出 | inputs asof + 构造保证 | 实线 transform 边 + compute_fn |
| `vendor_precomputed` | 厂商已算 (stk_factor_pro 261列) | `ann_date` 披露时点 | 实线标"厂商现成", 禁 PIT 重核 |
| `passthrough` | **原样透传未加工** | 上游 asof | **虚线 identity 边, 标 untransformed** |

```yaml
# feature_registry.yaml 每 variable 扩展 (不新建文件)
variables:
  momentum_z:   {source_kind: derived, inputs: [kline_qfq.close, kline_qfq.vol], compute_fn: factors.zscore_60, pit_stance: pit_safe_by_construction, consumers: [signal_assembler, dossier.technical_dim]}
  alpha158_kmid: {source_kind: vendor_precomputed, inputs: [stk_factor_pro.kmid], compute_fn: null}   # 锚 ann_date, 禁归 derived (防 261 列假 PIT 点)
  close:        {source_kind: passthrough, inputs: [kline_qfq.close], compute_fn: null, notes: 原样透传非因子}   # ★未加工显式标记
```
缺省 source_kind = moth FAIL (强制每变量声明加工态)。透传字段链上虚线一眼可辨, 不伪装派生。

## 3. 5 目标落地机制 (每条带执法门, 不靠人记)

| 目标 | 机制 | 执法门 |
|---|---|---|
| **1 分工合理 (单一计算点)** | 每子模块=一类边唯一写 owner; 同 derived 变量只能一条 transform 边 (物理不可能两处算) | `serve-consumer-bypass-zero` (现=2→收口0) + 新 `variable-single-compute-point` |
| **2 边界明确** | 归属=图上边类型; registry 加 `domain:` 标签 (非新建 yaml) 给字段语义一行声明 | 新 `lineage-edge-typed` + `domain-coverage` (每活表属且仅属一域) |
| **3 可维护 (局部化)** | 爆炸半径=图 fan-out (确定性查); 改 entity=改 yaml 条目本体零改 | `lineage impact <table>` 替代手 grep + 新 `check_lineage_drift.py` (连跑两次 diff=0) |
| **4 可扩展 (加列非加表)** | 加因子=registry 加条目+纯函数; 加源=sync_registry 加域 | `data_access_preflight` (schema 漂移 FAIL) |
| **5 可追踪 (含变量加工)** | by construction: 图=registry 投影; 透传标 passthrough; PIT 锚全程 provenance | 新 `lineage-complete` (展示字段追不到源=FAIL) + 新 `compute-state-declared` |

## 4. 分阶段迁移 (保留/收编/退役 + gate)

**保留**: SERVE 全套 (23 entity+5门) · 8 文件 pipeline · 7 库分区+data_layers · 45 moth 门 · read_model · provenance 信封 · EntitySpec.compute_fn (已埋种子)。
**收编**: feature_registry 加 4 字段 · 3 builder FACTOR_COLS+内联公式→读 registry 引擎 · sync_registry/data_access 打 domain 标签 · 61 血缘种子作 M5 校验目标 · acquire.py 9 内联 step→registry。
**退役物删**: tdx F10 产品表 (迁后) · tdxhub 财务簇 · akshare external_attention (无 tushare 等价)。退役走铁律11, `lineage impact` 落地后替代手 grep。

| 阶段 | 动作 | gate | 解决痛点 |
|---|---|---|---|
| **T0 收 Gap1** ★最高 | build_segment_panel/build_signal_panel 改走 SERVE | `serve-consumer-bypass-zero==0`+PIT 单测 | ④leakage (唯一卡 alpha 的洞) |
| **T1 变量加工登记** | feature_registry 补 4 字段+建 `factors/` 纯函数层; builder 收编引擎; 透传标 passthrough | `compute-state-declared`+`variable-single-compute-point` | ②变量加工不可追踪 |
| **T2 血缘 acquire+consume 段** [DONE]**DONE 2026-06-26** | `lineage build` 缝合 sync_registry+data_access+information_schema+确定性git-grep fan-in→impact/provenance/dead CLI | `lineage impact` 替代手 grep (已用于 §9 4表 fan-in) | ①血缘没物化+④删源手 fan-in 根治 |
| **T3 血缘 transform+display 段** | 解析 feature_registry+builder→字段级 transform 边+展示映射 | `lineage provenance <card>` 端到端 | ⑤端到端+透传标记 |
| **T4 闭环+domain 标签** | check_lineage_drift wired safe_commit; registry 打 domain 标签 | 全门绿+drift diff=0 连跑两次 | ③分工/边界成文可执法 |

**T0→T2 即根治本次全部痛点**, 全程档A 地基不碰档B。

## 5. 对抗 flag (真需要 vs 奥卡姆砍 + 边界漏洞)

**真需要**: T0 收 Gap1 (4 版一致, 真金白银 leakage) · 三态枚举+透传标 (命中目标5+防 261 列假 PIT) · 扩 feature_registry 非新建 (少层真相源) · T2 lineage impact (替代手 grep)。

**奥卡姆砍/降级**: 砍 pipeline.yaml DAG 化 (M7, premature, 降 T5 待触发) · 砍独立 domain_ownership.yaml (改 registry domain 标签) · 砍独立 variable_registry.yaml (并入 feature_registry) · AST 字段级解析只在 SERVE/特征层强制, 其余表级+关键字段。

**仍可能漏的边界 (盯)**:
1. **门假绿→图假真 (最危险)**: 图可信度=门可信度 (check_serve_read_layer 曾只扫 dossier 伪绿)。缓解: 4 新 lineage 门必过对抗复审+红绿单测, 不裸 substring grep。
2. **动态/拼接 SQL 的 consume 边**: codegraph 漏动态 import→图说"无消费方"实有→删表炸。缓解: 删表前仍 moth coupling 交叉, AST 边标 confidence。
3. **跨域派生归属歧义**: sector_momentum/regime 等 10-15 表归哪域要人工裁决 (tdx 判错同类风险)。门只防漂移不防初判错→主会话亲核字段语义不只看测试绿。
4. **纯函数 vs 物化静默分叉**: 调同函数保证不了传同参数。缓解: 参数进 registry+运行时 parity 单测 (物化值 vs 现算值一致)。

## 6. 元 flag (诚实, 真金白银优先级)

**这套地基不直接产 alpha** (4 版共识)。它根治删源/迁移/onboarding 工程债, 但**通向赚钱的关键路径 = T0 收 Gap1 (leakage) → 转 edge 确认 (解锁档B)**, 不是 T2-T4 血缘完备化。优先级: **T0 (真金白银) >> T1-T2 (痛点根治) > T3-T4 (完备化可缓)**。删源近尾声则 T3-T4 紧迫性 < 直接推 edge 确认。

## 7. 关键文件路径
- 新建: `services/lineage/` (M5 缝合器) · `scripts/chunkyctl lineage` CLI · `data/lineage/graph.json` · `scripts/check_lineage_drift.py` · `services/factors/` (纯函数层)
- 扩展: `feature_registry.yaml` (+source_kind/inputs/compute_fn/pit_stance) · `sync_registry.yaml`+`data_access.yaml` (+domain 标签)
- 收口 T0: `scripts/build_segment_panel.py`+`build_signal_panel.py` (bypass=2→走 SERVE, task#55)
- 补门: `.moth/` 加 variable-single-compute-point/lineage-complete/lineage-edge-typed/compute-state-declared/domain-coverage
- 现成复用: `data_access/spec.py` EntitySpec.compute_fn (已埋三态种子) · 61 血缘种子
- 不碰: 宪法 §8.1/§11 (档A PROCEED/档B BLOCK)

## 8. 阶段独立化 + 门控 + 前端反映 (用户 2026-06-24 升级: 从根本解耦)

> 用户原话: "数据获取/清洗/加工都要独立各管各的, 前端按钮独立; 获取后原始数据在自己模块检查准确性+完整性, 检查通过提示可以开始计算了, 然后前端点击计算; 后端这么设计并体现前端, 从根本解耦明确边界易增删改管理"。
> = M1/M2/M3 从"daily_update 里顺序跑的函数"升级为**独立可触发 + 自带验收门 + 状态机驱动前端**的 bounded 模块。**修正 §5 早先"砍 DAG 化"**: 用户显式需求=真实触发 (非 premature, architect rule6 撞墙=grow organ), 但只做**阶段状态机**不做通用 DAG 引擎 (奥卡姆)。

### 8.1 每阶段 = 独立模块四件套
每 stage (acquire/clean/process, 及 serve) 拥有:
1. **独立触发** [2026-06-25 后端 DONE]: 后端命令 (`chunkyctl pipeline acquire|clean|process|store`, 单跑不连带) + 前端独立按钮。daily_update 退化为"按门顺序链跑全部"的便捷编排, 非唯一入口。**已实现**: `backend/services/pipeline/stage_runner.py` (run_stage 复用 run.py 同款 4 阶段函数+PipelineContext, degraded→exit1) + `scripts/chunkyctl pipeline` case (→ `python -m services.pipeline.stage_runner`); 单测 test_pipeline_stage_runner.py 4 绿。前端独立按钮属切片 c。
2. **自带验收门 (用户要的"自己模块检查准确性+完整性")**: 阶段跑完跑**自己输出**的验收 — **完整性**(覆盖 vs 交易日历/universe, 无静默截断/0行) + **准确性**(schema/grain/PIT锚齐, 值域合理)。把现 data_quality 单体后置检查**拆成每阶段前置门** (M1 owns raw门 / M2 owns clean门 / M3 owns feature门)。
3. **"ready" 信号 + 下游门控**: 验收 pass → 写 ready → 解锁下游 (前端下游按钮 enabled / 后端下游命令 refuse-if-upstream-not-pass)。= "检查通过→提示可以开始计算→点击计算"。
4. **状态机**: `pipeline_stage_status` 表 (stage / run_at / status∈{not_run/running/done_unchecked/check_pass/check_fail/stale_upstream_changed} / gate_evidence)。上游重跑→下游标 stale 须重验。

### 8.2 前端反映 (M6/控制面)
阶段控制面板: 每 stage 一张卡 = [运行按钮 enabled iff 上游 pass] + [验收门结果 (完整性/准确性 具体数字)] + [ready→解锁下游提示]。这块 UI 就是 M5 路由中枢/总指挥的操作面 (中枢知每阶段状态+门结果)。

### 8.3 奥卡姆边界
stages 近线性固定序 (acquire→clean→process→serve), **每阶段状态 + 上游pass门**足够, 不上通用 DAG/拓扑引擎; 真出现复杂分叉依赖再升。M7 ORCHESTRATE 从"固定顺序"→"阶段状态机+门链"(仍薄)。

## 9. DB 文件按模块/写锁域分 — 评估 (用户问)

> 评估结论: **应该, 但精确原则 = "1 文件 = 1 写锁域", 通常对齐模块; 本轮最高价值改动 = 把读多写少 reference 从 smartmoney 大杂烩拆出 (直接根治本轮 contention)。**

### 9.1 现状实测 (文件层)
| 文件 | 大小 | 内容 | 对齐? |
|---|---|---|---|
| tushare_raw | 7.5G | raw_tushare_* vendor 镜像 | M1 干净 |
| market | 1.8G | K线/复权 | ~ M2 |
| feature_store | 1.7G | L2 特征 panel | M3 干净 |
| **smartmoney** | 2.8G | **holders/qfii/org_holding facts(写重) + dim/universe/ST(读多写少 reference) + 其它 facts** | ✗ **混多模块** |
| etf / experiment_store | 106M/268K | ETF / verdicts | niche |

### 9.2 第一性原理 + 建议
分文件真正用途 = **隔离写锁** (DuckDB 单写锁/文件)。raw/market/feature 已按 stage(=模块) 干净分。**smartmoney 把读多写少 reference 和写重 facts 混一文件 → 本轮户数回填读 universe 撞 seed 写 smartmoney 锁崩 = 实证根因**。
**最高价值即时改动 = 拆 reference 出 smartmoney**:
- 新 `reference.duckdb` (读多写少): dim_active / universe / 交易日历 / ST / security_master。所有人只读 ATTACH, 与 facts 写锁彻底解耦 → 根治 contention。
- smartmoney 留写重 facts, 仅当真并发写再细分 `facts_<域>`。其余维持。
**精确原则 (对抗性)**: 分区驱动 = **写锁竞争/访问模式** 非"模块纯洁性"。模块不并发写同库可共享文件; 真正必拆 = **读多写少 reference vs 写重 facts** (本轮坑) + **不同 vendor raw** (已分)。

### 9.3 对抗 flag (成本/风险)
1. 跨库 JOIN: 多 ATTACH, 但 M4 SERVE 已抽象 (entity 知在哪库); reference 小且只读成本低。
2. 迁移 (mythos §12): 挪表跨文件 = COPY FROM DATABASE **丢约束/索引** → 必 EXPORT/IMPORT + 五件套验收 (行数+抽值+约束数+索引数+upsert冒烟)。一次性有界。
3. 过度分区 (奥卡姆): 别一表一库, 按写锁域分, 最小高价值 = reference 拆出。
4. 经 `database_manifest` (已是 DB 分区 config 真相源) 改 alias→path + 迁表 + repoint, 不 hardcode。

### 9.5 执行计划

> **[2026-06-26 SUPERSEDED — 机制重定性, 权威移交 `analysis/section9_reference_split_verified_plan_20260626.md`]**
> 下方初版 (get_conn ATTACH + smartmoney view) 经 ultracode 8-agent 对抗验证 (workflow wxs0iyxin) + controller 2进程锁实测 **被证伪**:
> 1. **前提成立** (拆库正当): DuckDB 跨进程文件锁是进程级 (实测 smartmoney RW 阻塞另进程 RO open) → 隔离连接物理不可能解, reference 独立文件是唯一解。
> 2. **但 view+ATTACH 机制有 ≥8 break-point** (bestchoice第4套连接 JOIN sm.dim 漏判 / schema_migrations CREATE INDEX on view 硬炸 / 5+直连audit脚本 / 注入conn一大类 / duck_adapter attach吞异常§4.4 / 磁盘污染 / 读触发写 / check_universe_filter闸)。
> 3. **真 blast 源 = 4 套并行连接模型碎片化** (get_conn/直连/注入conn/bestchoice), 非搬表。
> 4. **机制定案: alias-routing (SERVE) 非 view+ATTACH** — 4 dim表纳 data_access SERVE entity + db别名→reference (resolver 直连, 无view无ATTACH), 8 break-point 绝大多数蒸发。
> 5. **§9 ≡ T0 (全读走SERVE) 的 dim 切片** = 同一份连接收口工。修正序: Phase0 连接收口(dual-write中间态安全)→ Stage C 别名切换 → D 验收(测对竞争:写reference+ATTACH 非假绿backfill+seed)→ E 物删(escalate)。
> **以下原表仅留历史溯源 (Stage A DONE 仍有效; B-E 的 view+ATTACH 法已废, 按验证版 spec 走 alias-routing)。**

> [初版, 已偏离] 审计实测: `dim_active_a_stock` (universe真相源) 有 **25 读消费方** + 写方 (security_master/build_dim_listing_status/calendar_extension) + schema DDL (schema_core)。机制 = get_conn (中央工厂 33 调用) ATTACH reference 只读 + smartmoney 留 view (读透明) + 写方 repoint reference + sync_runner 读 reference (撞锁根治)。**高风险** (动 get_conn = 全 app blast)。
> REF 集 (奥卡姆核心): dim_active_a_stock / dim_all_ever_listed / dim_listing_status / dim_trading_calendar (universe+calendar 读多写少)。静态config dim (fee/rules/segment) 待评估扩。

> **[2026-06-27 全 DONE — 机制走 alias-routing/dim_read_conn 非 view+ATTACH; B-E 计划已被实际执行路径取代]**
> 实际执行 (非下表的 view 法): Phase 0 fan-in 审计 (workflow wf_df52c6c6) → chunk1-4 逐 dim reader 迁 resolver.dim_read_conn (auto-fallback reference) + writer dual-write→reference-only → Stage-E 安全账本 (workflow wpa8busnl, 34文件/177读点 + 对抗验证) → Stage E 物删 (db_lifecycle_delete archive+deletion_record) + 移除 smartmoney schema-init DDL 防重建。下表 view+ATTACH 法**已废**, 仅留 Stage A 历史。

| Stage | 动作 | 状态 |
|---|---|---|
| **A 保真建库** | `migrate_reference_db.py`: EXPORT/IMPORT 4 表→reference.duckdb + 5件套验收 | **[DONE 2026-06-24]** 5208/5210/5210/5343 行全 match |
| **B-D 读写收口 (alias-routing 实际路径, 非 view)** | 逐 dim reader 迁 dim_read_conn (24触点) + writer reference-only + 2进程锁解耦实测 | **[DONE 2026-06-27]** commit 70657dec/bc213639; CI offline 90 + moth 47/0/0 |
| **E 物删 (不可逆)** | 物删 smartmoney 4 表 (archive parquet + deletion_record) + 移除 schema-init DDL 防重建 | **[DONE 2026-06-27]** commit a6b48eea; moth section9-dims-absent-smartmoney==0 棘轮锁 |

**§9 全 DONE = 不变量#2 完成 = 四地基全部 DONE。** 实际机制 alias-routing (dim_read_conn 返 reference-主库连接, 裸 FROM 直解析, 非 ATTACH) 比原 view+ATTACH 计划干净 (无 CREATE INDEX on view 硬炸/磁盘污染)。
