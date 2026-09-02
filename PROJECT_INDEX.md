# PROJECT_INDEX — Current Project Map

> 状态：live navigation，非规则 owner
> 更新：2026-07-27（strategy-lab fail-closed control plane）
> 当前目标看 `goal.md`（foundation CLOSED / `phase_closure_ready`；strategy-lab installed 但 live inputs BLOCKED，正式策略 paused）。
> **执行方案仅两份**：底座 `goal.md「下一步」执行 backlog` · 策略 `goal.md「下一步」执行 backlog + strategy_validation_contract.md §3.2/§3.3`（RX 前 BLOCKED）。台账 `chunkyctl history --grep "文档收敛"`。
> 架构看 `docs/MASTER_TOPLEVEL_DESIGN.md`；机器入口看 `FEATURE_MAP.md` / CodeGraph。
> **Board = 现查投影，零文件**（`scripts/chunkyctl status` / `agent-boot`；`backend/scripts/agent_board_projection.py`）：cutover 行并列 **yaml 意图 + resolver 实际裁决**，背离显式标注 —— 窗口走完后 yaml 仍 true 而 resolver 每日 fail-closed 回 legacy 的可见化；影子期到期由起点+上限**算出**非写死状态串。`BOARD.md` / `data/board/agent_context.json` 及 `agent_board` 漂移门已于 2026-08-11 P2.3 退役（L2 状态只许现查）。

## 1. Authority

```text
AGENTS.md
  -> goal.md
  -> docs/MASTER_TOPLEVEL_DESIGN.md
  -> docs/strategy_validation_contract.md
  -> docs/engineering_governance.md
  -> goal.md「下一步」执行 backlog | goal.md「下一步」执行 backlog + strategy_validation_contract.md §3.2/§3.3  (execution only)
```

历史只查 `scripts/chunkyctl history`（`--grep` 逐刀 / `--eras` 时期）。`CLAUDE.md`、dated analysis 不是 live authority。

## 2. Product map

| Tier | Current owner/package | Current reality |
|---|---|---|
| T0 market data | `backend/services/data_sources/`, `pipeline/`, `calendar.py`, `market_*` | A1–A5 代码完整；accepted daily / ST 起点 `20190102` / `20220104`（契约常量；**frontier 与天数 = 运行时状态，查 `accepted_partition`，禁止在本文件固定**；asymmetric ST raw floor；chunked ≤40d local-raw land→accept）；**S1–S6 FIXED / S7 near-FIXED**：default sync = acquire→caller-only land→accept；`security_day_acquire`；CLI land/accept/derive；derive+form+pipeline clean 默认 accepted-only（`--allow-legacy-fill` 逃生）；`legacy_raw_plane.yaml`+gate（**20/46 ssot + 3 retired** = 2 blocked + 8 serve_l0_declared + 10 sync_orphan；`stk_factor_pro`/`express`/`fina_mainbz` sunset/DROP；**2026-07-24 `stk_holdernumber` RESTORE** by_ann_date+DataAccess+dossier；B1 `dc_member`→`fact_dc_member_daily` observation-date PIT；B2 limit+moneyflow(+dc)+index_daily+top_inst→fact_* + **same-run publish catchup** (`type_b_fact_publish_catchup`, ≤40d window)；SW→PIT；pulse builders→mart；daily_basic→dim；stk_limit→form；stock_basic→dim；adj→qfq）；**B5 FIXED**（registry/qfq + Type-B enrichment；qfq default **incremental** / `--full`+compact）；form/qfq/segments/pulse 跟随 formal frontier（运行时状态，勿写死；legacy raw daily 不再前进 — formal 不写 raw）；margin **1b FIXED** `contract_version=3` SSE+SZSE via acquire on_demand catchup（禁 all-due/mass；补跑仅验证）；无 mass fetch / cutover |
| T0 classification | `taxonomy.yaml`, SW/DC raw tables, `build_sw_industry_view.py` → `v_sw_industry_pit`, DC snapshot builder | namespace 已分离；**SW L1 PIT exclusivity FIXED**（effective `out_date` 闭合重分类/同日双 L1；002310 等）；DC versioned PIT/membership 仍待 Phase 2 |
| T1 stock state | `technical_states/`, `segments.py`, `tier12_publish_{contract,writer,accept}.py`, `tier12_consumer_cutover.py`, `form_production_read.py`, `market_pulse_tier12_read.py`, `tier12_nominal_canary.py`, `tier12_project_universe.py` | 多轴状态可复用；C accept 分 `publish_scope=canary|project_universe`；`resolve_tier12_production_read` + B1/pulse 接线；**cutover yaml=true** → ACCEPTED_CUTOVER；**form enrich v1** + dossier/screener **typed hybrid**（accepted overlay name/pos/trend/breakout；purity/vol/sub=`hybrid_residual_fields`，非纯 accepted）；re-accept `20260717`/`20260720`；无 accept 日 fail-closed→LEGACY/fact |
| T2 market sensing | `market_pulse.py`, `market_pulse_serve_read.py`, `market_pulse_scope.py`, `margin_pulse_promote_gate.py`, `universe_serve_filter.py`, `market_pulse_tier12_read.py`, `tier12_publish_{contract,writer,accept}.py`, `tier12_consumer_cutover.py`, `tier12_nominal_canary.py`, `tier12_project_universe.py`, API/frontend | **沪深A serve 白名单 FIXED**；**breadth READY** as `project_universe_pit`（源=accepted canonical + 板块前缀白名单；无行则 typed EMPTY；2026-08-14 起不再经 b_pit 窗口判定）；**F4 rzrqye READY** as external_aggregate on accepted days（`margin_pulse_promote.yaml`；应有却缺 UNTRUSTED；覆盖前/确认空 typed EMPTY）；form 单轨 production-read |

