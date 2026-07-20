# PROJECT_INDEX — Current Project Map

> 状态：live navigation，非规则 owner
> 更新：2026-07-20
> 当前目标看 `goal.md`（A→H 已恢复；C/B-pit cutover ON；`BOARD.md`；启动 `scripts/chunkyctl agent-boot`）。
> 架构看 `docs/MASTER_TOPLEVEL_DESIGN.md`；机器入口与 writer 清单看 `FEATURE_MAP.md` 和 CodeGraph。
> 生成状态板：`PYTHONPATH=backend python backend/scripts/build_agent_board.py`（勿手改 BOARD.md；snapshot 时间戳非 trade_date；Next 菜单随 cutover yaml 投影）。

## 1. Authority

```text
AGENTS.md
  -> goal.md
  -> docs/MASTER_TOPLEVEL_DESIGN.md
  -> docs/strategy_validation_contract.md
  -> docs/engineering_governance.md
```

历史只查 `analysis/project_state_ledger.md`。`CLAUDE.md`、生成 handoff/checkpoint 和 dated analysis 不是 live authority。

## 2. Product map

| Tier | Current owner/package | Current reality |
|---|---|---|
| T0 market data | `backend/services/data_sources/`, `pipeline/`, `calendar.py`, `market_*` | A1–A5 代码完整；名义 K + ST accepted 至 `20260720`；manual sync `trigger_mode` 已拆：开市可拉今日，consumer/`available_at` 仍 `same_day_at 18:00`；form/qfq/segments/pulse 分析面经 canonical∪raw 已追到 `20260720`（legacy raw daily 仍停 `20260716`，formal 不写 raw）；margin 仍 frozen；无 mass fetch / cutover |
| T0 classification | `taxonomy.yaml`, SW/DC raw tables, DC snapshot builder | namespace 已分离；DC versioned PIT/membership 仍待 Phase 2 |
| T1 stock state | `technical_states/`, `segments.py`, `tier12_publish_{contract,writer,accept}.py`, `tier12_consumer_cutover.py`, `market_pulse_tier12_read.py`, `tier12_nominal_canary.py`, `tier12_project_universe.py` | 多轴状态可复用；C accept 分 `publish_scope=canary|project_universe`；`resolve_tier12_production_read` + B1/pulse 接线；**cutover yaml=true** → ACCEPTED_CUTOVER；**form enrich v1**（`stock_state_stage_pattern_v1`，exact-day `fact_stock_form_daily` → `form_name`/`axis_pos`；writer `axis_trend` 不覆盖）；re-accept `20260717`=4989 + `20260720`=4991；无 accept 日仍 fail-closed→LEGACY/`fact_stock_form_daily`；persist 可在 cutover-ON 下重跑（不回翻 yaml） |
| T2 market sensing | `market_pulse.py`, `market_pulse_tier12_read.py`, `market_pulse_b_pit_read.py`, `b_pit_mart_cutover.py`, `project_universe_breadth.py`, `tier12_publish_{contract,writer,accept}.py`, `tier12_consumer_cutover.py`, `tier12_nominal_canary.py`, `tier12_project_universe.py`, API/frontend | 展示可用但 breadth/margin UNTRUSTED（B-ext）；sentiment 旁路 `tier12_production_read` + `b_pit_mart_production_read`；B-pit shadow MATCH 120/120；**mart cutover=true（owner opt-in 2026-07-20）→ MART_CUTOVER（project_universe_pit）**；C envelope 可 project_universe scope；窗外/无 shadow 日仍 fail-closed→legacy；drill form 单轨 production-read（无 legacy JOIN+accepted 双写） |
| T3 institution | `institution_profile.py` + `institution_follow_b0/b1/b2/b4` (+ `_measure`) + `institution_follow_edge_gates.py` + `disclosure_research_read.py` + `disclosure_enrichment_projection.py` + `disclosure_dataset_snapshot.py` + dual-write/shadow/boundaries + `*_acceptance.py` + router/tests | **首个正式策略包**；E0 FIXED；E PARTIAL：120d B0/B1/B2 reject；B4 inconclusive（coverage fraction）；均 ≠ StrategyRelease |
| T3 main rally | `main_rally_dataset_snapshot.py`, `main_rally_b0.py`, `main_rally_b0_measure.py`, `main_rally_b1.py`, `main_rally_b1_measure.py`, `main_rally_b2.py`, `main_rally_b2_measure.py`, `rally_gt.py`, `rally_detect.py`, rally config/tests | GT 资产成熟；**F0+F1+F2+F3 FIXED**：`main_rally_v1` freeze + B0 setup-entry short-horizon + B1(+Tier1 stock state) + B2(+Tier2 market sensing/`MarketContextSnapshot` project-board breadth, 独立 ablate on B0, 非叠加 B1) measured，同 B0 folds/costs、`REQUIRE_HOLDOUT_LIFT_VS_B0` → 三者均 reject/`claimable=false`（共享 `research_runtime`；非 full-episode；F0–F3 可 checkpoint） |
| T3 formulas | `bestchoice/FROZEN.md` + `evidence_manifest.json` | 冻结 challenger；Phase G 前不吸收 |
| T4 decision/paper | `paper_portfolio.py`, frontend observation page | Legacy NONCONFORMING 观察账本；不是 paper execution |

