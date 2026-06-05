# Data, Lineage, Profiles, And UI Contract

This is the active data-product contract. It consolidates the durable direction
from the former lineage, profile, incremental management, technical spec, market
perception, stock graph, UI, and data-source docs. Historical research and
detailed drafts are archived under `analysis/docs_archive_20260531/`.

## Risk First

| Risk | Rule |
|---|---|
| Wrong universe or tradeability truth | K-line is tradeability truth; calendar is date truth; `universe_rules.yaml` owns board/limit rules |
| `dim_active_a_stock` overreach | It is only code-to-name/cache/schema support, not universe truth |
| Data source drift | Data needs must declare source priority, grain, PIT key, freshness SLA, and production eligibility |
| Frontend overclaiming | UI may show `unknown`, `proxy`, or `stale`, but must not dress them as production facts |
| Giant opaque payloads in DB | DB stores queryable facts, summaries, and artifact refs; large payloads move to detail tables or governed artifacts |

## Database Ownership

`backend/config/database_manifest.yaml` is the machine-readable database
boundary map. It owns alias, path, domain, owner, online/artifact/planned state,
and default attach mode. Cross-domain attach defaults to read-only; write access
must open the target database as the primary DB or request an explicit writable
attach edge.

Default business DB entry points must consume this manifest too. In particular,
`services.db_connection` resolves its default `smartmoney` path from the
manifest while still allowing test or temporary-database overrides through the
`services.db` facade. New production scripts should prefer manifest aliases or
service entry points over private `data/*.duckdb` path literals.

Current first-principles split:

| Alias | Role | Default attach |
|---|---|---|
| `smartmoney` | Business control plane, facts, marts, governance evidence, model outputs | read-only when attached |
| `market` | K-line, calendar, benchmark, and market truth source | read-only |
| `alpha158` | Rebuildable factor feature store | read-only |
| `etf` | ETF facts and benchmark support data | read-only |
| `phase5_predictions` | Offline model artifact import source | read-only |
| `feature_store` | Planned split target for large rebuildable feature/cache tables | read-only |

Do not move tables, compact files, or delete old panels from this manifest
alone. Table movement requires a separate owner/consumer/lineage proof, a
rollback plan, and post-fix stale-artifact audit.

## Source And Need Contract

| Asset type | Owner |
|---|---|
| Need/source coverage | `backend/config/tdx_data_need_coverage.yaml` |
| Source priority | Config at capability/data-need grain; no provider is global primary |
| Runtime evidence | Data tables and audit rows |
| Validation logic | Service/script modules |
| Stable thresholds | YAML/config unless a documented code exception is safer |

Every new data need must specify: `need_id`, consumer, grain, PIT key, freshness
SLA, source priority, fallback behavior, evidence status, and production
eligibility. Missing PIT or stale critical data means the downstream profile
field is `unknown` or blocked.

Source priority is capability-level, not vendor-level. `tdxhub`, TuShare,
`aif10`/miaoxiang, AkShare, and future direct vendor adapters may all be active
in parallel when they own different observed or derived capabilities. A provider
can be primary for one need, benchmark-only for another, and blocked/probe-only
for a third. Production routing must follow the need contract and runtime gates,
not a global "primary source" label.

Candidate sources that are not production-eligible must declare structured
`required_validation` items such as field mapping, date coverage, PIT key,
freshness SLA, watermark, reconciliation, and failure-queue resolution. Notes are
context only; they are not a substitute for machine-readable gates.

## Lineage Contract

| Layer | Required fields |
|---|---|
| Raw/source | provider, source endpoint, ingested_at, source watermark |
| Fact/mart | built_at, as_of/trade date, input assets, transform version |
| Feature/profile | source table, PIT key, freshness status, evidence level |
| API/UI | lineage_ref, evidence status, last successful build |

Lineage is used for stock files, institution profiles, main-force behavior
profiles, model features, and future frontend explanations. A profile component
without source/PIT/freshness is not production evidence.

## Profile Roadmap

| Priority | Profile | Inputs |
|---:|---|---|
| 1 | Stock file basics | K-line, listing status, board rules, name/cache |
| 2 | Data quality and freshness badge | Audit tables and source watermarks |
| 3 | Institution profile | Institution events, surveys, holdings, PIT notices |
| 4 | Main-force behavior profile | CYQ, price/volume, event signals, real fund flow when restored |
| 5 | Market perception support | Regime/theme/breadth/attention signals with clear `unknown` states |

## CYQ And Main-Force Data

`docs/chip_distribution_cyq_spec.md` remains active because CYQ is a key input
for main-force behavior and 主升浪 validation. It must be implemented only after
the data contracts are satisfied: float-share history, PIT-safe disclosure
dates, K-line alignment, validation cases, and production eligibility gates.

`raw_fund_flow_daily` is currently stale/deprecated for production use. Main
force / super-large / large / medium / small order flow fields remain blocked or
`unknown` until source coverage and freshness are restored.

## Market Perception And Graph

Market perception, theme lifecycle, stock relationship graph, block trade alpha,
and external perception module work are support modules. They are not the
current P0 architecture track and must not override the 主升浪猎手 roadmap.

Allowed use after governance gates:

| Use | Rule |
|---|---|
| Regime gate | Must be PIT, freshness checked, and paper_sim tested |
| Theme/leader-follower | Must avoid current-label fallback for history |
| LHB/visible main-force signals | Treat as candidate reverse or risk signals until revalidated |
| Graph features | Additive features only; no production claims without validation |

## Frontend Contract

Frontend redesign happens after backend contracts are stable. The first screen
should be an operating cockpit for evidence and decisions, not a marketing page.

| UI area | Requirement |
|---|---|
| Today decision page | Candidate, reason, risk, freshness, lineage, and gate status |
| Stock file | Stock profile, institution profile, main-force profile, evidence status |
| Backtest/forward monitor | KPI, drawdown, costs, data freshness, PIT status |
| Unknown states | Visible and explicit; never silently hidden |

## Archived Sources

This contract supersedes or summarizes:

| Former doc group | Current state |
|---|---|
| `../analysis/docs_archive_20260531/data_lineage_spec.md`, `../analysis/docs_archive_20260531/profile_lineage_roadmap.md`, `../analysis/docs_archive_20260531/technical_specification.md`, `../analysis/docs_archive_20260531/incremental_management_spec.md` | Consolidated here |
| `../analysis/docs_archive_20260531/market_perception_data_requirements_20260529.md`, `../analysis/docs_archive_20260531/market_perception_data_onboarding_spec_20260529.md`, `../analysis/docs_archive_20260531/market_perception_optimization_plan_20260529.md`, `../analysis/docs_archive_20260531/market_perception_signal_alpha_findings_20260529.md`, `../analysis/docs_archive_20260531/market_regime_framework.md`, `../analysis/docs_archive_20260531/perception_external_module.md` | Archived as support research; current support contract summarized here |
| `../analysis/docs_archive_20260531/stock_relationship_graph_spec.md`, `../analysis/docs_archive_20260531/block_trade_alpha_spec.md` | Archived as support research |
| `../analysis/docs_archive_20260531/ui_ux_interaction_plan.md` | Archived; durable frontend contract summarized here |
| `../analysis/docs_archive_20260531/perf_p1_trade_date_migration_spec.md` | Archived as implementation evidence |
