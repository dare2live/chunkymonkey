# ChunkyMonkey Architecture Implementation Plan

> **状态: 大部偏离 / deprecated (2026-06-20)** — 本计划成于 2026-06-16 reset 前, 引用的
> `audit_*`/`build_*`/`modal_data_push.py` 等多已删, 旧 alpha158/formula 管道已退役。
> **当前权威执行计划** = `../goal.md` (当前阶段/KPI) + `../analysis/data_validation_backtest_plan_20260619.md`
> (主升浪猎手 refined plan) + `MASTER_TOPLEVEL_DESIGN.md` (架构骨架)。本文件留作历史架构参考,
> **勿当现行命令源** (其中脚本命令多已悬空)。

This is the durable execution plan. It describes order, boundaries, gates, and
acceptance criteria. It must not duplicate every current PASS/WARN/FAIL detail;
the live ledger is `../goal.md`.

## Document Boundary

| Question | Source of truth |
|---|---|
| What is blocked or next right now? | `../goal.md` |
| What is the execution order? | This file |
| Why did the architecture reform start? | `MASTER_TOPLEVEL_DESIGN.md` (architecture_reform_context retired 2026-06-15) |
| What rules are non-negotiable? | `PROJECT_CONSTITUTION.md`, `engineering_governance.md` |
| What data/product contracts must future work satisfy? | `data_product_contract.md`, `strategy_validation_contract.md` |

If this file and `goal.md` disagree on current state, trust `goal.md` and update
this file only when the durable sequence or gate changes.

## Risk First

| Risk | Decision |
|---|---|
| Dirty worktree hides unrelated changes | Work by bucket and reviewed slice; never `git add .` |
| Old docs steer new work | Keep active docs <=10; current status only in `goal.md` |
| Universe/PIT errors create fake alpha | Architecture gates precede formulas, backtests, Optuna, and frontend |
| Historical HIGH complexity is confused with new debt | Use `chunkyctl doctor` baseline/diff and inspect scanner findings before patching |
| Data-source needs drift as sources change | Manage needs in config/table contracts, not chat memory |
| UI overclaims stale/proxy values | Backend evidence and lineage contracts must exist before frontend redesign |

## Current Pause Line

Do not resume business expansion until governance gates are accepted:

| Paused work | Resume condition |
|---|---|
| 300616 original or derived formulas | Universe, PIT, data freshness, and plan gates pass |
| BestChoice formula expansion | Artifact freeze, namespaced challenger import, and local paper evidence pass |
| Heavy Optuna or broad replay | `backtest_preflight`, `plan_validator`, data audit, and an `experiment_jobs` plan pass |
| Stock-profile or global frontend redesign | Data/profile/API contracts are stable and tested |

主升浪猎手 remains the product north star, but the research log is hypothesis
evidence, not production proof. It starts only after framework governance.

## Target Architecture

Full design draft (六层契约 + 回测配置化 + 新数据域/新策略挂载范式 + 6 条防发散 gate):
`../analysis/architecture_framework_design_20260611.md` (2026-06-11 顶层设计, controller verdict PROCEED).
核心立场: 复用 paper_sim v2 / data_sources registry / feature_registry 三骨架, 只补契约+注册制,
不建新引擎; 新增物总清单 = 2 yaml (sync_registry/strategies) + 2 模块 (sync_runner/strategy_registry)
+ 1 表 (fact_sim_run) + 1 lint。落地顺序见该稿 §6, 挂靠下方 Active Repair Plan 各 Phase。

| Layer | Responsibility | Truth source / owner |
|---|---|---|
| L0 Infrastructure | Calendar, K-line, configs, audits | K-line tradeability, calendar dates, YAML rules |
| L1 Formula engine | Formula logic, parameters, search spaces | Formula modules plus `formula_*.yaml` / `optuna_config.yaml` |
| L2 Signal and profile | Signal context, stock/institution/main-force profiles | PIT features, lineage, freshness evidence |
| L3 Strategy execution | Universe, paper sim, costs, constraints, promotion | Preflight gates, paper sim config, excluded stocks |
| L4 API/UI | Cockpit, stock file, monitor views | Backend contracts; UI never fabricates evidence |

行业分类层 (L0 真相源): 主口径 = 申万 L2 (measured: `../analysis/industry_discrimination_20260611.json`,
净区分度 0.137 > 通达信 L2 0.118; L2>L1, 通达信 L3 过细=过拟合)。生产需从 TuShare `index_member_all`
拉历史成分 PIT 化 (申万退役真因 = 只有快照无历史)。多口径并存: 申万 L2 主分类; 东财/同花顺口径
专服各自资金流/热度因子 (口径必须匹配才能 JOIN)。板块轮动/产业链扩散/个股多标签建在此层之上。

## Execution Roadmap

