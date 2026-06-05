# SESSION HANDOFF — Manual resume snapshot

> 此文件由 `scripts/session_snapshot.sh` / `scripts/cm_resume.sh` 按需手动刷新.
> Codex app/CLI 不再通过 cron 或 SessionStart hook 自动注入本 handoff，避免 stale state 被静默加载.
> 新会话应先按 `docs/chunkyctl_session_quickstart.md` 做启动检查，再把本文件当 context-only 状态快照.
> 业务 pipeline 进度另见 `analysis/workflow_checkpoint.md` (pull/audit/paper_sim/KPI/gate/decision).

## 中断恢复用法 (用户必读)

### 1. Mac 重启 / terminal 崩 后:
```
cd /Users/dp/Documents/M/stock/chunkymonkey
bash scripts/cm_resume.sh          # 1 命令出当前 state + prompt 模板
```

### 2. 新 Codex 会话输入哪句话:
- **推荐**: `请按照 docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查，再看 SESSION_HANDOFF.md 的 next_action。`
- **简短恢复**: `继续，看 SESSION_HANDOFF.md 和 analysis/workflow_checkpoint.md，按 next_action 推进。`
- **复杂 pipeline**: `从 analysis/workflow_checkpoint.md 推断当前 pipeline step，按 next_recovery_command 继续。`

### 3. 自动注入状态:
```
bash scripts/install_resilience.sh --status
```
默认不再安装 cron snapshot / SessionStart auto-inject；如需恢复旧自动化，必须显式设置脚本里的 legacy opt-in。

**Snapshot 时间**: 2026-06-04 13:50:35 CST

## 主线状态

| 项 | 值 |
|---|---|
| Model ID | `lgbm_phase5_v9b_20260523T083000Z` |
| Compute backend | `local` active; `modal` planned / blocked until adapter contract exists |
| Provider lifecycle | GCP project delete requested; active GCP VM/GCS execution surface removed |
| F2 checkpoint best_value | 0.3094819825339931 |
| F2 checkpoint best_trial | 32 |
| F2 updated_at | 2026-05-23T12:24:52+00:00 |
| F2 path | `data/reports/optuna/lgbm_phase5_v9b_20260523T083000Z.best.json` |

## 后台 process

| 项 | 状态 |
|---|---|
| Local monitor | dead |
| Codex companion threads | 0 running |

0

## Compute backend

| 项 | 值 |
|---|---|
| 状态 | provider-neutral job contract |
| active backend | `local` |
| planned backend | `modal` (blocked until adapter contract exists) |
| job plan command | `scripts/chunkyctl jobs --family <job-family> --backend local` |
| deleted provider evidence | ChunkyMonkey project `gen-lang-client-0821344445` is `DELETE_REQUESTED`; VM `chunkymonkey-optuna` and `gs://chunkymonkey-data-0517` deleted |

## Git 状态

| 项 | 值 |
|---|---|
| Branch | main |
| HEAD | `97c7f903 chore: exclude resume-generated checkpoints from snapshot dirtiness` |
| 最近 24h commits | 12 |
| 未 commit 文件 | 0 |

### 最近 10 commits

```
97c7f903 chore: exclude resume-generated checkpoints from snapshot dirtiness
1fa32d2d chore: align session snapshot next action
43b85626 docs: align data health startup contract
348c9b9f docs: refresh handoff after stage context production rebuild
b9e72d13 docs: record stage context production rebuild evidence
5a8273a0 docs: refresh handoff after technical-stage residual policy
ebd18209 fix: govern technical-stage residual classification
6c16bc47 docs: refresh session handoff after stage-opt gate update
9644d65d chore: expose stage-opt attrition and skill dispatch
7e1f14cb chore: capture Codex local ops and Moth profile rules
```

## NEXT ACTION (auto-computed)

**run startup checks first — scripts/chunkyctl doctor --fast; prioritize data_health blocking_yellow, then stage-opt structural blocker / need_027 blocked-gap triage**

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 14:11 CST)