## 3. Runtime and data layout

| Area | Role |
|---|---|
| `data/tushare_raw.duckdb` | TuShare legacy `raw_tushare_*` compatibility 表 + frozen margin evidence；accepted calendar generation + nominal OHLCV/ST landing/canonical/accepted_partition 至 `20260720`；legacy `raw_tushare_daily` 仍停 `20260716`（formal 不写） |
| `data/market.duckdb` | K 线 serving/派生数据；qfq=`canonical∪raw`×adj 分析面（max `2026-07-20`），不等于名义成交价真相 |
| `data/reference.duckdb` | 交易日历、身份/reference 数据 |
| `data/smartmoney.duckdb` | 当前 mart、profiles、ops/control evidence |
| `data/feature_store.duckdb` | 特征面；使用前必须有当前 consumer 和契约 |
| `data/experiment_store.duckdb` | 实验 verdict/control；当前不代表完整 research runtime |
| `backend/config/` | 目标只保留 active policy；过渡期 legacy registry 必须显式标 `NONCONFORMING` 并列入 Phase 迁移债务 |
| `data/reports/tooling/` | 可重建工具证据，不是业务真相 |

精确数据库路径以 `backend/config/database_manifest.yaml` 为准；表、入口和 writer 以 live DB、`FEATURE_MAP.md`、CodeGraph 和 Moth 为准，不在本文件固定计数。

## 4. Important entrypoints

| Purpose | Active entrypoint |
|---|---|
| Session boot context (git+moth+codegraph+board, one page) | `scripts/chunkyctl agent-boot [--format json]` |
| Health | `scripts/chunkyctl doctor --fast` |
| Manual full data update | `bash scripts/daily_update.sh --date YYYYMMDD`；on_demand formal domains 不进 all-due；margin 仍 scope_blocked |
| Manual single-domain sync/canary/replay | `scripts/chunkyctl sync --domain DOMAIN`；`trade_cal` full generation；`daily`/`stock_st` 须显式 `--start/--end`（同日或 ≤40 交易日）；`--drain` 对三域 inapplicable；其它 disabled/formal 仍 fail closed |
| Shared tooling snapshot | `moth snapshot --repo .` |
| Business/tool assertions | `moth assert --repo .` |
| Coupling/deletion impact | `moth coupling --repo . --impact <name>` |
| Code discovery | `codegraph explore "<question>"` |
| CodeGraph refresh | `codegraph sync .` |
| Doc governance | `PYTHONPATH=backend python backend/scripts/check_doc_governance.py` |
| Doc drift | `PYTHONPATH=backend python backend/scripts/check_doc_drift.py --check` |
| Live continuity | `PYTHONPATH=backend python backend/scripts/check_continuity_integrity.py` (`FAIL` 直接非零) |
| Local reviewed commit | `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "<message>"`（WP1：L1/L2/L3；政策=`backend/config/commit_tiers.yaml`；L2/L3 跑与 CI 同面 pytest=`backend/config/ci_pytest_surface.yaml` via `run_ci_pytest.py`） |
| Tier1/2 full-universe accept (manual) | `PYTHONPATH=backend python backend/scripts/persist_tier12_full_universe_accept.py --decision-date YYYYMMDD`；cutover-aware（ON 时要求 resolver ACCEPTED_CUTOVER；永不翻 yaml）；form enrich 经 `load_form_rows_exact_day` |
| Phase D ExperimentRun persist (idempotent) | `PYTHONPATH=backend python backend/scripts/persist_phase_d_experiment_runs.py [--force]`；b0_bound + runtime-owned measured_offline；claimable 恒 false |
| Phase F main_rally F0+F1+F2 persist | `PYTHONPATH=backend python backend/scripts/persist_phase_f_experiment_verdicts.py [--freeze] [--force]`；snapshot + B0+B1 verdicts under `data/lineage/phase_f_experiment_verdicts/`（`b0.json`,`b1.json`,`manifest.json`）；≠ StrategyRelease |

