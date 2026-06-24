# §8 切片 b/c-lite 阶段状态机 — architect 设计 (grill 后, 2026-06-25)

> prompt 要"先grill出§8实现计划(architect先行)再逐件建"。本 note = grill 结论 + 实现计划。
> owner=analysis/data_module_architecture_20260624.md §8。切片 a(独立触发)已 DONE(3af6d477)。

## grill 核心结论: 复用 manifest, 不新建 pipeline_stage_status 表 (单一真相源)

蓝图字面要新表 `pipeline_stage_status (stage/status/gate_evidence)`。grill 实测推翻"新建":
- `mart_pipeline_run_manifest` (754行) **已是 run 级状态账本**: run_id/pipeline_name/status/started_at/gate_result/blockers_json — 正好覆盖 stage 状态所需字段。
- pipeline 步**已在写它**: `data_quality.py` 调 `record_pipeline_run`; manifest 现有 `validate_global_data_quality`(128, =clean门)/`calc_risk_factors`(17, =process)/`refresh_source_watermarks`(14, =store) 等准阶段级行。
- **新建 pipeline_stage_status = 第二个状态中间表** → 违单一真相源 + 同 2026-06-24 survivorship 教训(真相在已有账本/数据, 别造第二张中间表去枚举/记录)。

**裁决: 复用 manifest** (pipeline_name=`pipeline.stage.<acquire|clean|process|store>`)。"当前阶段状态" = 该 pipeline_name 最新行。"stale"(上游重跑下游过时) = **派生计算** (upstream.started_at > this.started_at), 不存 flag(派生不存=单一真相源, 同 universe 由 K线派生不建 dim 表)。

## grill 第二结论: 状态机真消费方=前端卡片(defer), upstream-gate=now消费方(建)

§8 状态机的两个消费方:
1. **前端阶段控制卡片** (index.html view-data, vanilla JS) = 状态机的主用户价值, 但**产品面**(新 UI) → 范围 escalate 用户, **本轮 defer**。
2. **refuse-if-upstream-not-pass** (chunkyctl pipeline 独立触发安全网) = **now 消费方**(切片 a 已能单跑阶段, 但无上游门=可误跑 process 当 clean 失败)。→ 本轮建。

→ 建后端状态记录 + 读层 + upstream-gate(有 now 消费方, 非 premature); 前端 defer(消费方未greenlit, architect rule6 不建 infra 超前于消费方)。

## 实现计划 (逐件, 复用 manifest)

| 件 | 内容 | 风险 |
|---|---|---|
| 1. `pipeline/stage_status.py` | `record_stage(conn,stage,status,gate_result)` 薄包 record_pipeline_run(pipeline_name=`pipeline.stage.<s>`); `get_stage_status(conn)`→各stage最新行+派生stale; `upstream_status(conn,stage)` | 低(新模块只读+薄写) |
| 2. stage_runner+run.py 记状态 | 每阶段跑完 best-effort 记 check_pass/check_fail(degraded判定); clean 的 gate_result=data_audit overall。**best-effort**: manifest 写失败 try/except 不破链(阶段已成功) | 中(touch run.py 关键路径→best-effort隔离+测) |
| 3. chunkyctl pipeline upstream-gate | 跑 stage X 前查 upstream 最新 status==check_pass, 否则 refuse + 提示 --force 绕过; 上游更新→下游 stale 提示 | 低(stage_runner 内, 切片a已有CLI) |
| 4. 单测 | 状态写/读/派生stale/upstream门 red→green; daily_update 全链不破(--dry) | — |
| defer | 前端阶段卡片(产品面, 范围 escalate) | — |

奥卡姆边界(§8.3): 仅阶段状态(复用manifest)+线性上游门, 不上通用 DAG/拓扑引擎。

## 旁记 (stale artifact)
`data/reports/data_audit_latest.json` 现 stale(显 cry-wolf 修前 FAIL; live 7/7 PASS) — clean 阶段未重跑故未刷新, 下次 daily_update 自愈。非阻塞。