| T3 institution | `institution_profile.py` + `research_identity.py` + `holder_research_class.yaml` / `holder_capital_role.yaml` / `seat_research_class.yaml` + `strategy_spec.py` + `strategy_paper.py` + `institution_follow_spec_paper.py` + `disclosure_dataset_snapshot.py` + `snapshot_nominal_bind.py` + `one_name_pointer_bars.py` + `institution_follow_b0.py` + `holdout_guard.py` + `research_prereg_store.py` + `org_holding_pointer_integrity.py` + `org_holding_aif10.py`（miaoxiang **sharded aif10** + `pagination_integrity` 100-page cap）+ `org_holding_period_catchup.py` + … | **首个正式策略包骨架**（画像 ≠ `institution_follow_v1` 跟随纸面 ≠ E B0/B4 隔夜动量消融）；跟随 spec 纸面已接 `stk_holdertrade` 公告事件；E0-HIST/F6 PASS；org = **period-gap + population + count-probe MERGE**；auto **N=1/run**；B0–B4 仅接受 snapshot-bound canonical nominal bars；正式顺序 = plan→prereg→pointer preflight→consume→canonical hash/load→measure；stable holdout scope single-touch；typed fixture 永不 claimable；F6 pointer = FULL OUTER + content_hash；disclosure shadow NaN/±inf→null 诚实门（`disclosure_shadow_compare` 防 canonical NaN 残留崩 serve JSON；2026-08-05 实测 285 行 avg_price NaN residual 待上游清洗）；S1 RX 已跑且 `claimable=false`；无 StrategyRelease |
| T3 main rally | `main_rally_dataset_snapshot.py`, `main_rally_setup_paper.py`, `snapshot_nominal_bind.py`, `main_rally_b0.py`, `main_rally_b0_measure.py`, `main_rally_b1.py`, `main_rally_b1_measure.py`, `main_rally_b2.py`, `main_rally_b2_measure.py`, `rally_gt.py`, `rally_detect.py`, rally config/tests + `strategy_packages/main_rally_v1.yaml` | GT 资产成熟；**F0+F1+F2+F3 FIXED**；spec = setup 信号 `rally_setup_pivot_confirmed_base_days`；setup 纸面 = bottom 后下一 open + 命名短窗，**不是** Release / full-episode；禁读 peak outcome；formal measurement 禁 freeze override，名义 accepted generation 必须严格截止 holdout 前且 canonical hash 一致；历史 B0/B1/B2 均 reject/`claimable=false` |
| T3 research lab | `strategy_lab.py` + `strategy_lab_serve.py` + `strategy_lab.yaml` + `check_strategy_lab.py` + `backend/config/strategy_packages/` | **PARTIAL / fail-closed**：development-only `ResearchInputBundle` + local/manual/non-claimable smoke；live disclosure/main-rally freeze 已切到 holdout 前且 `framework_ready`；`local_smoke` 可加载三包 `StrategySpec`；跟随 spec 纸面 / rally setup 纸面 / 公式单名 pointer 已标 lab；披露覆盖分母 = freeze 的 holders/org/stk 分区，**排除** freeze 内 `nominal_ohlcv`；E/F verdict JSON 标 ablation-only；formal RX validators（opaque holdout seal + snapshot hash）已落地，`claimable` 仍 false；Optuna runner / Modal adapter 未实现；观察面 `GET /api/v3/lab/*` 只读压缩投影（永不 `claimable`） |
| T3 formulas | `bestchoice/FROZEN.md` + `evidence_manifest.json` + `strategy_packages/formulas.yaml` + `formula_challenge.py` + `one_name_pointer_bars.py` | 冻结 challenger；S3 合成烟测 + 单名 live pointer（metadata 预检 + `ts_code` 子集，**已测**），`claimable=false`；禁 Optuna CSV / `vwap_tradable_v1` / 裸 dict / holdout / 全宇宙 1553 日 / 全日 hash 冒充；B5 / 吸收 **not_implemented** |
| T4 decision/paper | `paper_portfolio.py`, `frontend/app/` 多页静态站（`DESIGN.md` owner；无 React/Vite）+ `GET /api/v3/lab` 只读压缩投影 | Legacy NONCONFORMING 观察账本；不是 paper execution。站点 = `/app/<space>/<tab>.html`。LAB 现查 lineage/spec，不把打样数字当判决；`claimable=false` |

## 3. Runtime and data layout

| Area | Role |
|---|---|
| `data/tushare_raw.duckdb` | TuShare legacy `raw_tushare_*` compatibility 表 + frozen margin evidence；accepted nominal OHLCV / ST 起点 `20190102` / `20220104`（**frontier 查 `accepted_partition`，勿在此固定**）；legacy `raw_tushare_daily` 不再前进（formal 不写；local-raw materialize → landing 另路径） |
| `data/market.duckdb` | K 线 serving/派生数据；qfq 分析面 max `2026-07-20`（derive+pipeline clean 默认 accepted-only）；≠名义成交价真相 |
| `data/reference.duckdb` | 交易日历、身份/reference 数据 |
| `data/smartmoney.duckdb` | 当前 mart、profiles、ops/control evidence；B2 `fact_stock_limit_daily` |
| `data/feature_store.duckdb` | 特征面（institution_profile / rally_gt DROP 重建）；重建后 `duckdb_compact.maybe_compact_alias`；死块 moth `feature-store-bloat-ratio` <10% |
| `data/experiment_store.duckdb` | 实验 verdict/control；当前不代表完整 research runtime |
| `backend/config/` | 目标只保留 active policy；过渡期 legacy registry 必须显式标 `NONCONFORMING` 并列入 Phase 迁移债务 |
| `data/reports/tooling/` | 可重建工具证据，不是业务真相 |
| `backend/services/duck_adapter.py` | 单一 DuckDB 访问层 `DuckConn`（连接互斥+锁等待计时）；实例暴露 `.db_path` 供观测/诊断 |

