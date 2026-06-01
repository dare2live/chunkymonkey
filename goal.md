# ChunkyMonkey Goal

> 这是当前目标的权威入口。每当新的验证结果、数据源状态、门禁结果或 blocker 发生变化，必须先更新本文件和对应 handoff，再继续沿用旧计划，避免在过期目标上循环。

## 2026-05-27 — 架构重构当前执行计划 (承接 handoff, 覆盖旧 M0/M4/M5 顺序)

> 当前阶段是 **先架构后业务**。本节是接手 `analysis/handoff_20260527.md`、
> `analysis/codex_bootstrap_20260527.md`、`docs/PROJECT_CONSTITUTION.md`、
> `docs/architecture_reform_context.md` 后形成的执行计划。`docs/implementation_plan.md`
> 已同步为同一架构优先口径。

### 当前 WARN / 风险先记

| 项 | 当前状态 | 风险/决策 |
|---|---:|---|
| `check_universe_filter.py --all` | **PASS: CLEAN (767 production files)** | non-test gate 已清零；`--include-tests` 仍可审计 fixture 引用 |
| `dim_active_a_stock` 定位 | non-test 直接引用已治理 | 只能做 code->name/cache/data-sync/schema/meta，不做 universe 真相源 |
| `universe_governance` | **PASS scoped audit**: `services.universe` / checker / recommendation universe / label universe 通过 test-tool audit、py_compile、per-file complexity、CodeGraph sync、`check_universe_filter --all`、`test_universe.py` + checker tests | ST SQL 过滤已改为从 `universe_rules.yaml` 的 `st_name_patterns` 生成；`dim_active_a_stock` 仅用于 ST 名称映射 evidence，不作为活跃 universe 真相源 |
| Rule 10 | 已在 `safe_commit.sh` 增加 Step 4.5 blocking gate，并补 `SAFE_COMMIT_NO_PUSH=1` 本地提交模式；`test_safe_commit.py` 6 passed | `.py` staged commit 必须被 `Codex-Reviewed: APPROVE/APPROVE_WITH_NOTES` 或 8+ 字符 skip reason 硬约束；`REQUEST_CHANGES` 不可放行；dirty 收束可先本地 safe commit，不为清理工作区被迫 push |
| 顶层设计审查 | **APPROVE_WITH_NOTES / 未最终闭环** | `docs/engineering_governance.md` 已补“二阶审查”，确认流程/功能/模块/数据表/配置文件这套管理方式本身合理；但 freshness/PIT/complexity gate 未全闭环，不能当架构完成证书 |
| 硬编码治理 | 已纳入 `AGENTS.md`、bootstrap、top-level design review 和 chunkymonkey skills | 业务规则/阈值/source catalog/数据源优先级默认归 config/table/service；Python 只留测试夹具、数学常量、schema/enum、SQL DDL 或有证据的例外 |
| 删除治理 | 已纳入 `AGENTS.md`、top-level design review 和 chunkymonkey skills | 经 CodeGraph + `rg` + 测试/审计验证可删的代码必须真删；不允许用注释、隐藏开关、改名、dead branch 或空壳保留来掩饰残留 |
| 数据需求契约 | **PASS scoped audit / 生产库未写**: `tdx_data_need_coverage.yaml` 已把 `grain`、`pit_key`、`freshness_sla`、`evidence_status`、`production_eligibility` 升级为每个 need 的必填契约；`audit_tdx_data_need_coverage.py` 缺字段/非法 enum/eligible+unknown PIT 会 FAIL，DDL 与 `schema_marts.py` 已补新列 | 当前只验证配置 loader 与临时 DuckDB 物化，未写生产 DuckDB；后续 source 增删改必须先登记 need contract，再走 source probe、PIT/freshness、exact-sync 和 consumer eligibility |
| 系统级数据健康审计 | **PASS: 0 red / 0 yellow / 342 total**: `scripts/chunkyctl doctor --fast` 现在把 `data_health_snapshot.py --dry-run --format json` 作为系统门禁的一部分；审计已按 `quality_gate_level` 把 `warning/monitor_only` 资产降到黄，blocking 红表已清零，`raw_margin_daily` 缺表也已降为 monitor-only 黄告警；2026-06-01 还把 `last_writer_at` 写入路径做了 timestamp normalization，`data_health_snapshot.py` 不再被紧凑时间戳格式卡死，官方 `cron_daily.py` 也已跑完全流程并把 `sync_raw` 60s budget timeout、health critical 0 red / 17 yellow / 341 total 这些问题显式吐出来；红/黄条目现在还会带 `writer_prompt` / `owner` / `sync_step`，便于系统自己把问题提示到对应写入端。2026-06-01 先用 writer-lane 方式刷新了 feature panel lane，`fact_feature_panel` / `mart_feature_panel_validation` / `mart_feature_panel_prune_run` 已从红里退出，随后 `capital_behavior` lane 成功补齐 `dim_capital_behavior_latest`，holder/shareholder-plan lane 也已通过 parse-raw-only + backfill + mart 重建清出红区；GPCW 与 raw_aif10 已退回 yellow maintenance，不再是 blocker，说明按 lane triage 比逐表补修更有杠杆；这轮还把 `blocking_yellow` 单列出来，随后又把 `mart_p0b_lambdamart_v6_predictions` 的 `ensemble_v7_phase7_context_v1` 在 2026-06-01 重新写回最新 built_at，确认 blocking yellow 已清成 0；2026-06-01 另将 `raw_executive_trade` 与 `fact_executive_trade_event` 以本地 writer 全量刷新，随后 `sync_surveys` 增量刷新 `raw_institution_surveys` 与 `mart_stock_survey_activity`, 最终健康回到 0 red / 0 yellow / 342 total；`mart_architecture_cleanup_plan` 现已通过 seed 语义修正改为 on-demand governance，不再计入 yellow；后续 data-health 已全绿，没有 remaining yellow 维护项；2026-06-01 新一轮官方 `cron_daily.py --full-sync` 也已验证 `sync_raw` 的 10-count / 30-second 进度节奏，`raw_fetch` 从 0/5201 跑到 5201/5201 后顺利进入 lineage / watermarks / topk / selection_log / selection_outcome / selection_summary / formula_weights / health / drift / audit，整轮 31/31 完成，`health` / `drift` / `audit` 均为 ok；2026-06-02 backend server 恢复后重跑的最新 `cron_daily.py --full-sync` 再次完成全流程，最终只剩 `watermarks:warn`，`raw_tdx_f10_holder_research` 与 `fact_top10_holder_period` 的最新 `fetched_at` 分别推进到 `2026-06-01 19:14:51` / `2026-06-01T19:14:57+00:00`，并且 28 条最可疑 holder raw 页做事务内 `parse -> write_one -> rollback` smoke 全 PASS，说明这次 holder/gap 修复没有引入新的 Python crash；另外，`fact_top10_holder_period` 的 legacy `idx_fact_hp_*` 索引已由启动清理删去并保留 canonical `idx_t10_*`，因此之前那条 `F10 extra parse failed` 的 DuckDB invalidation 不再是当前库状态；同时 `sync_raw` 的 raw ingest 进度现在会回写到 `run_context.step_progress` 并显示在 `/update/status`，长任务的 raw 进度按数量/时间双阈值更频繁刷新，不再只能看到“下载十大股东”这一个静态标签 | 这是 fail-closed 的系统级健康信号，不是 cosmetic warning；当前没有 blocking 红表，也没有 blocking yellow 表，但仍要按 bucket 复核这 2 个 yellow 维护项，区分 expected on-demand 资产和 writer/SLA debt，同时把 `sync_raw` 超时当成同步层的系统提示，而不是继续沿用“局部补修就算通过”的旧口径 |
| 库内 artifact/cache 污染审计 | **PASS / 未发现 LifeHack 自嵌套，单行大 payload FAIL 已迁移**: `audit_storage_payloads.py` 已改为只扫 payload-like 列，并用 JSON key 形态识别递归，避免把普通 `stock_code/date/built_at` 或命令字符串误判为循环引用；`chunkyctl doctor --fast` 现在默认包含 storage payload summary。2026-06-01 已将 `mart_today_signal_cache.signals_json` 20,220,095 bytes / 9,286 signals 从主表整包 JSON 迁入 `mart_today_signal_cache_signal` 明细表，主表只留 summary/cache metadata + `signals_json='[]'` 兼容字段；随后在 `storage_retention.yaml` 为 12 个经审查的有界 raw/detail/lineage pointer 列登记 owner、classification、单条/总量上限和 path-marker 许可（新增 `mart_macd_state_history.reason_codes_json` 作为诊断证据列）。最新真实库审计: 323 columns scanned / 0 FAIL / 0 WARN / 12 reviewed PASS / recursive hits 0。 | 这不是清表/VACUUM，也不是把风险静默跳过；未来若这些列出现递归 key、单条超 cap、总量超 cap 或未许可 path marker，会重新 WARN/FAIL。后续 storage 治理从“是否自嵌套”转向容量/保留期/可重算性分批治理。 |
| 文档引用图 / 循环权威链 | **PASS scoped / 10 active docs**: `docs/` 已压缩为 10 个活文档；旧研究/RCA/spec 已迁 `analysis/docs_archive_20260531/`；`audit_docs_graph.py` 当前区分 authority edge 与 context-only edge，运行快照/dated handoff 不再参与权威 SCC。 | 最新实跑: 13 sources / 260 edges / 191 authority edges / 23 context-only edges / 0 unresolved live refs / 0 missing archive targets / 0 forbidden SCC / largest SCC 7；后续新增活文档必须同批合并/归档/删除旧文档，保持 `docs/*.md <= 10`。 |
| 文档清理切片验收 | **PASS scoped when worktree clean / gate active**: `scripts/chunkyctl docs --format markdown` 已把 docs graph 与 docs/archive/support dirty 切片合并输出；docs_graph 当前 PASS，`docs/*.md=10`。本轮 storage-tool 切片修改期间该 gate 只因 support files dirty 显示 WARN。 | 这证明循环权威链已清且工具可用；后续 staging/commit 必须把 docs 删除、analysis 归档、docs map、goal/implementation、`audit_docs_graph.py`/tests、`chunkyctl docs` 入口作为同一 reviewed slice 处理，不能 `git add .`。 |
| 文档内容整合 | **PASS scoped / active docs职责收窄**: `docs/implementation_plan.md` 已改为 durable roadmap，不再复制每个 slice 的实时 PASS/WARN；`docs/architecture_reform_context.md` 已改为 300616 历史原因和稳定原则，移除旧“当前状态/GCP 当前用量/40 违规”等易误导内容；`docs/README.md` 补“goal=状态、docs=规则契约、analysis=证据”的文档治理规则。 | 当前状态只写 `goal.md`；历史证据只进 `analysis/`；活文档保留 <=10 且不再自说自话。后续若新增文档/改变启动模式/改变 gate，必须同批更新 docs map 和 quickstart。 |
| 文档归档内容审计 | **PASS scoped / no missing target**: 根目录三份迁移文档已与 `analysis/plan_v3_20260514_archived.md`、`analysis/data_integrity_audit_20260517.md`、`analysis/market_perception_development_plan_20260520.md` 逐字匹配；可从 HEAD 精确比对的归档项中 20 个 exact match，13 个为路径/status/controller-agent 规则的 intentional normalization，6 个无 HEAD 基线只能按 target-exists + live refs gate 管理。 | `Archived as/under` 不是当前权威入口；归档文件只作历史证据。若后续需要保留逐字证据，先用 `git show HEAD:<old>` / hash 比对，再改 ledger。 |
| 测试工具可信度 | **PRE-TEST GATE ACTIVE / PASS**: `backend/config/test_tool_registry.yaml` + `backend/scripts/audit_test_tool_health.py` + `docs/engineering_governance.md` 已覆盖全部 selected test artifacts；root micro-batch 13 已完成 updater 9 个 root tests 与剩余 system/strategy/tdx/v3/utils/conftest/xdxr 等 14 个 root artifacts 的 owner/status/evidence 登记；Rule 10 行为测试已加入；full audit 为 0 FAIL / 0 WARN / 365 selected / 365 registered / 100% registry coverage | 这只证明测试工具 registry 与默认/opt-in scope 对齐，不证明业务数据或策略有效；updater targeted pytest 89 passed / 2 warnings；剩余 14 个 root artifacts 默认 pytest 76 passed / 63 deselected / 15 warnings；`system_routes` 与 `v3_*` route smoke 已标 `realdb` opt-in，realdb collect-only 63/66 collected，未执行真实库测试；`backend/services/source_watermarks.py` 已改为 timezone-aware UTC 时间戳，相关 `test_source_watermarks` / `probe_source_capability` 的 `datetime.utcnow()` warning 已清掉；`test_tdx_source.py` 仍有 `datetime.utcnow()` deprecation 小债 |
| `raw_fund_flow_daily` | **FAIL / deprecated / stale, production fallback fixed**: 本地 86,426 rows，2025-08-21 -> 2026-04-24，`dim_data_asset.deprecation_status=deprecated`；`CapitalFlowAlpha` 与 institution score source gate 已改为 PIT-only，panel manifest 已标 raw 只能 research/deprecated；`need_027` 主力/超大/大/中/小单资金流仍 blocked/unknown；`backend/scripts/audit_tdx_data_need_coverage.py` 已把 blocked need summary 做成固定输出，目前只剩 `need_027` 一个 blocked gap；2026-06-01 该 audit 进一步显式列出 source registration：preferred `akshare` 在当前 registry 中已注册，而 declared fallback 标签 `miaoxiang` 只是 `aif10` 家族别名；最新 capability inventory 证明 `akshare` 确实暴露 `individual_fund_flow` / `individual_fund_flow_rank` / `individual_fund_flow_rank_snapshot`，但 `aif10` 家族能力里并没有这类 exact flow capability，所以 fallback 仍是概念路径，未构成可执行生产证据；2026-06-01 另外补上 `akshare.stock_fund_flow_individual` 研究侧排行快照 capability，并落成 `mart_stock_fund_flow_rank_snapshot_daily` / `build_fund_flow_rank_snapshot_daily`，已实跑落表到 `2026-06-01`（5,188 rows / 5,188 codes），对应 failure queue 也已从 open 收敛为 resolved；但它仍只是主力行为研究的辅助观测，不等同 `need_027` exact flow；2026-06-01 还把 blocked need summary 继续升级成 failure-queue-backed evidence：`need_027` 的 blocked summary 现在会直接携带 `mart_data_source_failure_queue` 的 open / resolved 快照，能一眼看到 `order_flow_fund_flow` 的 `individual_fund_flow` / `individual_fund_flow_rank` 现场失败证据，而不是只看到抽象的 blocked/unknown；`probe_source_capability.py` 现在默认压掉 `data_sources.registry` 的 fallback warning，只输出结构化 blocked JSON，`--show-registry-warnings` 才会恢复原始 fallback log；即使 persistence 落队列时遇到 DB 锁/模式问题，也会把 `persisted.status=error` 降级写回报告而不抛成真 traceback；但 Eastmoney 端点仍以 `ConnectionError` / `JSONDecodeError` 的 remote disconnect 失败，blocked probe 继续写入 `mart_data_source_failure_queue` 供后续 triage 复用 | CYQ 主力画像仍需要真实主力/超大/大/中/小单资金流；恢复前必须 source probe + PIT/freshness gate，生产策略/画像路径不得吃 stale raw，raw 只能 research/proxy/unknown |
| `market_perception` | **PLAN / gated**: 最近研究把路线收敛为“行业分类层均值回归、概念主题层才是 alpha 主战场，但必须 daily snapshot PIT 落库”；`fact_stock_attention_snapshot` / `raw_profit_forecast_snapshot_daily` 是 P0 接线，`dim_stock_tdx_block` 需要 history 化，`fact_margin_detail_daily` 是 P2 免费 14 年历史补位，LHB / 主力跟随信号整体反向或随机，AIF10 空壳不接 | 这条并行研究只反哺数据接入和 `daily_update` 纪律，不把“明面主力跟随”误升成正向生产证据；这轮主力资金链路已补齐到 2026-05-29，但 LHB 事件仍只能记 `partial_warn`。`raw_lhb_daily` 与 `fact_lhb_event` 都已到 2026-05-29，最新日 raw 94 rows / 84 codes、fact 84 rows / 84 codes，说明 LHB 是 source-sparse 事实，不是 ETL 落后。`need_027` 现在已登记 akshare capability；`miaoxiang` 只是 `aif10` 家族别名，但 capability inventory 里 `aif10` 仍不含 `individual_fund_flow`，live probe 继续被 Eastmoney `ConnectionError` / `JSONDecodeError` 卡住；另外 `akshare.stock_fund_flow_individual` 已有 `mart_stock_fund_flow_rank_snapshot_daily` 研究侧排行快照支撑，并已实跑落表到 `2026-06-01`（5,188 rows），failure queue 也已从 open 收敛为 resolved，但它仍不等同 exact flow；2026-06-01 还把 registry-side `lhb_daily` 对齐到和 `services.lhb_client` 一样的 date-bounded helper，所以 resolve/probe 不再落入旧的 aif10 全历史假象，主力画像仍继续按 `unknown/blocked` 管理 |
| `portfolio_sizer` profile thresholds | **PASS scoped / config-owned**: `short/mid/long` 阈值已迁到 `backend/config/portfolio_sizer_profiles.yaml`，由 `services.portfolio_sizer.config` 统一加载；`rank_and_size()` 仍会在 `hp/n_signals/Wilson` 门槛处把 exact PIT 候选截掉，所以配置化只是把 owner 和 tuning 面收口，不代表 coverage 已修复；`backend/scripts/audit_portfolio_sizer_profile_attrition.py` 现在是阈值调优前的固定审计入口，2026-06-01 复跑 353 个 raw candidates 后，短/中/长档分别只剩 5/1/2 个 selected_rows，且全部是 `cross_stage_fallback`；新的 `fail_reasons_by_match_tier` 进一步显示 exact-tier 主要卡在 `stage_pit` 的 `hp/n_signals/Wilson`，`stage_pit_formula_fallback` 主要卡在 `hp/n_signals`，而 `cross_stage_fallback` 的失败也主要集中在 `hp` 与 `wilson`；新补的 `fail_holding_days_by_match_tier` 进一步把 hp 失败拆到 holding_days：`stage_pit` 失败主要集中在 20/30/60/90 这些 off-anchor 档位，`stage_pit_formula_fallback` 主要集中在 20/30/60/90，`cross_stage_fallback` 则分布在 5/10/15/20/30/60/90 全部档位；2026-06-01 的 sensitivity audit 继续验证了这一点：`base` / `hold+20` / `min_n_signals-2` / `min_wilson_win-0.05` 对当前真实候选池的 selected_rows 没有影响，短/中/长仍然是 5/1/2 | 后续若要放宽 `min_n_signals` / `min_wilson_win`，必须先跑 profile attrition audit 并走 evidence-gated tuning，不允许回到 `profiles.py` 硬编码；当前更像是上游 candidate supply / formula coverage 的结构性稀疏，而不是 profile knob 微调能修好的问题 |
| stage-opt candidate supply audit | **PASS scoped / upstream supply evidence**: 新增 `backend/scripts/audit_stage_opt_candidate_supply.py` + `backend/tests/scripts/test_audit_stage_opt_candidate_supply.py`；2026-06-01 先后回填 `fact_stock_technical_stage` / `fact_signal_context` 的 2025-08-01→2026-05-29 断档，再跑 full-history 审计时实际看到 `raw_signal_rows=1,381,657`、`filtered_signal_rows=733,083`、`unique_keys=120,273`、`ready_keys=57,986`、`ready coverage=48.21%`、`dropped_index_rows=1,355`、`dropped_unknown_stage_rows=647,219`、`below_min_signals=62,287`，且 `codes_without_bars=0`，说明当前瓶颈不是 K 线缺失而是 signal density；这次还修正了脚本结果里 `raw_signal_rows` 被 summary shadow 的报告 bug，所以 raw / filtered 现在分开显示；后续又用 `compute_start=2022-01-01 / write_start=2023-01-01 / end=2023-09-11` 补了 `fact_stock_technical_stage` 早期窗口 427,436 行（min date 到 2023-01-13），但 rerun 审计结果完全不变，说明 stage-opt 目前的瓶颈并不在这段 2023 年初空白；2026-06-02 先把 `reversal_1m_deep` 阈值从 15-30% 放宽到 10-30%，历史重算后该 formula 的 `rows / keys / ready coverage` 提升到 `76,635 / 11,968 / 42.54%`，随后又把 `reversal_1m_mild` 阈值从 5-15% 放宽到 4-15%，历史重算后该 formula 的 `rows / keys / ready coverage` 提升到 `372,661 / 9,265 / 62.43%`，并把 `reversal_1w` 从 5-10% 放宽到 2-10% 后实跑到 `369,822 / 15,937 / 66.18%`；2026-06-02 还把 `dynamic_ma_iterative_cross` 默认迭代轮数从 10 轮压到 2 轮，历史重算后该 formula 的 `rows / keys / ready coverage` 提升到 `225,783 / 20,076 / 51.63%`；全局审计也更新为 `raw_signal_rows=2,103,143 / filtered_signal_rows=1,110,280 / unique_keys=133,857 / ready_keys=76,480 / ready coverage=57.14% / below_min_signals=57,377`；`build_formula_signals_history.py --recompute-horizon-evidence` 现在也已在补 `defaultdict` import 后恢复可跑，不再在 horizon evidence 重算阶段抛 NameError；2026-06-02 的 `min_signals` probe 进一步显示 `min_signals=4` / `3` 会把全局 ready coverage 分别抬到 `64.84%` / `73.75%`，对应 `ready_keys=86,796` / `98,721`、`below_min_signals=47,061` / `35,136`，2026-06-02 额外把 `min_signals=2` 继续推到 `84.80%`（`113,506` ready_keys / `20,351` below_min_signals），同时 `reversal_1m_deep` ready coverage 提升到 `50.97%` / `62.34%`，但 controller 仍把下一步指向 `P1 / upstream_candidate_supply`，说明阈值确实是杠杆但当前仍需回到上游供给设计；新增的 `blocked_reason_counts_by_formula_id` / `blocked_reason_counts_by_formula_variant` / `blocked_reason_counts_by_stage_bin` 进一步显示所有 blocked keys 都卡在 `below_min_signals`，其中 `macd_golden_cross`、stage 3/4 是当前最弱分布；脚本现在还会自带 `next_action_recommendation`，直接把下一步指向 `P1 / upstream_candidate_supply`，并把 `macd_golden_cross` 与最弱 stage bins 作为优先 triage 目标；`2024-03-06` 起的 `dropped_unknown_stage_rows` 降到 454,158，说明 2025 H2 的硬断档已修，剩余 `technical_stage='?'` 更偏结构性分类/预热缺口而不是新鲜 ETL outage；脚本默认 end 现在复用当前连接里的交易日历真相源，不再 nested `latest_closed_or_raise()` 新连接。2026-06-02 04:48 CST 的最新重算又把这条线往前推了一格：在 `reversal_1m_deep` 继续维持 8-30% 的前提下，`mart_macd_state_history` 以 180 天 warm-up 写出的 `raw_state_history_rows=370,039` 已被正式纳入 MACD 诊断 mart，`audit_stage_opt_candidate_supply.py --formula macd_golden_cross` 现在计入 `raw_trigger_rows=161,279` + `raw_state_history_rows=370,039`，把 MACD 相关 coverage 抬到 `47.13%`（`16,474` ready keys）；同一轮 full-history 复跑的全局审计已刷新为 `raw_signal_rows=5,085,286 / filtered_signal_rows=2,550,775 / unique_keys=147,441 / ready_keys=101,382 / ready coverage=68.76% / below_min_signals=46,059`，`macd_golden_cross` 达到 `1,714,731 signal_rows / 46,120 keys / 84.75% coverage`，`reversal_1m_deep` 达到 `118,098 signal_rows / 14,389 keys / 51.30% coverage`，`min_signals=4/3/2` 分别抬到 `75.33% / 82.36% / 90.25%`（`111,069 / 121,431 / 133,063` ready keys），但 controller 仍然指向 `P1 / upstream_candidate_supply`，说明即便这轮供应大幅抬升，结构性瓶颈依旧是上游候选供给，不是单个公式的阈值；2026-06-02 05:02 进一步把 `turtle_breakout_55` 的量能确认门槛外置到 `backend/config/formula_turtle_breakout.yaml` 并从 `1.3` 收到 `1.2`，历史重算后 `turtle_breakout_20` / `turtle_breakout_55` 分别达到 `199,495 / 19,413 / 80.81%` 和 `115,911 / 16,898 / 60.17%`，全局审计刷新为 `raw_signal_rows=5,123,528 / filtered_signal_rows=2,574,836 / unique_keys=147,674 / ready_keys=102,500 / ready coverage=69.41% / below_min_signals=45,174`，`min_signals=4/3/2` 现在对应 `75.85% / 82.68% / 90.43%`（`112,013 / 122,097 / 133,545` ready keys），但 controller 结论仍然是 `P1 / upstream_candidate_supply`；`macd_golden_cross` 的进一步扩展仍受 `fact_technical_trigger` PRIMARY KEY `(stock_code, date, formula_id)` 限制，若要增加多状态行，必须走 schema evolution 而不是单改 formula state |
| 画像 / 股票档案 | **PLAN / gated**: `docs/data_product_contract.md` 已新增 | 股票画像、机构画像、主力行为画像和前端股票档案必须先走数据需求契约 + lineage/freshness contract；前端不能先把 `unknown/proxy/stale` 包装成生产证据 |
| `test_screening_engine.py` | **PASS**: active-universe fixture 已修复 | K 线 truth-source fixture 改为相对当前日期；`test_screening_engine.py` + `test_screening_read.py` 4 passed |
| main 工作区 | **长期 dirty 已清零 / 本轮数据刷新未制造 tracked dirty**: 2026-06-01 已按 bucket -> gate -> explicit stage -> `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh` 分阶段收束，上一安全/tooling 切片已提交；K 线补齐写入 ignored DuckDB，补文档前 `git status --short` 为空。 | dirty 工作区问题已从长期阻塞变成受控切片流程；后续仍禁止 `git add .`，每个新改动继续按小切片、test-tool gate、CodeGraph+complexity、safe_commit 收束。 |
| CodeGraph 索引状态 | 上一 `.py` 变更后已 `codegraph sync .` 且 `doctor --fast` PASS；本轮 K 线补齐未改 `.py`。 | CodeGraph 已不再被长期 untracked/dirty 误导；后续 `.py` 变更仍需改前 query/context、改后 `codegraph sync .`，最终交付前 `codegraph status .` 应回到 up to date。 |
| 工具更新 | **LOCAL WRAPPER FIRST / PROJECT ASSISTANT SEED**: `audit_tooling_gate.py` / `chunkyctl doctor` 已能区分 dirty、CodeGraph pending、complexity baseline/diff，并新增 storage payload summary；`chunkyctl worktree` 当前可归桶 dirty；`chunkyctl audit` 支持 scoped gate；`safe_commit.sh` 支持 `SAFE_COMMIT_NO_PUSH=1` 本地安全提交；`docs/chunkyctl_session_quickstart.md` 已同步 doctor storage payload 和 complexity identity 解释。2026-06-01 发现旧本地 baseline 截断会误报历史 HIGH，已刷新到 220 条；本轮又修正 diff identity 默认不依赖行号，避免删除/新增一行把历史 HIGH 漂移误判为 new。 | GitHub 调研后暂不引入 Worktrunk/lazygit/delta/git-absorb/git-town/git-branchless；先把 dirty/codegraph/complexity/storage 机器化，再逐步收束成 `chunkyctl` 类项目专用审计/开发辅助入口；后续任何 session 启动模式、gate、工具入口或 controller/agent 工作法变化，必须同批更新 quickstart，不允许只留在对话或 handoff。 |
| `docs_archive_moves` | **APPROVE_WITH_NOTES**: 三份 root 历史文档已逐字迁到 `analysis/data_integrity_audit_20260517.md`、`analysis/plan_v3_20260514_archived.md`、`analysis/market_perception_development_plan_20260520.md`；hash 复核一致: plan archive=851cd7b7, data-integrity archive=3aaabf4b, market-perception archive=ceb29db0，`git diff --no-index --stat` 无输出 | 后续 staging 时三份 root 删除和三份 `analysis/` 归档必须同组处理；旧 root 名称剩余命中为 Docs Map、迁移记录、归档正文标题或 test fixture，不是活跃入口 |
| `config_project` | **PASS scoped audit**: `.gitignore`、`backend/config/field_dictionary.yaml`、`pytest.ini` 已和 test-tool registry / audit 脚本 / 回归一起审计；`pytest.ini` 默认排除 `realdb/perf/network/gcp/slow` | 这是配置门禁证据，不是业务数据或策略证据；后续 staging 应随 startup/test-tool/config slice 成组处理 |
| `docs/implementation_plan.md` | 已收窄为 durable roadmap | 当前 PASS/WARN/FAIL 看本节；执行顺序、边界和验收标准看该文件 |
| `updater.py` | 723 LOC route shell + `updater_execution.py` 823 LOC + `updater_launcher.py` 278 LOC + `updater_completeness.py` 108 LOC + `updater_plan.py` 130 LOC + `updater_lifeboat.py` 88 LOC + `updater_market_data.py` 765 LOC + `updater_infra.py` 258 LOC + `updater_calendar.py` 157 LOC + `updater_steps.py` 232 LOC + `updater_connectivity.py` 156 LOC + `updater_sync.py` 443 LOC + `updater_calc.py` 196 LOC + `updater_runtime.py` 34 LOC + `updater_audit.py` 53 LOC + `updater_status.py` 593 LOC + `updater_reset.py` 161 LOC + `updater_institution.py` 533 LOC + `updater_trends.py` 303 LOC + `updater_profiles.py` 455 LOC | runtime helper、infra/helper、calendar/date-truth、DAG metadata 与 DAG 查询/选择 helper、execution orchestration helper、full/group/smart/single 状态账本、group pipeline 执行循环 helper、full DAG 执行循环 helper、single-step chain 执行循环 helper、smart plan 执行循环 helper、smart-update 计划/交易日历 preflight helper、background task failure/cleanup launcher helper、smart/full/single/group background launcher deps、launcher callback bundle、group route request launcher helper、run-start helper、step-status priming helper/connection lifecycle、stale-running step_status 清理 helper、step_status catalog 同步 helper、source failure queue 状态 helper、update status payload/response helper、audit snapshot refresh helper、audit route payload helper、step-result apply helper、stop/hard-dependency bookkeeping helper、running/stopped/failed transition helper、K 线不可用 skip/gap_queue helper、K 线连通性预检 helper、runner managed connection helper、data_completeness 校准 helper、status/plan summary、connectivity probe/cache、reset table helper 与 reset response payload/connection lifecycle、standalone external sync/calc、sync_raw/sync_financial body、institution match、sync_industry body、industry_stat sync body、build_trends body、build_profiles body、sync_market_data body、lifeboat legacy endpoints 已抽出；full/group/single launcher 参数注入与 group route request 调度已迁入 `updater_launcher.py`，reset-derived/reset-industry response/connection lifecycle 已迁入 `updater_reset.py`，后续继续按 route/status 边界收薄 |
| Complexity HIGH | backend API HIGH 已清；已治理 gate scripts 无 HIGH。2026-06-01 已把 `build_architecture_inventory.py`、BestChoice context/feed 脚本、`build_candidate_feature_panel.py`、`build_dim_listing_status.py`、`build_drift_safe_feature_candidates.py`、Phase7、executive、feature association 等切片清掉；本轮 `audit_tooling_gate.py` scoped complexity 无热点。`build_daily_position_recommendations.py` 仍有历史 HIGH 412/492，当前只修 destructive DDL，不把它误报为新增。 | 下一优先级不再追截断误报的 complexity `new_high`，也不再把已审过的 storage payload 当 dirty；优先处理真实业务风险: 端到端数据 freshness/PIT FAIL、Survivorship/Data completeness 实跑 FAIL。若后续新增 `.py` 再触发 new HIGH，仍按 CodeGraph+complexity 小切片实修或 reviewed exception。 |