- Treat `CLAUDE.md` as legacy Claude-only history. Codex policy comes from `AGENTS.md`, current docs, Codex skills, Moth evidence paths, and live tooling output unless the user explicitly asks for historical migration.
- `chunkyctl preflight` now exposes `controller_agent_gate`, `design_review_gate`, and `instruction_sources`; broad audit/research/architecture/data/debug/review/spec/triage work should dispatch bounded sidecar agents or record the concrete skip reason, and scoped architecture/data/config/table/threshold work should answer first-principles, Occam, owner, truth-source, failure-mode, and drift-blocking gate checks.
- Latest live forecast slice: `raw_profit_forecast_snapshot_daily` now has `2026-06-04` / `2,377` stocks; `mart_forecast_upside_live` now has `2026-06-04` / `2,305` stocks. `data_health_snapshot.py --dry-run --format text` improved to `green=316 / yellow=26 / red=0 / blocking_yellow=9`.
- Root cause fixed: `scripts/daily_update.sh` now refreshes `compute_forecast_upside_live.py` after raw forecast ingest using the same `FORECAST_SNAPSHOT_DATE`; `backend/tests/test_daily_update_model_refresh.py` locks this contract. The mart remains live shadow only, not training/backtest input.
- Next data-health write path should be sliced, not mixed: holder/F10, GPCW derived audits, capital sync, and feature-panel tail refresh. Re-run `doctor --fast` or `data_health_snapshot.py --dry-run --format text` after each writer slice.

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 16:45 CST)

- 2026-06-04 盘后刷新已完成。`data_health_snapshot.py --dry-run --format text` 当前为 `green=326 / yellow=16 / red=0 / blocking_yellow=0`；`scripts/chunkyctl doctor --fast` 仍为 `WARN`，但 `data_health.blocking_yellow_tables=[]`、`red_tables=[]`。下一会话不要再按 14:11 的 `blocking_yellow=9` 循环。
- K-line / feature-panel 已到 2026-06-04：本地 `build_price_kline_tdxhub.py --skip-existing --target-date 2026-06-04` 写入 `10,388` 行，`5,200` 股成功 / `000638` 失败；canonical K-line 为 `2022-01-01 -> 2026-06-04` / `5,203` codes。`sync_hs300_benchmark_kline.py` OK，`build_feature_panel_duck.py --mode incremental` 后 `fact_feature_panel` 为 `4,161,982` rows / `2023-01-03 -> 2026-06-04`，`feature_panel_prune_20260604_after_close` 显示两张 feature 表 `missing_signal_count=0` / `pruned_count=0`。
- holder/F10 / GPCW / capital 本轮 blocking 切片已处理：GPCW profile/PIT audit 已刷新；capital latest 更新到 `2026-06-04T15:00:54.088424`；F10 raw 到 2026-06-04，canonical holder replay 有 2026-06-04 行，9 个 holder/plan/trade replay 索引已恢复；`mart_shareholder_plan_initial_event` 重建为 `9,677` rows / `built_at=2026-06-04T08:33:04+00:00`。
- 本轮代码修复点：`ingest_holders_tdxhub.py` 的 replace raw replay 改成 raw-key temp table 批量删除旧 facts、跳过 holder-key 逐行 delete，并直接插入 `availability_source`，避免 DuckDB indexed delete fatal 和无索引逐行 UPDATE。相关测试 `backend/tests/test_ingest_holders_tdxhub.py` PASS。
- Moth 已提交并推送到最新：`dcb809a fix: sync ChunkyMonkey instruction sources`。GitHub repo `dare2live/moth` 为 PUBLIC，本机 `/Users/dp/.local/bin/moth` 指向 repo `.venv/bin/moth`，registry `moth profile chunkymonkey` 与 repo-local snapshot/profile 路径都能输出 `instruction_sources.ignored_by_default=["CLAUDE.md"]`。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 17:15 CST)

