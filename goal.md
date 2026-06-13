# ChunkyMonkey Goal

> Current phase contract and implementation plan only. Keep this file compact:
> current objective, active priorities, live gates, and long-term direction.
> Completed work and detailed evidence belong in
> `analysis/project_state_ledger.md`; generated resume state belongs in
> `SESSION_HANDOFF.md`; pipeline artifact checkpoints belong in
> `analysis/workflow_checkpoint.md`.

## Document Contract

| Document | Owns | Startup use |
|---|---|---|
| `goal.md` | Current phase objective, priority board, implementation plan, long-term roadmap | Read first |
| `analysis/project_state_ledger.md` | Completed work, historical status, evidence notes formerly appended to `goal.md` | Query with `rg` or `tail`, not full startup read |
| `SESSION_HANDOFF.md` | Generated/manual runtime resume snapshot | Context-only; verify with live gates |
| `analysis/workflow_checkpoint.md` | Retired 2026-06-11 (inactive since 2026-06-05); physical removal tracked as P2 slice in `docs/implementation_plan.md` | Do not read for resume; historical evidence only |
| `docs/chunkyctl_session_quickstart.md` | Startup procedure and controller workflow | Startup contract |
| `docs/README.md` | Active docs map and ownership rules | Docs authority map |

Update rule: if an item is done, move the evidence to
`analysis/project_state_ledger.md` and keep only the resulting current decision
or blocker here. `goal.md` should stay under roughly 150 lines unless the active
phase genuinely needs a larger plan.

## North-Star KPI (唯一 owner: 本文件; 其他文档只指针引用)

| 指标 | 目标 | 口径 |
|---|---|---|
| 年化收益 | >= +30% | 含成本 OOS paper_sim, 2023-01-03 起 100 万初始 |
| 最大回撤 | >= -20% | 同上 |
| 超额 vs HS300 | > 0 | 真实 HS300 基准 (基准=0 的结果无效) |
| 月胜率 | >= 55% | walk-forward OOS 月度胜率分布, 不是均值 |

当前实测状态 (2026-06-11 体检): 无任何同时满足四项、含成本、gate 通过且数据新鲜的数字;
最近可信 artifact 均为 2026-05-23 前。正确答案是 `unknown`, 修复路径见
`docs/implementation_plan.md` Active Repair Plan。

## Strategy Portfolio (2026-06-11 策略锻造定稿)

Owner: `analysis/strategy_portfolio_20260611.md` (三套组合 + 12 周路线图 + 13 条全局纪律件;
三评委 judge panel 定稿: B 回调增强 8.0 主书 / D v7 排序 7.6 公共增强层 / C 题材扩散 6.0
降级数据采集)。**宪法 v2 已生效** (2026-06-11 用户原话确认 "宪法v2确认"): live 文件 =
`docs/PROJECT_CONSTITUTION.md` (13 条全带反例); v1 归档 `analysis/constitution_v1_retired_20260611.md`;
草案 `analysis/constitution_v2_draft_20260611.md` 已采纳为 live, 仅留作锻造证据。当前周 **W1** (完成项详 ledger)。
**轨道现状 (06-12 矩阵已被下方验证计划取代, 完成项详 ledger)**: A 存储 DONE (34G 拆分+PK 恢复,
旧库已删 06-13) / B 数据 tushare 22 域 ~1.1 亿行基本抓完 (gap: fina_mainbz 单期/watermark 失真) /
C 实验 三判决落定 (LHB GO / LF REJECT / S3 REJECT-泄漏, 详 ledger 与验证计划 L2) / D modal 已 deploy
+ smoke 隔离修 (CYQ 特征扩展待 S3 判正) / E 复权链转正。验证期 (W1-W12) 新策略 0 真金白银全 paper_sim 候选态。

## 数据底座基础设施 (2026-06-13 体检 + 修复进行中, 用户指令"先做好数据底座")

**决议: daily_update 保持手动** (本地未上云 + 定时不保证开机时刻在线; 成熟后再上云自动跑)。
**doctor verdict=FAIL, 但真相源完好**: K线 (price_kline_tdxhub/raw_tushare_daily 均 06-12) + DuckDB
schema (smartmoney 348表/839约束/172PK, 06-12 库分裂丢约束已恢复) 都健康。坏的是派生层。