已移除的 ChunkyCtl 子命令不是工作流，调用必须返回非零；不要在活文档或生成地图中把任何 retired lifecycle 重新列为 active。

## 5. Current structural defects

| Priority | Defect | Consequence |
|---:|---|---|
| P0 | K accepted + form/qfq/segments/pulse 至 `20260720`；legacy raw daily 仍 `20260716`（预期）；`index_dailybasic` 短窗 min_rows 拒写；margin frozen（rzrqye 新日 NULL）；禁 mass backfill | 两融列诚实 unknown；估值水位可能 stale |
| P0 | E 120d checkpointed measured reject/no-gain；C full-universe accept `20260717`（4989）+ `20260720`（4991）form enrich v1；D FIXED；F0+F1+F2 main_rally B0/B1 reject/`claimable=false`；B-pit 120d shadow **120/120 MATCH**；C/B-pit cutover **ON**（ACCEPTED_CUTOVER / MART_CUTOVER；无 accept/窗外日 fail-closed→legacy）；pulse drill 双轨 form 读已退役 | 下一刀 F3 main_rally B2（market sensing，同 B0 snapshot/folds/costs）**或** stop（非 Optuna / 非松门 / 非 mass backfill / 非静默 cutover / 非 StrategyRelease） |
| P0 | qfq serving surface has placeholder lineage and is used too broadly | Research reproducibility and execution price semantics are ambiguous |
| P0 | Legacy DC PIT residue lacks exit/re-entry/type; writer retired | Existing DB view cannot be used as historical taxonomy truth |
| P1 | formal `boundary_inventory` 仅为静态/测试资源，非 doctor readiness 证书（`formal_boundaries` 文案已澄清）；canary_pending 域无 countdown 出口 | 豁免不可见即永久；须在 goal/ledger 跟踪 canary 授权点 |
| P1 | Live DC snapshot/pulse tables predate namespace fix until manual rebuild | Code contract is fixed but stored rows still need controlled reconciliation |
| P1 | Market pulse mixes taxonomy, measurements, rolling/regime, write/read；仍读错误 scope raw | B-ext FIXED；B-pit shadow MATCH 120/120（membership proxy）；mart cutover=true（owner opt-in）→ 窗内切 project_universe_pit，窗外 fail-closed |
| P1 | Stock state/market regime rows lack config/input version | Historical outputs cannot prove which definition produced them |
| P1 | Phase D FIXED — runtime-owned measured offline (`research_runtime_measure`) + lineage `measured_offline.json`；StrategyRelease 仍禁 | Strategy evidence still cannot reach decision/product safely |
| P1 | Docs/CLI gates previously treated retired/warn as PASS；事实性断言（如“仅 TuShare”）不在 gate 覆盖 | Tooling green did not prove executable reality |

The current migration and blockers are maintained only in `goal.md`.

`safe_commit.sh` separately reports live continuity as `READY / DEGRADED / UNVERIFIED / BLOCKED`.
Only verifier/report contradictions block a repair commit; every non-READY state still blocks Tier0
consumption/release and must be closed by a separate continuity rerun.

## 6. Target package boundaries

These are logical owners, not an instruction to move every file immediately:

```text
market_data       landing, acceptance, canonical market facts
classification    taxonomy nodes, memberships, crosswalk
stock_state       state axes and pattern events
market_sensing    observations and context snapshots
research_runtime  snapshots, runs, artifacts, verdicts
strategies        institution_follow / main_rally / formulas
decision          strategy release, batches, candidates
paper_execution   orders, fills, positions, NAV
product           read-only APIs and UI read models
ops/governance    jobs, alerts, projections and gates
```

