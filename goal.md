# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-20
> 手写：objective / 已裁决 / 禁令 / 下一步。状态投影见 `BOARD.md`（生成，勿手改）。
> 完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

**轨道 = A→H 恢复（owner 裁决 2026-07-20：agent-OS 核心 WP0–WP4 闭合后恢复）**。
Agent-OS 影子期照常开放（WP6 仪式 flip 仍 owner-gated，与 A→H 无关）。
`20260720` daily+ST 已 sync+accept。启动：`scripts/chunkyctl agent-boot`；状态：`BOARD.md`。

## 下一步（A→H 残余刀）

1. C/B-pit cutover = **OWNER_OPTED_IN_CUTOVER_ON**（owner 显式裁决 2026-07-20 flip）。
   `tier12_publish.yaml#consumer_cutover.cutover_allowed=true` +
   `b_pit_mart_cutover.yaml#mart_cutover.cutover_allowed=true`。default yaml →
   resolver **ACCEPTED_CUTOVER**（C，accepted_partition 4989，claim_project_universe）
   / **MART_CUTOVER**（B-pit，project_universe_pit）。无 accept/窗外日仍 fail-closed
   →legacy（20260720 missing_accept、20251201 outside_window 实证）。证据：B-pit
   shadow MATCH 120/120；C full-universe accept 4989。认证：
   `data/lineage/c_b_pit_cutover_readiness.json`（L1 孪生 `data/board/`）。
   残余：accepted stock_states 为 scaffold（axis_pos/form_name null）— 富 form 仍在
   `fact_stock_form_daily`。
2. Agent-OS 影子期残余（非本轨道）：影子期满检查单、真实 L2 路径证据
   （T0 墙钟已实测入账 2026-07-20 见 ledger；编排/墙钟政策 = eng_gov §15：
    并行 subagents、异步 CI、Rule 10 按刀、L1 docs push 不起 CI）
3. 或 stop（D measured offline runtime-owned 路径已闭合；禁 Optuna / E 松门 / StrategyRelease）

## 禁令

- **静默** cutover（无 owner 显式 yaml flip 不切读）；B-pit/C 已由 owner 显式 opt-in
  2026-07-20 flip 为 true（非静默，合规）；Optuna；E 松门；StrategyRelease
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