**根因 (查实)**: 调度手动化 + 旧 `cron_daily.py` → `daily_update.sh` 迁移时, 一批派生 builder
被漏在新管道外 (孤儿管道)。两条日更管道割裂: daily_update 覆盖 live 链 (p0a panel/scores/syncs/
watermark), 但 fact_feature_panel + holder/gpcw/drift/prune/picture/financial_pit/shareholder 的
builder 全不在 daily_update (原属 cron_daily)。

**进度 (blocker 14→12)**:
- [完成] `fact_feature_panel` 重建 (build_feature_panel_duck.py --mode incremental): 4.19M 行, max_date
  06-04→**06-12**, 最新行 forward 列 NULL (PIT 边界净, stale 修了没引泄漏). → 连带清 mart_feature_panel_validation.
- [完成] `refresh_source_watermarks.py` 跑过 — 治 watermark 失真 (Step 2.97 孤儿化根因).
- [完成] daily_update.sh:18 stale 注释修 (指向已删 plist 改手动运行说明 + 孤儿管道警告).

**剩余 12 blocker + 下一批** (DuckDB 单写锁→必须串行):
- Category B (4 表, tdxhub holder sync, 需网络): ingest_holders_tdxhub.py [tier-2 miaoxiang aif10 兜底]
- Category D (7 表, 派生 builder 没跑, 读已新鲜 panel/kline): compute_feature_drift / prune_feature_panel_to_canonical_kline /
  validate_tdx_gpcw_auto_pit / profile_tdx_gpcw_fields / build_picture_daily / backfill_financial_pit / build_shareholder_plan_initial_event
- Category A (1 表, SLA 误配): dim_capital_behavior_latest (akshare 已退役却仍 48h blocking) → 改 seed_dim_data_asset.py SLA/移出 blocker, 不是重建.

**待用户定夺的结构决策**: 孤儿 builder 归属 — (a) 并入 daily_update.sh (手动一次跑全) 还是
(b) 保留 cron_daily.py 单独跑? 不擅自重构核心管道。reconcile 后这类断流不再复发。

## 多维策略立方体架构 (2026-06-13, 完整 owner=analysis/multidim_strategy_architecture_20260613.md)

