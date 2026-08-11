# ChunkyMonkey Goal

> 状态：live controller board
> 手写：objective / 已裁决 / 禁令 / 下一步。**运行时状态现查 `scripts/chunkyctl status`**（零文件；非执法输入）。
> 完成证据：`analysis/project_state_ledger.md`（关键词查）。交接：`analysis/account_switch_handoff_20260720.md`。
> **执行方案（仅两份；abolished 主方案/支线）**：底座 `analysis/FOUNDATION_EXECUTION_PLAN.md` · 策略 `analysis/STRATEGY_EXECUTION_PLAN.md`（RX 前 BLOCKED）。
> **清理台账**：`analysis/DOC_CLEANUP_20260723.md`。Owner 立法仍只认 `docs/README.md` 三份 contracts。
> **活契约引用（非第二 backlog）**：`analysis/foundation_phase_reeval_20260721.md` · `analysis/data_brick_architecture_20260721.md` · `analysis/db_layering_toplevel_design_20260721.md` · `analysis/architecture_fix_treadmill_first_principles_20260722.md` · `analysis/serve_derive_closed_loop_law_20260723.md` · `analysis/org_holding_incremental_loop_20260723.md` · `analysis/hs_a_whitelist_includes_st_20260722.md`。

## 当前 objective

**轨道 = foundation solidify CLOSED**（2026-07-21；母体 = transport strangler S1–S7 + brick L0–L3 + E0 + DB 分层；**phase_closure_ready=true**）。模块化 **S1–S6 FIXED**；**S7 near-FIXED**（**20 ssot + 3 retired** typed hard-stop 墙；B1+B2 done；禁假 COMPAT；`stk_factor_pro`+`express`/`fina_mainbz` sunset/DROP；**2026-07-24 `stk_holdernumber` RESTORE** `by_ann_date`+DataAccess+dossier assist）。**E0-HIST / F6 PASS**（holders≥120 trading-day overlap）。**FND-GATE PASS**（F1–F10 全 PASS；F8 §15-VERIFY **PASS**）。**§15 behavior PASS**（连续 3 刀 commits/knife=1.0 + pre-knife）。**B5** registry/qfq **FIXED**；Type-B enrichment **FIXED**；qfq incremental **FIXED**；breadth B-pit promote **FIXED**。A→H = **后置研究地图**；**E/F remeasure paused**（可 schedule，未开）。WP0–WP4 闭合；**WP6 shadow 期已超 `engineering_governance.md` §13 上限**（起点 `be8efc6f`/2026-07-20 + 「10 session 或 14 天先到者」→ 2026-08-03 到期；2026-08-10 发现仍标「开放」），待 owner 裁决 cutover 或重置。**§15 knife-merge binding 不变**。

已落地硬事实（勿回滚；细节见 FOUNDATION §2 + git）：
- C + B-pit **`cutover_allowed=true`**（`b38e9ac5`）→ `ACCEPTED_CUTOVER` / `MART_CUTOVER`；dual-track residual **NONE**
- accepted daily / ST 已 cutover；起点 **`20190102`** / **`20220104`** 是契约常量。**当前 frontier 是运行时状态 — 现查 `scripts/chunkyctl status`，禁止在本文件写死**（2026-08-10 实测：手写的 `→20260721` 已落后实况 `→20260804` 两周，PROJECT_INDEX 另写 `→20260720`，两份手写文档互相矛盾）；form/qfq/pulse 跟 formal；工作台一键更新 + Cap E 分步节点 FIXED
- Phase D runtime FIXED；Phase F F0–F3 ladder measured **reject** / `claimable=false`（可 checkpoint；≠ Release）
- Delivery-OS：eng_gov **§15**（一刀=Rule10+safe_commit；异步 CI；L3 pre-knife；不放宽 PIT/≤40d）
- CX-1…CX-4 PASS；Cap A/B/D/E/F usable；margin v3 path + holders skip-land + qfq in-module compact FIXED

启动：`scripts/chunkyctl agent-boot`；运行时状态：`scripts/chunkyctl status`（现查，零文件）。

## 下一步