- `chunkyctl preflight` 的 controller/agent gate 已从文档提醒升级为机器门禁：广义 audit/research/architecture/data/debug/review/spec/triage 或 3+ 独立 scope 缺少 `--agent-dispatch` 证据时会返回 `controller_agent_dispatch_missing` 并 FAIL；`--agent-skip-reason` 只能作为 WARN 例外。后续新会话不要只在文字上承诺“总指挥模式”，要先让 preflight 看到 agent 调度证据。
- Rule 10 reviewer 指出 holder replace replay 还会留下旧 `fact_controlling_shareholder`。已修：replace replay 先解析成功 raw，再批量删除 holder/plan/trade/controlling 旧 facts；解析失败不会先删旧 facts。另补 `availability_source` 直接插入分支测试。核心 holder/chunkyctl/capital tests 当前通过。
- DB 容量只读并行审计已记录到 `analysis/db_capacity_audit_20260604.md`。`data/smartmoney.duckdb` 约 `33.6 GiB` / `34G`；未发现 `no2`、session snapshot 循环写爆，或能解释容量的 `.bak/.gz/.zst` 压缩备份副本。主因更像多版本宽面板全量并存、rank/cache 表 key 重叠、347 个索引/存储元数据开销、小表 repeated rewrite row-group bloat，以及 `formula_engine` reason JSON 总量 WARN。最可疑冗余组是 `mart_p0a_feature_label_panel` legacy/v3/v4/v5/unified，以及 `fact_feature_panel_candidate` / `fact_feature_panel_tdx_keep_challenger` / `mart_feature_rank_matrix_cache_*` 同 key 重叠。不要从这条证据直接删表或 VACUUM；下一步应单独做 retention/index/compact 方案，先备份、分类 owner、证明 consumer。
- Storage payload WARN 已用配置契约收口，不做 DB 写入/删除/压缩：`fact_technical_trigger.reason_codes_json` 与 `mart_macd_state_history.reason_codes_json` 改为 full-history evidence cap，`mart_stock_picture_daily.institution_top_json` 纳入 bounded picture summary reviewed rule。最新 live storage audit 为 `323 columns / 0 FAIL / 0 WARN / 13 reviewed`；未来 recursive key、path marker、单行或总量超 cap 仍会重新告警。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 18:45 CST)

- `scripts/chunkyctl preflight` wrapper 已补 `--agent-dispatch` / `--agent-skip-reason` 转发，项目推荐入口现在能真正把 agent evidence 传给 Python gate；缺失 evidence 的 broad work 仍会 FAIL。新增 wrapper 回归测试覆盖 positional scope、flag task、skip reason。
- DB retention 第一片已实现为 dry-run inventory：`backend/config/storage_retention.yaml` 新增 `table_inventory`，`backend/services/storage_retention.py` 输出 `table_inventory` / `table_inventory_count`，`backend/scripts/plan_storage_retention.py` 在非 execute 模式默认 read-only 打开 DuckDB。生产库只读 dry-run 当前为 `candidate_count=0`、`table_inventory_count=12`、`protected_artifact_table_count=7`、`compaction.recommended=false`；它只分类，不删除，不 VACUUM。
- sidecar 复核后的业务优先级：当前本地 P0 是收干净 controller tooling / retention dry-run 切片；下一业务 P1-A 是 `need_027` exact-flow blocked-gap triage。`data_health` 仍是 warning-only，`stage-opt` 是 P1 supply contract，`storage_payload` 为低优先级 WARN。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 19:20 CST)

- DB 模块化管理第一片已收口到 registry + attach 权限，而不是表迁移：新增 `backend/config/database_manifest.yaml` 和 `backend/services/database_manifest.py`，记录 `smartmoney` / `market` / `alpha158` / `etf` / `phase5_predictions` / planned `feature_store` 的 alias、path、owner/domain、online 状态和默认 attach mode；`analytics` 默认路径从 manifest 解析。
- `backend/services/duck_adapter.py` 现在把旧式 `attach={"market": path}` 解释为 read-only attached DB；只有显式 `{"path": path, "read_only": false}` 才允许 writable attach。只读 sidecar 未发现必须写 attached DB 的生产路径；当前常见模式是写 smartmoney、读 market/alpha158。
- 验证：controller preflight PASS 且记录 agent dispatch；`audit_test_tool_health` PASS / registry coverage 100%；`py_compile` PASS；targeted pytest `24 passed`；`paper_sim/test_ddl.py` 与 `test_candidate_feature_pipeline.py` PASS；target files complexity clean；`scripts/chunkyctl audit --run ...` PASS；CodeGraph 已 sync。没有移动表、删除表、VACUUM 或生产 DB 写入。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 19:43 CST)

