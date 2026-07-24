# PROJECT_INDEX — Current Project Map

> 状态：live navigation，非规则 owner
> 更新：2026-07-23（文档收敛）
> 当前目标看 `goal.md`（foundation CLOSED / `phase_closure_ready`；策略 paused）。
> **执行方案仅两份**：底座 `analysis/FOUNDATION_EXECUTION_PLAN.md` · 策略 `analysis/STRATEGY_EXECUTION_PLAN.md`（RX 前 BLOCKED）。台账 `analysis/DOC_CLEANUP_20260723.md`。
> 架构看 `docs/MASTER_TOPLEVEL_DESIGN.md`；机器入口看 `FEATURE_MAP.md` / CodeGraph。
> `BOARD.md` = 生成投影，**勿手改、非执法**（`build_agent_board.py`）。

## 1. Authority

```text
AGENTS.md
  -> goal.md
  -> docs/MASTER_TOPLEVEL_DESIGN.md
  -> docs/strategy_validation_contract.md
  -> docs/engineering_governance.md
  -> analysis/FOUNDATION_EXECUTION_PLAN.md | STRATEGY_EXECUTION_PLAN.md  (execution only)
```

历史只查 `analysis/project_state_ledger.md`。`CLAUDE.md`、dated analysis 不是 live authority。

## 2. Product map

| Tier | Current owner/package | Current reality |
|---|---|---|
| T0 market data | `backend/services/data_sources/`, `pipeline/`, `calendar.py`, `market_*` | A1–A5 代码完整；accepted daily `20190102`→`20260720`（1829d）+ ST `20220104`→`20260720`（1099d；asymmetric ST raw floor；chunked ≤40d local-raw land→accept）；**S1–S6 FIXED / S7 near-FIXED**：default sync = acquire→caller-only land→accept；`security_day_acquire`；CLI land/accept/derive；derive+form+pipeline clean 默认 accepted-only（`--allow-legacy-fill` 逃生）；`legacy_raw_plane.yaml`+gate（**20/46 ssot + 3 retired** = 2 blocked + 8 serve_l0_declared + 10 sync_orphan；`stk_factor_pro`/`express`/`fina_mainbz` sunset/DROP；**2026-07-24 `stk_holdernumber` RESTORE** by_ann_date+DataAccess+dossier；B1 `dc_member`→`fact_dc_member_daily` observation-date PIT；B2 limit+moneyflow(+dc)+index_daily+top_inst→fact_*；SW→PIT；pulse builders→mart；daily_basic→dim；stk_limit→form；stock_basic→dim；adj→qfq）；**B5 FIXED**（registry/qfq + Type-B enrichment；qfq default **incremental** / `--full`+compact）；form/qfq/segments/pulse 至 `20260720`（legacy raw daily 仍停 `20260716`，formal 不写 raw）；margin **1b FIXED** `contract_version=3` SSE+SZSE via acquire on_demand catchup（禁 all-due/mass；补跑仅验证）；无 mass fetch / cutover |
| T0 classification | `taxonomy.yaml`, SW/DC raw tables, `build_sw_industry_view.py` → `v_sw_industry_pit`, DC snapshot builder | namespace 已分离；**SW L1 PIT exclusivity FIXED**（effective `out_date` 闭合重分类/同日双 L1；002310 等）；DC versioned PIT/membership 仍待 Phase 2 |
| T1 stock state | `technical_states/`, `segments.py`, `tier12_publish_{contract,writer,accept}.py`, `tier12_consumer_cutover.py`, `form_production_read.py`, `market_pulse_tier12_read.py`, `tier12_nominal_canary.py`, `tier12_project_universe.py` | 多轴状态可复用；C accept 分 `publish_scope=canary|project_universe`；`resolve_tier12_production_read` + B1/pulse 接线；**cutover yaml=true** → ACCEPTED_CUTOVER；**form enrich v1** + dossier/screener **typed hybrid**（accepted overlay name/pos/trend/breakout；purity/vol/sub=`hybrid_residual_fields`，非纯 accepted）；re-accept `20260717`/`20260720`；无 accept 日 fail-closed→LEGACY/fact |
| T2 market sensing | `market_pulse.py`, `market_pulse_serve_read.py`, `market_pulse_scope.py`, `margin_pulse_promote_gate.py`, `universe_serve_filter.py`, `market_pulse_tier12_read.py`, `market_pulse_b_pit_read.py`, `b_pit_mart_cutover.py`, `project_universe_breadth.py`, `tier12_publish_{contract,writer,accept}.py`, `tier12_consumer_cutover.py`, `tier12_nominal_canary.py`, `tier12_project_universe.py`, API/frontend | **沪深A serve 白名单 FIXED**；**breadth READY** as `project_universe_pit` when B-pit `MART_CUTOVER`（窗外 typed EMPTY；窗内缺证据 UNTRUSTED；禁假 TRUSTED）；**F4 rzrqye READY** as external_aggregate on accepted days（`margin_pulse_promote.yaml`；应有却缺 UNTRUSTED；覆盖前/确认空 typed EMPTY）；B-pit shadow 窗 `20260121`–`20260722`；form 单轨 production-read |

