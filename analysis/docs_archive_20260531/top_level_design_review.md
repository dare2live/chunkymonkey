# 顶层设计审查 — 流程 / 功能 / 模块 / 数据表 / 配置文件

Date: 2026-05-27
Status: current
Authority: subordinate to `docs/PROJECT_CONSTITUTION.md`

本文件不是新的项目宪法, 而是把宪法、300616 哨兵案例和当前架构计划转换成
可执行的顶层设计审查表。用途是防止“模块 + 数据表 + 配置文件”变成口号,
并在新增流程、功能、模块、表或配置前先证明它值得存在。

## 当前结论

| 项 | 结论 | 说明 |
|---|---|---|
| 顶层方向 | APPROVE_WITH_NOTES | “流程管控 + 模块执行 + 表承载事实/证据 + 配置承载规则参数”符合本项目第一性原理 |
| 主要风险 | FAIL unless gated | 若没有 owner、truth source、schema 和 gate, 文档/配置/表会继续膨胀并误导实盘判断 |
| 执行口径 | 架构先于业务 | 300616 五公式、前端公式视图、GCP/Optuna 仍暂停到架构 gate 通过 |

## 审计状态边界

本文件完成的是**顶层设计方案本身的审计**: 审查“流程 / 功能 / 模块 / 数据表 /
配置文件”这种管理方式是否合理, 是否符合第一性原理和奥卡姆剃刀, 以及它需要
哪些 blocking gate 才能防止失效。

它不是“架构重构已完成”的验收证书。当前结论仍是 `APPROVE_WITH_NOTES`, 原因是:

| 维度 | 状态 | 含义 |
|---|---|---|
| 设计合理性 | PASS_WITH_NOTES | 顶层方法合理, 但必须靠 gate 和唯一 owner 约束 |
| 执行闭环 | IN_PROGRESS | updater DAG、数据 freshness、PIT recommendation 和部分 complexity 仍未闭环 |
| 生产可用性 | NOT_APPROVED | 真实端到端审计仍有 freshness FAIL / PIT WARN, 不能恢复业务主线或前端展示结论 |

因此后续每个架构优化切片必须同时满足两件事:

1. 能说明它让顶层设计更真: 更少平行账本、更清楚 owner、更接近唯一 truth source。
2. 能通过相应代码/数据 gate: CodeGraph + complexity、universe lint、targeted tests、必要时
   data_audit/backtest_preflight/plan_validator。

## 二阶审查: 管理方式本身

本节审查的不是某个模块, 而是“用流程 / 功能 / 模块 / 数据表 / 配置文件管理项目”
这套顶层设计是否合理。结论: **合理, 但必须被 gate 约束; 不能变成五套平行账本**。

| 对象 | 第一性原理 | 奥卡姆判断 | 有效性结论 | 主要失效模式 |
|---|---|---|---|---|
| 流程 | 实盘系统需要可停、可追、可复现的执行链 | 只保留能阻断错误或保留证据的流程 | PASS | 流程只写文档, 不接 gate |
| 功能 | 功能必须服务可交易候选, 不是展示漂亮结果 | 无消费者、无验收的功能不做 | PASS_WITH_NOTES | 先做前端/公式, 后补真相源 |
| 模块 | 领域逻辑需要唯一 owner 和稳定 API | 复用现有 service, 不能绕出平行入口 | PASS | router/updater 重新吞业务逻辑 |
| 数据表 | 表只能承载 truth/cache/evidence/artifact/log 之一 | 能用真相源实时判断就不新增派生真相表 | PASS_WITH_NOTES | cache 表被当 universe 或策略事实 |
| 配置文件 | 规则和参数必须可审计、可调、可回滚 | 同类规则只放一个 YAML, 不把流程写成 DSL | PASS_WITH_NOTES | 配置膨胀成隐藏代码或多处重复规则 |

因此顶层方案的使用规则是: **先问是否需要存在, 再问归谁负责, 最后才问如何实现**。
任何新增对象填不完下面的变更记录模板时, 默认结论是 BLOCK。

## 第一性原理审查

真金白银 A 股量化系统的底层事实不是“代码能跑”, 而是:

| 不可压缩事实 | 架构含义 | 当前真相源 / gate |
|---|---|---|
| 交易判断必须基于真实可交易历史 | K 线是交易真相源, 不让快照表决定 universe | `services.universe.get_active_universe()`, `check_universe_filter.py` |
| 日期判断必须可复现 | 交易日历是日期真相源, 不用 weekday/硬编码日期 | calendar service / updater calendar gate |
| 交易所规则会变且按板块不同 | 涨跌停/ST/退市规则放配置, 运行时按股票取 | `backend/config/universe_rules.yaml`, `backtest_preflight` |
| 时间 t 决策只能用 t 之前信息 | 公式/信号/回测入口必须 PIT 审查 | `backtest_preflight`, leakage scan, formula PIT spotcheck |
| 昂贵执行必须先证明有产出 | Optuna/GCP 前验证 search space、样本覆盖、输出消费者 | `plan_validator`, GCP controlled-use latch |
| session 会中断, 人会忘 | 当前状态必须写入少数权威账本 | `goal.md`, `docs/implementation_plan.md`, `SESSION_HANDOFF.md` |