- DB 连接边界第二片已把 `backend/services/db_connection.py` 的默认 `DB_PATH` / `DB_DIR` 接到 `database_manifest.smartmoney`，但仍保留 `services.db` facade monkeypatch 覆盖，兼容测试和临时库。`backend/tests/test_db.py` 新增默认路径来自 manifest 的回归。
- 验证：controller preflight PASS 且记录 agent dispatch；`audit_test_tool_health` PASS；`py_compile` PASS；targeted pytest `16 passed`；target files complexity clean；`scripts/chunkyctl audit --run ...` PASS；`git diff --check` PASS；CodeGraph 已 sync。没有生产 DB 写入、搬表、删表或 VACUUM。下一步可继续按代表脚本分片清理散落 `data/*.duckdb` 字面量；业务优先级仍是 `need_027` exact-flow probe gate。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 20:25 CST)

- `need_027` exact-flow probe gate 已实现为默认只读、不持久化的小批量 gate：默认样本从 `backend/config/tdx_data_need_coverage.yaml` 的 `need_027.source_probe_cases` 读取，覆盖 `600519/sh`、`000001/sz`、`300750/sz` 三个 `individual_fund_flow` exact case；`--cases-json` 可显式覆盖，但 case-level `persist_status` 会被拒绝，只有顶层 `--persist-status` 才写 `mart_data_source_failure_queue`。
- persistence 现在在 exact-flow validation 之后执行：exact probe 返回 `ok` 但缺日期/行数/主力/超大/大/中/小单字段时，`--persist-status` 也不会 resolve `order_flow_fund_flow`，只会继续/open validation blocker。
- gate 现在明确拒绝 rank snapshot 作为 exact-flow 证据：非 `individual_fund_flow` capability 只会计入 `ignored_for_need_027_exact_flow_gate`；`individual_fund_flow_rank_snapshot` 的 persistence domain 改为 `stock_fund_flow_rank_snapshot`，不会误清 `order_flow_fund_flow` open 行。
- live no-persist 复验：`PYTHONPATH=backend python backend/scripts/probe_source_capability.py --need027-exact-flow-gate --indent 2` 返回 `verdict=BLOCKED`、`probe_count=3`、`valid_count=0`、`failure_reasons.probe_blocked=3`；三只样本均为 `RemoteDisconnected`。因此 `need_027` 继续保持 `production_eligibility=blocked`，下一步不是 writer，而是等 exact source 稳定后再跑同一 gate，并补 PIT/freshness、writer/watermark、failure_queue resolve。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 21:15 CST)

- stage-opt upstream supply contract 第一片已落地：`backend/config/stage_opt_candidate_supply.yaml` / `backend/services/stage_opt_candidate_supply.py` 现在拥有 `fact_technical_trigger` 与 `mart_macd_state_history` 的 source role、grain、eligibility、PIT status、allowed consumers、allowed stage bins 和 research-challenger formula scope override。
- `audit_stage_opt_candidate_supply.py` 已消费该 contract 并输出 `schema_version=1` / `candidate_supply_contract`；`chunkyctl doctor` 会透传该 summary。这个切片不跑 writer、不迁表、不调 `min_signals`，所以 stage-opt 仍是 `P1 / upstream_candidate_supply` blocker；下一步如继续 stage-opt，应做 source/schema redesign，而不是 knob tuning。
- 验证：scoped test-tool audit PASS，`py_compile` PASS，stage-opt contract/audit/chunkyctl pytest `45 passed`，`backend/tests/test_build_formula_signals.py` `23 passed`，`scripts/chunkyctl audit --run ...` PASS，target files complexity clean，CodeGraph 已 sync。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-05 00:33 CST)