| T3 institution | `institution_profile.py` + `institution_follow_b0/b1/b2/b4` (+ `_measure`) + `institution_follow_edge_gates.py` + `disclosure_transport.py` + `disclosure_research_read.py` + `disclosure_enrichment_projection.py` + `disclosure_dataset_snapshot.py` + dual-write/shadow/boundaries + `*_acceptance.py` + `org_holding_aif10.py` + router/tests | **首个正式策略包**；E0-HIST/F6 PASS；org = **period-gap + population gate**（`under_populated_accepted`；mass/by-date invent banned）；**`mart_inst_profile` display coverage FIXED** + **daily process delta-gates `rebuild_all`**（闭环法 `serve_derive_closed_loop_law_20260723.md` / `pipeline.closed_loop`）；HS-A latest top10 profile≈episode；E PARTIAL：B0/B1/B2 reject；≠ StrategyRelease |
| T3 main rally | `main_rally_dataset_snapshot.py`, `main_rally_b0.py`, `main_rally_b0_measure.py`, `main_rally_b1.py`, `main_rally_b1_measure.py`, `main_rally_b2.py`, `main_rally_b2_measure.py`, `rally_gt.py`, `rally_detect.py`, rally config/tests | GT 资产成熟；**F0+F1+F2+F3 FIXED**：`main_rally_v1` freeze + B0 setup-entry short-horizon + B1(+Tier1 stock state) + B2(+Tier2 market sensing/`MarketContextSnapshot` project-board breadth, 独立 ablate on B0, 非叠加 B1) measured，同 B0 folds/costs、`REQUIRE_HOLDOUT_LIFT_VS_B0` → 三者均 reject/`claimable=false`（共享 `research_runtime`；非 full-episode；F0–F3 可 checkpoint） |
| T3 formulas | `bestchoice/FROZEN.md` + `evidence_manifest.json` | 冻结 challenger；Phase G 前不吸收 |
| T4 decision/paper | `paper_portfolio.py`, frontend observation page | Legacy NONCONFORMING 观察账本；不是 paper execution |

## 3. Runtime and data layout

