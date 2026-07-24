# ChunkyMonkey Goal

> 状态：live controller board
> 手写：objective / 已裁决 / 禁令 / 下一步。状态投影见 `BOARD.md`（生成，勿手改；**非执法输入**）。
> 完成证据：`analysis/project_state_ledger.md`（关键词查）。交接：`analysis/account_switch_handoff_20260720.md`。
> **执行方案（仅两份；abolished 主方案/支线）**：底座 `analysis/FOUNDATION_EXECUTION_PLAN.md` · 策略 `analysis/STRATEGY_EXECUTION_PLAN.md`（RX 前 BLOCKED）。
> **清理台账**：`analysis/DOC_CLEANUP_20260723.md`。Owner 立法仍只认 `docs/README.md` 三份 contracts。
> **活契约引用（非第二 backlog）**：`analysis/foundation_phase_reeval_20260721.md` · `analysis/data_brick_architecture_20260721.md` · `analysis/db_layering_toplevel_design_20260721.md` · `analysis/architecture_fix_treadmill_first_principles_20260722.md` · `analysis/serve_derive_closed_loop_law_20260723.md` · `analysis/org_holding_incremental_loop_20260723.md` · `analysis/hs_a_whitelist_includes_st_20260722.md`。

## 当前 objective

**轨道 = foundation solidify CLOSED**（2026-07-21；母体 = transport strangler S1–S7 + brick L0–L3 + E0 + DB 分层；**phase_closure_ready=true**）。模块化 **S1–S6 FIXED**；**S7 near-FIXED**（**20 ssot + 3 retired** typed hard-stop 墙；B1+B2 done；禁假 COMPAT；`stk_factor_pro`+`express`/`fina_mainbz` sunset/DROP；**2026-07-24 `stk_holdernumber` RESTORE** `by_ann_date`+DataAccess+dossier assist）。**E0-HIST / F6 PASS**（holders≥120 trading-day overlap）。**FND-GATE PASS**（F1–F10 全 PASS；F8 §15-VERIFY **PASS**）。**§15 behavior PASS**（连续 3 刀 commits/knife=1.0 + pre-knife）。**B5** registry/qfq **FIXED**；Type-B enrichment **FIXED**；qfq incremental **FIXED**；breadth B-pit promote **FIXED**。A→H = **后置研究地图**；**E/F remeasure paused**（可 schedule，未开）。WP0–WP4 闭合；WP6 shadow 开放。**§15 knife-merge binding 不变**。

已落地硬事实（勿回滚；细节见 FOUNDATION §2 + git）：
- C + B-pit **`cutover_allowed=true`**（`b38e9ac5`）→ `ACCEPTED_CUTOVER` / `MART_CUTOVER`；dual-track residual **NONE**
- accepted daily **`20190102`→`20260721`**；ST **`20220104`→`20260721`**；form/qfq/pulse 跟 formal；工作台一键更新 + Cap E 分步节点 FIXED
- Phase D runtime FIXED；Phase F F0–F3 ladder measured **reject** / `claimable=false`（可 checkpoint；≠ Release）
- Delivery-OS：eng_gov **§15**（一刀=Rule10+safe_commit；异步 CI；L3 pre-knife；不放宽 PIT/≤40d）
- CX-1…CX-4 PASS；Cap A/B/D/E/F usable；margin v3 path + holders skip-land + qfq in-module compact FIXED

启动：`scripts/chunkyctl agent-boot`；状态：`BOARD.md`（投影）。

## 下一步

**执行权威（what next）** = 仅两份方案：
1. **数据底座** → `analysis/FOUNDATION_EXECUTION_PLAN.md`（§6 exit **MET**；**100% usable MET** = 无 class-A；根因 `analysis/foundation_residual_rootcause_20260723.md`；annotate/UNTRUSTED = class-B 诚实 OK）
2. **后续策略** → `analysis/STRATEGY_EXECUTION_PLAN.md`（**仍 BLOCKED** until 本文件显式 schedule RX — exit MET ≠ 自动开 RX）

