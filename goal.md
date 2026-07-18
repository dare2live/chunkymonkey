# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-18
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

停止继续对旧架构做局部补洞，按 `docs/MASTER_TOPLEVEL_DESIGN.md` 建立可组合、可替换、可审计的数据与策略系统。

Phase 1.1 已从“补齐 margin v2 历史”改为“恢复与交易日历同级的 eligible-universe 硬门”。
只读复核证明 v2 把 Tushare 的 transport shape（SSE/SZSE/BSE）误升为项目业务范围，现有
universe checker 与 Moth assertion 均是假绿；因此停止 `20260709/BSE` 裁决、重观察和后续回放。
旧 v2 batch/landing/canonical 保持不可变审计证据，但不得提升 full-coverage generation、切消费者
或冒充项目 universe 数据。下一既定动作是先闭合 typed population scope、统一 policy snapshot、
真实 red gate，再以 margin/market-pulse 为首个迁移切片。

Tier0 未闭合前，不启动公式寻优、付费计算、生产候选或自动跑批。

## 产品层级（已裁决）

| 层 | 目的 | 首个正式输出 |
|---|---|---|
| Tier 0A 市场数据 | 日历、身份、名义 K 线、公司行动、复权与供应商事实 | accepted canonical partition |
| Tier 0B 分类 | 版本化行业树、概念标签、成员快照和证据化 crosswalk | taxonomy node + membership |
| Tier 1 股票状态 | 描述当前阶段、形态和事件，不预测未来 | stock state + pattern event |
| Tier 2 市场感知 | 描述活跃度、不平衡代理、广度和价格响应 | market context snapshot |
| Tier 3 研究/策略 | 裸 K → 状态 → 市场 → 机构/公式逐层消融 | experiment verdict + strategy spec |
| Tier 4 决策/产品 | 只消费已发布策略，生成候选和纸面成交证据 | strategy release + decision batch |

依赖只能向下。Ops/Governance 观察全部层级，但不拥有业务事实。

## 架构硬决定

1. “积木”是 `module + data + config + contract + evidence`，不是目录加 YAML。
2. landing 保留已请求的供应商原始响应；universe/business filter 在 canonical/serve 执行并记录 reason。
3. 交易日历管“何时”，eligible universe 管“谁/哪个市场”；两者是同级、不可绕过的发布前置门。
4. 每个正式数据集必须声明 `raw_evidence`、`external_aggregate` 或 `project_universe_pit` population scope，
   并携带同一 immutable universe policy id/version/hash；缺失即 fail closed。
5. 外部交易所汇总只能表达供应商/交易所定义的总体，不能冒充项目股票池；项目聚合必须由可逐证券
   执行 PIT 过滤的明细生成。后来退市不得反向删除其当时合格的历史，避免生存者偏差。
6. 名义 OHLCV 是成交真相；qfq 是带方法/as-of/lineage 的派生分析视图。
7. 分类统一契约、不统一值域：申万、东财行业、东财概念保持 namespace。
8. “资金去哪”只表达活动度、方向性不平衡代理、参与广度和价格响应，不宣称资金守恒流转。
9. 一数据集一 writer；配置只保存稳定政策；运行状态、成员事实和 code topology 不进 active YAML。
10. 采用 strangler 迁移；数据更新保持 `manual_only`，不恢复 cron/launchd/隐藏触发。

## Live 证据与已推翻前提

- `universe_rules.yaml` 白名单仅 `60/00/30/68`，排除 BSE/新老三板、ST 与不再合格证券；
- 47 个同步域中 30 个声明 `universe_filter`，仅 6 个 `by_ts_code` 请求前走较完整当前 universe；
  其余 24 个只做前缀过滤，不执行 ST/退市 PIT；formal `margin` 反而显式要求 BSE；
- `check_universe_filter.py --all` 对 `*ST` 坏例仍放行并报告 `CLEAN (1103 files)`；Moth 只 grep
  gate symbol 是否存在，没有证明 registry、contract、writer 或 consumer 真消费它；
- live `raw_tushare_daily` 最新日有 208 只 PIT-ST；市场脉搏 855 日中 854 日的涨跌广度会因剔除
  ST 改变，龙虎榜最新 74 只中有 7 只 ST；SW/DC 成员与下钻也存在 BSE/ST 泄漏；
- legacy margin 有 SSE/SZSE 各 1827 日、BSE 831 日；formal v2 accepted=1823 日、canonical=4473 行，
  含 BSE 827 行。market pulse 830 日计入 BSE，4 日连日增方向被改变；