| Area | Role |
|---|---|
| `data/tushare_raw.duckdb` | TuShare legacy `raw_tushare_*` compatibility 表 + frozen margin evidence；accepted nominal OHLCV `20190102`→`20260720`（1829d）+ ST `20220104`→`20260720`（1099d）；legacy `raw_tushare_daily` 仍停 `20260716`（formal 不写；local-raw materialize → landing 另路径） |
| `data/market.duckdb` | K 线 serving/派生数据；qfq 分析面 max `2026-07-20`（derive+pipeline clean 默认 accepted-only）；≠名义成交价真相 |
| `data/reference.duckdb` | 交易日历、身份/reference 数据 |
| `data/smartmoney.duckdb` | 当前 mart、profiles、ops/control evidence；B2 `fact_stock_limit_daily` |
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
| Manual full data update | UI `#/workbench`「数据更新」→ `POST /api/v3/ops/jobs/daily_update/run`；或 `bash scripts/daily_update.sh --date YYYYMMDD`；acquire **先** `--all-due` drain、**后** formal `daily`/`stock_st` latest-eligible（pending soft / hard→degrade，不 raise 绑架兄弟域；证据 `foundation_acquire_all_due_unblock_20260722.md`）；`stock_st`=ST membership 证据（沪深A **含 ST**；排除仅三板/退市整理/B/BJ；soft-fail≠踢出 ST）；due-plan 预览读 SLA JSON；holders skip-if-wm-unchanged；on_demand 不进 all-due；Cap E S1/S2 → `POST /api/v3/ops/pipeline/land-accept/run`；margin=`on_demand`+`bounded_calendar_catchup`（1b；不进 all-due；不挡 preflight）；**typed `run_outcome`** ∈ {success/soft_waiting_clock/**integrity_observe**/hard_fail} 写入 `data/reports/daily_*.json`（`services.pipeline.run_outcome` 单一计算点；闭环法：完整性≠等时钟）；exit/wrapper/notify/UI **渲染**该字段——软时钟/完整性琥珀观测、永不红 FAIL；硬挡才 `job FAIL`（证据 `phase1_run_outcome_20260722.md` + `serve_derive_closed_loop_law_20260723.md`）；软观测 macOS 横幅按**签名合并**（`store._soft_banner_signature`：date+outcome+reason+classified msgs+SLA stale；per-day marker `chunkymonkey_soft_banner_<date>.marker`）——空点重跑同软态不再刷屏、软变化/成功后再软才重弹（证据 `workbench_incremental_orchestrator_ux_20260722.md`）；**CX-1**：`delta_manifest`（`services.pipeline.delta_manifest`）+ `stage_timing_s`/`budget_status`（config `pipeline_latency_budgets.yaml`）写入 `daily_*.json`；DC 前沿未变则 skip `build_dc_industry_view`，`market_pulse` 迟到窗恒跑；ops idle 暴露 manifest（证据 `cx1_acquire_efficiency_acceptance_20260722.md`）；**CX-2**：`state_sensors` 只读探测 ST 戴帽/摘帽、holders ratio/rank/exit（accepted notice only，禁 MAX invent）、dim_active 退市/入市 → `delta.state_changes` + process_plan `state_change:*` force（不写 Tier0；证据 `cx2_state_sensors_acceptance_20260722.md`）；drain 经 `_run_drain_subprocess`（Popen + stderr pump **实时**写 UI 作业日志 #1 + 管线日志 #2，每域 `[drain i/N]`）——不再 `capture_output` 憋 40min 假死；**manual 探源优先**（`eligible_end=今天`，空<窗→pending_publish 软、空≥窗→fail-closed，`available_after` 仅管 automatic 消费前沿+分类）；org 增量按披露截止**自动前移**（skip 日志带 `next_period`/unlock）（证据 `business_clock_and_drain_rework_20260722.md`） |
| Stock dossier Cap F | UI `#/stock/:code` → `GET /api/v3/stock/{code}/dossier`（`stock_dossier_cap_f_usable`；沪深A；form/stage/holders + episode cycle/return；机构 deep-link closed-loop；tabs ok/empty/delegated）；**资金 tab** → Cap A moneyflow API；证据 `analysis/dossier_100_usable_20260723.md`；pytest `tests/test_stock_dossier_api.py` ∈ blocking |
| Moneyflow decision assist (3A+3C) | Tier3 `GET /api/v3/decision/moneyflow/board` + `/stock/{code}`；config `moneyflow_assist.yaml`；service `moneyflow_assist.py`（读 pulse mart + stock moneyflow facts；`flow_regime`→潜伏/抢筹/出货迹象；窗未满→unknown；**CX-3** 暴露 signed `flow_streak`）；UI `#/market`「资金决策辅助」= **潜伏象限** scatter + 表（Cap A；unknown≠0；地形 Enrich 延后；`phase3_latent_quadrant_mvp_20260722.md`）+ dossier 资金；证据 `capability_a_moneyflow_assist_20260721.md`；感知卡保持零买卖暗示 |
| 交集最强股 decision assist (4D) | Tier3 `GET /api/v3/decision/intersection/strongest` + `/intersection/stock/{code}`；config `decision_intersection.yaml` v1；service `decision_intersection.py`（DC 行业∩概念∩申万 L1 三链；`sw_l1_member_mem_sql` PIT；复用 `moneyflow_assist` behavior）；三链 as-of 不一致/滞后 SLA → `status=stale`；UI `#/market`「交集最强」+ dossier 交集；**CX-3** 板块名 → `sector_membership` facet chip；证据 `capability_d_intersection_strongest_20260721.md` + `plan_residual_reconcile_20260722.md`；pytest `tests/test_decision_intersection.py` ∈ blocking |
| CX-3 briefing + facet serve bricks | Tier3 `GET /api/v3/decision/briefing/daily`（`daily_briefing.py`+yaml；Cap A/B/D trust gate → stale 则 `narrative=null`）+ `GET /sector/members`（`sector_membership_serve` 包 pulse members + DC SLA）+ `GET /moneyflow/stock_streak`（`stock_flow_streak` 连续净流入宇宙）；UI `#/briefing` + Market assist 简报面板 + `#/explore?kind=sector_membership|flow_streak`；证据 `cx3_capability_bricks_acceptance_20260722.md`；pytest `tests/test_cx3_capability_bricks.py` ∈ blocking |
| 形态/阶段选股面 (5B) | Tier3 `GET /api/v3/screener/options` + `/form_stage`；config `stock_screener.yaml`；`stock_screener.py` + 共享 `form_production_read`（与档案 F 同一 production-read：fact brick + ACCEPTED_CUTOVER overlay；`sql_where_active_a_share`）；全局 `MAX(trade_date)` SLA → `status=stale`；UI `#/market`「形态/阶段选股」→`#/stock/:code`；证据 `capability_b_stock_screener_20260721.md`；pytest `tests/test_stock_screener.py` ∈ blocking |
| Holders / frontier primitive | Shared `frontier_decision.decide_frontier`（`skip_behind`/`equal_day_population_gap`/`advance_window`/`pending_clock`/`hard_fail`）；holders=`notice_date` sparse + **behind-wm hole catchup**（`holders_notice_catchup`：local-fact accept ≤40 + forward by_notice；禁 by_ts_code mass；证据 `holders_ann_date_axis_20260724.md`）；`stk_holdernumber`=`by_ann_date`；`by_ann_date`=`ann_reprobe` 保留 wm 当天；`by_trade_date`=`atomic_skip`；org=`period` hook 禁 by-date invent；映射+验收 `analysis/data_frontier_detection_system_20260723.md` + `unified_frontier_detection_acceptance_20260723.md`；holders 路径仍 `acquire._sync_holders_aif10`；legacy accept=`assign_unique_holders_row_seq`（防 DUPLICATE_GRAIN 堵 catchup）；A1 drain 1271→0 见 `data_axis_frequency_review_20260724.md` |
| Serve→derive closed loop | Law `analysis/serve_derive_closed_loop_law_20260723.md` + config `serve_derive_closed_loop.yaml`；process `institution_profile` delta-gate + as_of seed；org `repair_accept_from_local_raw` / F6 `min_org_accepted_stocks`；`integrity_observe`；证据 `closed_loop_residual_closure_20260723.md`；pytest `tests/test_pipeline_closed_loop.py` |
| Rewrite must-keep vs delete | 裁决折入 `analysis/FOUNDATION_EXECUTION_PLAN.md` §4：KEEP = sync replace / qfq incremental+full CTAS+in-module compact / landing+skip / delta rebuild；**DELETED** = `rewrite_legacy` True + canary CLI；禁 periodic dedupe/compact fixer |