精确数据库路径以 `backend/config/database_manifest.yaml` 为准；表、入口和 writer 以 live DB、`FEATURE_MAP.md`、CodeGraph 和 Moth 为准，不在本文件固定计数。

## 4. Important entrypoints

| Purpose | Active entrypoint |
|---|---|
| Session boot context (git+moth+codegraph+board, one page) | `scripts/chunkyctl agent-boot [--format json]`；cutover 行标注 **yaml 意图**，实际裁决由 `check_cutover_effective` 现查；board 段现查生成（无落盘文件），config 缺失时报 error 而非渲染一份全缺省的空板 |
| Health | `scripts/chunkyctl doctor --fast` |
| Manual full data update | UI `#/workbench`「数据更新」→ `POST /api/v3/ops/jobs/daily_update/run`；或 `bash scripts/daily_update.sh --date YYYYMMDD`；acquire **先** `--all-due` drain、**后** formal `daily`/`stock_st` latest-eligible（pending soft / hard→degrade，不 raise 绑架兄弟域；证据 `foundation_acquire_all_due_unblock_20260722.md`）；`stock_st`=ST membership 证据（沪深A **含 ST**；排除仅三板/退市整理/B/BJ；soft-fail≠踢出 ST）；due-plan 预览读 SLA JSON；holders skip-if-wm-unchanged；on_demand 不进 all-due；Cap E S1/S2 → `POST /api/v3/ops/pipeline/land-accept/run`；margin=`on_demand`+`bounded_calendar_catchup`（1b；不进 all-due；不挡 preflight）；**typed `run_outcome`** ∈ {success/soft_waiting_clock/**integrity_observe**/hard_fail} 写入 `data/reports/daily_*.json`（法条 owner=`docs/MASTER_TOPLEVEL_DESIGN.md` §5.4；`services.pipeline.run_outcome` 单一计算点；闭环法：完整性≠等时钟）；exit/wrapper/notify/UI **渲染**该字段——软时钟/完整性琥珀观测、永不红 FAIL；硬挡才 `job FAIL`（证据 `phase1_run_outcome_20260722.md` + `docs/MASTER_TOPLEVEL_DESIGN.md §5.8 (派生新鲜度闭环法)`）；软观测 macOS 横幅按**签名合并**（`store._soft_banner_signature`：date+outcome+reason+classified msgs+SLA stale；per-day marker `chunkymonkey_soft_banner_<date>.marker`）——空点重跑同软态不再刷屏、软变化/成功后再软才重弹（证据 `workbench_incremental_orchestrator_ux_20260722.md`）；**CX-1**：`delta_manifest`（`services.pipeline.delta_manifest`）+ `stage_timing_s`/`budget_status`（config `pipeline_latency_budgets.yaml`）写入 `daily_*.json`；DC 前沿未变则 skip `build_dc_industry_view`，`market_pulse` 迟到窗恒跑；ops idle 暴露 manifest（证据 `cx1_acquire_efficiency_acceptance_20260722.md`）；**CX-2**：`state_sensors` 只读探测 ST 戴帽/摘帽、holders ratio/rank/exit（accepted notice only，禁 MAX invent）、dim_active 退市/入市 → `delta.state_changes` + process_plan `state_change:*` force（不写 Tier0；证据 `cx2_state_sensors_acceptance_20260722.md`）；drain 经 `_run_drain_subprocess`（Popen + stderr pump **实时**写 UI 作业日志 #1 + 管线日志 #2，每域 `[drain i/N]`）——不再 `capture_output` 憋 40min 假死；**manual 探源优先**（`eligible_end=今天`，空<窗→pending_publish 软、空≥窗→fail-closed，`available_after` 仅管 automatic 消费前沿+分类）；org 增量按披露截止**自动前移**（skip 日志带 `next_period`/unlock）（证据 `business_clock_and_drain_rework_20260722.md`） |
| Stock dossier Cap F | UI `#/stock/:code` → `GET /api/v3/stock/{code}/dossier`（`stock_dossier_cap_f_usable`；沪深A；form/stage/holders + episode cycle/return；机构 deep-link closed-loop；tabs ok/empty/delegated）；**资金 tab** → Cap A moneyflow API；证据 `chunkyctl history --grep "dossier"`；pytest `tests/test_stock_dossier_api.py` ∈ blocking |
| Moneyflow decision assist (3A+3C) | Tier3 `GET /api/v3/decision/moneyflow/board` + `/stock/{code}`；config `moneyflow_assist.yaml`；service `moneyflow_assist.py`（读 pulse mart + stock moneyflow facts；`flow_regime`→潜伏/抢筹/出货迹象；窗未满→unknown；**CX-3** 暴露 signed `flow_streak`）；UI `#/market`「资金决策辅助」= **潜伏象限** scatter + 表（Cap A；unknown≠0；地形 Enrich 延后；`phase3_latent_quadrant_mvp_20260722.md`）+ dossier 资金；证据 `capability_a_moneyflow_assist_20260721.md`；感知卡保持零买卖暗示 |
| 交集最强股 decision assist (4D) | Tier3 `GET /api/v3/decision/intersection/strongest` + `/intersection/stock/{code}`；config `decision_intersection.yaml` v1；service `decision_intersection.py`（DC 行业∩概念∩申万 L1 三链；`sw_l1_member_mem_sql` PIT；复用 `moneyflow_assist` behavior）；三链 as-of 不一致/滞后 SLA → `status=stale`；UI `#/market`「交集最强」+ dossier 交集；**CX-3** 板块名 → `sector_membership` facet chip；证据 `capability_d_intersection_strongest_20260721.md` + `plan_residual_reconcile_20260722.md`；pytest `tests/test_decision_intersection.py` ∈ blocking |
| CX-3 briefing + facet serve bricks | Tier3 `GET /api/v3/decision/briefing/daily`（`daily_briefing.py`+yaml；Cap A/B/D trust gate → stale 则 `narrative=null`）+ `GET /sector/members`（`sector_membership_serve` 包 pulse members + DC SLA）+ `GET /moneyflow/stock_streak`（`stock_flow_streak` 连续净流入宇宙）；UI `#/briefing` + Market assist 简报面板 + `#/explore?kind=sector_membership|flow_streak`；证据 `cx3_capability_bricks_acceptance_20260722.md`；pytest `tests/test_cx3_capability_bricks.py` ∈ blocking |
| 形态/阶段选股面 (5B) | Tier3 `GET /api/v3/screener/options` + `/form_stage`；config `stock_screener.yaml`；`stock_screener.py` + 共享 `form_production_read`（与档案 F 同一 production-read：fact brick + ACCEPTED_CUTOVER overlay；`sql_where_active_a_share`）；全局 `MAX(trade_date)` SLA → `status=stale`；UI `#/market`「形态/阶段选股」→`#/stock/:code`；证据 `capability_b_stock_screener_20260721.md`；pytest `tests/test_stock_screener.py` ∈ blocking |
| 主升浪 GT 重建 | `scripts/chunkyctl derive rally-gt [--data-end YYYYMMDD]` → `services.rally_gt.rebuild`，DROP 重建 feature_store 三表 (`fact_rally_ground_truth`/`fact_rally_negative`/`fact_rally_strata`)。**刻意不走 `derive_runtime`**：`test_derive_runtime_s5.py:32` 锁死 `DERIVE_TARGETS == {qfq, form}`，且该模块自述范围是「S5/S7 accepted-only，独立于 acquire」，而 rally-gt 从 K 线+特征面派生 Tier3 GT，语义不同 —— 故沿用 derive 命名空间但实现为 `derive_cli.main()` 的并列分支，CLI 层零业务逻辑；新测试同时锁住「rally-gt 不得进入 DERIVE_TARGETS」。**GT 时间上界是结构性钉死的，不是陈旧**：`holdout_policy.holdout_start` → `training_cutoff_before_holdout()` → 再减 `rally_gt.yaml` 的 `max_forward_days` 个交易日前向完整性禁令；上游 qfq/B1/B2/canonical 均为当期，不是瓶颈。前沿数值查 `scripts/chunkyctl status`，勿在此固定；pytest `tests/scripts/test_derive_cli_rally_gt.py` ∈ blocking |
| 数据身份 vs 获取渠道 (三层模型) | 法条: 身份 `sync_registry.yaml` 域级 `identity`（`kind` ∈ exchange_fact/derived_rule/vendor_view/undecided；`vendor_view` **必带 `judge`**=谁的判断，换 judge 即换数据；事实类**禁带任何机构名**）· 渠道 `source` + `fallback_channels`（只许传输轴键，出现 grain/target_table 等语义轴键即 ValueError）· 批次 `ingest_batch.source_name`（落地封印，永不重打）。合并点仍只有 `sync_runner.domain_spec()`（`_channel_views` 只准它调用，测试锁）；通道校验五条 fail-closed（观点类不许声明 fallback / 缺 source|api / 含语义轴键 / `system_evidence` 空 / 源未在 `sources` 段登记）。`sources` 段 7 源全登记 `kind`（network_vendor / local_derive）。当前 42 域 = 19 exchange_fact / 16 vendor_view / 5 derived_rule / 2 undecided（`adj_factor`/`daily_basic` 待口径核对；因 kind 不在白名单，**它们无法声明 fallback**）；pytest `tests/services/test_domain_spec_channels.py` ∈ blocking |
| 契约戳一致性门 (发现式，非清单式) | `PYTHONPATH=backend python backend/scripts/check_contract_stamp_consistency.py [--only N] [--db-override alias=path]`；五类检查：①指针 vs 现算契约 ②canonical vs 指针 ③`data/lineage` 戳记录 vs 现算契约 ④`ingest_batch` **payload_hash 重算**（用生产函数，不重实现；`contract_hash`/`config_hash` **允许**停在落地时刻——门查的是「有人改了它却没重算 payload_hash」）⑤**文件级整文件摘要钉住**（字段级之上还有一层，纯字段扫描看不见）。扫描范围由 `database_manifest` + 结构形状**发现**，非硬编码库/键清单；库可达但缺表 → **FAIL 不是跳过**（`--allow-missing-db` 是显式逃生口）。**尚未接进 `safe_commit`**（phase_e 5 条 FAIL 未闭合前接线会卡死全部提交）。实现分四处：入口+CLI+check⑤ 在脚本本身，共享类型 `services/data_sources/stamp_types.py`，发现式扫描机制 `stamp_discovery.py`，检查①②③④⑥ `stamp_checks.py`（check⑤ 留在脚本内是被测试的 `monkeypatch.setattr(mod, "REPO", ...)` 绑定的——函数的 globals 解析到定义它的模块，搬走会让那条 patch 失效）；pytest `tests/scripts/test_check_contract_stamp_consistency.py` ∈ blocking |
| 取数失败分层判据 | `services/data_sources/fetch_verdict.py`：①供应商业务错误码/异常属性 → ②Python 异常类 → ③`sync_runner` 中文文案表（**仅兜底**）。必然分层非替换：tdxhub 裸 TCP 无状态码只能靠异常类；tushare 经 tinyshare 只吐中文散文，文案是它**唯一**信号。`FailureKind` ∈ hard_wall/auth_expired/transient/structural/unknown；**UNKNOWN 是诚实缺省不是垃圾桶**（走既有有界重试，与改动前一致；高发=该补该源 classify 的可观测信号）。**按成员细分不按类整档搬**：baostock `ACCOUNT_PERMISSION` 混装黑名单(10001011,真墙)与未登录(10001001,重建即可)；tushare 一个异常类 6 种 reason 含 `auth_probe_unavailable`(=网络问题)；pytest `tests/services/test_fetch_verdict.py` + `test_fetch_retry_layering.py` ∈ blocking |
| Calendar/ST identity recon (no primary cut) | `PYTHONPATH=backend python backend/scripts/recon_calendar_identity.py` vs accepted `canonical_sse_trading_calendar_generation` / `canonical_stock_st_daily`；`raw_tushare_trade_cal`/`stock_st` 不当尺子；suspend = `blocked_no_publication` |
| Taxonomy four-chain recon (no primary cut) | `PYTHONPATH=backend python backend/scripts/recon_taxonomy.py`；DC 尺子=`fact_dc_member_daily`，SW 尺子=`v_sw_industry_pit`，THS=扶摇当前成分（禁区间 PIT）；同名≠身份；通达信 block 不进四链 |
| Finance/margin recon (no primary cut) | `PYTHONPATH=backend python backend/scripts/recon_fina_margin.py`；两融尺子=`canonical_margin_exchange_daily` SSE+SZSE（明细加总 ≠ 交易所身份；禁 `raw_tushare_margin`）；财务=`raw_tushare_income`/`balancesheet` 落地孤儿 vs 妙想 GINCOME/GBALANCE 抽样（PIT=公告日；禁 gpcw 复活） |
| Chip overlay vs cyq_perf (no primary cut) | `PYTHONPATH=backend python backend/scripts/recon_cyq.py`；尺子=`canonical_nominal_ohlcv_daily` + `daily_basic.turnover_rate_f`；方法=`turnover_overlay_v1`（名义坐标，不是持仓）；挑战者=`raw_tushare_cyq_perf`（L0 声明非 accepted；禁 qfq K；不进公式 `winner_rate`） |
| Moneyflow two-layer recon (no primary cut) | `PYTHONPATH=backend python backend/scripts/recon_moneyflow.py`；三名分列：日终 `fact_stock_moneyflow(_dc)_daily`、盘中分钟代理=`blocked_no_publication`、通达信 `transactions` 主动差额抽样；禁跨源加总；截断诚实 |
| Structure-product recon (no primary cut) | `PYTHONPATH=backend python backend/scripts/recon_structure_products.py`；公式下一开盘、主升浪 B0/B1 setup-only、跟随公告日 PIT；`claimable=false`；禁同日收盘入场 / 禁 B3+ 必经 / 禁 Optuna |
| Assignment-gap recon (no primary cut) | `PYTHONPATH=backend python backend/scripts/recon_assignment_gaps.py`；补测代码表/估值/龙虎榜/户数/大宗/解禁/调研/涨停池/指数收盘；无等价产品记 `product_mismatch`；禁切 `primary` |
| TDX HQ host catalog | Official = TDX client `connect.cfg` `[HQHOST]` via `TDXHUB_CONNECT_CFG` (read-only parse, never `bestip`); `tdxhub.consts.HQ_HOSTS` is a frozen snapshot of those official client/broker names (tdxhub 2026-04-11 liveness filter, 2026-04-13 merge) — not a live HTTP catalog; pin `TDXHUB_HQ=ip:port` when a handshake-ready host is known |
| Serve→derive closed loop | Law `docs/MASTER_TOPLEVEL_DESIGN.md §5.8 (派生新鲜度闭环法)` + config `serve_derive_closed_loop.yaml` + `data_sources/pagination_integrity.py`（paginated land ≠ complete；**hard** trunc=page-cap/provider_count；**soft** `under_modern_baseline` 不进 repair queue；证据 `org_heuristic_soft_baseline_20260725.md`）；process `institution_profile` delta-gate + as_of seed；org `repair_accept_from_local_raw` / `provider_truncated`→单期 sharded repair / F6 `min_org_accepted_stocks`；证据 `org_provider_page_cap_fix_20260724.md`；… |
| Factor-family inventory (RX 前) | Config `factor_family_inventory.yaml` + `check_factor_family_inventory.py`（结构门）+ `check_factor_family_gates.py`（frequency continuity 矩阵）+ `project_factor_family_frontiers.py`（绑定 inventory hash/freshness/status 的 K3 live defer 投影）+ `check_factor_family_frontier_live.py`（DB missing/query error/UNVERIFIED/stale 均 fail-closed）+ 设计 `docs/MASTER_TOPLEVEL_DESIGN.md §9.1 (因子族边界) + strategy_validation_contract.md §3.1 (窗口对齐)`；K3 收口证据 `95cfd2697`（细节 `chunkyctl history --grep factor-family --full`） |
| Brick registry (B5) | `brick_registry.yaml` + `check_brick_registry.py`（L2/L3 FeatureBlock + Type-B；moth claim PASS）；权威 `docs/MASTER_TOPLEVEL_DESIGN.md §5.5 (变量积木分层)` |
| Rewrite must-keep vs delete | 裁决折入 `goal.md「下一步」执行 backlog` §4：KEEP = sync replace / qfq incremental+full CTAS+in-module compact / landing+skip / delta rebuild；**DELETED** = `rewrite_legacy` True + canary CLI；禁 periodic dedupe/compact fixer |

