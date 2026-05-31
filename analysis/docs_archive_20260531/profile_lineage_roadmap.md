# 画像 / 血缘 / 股票档案路线图

Date: 2026-05-27
Status: planned
Authority: subordinate to `docs/PROJECT_CONSTITUTION.md`, `docs/top_level_design_review.md`

本文档把“股票画像、机构画像、主力行为画像、股票档案前端、全局前端 UI/交互重设计”纳入架构重构计划。
结论先记: **先血缘和画像契约, 再画像 mart/API, 最后前端展示**。前端不能先于
数据可信度, 也不能把 proxy 或 stale raw 显示成生产证据。

## 当前 FAIL / 风险

| 项 | 状态 | 阻断意义 |
|---|---:|---|
| 数据血缘 | `docs/data_lineage_spec.md` 仍标记 design only, 现有 registry 多为 metadata-only | 画像若无输入表、PIT、freshness、build command, 会变成不可追溯结论 |
| `raw_fund_flow_daily` | deprecated/stale, 本地停在 2026-04-24 | 真实主力/超大/大/中/小单资金流不可做生产画像证据 |
| 画像来源 | 已有 `mart_institution_profile`, `mart_stock_trend`, `stock_graph`, `stock_detail_read` 等碎片 | 需要统一画像 contract, 否则前端会拼接多套口径 |
| 前端股票档案 | 已有 stock detail/graph/institution 展示片段 | 不应先扩 UI; 必须等后端返回 `unknown/proxy/production` 与 lineage |

## 第一性原理

| 问题 | 结论 |
|---|---|
| 画像的用途是什么? | 帮助理解一只股票/机构/主力行为的证据结构, 不是直接替代交易信号 |
| 最小可信单元是什么? | `profile_component = value + as_of_date + built_at + source + freshness + confidence + lineage` |
| 什么不能省? | PIT key、数据新鲜度、输入表、是否 proxy、缺失时 `unknown` |
| 什么可以晚做? | 前端卡片美化、全市场画像排名、复杂交互 |

## 奥卡姆方案

| 方案 | 判断 |
|---|---|
| 新建一套画像平台 | 暂不做, 会增加 parallel truth source |
| 复用现有 mart/service, 加统一 contract | 采用, 最少新增对象且能约束前端 |
| 让前端直接拼多个 API/raw 表 | BLOCK, 血缘和 unknown 口径不可控 |

## 目标 Contract

所有画像组件必须返回同一类元信息:

| 字段 | 含义 |
|---|---|
| `component_id` | `stock_trend`, `institution_behavior`, `main_force_cyq` 等稳定 ID |
| `entity_type` / `entity_id` | `stock/code`, `institution/inst_id` |
| `as_of_date` | 画像判断对应的交易日或事件日期 |
| `built_at` | 画像生成时间 |
| `value_json` | 画像值、标签、分数、解释 |
| `source_tables` | 直接输入表 |
| `lineage_ref` | `lineage://...` 或 trace 命令/registry key |
| `freshness_status` | `pass`, `warn`, `fail`, `unknown` |
| `evidence_status` | `production`, `proxy`, `research`, `unknown` |
| `notes` | 缺失、代理、stale、PIT 限制说明 |

## 分阶段实施

| 阶段 | 优先级 | 目标 | 主要动作 | 验收 |
|---:|---|---|---|---|
| 0 | P0 | 血缘和数据需求先行 | 扩展 `tdx_data_need_coverage.yaml`; 明确画像需要的字段、grain、PIT/freshness、consumer | 缺源项可审计, 不靠口头记忆 |
| 1 | P0/P1 | 画像 contract 定义 | 在后端服务层定义统一 profile component 返回结构; 缺数据输出 `unknown` | 单元测试覆盖 stale/proxy/unknown |
| 2 | P1 | 股票画像读模型 | 复用 `mart_stock_trend`, `stock_graph`, horizon/effect、风险/流动性、机构事件, 汇总为 `stock_profile` read model | 不直接读 `dim_active_a_stock` 做 universe; 带 lineage/freshness |
| 3 | P1 | 机构画像收敛 | 在现有 `mart_institution_profile` 基础上补 lineage、freshness、source status | 机构画像可解释持仓周期、行业偏好、事件活跃度 |
| 4 | P1/P2 | 主力行为画像 | CYQ + 量价 + 事件 + 资金流; 真实订单流恢复前资金流维度标 `unknown` 或 `proxy` | 不使用 stale `raw_fund_flow_daily` 作生产证据 |
| 5 | P2 | 股票档案 API | 统一 `stock_detail_read`, `stock_graph_read`, institution/profile 组件为股票档案 read API | 前端只消费一个稳定 contract |
| 6 | P2/P3 | 前端股票档案改造 | 增加画像、机构、主力/CYQ、血缘/新鲜度分区; 展示 `unknown/proxy` 标识 | UI 不展示未测指标为确定结论 |
| 7 | P3/P4 | 全局前端 UI/交互重设计 | 按项目架构和流程重组导航、任务流、gate 状态、证据抽屉、异常处理和操作权限 | 后端 contract/gate 稳定后实施, 不以视觉先行 |