- `chunkyctl preflight` 已新增 `design_review_gate` 机器字段，把 `docs/engineering_governance.md` 的 Design Review Gate 显式输出到 JSON：`first_principles`、`occam`、`owner`、`truth_source`、`failure_mode`、`gate`。这补齐了“第一性原理 / 奥卡姆 / 架构师视角”只在文档里的缺口。
- Moth registry profile 已同步 repo-local instruction sources 并推送：`dcb809a fix: sync ChunkyMonkey instruction sources`；`/Users/dp/.local/bin/moth` 仍指向 repo `.venv/bin/moth`，所以本机使用的是最新 Moth。
- 提交后 live `scripts/chunkyctl doctor --fast` 为 `WARN` / worktree `PASS`。data-health 因 2026-06-05 08:39 CST 的 freshness SLA 滚动变成 `green=321 / yellow=21 / red=0 / blocking_yellow=4`，blocking 表为 `fact_financial_pit_daily`、`fact_stock_fundamental_stage_daily`、`mart_feature_drift`、`mart_feature_drift_histogram`。下一轮不要沿用 2026-06-04 16:45 的 `blocking_yellow=0` 作为当前状态。
- 2026-06-05 08:50 CST 已清掉上述 4 个 blocking-yellow。执行顺序：financial PIT tail `2026-06-03..2026-06-04`、technical stage tail `2026-06-03..2026-06-04`（第一次用 2026-01-01 预热不足被空窗口保护拒绝，未写库；第二次用 2025-01-01 成功）、picture daily `2026-06-04`、feature drift `--refresh-baseline`。复验 `doctor --fast`: `WARN` / worktree `PASS` / data-health `green=325 yellow=17 red=0 blocking_yellow=0`；feature drift writer 返回码 2 仅表示新 snapshot 里 `critical=3`，需作为模型漂移风险另行审查。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-05 09:02 CST)

- 新增可调用本地 skill `/Users/dp/.codex/skills/architect-controller/SKILL.md`，用于架构设计、总指挥、多 agent 编排、模糊需求拆解、地基优先、第一性原理/奥卡姆/证伪审查。全局 `/Users/dp/.codex/AGENTS.md`、`chunkymonkey-governance` skill、项目 `AGENTS.md`、`docs/chunkyctl_session_quickstart.md` 均已指向 `$architect-controller`。
- `.moth/profile.yaml` 已新增 `evidence_paths.skill_architect_controller`，让 Moth snapshot 和 ChunkyMonkey profile 能发现该 skill；Moth 仍只负责 shared tooling/evidence paths，不拥有 stage-opt、need_027、storage_payload、data_health 等业务 gate。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-05 09:36 CST)

- Codex app 更新后复查：当前未提交补丁仍存在；旧 `chunkyctl doctor` / `moth snapshot` / complexity scan 孤儿进程已清理。本轮继续按 controller 模式派只读 sidecar 复核 Moth 和 ChunkyMonkey diff，Codex 负责最终取舍、门禁和提交。
- Moth complexity diff 已改为正常 compare + path normalization：不再用 top-level path root 判断 baseline 不兼容；repo 内绝对路径会归一到相对路径，避免同源 finding 漂移。本机 ignored baseline `data/reports/tooling/complexity_baseline.json` 已刷新到当前全仓 scanner scope（80 条 `assets` findings），live snapshot 应为 `complexity.diff.status=compared` / `new_high_count=0`。
- `data_health_snapshot.py --dry-run` 已改为 read-only DuckDB 连接并跳过 DDL，避免 dry-run 抢写锁；复验 `--dry-run --format json` 为 `green=325 / yellow=17 / red=0`。这只修 dry-run 门禁路径，17 个 yellow 仍是 warning-only data-health debt。
- 本轮架构检查没有发现 `no2` 循环快照、压缩备份副本或快照反复落盘导致的 P0 DB 膨胀；storage 仍为 `WARN`，当前 3 条 payload WARN 归 formula/picture evidence payload。下一优先级保持：retention/compact 设计、`need_027` exact-flow、stage-opt upstream supply。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-05 10:20 CST)