| Manual single-domain sync/canary/replay | `scripts/chunkyctl sync --domain DOMAIN`；`trade_cal` full generation；`daily`/`stock_st` 须显式 `--start/--end`（同日或 ≤40 交易日）；`--drain` 对三域 inapplicable；其它 disabled/formal 仍 fail closed |
| Shared tooling snapshot | `moth snapshot --repo .` |
| Business/tool assertions | `moth assert --repo .` |
| Coupling/deletion impact | `moth coupling --repo . --impact <name>` |
| **L2 运行时状态（唯一现查入口）** | `scripts/chunkyctl status`（人读）/ `--json`（agent）；owner=`backend/services/project_status.py`。给出 accepted 前沿 + **距最近已完成交易日的交易日滞后**、源水位、cutover 声明 vs 实际、门分布、告警 flag。**零文件、不缓存、退出码恒 0**（报事实不做裁决；红绿归 continuity / watermark SLA / `check_cutover_effective` 各自的门）。人工维护文档**禁止再抄这些值**，只许指向本命令 —— 执法 `check_doc_runtime_state`（第 20 道门，scaffold warn-only）+ `backend/config/doc_runtime_state.yaml` |
| Git hook（`git commit` 直调的兜底路径） | `configs/git-hooks/{pre-commit,commit-msg}`（**入 git、可审查**）；`git config core.hooksPath configs/git-hooks` 新克隆一次性设置。后果与 `safe_commit` **同源**：读 `gate_policy --names scaffold`，scaffold 组只 warn。2026-08-11 前 hook 只存在于各自机器的 `.git/hooks/`，不入版本且与门分组打架 |
| 门分布策略 / 运行时自检 / 脚手架收口 | `scripts/chunkyctl gates`（分组表）· `gates --check`（登记表 ↔ `classify_commit_tier` ↔ `safe_commit.sh` 兜底串 三处门名对账）· `gates --run-system-health`（手动跑 `runtime_checks` 组：continuity/residual_hygiene/grain_uniqueness/**cutover_effective**）· `scripts/chunkyctl scaffold-fix`（重生 FEATURE_MAP/BOARD + 报人工缺口）；owner=`backend/config/governance_gates.yaml` + `backend/services/governance_gates.py`；条文 `docs/engineering_governance.md` §14.1 |
| Cutover 声明 vs 实际裁决 | `PYTHONPATH=backend python backend/scripts/check_cutover_effective.py`；把**最近已完成交易日**（日历真相源，非 wall-clock）送进 tier12 production resolver：tier12 逐日 accepted 依赖是 config 写明的预期回落故=WARN；日历不可达=UNVERIFIED（exit 2，不算通过）。已挂进 `daily_update` store |
| Code discovery | `codegraph explore "<question>"` |
| CodeGraph refresh | `codegraph sync .` |
| Doc governance | `PYTHONPATH=backend python backend/scripts/check_doc_governance.py` |
| Doc drift | `PYTHONPATH=backend python backend/scripts/check_doc_drift.py --check` |
| Live continuity | `PYTHONPATH=backend python backend/scripts/check_continuity_integrity.py` (`FAIL` 直接非零；daily/ST 读 `accepted_partition` formal frontier；**F1 typed gaps FIXED** — `hk_holidays`/`event_sparse`/known_empty → 预期空 PASS；应有却缺 FAIL；禁 mute/READY cosmetics；证据 `chunkyctl history --grep "typed gaps"`）；**F9 residual hygiene** `check_residual_hygiene.py` + `residual_hygiene.yaml`（Type-B raw→fact + ann tip vs eligible；store 2.985；超 SLA → degraded+ALERT；缺库/CI offline → skip PASS 不 degrade；≠ Continuity READY 化妆；证据 `chunkyctl history --grep "residual_hygiene"`）；**org accepted pointer** FULL OUTER + content_hash（F6 `org_pointer_mismatches`；repair `repair_org_holding_accepted_pointers.py`；证据 `chunkyctl history --grep "org pointer"`）；**2026-07-25 full audit** `chunkyctl history --grep "full audit"`；**dual-plane faucet FIXED** → **holders fact DROP FIXED** `chunkyctl history --grep "holders fact retire"`（canonical notice SSOT；names=`dim_active_a_stock`） |
| Watermark SLA 判不出 = UNVERIFIED (非 0) | `services/calendar.py` `trading_days_since` 三种情形返 **None**：日历取不到 / **today 超出日历覆盖** / **数据日期晚于 today**；调用方 `update_watermark_sla.py:636` `measured_days is None` → `SLA_UNVERIFIED` + alert（日志按三种成因分辨措辞）。**2026-08-23 去掉 `max(0,...)` 钳位**——它把「不可能」钳成「完美」：0 不只是不告警，读起来还是「零延迟＝最新鲜」。且**不需要脏数据就会触发**：today 落在日历末端之后（跨年未续订）时，它与任何近端日期的 bisect 位置同为末端，差恒为 0——实测日历止于 2026-12-31 而 today=2027-03-01 时，真停更两个月报「落后 0 个交易日」，且是**全域同时**失效。判据须**直接比日期**，非交易日的未来值（bisect 位置与 today 重合，差为 0 非负）会从 `diff<0` 漏网。同形态钳位另有两处未修：`check_continuity_integrity._lag_trading_days`、`residual_hygiene.trading_lag_days`（后者是那 17 个 continuity 盲区域的唯一覆盖） |
| Date-bounds audit (read-only) | `PYTHONPATH=backend python backend/scripts/audit_date_bounds.py [--json]`；扫全部 44 域**实际存在**的日期列（`duckdb_columns()` 探列，一表多列全查），报告落在 `[19900101, today+5y]` 之外的值。**audit 模式恒 exit 0**。边界取「荒谬值」而非「业务期望值」：下界不用 `data_start`（那是采集轴起点，报告期/解禁日天然可早于它——初版这样取，44 域报出 328,266 行而只有 3 行真异常）；上界留 5 年（限售解禁最长 36 个月）。同表同列去重计数（`index_member_all` 与 `_hist` 指向同一物理表）。现存量 16 行：`share_float` 一行 9.5 年锁定期疑年份错位、`stk_holdernumber` 6 行已知脏数据（值为 NULL 故消费方已过滤）、`index_member_all` 9 行申万用公司早期日期（良性） |
| Exemption audit (read-only) | `PYTHONPATH=backend python backend/scripts/audit_exemptions.py [--json]`；扫 `sync_registry.yaml` 四类豁免（`known_empty_days` / `verified_low_days` / `gap_tolerance` / `row_dip_tolerance`），按「有无理由/owner/到期日」排风险。**audit 模式恒 exit 0，不拦截**——先看清存量再谈收紧（eng_gov §15.4：新建判据本身是高危对象）。理由取**同行尾注 + 缩进续行**，不向上找：向上找会同时造成假警报（写全了理由的被判无）与假绿（借走上一字段的理由），两者均已实测并由 `test_field_does_not_borrow_neighbour_reason` 锁死 |
| Local reviewed commit | `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "<message>"`（WP1：L1/L2/L3；政策=`backend/config/commit_tiers.yaml`；L2/L3 跑与 CI **同 blocking 面** pytest=`ci_pytest_surface.yaml` via `run_ci_pytest.py --tier blocking` — **1 `ci_pytest` gate**，非按用例计独立门；`nightly_paths` 异步；gate 分层见 `chunkyctl history --grep "gate redesign"`；**2026-08-10 自述型门降级**：`rule10` 只阻断显式 `Codex-Reviewed: REQUEST_CHANGES`（缺 APPROVE 仅提示）、`commit_msg` GROUP A/B/D 降为提示（subject <10 字符仍阻断）—— 二者唯一输入是提交者自写文本，无法验证审查/测试是否真发生；读代码与数据的 17 道实质门未动，条文见 `AGENTS.md` §9 + `docs/engineering_governance.md` §14；**2026-08-11 门重新分布 P1**：分组=`backend/config/governance_gates.yaml`，与 tier 正交 —— `diff_correctness` 10 门阻断、`system_health` 2 门（grain/continuity）commit 不跑改由 `daily_update` store 自检、`scaffold` 7 门 warn-only 配 `chunkyctl scaffold-fix`；策略文件不可读 → 全阻断 fail-closed；条文 §14.1） |
| Tier1/2 full-universe accept (manual) | `PYTHONPATH=backend python backend/scripts/persist_tier12_full_universe_accept.py --decision-date YYYYMMDD`；cutover-aware（ON 时要求 resolver ACCEPTED_CUTOVER；永不翻 yaml）；form enrich 经 `load_form_rows_exact_day` |
| Phase D ExperimentRun persist (idempotent) | `PYTHONPATH=backend python backend/scripts/persist_phase_d_experiment_runs.py [--force]`；b0_bound + runtime-owned measured_offline；claimable 恒 false |
| Strategy Lab readiness (read-only) | `PYTHONPATH=backend python backend/scripts/check_strategy_lab.py --framework --json`；区分 `framework_installed` 与 `framework_ready`；当前两份 live input 不合格时返回 rc=2，绝不把 control-plane installed 洗成策略可跑 |
| Phase F main_rally F0+F1+F2 persist | `PYTHONPATH=backend python backend/scripts/persist_phase_f_experiment_verdicts.py [--freeze] [--force]`；snapshot + B0+B1 verdicts under `data/lineage/phase_f_experiment_verdicts/`（`b0.json`,`b1.json`,`manifest.json`）；≠ StrategyRelease |

已移除的 ChunkyCtl 子命令不是工作流，调用必须返回非零；不要在活文档或生成地图中把任何 retired lifecycle 重新列为 active。

## 5. Current structural defects

| Priority | Defect | Consequence |
|---:|---|---|
| P0 | K accepted + form/qfq/segments/pulse 前沿 = **运行时状态，现查 `scripts/chunkyctl status`**（扇区/DC pulse 可能滞后；legacy raw daily 不再前进属预期，非缺陷）；`index_dailybasic` 短窗 min_rows 拒写；margin **1b+F4 FIXED** = acquire 日历缺口 land/accept（v3 SSE+SZSE）+ pulse serve→accepted（prefer v3/fallback gen；`promote_gate=PROMOTED` → rzrqye **READY** external_aggregate；应有却缺 UNTRUSTED；覆盖前 typed EMPTY；证据 `margin_f4_promote_gate_20260723.md`）；禁 mass / 假 TRUSTED | 两融列在 accepted 日可用；估值水位可能 stale |
| P0 | E 120d checkpointed measured reject/no-gain；C full-universe accept `20260717`（4989）+ `20260720`（4991）form enrich v1；D FIXED；F0+F1+F2 main_rally B0/B1 reject/`claimable=false`；C cutover **ON**（ACCEPTED_CUTOVER；无 accept 日 fail-closed→legacy）；**B-pit 整层 2026-08-14 退役**（实测与生产 mart 逐日全等、risk_on 翻转 0 次）；pulse drill 双轨 form 读已退役 | 下一刀 F3 main_rally B2（market sensing，同 B0 snapshot/folds/costs）**或** stop（非 Optuna / 非松门 / 非 mass backfill / 非静默 cutover / 非 StrategyRelease） |
| P0 | qfq physical lineage FIXED (batch_id/ingested_at/factor_as_of); not execution truth; **F8 FIXED** — default **incremental** (`f_latest` value change → full-history rewrite; unchanged → append, do not stamp history); `--full` DROP+CTAS then **in-module** `db_compact`; incremental compact when `free_blocks% ≥ 10` (escape `--no-compact`) | Pin batch_id; never treat qfq as nominal execution price; ban silent stale pre-rebase history |
| P0 | Legacy DC PIT residue lacks exit/re-entry/type; writer retired | Existing DB view cannot be used as historical taxonomy truth |
| P1 | formal `boundary_inventory` 仅为静态/测试资源，非 doctor readiness 证书（`formal_boundaries` 文案已澄清）；canary_pending 域无 countdown 出口 | 豁免不可见即永久；须在 goal.md 跟踪 canary 授权点 |
| P1 | Live DC snapshot/pulse tables predate namespace fix until manual rebuild | Code contract is fixed but stored rows still need controlled reconciliation |
| P1 | Market pulse mixes taxonomy, measurements, rolling/regime, write/read；仍读错误 scope raw | B-ext FIXED；B-pit 两轨+窗口 2026-08-14 整层退役 —— 影子比对被证实是自比自（同一 membership 同一批行），与生产 mart 逐日全等 |
| P1 | Stock state/market regime rows lack config/input version | Historical outputs cannot prove which definition produced them |
| P1 | Phase D FIXED — runtime-owned measured offline (`research_runtime_measure`) + lineage `measured_offline.json`；StrategyRelease 仍禁 | Strategy evidence still cannot reach decision/product safely |
| P1 | Strategy Lab：development freeze 已 READY；formal RX validators 已落地（opaque holdout seal）；evaluator/artifact reducer 仍薄；Optuna runner / Modal 未实现 | S1→S2 同 protocol remeasure（诚实 reject 也算交付，≠ Release）；禁开 Optuna runner / StrategyRelease |
| P1 | Docs/CLI gates previously treated retired/warn as PASS；事实性断言（如“仅 TuShare”）不在 gate 覆盖 | Tooling green did not prove executable reality |
| P0 | **CI 覆盖面本身是子集**：`ci_pytest_surface.yaml` 的 `ci_test_optional` 里 **75 个测试文件**自 2026-07-20 被排除（理由「未做离线安全分诊，之后逐片提升」，六周零提升）；`nightly_paths` **0 项** = 无第二道网，"optional" 实际等于永不运行。2026-09-02 实测这 75 个在 HEAD 上 **17 红**（多为测试腐烂：真函数加参数/配置有意改而测试替身与期望未同步，非生产坏）。名单覆盖 `sync_runner_*`(4)/`pipeline`/`margin_state`/`market_db_canonical_kline`/`rally_gt`/`by_trade_date_*`(3)/`calendar_*`(4) 等核心 | 「完整 CI 全绿」是**假完整感**——声称时必须同时说分母；改这些模块的热路径必须手动补跑被排除文件，否则回归不可见 |
| P1 | **申万分层在分类学意义上不是 PIT 的，且是不一致地不 PIT**：`raw_tushare_index_member_all` 只含申万 **2021 版**（一级行业 31 个；2014 版为 28 个，其独有名称「化工/电气设备/休闲服务/采掘/纺织服装/商业贸易/交运设备」在本表零行）。两种语义混在同一张表且**无字段可区分**：个股在既有行业间迁移**确实**留带日期的迁移记录（`is_new='N'` 区间），但 2021 版**整行业拆分是无痕改标**——被拆出的新一级行业（煤炭/石油石化/环保/美容护理）的成分股只有一行，`in_date` 直接回填到上市日，而这些行业 2021 年才存在。后果落在 `fact_rally_strata` 的 `sw_l1`/`sw_l2`。**重测**（数值属运行时状态，不在此钉死）：数被拆出的四个一级行业里 `in_date` 早于 2021 版实施月的行数，及 `fact_rally_strata` 中 `bottom_date` 早于该月、而 `sw_l1` 属这四个行业的样本占比；确切查询与实测留痕见 `backend/services/rally_gt.py` 模块 docstring 与 `chunkyctl history --grep "数据身份与获取渠道分离"` | 不是价格/收益泄漏，是**分层变量里的事后视角**（2021 版的线是看过其后多年表现才画的）。描述性分层可控；`sw_l1`/`sw_l2` **一旦被当特征或负样本配对键使用即成泄漏**，那一刻必须先解决本条。换源不改变它——申万官方发布的也是当前版分类。边界与实测细节写在 `backend/services/rally_gt.py` 模块 docstring |

The current migration and blockers are maintained only in `goal.md`.

Live continuity 已于 2026-08-11（P1 门重新分布）从 commit 路径归位到 `daily_update` 的
`system_health` 自检（`READY / DEGRADED / UNVERIFIED / BLOCKED` 现由日更报告与
`chunkyctl gates --run-system-health` 给出）。**commit 不再产生也不再消费任何 live
readiness 声明**；每个非 READY 状态仍阻断 Tier0 消费/发布，必须由单独一次 continuity
重跑闭合。

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
org mass by-date invent banned / daily incremental-by-period）。`holders_top10` land：**ACCEPTED + same `payload_hash` → skip re-land**（防 ~32× append storm；真新内容仍 append-only；禁 bare DELETE landing）。**F3 retention FIXED**（archive 非 latest ACCEPTED → parquet；landing 7.17M→236k≈1.05×；smartmoney compact 6.7→4.3 GiB；证据 `chunkyctl history --grep "landing retention"`）。S5 derive（FIXED）+ S7 near-FIXED / stronger PARTIAL：
`chunkyctl derive qfq|form` + form library + pipeline clean/process 默认
accepted-only；`--allow-legacy-fill` 逃生；daily accepted 起点 `20190102`
（ST asymmetric `20220104`）——**前沿是运行时状态，现查 `scripts/chunkyctl status`，禁止在本文件写死**（2026-08-11 实测：此处原写 `→20260720` 而真相源已是 `20260804`，正是 2026-08-10 审计点名、上一轮清理漏掉的那一处）；`legacy_raw_plane.yaml` + gate（ssot/compatibility/retired **计数现查** `check_legacy_raw_plane.py`，勿抄；2026-08-11 实测此处原写 `23/46` 而实跑 `ssot=20`，与本文件 T0 行的 `20/46` 自相矛盾；B1+B2 done；本阶段不再开 S7 刀）；§15 `pre-knife`；`check_foundation_done.py` FND-GATE（F1–F10 PASS；`phase_closure_ready=true`；F8 §15-VERIFY）。近端：owner-scheduled E/F only（见 `docs/MASTER_TOPLEVEL_DESIGN.md §11 (FND-GATE 十维)`；E0-HIST/F6 + FND-GATE + §15-VERIFY PASS）。
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
- `analysis/` is not a second docs tree; its target is **zero files**. Rules belong in the owner contracts, history belongs in git commit messages (`chunkyctl history`).
- `.agents/skills/` mirrors generic skills and is not the project policy owner; project-specific skills live under `/Users/dp/.codex/skills/chunkymonkey-*`.