| 端到端审计实跑 | **PASS with WARN: 24 total / 23 OK / 1 WARN / 0 FAIL** | 2026-06-01 08:12 CST 复跑已无 FAIL：`mart_stock_picture_daily` 与 `mart_stock_survey_features` 均补到 2026-05-29；`audit_end_to_end.py` 现改为对齐最新已完成交易日，周末/盘中自然日误伤不再计入 freshness WARN，所以当前唯一 WARN 只剩最新推荐 PIT coverage=0。2026-06-01 已把 `rank_and_size()` 改成 PIT-tier-first，但 2026-05-29 复跑显示 PIT exact 候选仍因 `hp/n_signals/Wilson` 门槛稀疏而未进入最终推荐，所以 coverage 仍是数据稀疏问题，不是排序 bug。随后对 `build_stage_opt_pit.py --cutoffs 2026-05-19 --stock-codes 600850 601963 300750 001286 300360 301568 605580` 做 targeted backfill，最新 cutoff 行数从 3 补到 4；再对 `optimize_per_stock_stage_strategy.py --stock-codes 001286 605580 --min-signals 3` 做 smoke，仍是 0 governance pass rows。2026-06-01 额外跑了 `audit_portfolio_sizer_profile_attrition.py`：353 个 raw candidates 中，短/中/长档 selected_rows 仅 5/1/2，且全是 `cross_stage_fallback`，fail reasons 主要集中在 `hp` 与 `wilson`；这次新增的 `fail_reasons_by_match_tier` 进一步显示 exact-tier 主要卡在 `stage_pit` 的 `hp/n_signals/Wilson`，`stage_pit_formula_fallback` 主要卡在 `hp/n_signals`，而 `cross_stage_fallback` 的失败也主要集中在 `hp` 与 `wilson`；新补的 `fail_holding_days_by_match_tier` 进一步把 hp 失败拆到 holding_days：`stage_pit` 失败主要集中在 20/30/60/90 这些 off-anchor 档位，`stage_pit_formula_fallback` 主要集中在 20/30/60/90，`cross_stage_fallback` 则分布在 5/10/15/20/30/60/90 全部档位，所以 exact stage × formula 候选供给是结构性稀疏，不是单次补表能解决。2026-06-01 的 sensitivity audit 也确认，`base` / `hold+20` / `min_n_signals-2` / `min_wilson_win-0.05` 对 selected_rows 没有影响，短/中/长仍然是 5/1/2，因此后续 tuning 重点应回到上游 candidate supply / formula coverage，而不是继续围着 profile knobs。2026-06-01 又对当前推荐的 7 个 stock code 以 cutoffs `2026-01-01,2026-05-19,2026-05-29` 重跑 `build_stage_opt_pit.py`，结果 latest recommendation PIT coverage 仍然 0（8 total / 0 exact / 0 same_formula / 1 same_stock / 8 cross_stage），说明 exact stage × formula 的候选供给是结构性稀疏，不是单次补表能解决。2026-06-01 还补了 `mart_daily_position_recommendation_pit_diagnostic` 的 `governance_reject_count` / `governance_latest_reason` / `governance_latest_rejected_at`，并重跑 `build_daily_position_recommendations.py --date 2026-05-29`，让 7 个 `stock_missing_pit` 和 1 个 `formula_missing_pit` 行直接携带最新治理拒绝原因；这只提升 triage 可见性，不改变 PIT coverage 0 的结论。当前 PIT coverage 0 说明 exact stage × formula 组合在现有样本和治理门槛下仍太稀，不是推荐排序本身的 bug。不能当生产证据；继续微调 profile knobs 不会改变这个结论。 |
| PIT integrity 实跑 | **PASS: 11 PASS / 28 WARN / 0 FAIL** | 2026-06-01 04:07 CST 复跑 PASS；`fact_signal_context`/`fact_technical_trigger` 在 2026-05-29 spot-check 无 future rows。2026-06-01 11:17 CST 复跑 `audit_pit_coverage.py` 仍是 4/4 PASS，`fact_lhb_event` gain_20d coverage 83.9% > 60%，所以 LHB 的剩余问题是 sparse-event completeness，不是 PIT 安全性。仍有 legacy single-batch/OOS/selector WARN，不能把 legacy/warn-only 当 production 证据 |
| Survivorship gate 实跑 | **PASS: current label_version=p0a_v3_horizon_governance** | 当前训练面板 `mart_p0a_label_panel` 的 `p0a_v3_horizon_governance` 版本覆盖 5,210 distinct codes，>= KEEP ever-listed 5,210 的 90% 门槛；`p0a_v2_governance_v1` 仍保留为历史版本，只在显式 flag 下复查 |
| Universe coverage 实跑 | **PASS: 16 PASS / 6 WARN / 0 FAIL** | 真实 gate 0 FAIL；6 WARN 为 `fact_signal_context` 空行、近期 panel 空行和 1 个 partial-sync month-first 样本，不能当数据全新鲜证据 |
| 数据完整性实跑 | **PASS with WARN: 0 FAIL / 2 WARN** | 2026-06-01 已把 `price_kline_tdxhub`、`fact_alpha158_panel`、`fact_stock_technical_stage`、`fact_signal_context`、`fact_technical_trigger`、`fact_capital_flow_pit_daily`、`fact_risk_factors`、`fact_sector_momentum_daily`、`mart_stock_picture_daily`、`mart_stock_survey_features`、`mart_p0a_label_panel`、`mart_p0a_feature_label_panel_v3`、`mart_p0a_feature_label_panel_v4`、`mart_sniper_score_daily`、`mart_institution_score_daily` 补到交易日历 `2026-05-29`；2026-06-01 又额外回填 `fact_stock_technical_stage` / `fact_signal_context` 的 2025-08-01→2026-05-29 断档，stage-opt audit 里的 `technical_stage='?'` 现在更多是结构性分类/预热缺口而不是新鲜 ETL outage；`fact_lhb_event` 与 `fact_technical_trigger` 已在 `dim_data_asset` 注册为 `sparse_event_presence_only`，完整性审计只保留 WARN evidence：`fact_lhb_event` 84 个 code（raw/fact 都到 2026-05-29，最新日 raw 94 rows / fact 84 rows）、`fact_technical_trigger` 1,692 个 code，不再当缺数 blocker。 |
| 业务推进 | 暂停 | 不做 300616 五公式、前端公式视图、GCP/Optuna 全量跑批 |

