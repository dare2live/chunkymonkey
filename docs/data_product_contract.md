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

## Source And Need Contract

| Asset type | Owner |
|---|---|
| Need/source coverage | `backend/config/tdx_data_need_coverage.yaml` |
| Source priority | Config, with tdxhub primary and akshare fallback when a need requires it |
| Runtime evidence | Data tables and audit rows |
| Validation logic | Service/script modules |
| Stable thresholds | YAML/config unless a documented code exception is safer |

Every new data need must specify: `need_id`, consumer, grain, PIT key, freshness
SLA, source priority, fallback behavior, evidence status, and production
eligibility. Missing PIT or stale critical data means the downstream profile
field is `unknown` or blocked.

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
