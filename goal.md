# ChunkyMonkey Goal

> 状态：live controller board
> 手写：objective / 已裁决 / 禁令 / 下一步。**运行时状态现查 `scripts/chunkyctl status`**（零文件；非执法输入）。
> 完成证据：`scripts/chunkyctl history --grep <关键词>`（git log 即原件）；时期导航 `--eras`。交接：`chunkyctl history --grep "account-switch"`。
> **执行方案（仅两份；abolished 主方案/支线）**：底座 `goal.md「下一步」执行 backlog` · 策略 `goal.md「下一步」执行 backlog + strategy_validation_contract.md §3.2/§3.3`（RX 前 BLOCKED）。
> **清理台账**：`chunkyctl history --grep "文档收敛"`。Owner 立法仍只认 `docs/README.md` 三份 contracts。
> **活契约引用（非第二 backlog）**：`docs/MASTER_TOPLEVEL_DESIGN.md §11 (FND-GATE 十维)` · `docs/MASTER_TOPLEVEL_DESIGN.md §5.5 (变量积木分层)` · `docs/MASTER_TOPLEVEL_DESIGN.md §5.6 (物理分层裁决)` · `docs/engineering_governance.md §3.1 (何时不该开刀)` · `docs/MASTER_TOPLEVEL_DESIGN.md §5.8 (派生新鲜度闭环法)` · `docs/MASTER_TOPLEVEL_DESIGN.md §5.7 (披露域增量策略)` · `chunkyctl history --grep "ST 白名单"`。

## 当前 objective

**轨道 = foundation solidify CLOSED**（母体 = transport strangler S1–S7 + brick 分层 + E0 + DB 分层）。
逐项完成史查 `chunkyctl history --grep <关键词>`；FND-GATE 十维实时裁决跑
`check_foundation_done.py` —— **本节不再复述已闭合项**（复述必然滞后，本轮实证过）。

**当前 blocker**
- **数据线滞后**：accepted 日线落后若干交易日，滞后数现查 `scripts/chunkyctl status`。

已裁决硬事实（勿回滚）：
- accepted daily / ST 起点 **`20190102`** / **`20220104`** 是契约常量；**当前 frontier 是运行时状态，
  现查 `chunkyctl status`，禁止在本文件写死**（2026-08-10 两份手写文档互相矛盾且同时落后两周）
- Phase F ladder measured **reject** / `claimable=false`（可 checkpoint；**≠** Release）
- Delivery-OS：eng_gov §15（一刀 = Rule10 + safe_commit；异步 CI；L3 pre-knife；不放宽 PIT / ≤40d）
- A→H = 后置研究地图；**E/F remeasure paused**（须 owner 显式 schedule 才开；`foundation_done.yaml` 的 `strategy_pause.goal_must_contain` 按字面匹配本短语，改写措辞会让 F9 变红）

启动：`scripts/chunkyctl agent-boot`；运行时状态：`scripts/chunkyctl status`（现查，零文件）。

## 下一步

**执行 backlog 就在本节**（2026-08-11 起：原两份 `analysis/` 执行计划已并入这里 —— 规则段进
owner contract，进度段在此。**不再有第二个说「下一步」的地方**）。

*底座*（exit 已 MET；「100% usable」= 无 class-A，判据 eng_gov §9.1）
- **A2** `stk_holdernumber` `MAX(ann_date)` tip vs eligible —— 超 SLA 由 F9 residual_hygiene 判红
- **A3** Type-B fact publish 短滞后（moneyflow / limit / index / dc）—— 同跑 catchup
- **A4** org 中间历史季洞 —— **DEFER**：仅显式 backfill 刀，日常增量路径不变
- **A5** cyq 消费口径（历史段 FAIL）—— **DEFER**：消费前换算或弃用，非采集轴问题

*策略*（**仍 BLOCKED**；开门条件见 `strategy_validation_contract.md` §3.2 —— exit MET **不等于**
自动开 RX，须本文件显式排期）
- **S0** Strategy Lab 本地框架 —— **PARTIAL**，两份 live 输入不合格前只能 `claimable=false` smoke
- **S1→S2** RX-E / RX-F 同 protocol remeasure（诚实 reject 也算交付，**≠** Release）
- **S3** 公式挑战（仅 RX 后）· **S4** Release + 纸面执行 · **S5** Optuna（**另需**显式开 phase，双签字）
- 默认序 S1→S2→S3→S4，S5 最后