**foundation-done 已闭合**（F1–F10 PASS；`phase_closure_ready=true`；CX-1…CX-4 PASS）。FND-GATE spec = `analysis/foundation_phase_reeval_20260721.md`。无「主方案 vs 支线」——残差一律进上述 backlog。

**已闭合（勿回滚）**：S1–S6 FIXED；S7 near-FIXED（禁假 COMPAT；无 owner 新 block 不开 S7 刀）；E0-HIST/F6 PASS；org **incremental-check-every-run**（mass/by-date invent banned）；B5 registry/qfq/Type-B enrichment **FIXED**；qfq incremental **FIXED**；breadth B-pit promote **FIXED**；Cap F dossier usable FIXED；margin 1a+1b+**F4 serve→accepted** FIXED（SSE+SZSE；rzrqye READY as external_aggregate on accepted days；缺日 UNTRUSTED；禁假 TRUSTED/project_universe）；holders skip-land FIXED；qfq in-module compact FIXED；Serve→derive 闭环 FIXED；跑步机 0–3 FIXED；§15-VERIFY PASS；**Continuity Knife4 FIXED**；**foundation §6 exit MET** + **100% usable MET**（无 class-A；annotate WARN = class-B；禁为清单洗绿）。

**近端 focus**：F4 serve→accepted **FIXED**；breadth B-pit promote **FIXED**（READY as project_universe_pit when MART_CUTOVER）；F7 Type-B enrichment **FIXED**；F8 qfq incremental **FIXED**。等 owner **显式 schedule RX** 才开 STRATEGY。**Optuna / Release 未开**。仍禁 S7 假 COMPAT / org invent / 松 holdout / Continuity 洗绿。


**护栏**：formal frontier 与 drain soft 窗分立叙述；PIT+≤40d；§15 不放宽；org 增量见 `org_holding_incremental_loop_20260723.md`；禁全宇宙扫股东公告（`shareholder_update_check_design_20260723.md`）；serve=沪深A 含 ST。

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

**Period-domain incremental（owner Q3 + hard lock；2026-07-23 纠偏；2026-07-24 bounded fill + ops drain）**：每次 `daily_update` / 显式 sync 对 org（及同类 period 域）**必须** check latest plannable vs local raw+accepted；**raw 缺 → 拉一期；raw 有 accepted 无 → accept from local-raw；都有 → skip + next-period unlock log**；**plannable 完整且中间季有洞 → 每 auto run 填最老缺季 N=1**（`fill_older_period` via `sync_period(..., allow_existing_refresh=False)` + `plan_partition_catchup` oldest_first）。**Ops/manual 可显式 loop 直到 `missing_older_count→0`（≤40/session）** — `backend/scripts/org_holding_period_drain.py`；**auto 仍 N=1/run**（`ORG_PERIOD_CATCHUP_MAX=1`）。**NEVER** 每次点击全市场单期 ~830k mass re-pull / unbounded page crawl refresh / by-date invent / pipeline `backfill()`。实现：`org_holding_period_gap_report` + `org_holding_period_catchup` + `sync_org_holding_incremental`；表面：`delta_manifest.acquire_summary.incremental` + due_plan period 行；mass ban 不变。

**Product 系统 + Agent-OS 演进裁决（owner，针对 Fable5 提案）**：后续演进 = **strangler + 聚焦**，非 greenfield 重写。仅三把杠杆：(1) 单一读 SSOT 经 resolver（禁旁路直读）；(2) 本地 L2/L3 pytest = CI test-list 唯一 SSOT；(3) god-seam strangler，按 blast radius 分步收编，不整体推倒。

手动 sync：`trigger_mode=manual` 不受 `same_day_at 18:00` 挡；自动更新与 consumer `available_at` 仍受 clock；交易日历对两者硬约束。见近端 focus（drain 流式 + probe-first FIXED）。

## 边做边测

坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier →（PIT/schema/writer）stale 审计 → `FIXED|PARTIAL|BLOCKED`。

## Blocker / 禁止误报

交易所汇总≠沪深池。accepted 行数≠业务正确。continuity 非 READY≠代码不可提交。E measured reject ≠ StrategyRelease。函数存在/WARN/fixture 绿≠交付。BOARD≠执法输入。