因此本项目的顶层设计必须能回答 5 个问题:

| 问题 | 必须有的答案 |
|---|---|
| 谁决定事实? | K 线、交易日历、配置文件或明确的数据源, 不能是缓存表 |
| 谁执行业务? | 领域模块/service, 不是 API router 或 updater 管家 |
| 谁编排流程? | updater / run script / DAG, 只调度和验收, 不吞业务逻辑 |
| 谁记录证据? | audit report、lineage、状态表或稳定 analysis 文档 |
| 谁阻断错误? | blocking gate, 不是口头提醒或 warn-only 结果 |

## 奥卡姆剃刀审查

新增或保留任何流程、功能、模块、表、配置前, 先过以下最简性问题:

| 审查问题 | PASS | FAIL |
|---|---|---|
| 能删掉吗? | 删除后仍能复现事实和证据, 则直接删除 | 只是“以后可能有用”, 或用注释/隐藏开关/改名留尸体伪删除 |
| 能复用现有入口吗? | 使用既有 service/config/gate | 新建平行入口绕过治理 |
| 是否只有一个 owner? | owner 和调用方明确 | 多个模块都能写同一事实 |
| 是否重复表达同一规则? | 一个规则只有一个配置/函数定义 | YAML、代码、SQL 各写一遍 |
| 配置是否还是数据? | YAML 只放规则、阈值、参数、开关 | YAML 变成隐藏业务流程/DSL |
| 表是否有身份? | truth/cache/evidence/artifact/log 分类明确 | 表既像缓存又像 universe 真相源 |

默认策略: **先删、再合并、再抽象、最后才新增**。复杂度只有在有证据证明简单方案不足时才被接受。
删除前必须用 CodeGraph (`query` / `context`) + `rg` + 相关测试/审计确认引用、
owner、证据链和替代路径；确认可删后必须真删除，不允许用注释、隐藏分支、
dead flag 或保留空壳来掩饰残留。

## 五类对象的管理边界

| 对象 | 责任 | 不负责 | 必须登记 / 验收 |
|---|---|---|---|
| 流程 | 编排顺序、依赖、停止/回滚、gate 汇总 | 领域计算细节、真相判断 | 入口、前置 gate、输出 artifact、失败语义 |
| 功能 | 用户/业务可感知能力 | 私自定义底层事实 | 目标、数据输入、消费方、验收测试 |
| 模块 | 单一领域逻辑和稳定 API | 跨层直接查表、绕过 service | 所属 L0-L4、owner、导入方向、相关测试 |
| 数据表 | truth/cache/evidence/artifact/log 之一 | 同时承担多个互相冲突角色 | writer、reader、PIT key、freshness、retention |
| 配置文件 | 规则、阈值、参数、资源策略 | 复杂业务流程和隐式代码 | schema、owner、默认值、校验脚本 |

### 数据需求契约

“模块 + 数据表 + 配置文件”还不够。数据源会增删改, 策略和分析用到的字段也会增删改,
所以中间必须有一层稳定契约: **数据需求先于数据源**。

| 层 | 责任 | 当前 owner |
|---|---|---|
| 数据需求契约 | 说明业务需要的字段、grain、PIT key、freshness、生产资格、consumer | `backend/config/tdx_data_need_coverage.yaml` |
| 数据源登记 | 说明 tdxhub/aif10/akshare/miaoxiang 能力、优先级、fallback、退役状态 | `backend/services/data_sources/clients_registry.py`, `backend/services/data_sources/data_routes.py` |
| 数据表资产 | 说明 writer、reader、日期列、freshness、deprecation、用途身份 | `dim_data_asset`, `mart_data_health`, `mart_data_deprecation_record` |
| 审计物化 | 把需求覆盖、source priority、重分配建议落表给 UI/管家/人工审查 | `backend/scripts/audit_tdx_data_need_coverage.py` |

新增数据需求时先改契约, 不先改业务代码。若 TDX 不覆盖, 结论不是“砍掉需求”,
而是标成 `tdx_coverage_level=none`、指定外部源探测和 gate；若没有通过 PIT/freshness/source
稳定性验证, 生产消费者必须输出 `unknown` 或使用已标明的 proxy, 不能把 stale raw 当证据。

### 硬编码治理

| 业务值类型 | 默认 owner | 可留在代码的例外 |
|---|---|---|
| 规则、阈值、策略参数、开关 | 配置文件 + loader 校验 | 测试夹具、数学常量 |
| 日期窗口、stock code 列表、source/path catalog、数据源优先级 | 数据表、稳定 artifact 或配置文件 | 单次脚本局部实现细节 |
| fallback 顺序、typed access、规则解释 | service module | 不暴露给业务决策的私有 helper |
| schema 名、enum、SQL DDL | 代码可接受 | 不得被当作业务策略 |

