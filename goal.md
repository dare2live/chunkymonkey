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
草案 `analysis/constitution_v2_draft_20260611.md` 已采纳为 live, 仅留作锻造证据。
当前周: **W1** — DONE: need_027 5 gates (moneyflow 4.22M 主源就位) / E4 回填 (moneyflow/dc_member/stk_limit/stock_st 等 watermark 至 20260611) / E7 快照自养 (concept-snapshot launchd) / 数据平台 P0 (失败分级+drain+SLA registry 驱动) / 前端急救 / 概念事件 detector / 互动易 L2 直抓 / 全量默认 tushare 政策。进行中: chain4/5 回填 (旧源退役批 + K线三件套) / W-B 25 接口逐域注册。
**同步推进矩阵 (2026-06-12 用户决议: 并行不互抢资源)**:
| 轨道 | 资源 | 内容 | 状态 |
|---|---|---|---|
| A 存储 | smartmoney IO | 34G 拆分完成但出回归: COPY FROM DATABASE 丢 315 约束/348 索引 → 两轮整改 (06-12 15:34 收口: 164 张 upsert 目标表 PK 全量恢复, 冒烟 6/6 PASS, 终态 24G); 反例与 validation v2 见 `analysis/db_split_runbook_20260612.md` | DONE (旧库 06-26 后删) |
| B 数据 | tushare gateway (并发 2) | chain9 收官 (06-13 05:50): 日历扩展 5343 行后 top_list/daily/adj_factor 实验窗 728 日齐, dc_member 重拉 23.1M 行 (8.6x), top_inst 1.87M; 首批类型推断陷阱根治 (加宽重试)。**chain9b 在跑** (06-13 06:22 发射): daily_basic 2020-2022 → LHB 判决 → dc_member 6 凹陷日定点重拉 (实测全是残缺拉取非 vendor 缺口) → 加宽自愈三域 (suspend_d/dc_index/fina_mainbz) → 概念事件重建 | 链在跑 |
| C 实验 | 本地 CPU (判决全 SQL 秒-分钟级) | **C0 FAIL (06-12)** 筹码轴冻结。**LHB 上榜即退出 = GO (06-13 06:52, 第一个判正 alpha)**: n=74,111, 净效应 +2.428pp/20日 CI[2.24,2.61], 7/7 年为正, 对照臂 -0.296 证非均值回归 — `analysis/lhb_exit_verdict_20260612.json`; **是否进 B 主书退出组件 ablation 待用户拍板**; C2 冷却期二审解锁。**LF V0 概念龙头-跟随者 = REJECT (06-13 14:15)**: net -0.025pp CI[-0.057,0.008] 阈值+0.55, follower 相对同日同涨幅带非成员零超额 — theme/LF +5-10pp 假设证伪 (`analysis/lf_v0_verdict_20260613.json`); 判负处置 theme/LF 轴封档 + bank/sentiment.py 列退役 + 产能转 D 排序层/LHB | LHB 判正; LF 判负 |
| D 算力 | **modal ($30/月)** | chunkymonkey-compute 已 deploy (smoke PASS); 全市场 CYQ 复算未启动 (push 脚本未写 + C0 FAIL 连带冻结首单); smoke 与真数据同路径覆写隐患待修 | 冻结待裁 |
| E 消费侧 | 代码 | 复权链转正 (adj_factor 4.47M 已落) + LHB 双轨核对退役 | 排 C 轨道同窗 |
验证期 (W1-W12) 新策略 0 真金白银, 全部 paper_sim 候选态。

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