| Manual single-domain sync/canary/replay | `scripts/chunkyctl sync --domain DOMAIN`；`trade_cal` full generation；`daily`/`stock_st` 须显式 `--start/--end`（同日或 ≤40 交易日）；`--drain` 对三域 inapplicable；其它 disabled/formal 仍 fail closed |
| Shared tooling snapshot | `moth snapshot --repo .` |
| Business/tool assertions | `moth assert --repo .` |
| Coupling/deletion impact | `moth coupling --repo . --impact <name>` |
| Code discovery | `codegraph explore "<question>"` |
| CodeGraph refresh | `codegraph sync .` |
| Doc governance | `PYTHONPATH=backend python backend/scripts/check_doc_governance.py` |
| Doc drift | `PYTHONPATH=backend python backend/scripts/check_doc_drift.py --check` |
| Live continuity | `PYTHONPATH=backend python backend/scripts/check_continuity_integrity.py` (`FAIL` 直接非零；daily/ST 读 `accepted_partition` formal frontier；**F1 typed gaps FIXED** — `hk_holidays`/`event_sparse`/known_empty → 预期空 PASS；应有却缺 FAIL；禁 mute/READY cosmetics；证据 `analysis/continuity_f1_typed_gaps_20260723.md`） |
| Local reviewed commit | `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "<message>"`（WP1：L1/L2/L3；政策=`backend/config/commit_tiers.yaml`；L2/L3 跑与 CI **同 blocking 面** pytest=`ci_pytest_surface.yaml` via `run_ci_pytest.py --tier blocking` — **1 `ci_pytest` gate**，非按用例计独立门；`nightly_paths` 异步；gate 分层见 `analysis/gate_redesign_occams_20260721.md`） |
| Tier1/2 full-universe accept (manual) | `PYTHONPATH=backend python backend/scripts/persist_tier12_full_universe_accept.py --decision-date YYYYMMDD`；cutover-aware（ON 时要求 resolver ACCEPTED_CUTOVER；永不翻 yaml）；form enrich 经 `load_form_rows_exact_day` |
| Phase D ExperimentRun persist (idempotent) | `PYTHONPATH=backend python backend/scripts/persist_phase_d_experiment_runs.py [--force]`；b0_bound + runtime-owned measured_offline；claimable 恒 false |
| Phase F main_rally F0+F1+F2 persist | `PYTHONPATH=backend python backend/scripts/persist_phase_f_experiment_verdicts.py [--freeze] [--force]`；snapshot + B0+B1 verdicts under `data/lineage/phase_f_experiment_verdicts/`（`b0.json`,`b1.json`,`manifest.json`）；≠ StrategyRelease |

已移除的 ChunkyCtl 子命令不是工作流，调用必须返回非零；不要在活文档或生成地图中把任何 retired lifecycle 重新列为 active。

## 5. Current structural defects

| Priority | Defect | Consequence |
|---:|---|---|
| P0 | K accepted + form/qfq/segments/pulse 至 `20260721`（扇区/DC pulse 可能滞后）；legacy raw daily 仍 `20260716`（预期）；`index_dailybasic` 短窗 min_rows 拒写；margin **1b+F4 FIXED** = acquire 日历缺口 land/accept（v3 SSE+SZSE）+ pulse serve→accepted（prefer v3/fallback gen；`promote_gate=PROMOTED` → rzrqye **READY** external_aggregate；应有却缺 UNTRUSTED；覆盖前 typed EMPTY；证据 `margin_f4_promote_gate_20260723.md`）；禁 mass / 假 TRUSTED | 两融列在 accepted 日可用；估值水位可能 stale |
| P0 | E 120d checkpointed measured reject/no-gain；C full-universe accept `20260717`（4989）+ `20260720`（4991）form enrich v1；D FIXED；F0+F1+F2 main_rally B0/B1 reject/`claimable=false`；B-pit 120d shadow **120/120 MATCH**；C/B-pit cutover **ON**（ACCEPTED_CUTOVER / MART_CUTOVER；无 accept/窗外日 fail-closed→legacy）；pulse drill 双轨 form 读已退役 | 下一刀 F3 main_rally B2（market sensing，同 B0 snapshot/folds/costs）**或** stop（非 Optuna / 非松门 / 非 mass backfill / 非静默 cutover / 非 StrategyRelease） |
| P0 | qfq physical lineage FIXED (batch_id/ingested_at/factor_as_of); not execution truth; **F8 FIXED** — default **incremental** (`f_latest` value change → full-history rewrite; unchanged → append); `--full` DROP+CTAS then **in-module** `db_compact` (escape `--no-compact`); incremental skips compact | Pin batch_id; never treat qfq as nominal execution price; ban silent stale pre-rebase history |
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
                  (soft-degrade macOS: store._degraded_summary 单条;
                   dispatcher --skip-macos; wrapper 跳过 rc=1 FAIL 横幅)
```

First establish `DatasetContract` and writer ownership around existing files. Move physical files only when a bounded-context migration has a passing shadow comparison.

Formal 执行契约物理 owner：`population_scope.py`（factory bind + `verify_execution_contract`）、
`formal_execution.py`（domain consumer 注册与 `propagate_formal_execution_contract` identity 门）、
`sync_runner.py`（`_require_formal_population_execution` → 传播成功后 `_refuse_formal_domain_runtime`，
禁止 legacy 落穿）。margin consumer 传播后 reason=`formal_runtime_retired`；无 consumer 仍
`execution_contract_not_propagated`。margin **1b** = acquire Step 2.955 `margin_catchup_acquire`（on_demand bounded；不进 all-due）→ `sync_runner.run_domain`；frontier=`accepted_partition`@current contract_version；v2 live-write frozen；
`margin_population_scope.py` 强制 accepted venues=`SSE+SZSE`（禁 BSE / project_universe；
enabled 时还要求 transport 对齐，否则 fail-closed）。`QuotaExhaustedError` 定义在 `sync_runner`（勿从 `sources.tushare` 导入 — 曾阻断 click-update）。

当前 margin read path 的物理 owner：`margin_evidence.py` 负责固定查询快照，`margin_state.py` 负责
accepted proof，`margin_legacy_reconcile.py`/`margin_reconcile.py` 负责纯比较与现场编排，
`margin_readiness.py`/`margin_projections.py` 只在上层组合结果；依赖不得反向。

共享 accepted evidence 的物理 owner 是 `backend/services/data_sources/accepted_schema.py`；它只拥有
`ingest_batch` / `accepted_partition` 的固定 DDL 与结构验证，不拥有任何 domain completeness、writer、
availability 或 consumer 语义。现有 `dim_trading_calendar` 是 open-day serve projection
（`DIM_ROLE=serve_projection_open_days_only`），不是 accepted immutable generation。

calendar 按 `calendar_contract.py`、`calendar_schema.py`、`calendar_landing.py`、
`calendar_acceptance.py`、`calendar_reader.py`、`calendar_runtime.py` 分责；A2 发表入口是
`publish_accepted_calendar_generation`；生产 sync = S1
`capture_and_land_authorized_calendar_generation` → S2
`accept_calendar_from_landing`（caller-only）；fused
`capture_and_publish_authorized_calendar_generation` **test-only**。名义 K/ST 按
`nominal_ohlcv_*` / `stock_st_*` + 共享 `security_day_partition.py` /
`security_day_capture.py` + 薄编排 `security_day_transport.py` + S4
`security_day_acquire.py`（`provider_tushare` /
`local_legacy_raw_materialize` 仅 land 边界可换）分责；
default sync → acquire → `land_then_accept_authorized_security_day`；fused
`capture_and_publish_authorized_*_partition` **test-only**。CLI：
`chunkyctl sync --domain daily|stock_st`（default land→accept）以及
`--land-only|--accept-from-landing|--land-then-accept`（可选 `--from-local-raw`；
accept 路径跳过 provider auth / acquire；disclosure 三域同 land/accept flags：
`--from-local-raw`（三域；empty_skip）或 provider land（`stk_holdertrade`+`holders_top10` only；
org mass by-date invent banned / daily incremental-by-period）。`holders_top10` land：**ACCEPTED + same `payload_hash` → skip re-land**（防 ~32× append storm；真新内容仍 append-only；禁 bare DELETE landing）。**F3 retention FIXED**（archive 非 latest ACCEPTED → parquet；landing 7.17M→236k≈1.05×；smartmoney compact 6.7→4.3 GiB；证据 `analysis/holders_landing_retention_f3_20260723.md`）。S5 derive（FIXED）+ S7 near-FIXED / stronger PARTIAL：
`chunkyctl derive qfq|form` + form library + pipeline clean/process 默认
accepted-only；`--allow-legacy-fill` 逃生；daily accepted `20190102`→`20260720`
（ST asymmetric `20220104`）；`legacy_raw_plane.yaml` + gate（**23/46 ssot** typed hard-stop wall；B1+B2 done；本阶段不再开 S7 刀）；§15 `pre-knife`；`check_foundation_done.py` FND-GATE（F1–F10 PASS；`phase_closure_ready=true`；F8 §15-VERIFY）。近端：owner-scheduled E/F only（见 `foundation_phase_reeval_20260721.md`；E0-HIST/F6 + FND-GATE + §15-VERIFY PASS）。
S6 serve（FIXED）：
`market_pulse_serve_read` + DataAccess entities；router 零 `# serve-exempt:`；D5 全绿。
`observation_population.py` 的 default
readiness 经 `resolve_eligible_observation_date`（accepted calendar ∩ K/ST
`availability_policy`）评 frontier，不索要周末/节假 calendar-today 分区。
Sync transport 用 `trigger_mode=manual|automatic`（`resolve_sync_eligibility_frontier`）；
consumer/`available_at`/continuity 仍走时钟门 `resolve_availability_frontier`。
`trade_cal`/`daily`/`stock_st` = `authorized_manual_generation` + `on_demand`
（禁 all-due；K/ST 禁 drain）；sync 禁 legacy raw 直写 canonical。margin=`on_demand`+`bounded_calendar_catchup`（1b；禁 thaw/all-due；acquire bounded catchup；硬门仅 `product_blocking`）。

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