**执行权威（what next）** = 仅两份方案：
1. **数据底座** → `analysis/FOUNDATION_EXECUTION_PLAN.md`（§6 exit **MET**；**100% usable MET** = 无 class-A；根因 `analysis/foundation_residual_rootcause_20260723.md`；annotate/UNTRUSTED = class-B 诚实 OK）
2. **后续策略** → `analysis/STRATEGY_EXECUTION_PLAN.md`（**仍 BLOCKED** until 本文件显式 schedule RX — exit MET ≠ 自动开 RX）

**foundation-done 已闭合**（F1–F10 PASS；`phase_closure_ready=true`；CX-1…CX-4 PASS）。FND-GATE spec = `analysis/foundation_phase_reeval_20260721.md`。无「主方案 vs 支线」——残差一律进上述 backlog。

**已闭合（勿回滚）**：S1–S6 FIXED；S7 near-FIXED（禁假 COMPAT；无 owner 新 block 不开 S7 刀）；E0-HIST/F6 PASS；org **incremental-check-every-run**（mass/by-date invent banned）；B5 registry/qfq/Type-B enrichment **FIXED**；qfq incremental **FIXED**；breadth B-pit promote **FIXED**；Cap F dossier usable FIXED；margin 1a+1b+**F4 serve→accepted** FIXED（SSE+SZSE；rzrqye READY as external_aggregate on accepted days；缺日 UNTRUSTED；禁假 TRUSTED/project_universe）；holders skip-land FIXED；qfq in-module compact FIXED；Serve→derive 闭环 FIXED；跑步机 0–3 FIXED；§15-VERIFY PASS；**Continuity Knife4 FIXED**；**foundation §6 exit MET** + **100% usable MET**（无 class-A；annotate WARN = class-B；禁为清单洗绿）。

**近端 focus**：F4 serve→accepted **FIXED**；breadth B-pit promote **FIXED**（READY as project_universe_pit when MART_CUTOVER）；F7 Type-B enrichment **FIXED**；F8 qfq incremental **FIXED**。等 owner **显式 schedule RX** 才开 STRATEGY。**Optuna / Release 未开**。仍禁 S7 假 COMPAT / org invent / 松 holdout / Continuity 洗绿。


**护栏**：formal frontier 与 drain soft 窗分立叙述；PIT+≤40d；§15 不放宽；org 增量见 `org_holding_incremental_loop_20260723.md`；禁全宇宙扫股东公告（`shareholder_update_check_design_20260723.md`）；serve=沪深A 含 ST；**F9 residual_hygiene** 约束 Type-B publish / ann tip 滞后（超 SLA → 日更 degraded；≠ Continuity READY 化妆）；**org accepted pointer = full canonical partition**（同 available 多 report 禁末 batch 覆盖；F6 计 mismatch）。

## 治理体系重构（2026-08-10 立项；owner 认可第一性原理设计；未开工）

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

### 执行顺序（P1→P4.1→P2→P3；每步一刀一 safe_commit）

- **P1 门重新分布** — **FIXED**（2026-08-11）
  - 分组 owner = `backend/config/governance_gates.yaml`（与 tier 正交）：`diff_correctness` 10 阻断 / `system_health` 2 归运行时 / `scaffold` 7 warn-only。条文 `engineering_governance.md` §14.1
  - P1.2：`daily_update` store 阶段跑 `runtime_checks`（continuity · residual_hygiene · grain_uniqueness · **cutover_effective**；lineage_catalog 已于同日按独立审查证据撤出 —— 它在运行时报的是开发者状态非数据健康）；`system_health` 门未挂运行时 → `load_registry()` 直接抛错，不许「摘掉却无人接手」
  - P1.3：scaffold 门 `gate_fail` 走 warn 分支 + commit 尾部汇总 + `scripts/chunkyctl scaffold-fix`
  - 验收实测：commit 路径只剩 `diff_correctness`（`chunkyctl gates --check` PASS；门数随 P2.1 增至 20，三处一致由 `--check` 机械保证）；`daily_update --dry --skip-sync --date 20260811` 报出 `system_health cutover_effective FAIL`（b_pit attested 窗口 20260121–20260722 已过期，20260810 → BLOCKED/legacy_mart），typed `run_outcome=integrity_observe`
  - **交给 owner 的两条裁决**（检查报出来了，修不了）：① b_pit `cutover_allowed=true` 但窗口过期 → 重新 attest 或改回 `false`；② tier12 `consumer_cutover.cutover_allowed=true` 但最新交易日无 accepted partition（WARN —— 2026-08-11 独立审查后已改为**按原因分级**：只有「当天没 accepted」这种逐日回落算 WARN，`config_hash_mismatch` 等结构性原因判 FAIL，与 b_pit 同级）
