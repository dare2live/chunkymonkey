# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-20
> 手写：objective / 已裁决 / 禁令 / 下一步。状态投影见 `BOARD.md`（生成，勿手改）。
> 完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

**轨道 = Agent-OS 重设计（先于 A→H）**：Truth OS 机器门不动。
WP0–WP4 FIXED（板生成 / tiered commit / `chunkyctl agent-boot` / AGENTS 瘦身）。
**A→H 挂起于 `d8b69090`**。状态细节只读 `BOARD.md`；启动用 `scripts/chunkyctl agent-boot`。

## 下一步（agent-OS 残余检查单）

1. （可选）WP5 fixture pack stub；WP6 ceremony cutover 需 owner 明确批准范围
2. 核心 agent-OS 交付（boot/board/tiers/AGENTS）已闭合；owner 裁决后可恢复 A→H
3. 手动数据运维：`20260720` provider 有行后 sync+accept（非 A→H 刀）

## 禁令

- 新 A→H 刀；B-pit/C `cutover_allowed=true`；Optuna；E 松门；StrategyRelease
- margin thaw；mass backfill；plugin bus；第二 DB；agent 自降 commit tier

## 已裁决（稳定）

| 层 | 目的 | 首个正式输出 |
|---|---|---|
| Tier 0A 市场数据 | 日历、身份、名义 K、公司行动、复权 | accepted canonical partition |
| Tier 0B 分类 | 版本化树/概念/成员/crosswalk | taxonomy node + membership |
| Tier 1 股票状态 | 阶段/形态/事件，不预测 | stock state + pattern event |
| Tier 2 市场感知 | 活跃度/不平衡代理/广度/价格响应 | market context snapshot |
| Tier 3 研究/策略 | B0→B5 消融 | experiment verdict + strategy spec |
| Tier 4 决策/产品 | 只消费已发布策略 | strategy release + decision batch |

依赖只向下。Ops 观察但不拥有业务事实。多源=契约可换 adapter（目标态）；首策略包=`institution_follow`；边做边测。Tier0 未闭合前禁止寻优、生产候选、cutover、自动跑批。

架构硬决定摘要：积木=`module+data+config+contract+evidence`；landing 保留供应商响应；日历与 universe 同级硬门；名义 OHLCV=成交真相；一数据集一 writer；`manual_only`；静态 PASS≠`live_readiness`。完整条文见 `docs/MASTER_TOPLEVEL_DESIGN.md`。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

交易所汇总≠沪深池。accepted 行数≠业务正确。continuity 非 READY≠代码不可提交。E measured reject ≠ StrategyRelease。函数存在/WARN/fixture 绿≠交付。cutover yaml false≠可静默切读。板（BOARD.md）≠执法输入。