- 2026-06-02 `build_daily_position_recommendations.py` 已把 MACD `mart_macd_state_history` 纳入候选池，并把 cross-stage fallback 回接到现有的 `mart_per_stock_strategy_optimal`；live 2026-06-01 运行现在不会再因错表崩溃，当前快照虽然返回 0 candidates，但这是 live DB 无匹配行，不是 loader 失败；该修复已在 commit `1402bc0b` 落地，随后这轮 stage-opt / MACD / docs slice 又以 `ed5a3ee6` 收口，当前 worktree 已 clean。

### 权威文档顺序

| 优先级 | 文档 | 用途 |
|---:|---|---|
| 1 | `AGENTS.md` | Codex 操作政策: dirty worktree、CodeGraph+complexity、GCP、删除治理 |
| 2 | `goal.md` 本节 | 当前 FAIL/WARN 账本、优先级和下一步 |
| 3 | `docs/chunkyctl_session_quickstart.md` | 新 session 启动契约 |
| 4 | `docs/PROJECT_CONSTITUTION.md` | 最高规则: 真相源、分层、gate、完成标准 |
| 5 | `docs/engineering_governance.md` | 第一性原理/奥卡姆、CodeGraph+complexity、测试工具、agents、GCP、删除治理 |
| 6 | `docs/data_product_contract.md` | 数据需求、血缘、画像、市场感知支持、前端契约 |
| 7 | `docs/strategy_validation_contract.md` | 回测、Optuna/GCP、paper_sim、forward、promotion、主升浪验证边界 |
| 8 | `docs/architecture_reform_context.md` | 300616 哨兵案例和架构改革原因 |
| 9 | `docs/implementation_plan.md` | 正式计划文档，已同步为架构优先口径 |