- **P4.1 孤儿法条归位** — **FIXED**（2026-08-11）：`run_outcome` 四态法条落 **`MASTER` §5.4**（系统语义；确认没放 `engineering_governance`）。含四态判据表、rollup 顺序、exit 映射、四条不可放宽规则（归类不明≠等时钟 / 完整性≠时钟 / 下游只渲染不按 rc 反推 / 报告 JSON 是真相源 exit 是渲染器）、消费面清单。`backend/services/pipeline/run_outcome.py` docstring 的 Authority 从 `analysis/` 改指 MASTER，analysis 两份降为 Origin。文档↔enum 一致性由 moth `run-outcome-four-states-law` 锁死（任一侧增删态即红）。顺手修 `check_doc_governance` C7 假阳性：真实存在的**全路径**引用不再当成悬空命令名（该维度本就归 `check_doc_drift`，注释早写了实现漏了），加两个方向的回归测试
- **P2 状态零手写**（依赖 P1.2）
  - P2.1 **FIXED**（2026-08-11）：机器门 `check_doc_runtime_state` + `doc_runtime_state.yaml`（默认禁止紧凑日期 + 显式豁免须写明为何是常量；豁免失效自报），注册为第 20 道门（scaffold，warn-only）。抓到并修掉上一轮人工清理漏掉的：`PROJECT_INDEX` 的 `→20260720`（正是 2026-08-10 审计点名那一处）、`至 20260721`，以及 `23/46 ssot` vs 同文件 `20/46`（实跑真值 `ssot=20`）自相矛盾。**结论：靠人扫必漏，所以门比清理重要**
  - P2.2 **FIXED**（2026-08-11）：`scripts/chunkyctl status [--json]` = L2 单一现查入口（`services/project_status.py`；零文件、退出码恒 0 报事实不裁决）。给出 accepted 前沿 + **距最近已完成交易日的交易日滞后**、源水位、cutover 声明 vs 实际、门分布、告警 flag。此前**没有任何一条命令**能回答「前沿在哪」——真相散在两个库，而当时的 `docs/README` 把人指向一个明说自己没有该值的生成文件。坏指针一并修正
  - P2.3 **FIXED**（2026-08-11）：board 改**现查、零文件**。`agent_boot` 与 `chunkyctl status` 现调 `agent_board_projection.collect()`（实测 0.3s、不连库）；`BOARD.md` + `data/board/agent_context.json` 两个落盘产物与 `agent_board` 漂移门一并退役（门 20→19），旧的 `build_agent_board` 脚本改名为 `backend/scripts/agent_board_projection.py` 并去掉写盘/`--check`。新增诚实性护栏：投影声明 `inputs_present`，config 缺失时 boot 报 error 而不是渲染一份全缺省的空板
- **P3 历史归 git**（依赖 P2.2）
  - P3.1 commit message 模板固化 Q/Fix/Evidence/Residual（这才是 commit_msg 门的正确形态：查结构，不查关键词）
  - P3.2 `chunkyctl history --grep` 包装 git log，替代 ledger 检索
  - P3.3 ledger 冻结（read-only 不再追加）→ 退役
  - P3.4 `analysis/` 归零（现 65 份；卡点是几十处注释性溯源要一起改，批量替换已试过一次并回滚，需更好策略）
- **P4.2-4.4 文档按层归位**：`eng_gov §6/§11`、`AGENTS §4` 的系统约束移进 `MASTER`；三份 owner contract 重新划界（MASTER=系统法 / eng_gov=开发纪律 / strategy=研究发布）

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