**护栏**（长期有效，非进度）：formal frontier 与 drain soft 窗分立叙述 · PIT + ≤40d ·
§15 不放宽 · serve = 沪深A 含 ST · 禁为清单洗绿（class-B 诚实状态**留着就是做对了**）。

## 治理体系重构（2026-08-10 立项；L1/L2/L3 层已落地，收尾中）

### 诊断

**系统约束与脚手架约束混在一起**。系统约束 = 系统运行时必须成立（PIT、日历、单 writer、`run_outcome` 语义、population scope）；脚手架约束 = 开发时人/agent 该怎么做（Rule 10、commit 说明、PROJECT_INDEX 同步、BOARD 重生成）。二者受众、变化频率、坏掉的后果都不同。

实证：`safe_commit` 19 门 = **8 系统 + 9 脚手架 + 2 混合**；脚手架门 3 次阻断系统修复提交。这解释了更早两层根因 —— 49 道门全空间维度零时间维度，是因为大部分是脚手架门（脚手架天然空间性）；唯二自述型门恰是唯二卡住诚实提交者的门。

### 三条原则

1. **能机器生成的绝不人写** —— 状态 100% 可生成；人手写状态，从写下那刻起就在烂。
2. **一个事实一个存放处，存在最靠近使用它的地方** —— 阈值被代码读→YAML；判据人和门共用→文档。**文档解释「为什么」，配置持有「是什么」。**
3. **门装在受害发生的时刻，不装在最方便检查的时刻。**

### 目标态：按变化频率分四层（不按主题分）

| 层 | 内容 | 变化频率 | 载体 |
|---|---|---|---|
| **L0 宪法** | 不变量 / 判据 / 边界 | 几月 | 极少数文档 |
| **L1 契约** | 阈值 / 窗口 / 白名单 / 注册表 | 几周 | YAML，被代码读 + schema 校验 |
| **L2 状态** | 前沿 / 覆盖 / 门的实际裁决 | 每次运行 | **命令现查，零文件，禁人写** |
| **L3 历史** | 做了什么 + 为什么 | 只追加 | **git commit，无独立文件** |

推论：**`analysis/` 终点是 0 份**（已实证全仓 `open()/read_text()` 指向 analysis 零命中，无一是运行时依赖）；**ledger 退役**（commit message 已含 Q/Fix/Evidence/Residual，ledger 是 git log 的人工副本，必然滞后 —— 实证断档 77 个 commit 而 git 一条没丢；检索用 `git log --grep`，永不断档）。

### 剩余计划（完成项查 `chunkyctl history --grep 治理`，本节只朝前看）

**已闭合**：P1 门重新分布 · P4.1 孤儿法条归位 · P2 状态零手写（含 board 现查）· P3.2/P3.3 历史归 git（ledger 退役）· P3.4 主体（`analysis/` 55→3）。四层里 L1 契约 / L2 状态 / L3 历史均已到位。

**A/B 已闭合**（治理收尾 + 门体系残留）：`analysis/` 归零并退役 · commit message 改结构自检 ·
三份 owner 重新划界（AGENTS §4 十五条里 12 条曾是 MASTER 的近逐字副本）· `check_no_emoji` 登记为第 20 门 ·
`test_safe_commit` 从长期 25 红修到 18 绿并转正 CI · B3 查出 moth 门的 `elif` 在 warn-only 下短路掉
`moth coupling`（warn-only 退化成 warn-nothing 的实例，已修 + 参数化守卫锁定）。

**C 已闭合**（数据线）：daily 与 stock_st 两个 `on_demand` 域的尾部断流已补齐（两域前沿现已追平交易日锚，
滞后 0；行数单调无截断，现查 `chunkyctl status`）· qfq/form 已跟进 · tier12 补发 5 期，`stock_row_count ==
universe_membership_size == 5205`（无静默填充）→ **tier12_consumer 转 PASS**（ACCEPTED_CUTOVER）·
org_holding 期轴非缺口已定论。

**D. 待 owner 裁决**
- **D4 tushare 授权 2026-08-12 15:43 到期（即明日）** —— 到期后全部 Tier0 采集停摆。只能由 owner 续费，
  agent 无法代劳。（日志此前照抄供应商 `week` 字段显示「remaining_weeks=4」，与真相差 4 周，已改为按 `limitDate` 现算）

**迁移原则**：不追求一步到位，但新增任何文档前先问「这是 L0/L1/L2/L3 哪一层」—— 是 L2 就不许写，是 L3 就写进 commit message。

