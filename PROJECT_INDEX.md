# PROJECT_INDEX — Current Project Map

> 状态：live navigation，非规则 owner
> 更新：2026-07-19
> 当前目标看 `goal.md`（Phase A→H；策略首包=机构跟随；多源=契约可换；边做边测）。
> 架构看 `docs/MASTER_TOPLEVEL_DESIGN.md`；机器入口与 writer 清单看 `FEATURE_MAP.md` 和 CodeGraph。

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
| T0 market data | `backend/services/data_sources/`, `pipeline/`, `calendar.py`, `market_*` | Phase A 代码出口：A1–A5 闭合（calendar runtime、observation resolver、landing purity、`formal_boundaries`）。`live_readiness` 可评估（多为 BLOCKED）。K/ST accepted writer + calendar canary 仍缺；无 provider mass fetch / consumer cutover |
| T0 classification | `taxonomy.yaml`, SW/DC raw tables, DC snapshot builder | namespace 已分离；DC versioned PIT/membership 仍待 Phase 2 |
| T1 stock state | `backend/services/technical_states/`, `segments.py` | 多轴状态可复用；缺 definition/config/snapshot 版本与正式 pattern event 发布 |
| T2 market sensing | `backend/services/market_pulse.py`, API/frontend | 当前展示可用；分类解释、measurement、regime 和 persistence 耦合，暂不可直接做 PIT 特征 |
| T3 institution | `institution_profile.py` + router/tests | **首个正式策略包目标**；现仅为 research evidence，待 Phase D/E |
| T3 main rally | `rally_gt.py`, `rally_detect.py`, rally config/tests | GT 资产成熟；在机构首包之后接入同一 runtime |
| T3 formulas | `bestchoice/FROZEN.md` + `evidence_manifest.json` | 冻结 challenger；Phase G 前不吸收 |
| T4 decision/paper | `paper_portfolio.py`, frontend observation page | Legacy NONCONFORMING 观察账本；不是 paper execution |

## 3. Runtime and data layout

| Area | Role |
|---|---|
| `data/tushare_raw.duckdb` | TuShare legacy `raw_tushare_*` compatibility 表，以及 frozen margin evidence；calendar accepted landing/canonical 代码路径已就绪但 live 尚未 bootstrap/发表 |
| `data/market.duckdb` | K 线 serving/派生数据；qfq 不等于名义成交价真相 |
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
| Health | `scripts/chunkyctl doctor --fast` |
| Manual full data update | `bash scripts/daily_update.sh --date YYYYMMDD`；当前因 disabled formal domains 在 calendar/auth/provider/DB/write 前 fail closed |
| Manual single-domain sync/canary/replay | `scripts/chunkyctl sync --domain DOMAIN [--drain --max-dates N]`；disabled/formal domains fail closed before provider/DB |
| Shared tooling snapshot | `moth snapshot --repo .` |
| Business/tool assertions | `moth assert --repo .` |
| Coupling/deletion impact | `moth coupling --repo . --impact <name>` |
| Code discovery | `codegraph explore "<question>"` |
| CodeGraph refresh | `codegraph sync .` |
| Doc governance | `PYTHONPATH=backend python backend/scripts/check_doc_governance.py` |
| Doc drift | `PYTHONPATH=backend python backend/scripts/check_doc_drift.py --check` |
| Live continuity | `PYTHONPATH=backend python backend/scripts/check_continuity_integrity.py` (`FAIL` 直接非零) |
| Local reviewed commit | `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "<message>"` |

已移除的 ChunkyCtl 子命令不是工作流，调用必须返回非零；不要在活文档或生成地图中把任何 retired lifecycle 重新列为 active。

## 5. Current structural defects

| Priority | Defect | Consequence |
|---:|---|---|
| P0 | 名义 K/ST accepted writer 未建；calendar accepted 未 live bootstrap/canary | `traded_on_observation_date` 不能 live 证明；`live_readiness` 诚实 BLOCKED/NOT_EVALUATED |
| P0 | qfq serving surface has placeholder lineage and is used too broadly | Research reproducibility and execution price semantics are ambiguous |
| P0 | Legacy DC PIT residue lacks exit/re-entry/type; writer retired | Existing DB view cannot be used as historical taxonomy truth |
| P1 | Live DC snapshot/pulse tables predate namespace fix until manual rebuild | Code contract is fixed but stored rows still need controlled reconciliation |
| P1 | Market pulse mixes taxonomy, measurements, rolling/regime, write/read | One 800-line module owns multiple change reasons and incomparable methods |
| P1 | Stock state/market regime rows lack config/input version | Historical outputs cannot prove which definition produced them |
| P1 | No shared dataset snapshot/experiment/release chain | Strategy evidence cannot reach decision/product safely |
| P1 | Docs/CLI gates previously treated retired/warn as PASS | Tooling green did not prove executable reality |

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
`publish_accepted_calendar_generation`，sync 禁 legacy raw 落穿，provider canary 仍
`accepted_generation_pending`。all-due execution/population preflight 已由 full pipeline、
direct acquire 与独立 acquire stage 共用，disabled 域不得在后置失败。

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