`SESSION_HANDOFF.md`、`analysis/workflow_checkpoint.md`、`analysis/handoff_*.md`
和 dated bootstrap/prompt 文件只作运行上下文与历史证据；若与以上权威文档
冲突，以权威文档为准。

### 不做清单 (直到架构 gate 通过)

| 暂停项 | 原因 | 恢复条件 |
|---|---|---|
| M0 300616 五公式 | 业务逻辑会被错误 universe / PIT / gate 污染 | 架构验收通过后再按 god-view -> PIT 去泄漏方法做 |
| 主升浪猎手正式研究/验证 | 当前仍在框架治理期，不能把研究日志里的旧胜率当生产结论 | 框架治理工作结束并通过 architecture/docs/test/data/tooling gate 后，作为主线 P1 专题认真研究、复现、去泄漏验证 |
| M4 前端公式视图 | 展示层不能先于可信公式/数据层 | L0-L3 gate 清楚、公式 registry 可信 |
| M5 GCP 全量 Optuna | 之前 29/34 无 search space 白跑、200 只深主板偏样本 | `backtest_preflight` + `plan_validator` + GCP preflight 全 PASS |
| 新策略 promotion claim | 未测指标必须 unknown | 只能用 paper_sim/Phase4/PBO/DSR/forward 证据 |

### 架构完成后回归主线

架构 / 框架治理 gate 通过后恢复主线, 但顺序必须是验证先于展示。`docs/zhushenglang_hunter_research_log_20260528.md`
作为“主项目做成主升浪猎手”的产品北极星保留；治理结束后要认真研究、复现和验证，不把旧 prototype 结论直接当生产证据。

| 顺序 | 优先级 | 主线任务 | 前置 gate |
|---:|---|---|---|
| 0 | P1 | 主升浪猎手认真研究和验证 | 框架治理结束后启动；先复核研究日志的数据/代码/样本边界，再经 PIT、成本、T+1、涨跌停、持仓重叠、walk-forward、paper_sim 和 forward monitor 复验；70%/78%/86% 等数字只作研究假设 |
| 1 | P1 | BestChoice 公式接入与冻结证据复核 | BestChoice artifact freeze/hash/lineage, namespaced challenger import |
| 2 | P1 | 300616 原始公式复现 | universe/PIT 清洁, god-view 与 PIT 去泄漏分离 |
| 3 | P1/P2 | 300616 衍生公式与参数空间 | `plan_validator` 8 项 PASS, search space 非空 |
| 4 | P1/P2 | 主项目量化回测 / paper_sim | `backtest_preflight` 8 项 PASS, 成本/涨跌停/排除股票规则有效 |
| 5 | P2 | 候选池与持仓监控 | paper_sim/Phase4/PBO/DSR/forward 证据足够, 未测指标仍 `unknown` |
| 6 | P2/P3 | 股票画像和档案 API | profile contract + lineage/freshness gate |
| 7 | P3/P4 | 全局前端 UI/交互重设计 | 业务 API、gate 状态、lineage contract 稳定 |

因此“架构完成”不是直接上前端, 而是恢复可验证主线: 公式 -> 回测 -> 候选/监控 -> 画像/API -> 前端。

### 总体目标架构

| 层 | 责任 | 真相源 / 模块 | 当前整改重点 |
|---|---|---|---|
| L0 基础设施 | 交易日历、K 线、配置、审计 | `price_kline*`, `services/calendar.py`, `config/*.yaml`, `data_audit.py` | K 线为交易真相；`dim_active_a_stock` 降级为 cache/name |
| L1 公式引擎 | 59 公式、参数、search space | `bc_absorbed/formula_engine.py`, `formula_*.yaml` | 暂停新公式，先确保 gate 和 universe 可信 |
| L2 信号处理 | 共振评分、画像、SmartMoney adapter | `stock_profiler.py`, `signal_ranker.py`, `smartmoney_adapter.py` | 画像先做 contract/lineage/freshness；缺源字段输出 `unknown`，不抢当前 P0 |
| L3 策略执行 | 股票池、paper_sim、交易模型 | `portfolio_pool.py`, `paper_sim/` | 成本/涨跌停/持仓规则来自配置 |
| L4 展示 | API / 前端 | `backend/routers/*.py`, `v3/*` | `updater.py` god module 拆分，股票档案前端等后端画像 contract 稳定后再改 |

### 智能更新管家边界

| 角色 | 负责 | 不负责 |
|---|---|---|
| 智能更新管家 | 审计输入、DAG 计划、依赖调度、锁/停止、超时、状态、日志、StepResult 汇总、质量 gate 汇总 | 拉行情、写财务表、算画像、判断 universe 真相 |
| 数据/计算模块 | 自己的数据源、表写入、领域内校验、watermark、统一返回 `status/count/detail/error` | 全局流程、跨模块调度、前端状态、绕过 gate |
| 审计模块 | 新鲜度、完整性、缺口、异常和是否需要更新 | 直接执行更新 |

后续 `updater.py` 拆分必须沿这个边界推进: 管家只监督流程和质量，数据模块自更新并交回可审计证据。

### 数据血缘与画像 / 股票档案路线图

| 顺序 | 优先级 | 内容 | 依赖 / Gate |
|---:|---|---|---|
| 1 | P0 | 数据需求契约与血缘先行: 新画像字段先登记 need、grain、PIT key、freshness、consumer | `tdx_data_need_coverage.yaml`, `audit_tdx_data_need_coverage.py`, `docs/data_product_contract.md` |
| 2 | P0/P1 | 统一画像 component contract: `value + as_of_date + built_at + source_tables + freshness_status + evidence_status + lineage_ref` | 缺数据必须 `unknown`; proxy 必须显式标注 |
| 3 | P1 | 股票画像读模型: 趋势/量价/风险/行业/graph/horizon evidence 汇总 | K 线真相源、PIT lineage、不能从 `dim_active_a_stock` 推 universe |
| 4 | P1 | 机构画像收敛: 复用 `mart_institution_profile`，补 source/freshness/lineage | 机构持仓、调研、龙虎榜、行业统计均需可追溯 |
| 5 | P1/P2 | 主力行为画像: CYQ + 量价 + 事件 + 真实订单流资金 | `docs/chip_distribution_cyq_spec.md` 已把 CYQ 算法和验证写清；`raw_fund_flow_daily` / `need_027` 恢复前资金流维度为 `unknown` 或 proxy，不作生产证据；`akshare.stock_fund_flow_individual` 可作研究侧排行快照补充，但不等同 exact flow |
| 6 | P2 | 股票档案 API: 统一现有 stock detail/graph/institution/profile 读模型 | contract test 覆盖 `unknown/proxy/production` |
| 7 | P2/P3 | 前端股票档案: 总览、机构、主力/CYQ、数据血缘/新鲜度分区 | 后端 contract 稳定后再做；前端只展示证据，不自造判断 |
| 8 | P3/P4 | 全局前端 UI/交互重设计: 按项目架构和流程重组操作台 | IA/交互方案审查 + contract tests + Browser 截图验收 + 关键流程 smoke |

详细路线见 `docs/data_product_contract.md`。该路线不改变当前“先架构后业务”的优先级: P0 仍是治理、universe、Rule 10、数据契约、复杂度和 updater 边界；全局前端重设计放在主线验证之后。

### Dirty worktree 分阶段收束计划

| 批次 | 优先级 | 当前桶/数量 | 处理方式 | 验收/退出条件 |
|---:|---|---:|---|---|
| 0 | P0 | 本地生成残留 | 已删除 `.DS_Store` / `__pycache__` / `.pytest_cache` / `.pyc`；日志、报告、DB 不删，先判定证据价值 | `find` 不再发现上述生成残留；git dirty 数不会因此下降，属环境清理 |
| A | P0 | docs + controller: `project_docs=69`、`docs_archive_moves=6`、controller docs/state 相关 | 把 10 active docs、analysis 归档、root 历史文档删除、goal/implementation/quickstart 更新作为同一 docs-cleanup slice | `audit_docs_graph.py` PASS；`scripts/chunkyctl docs` 除 dirty slice 外无 graph FAIL；归档 hash/引用证明齐全 |
| B | P0/P1 | startup/tooling: `startup_tooling=12`、部分 `config_project/tests/audit_gate_scripts` | 验收 `chunkyctl`、test-tool registry、Rule 10、safe_commit dry-run、GCP latch 文案；通过后单独 stage/commit | test-tool scope PASS；`test_chunkyctl.py`、`test_safe_commit.py`、工具脚本 py_compile/pytest PASS；Rule 10 verdict 可写入 commit message |
| C | P0/P1 | universe/data/storage gates: `universe_governance=4`、`data_source_lineage_profiles=7`、storage payload audit | 验证 `check_universe_filter --all`、tdx need coverage、storage payload audit；禁止把 stale/proxy 变生产证据 | universe CLEAN；source/lineage/storage scoped tests PASS；真实库 storage finding 入账为后续迁移任务 |
| D | P1 | updater split: `updater_split=30` | 按“管家监督流程、数据模块自更新”边界审查拆分文件和 root updater tests | CodeGraph + complexity paired；updater targeted pytest PASS；`updater.py` route shell 边界清楚 |
| E | P1 | services/scripts/tests/config: `backend_services_api=30`、`pipeline_build_scripts=20`、`audit_gate_scripts` 剩余、`tests=36`、`config_project=7` | 按业务域小批次验收，优先数据真相源、PIT/freshness、测试工具有效性；不跨域大 commit | 每批都有 scope、test-tool gate、py_compile/pytest/domain audit、complexity diff；无新增 HIGH |
| F | P0 | final clean | **DONE for long-lived dirty / current slice gated**: 所有长期 dirty 已按小切片提交；当前 safety/tooling 切片完成 test-tool audit、py_compile、scoped pytest 30 passed、`scripts/chunkyctl audit --run` 和 CodeGraph sync，提交后工作区应回到 0 dirty。 | `scripts/chunkyctl doctor --fast` 不应再因长期 dirty/unknown/pending FAIL；本地 complexity baseline 已刷新且 diff identity 已抗行号漂移，下一剩余真实治理对象是端到端数据 freshness/PIT、Survivorship 与 Data completeness。 |