## 画像组件优先级

| 组件 | 优先级 | 数据基础 | 当前处理 |
|---|---|---|---|
| 股票基础画像 | P1 | K 线、趋势、风险、行业、graph | 可先做, 但必须带 freshness/lineage |
| 机构画像 | P1 | 持仓、调研、龙虎榜、行业统计 | 复用 `mart_institution_profile`, 补契约 |
| 主力行为画像 | P1/P2 | CYQ、量价、龙虎榜、资金流、减持/解禁 | CYQ 可研究; 真实资金流未恢复前 production 维度为 `unknown` |
| 股票档案前端 | P2/P3 | 后端画像 contract | 等 contract 稳定再做 |
| 全局前端 UI/交互 | P3/P4 | L0-L4 架构、更新管家、审计/gate、画像 contract、lineage | 最后阶段统一重设计 |

## Frontend 边界

| 前端允许 | 前端禁止 |
|---|---|
| 展示后端返回的画像组件、证据状态、血缘链接、新鲜度 | 自己推断画像状态或直接查 raw/mart 表 |
| 对 `unknown/proxy/research` 做清晰标识 | 把 stale/proxy 指标显示为生产事实 |
| 提供股票档案页内分区: 总览、机构、主力/CYQ、数据血缘 | 把画像当 promotion 证据或选股排序真相源 |

## 架构后回归主线

架构 gate 通过后才恢复业务主线。恢复顺序如下:

| 顺序 | 优先级 | 主线任务 | 前置 gate |
|---:|---|---|---|
| 1 | P1 | BestChoice 公式接入与冻结证据复核 | BestChoice artifact freeze/hash/lineage, namespaced challenger import |
| 2 | P1 | 300616 原始公式复现 | universe/PIT 清洁, god-view 与 PIT 去泄漏分离 |
| 3 | P1/P2 | 300616 衍生公式与参数空间 | `plan_validator` 8 项 PASS, search space 非空 |
| 4 | P1/P2 | 主项目量化回测 / paper_sim | `backtest_preflight` 8 项 PASS, 成本/涨跌停/排除股票规则有效 |
| 5 | P2 | 候选池与持仓监控 | paper_sim/Phase4/PBO/DSR/forward 证据足够, 未测指标仍 `unknown` |
| 6 | P2/P3 | 股票画像和档案 API | profile contract + lineage/freshness gate |
| 7 | P3/P4 | 全局前端 UI/交互重设计 | 业务 API、gate 状态、lineage contract 稳定 |

这意味着“架构完成”不是直接上前端, 而是恢复可验证主线: 公式 -> 回测 -> 候选/监控 -> 画像/API -> 前端。

## 最终前端 UI / 交互重设计

前端最终应成为“实盘候选工作台”, 而不是散落功能页。设计顺序必须跟项目架构一致:

| UI 区域 | 对应架构/流程 | 主要交互 |
|---|---|---|
| Control Room | updater 管家、DAG、锁、停止、状态 | 今日流程、失败队列、重跑/停止、StepResult、质量 gate 总览 |
| Data Health | L0 数据真相源、数据需求契约、data_audit | 表新鲜度、缺源需求、source fallback、`unknown/proxy/stale` |
| Stock Dossier | 股票画像、机构画像、主力/CYQ、lineage | 股票档案、证据状态、画像组件、血缘追溯 |
| Research / Backtest | backtest_preflight、plan_validator、paper_sim | 只能在 gate PASS 后启动研究或回测; 未测指标显示 `unknown` |
| Portfolio / Candidates | L3 策略执行、候选池、paper sim | 候选解释、持仓监控、风险/成本/涨跌停约束 |
| Lineage / Evidence | trace_lineage、mart_data_lineage、audit report | 每个 KPI/画像/候选能打开证据抽屉 |

设计原则:

| 原则 | 说明 |
|---|---|
| 证据优先 | KPI、画像、候选、按钮都显示 gate/lineage/freshness 状态 |
| 流程优先 | UI 按“更新 -> 审计 -> 研究 -> 候选 -> 监控”组织, 不按脚本清单堆页面 |
| 明确阻断 | FAIL gate 的动作按钮禁用或要求明确修复路径, 不给误操作入口 |
| 不隐藏 unknown | 未测、代理、stale、warn-only 都要显式显示, 不能用空白或漂亮分数掩盖 |
| 前端不造事实 | 前端只消费后端 read API 和 contract, 不直接拼 raw/mart 表 |

## Gate

| 场景 | 必须通过 |
|---|---|
| 新增画像数据源 | data need coverage audit + source freshness/PIT check |
| 新增画像 mart | lineage registry/trace row + writer/reader/freshness 字段 |
| 新增股票档案 API | contract test + `unknown/proxy` regression |
| 改前端股票档案 | API contract test + browser/screenshot 验证 |
| 全局前端重设计 | IA/交互方案审查 + contract tests + Browser 截图验收 + 关键流程 smoke |
| 涉及 `.py` | CodeGraph + complexity optimizer 成对执行 |
