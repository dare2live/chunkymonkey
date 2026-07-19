# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-19
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

按 MASTER 建立可审计沪深判断链。当前开工 **Phase A（Tier0 硬门）**：A1 contract/attestation → A2 calendar accepted → A3 K/ST resolver → A4 landing 纯度 → A5 adapter 边界。

已拍板：多源=契约可换 adapter；首策略包=`institution_follow`；边做边测（坏例先红→绿→窄回归）。Tier0 未闭合前禁止寻优、生产候选、cutover、自动跑批。

## 产品层级（已裁决）

| 层 | 目的 | 首个正式输出 |
|---|---|---|
| Tier 0A 市场数据 | 日历、身份、名义 K、公司行动、复权 | accepted canonical partition |
| Tier 0B 分类 | 版本化树/概念/成员/crosswalk | taxonomy node + membership |
| Tier 1 股票状态 | 阶段/形态/事件，不预测 | stock state + pattern event |
| Tier 2 市场感知 | 活跃度/不平衡代理/广度/价格响应 | market context snapshot |
| Tier 3 研究/策略 | B0→B5 消融 | experiment verdict + strategy spec |
| Tier 4 决策/产品 | 只消费已发布策略 | strategy release + decision batch |

依赖只向下。Ops 观察但不拥有业务事实。

## 架构硬决定

1. 积木=`module+data+config+contract+evidence`。
2. landing 保留供应商响应；universe 过滤在 canonical/serve 并记 reason。
3. 日历与 eligible universe 同级硬门。
4. 正式集声明 `raw_evidence`/`external_aggregate`/`project_universe_pit` + policy hash。
5. 外部汇总≠项目池；PIT 过滤明细；后来退市不改写当时合格史。
6. 名义 OHLCV=成交真相；qfq=带血缘分析视图。
7. 分类统一契约、不统一值域。
8. “资金”=活动度/代理/广度/响应，非守恒。
9. 一数据集一 writer；YAML 只存政策。
10. strangler + `manual_only`。
11. Provider=adapter；真相在 accepted/canonical。
12. 切片红→绿；静态 PASS≠`live_readiness`。

## Live 证据与已推翻前提

- 白名单仅 `60/00/30/68`；多数域仅前缀过滤；margin v2/pulse 含 BSE 错误 scope。
- universe/Moth 曾假绿；live ST 污染 breadth/龙虎榜/SW/DC。
- margin accepted=1823 只证冻结自洽；`20260709/BSE` 出 scope。细节见 ledger。

## 执行计划（A→H）

控制面原语/margin 冻结/all-due 前阻断/doctor `NOT_EVALUATED` FAIL/calendar 隔离原型：见 ledger。≠业务就绪。

- **A（当前）** A1/A2 **complete**；A3 **PARTIAL**（`observation_population.resolve_traded_on_observation_date` + trusted loaders；policy v3 对齐 calendar dataset id；`live_readiness` 经 loader 评估非写死；K/ST accepted writer 仍缺 → residual `NOT_EVALUATED/BLOCKED`）。下一步 **A4** landing 纯度；A5 adapter 三界。出口：对抗测绿且 `live_readiness` 可评估；禁抓数/切消费者。
- **B** external vs project-universe；pulse 脱离错误 raw；shadow 后切读面。
- **C** Tier1/2 正式 lineage（definition/config/snapshot/universe/available_at）。
- **D** `DatasetSnapshot`→`ExperimentVerdict` 最小链 + PIT 截断。
- **E** `institution_follow_v1`（首包；B0→B4；跟随收益独立）。
- **F** main_rally B0–B2。**G** 公式+BestChoice。**H** Release/名义价纸面/产品。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

margin/pulse scope 错：禁 provider/live 写/cutover。交易所汇总≠沪深池。accepted=1823≠业务正确。continuity 告警未清除。函数存在/WARN/fixture 绿≠交付。Tier0=`BLOCKED`/`NOT_EVALUATED`。