当前裁定: dirty 问题已完成一轮系统化清零；后续继续使用 bucket -> gate -> explicit stage -> safe_commit 的收束流水线。`build_architecture_inventory.py`、BestChoice context/feed、candidate feature panel DDL fallback、listing status schema migration、drift-safe fold-stable scoring/serialization、Phase7 context bulk K-line、executive trade events bulk/index、feature association fold/correlation cleanup、storage payload reviewed-column、公式信号增量刷新安全阀、daily recommendation DDL 防误删、tooling diff identity、alpha158 安全窗口写入、stage/context/technical-trigger 安全窗口刷新、picture 单日事务补齐、survey read-window/write-window 拆分与空窗口防误删切片均已完成 scoped gates；K 线、alpha158、stage、signal_context、technical_trigger、picture、survey freshness 已补到 2026-05-29。下一步不是继续找长期 dirty，也不是追被 baseline/行号漂移误报的 complexity new HIGH，而是处理真实业务风险: label/v3/v4/sniper/institution stale、LHB stale、Survivorship/Data completeness 实跑 FAIL，以及推荐 PIT coverage=0 的 legacy fallback。storage payload 当前为 PASS / 12 reviewed columns，不再是剩余阻塞。

### P0-P3 实施计划

| 阶段 | 优先级 | 目标 | 主要动作 | 验收 |
|---:|---|---|---|---|
| 0 | P0 | 冻结现场和 scope | `git status --short`; `scripts/chunkyctl worktree --format markdown`; 按 dirty bucket 分类 tracked dirty / untracked；不 stage data parquet / skill 目录 / 无关文档；先删可再生本地残留 | 2026-06-01 之前 dirty entries 曾清零；当前 worktree 仍有 8 个 tracked mods（holder/shareholder-plan slice），需按 bucket 收束后再 commit；`scripts/chunkyctl worktree --format markdown` 仍应以 bucket 方式下钻 |
| 1 | P0 | Universe lint 从误标 FAIL 变成真实剩余清单 | 修 5 处 `rule-compliance` 同行；复跑 checker；记录 non-test 剩余 | 5 处从报告消失，剩余列表可分组 |
| 2 | P0 | Rule 10 从提醒变硬阻断 | `scripts/safe_commit.sh` 已增加 `.py staged` commit message gate，并补临时 git repo 行为测试 | 无 `Codex-Reviewed: APPROVE/APPROVE_WITH_NOTES` 或 8+ 字符 `codex-review: skipped reason=...` 时 exit 6；`REQUEST_CHANGES` 不放行 |
| 3 | P0/P1 | 清完 non-test `dim_active_a_stock` | 合法 cache/name/data-sync/schema/meta 已加同一行 evidence；非法 universe 走 `get_active_universe()` | `check_universe_filter --all` CLEAN |
| 4 | P1 | 保持正式计划文档同步 | `docs/implementation_plan.md` 已改为架构优先；后续随实际进度同步 | 文档不再声明旧 CLEAN 或旧优先级 |
| 4.5 | P0/P1 | 顶层设计审查制度化 | `docs/engineering_governance.md` 已新增，明确流程/功能/模块/表/配置的 owner、truth source、gate、奥卡姆审查 | 后续架构变更能先填审查模板，避免新增表/配置/模块无主膨胀 |
| 4.6 | P0/P1 | 硬编码治理制度化 | 将“业务规则/阈值/source catalog/优先级先定 owner”写入 `AGENTS.md`、bootstrap、top-level review、`chunkymonkey-governance` 和 `chunkymonkey-review-gate` | 新增业务值先判定 config/table/service/code exception；同一规则只能有一个真相源 |
| 4.6.1 | P0/P1 | 删除治理制度化 | 将“验证可删就真删，禁止注释/隐藏/改名假删除；删除前用 CodeGraph + `rg` + 测试/审计取证”写入 `AGENTS.md`、top-level review、`chunkymonkey-governance` 和 `chunkymonkey-review-gate` | 死代码/旧测试/旧文档不再以注释、disabled branch 或空壳污染工作区；未验证可删的对象保持 active/quarantined 状态并写明证据缺口 |
| 4.7 | P0/P1 | 数据需求契约化 | 已把 `grain`、`pit_key`、`freshness_sla`、`evidence_status`、`production_eligibility` 从 notes 提升为 YAML 必填字段；`need_027` 主力/超大/大/中/小单资金流明确为 `pit_key=unknown`、`evidence_status=unknown`、`production_eligibility=blocked`；`CapitalFlowAlpha` / institution score / panel manifest 已移除 stale raw 生产 fallback | `scripts/chunkyctl audit --run ...` PASS；targeted pytest 25 passed；临时 DuckDB 物化 27 needs / 10 priorities / 14 reassignments；缺字段、非法 enum、eligible+unknown PIT 都会 FAIL；生产 DuckDB exact-sync 尚未执行，需用户批准后单独写入 |
| 4.8 | P0/P1 | 画像与股票档案路线契约化 | 新增 `docs/data_product_contract.md`；把股票画像、机构画像、主力行为画像、股票档案前端按 lineage -> profile contract -> mart/service -> API -> frontend 排序 | 任何画像/前端展示必须携带 source、PIT/freshness、`unknown/proxy/production` 状态和 lineage_ref |
| 5 | P1 | 拆 `updater.py` | 已抽 19 个 `updater_*` 模块；第二十一至第五十八刀已迁出状态账本、step-status、连通性、execution loop、background task helper、smart plan/preflight、launcher 和 reset response payload/connection lifecycle；第五十一刀将 `sync_industry` body/gap queue/progress JSON 迁入 `updater_institution.py::_step_sync_industry_with_hooks`，`updater.py` 只保留 hook 注入 wrapper；第五十二刀将 `/update/status` 连接生命周期与 step_status catalog sync 迁入 `updater_status.py::build_update_status_response`；第五十三刀将 `/update/smart-plan` 连接生命周期与 plan budget response 迁入 `updater_status.py::build_smart_plan_response`；第五十四刀将 `/update/reset-derived` 与 `/update/reset-industry-derived` 连接生命周期迁入 `updater_reset.py::build_reset_derived_response` / `build_reset_industry_response`；第五十五刀将启动前 step_status priming 连接生命周期迁入 `updater_steps.py::prime_run_step_status_for_steps`；第五十六刀将 `/update/smart` 计划构建连接生命周期迁入 `updater_status.py::build_smart_update_plan`；第五十七刀将 run context/noop/finish/heartbeat helper 迁入 `updater_status.py`；第五十八刀将 group route request scheduling 迁入 `updater_launcher.py::launch_group_update_request` | god module 5136 -> 723 LOC，targeted tests pass，0 新 HIGH |
| 6 | P0/P1 | 清复杂度 HIGH | P0-A `v3_meta.py` 已批量化；P0-B `institution.py` 已改为计数/预排序 map；P0-C `screening.py` 已抽顶层 name-key helper；P1 `v3_portfolio_builder.py` 已将 regime 分段汇总下沉到 service；P1 gate script `audit_delivery_readiness.py` 已预排序 glob paths 并批量查询 mart table existence；P1 data gate script `audit_data_completeness.py` 已按 DB 批量汇总 table freshness/coverage；P1 scanner `audit_n_plus_one.py` 已拆单层 helper并清自身 complexity HIGH；P1 leakage gate `audit_panel_leakage.py` 已批量 schema/null-gradient 查询并拆单层 grep helper；P1 PIT gate `audit_pit_integrity.py` 已批量 table/column inventory 并扁平化 walk-forward/forward-leak scans；P1 tradeability gate `audit_tradeability.py` 已拆静态扫描 helper并批量 spot-check raw/view counts；P1 survivorship gate `audit_survivorship_gate.py` 已批量 ever/active/panel count 并拆训练入口扫描 helper；P1 universe coverage gate `audit_universe_coverage.py` 已批量 K 线/业务表 ref-date codes 并抽稳定采样 helper；P1 TDX data need coverage gate `audit_tdx_data_need_coverage.py` 已把 source catalog/priority/reassignment 从 Python 硬编码迁到 `backend/config/tdx_data_need_coverage.yaml`，脚本只做 loader/校验/物化并补 exact-sync；P1 architecture inventory 已批量 latest-column 查询、修 nested router prefix、建立 route match index、把 `_strip_js_comments()` 改为单通道状态机、把 `_apply_dependency_context()` 改为 set-based 去重/阻断汇总，并在 2026-06-01 继续拆出 backend/frontend edge helper、import resolution helper、route lookup helper 和 DDL statement helper，清除 broad scan 中该文件 HIGH；P1 BestChoice context exit/feed 已去掉 candidate K 线 N+1、policy row per-row insert、entry 内部手写二分循环并补 helper 单测；P1 Phase7 context 已把 per-stock K-line query 改为一次批量 JOIN + context map，并补 bulk/filter/score-gate 回归测试；P1 executive trade events 已把 K-line chunk query 改为临时 code 表单次 JOIN，并用 per-code date/close index + `bisect_right` 替代 per-event 全历史扫描；P1 feature association 已去掉 DDL split execute loop、fold temp-table loop，按 fold range 过滤 base table，并把 pairwise correlation / cluster / horizon sensitivity 拆成有界 helper | backend API HIGH 已清；上述 gate scripts 无 HIGH，`audit_tdx_data_need_coverage.py` 仍有 1 个 MEDIUM membership 提示；`build_architecture_inventory.py`、BestChoice context exit/feed、Phase7 context、executive trade events、feature association 已不再出现在 complexity diff new HIGH；每刀 CodeGraph + complexity 成对；相关测试 PASS；不改变业务语义 |
| 6.5 | P0/P1 | 测试工具清理与可信度审查 | 已建立第十轮闭环并完成全部 selected test artifact backfill：registry 记录 owner/status/evidence；审计器输出 FAIL/WARN + `controller_feedback` + `gate_updates` + `unregistered_selected_slices`；显式空 scope/默认 gate non-current/marker-scope drift 均失败；前三批目录级 129 个 artifacts scoped audit PASS；root batch 4/5 与 micro-batch 6-13 已按 owner 登记；root micro-batch 13a 覆盖 updater 9 个 root tests，scoped audit PASS，pytest 89 passed / 2 warnings；root micro-batch 13b 覆盖剩余 system/strategy/tdx/v3/utils/conftest/xdxr 等 14 个 root artifacts，scoped audit PASS，默认 pytest 76 passed / 63 deselected / 15 warnings，realdb collect-only 63/66 collected；Rule 10 行为测试已登记；`test_run_feature_ablation.py` 已隔离 `alpha158.duckdb` 隐式真实库；`test_system_routes.py` 不再在 module import 阶段加载生产 `main` | `pytest.ini` 默认排除 `realdb/perf/network/gcp/slow`；full audit 当前 PASS：0 FAIL / 0 WARN / 365 selected / 365 registered / 100% coverage；后续重点转为定期审计漂移、清理过期测试和真实库 opt-in 测试逐步 contract 化，不允许把 realdb collect-only 当业务证据 |
| 6.6 | P0/P1 | 工具门禁 JSON 化 | 新增 `backend/scripts/audit_tooling_gate.py`：解析 git status、CodeGraph status、complexity markdown，输出 baseline/diff；无 baseline 时输出 `baseline_unavailable` + `unclassified_high_count`，加载 baseline 后才输出有效 `new_high_count`；`chunkyctl doctor` 默认加载本地忽略文件 `data/reports/tooling/complexity_baseline.json` 并新增 storage payload summary；2026-06-01 发现旧 baseline 只有前 40 条，清掉 feature association 后会把后续历史 HIGH 误报为 new，已刷新本地 ignored baseline 到 220 条；`audit_tooling_gate --max-findings 40` 当前 PASS / `new_high_count=0`；storage payload reviewed-column 配置后为 323 columns / 0 FAIL / 0 WARN / 12 reviewed PASS，`mart_pipeline_run_manifest.perf_summary_json` 也已加 `compact_perf_summary_payload()`，当前最大 row 约 260,408 bytes；`chunkyctl preflight` 改为 token 匹配 task risk，避免 `build` 误触发 `ui` frontend gate；新增 `docs/engineering_governance.md` 记录 GitHub 工具调研 | dirty worktree 不再只靠人工读长列表；历史 HIGH、新增 HIGH、大 payload/递归 JSON 风险可分开治理；baseline 是本地忽略工具状态，不是生产证据；当前 P0/P1 后续转向数据 freshness/PIT FAIL 与真实新增 complexity 回退 |
| 6.7 | P0/P1 | 项目专用审计/开发辅助工具雏形 | 新增 `backend/scripts/chunkyctl.py` + `scripts/chunkyctl` + `docs/chunkyctl_session_quickstart.md`，以 `audit_tooling_gate.py` 为 seed，保留 `doctor/worktree/docs/preflight/audit` 最小入口；规则来自 AGENTS/goal/skills/config，不做黑箱大 prompt；`doctor --fast` 现在同时暴露 worktree、CodeGraph、complexity baseline/diff、storage payload summary 和 system data-health snapshot；`worktree` 默认 JSON，`--format markdown` 给 controller/agent 人读审查；`preflight` 兼容 `--task/--scope` 与位置参数；`audit` 只对 `.py` scope 跑 py_compile/complexity，避免 YAML/INI 等配置误入 Python gate；quickstart 已写明启动契约维护规则、dirty resolution mode 和 storage/data-health FAIL 解释 | 新 session 默认只需按 `docs/chunkyctl_session_quickstart.md` 接手并跑 `scripts/chunkyctl doctor --fast`；dirty 时再跑 `scripts/chunkyctl worktree --format markdown` 和 bucket 下钻；storage payload FAIL 时跑 `audit_storage_payloads.py --format markdown`；data-health red tables 则先用 `data_health_snapshot.py --format markdown` 看 bucket，再回到 writer / evidence gap；若 doctor FAIL 应优先看 complexity/data/storage/test/data-health gate 而非继续找 dirty；后续增删改文档、优化工具或改变 gate/agent 启动流程时，必须同批更新 quickstart 并在 handoff/final 中说明 |
| 6.8 | P0/P1 | 文档归档 bucket 审计 | `docs_archive_moves` 三份 root 历史文档已迁入 `analysis/`，controller + subagent 均验证旧 root 与新归档逐字一致；活跃代码注释已指向 `analysis/plan_v3_20260514_archived.md`；`docs/README.md` 已升级为文档索引/生命周期规则；一次性 cron automation RCA 已移到 `analysis/cron_automation_breakage_rca_20260529.md`；旧 market perception Codex prompt 经 `rg` 验证无外部引用后删除；`goal.md` 已把 2026-05-24 及更早历史章节完整归档到 `analysis/goal_legacy_20260531.md` | staging 时三份 root 删除和三份 `analysis/` 归档必须同组处理；`cron_automation_breakage_rca_20260529.md` 与 legacy goal archive 作为 analysis evidence 处理；旧 prompt 不再保留，后续 session 只使用 `docs/chunkyctl_session_quickstart.md` |
| 6.9 | P0/P1 | artifact/cache storage 治理 | 已按 LifeHack 24.7GB 事故模型做本项目只读排查并落证据到 `analysis/lifehack_storage_bloat_analog_audit_20260531.md`；`audit_storage_payloads.py` 已固化为可重复门禁并接入 `chunkyctl doctor`。2026-06-01 完成 today signal cache 迁移：`mart_today_signal_cache` 主表只存 summary/cache metadata，新增 `mart_today_signal_cache_signal` 按 `cache_key + signal_rank` 存有界 `signal_json`；`backend/scripts/migrate_today_signal_cache_payload.py` 支持默认 dry-run、`--execute` 才写库。真实库迁移结果: 1 row / 9,286 signals / payload 20,220,095 -> 2 bytes；随后把 raw F10/GPCW/AIF10、signal detail、reason codes、lineage/source-artifact/path pointer 这 11 个非递归有界列登记为 reviewed columns，后续又将 `mart_macd_state_history.reason_codes_json` 作为 diagnostic_state_history_evidence 纳入 reviewed 列。storage audit 最新为 323 columns / 0 FAIL / 0 WARN / 12 reviewed PASS。 | `/api/signals/today` cache hit/miss 与 `signals_v2` 今日缓存回归 42 passed；reviewed PASS 由 `storage_retention.yaml` 的 owner/classification/cap 控制，递归 key、超 cap 或未许可 path marker 仍会重新告警。禁止直接清表/VACUUM 造成 API 空响应；后续 storage 治理按容量、保留期、消费者价值和可重算性排序。 |
| 6.10 | P0/P1 | 文档引用图和循环权威链清理 | Scoped cleanup complete: `docs/` 已收敛为 10 个活文档；本轮新增 `engineering_governance.md`、`data_product_contract.md`、`strategy_validation_contract.md` 三个合并契约，并把 34 个旧 active docs 迁入 `analysis/docs_archive_20260531/`；`backend/scripts/audit_docs_graph.py` 当前 PASS：13 sources / 286 total edges / 217 authority edges / 26 context-only edges / 0 unresolved live refs / 0 missing archive targets / 0 forbidden SCC / largest SCC 7 | 后续验收保持 `docs/*.md <=10`，新增活文档必须同批合并/归档/删除旧文档；`zhushenglang_hunter_research_log_20260528.md` 作为主升浪猎手产品北极星保留，框架治理结束后进入 P1 严肃复现和验证 |
| 7 | P2 | 清 paper_sim 死 YAML | `rg` 查 7 个 YAML 引用；无引用才删 | 无死引用，文档记录 |
| 8 | P0/P1 | 总验收和架构说明 | 跑 checker/codegraph/complexity/tests/diff check；输出 L0-L4 全貌 | 用户能接着做业务，不靠口头记忆 |

