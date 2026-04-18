# Interface Layering Plan

## Corrected Status

- Completed: institution router stock-research read-model收口已推进到 service，当前 `backend/routers/institution.py` 已无 router-local `conn.execute(...)`。
- Completed: `backend/routers/financial.py` 已不存在，审计里“financial/* 可退役”在当前仓库已不是待办项。
- Completed: screening 兼容接口已通过 `include_in_schema=False` 降级，并有 `backend/tests/test_screening_contract.py` 约束其不进入产品契约。
- Completed: 修正版接口清单已落地到 `docs/endpoint-classification-2026-04-16.md`，当前仓库已有 `public / internal / compat / retire` 四类分类文档。
- Completed: 工作台 / 系统操作职责拆分已落地；首页当前已将研究工作流与运维工作流拆到独立信息架构，系统操作入口集中在 `view-system`。
- Verified: `setup-validation/report` 与 `setup-tracking/snapshots` 仍被前端 `assets/js/app.js` 直接消费，应继续视为 public product contract。
- Verified: `setup-replay/*` 当前无仓内前端消费者，但其底层数据仍被 setup validation 能力间接复用，更适合归类为 internal analysis routes，而不是直接删除。
- Verified: `update/smart-plan` 当前无仓内前端消费者，但仍保留“先看计划再执行”的运维价值，应归类为 internal ops route。
- Verified: `holdings`、`industry-stats`、`setup-tracking/summary`、`stocks/attention/{stock_code}` 当前无仓内前端消费者，更适合归类为 internal raw/analysis routes。
- Verified: `northbound` 当前是明确禁用的预埋能力，不属于优先退役项。

## Remaining Work

1. 持续维护 openapi 边界测试。
   - 当前 internal/compat 分层已经落地，但后续若新增 endpoint，仍需同步补 contract 测试，避免隐藏路由重新回流到 public schema。

2. 仅在消费者消失时推进 internal 路由退役。
   - `setup-replay/*`、`update/smart-plan`、`holdings`、`industry-stats`、`setup-tracking/summary`、`stocks/attention/{stock_code}` 当前更适合作为 internal 保留。
   - 后续若这些入口失去分析/调试价值，再进入 retire 队列，而不是现在提前删除。

## Phase 1 Scope

- `backend/routers/institution.py`
- `backend/routers/updater.py`
- `backend/tests/test_institution_contract.py`

## Out Of Scope For Phase 1

- 删除 setup replay 逻辑
- 删除 smart plan 逻辑
- 进一步拆分前端页面结构
- 再次大规模重构 institution router

## Current Endpoint Decisions

- Public: `setup-validation/report`, `setup-tracking/snapshots`, `events`, `profiles/detail/{inst_id}`, `profiles/returns-history/{inst_id}`, `watchlist`, `candidate-setups`, `stock-trends`, `update/*` 主流程、评分卡配置与计算。
- Internal: `setup-replay/*`, `update/smart-plan`, `holdings`, `industry-stats`, `setup-tracking/summary`, `stocks/attention/{stock_code}`。
- Compat: screening 隐藏路由 `sector-momentum`, `dual-confirm`, `results`, `detail/{stock_code}`, `summary`。
- Retire: 当前活跃路由暂无新增高置信度候选；后续若 internal 路由失去分析/调试价值，再进入退役队列。