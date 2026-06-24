# 数据管理模块 顶层设计蓝图 (2026-06-24)

> owner: 主会话 (控制面). 状态: 设计定稿待用户拍板迁移顺序. 扩展宪法 `data_module_toplevel_design_20260622.md` §1.5 (四不变量) 落到子模块物理边界+契约+执法门.
> 产出: 4 视角 design panel (流水线分层/声明式registry/域纵切/血缘图为脊) → 对抗评分+综合 (wf_0ac4f5b0). 全文综合见本文件.
> 北极星: 用户 2026-06-24 定调 — "数据模块的 codegraph + 路由器 + 字典 + 总指挥, 增删改先过中枢再干活" + "变量加工 raw→计算→变量→消费, 无加工标未加工" + "分工合理/边界明确/可维护/可扩展/可追踪".

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
| **M5 LINEAGE** `services/lineage/`+`chunkyctl lineage` ★新 | 缝合 M1-M8 声明成单一可查 DAG, **派生不手维护** | 整图物化+CLI (impact/provenance/dead) | 只读各 registry 投影, 不产真相 | **新建** (脊柱; 61 条种子已有) |
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
| **T2 血缘 acquire+consume 段** | `lineage build` 缝合 sync_registry+data_access+codegraph→impact/provenance CLI+死字段报告 | `lineage impact` 替代手 grep (tdx 迁移验收) | ①血缘没物化+④删源手 fan-in |
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