### 阶段 0: 现场冻结

| 动作 | 命令/方法 | 注意 |
|---|---|---|
| 查看 dirty | `scripts/chunkyctl doctor --fast` + `scripts/chunkyctl worktree --format markdown` | 以实时 gate 为准；先看当前 bucket 与 CodeGraph pending，再决定是否收束/commit；若 doctor FAIL 先看 complexity/data/test gate，再按 bucket 看 worktree |
| 查看 tracked diff | `git diff --stat` | 区分前 session 改动和本次新增 |
| 查看 untracked | `git ls-files --others --exclude-standard` | `data/phase5_exports/*.parquet` 不应误纳入架构 commit |
| 计划 stage scope | 按阶段 stage | 不使用 `git add .` |

### 阶段 1: `dim_active_a_stock` 治理

| 子任务 | 文件/范围 | 处理方式 |
|---|---|---|
| 1.1 同行 annotation | `recommendation.py`, `screening_engine.py`, `recommendation_universe.py`, `stock_detail_read.py`, `stock_graph_read.py` | 把 `rule-compliance: ok evidence=...` 放到含 `dim_active_a_stock` 的同一行 |
| 1.2 data-sync 合法枚举 | `build_price_kline_tdxhub.py`, `financial_client.py`, `capital_client.py`, `aif10_capability_client.py`, `institution_write.py` 等 | 同行 evidence: `data-sync-enumeration` / `lineage-metadata` |
| 1.3 name lookup | `run_daily_topk.py`, stock read / router name mapping | 同行 evidence: `code-to-name-mapping` |
| 1.4 schema/meta/audit | `schema_core.py`, `schema_migrations.py`, `security_master.py`, `data_lineage/registry.py`, `data_audit.py` 等 | 同行 evidence: `schema-definition`, `table-writer-itself`, `audit-config-reference` |
| 1.5 非法 universe 过滤 | 任何把 dim 表当 active universe 的业务路径 | 改用 `services.universe.get_active_universe()` 或服务 API |

阶段 1 验收命令:

```bash
PYTHONPATH=backend python backend/scripts/check_universe_filter.py --all
```

验收口径: non-test violations 必须为 0。若 test fixtures 仍报，先记录并决定是给测试夹具加 evidence，还是收紧 checker 的 test 豁免。

当前结果 (2026-05-28): `check_universe_filter.py --all` 默认 production-code only，CLEAN (767 files checked)。`--include-tests` 保留人工审计口径，当前报告 37 个 test fixture 引用。

### 阶段 2: Rule 10 blocking gate

`scripts/safe_commit.sh` 在 commit message keyword 后、`git commit` 前增加硬门:

```bash
py_staged=$(git diff --cached --name-only -- '*.py' | wc -l | tr -d ' ')
if [[ "$py_staged" -gt 0 ]]; then
    has_codex=$(echo "$MSG" | grep -cE "Codex-Reviewed:[[:space:]]*(APPROVE|APPROVE_WITH_NOTES)" || true)
    has_request_changes=$(echo "$MSG" | grep -cE "Codex-Reviewed:[[:space:]]*REQUEST_CHANGES([[:space:]]|$|\\()" || true)
    has_skip_reason=1  # only when codex-review skip reason is non-empty and 8+ chars
    if [[ "$has_request_changes" -gt 0 ]]; then
        echo "ERROR: staged .py files cannot be committed with Codex-Reviewed: REQUEST_CHANGES"
        exit 6
    fi
    if [[ "$has_codex" == "0" && "$has_skip_reason" == "0" ]]; then
        echo "ERROR: staged .py files require approved Codex review or meaningful skip reason"
        exit 6
    fi
fi
```

验收:

| 检查 | 命令/方式 |
|---|---|
| shell 语法 | `bash -n scripts/safe_commit.sh` |
| 无 review 阻断 | `backend/tests/scripts/test_safe_commit.py` 临时 staged `.py` dry-run 覆盖 exit 6 |
| 有 review 放行 | commit message 含 `Codex-Reviewed: APPROVE/APPROVE_WITH_NOTES` 或 8+ 字符 skip reason |
| REQUEST_CHANGES 阻断 | `Codex-Reviewed: REQUEST_CHANGES` 无论是否附带 skip reason 都不能通过 Rule 10 |

### 阶段 3: CodeGraph + complexity 工作流

任何 `.py` 变更必须按以下顺序执行，不攒到最后:

```bash
codegraph query "<symbol>"
codegraph context "<task>"
# edit
codegraph sync .
python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py \
  /Users/dp/Documents/M/stock/chunkymonkey/backend --format markdown
PYTHONPATH=backend python backend/scripts/check_universe_filter.py --all
git diff --check
```

若修改触及回测/验证/Optuna/GCP 入口，还必须先看:

| Gate | 检查项 |
|---|---|
| `backtest_preflight` | `universe_clean`, `limit_pct_per_board`, `cost_model`, `data_freshness`, `walk_forward`, `signal_pit_spotcheck`, `code_leakage_scan`, `excluded_stocks` |
| `plan_validator` | `search_space`, `trial_value`, `formula_runnable`, `cost_efficiency`, `param_scope`, `sample_size_coverage`, `board_coverage`, `output_usable` |
| `data_audit` | `kline_completeness`, `kline_consistency`, `board_coverage`, `date_range`, `volume_sanity`, `smartmoney_freshness`, `cross_table_consistency` |

#### 硬编码治理检查

每次新增或修改业务值时先判定 owner:

| 类型 | 默认 owner | 例外 |
|---|---|---|
| 规则、阈值、策略参数、开关 | YAML/config + loader 校验 | 测试夹具、数学常量 |
| source/path catalog、数据源优先级、迁移建议、gate evidence | 数据表、稳定 artifact 或配置 | 单次脚本局部实现细节 |
| fallback 顺序、规则解释、typed access | service module | 私有 helper 且不参与业务决策 |
| schema/enum/SQL DDL | Python 可接受 | 不能复制成生产策略 |

BLOCK 条件: 业务规则长期硬编码在 Python、同一规则在 YAML/SQL/Python 多处重复、无 owner/schema/consumer 的配置或表、router/updater 复制领域策略。

### 阶段 3.5: Complexity HIGH 治理