## 禁令

- 静默 cutover / 无证据回翻 `cutover_allowed=false`；Optuna；E 松门；StrategyRelease
- margin thaw；mass backfill；plugin bus；第二 DB；agent 自降 commit tier
- **org_holding（及同类 by-period 域）在每次 manual/`daily_update` 上做全市场单期 ~830k mass re-pull / 无界翻页 refresh** — 只允许 check latest plannable vs local，**缺则拉一期，有则 skip**
- 随手重写 accepted canonical / 日历契约 / PIT-availability / `stage→validate→publish` / cutover 证据链；dual-write 迁移窗口；把「残破感」当 greenfield 重写许可证
- 后台 subagent 若再出现「仅 2 行 transcript、tool 无 result」：改用本会话直接做或 `shell` 子代理（见交接文档）
- S7 14 sync_orphan **blanket pre-accept as standby**；假 S7 COMPAT

## 已裁决（稳定）

| 层 | 目的 | 首个正式输出 |
|---|---|---|
| Tier 0A 市场数据 | 日历、身份、名义 K、公司行动、复权 | accepted canonical partition |
| Tier 0B 分类 | 版本化树/概念/成员/crosswalk | taxonomy node + membership |
| Tier 1 股票状态 | 阶段/形态/事件，不预测 | stock state + pattern event |
| Tier 2 市场感知 | 活跃度/不平衡代理/广度/价格响应 | market context snapshot |
| Tier 3 研究/策略 | B0→B5 消融 | experiment verdict + strategy spec |
| Tier 4 决策/产品 | 只消费已发布策略 | strategy release + decision batch |

依赖只向下。Ops 观察但不拥有业务事实。多源=契约可换 adapter（目标态）；首策略包=`institution_follow`；边做边测。Tier0 未闭合前禁止寻优、生产候选、自动跑批。

架构硬决定摘要：积木=`module+data+config+contract+evidence`；landing 保留供应商响应；日历与 universe 同级硬门；名义 OHLCV=成交真相；一数据集一 writer；`manual_only`；静态 PASS≠`live_readiness`。完整条文见 `docs/MASTER_TOPLEVEL_DESIGN.md`。

**Formal daily/ST acquire · 手动 sync 时钟 · 披露域增量** 三条裁决的正文已归位 `docs/MASTER_TOPLEVEL_DESIGN.md` §5.1/§5.7 —— 本文件不再留副本（副本必然与 owner 漂移）。要点仍成立：全市场按 `trade_date` 拉、禁 exclude-then-fetch、沪深A 含 ST/*ST、`stock_st` 是 membership 证据不是 denylist、org 按可披露期缺则拉一期有则 skip、禁 mass 与 by-date invent。

**Gate pytest 分层**：owner = `backend/config/ci_pytest_surface.yaml`（`blocking` / `nightly` / optional 三面 + 每条 optional 带 reason），L2/L3 与 CI 同跑 `--tier blocking`。分层理由与当初的取舍查 `chunkyctl history --grep "gate redesign"`。

**S7 sync_orphan standby（owner Q2）**：**NO** blanket pre-accept of 14 orphans（无 consumer / 无 contract / 大宗成本 / 假 readiness）。保持 ssot 墙；`legacy_raw_plane.yaml` **publication_watchlist** = 未来策略需要时的 publication 候选（非自动队列）；薄门：sync_orphan 进 DataAccess → `check_legacy_raw_plane` FAIL。**禁假 COMPAT**。

**Product 系统 + Agent-OS 演进裁决（owner，针对 Fable5 提案）**：后续演进 = **strangler + 聚焦**，非 greenfield 重写。仅三把杠杆：(1) 单一读 SSOT 经 resolver（禁旁路直读）；(2) 本地 L2/L3 pytest = CI test-list 唯一 SSOT；(3) god-seam strangler，按 blast radius 分步收编，不整体推倒。

## 禁止误报（交付判据）

切片循环见 `docs/engineering_governance.md` §5（坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier → stale 审计 → `FIXED|PARTIAL|BLOCKED`）。

交易所汇总 ≠ 沪深池。accepted 行数 ≠ 业务正确。continuity 非 READY ≠ 代码不可提交。measured reject ≠ StrategyRelease。函数存在 / WARN / fixture 绿 ≠ 交付。**投影 ≠ 执法输入**（board 已改现查、零文件）。
