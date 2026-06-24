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
| 1. `pipeline/stage_status.py` [DONE df8d5e67] | `record_stage`/`get_stage_status`(各stage最新行+派生stale)/`upstream_ok`; 5单测 | 低 |
| 2. run_and_record [DONE] | run.py全链+stage_runner单跑 每阶段 best-effort 记 check_pass/check_fail(degraded delta判定); clean gate_result=data_audit overall。manifest写try/except不破链+dry跳过 | 中(touch run.py→best-effort隔离, --dry --skip-sync全链exit0验证) |
| 3. stage_runner upstream-gate [DONE] | `_upstream_refusal`: 跑stage X前 upstream_ok==False→refuse exit2+提示; 状态读失败=放行(best-effort门非硬安全)。`--force`绕过 | 低 |
| 4. 单测 [DONE] | 18 pipeline单测全绿(状态写/读/派生stale/best-effort不raise/upstream门/force绕)+ daily_update --dry全链不破 | — |
| defer | 前端阶段卡片(产品面, 范围 escalate 用户) | — |

**§8 backend (b/c-lite) 完整** (件1-4 DONE)。唯一剩 = 前端阶段控制卡片 (产品面, 等用户拍板范围)。每阶段门拆分 M1/M3 = 评估后不做 (data_audit 已是 M2 门 7/7 + watermark 已守 acquire freshness, grow-on-proven-need)。

奥卡姆边界(§8.3): 仅阶段状态(复用manifest)+线性上游门, 不上通用 DAG/拓扑引擎。

## 旁记 (stale artifact)
`data/reports/data_audit_latest.json` 现 stale(显 cry-wolf 修前 FAIL; live 7/7 PASS) — clean 阶段未重跑故未刷新, 下次 daily_update 自愈。非阻塞。