用户多维想法 (数据源参数化 optuna / 分组适配非全市场统一 / 主辅策略 / 模块×数据×配置 ∩
规则×模型×策略) 的第一性原理顶层设计。**架构 = 策略立方体 (cell = Segment × Feature-set ×
Policy)**, 三轴真相源全有现成底座 (板块=universe_rules / 形态=technical_stage / 市值=circ_mv /
资金=moneyflow; 数据源族=feature_registry.groups; 策略=formula+model+optuna 退出层)。立法已落
(genesis/codex/3 死亡条款), **唯二新增件 = strategy_cube.yaml + services/strategy_cube/**, 其余全复用。

**Verdict: 立法 PROCEED + 实例化 BLOCK (§5 已实测坐实)**。最小可逆第一步 (板块维 read-only 检验,
`cube_board_axis_check_20260613.json`) 结论: 板块间最优 stop/target 参数无显著差异 (p>0.15) +
底层 per-stock 策略 OOS 本身为负 (mean oos_sharpe -0.35~-0.41)。**瓶颈在 base-edge 缺失, 不在
分组架构** —— 不建 cube 空壳, 算力重定向到下方系统性验证 L0-L4 + T 轨先找 base edge; edge 为正
再回来逐维解锁立方体。

## 系统性验证计划 (2026-06-13, 完整 owner=analysis/systematic_validation_plan_20260613.md)

核心: alpha/特征/live 可信度自底向上逐层机器可检, 任一层红则其上全部"受污染" (触发=S3 实证
fact_feature_panel 喂 follow_net_return 标签泄漏, 同源 mart_p0a_v4/v5 直喂 3 条 live 链)。工具
`services/leakage_detect.py` (4 阶段)。铁律详见 plan 文档 (判据冻结/异常高回 L0/真相源单一)。

- **L0 特征面板 PIT** (进行中): 消费者 feature_cols ∩ builder 标签集==∅ + lineage 净 + S3 重跑 AUC~0.50
- **L1 标签真实+时间安全** (待): 被 L0 排除 + embargo>=horizon + corr(特征,label)无>0.2
- **L2 实验复验** (LHB/LF/S3 已判): 冻结判据 + ablation 边际非负
- **L3 Live OOS 背书** (待, 审计 HIGH): 3 链训练面板 L0 PASS + selector 只读 oos_*
- **L4 含成本 paper_sim+forward** (待, 2026-05-16 all_kpi=False): KPI + forward 兑现落区间
- **T tushare 域 alpha 研究** (横切, 待): 22 域逐域 IC/ablation 增量 (用户问: 尚无系统研究)

**immediate_next**: L0 live 泄漏体检 — `run_daily_v7_inference.py:94` 手写 EXCLUDE 读 mart_p0a_v5
不引 builder 契约 = S3 同型漏排活体嫌疑; `leakage_probe --stage feature-consumer` 核 3 live 消费者。

## Current Phase

**Phase:** architecture/data governance foundation before deeper strategy work.

**Objective:** make the project controllable and auditable before expanding
data sources, provider compute, model search, or production promotion.

**Controller rule:** Codex owns direction, truth-source decisions, shared docs,
gates, staging, commits, and risky write windows. Side agents provide bounded
evidence or disjoint patches; their output is not a verdict.

**Commit ownership (2026-06-12 用户决议更新):** Codex review 强制已解除
(safe_commit Step 4.5 + check_codex_review hook 非阻塞化)。质量闸 = 单测 +
self-check 5 项 + 重大改动 (数据语义/策略/资金路径) 对抗复审 workflow。
其余 pre-commit hook (INDEX-sync / rule-compliance / no-emoji / commit-msg) 不变。

## Active Priority Board

| Priority | Workstream | Current state | Next action |
|---|---|---|---|
| P0 | Documentation control plane | Thin `goal.md` / ledger split committed in `8371e60c` | Keep current-state facts here; put completed evidence in `analysis/project_state_ledger.md` |
| P0 | Provider-neutral execution surface | Retired GCP execution surface removed and `experiment_jobs` contract committed in `8371e60c`; `modal` promoted to active 2026-06-11 (user decision "该用就用", $30/mo quota, ~/.modal.toml) | Both `local` and `modal` active; `modal` dispatch is gated by `dry_run=True` default + reviewed adapter + artifact-manifest contract (active = dispatchable, not auto-dispatched); never set `dry_run=False` without a reviewed plan + rollback |
| P0 | Dirty worktree governance | Clean after `3e9fafc8`; `worktree` gate reports `unknown=0` | Keep future commits slice-based; never `git add .` |
| P0 | Platform reliability convergence | Three-track audit done 2026-06-11 (`analysis/platform_top_level_design_20260611.md`); Platform Runtime Contract adopted into `docs/data_product_contract.md`. Verified gaps: 12+ WARN-and-continue swallow steps in `scripts/daily_update.sh`, 7 sources without watermark, failure_queue has writers but no drain consumer, 4 `except Exception: pass` in backfill scripts, frontend silent mock fallback plus 10+ hardcoded thresholds | **Done 2026-06-11 evening**: failure-level semantics live (29 swallow points → `step_degraded` + ALERT flag + end-of-chain notification) and calendar-gap drain live (`sync_runner --drain`, completeness-aware via min_rows, newest-first truncation, non-daily domains fall back to incremental run_domain; 16 unit tests + live lock-path probe exit 1). Registry-derived SLA audit also live: `update_watermark_sla.py` auto-generates `sync:*` probes from sync_registry.yaml with explicit NO_PROBE_RULE / NO_QUERY_MAPPING / DB_LOCKED_UNVERIFIED / NEVER_SYNCED states (no more silent-OK); first live dry-run caught real rot: `lhb_daily` stale 13d (aif10 path) and `xdxr` stale 17d (dividend season — affects adjustment correctness). Remaining: root-cause xdxr staleness, migrate legacy sources to tushare registry (top_list/top_inst/stk_surv/report_rc/ths_hot/moneyflow_hsgt all within 10000 points), dead-dates dampening, doctor ALERT-flag check; frontend rescue trio as separate batch |
| DONE | `need_027` exact order-flow | **All 5 post-probe gates PASS (2026-06-11)**, infra + runtime double-evidence in `analysis/need027_5gate_acceptance_20260611.md`: pit_key/freshness_sla/writer/watermark/failure_queue_resolution all satisfied; runtime: `raw_tushare_moneyflow` 4.22M rows, watermark `sync:moneyflow` at 20260611, failure_queue 29 backfill failures → resolved (full fail→queue→resolve loop proven). AkShare formally retired from need_027 (persistent `akshare_remote_disconnected`); TuShare is sole primary | Consume in alpha research (Task #4 / route-2 mid-layer): feature JOIN must obey pit_anchor t-1; moneyflow_ind_dc open failure auto-replays next daily_update drain |
| P1 | Data-source capability routing | Capability contracts committed in `3e9fafc8`; TuShare `moneyflow` adapter/gate wiring is local no-persist probe scope only; iFinD MCP is a research-only semantic/industry-chain snapshot candidate, not a行情 or exact-flow production replacement; 2026-06-11 live probes passed with user-provided token (initialize / tools-list / `sector_data` real data), token kept in local `.env` only, 3 servers registered local-scope for research sessions; concept add/drop event-stream design in `analysis/concept_event_chain_mining_20260611.md` | Keep new providers in capability-level probe mode before any DB writer or provider promotion; route iFinD MCP through daily PIT snapshot contracts for sector/theme/news/notice evidence; implement `fact_concept_event` after concept-domain backfill lands |
| P0 | DB retention/modularization | User directive 2026-06-12: execute the 34G reclamation now. Two dead hash-cache tables already archived+dropped (985MB parquet rollback). Split runbook = `analysis/db_split_runbook_20260612.md` | **DONE 2026-06-12 (两轮整改后)**: per-table rebuild + 回归修复后终态 36.1G→24G (实际回收 12.1G; 19.3G 是索引/PK 重放前的 stale 快照值). 回归: COPY FROM DATABASE 不搬约束/索引 (315→1), 首轮只补 4 表, 二轮按"凡 upsert 目标必有 PK"恒等式恢复 164 张 (冒烟 6/6 PASS, 0 失败); validation v2 六件套沉淀于 runbook. Old file `smartmoney_v1_retired_20260612.duckdb` — **delete manually after 2026-06-26** (frees 36G). Second pass = G4 panel convergence (~5-8G more) |
| P1 | Stage-opt supply | Freshness repair is complete through trusted K-line max `2026-06-04`: `fact_signal_context`, `fact_technical_trigger`, and `mart_macd_state_history` all max at `2026-06-04`; latest 5-K-line-day audit has `source_freshness_warnings=0`, default readiness `3010/23661=12.72%`, reversal-only readiness `1401/9218=15.2%`; YTD reversal-only readiness is nonzero (`21530` ready keys / `62.7%`) | Treat remaining stage-opt blocker as upstream signal-density / state-source design, not stale tables; do not tune formula thresholds again until a no-persist source/state POC proves useful PIT candidate supply |
| P2 | Microsoft RD-Agent(Q) research | Tracked as follow-up only; no production integration approved | Create a dated `analysis/rd_agent_q_research_*.md` deep-dive note mapping RD-Agent(Q)/Qlib reusable components, Co-STEER feedback, factor mining, report-to-code flow, experiment loop, agent role split, and experiment-manager contracts into borrow/reject/POC decisions under ChunkyMonkey gates |
| P2 | Data-health warning-only assets | No red or blocking-yellow in latest live doctor; 7 warning-quality tables remain yellow | Treat as owner-specific maintenance, not startup blockers |

## Latest Live Gate Snapshot

As of the latest checked state, with full doctor/stage-opt evidence and focused
`need_027` source-probe evidence from 2026-06-06:

| Gate | Current result | Meaning |
|---|---|---|
| `scripts/chunkyctl doctor --fast` | `WARN` | `need_027` remains blocked and `data_health` has 7 warning-only tables; execution surface, Moth, universe, worktree, and stage-opt freshness are clear |
| `scripts/chunkyctl worktree --format markdown` | Use live gate before staging | Latest pre-commit check had `unknown=0`; keep future commits slice-based |
| `moth snapshot --repo . --format markdown` | CodeGraph `PASS` | CodeGraph is up to date; complexity diff has `new_high_count=0` |
| `data_health_snapshot.py --dry-run` | `green=335 / yellow=7 / red=0 / blocking_yellow=0` | No startup data-health blocker; yellow assets are maintenance debt |
| `plan_storage_retention.py` | `candidate_count=0 / table_inventory_count=12 / policy_contract=PASS / compaction.recommended=false` | Contract is complete, but no production delete/VACUUM path is open |
| `audit_storage_retention_consumers.py` | `PASS / audited_tables=11 / runtime_ref_tables=11` | Cleanup candidates are protected by explicit consumer evidence instead of unknown placeholders |
| `audit_execution_surface.py --include-live-launchd` | `PASS / 0 findings` | Retired execution-surface references are not currently detected |
| `need_coverage` | `need_027` no-persist gate **PASS** (2026-06-11) | TuShare source group `ok` 3/3 (`selected_source=tushare`); AkShare group still `akshare_remote_disconnected=3`; `field_mapping`/`date_coverage` pass; remaining `required`: `pit_key`/`freshness_sla`/`writer`/`watermark`/`failure_queue_resolution` before production writes |
| `audit_stage_opt_candidate_supply.py` | Freshness clear / density low | Latest actual 5-K-line-day audit `2026-05-29..2026-06-04`: default `raw_signal_rows=54206`, `ready_keys=3010`, `ready_coverage_pct=12.72`, `source_freshness_warnings=0`; remaining blocker is `below_min_signals`, not source staleness |

Live gates override this section. Refresh before using the numbers as evidence.

## Implementation Plan

1. **Data-source contracts:** keep iFinD MCP / TuShare / tdxhub work in
   capability-scoped research/probe mode until PIT/freshness/watermark and
   no-persist probes pass. iFinD MCP is for sector/theme/news/notice/industry
   chain snapshots first; TuShare remains the structured exact-flow/batch probe
   candidate; tdxhub remains the current backbone for stable local facts.
2. **Storage/DB governance:** continue from manifest + retention dry-run toward
   owner-based retention and compact policy. No production deletion without
   consumer migration/retirement evidence, copied-DuckDB validation, manifests,
   and rollback.
3. **Strategy/model work:** resume stage-opt and model exploration only after
   upstream signal density, data-source truth contracts, and live audit gates
   are explicit. Stage-opt freshness is now repaired to trusted K-line max
   `2026-06-04`; the next stage-opt decision is whether a no-persist
   state/source POC can improve PIT candidate supply without adding another
   stale writer.
4. **RD-Agent(Q) follow-up research:** run a read-only deep dive after P0
   cleanup or in an explicitly bounded parallel research slot, with output as a
   dated `analysis/` research note. Compare the upstream RD-Agent(Q)/Qlib
   research loop, Co-STEER feedback, factor mining, report-to-code flow,
   experiment manager, and agent role split against this project's PIT,
   data-source, paper-sim, promotion, and `experiment_jobs` contracts. The
   expected result is a borrow/reject table, an integration-risk map, a mature
   component reuse list, and the smallest reversible POC plan. Mature ideas may
   be copied as isolated research tooling; no upstream framework, agent loop,
   generated factor, or model result can bypass ChunkyMonkey's truth-source,
   leakage, paper-sim, and promotion gates.

## Long-Term Roadmap

| Area | Direction |
|---|---|
| Data sources | Capability-level routing: `tdxhub` backbone, iFinD MCP semantic/industry-chain research snapshots, TuShare structured exact-flow/batch probes, each gated by PIT/freshness/watermark |
| Compute | `experiment_jobs` is the only approved job contract; `local` and `modal` both active (2026-06-11); `modal` dispatch stays safe-by-default (`dry_run=True`) and requires reviewed adapter + artifact manifest + rollback before any `dry_run=False` paid run |
| Research agents | Microsoft RD-Agent(Q) / Qlib ideas may feed isolated candidate generation and experiment design; production data, PIT, backtest, and promotion gates stay in ChunkyMonkey |
| Database | Manifest-owned DB aliases, read-only attached DBs by default, owner-based retention, no ad hoc scripts or hidden DB paths |
| Promotion | Measured paper sim / Phase4 / PBO / forward evidence only; in-sample or proxy metrics remain `unknown` |

## Operating Reminders

- Do not read or apply `CLAUDE.md` by default.
- Use `$architect-controller` for architecture/controller work,
  `$chunkymonkey-governance` before risky project execution, and
  `$chunkymonkey-review-gate` before commits or after `.py/.yaml/.sql` changes.
- Prefer first-principles truth sources: K-line for tradeability, calendar for
  dates, config/table/service owners for business rules.
- Remove proven-dead paths directly; do not keep obsolete code by comments,
  hidden flags, or compatibility shims.