| Order | Priority | Slice | Acceptance |
|---:|---|---|---|
| 0 | P0 | Worktree and docs governance | `scripts/chunkyctl doctor --fast`, `scripts/chunkyctl docs --format markdown`, docs graph PASS, reviewed dirty buckets |
| 1 | P0 | Universe and truth-source governance | `check_universe_filter.py --all` CLEAN; `dim_active_a_stock` only code/name/cache/schema |
| 2 | P0 | Commit/review/test-tool gates | Rule 10 blocks staged `.py` without review; test-tool audit has current registry coverage |
| 3 | P0/P1 | Data-source and lineage contract | Need/source config has grain, PIT key, freshness SLA, evidence status, production eligibility |
| 4 | P0/P1 | Storage/artifact contract | Large payloads are summarized, normalized, or governed as artifacts; no recursive audit blobs |
| 5 | P1 | Updater manager split | `updater.py` stays a thin supervisor; data modules own their own updates and evidence |
| 6 | P1 | Complexity debt | No new HIGH; historical HIGHs ranked by hot path, gate impact, data size, and testability |
| 7 | P1 | Freshness/PIT repair chain | Stale tables and PIT WARN/FAIL are fixed at first bad writer, not hidden by fallbacks |
| 8 | P1/P2 | Post-governance strategy mainline | 主升浪猎手, BestChoice, 300616, and main paper sim follow validation gates |
| 9 | P2/P3 | Profiles/API/frontend | Profile contracts and evidence states exist before UI redesign |

## Active Repair Plan (2026-06-11 checkup)

Dated evidence (2026-06-11 体检产物 28 confirmed + 61 medium/low findings) retired
2026-06-15 — 地基-reset 后该批 findings 多已偏离; 现行执行序见本节 + `../goal.md`。

| Phase | 内容 | 验收 |
|---|---|---|
| 0 止血 (P0) | 调度 cron→launchd + 告警送达 (done 2026-06-11, `6f0357d5`); K 线补数 06-05~06-10; 427 commits push (done); champion 身份统一到单一 yaml 注册点; 证据链补洞 (v9b train_log 缺失行 / p3 HS300 基准=0 / GO-NO-GO 混 model_id) | launchd 连续 3 天成功; K 线 max(date)=最新交易日; delivery_readiness 每项证据可复核 |
| 1 TuShare 接入 (P0/P1) | need_027 gate 已 PASS (2026-06-11); 通用 sync client (0 行=失败重试 / 按 trade_date 批量 / watermark / failure queue); 接入序: moneyflow → cyq_perf/chips → stk_limit/stock_st/suspend_d → 北向 → margin → top_inst → trade_cal/stock_basic 去 akshare 化 → daily 对账 | 5 项 required post-probe gates 全 pass |
| 2 Alpha 研究 (P1) | 特征注册制 + ROI gate (Spearman/coverage/var); 顺序: 资金流族 → 筹码族 (winner_rate 与胜率诉求同义) → 板块/概念协同 (dc_member PIT 化) → 龙虎榜/游资 → 事件族; OOS RankIC 基线 0.0108-0.0203, 相对提升 ≥+50% 触发 PIT 复审 | walk-forward OOS RankIC + ablation |
| 3 回测收敛 (P1, 与 2 并行) | 不重写 paper_sim; AbstractStrategy 接口; 删 legacy latest 路径; 内建三基准对比; paper_sim 超参进 Optuna; 12 yaml 变体收敛为 prod 1 + 模板 1 | 数字出口规则: 对外只引用含成本 replay 及以上 |
| 4 模型/策略升级 (P1/P2) | v7 = v6 + 新特征族逐族 ablation; ensemble 权重进 Optuna; regime gate 数据驱动 (+moneyflow_mkt_dc); 胜率专项以 OOS 月度胜率分布验收 (目标稳定 ≥55%) | Phase4 gate + paper_sim 全 KPI |
| 5 实盘验证 (P2) | 可执行性闭环 (stock_st/stk_limit/suspend_d 替代静态规则); 小资金实盘滑点校准; paper→live 偏差记录; 双周 KPI gate | 四大目标含成本 OOS 口径 |

治理瘦身 (贯穿): G1 状态面 8→3 · G2 死表族清理 (~34GB) · G3 dim_all_ever_listed 退役 ·
G4 每股最优 5 表→1 / 推荐 8 表→1 · G5 bestchoice 双拷贝二选一。
P2 工程 slice 待办: workflow_checkpoint 物理退役 (涉 14 文件); 体检 28 confirmed 余项
(SQL 注入参数化 / 0.0.0.0 默认绑定 / champion artifact 备份 / daily_update 18 步覆盖缺口 /
plan_validator 缩进 bug / complexity 分 scope baseline / validate_loaded_stocks 公共化)。

## Updater Boundary

The smart updater is a supervisor, not the worker for every domain.