- warning-only data-health P1 本地派生切片已执行：`build_current_relationship(conn)` 重建 `mart_current_relationship` 写入 `5,000` 行；`calc_dual_confirm(conn)` 更新 `mart_dual_confirm` `15,320` 条事件。两步均为本地 derived mart 写入，不拉外部网络、不跑 GCP、不改代码、不 stage/commit。
- 复验 `data_health_snapshot.py --dry-run --format json` 为 `green=335 / yellow=7 / red=0 / blocking_yellow=0`；`scripts/chunkyctl doctor --fast` 仍为 `WARN`，但 `worktree=PASS`、storage payload `PASS`，剩余业务 WARN 是 stage-opt supply 和 `need_027` exact-flow blocked。
- 剩余 7 个 data-health yellow：`fact_dzjy_event`、`fact_jgdy_event`、`raw_executive_trade`、`fact_executive_trade_event`、`fact_institution_event`、`fact_shareholder_trade`、`fact_paper_sim_trade`。不要把它们混成一个“刷新所有”批次：`build_akshare_panel.py` 对 jgdy/dzjy 是全表 drop/rebuild；高管增减持脚本也需先 dry-run；`fact_institution_event` 是 high fan-out derived chain；`fact_shareholder_trade` 要走 holder/F10 专项；`fact_paper_sim_trade` 是策略验证产物，不应只为 freshness 重跑。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-05 10:37 CST)

- 同花顺 / 通达信 / TuShare 数据源阶段研究已收束并记录到 `analysis/data_source_selection_20260605.md`。结论不是单源替换，而是按用途拆分：TuShare 是 `need_027` exact-flow 的第一只读 probe 候选；同花顺/iFinD 或同花顺语义 MCP 更适合产业链、题材热度、板块轮动和龙头扩散；通达信 MCP/xmtdx 只能作为低成本行情/板块/资金流实验候选，不能直接替代现有 `need_027` gate。
- 最小可逆下一步：若有 TuShare token，先做不持久化三股 probe（`600519`、`000001`、`300750`），把返回字段映射到 `main_net_amount`、`super_large_net_amount`、`large_net_amount`、`medium_net_amount`、`small_net_amount`、`trade_date`、watermark 和 raw lineage；只有字段、日期、行数、稳定性、PIT/freshness、writer/watermark gate 通过后，才允许设计生产 writer 或 resolve failure_queue。
- 产业链/板块轮动/龙头扩散方向先定义 daily snapshot contract，不能用 current-only 概念成分或热榜做历史回测；该方向可以作为 P1 research source onboarding，但不得绕过 `docs/data_product_contract.md` 的 source/PIT/freshness/eligibility 契约。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-05 16:05 CST)

- 已按用户最新追问复核 iFinD MCP 官方价格页公开前端 bundle、TuShare 官方权限表和本地 TuShare 镜像，并把成本边界补入 `analysis/data_source_selection_20260605.md`。iFinD trial 是 `2000` total requests / `2/s`，个人版是 `40 CNY/month` 或 `399 CNY/year`、`5000/month` / `5/s`；TuShare 2000 积分当前个人表约 `200 CNY/year`，`200/min`、`100000/day/api`，且积分是权限门槛不是逐次扣减。
- 结论调整为能力分层：`tdxhub` 继续是 K-line / xdxr / gpcw / F10 / holder / local CYQ 的生产 backbone；iFinD trial/personal 最大化用于产业链、主题、news/notice、EDB、leader-diffusion 的 daily PIT research snapshots；TuShare 2000 优先用于结构化批量和 `need_027.moneyflow` exact-flow no-persist probe，高阶 TuShare 权限只按 CYQ/DC flow/THS flow/report 等具体能力证明后再买。
- 本轮没有新增 source writer、没有持久化 iFinD/TuShare token、没有写 DB、没有改生产 source route。Tesla sidecar 结论已纳入：THS/iFinD 只能作为 capability-level research/probe 候选，生产接入仍需 PIT/freshness/watermark/source-coverage exact-sync。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-05 Local iFind Mirror)

