# PROJECT_INDEX — Current Project Map

> 状态：live navigation，非规则 owner
> 更新：2026-07-18
> 当前目标看 `goal.md`；架构看 `docs/MASTER_TOPLEVEL_DESIGN.md`；机器入口与 writer 清单看 `FEATURE_MAP.md` 和 CodeGraph。

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
| T0 market data | `backend/services/data_sources/`, `pipeline/`, `calendar.py`, `market_*` | `margin` 是首个 v2 typed availability + landing/canonical/accepted tracer；读取边界为 evidence snapshot → state/reconcile → readiness/projection，历史写边界为 typed request/plan/result → checkpoint → runtime/ingest，当前仍仅两日 accepted，其他域仍待逐个迁移 |
| T0 classification | `taxonomy.yaml`, SW/DC raw tables, DC snapshot builder | namespace 已分离；DC versioned PIT/membership 仍待 Phase 2 |
| T1 stock state | `backend/services/technical_states/`, `segments.py` | 多轴状态可复用；缺 definition/config/snapshot 版本与正式 pattern event 发布 |
| T2 market sensing | `backend/services/market_pulse.py`, API/frontend | 当前展示可用；分类解释、measurement、regime 和 persistence 耦合，暂不可直接做 PIT 特征 |
| T3 institution | `institution_profile.py` + router/tests | 画像/episode 资产可复用；尚无统一实验与可执行跟随策略 |
| T3 main rally | `rally_gt.py`, `rally_detect.py`, rally config/tests | ground truth/negative/strata/embargo 资产成熟；尚未完成 B0→B2 正式消融 |
| T3 formulas | `bestchoice/FROZEN.md` + `evidence_manifest.json` | 五公式与两份全量历史机器证据仅作冻结 challenger，不能直接合并/转正 |
| T4 decision/paper | `paper_portfolio.py`, frontend observation page | Legacy NONCONFORMING 观察账本；不是 paper execution |

## 3. Runtime and data layout

| Area | Role |
|---|---|
| `data/tushare_raw.duckdb` | TuShare legacy `raw_tushare_*` compatibility 表，以及 formal margin 的 `ingest_batch`、provider landing、canonical 与 accepted pointer；其他域仍待 provider-preserving landing 迁移 |
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
| Manual full data update | `bash scripts/daily_update.sh --date YYYYMMDD` |
| Manual single-domain sync/canary/replay | `scripts/chunkyctl sync --domain DOMAIN [--drain --max-dates N]`；formal margin history 使用 `--backfill --start YYYYMMDD --end YYYYMMDD --max-dates N`（强制单域，拒绝 `--all-due`） |
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
| P0 | 只有 `margin` tracer 具备 durable provider landing、accepted partition 与 accepted-state Ops projection | 其他 legacy sync 域仍把 source response、rejection、canonical publication 和运行水位混在旧边界，必须逐域迁移 |
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

当前 margin read path 的物理 owner：`margin_evidence.py` 负责固定查询快照，`margin_state.py` 负责
accepted proof，`margin_legacy_reconcile.py`/`margin_reconcile.py` 负责纯比较与现场编排，
`margin_readiness.py`/`margin_projections.py` 只在上层组合结果；依赖不得反向。

当前 margin history path 的物理 owner：`margin_history_contract.py` 定义稳定 evidence 类型与 hash，
`margin_history.py` 负责静态 request/plan/checkpoint，`margin_history_runtime.py` 负责首错停循环，
`margin_history_ingest.py` 负责逐分区原子 compare/publish；共享 provider timeout 只由
`sync_registry.yaml` 配置并经 `runtime_limits.py` 在副作用前验证。

## 7. Generated map discipline

`FEATURE_MAP.md` is generated and must:

- distinguish active from retired commands;
- enumerate current sync domains and writer evidence;
- declare static-scan blind spots such as dynamic table names;
- be idempotent on a second generation run;
- never become a human-maintained architecture owner.

When it disagrees with live code/DB, fix the generator or lifecycle registry, regenerate, and challenge the verifier before trusting the map.

## 8. Repository boundaries

- `bestchoice/` must match `FROZEN.md` and its manifest exactly; it has no independent goal/agent/handoff/runtime and must not be folded into production during cleanup.
- `sandbox/` is disposable exploration; only `sandbox/README.md` is durable.
- `analysis/` is not a second docs tree. Keep only the ledger and necessary non-reproducible evidence; ordinary plans and narratives belong in git history.
- `.agents/skills/` mirrors generic skills and is not the project policy owner; project-specific skills live under `/Users/dp/.codex/skills/chunkymonkey-*`.