| Role | Owns | Does not own |
|---|---|---|
| Updater supervisor | DAG plan, dependencies, locks, stop/resume, StepResult aggregation, audit summaries | Pulling every source, computing every mart, deciding universe truth |
| Data/calculation module | Source connection, writes, domain validation, watermark, returned evidence | Global scheduling, UI state, cross-domain gate bypass |
| Audit module | Freshness, completeness, PIT, lineage, anomaly status | Directly mutating source data as a side effect |

This boundary is the criterion for any remaining `updater.py` split.

## Data And Product Roadmap

| Order | Priority | Work | Gate |
|---:|---|---|---|
| 1 | P0 | Source coverage exact-sync, including tdxhub primary and akshare/miaoxiang probe where the need requires it | `audit_tdx_data_need_coverage.py` |
| 2 | P0/P1 | Restore or replace stale main/super-large/large/medium/small order-flow data (`need_027`) | source probe (registered capability + `probe_source_capability.py`) + PIT/freshness evidence before production use; keep unknown/proxy until proven |
| 3 | P1 | CYQ implementation prerequisites | Float-share history, K-line alignment, PIT disclosure dates, validation cases |
| 4 | P1 | Stock, institution, and main-force profile contracts | `value`, `as_of_date`, `built_at`, `source_tables`, `freshness_status`, `evidence_status`, `lineage_ref`; main-force stays unknown/proxy until source probe succeeds; profile threshold tuning must run `audit_portfolio_sizer_profile_attrition.py` before any change |
| 5 | P2 | Stock file API | Unknown/proxy/stale states covered by contract tests |
| 6 | P3 | Frontend redesign | Browser/smoke checks after backend contracts stabilize |

## Strategy Mainline After Governance

| Order | Work | Minimum validation |
|---:|---|---|
| 0 | 主升浪猎手 serious research | Reproduce data/code boundaries; PIT, cost, T+1, limit-up, overlap, walk-forward, paper sim, forward monitor |
| 1 | BestChoice challenger | Freeze/hash/lineage, namespaced import, complementarity evidence |
| 2 | 300616 original formula replay | God-view diagnosis separated from PIT-safe logic |
| 3 | 300616 derived formulas | Non-empty search space and sample/board coverage |
| 4 | Main project backtest/paper sim | `backtest_preflight` 8 checks plus realistic execution |
| 5 | Candidate and holding monitor | Promotion only with current, non-proxy evidence; missing metrics remain `unknown` |

## Required Gates

| Situation | Required commands / checks |
|---|---|
| New session | Follow `chunkyctl_session_quickstart.md` |
| Before a task | `scripts/chunkyctl preflight "<task>" path...` |
| `.py` edits | CodeGraph query/context before; `codegraph sync .` and complexity scan after |
| Test evidence | `audit_test_tool_health.py --scope <scope>` before citing tests |
| Docs cleanup | `audit_docs_graph.py --format markdown` and `scripts/chunkyctl docs --format markdown` |
| Backtest/Optuna/provider job | `backtest_preflight`, `plan_validator`, data audit, and `scripts/chunkyctl jobs --family <family> --backend local --input-snapshot <snapshot> --objective <why> --rollback-plan <plan> --gate-evidence <gate>=<artifact>` |
| Commit | `scripts/safe_commit.sh`; no raw `git commit` |

## Acceptance Criteria

| Area | Accept when |
|---|---|
| Docs | Active docs <=10, no unresolved live refs, no forbidden authority cycles, stale docs archived or deleted |
| Worktree | Intentional slices are reviewed by bucket; unrelated dirty files are preserved |
| CodeGraph | Index synced after edits; remaining pending `Added` files are explained by untracked indexable files |
| Complexity | No new HIGH; historical debt has priority and owner |
| Data | Critical freshness/PIT/source gaps are `PASS`, `blocked`, or explicit `unknown`; no silent fallback |
| Strategy | Results come from current measured evidence, not in-sample/proxy/warn-only claims |
| User handoff | `goal.md` records current FAIL/WARN, next action, and unresolved risk |

## Compute Jobs

This plan does not start provider compute. Long data validation, model
training, backtest validation, and parameter search work must first be
registered in `backend/config/experiment_jobs.yaml` and planned with:

```bash
scripts/chunkyctl jobs --family <job-family> --backend local \
  --input-snapshot <snapshot> \
  --objective "<why this job should run>" \
  --rollback-plan "<how to stop or discard artifacts>" \
  --gate-evidence <gate>=<artifact-or-command>
```

`local` and `modal` are both active (2026-06-11 user decision, `~/.modal.toml`,
$30/mo cap). `modal` dispatch stays safe-by-default: `dry_run=True` default plus
reviewed adapter + artifact manifest + rollback before any paid run. App
`chunkymonkey-compute` deployed 2026-06-12 (cyq_replay_batch/all + smoke, smoke
passed with 300 synthetic rows); full-market CYQ replay has NOT started — data
push script `scripts/modal_data_push.py` is still pending, and the first paid
run is gated on the C0 verdict follow-up (C0 FAILed 2026-06-12).