- `20260709` 仍为未发布 LANDED，唯一冲突是 BSE `rqmcl NULL != 0`；没有 apply、re-fetch、legacy DML
  或 consumer cutover。该 observation 现判为 out-of-scope，不再是需闭合的项目数据缺陷；
- `margin_detail` 是逐证券原料，现有坏前缀和 PIT-ST 交集为 0，但截至 `20260714` 且 SZ 分片更旧；
  它可生成新项目口径，不能冒充交易所官方汇总，也尚未具备正式 accepted history。

## 执行计划

### Phase 0 / Phase 1 — 控制面与 accepted-partition 原语

已完成的 contract、atomic acceptance/Ops、manual-only 入口、canary 与固定查询证据保留在 ledger；
它们证明机制可复用，不证明错误 population scope 的 v2 可继续发布。

### Phase 1.1A — universe contract 与真门（当前）

- [x] 暂停 BSE 观测裁决、重观察与 provider 写入，删除未提交错误支线；
- [x] 从 `universe_rules.yaml` 派生 factory-owned typed immutable policy snapshot；正式 eligibility 固定为
  `traded_on_observation_date`，90 日窗口只留给 legacy 当前枚举；
- [x] DatasetContract 绑定 typed population scope，transport completeness 与 publication eligibility 分离；
- [ ] 静态 scope 已能在 calendar/writer/auth/adapter/DB 前阻断；exact-date resolver 的信任契约已裁决，
  但对抗审查否决了未绑定 availability/completeness 的初稿并已撤下。三个 accepted truth source、trusted
  loader、resolver 与正式 writer/consumer 均待接通；
- [x] verifier 已删除 shape-mutation 假坏例并区分 worktree/index，binder/resolver 对抗测试已变红；
  Moth、doctor、safe-commit/CI 已接 static gate，doctor 将 live NOT_EVALUATED 独立判为 FAIL。

### 2026-07-18 系统升级检查点（PARTIAL）

- legacy margin history writer/runtime/CLI 旁路已物删；`margin` 前台入口在任何 provider/DB 副作用前以
  `execution_blocked / scope_blocked` 退出，残留 acceptance mechanic 也按 live DB identity 物理拒写；
  旧 accepted generation 只保留审计读证据；
- resolver 语义已固定为 accepted calendar + exact-date nominal Kline + same-day ST；未能证明 availability、
  completeness 与 calendar generation 的初稿未进入检查点，避免把自洽 hash 冒充 accepted truth；
- 当前 static population contract PASS 只说明 1 个 formal external aggregate contract 合法，明确输出
  `live_readiness=NOT_EVALUATED`；Tier0 仍 BLOCKED，升级后从本节继续，不得抓数或切消费者。

### Phase 1.1B — margin / market-pulse 首个迁移

- [ ] 冻结 v2 为错误 scope 的不可变审计 generation；语义变化不得继承其 predecessor parity；
- [ ] 分立 `external venue-reported margin aggregate` 与 `project-universe margin aggregate`，确定实际消费者；
- [ ] 若项目指标保留，从完整 `margin_detail` accepted canonical + PIT universe 派生，记录过滤原因、
  input snapshot、available_at、method/unit/coverage/config hash；
- [ ] breadth、龙虎榜、SW/DC 聚合与 drill 统一消费同一 PIT universe；旧 pulse 先标不可信，再 shadow
  重建并逐日对账，API/UI 只在新 generation 验收后切换。

### 后续

按 consumer 风险顺序迁移其余 legacy 域，再进入 Tier0 K 线/分类 → stock state → market context →
主升浪 B0/B1/B2 → 机构跟随和公式 feature package → strategy release/decision/paper/product。

## 当前 blocker / 禁止误报

- 当前 margin v2 与 market pulse population scope 错误，禁止 provider 调用、live DB 写、consumer cutover；
- raw/landing 可保留排除对象作为证据，不能据此声称正式输出已通过 eligible-universe 门；
- 交易所汇总无法剔除 ST/退市/ETF，改成仅 SSE/SZSE 仍不等于项目股票池；
- 正式日级股票池按 t 日实际名义 K 线 + t 日 ST 处理，禁止用今天状态清洗全史；退市整理期仍有成交
  但业务要求排除时，必须新增 temporal status source，不能把 exact-Kline 口径冒充该语义；
- 现有 accepted=1823 日只证明 frozen v2 内部自洽，不证明业务范围正确；
- 全局 SLA/continuity 仍有既有告警，单域修复不能洗绿全链；当前无可信 KPI、发布策略或候选；
- verifier 必须证明坏例会红且退出码传播；函数存在、WARN、fixture 自洽、当前绿都不是交付证据。