**Formal daily/ST acquire（owner 2026-07-21；ST∈白名单澄清 2026-07-22）**：acquire = 全市场按 `trade_date`（`raw_evidence`），**禁止** exclude-then-fetch。**沪深A 白名单含 ST/*ST**；排除仅 B/BJ/三板/观察日无名义K。`stock_st` = accepted 日级 **membership 证据**（谁在何时是 ST），**不是** universe denylist，也不是 acquire 排除名单；同日 `zero_rows`=`pending_publish` 属发布窗，勿误读为「不要 ST」。BSE/三板 landing 可含，经 board 白名单过滤。Owner：`docs/MASTER_TOPLEVEL_DESIGN.md` §5.1 + `analysis/hs_a_whitelist_includes_st_20260722.md`。

**Gate pytest 分层（owner 2026-07-21 redesign #1 SHIPPED）**：`ci_pytest_surface.yaml` = `blocking_paths` + `nightly_paths` + optional；`run_ci_pytest.py --tier blocking|nightly|all`；L2/L3 safe_commit + CI = **`--tier blocking`**（非全量 985）；tier12 publish contract **promoted**；strategy-paused main_rally/institution_follow → **nightly**。详见 `analysis/gate_redesign_occams_20260721.md`。

**S7 sync_orphan standby（owner Q2）**：**NO** blanket pre-accept of 14 orphans（无 consumer / 无 contract / 大宗成本 / 假 readiness）。保持 ssot 墙；`legacy_raw_plane.yaml` **publication_watchlist** = 未来策略需要时的 publication 候选（非自动队列）；薄门：sync_orphan 进 DataAccess → `check_legacy_raw_plane` FAIL。**禁假 COMPAT**。

**Period-domain incremental（owner Q3 + hard lock；2026-07-23 纠偏；2026-07-24 bounded fill + ops drain）**：每次 `daily_update` / 显式 sync 对 org（及同类 period 域）**必须** check latest plannable vs local raw+accepted；**raw 缺 → 拉一期；raw 有 accepted 无 → accept from local-raw；都有 → skip + next-period unlock log**；**plannable 完整且中间季有洞 → 每 auto run 填最老缺季 N=1**（`fill_older_period` via `sync_period(..., allow_existing_refresh=False)` + `plan_partition_catchup` oldest_first）。**Ops/manual 可显式 loop 直到 `missing_older_count→0`（≤40/session）** — `backend/scripts/org_holding_period_drain.py`；**auto 仍 N=1/run**（`ORG_PERIOD_CATCHUP_MAX=1`）。**NEVER** 每次点击全市场单期 ~830k mass re-pull / unbounded page crawl refresh / by-date invent / pipeline `backfill()`。实现：`org_holding_period_gap_report` + `org_holding_period_catchup` + `sync_org_holding_incremental`；表面：`delta_manifest.acquire_summary.incremental` + due_plan period 行；mass ban 不变。**Paginated land 诚实**：East Money v1 等同 filter **100 页硬顶** — truncated land 不得当 complete skip；`pagination_integrity` + org sharded fetch；daily 下一步 = **有界 repair** 非跳过（实现 `888bfde75`；细节查 ledger「2026-07-24 org 分片抓取 + pagination integrity」条）。

**Product 系统 + Agent-OS 演进裁决（owner，针对 Fable5 提案）**：后续演进 = **strangler + 聚焦**，非 greenfield 重写。仅三把杠杆：(1) 单一读 SSOT 经 resolver（禁旁路直读）；(2) 本地 L2/L3 pytest = CI test-list 唯一 SSOT；(3) god-seam strangler，按 blast radius 分步收编，不整体推倒。

手动 sync：`trigger_mode=manual` 不受 `same_day_at 18:00` 挡；自动更新与 consumer `available_at` 仍受 clock；交易日历对两者硬约束。见近端 focus（drain 流式 + probe-first FIXED）。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

交易所汇总≠沪深池。accepted 行数≠业务正确。continuity 非 READY≠代码不可提交。E measured reject ≠ StrategyRelease。函数存在/WARN/fixture 绿≠交付。BOARD≠执法输入。