First establish `DatasetContract` and writer ownership around existing files. Move physical files only when a bounded-context migration has a passing shadow comparison.

Formal 执行契约物理 owner：`population_scope.py`（factory bind + `verify_execution_contract`）、
`formal_execution.py`（domain consumer 注册与 `propagate_formal_execution_contract` identity 门）、
`sync_runner.py`（`_require_formal_population_execution` → 传播成功后 `_refuse_formal_domain_runtime`，
禁止 legacy 落穿）。margin consumer 传播后 reason=`formal_runtime_retired`；无 consumer 仍
`execution_contract_not_propagated`。margin 仍 `scope_blocked` / live-write frozen。

当前 margin read path 的物理 owner：`margin_evidence.py` 负责固定查询快照，`margin_state.py` 负责
accepted proof，`margin_legacy_reconcile.py`/`margin_reconcile.py` 负责纯比较与现场编排，
`margin_readiness.py`/`margin_projections.py` 只在上层组合结果；依赖不得反向。

共享 accepted evidence 的物理 owner 是 `backend/services/data_sources/accepted_schema.py`；它只拥有
`ingest_batch` / `accepted_partition` 的固定 DDL 与结构验证，不拥有任何 domain completeness、writer、
availability 或 consumer 语义。现有 `dim_trading_calendar` 是 open-day serve projection
（`DIM_ROLE=serve_projection_open_days_only`），不是 accepted immutable generation。

calendar 按 `calendar_contract.py`、`calendar_schema.py`、`calendar_landing.py`、
`calendar_acceptance.py`、`calendar_reader.py`、`calendar_runtime.py` 分责；A2 发表入口是
`publish_accepted_calendar_generation` / authorized
`capture_and_publish_authorized_calendar_generation`。名义 K/ST 按
`nominal_ohlcv_*` / `stock_st_*` + 共享 `security_day_partition.py` /
`security_day_capture.py` 分责；authorized 单日入口
`capture_and_publish_authorized_*_partition`。`observation_population.py` 的 default
readiness 经 `resolve_eligible_observation_date`（accepted calendar ∩ K/ST
`availability_policy`）评 frontier，不索要周末/节假 calendar-today 分区。
Sync transport 用 `trigger_mode=manual|automatic`（`resolve_sync_eligibility_frontier`）；
consumer/`available_at`/continuity 仍走时钟门 `resolve_availability_frontier`。
`trade_cal`/`daily`/`stock_st` = `authorized_manual_generation` + `on_demand`
（禁 all-due；K/ST 禁 drain）；sync 禁 legacy raw。margin 仍 scope_blocked / frozen。

旧 margin history request/runtime/writer/CLI 已退役物删。冻结 v2 只由 `margin_evidence.py`、
`margin_state.py`、reconcile/readiness/projection 读侧保留不可变审计证据；不存在受支持的继续写入旁路。
共享 provider timeout 只由 `sync_registry.yaml` 配置并经 `runtime_limits.py` 在副作用前验证。

## 7. Generated map discipline

`FEATURE_MAP.md` is generated and must:

- distinguish active from retired commands;
- enumerate current sync domains and writer evidence;
- declare static-scan blind spots such as dynamic table names;
- be idempotent on a second generation run;
- never become a human-maintained architecture owner.

When it disagrees with live code/DB, fix the generator or lifecycle registry, regenerate, and challenge the verifier before trusting the map.
Current lineage discovery is conservative static evidence: literals in disabled compatibility contracts and adversarial tests may appear as
`consume` edges; they do not override CodeGraph call paths or the no-production-fan-in Rule 10 verdict.

## 8. Repository boundaries

- `bestchoice/` must match `FROZEN.md` and its manifest exactly; it has no independent goal/agent/handoff/runtime and must not be folded into production during cleanup.
- `sandbox/` is disposable exploration; only `sandbox/README.md` is durable.
- `analysis/` is not a second docs tree. Keep only the ledger and necessary non-reproducible evidence; ordinary plans and narratives belong in git history.
- `.agents/skills/` mirrors generic skills and is not the project policy owner; project-specific skills live under `/Users/dp/.codex/skills/chunkymonkey-*`.