审查结论必须避免两种极端: 一是把交易规则藏在 Python 常量里, 二是为了一个不会复用的局部常量新建配置或表。默认先找已有 owner, 再考虑新增 owner。

### 数据表身份

| 身份 | 允许 | 禁止 |
|---|---|---|
| truth | 原始或权威事实, 可用于业务判断 | 由不可靠快照推断交易状态 |
| cache | 提速、名称映射、外部源镜像 | 反过来决定 universe / 交易可行性 |
| evidence | 审计结果、gate 报告、验证指标 | 被下游当实时事实使用 |
| artifact | 实验/模型/导出产物 | 覆盖旧证据且无 lineage |
| log | 运行状态、失败队列、watermark | 作为策略信号输入 |

`dim_active_a_stock` 当前身份只能是 `cache`: code -> name / schema / data-sync 枚举。任何 active universe 判断必须走 K 线真相源和 `get_active_universe()`。

## 顶层设计变更记录模板

每个非平凡架构变更在动手前应能填完下表。填不完时先补证据, 不先写代码。

| 字段 | 要求 |
|---|---|
| 变更对象 | 流程 / 功能 / 模块 / 数据表 / 配置文件 |
| 所属层级 | L0 / L1 / L2 / L3 / L4 |
| 第一性原理 | 它保护哪条不可压缩事实 |
| 奥卡姆结果 | 为什么不能删除、合并或复用现有对象 |
| truth source | 事实来源, 以及禁止使用的替代来源 |
| owner | 唯一写入方 / 维护方 |
| consumers | 读取方或调用方 |
| gate | 阻断式验证命令或测试 |
| 失败模式 | 防止 300616 暴露出的哪类系统性问题 |
| 回滚/删除 | 如何安全撤回 |
| 删除证据 | CodeGraph / `rg` / 测试或审计如何证明不是活跃路径 |

## Blocking Gate

| 场景 | 必须阻断 |
|---|---|
| 经验证可删除的代码只被注释、隐藏、改名或 dead flag 保留 | BLOCK |
| 新增表但未说明身份、writer、freshness、PIT key | BLOCK |
| 新增配置但没有 owner/schema/使用方 | BLOCK |
| 新增 updater 逻辑让管家直接做领域更新 | BLOCK |
| API router 直接承担业务计算或 truth 判断 | BLOCK |
| `dim_active_a_stock` 重新参与 universe/business filtering | BLOCK |
| 业务规则/阈值/source catalog 长期硬编码在 Python 且无 owner 说明 | BLOCK |
| 同一规则在 YAML、SQL、Python 多处重复表达 | BLOCK |
| 前端先于后端 contract/gate 展示画像、公式或回测结论 | BLOCK |
| Optuna/GCP/backtest 缺 `backtest_preflight` 或 `plan_validator` | BLOCK |
| 指标未测却用于 production/promotion claim | BLOCK |

## 对 updater 的应用

用户提出的“智能更新是管家, 数据模块自己更新自己, 管家监督流程和质量”通过本审查。

| 角色 | 通过原因 | 审查边界 |
|---|---|---|
| updater 管家 | 负责 DAG、锁、停止、状态、日志、质量 gate 汇总 | 不直接拉行情、写财务表、算画像、决定 universe |
| 数据/计算模块 | 拥有领域更新和领域内校验 | 必须返回可审计 `status/count/detail/error` |
| audit 模块 | 负责新鲜度、完整性、缺口、异常 | 不直接执行业务更新 |

这既符合第一性原理的责任分离, 也符合奥卡姆剃刀: 保留一个管家而不是 N 个互相竞争的调度入口。

## 对前端重设计的应用

全局前端 UI/交互重设计放在架构重构和主线验证之后。前端不是事实来源, 而是证据和流程的操作台。

| UI 层职责 | 通过原因 | 审查边界 |
|---|---|---|
| 展示 gate、lineage、freshness、`unknown/proxy/production` | 帮助用户判断证据能否用于实盘候选 | 不直接拼 raw/mart 表、不自造画像结论 |
| 编排用户工作流 | 按“更新 -> 审计 -> 研究 -> 候选 -> 监控”组织任务 | 不能绕过后端 blocking gate |
| 操作按钮 | 只调用后端受控 API, 显示前置条件和失败原因 | FAIL gate 时不得提供误导性一键推进 |

因此前端最终重设计的前置条件是: 后端 read API contract 稳定、关键 gate 有机器可读状态、画像/候选/KPI 有 lineage_ref, 以及 Browser 截图与关键流程 smoke 可验收。

## 验收命令

顶层设计审查本身不替代代码 gate。触发对应代码变更时仍需执行:

```bash
codegraph query "<symbol>"
codegraph context "<task>"
codegraph sync .
python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py \
  /Users/dp/Documents/M/stock/chunkymonkey/backend --format markdown
PYTHONPATH=backend python backend/scripts/check_universe_filter.py --all
git diff --check
```

涉及回测、Optuna、GCP、数据 sync 时, 还必须按宪法运行对应的 `backtest_preflight`、
`plan_validator`、`data_audit` 或 GCP controlled-use preflight。