- 用户放入的本地镜像 `/Users/dp/Documents/M/stock/iFind` 已核验并补入 `analysis/data_source_selection_20260605.md`。镜像包含 `pricing.html`、`product-data-scope.html`、`example-stock-chanye.html`、`example-stock-zhuti.html`、`example-macro-chanye.html`、`skill-finance-data.html`、前端 JS bundle 和产业链研究视频，足以作为本轮 iFinD MCP 能力/价格/案例的本地可复现证据。
- 镜像确认 iFinD MCP 是 7 个数据域 / 32+ 工具的研究型 MCP：A 股、基金、债券、港美股、指数板块、宏观行业 EDB、公告新闻；同时明确高频实时行情和盘口数据暂不支持 MCP 服务。因此它适合产业链、主题、news/notice、EDB、sector snapshot 的 `research_only/probe`，不适合直接替代 tick/order-book 或 `need_027` exact order-flow 生产证据。
- Chandrasekhar sidecar 复核不推翻当前路线：iFinD 先映射到 `need_005` 板块/主题扩展、`need_017` 主营/产业链文本证据、`need_025` 公司概况补充，以及未来 EDB/news/notice/leader-diffusion 新 need；全部先标 `research_only`。`need_027` 继续 blocked，TuShare 仍是 exact-flow 第一结构化 no-persist probe 候选；`tdxhub` / miaoxiang / aif10 的既有 backbone 不变。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-05 Compute Job Contract Follow-up)

- 旧 GCP guard 路线已替换为删除旧执行面 + `experiment_jobs` 契约：项目内 GCP scripts、GCS sync、cost tracker、phase5 monitor、gcp policy、guard test 和失效 launchd plist 均已删除或移出 active install path。
- 新入口为 `backend/config/experiment_jobs.yaml` / `backend/services/experiment_jobs.py` / `scripts/chunkyctl jobs`。当前 `local` active，`modal` planned 且阻断；data validation、backtest validation、model training、parameter search 必须声明 required gates 和 artifact contracts。
- 本机 `~/Library/LaunchAgents/com.chunkymonkey.gcp-cost-tracker.plist` 与 `~/Library/LaunchAgents/com.chunkymonkey.phase5-monitor.plist` 已检查，当前均 absent。后续不得用注释、隐藏 flag、双 latch 或 compatibility shim 恢复已删路径。

## Resilience 配置 (verified)

| 机制 | 状态 |
|---|---|
| F1 Optuna SQLite storage | deployed (`sqlite:///data/reports/optuna/$MODEL_ID.db` resume on preempt) |
| F2 per-trial checkpoint | deployed (`data/reports/optuna/$MODEL_ID.best.json` atomic write) |
| nohup + setsid + disown | retrain detached, SSH 断不影响 |
| monitor MAX_DURATION_HOURS=24 | Mac sleep proof |
| manual session_snapshot.sh | active; run via `bash scripts/cm_resume.sh` |
| cron session_snapshot.sh | disabled by default for Codex app/CLI |
| SessionStart handoff auto-inject | disabled by default for Codex app/CLI |
| Stop hook session_rule_audit | 防 multi-agent / continuous-mode 违规 |

## 一旦中断如何无缝衔接

1. **Mac 重启 / terminal 崩 后**: 启动 terminal → `cd /Users/dp/Documents/M/stock/chunkymonkey`
2. 运行 `bash scripts/cm_resume.sh` 刷新本 handoff 和 snapshot
3. 新 Codex 会话输入: `请按照 docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查，再看 SESSION_HANDOFF.md 的 next_action。`
4. Codex 先跑 live checks，再按 NEXT ACTION 执行本地工作 (audit / compare / commit / etc)

Deleted provider evidence (2026-06-05 用户要求):
- ChunkyMonkey GCP resources deleted: project `gen-lang-client-0821344445` is `DELETE_REQUESTED`; VM `chunkymonkey-optuna`, boot disk, and `gs://chunkymonkey-data-0517` were deleted first.
- Gemini API project `gen-lang-client-0274784341` is external/unowned by ChunkyMonkey evidence; live cloud evidence shows its VM runs `shadowsocks-go.service` / `shadowsocks-go-443.service`, and its 14 x 10GB snapshots are daily auto snapshots from `default-schedule-1` with 14-day retention.
- 2026-06-05 15:09 CST: `default-schedule-1` retention changed to 3 days and `onSourceDiskDelete=APPLY_RETENTION_POLICY`; 15:18 CST deleted the oldest 11 automatic snapshots and left only the latest 3 boot-disk snapshots.
- 后续重活默认通过 `experiment_jobs` 规划；Modal adapter 只有在契约、测试和 artifact manifest 通过后才能从 planned 改 active。