| 优先级 | 范围 | 当前状态 | 下一步 |
|---|---|---|---|
| P0-A | `backend/routers/v3_meta.py` | 已完成：`get_formulas()` 由 per-formula 查询改为批量查询 map，清掉 3 个 HIGH io/query-in-loop；`backend/tests/test_v3_meta.py` 27 passed, 2 warnings；`codegraph sync .` synced 29 changed files (27 added, 2 modified, 503 nodes) | 继续监控 response shape，不新增业务语义 |
| P0-B | `backend/routers/institution.py` | 已完成：`get_institution_detail()` 行业树由循环内排序改为 Counter 计数 + parent group 预排序 map；新增 helper 回归覆盖 Layer B 注释与排序；`test_institution_read.py` + `test_institution_contract.py` 14 passed, 2 warnings；backend/routers complexity 不再报告 `institution.py` HIGH | 剩余 MEDIUM membership 提示暂不阻断 |
| P0-C | `backend/routers/screening.py` | 已完成：`_to_name_keyed()` 从 route 内嵌 nested loop 改为顶层 `_rename_sector_in_value()` + 浅拷贝转换；`backend/tests/test_screening_read.py` 3 passed；backend/routers complexity 不再报告 `screening.py` HIGH | active-universe fixture 已单独修复；`test_screening_engine.py` + `test_screening_read.py` 4 passed |
| P1 | `backend/routers/v3_portfolio_builder.py` | 已完成：regime 分段统计从 router 下沉到 `services.portfolio_walk_forward.regime.summarize_regime_segments()`；新增 service + endpoint 回归；backend/routers complexity 不再报告 HIGH | 后续转向 scripts/audit/backfill HIGH，按热路径和门禁影响排序 |
| P1 | `backend/scripts/audit_delivery_readiness.py` | 已完成：循环内 `glob` 排序改为循环前预排序列表；strategy model source mart 表存在性由循环内 information_schema 查询改为一次批量查询；单文件 complexity 已无明显热点 | `test_audit_delivery_readiness.py` 16 passed；backend 全量 complexity 不再报告该文件；未运行会写 `delivery_readiness.json` 的完整审计 |
| P1 | `backend/scripts/audit_data_completeness.py` | 已完成：每张表循环内 connect/query 改为按 DB 分组后一次 UNION 汇总 max_date 与当日 n_codes；新增纯函数和 DuckDB 临时库回归测试 | `backend/tests/scripts/test_audit_data_completeness.py` 3 passed；单文件/scripts/backend complexity 不再报告该文件；脚本实跑 exit 1，因 6 张本地表 `STALE_7d⚠` |
| P1 | `backend/scripts/audit_n_plus_one.py` | 已完成：scanner 自身的 root/file/body/iterrows 嵌套扫描拆为单层 helper，循环头 `sorted(...)` 改为预排序列表；不改变 finding 规则和报告格式 | `backend/tests/scripts/test_audit_n_plus_one.py` 15 passed；脚本实跑到 `/tmp` 为 30 findings / 22 HIGH / 8 LOW / baseline OK；backend complexity 不再报告该文件 |
| P1 | `backend/scripts/audit_panel_leakage.py` | 已完成：PIT marker schema introspection 改为一次 information_schema 批量读取；flat mapping PARTITION BY、fallback ratio、NULL year gradient、forward-index grep、universe PIT grep 与 summary print 拆成单层 helper；check 6 的 per-feature SQL 改为一次按 year 聚合；不放松 leakage finding 口径 | `backend/tests/scripts/test_audit_panel_leakage.py` 4 passed；与 `test_audit_n_plus_one.py` 合跑 19 passed；backend/scripts/backend complexity 不再报告该文件；未跑真实大库 `audit_panel_leakage.py --panel ...`，避免本轮写正式 leakage report/长耗时 |
| P1 | `backend/scripts/audit_pit_integrity.py` | 已完成：walk-forward batch/OOS 表规格扁平化，information_schema 表/列读取批量化，cross-date forward leak spot-check 改为扁平 specs + 单次 UNION ALL；不改变 critical FAIL / legacy WARN / future-dated WARN 语义 | `backend/tests/scripts/test_audit_pit_integrity.py` 3 passed；与 import/leakage/nplusone 相邻测试合跑 24 passed；backend complexity 不再报告该文件；BestChoice PIT 元数据补齐后脚本实跑 PASS：9 PASS / 30 WARN / 0 FAIL |
| P1 | `backend/scripts/import_bestchoice_phase1_candidates.py` | 已完成：BestChoice Phase 1 writer 新增/迁移 `as_of_date` 与 `built_at`，默认 source 切到 repo 内 `chunkymonkey/bestchoice/analysis/...`，避免静态 challenger artifact 缺 PIT key | `test_import_bestchoice_phase1_candidates.py` 2 passed；重导入 `mart_stock_formula_optuna_bestchoice_v1` 1146 rows；`audit_pit_integrity.py` 实跑 PASS：9 PASS / 30 WARN / 0 FAIL；backend complexity 不报告该文件 HIGH |
| P1 | `backend/scripts/audit_event_timestamp.py` | 已完成：event table/column inventory 复用批量 helper；timestamp non-null、PIT lag、recent-30d sanity 从 per-table query 改为 UNION ALL 批量指标；不改变 primary FAIL / secondary WARN / unusual lag WARN 语义 | `backend/tests/scripts/test_audit_event_timestamp.py` 3 passed；与 import/PIT/leakage/nplusone 相邻测试合跑 27 passed；真实脚本实跑 PASS：55 PASS / 5 WARN / 0 FAIL；backend complexity 不再报告该文件 |
| P1 | `backend/scripts/audit_tradeability.py` | 已完成：静态 grep 拆为 file-level helper，避免 file×line×pattern 嵌套；spot check raw/view 逐日查询改为一次批量 UNION ALL 计数；不改变 suspension/limit/spot-check PASS/WARN/FAIL 语义 | `backend/tests/scripts/test_audit_tradeability.py` 3 passed；与 `test_audit_n_plus_one.py` 合跑 18 passed；真实脚本实跑 PASS：4 PASS / 4 WARN / 0 FAIL；WARN 为本地 `price_kline` 无停牌样本和近 14 天无涨跌停 proxy，不能当完整生产覆盖证据；backend complexity 不再报告该文件 |
| P1 | `backend/scripts/audit_survivorship_gate.py` | 已完成：DB 侧 ever-listed/active/panel count 从多次查询合成一次 CTE；训练入口 `is_active=1` 扫描拆成可测试 helper；默认 label_version 已对齐当前主线 `p0a_v3_horizon_governance`，旧 `p0a_v2_governance_v1` 仅作为显式历史复查口径 | `backend/tests/scripts/test_audit_survivorship_gate.py` 3 passed；与 `test_audit_n_plus_one.py` 合跑 18 passed；真实脚本实跑 PASS：label panel 5,210 codes >= 90% of ever-listed 5,210；backend complexity 不再报告该文件 |
| P1 | `backend/scripts/audit_universe_coverage.py` | 已完成：business-table coverage 从 ref_date × table 逐次查询改为批量 K 线 universe 与业务表 codes map；gap sample 排序抽到 helper；不改变 panel FAIL、event info、gap PASS/WARN/FAIL 语义 | `backend/tests/scripts/test_audit_universe_coverage.py` 4 passed；与 `test_audit_n_plus_one.py` 合跑 19 passed；真实脚本实跑 PASS：16 PASS / 6 WARN / 0 FAIL；backend/scripts/backend complexity 不再报告该文件；6 WARN 不可当 freshness 全绿证据 |
| P1 | `backend/scripts/audit_tdx_data_need_coverage.py` | 已完成：`ensure_tables` fallback 不再 split DDL 循环执行；TDX data need/source priority/reassignment catalog 从 Python 常量迁入 `backend/config/tdx_data_need_coverage.yaml`；2026-05-27 补 exact-sync；2026-05-31 将 `grain` / `pit_key` / `freshness_sla` / `evidence_status` / `production_eligibility` 设为 need contract 必填并补 enum/eligible 校验；`need_027` 明确 blocked/unknown，2026-06-01 该 audit 还把 source registration 显式写进 blocked summary：preferred `akshare` 已注册，declared fallback 标签 `miaoxiang` 则归到 `aif10` 家族，但当前 `aif10` adapter 仍未实现 `individual_fund_flow`，所以 fallback 仍是概念路径；`probe_source_capability.py` 现场探针现在已先清代理再重试，但 Eastmoney 端点仍返回 `ConnectionError` / `JSONDecodeError` remote disconnect，blocked probe 继续写入 `mart_data_source_failure_queue` 供后续 triage 复用；另有 `akshare.stock_fund_flow_individual` 研究侧排行快照，可作为主力行为研究的辅助观测，但不等同 `need_027` exact flow；2026-06-01 新增 blocked_reason 维度的 stage-opt audit 诊断后，candidate supply 侧的 bottleneck 已更清楚地落在 `below_min_signals`，不是 bar 缺失。 | `scripts/chunkyctl audit --run backend/scripts/audit_tdx_data_need_coverage.py ...` PASS；`backend/tests/scripts/test_audit_tdx_data_need_coverage.py` 16 passed；三文件 targeted pytest 25 passed；默认配置物化目标为 27 coverage / 10 priority / 14 reassignment rows；生产 DuckDB 已只读核实 `raw_fund_flow_daily` stale/deprecated，本轮未写生产 DuckDB exact-sync |
| P1 | `backend/scripts/audit_stale_references.py` | 已完成：Tier 5/6/7 结论写入 JSON 报告和 summary，避免 console-only 证据丢失；commented-out-code 检测改为 AST/SQL 可解析口径，过滤公式说明、YAML 来源说明、英文说明性注释；新增 per-run `_read_lines` cache，降低 Tier1/Tier3/Phase0 重复全仓读取风险；Phase0 denylist 仍保持 report-only，不顺手改 blocking 策略 | `backend/tests/scripts/test_audit_stale_references.py` 8 passed；真实 smoke `--no-fail --output /tmp/chunkymonkey_stale_audit_smoke.json` 写出 `summary` + Tier5/6/7 arrays，当前 critical/warn/parity/Tier5/Tier6/Tier7 均 0；单文件 complexity 为 0 HIGH / 2 MEDIUM，测试文件 0 findings |
| P1 | `backend/scripts/build_architecture_inventory.py` | 已完成三刀：`_safe_latest()` 从候选列逐条查询改为一次 SELECT 批量 latest-column 聚合；新增 nested `include_router()` app-prefix 传播，修复 lifeboat 子路由合同缺口；frontend route contract 改为静态 route set + pattern/prefix index；`_strip_js_comments()` 从嵌套 `while` 改为单通道状态机；`_apply_dependency_context()` 改为 incoming/source/target blocker set maps，统一排序/去重后赋值，test dependency 不产生 blocker | scoped test-tool audit PASS；`py_compile` PASS；`backend/tests/pipeline/test_architecture_inventory.py` + `backend/tests/contract/test_architecture_contracts.py` 15 passed；`codegraph sync .` synced 46 changed files；`check_universe_filter.py --all` CLEAN；full test-tool audit PASS；backend complexity 已不再报告旧 JS comment stripper 与 dependency context sort-in-loop 热点，但 broad scan 仍报该文件其他历史 HIGH；不能宣称 clean |

### 阶段 4: `updater.py` 拆分方案

执行前必须先跑:

```bash
codegraph query "updater"
codegraph context "updater split"
python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py \
  /Users/dp/Documents/M/stock/chunkymonkey/backend/routers --format markdown
```

目标边界:

| 模块 | 内容 | 备注 |
|---|---|---|
| `backend/routers/updater_infra.py` | UI log handler, reset logs, metrics helpers | 基础设施 helper |
| `backend/routers/updater_steps.py` | step status, normalize/sanitize, `_prime_step_status_rows`, `_sync_step_status_catalog_for_steps`, `_record_step_source_state_for_domains`, `_update_step` | 状态机核心 |
| `backend/routers/updater_calendar.py` | calendar step, trading calendar status | 日期真相源相关 |
| `backend/routers/updater_runtime.py` | `_run_blocking_db_task`, `_run_blocking_market_db_task` | 共享 runtime helper，不放业务逻辑 |
| `backend/routers/updater_audit.py` | audit snapshot refresh task、refreshing status helper、sync refresh helper | 审计快照后台刷新从主 router 分离；`routers.updater` 继续导入同名 helper |
| `backend/routers/updater_plan.py` | `STEPS`, `HARD_DEPS`, `SOFT_DEPS`, `MANUAL_ONLY_STEPS` | DAG metadata 从 router 分离；`routers.updater` 继续 re-export 兼容测试/调用 |
| `backend/routers/updater_execution.py` | hard-dependency blocking、remaining steps、K 线不可用 gap queue block/update fields、`StepRunProgress`、K 线不可用 skip helper、runner managed connection helper、group/full/single/smart 执行循环 helper | 从 smart/single/group/full route body 抽共享执行规则，不移动运行态 globals |
| `backend/routers/updater_launcher.py` | `UpdaterExecutionDeps` launcher callback bundle、`run_background_update_task` 后台任务失败/cleanup launcher、`run_smart_update_background` / `run_full_update_background` / `run_single_update_background` / `run_group_update_background` launcher helpers | route-level launcher plumbing，依赖 `updater_execution.run_smart_steps/run_all_steps/run_single_steps/run_group_steps`，避免 execution helper 继续膨胀 |
| `backend/routers/updater_status.py` | step budget、source domain、smart plan budget、critical daily filter、smart-update 计划/交易日历 preflight helper、status summary、update status payload/response connection lifecycle、smart-plan response connection lifecycle、downstream DAG helper | `updater.py` 只保留当前 STEPS/HARD_DEPS/SOFT_DEPS wrapper |
| `backend/routers/updater_reset.py` | reset table 清理常量、批量 table existence/count/delete helper、reset-derived/reset-industry response payload/connection lifecycle helper | `updater.py` 只保留 reset route 编排和 smart-update 接续 |
| `backend/routers/updater_connectivity.py` | connectivity probe、TTL cache、cached status helper | `updater.py` 只保留 route/runner 调用与 re-export |
| `backend/routers/updater_sync.py` | 独立外部 sync runner；目前承接 sync_raw、LHB/QFII/AIF10/surveys/sync_financial body | 如果超过 1000 行，拆成 sync_fetch / sync_build，不硬塞 |
| `backend/routers/updater_calc.py` | 独立计算/build/score runner；目前承接 financial/screening/sector/prediction/risk/external/stage/turtle/score/today-signal wrapper | 计算型步骤 |
| `backend/routers/updater_institution.py` | institution-domain runner；目前承接 match_inst/exclusion helper、sync_industry body 与 build_industry_stat sync body | 后续优先评估 profiles 是否同域迁入 |
| `backend/routers/updater_trends.py` | stock trend mart runner；承接 build_trends body、K 线批量读取、趋势 helper | `updater.py` 只保留 thin wrapper 注入 stop hook |
| `backend/routers/updater_profiles.py` | institution profile mart runner；承接 build_profiles body、机构画像批量聚合、持仓周期 helper | `updater.py` 只保留 thin wrapper 注入 stop hook |
| `backend/routers/updater_market_data.py` | market data runner；承接 sync_market_data body、gap queue reconciliation、daily/monthly K 线同步、xdxr sync 编排 | `updater.py` 只保留 thin wrapper 注入 stop hook/update_step |
| `backend/routers/updater_lifeboat.py` | legacy lifeboat endpoints；承接 `/lifeboat/run/status/report`、子进程执行、HTML 报告返回 | `updater.py` 只 include 子 router，API path 不变 |
| `backend/routers/updater_completeness.py` | data_completeness 覆盖率校准 helper；承接 returns/industry coverage 检查和 mart 表 `data_completeness` 标记 | 非目标 step 直接 return，避免每个 step 都做覆盖率查询；`updater.py` 只保留薄 wrapper 注入 truth source/logger |
| `backend/routers/updater.py` | API routes 薄代理 | 保持现有 endpoint 兼容 |

当前进展 (2026-05-27):

| 项 | 结果 |
|---|---:|
| 已抽模块 | `backend/routers/updater_infra.py`, `backend/routers/updater_calendar.py`, `backend/routers/updater_steps.py`, `backend/routers/updater_connectivity.py`, `backend/routers/updater_sync.py`, `backend/routers/updater_calc.py`, `backend/routers/updater_runtime.py`, `backend/routers/updater_audit.py`, `backend/routers/updater_institution.py`, `backend/routers/updater_trends.py`, `backend/routers/updater_profiles.py`, `backend/routers/updater_status.py`, `backend/routers/updater_reset.py`, `backend/routers/updater_market_data.py`, `backend/routers/updater_lifeboat.py`, `backend/routers/updater_plan.py`, `backend/routers/updater_execution.py`, `backend/routers/updater_launcher.py`, `backend/routers/updater_completeness.py` |
| `updater.py` 行数 | 5136 -> 723 |
| `updater_completeness.py` 行数 | 108 |
| `updater_execution.py` 行数 | 823 |
| `updater_launcher.py` 行数 | 278 |
| `updater_plan.py` 行数 | 130 |
| `updater_lifeboat.py` 行数 | 88 |
| `updater_market_data.py` 行数 | 765 |
| `updater_infra.py` 行数 | 258 |
| `updater_calendar.py` 行数 | 157 |
| `updater_steps.py` 行数 | 232 |
| `updater_connectivity.py` 行数 | 156 |
| `updater_sync.py` 行数 | 443 |
| `updater_calc.py` 行数 | 196 |
| `updater_runtime.py` 行数 | 34 |
| `updater_audit.py` 行数 | 53 |
| `updater_status.py` 行数 | 593 |
| `updater_reset.py` 行数 | 161 |
| `updater_institution.py` 行数 | 533 |
| `updater_trends.py` 行数 | 303 |
| `updater_profiles.py` 行数 | 455 |
| 已迁移内容 | UI log handler/reset/get logs、daily sync source metrics、step detail/status normalize/format helper；交易日历前置、日期覆盖检查、calendar refresh；DAG metadata (`STEPS`/deps/manual-only) 与 DAG 查询/计划过滤 helper（step ids/index/name/group/selected deps/selected specs/skipped outside plan）；执行编排共享规则（hard dependency blocking、remaining steps、K 线不可用 gap queue block/update fields、`StepRunProgress` full/group/smart/single 状态账本、`_begin_run` 启动状态 helper、`_prime_run_step_status` 连接生命周期 helper、`apply_step_result` runner result 落账/progress helper、`mark_remaining_stopped` stop remaining helper、`skip_if_hard_dependency_blocked` hard-dependency skipped helper、`mark_step_running` / `mark_step_stopped` / `mark_step_failed` step transition helper、`skip_if_kline_unavailable` K 线不可用 skip/gap_queue bookkeeping helper、`kline_connectivity_for_steps` K 线连通性预检 helper、`run_step_with_managed_connection` runner 独立连接生命周期 helper、`run_group_steps` group pipeline 执行循环 helper、`run_all_steps` full DAG 执行循环 helper、`run_single_steps` single-step chain 执行循环 helper、`run_smart_steps` smart plan 执行循环 helper）；launcher callback bundle / background task failure-cleanup / smart/full/single/group background launcher / group route request scheduling 已迁入 `updater_launcher.py`；data_completeness 覆盖率校准 helper；step_status prime/connection-lifecycle/mark/stale-running-cleanup/catalog-sync/source-failure-state/fail/update/result normalize；blocking runtime helper；audit snapshot refresh task/status helper 与 `/update/audit` payload helper；status summary / update status payload/response connection lifecycle / smart-plan response connection lifecycle / smart plan budget / smart-update 计划/交易日历 preflight helper / downstream DAG helper；connectivity probe/cache helper；reset table 常量、批量清理 helper 与 reset response payload/connection lifecycle；sync_raw、LHB/QFII/AIF10/surveys/sync_financial external sync runner；gen_events/calc_returns/current_relationship + financial/screening/sector/prediction/risk/external/stage/turtle/score/today-signal calc/build wrapper；match_inst/exclusion helper；build_industry_stat sync body；build_trends body 与趋势/K 线批量读取 helper；build_profiles body 与机构画像批量聚合 helper；sync_market_data body 与 gap queue/daily/monthly/xdxr 编排；lifeboat legacy endpoints（`updater.py` 保留 thin wrapper / include router） |
| 兼容处理 | `routers.updater` 继续回导出测试仍引用的私有 helper/calendar/step helper；`_prime_step_status_rows` 保留薄 wrapper 读取当前 `STEPS` |
| 验证 | `py_compile` PASS；industry/reset focused tests 8 passed, 2 warnings；status+nplusone focused tests 39 passed；nplusone/status/system focused tests 45 passed, 2 warnings；updater adjacent suite 104 passed, 2 warnings；post-cleanup smoke 38 passed, 2 warnings；checker CLEAN (764 files checked)；敏感扫描仅命中两处历史注释，无 `dim_active_a_stock` / `shift(-` / `np.roll` / GCP / Optuna 命中；`git diff --check` / project index PASS；`codegraph sync .` 完成（27 changed files: 27 added, 445 nodes）；backend complexity 仍只有既有 HIGH，未新增 touched-file HIGH |
| 剩余风险 | `codegraph status .` 因新 untracked 文件仍提示 `Added: 40 files`；`updater.py` 仍 723 LOC，route/status glue 仍可继续收薄；`updater_execution.py` 已降到 823 LOC，但仍偏大；`updater_market_data.py` 仍 765 LOC，后续可继续按 daily/monthly/xdxr 边界拆；未 stage/commit 前属于 main 工作区状态风险，不是索引未 sync |

拆分原则:

| 规则 | 做法 |
|---|---|
| 先小后大 | 先抽无副作用 helper，再抽 step 函数 |
| API 不变 | route path、response shape、status 字段不变 |
| 单模块尺寸 | 目标 < 1000 行；若 6 模块方案违反尺寸，允许拆成 7+ 模块 |
| 测试跟随 | 每次抽取后跑相关 `test_updater_*` 和 route smoke |
| 不跨层 | router 只编排，业务逻辑下沉 service 时需另立计划 |

目标测试:

```bash
PYTHONPATH=backend python -m pytest -q \
  backend/tests/test_updater_n_plus_one_fix.py \
  backend/tests/test_updater_reset_industry.py \
  backend/tests/test_updater_daily_sync_metrics.py \
  backend/tests/test_system_routes.py
```

### 阶段 5: 文档和交付同步

| 文档 | 更新内容 |
|---|---|
| `docs/implementation_plan.md` | 已改为本节同口径: 架构优先；M0/M4/M5 暂停；P0/P1 架构 gate 优先 |
| `SESSION_HANDOFF.md` | 只在交接/状态变化时更新，不和 cron 输出打架 |
| `analysis/workflow_checkpoint.md` | 业务 pipeline 状态变化才更新 |
| `goal.md` | 每批完成后更新数字、证据、下一步 |

### 最终验收标准

| 验收项 | 标准 |
|---|---|
| Universe lint | `check_universe_filter.py --all` non-test = 0 |
| Rule 10 | `safe_commit.sh` 对 staged `.py` 无 review 直接 block |
| CodeGraph | `codegraph sync .` 已完成；`status` 只允许出现已解释的 untracked `Added` 风险 |
| Complexity | backend 扫描无本轮新增 HIGH；遗留 hotspot 单独列明 |
| Tests | 变更相关 targeted tests pass；大范围改动再跑更广测试 |
| Diff hygiene | `git diff --check` pass |
| Docs | `docs/implementation_plan.md`、`goal.md`、handoff 口径一致 |
| 用户交付 | 输出 L0-L4 架构全貌、数据流、gate 位置、改前 vs 改后 |

> 2026-06-01 note: `need_027` exact-flow 已确认不是 aif10 的继续 probe 问题；`aif10` 当前没有 `individual_fund_flow` exact capability。后续只有在 registry 新增能力或 route 映射时，才重新打开 exact-flow 恢复线。

### GCP 规则保持

本计划不启动 GCP。后续如恢复 GCP/Optuna，必须先说明 objective、命令族、预计 wall time/成本、输入快照、输出路径、artifact 保存、monitor/stop/rollback，并且所有 GCP 命令必须显式带:

```bash
CHUNKYMONKEY_GCP_EXPLICIT_OK=1
```

## Legacy Archive

2026-05-24 and earlier goal sections have been archived to `analysis/goal_legacy_20260531.md`.
They are historical evidence only. Current execution authority is the
2026-05-27 architecture-reform section above plus the active docs listed in
`docs/README.md`; runtime snapshots such as `SESSION_HANDOFF.md` and
`analysis/workflow_checkpoint.md` are context-only.
