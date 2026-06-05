# Data Source Selection Note - 2026-06-05

Scope: compare Tonghuashun-oriented MCP/iFinD data, Tongdaxin MCP/TDX protocol data, and TuShare for ChunkyMonkey's current data-source gaps.

## Controller Verdict

Use a split decision, not one universal vendor:

| Use case | Preferred path | Reason | Current production status |
|---|---|---|---|
| `need_027` exact stock-level main/super-large/large/medium/small order flow | TuShare read-only probe first | TuShare documents daily stock money-flow interfaces with historical rows and explicit large-order buckets. `moneyflow` has buy/sell small, medium, large, extra-large fields since 2010; `moneyflow_dc` has main, extra-large, large, medium, small net fields from 2023-09-11. | Blocked until a local token-backed probe passes field/date/row stability plus PIT/freshness/writer/watermark gates. |
| Industry/theme rotation, leader diffusion, hot concept tracking | Tonghuashun semantic data first, with TuShare THS/DC interfaces as the lowest-friction structured route | TuShare exposes THS index/member/daily, THS industry/concept money flow, THS hot list, THS limit-up list, and strongest concept statistics. Direct iFinD/Tonghuashun can be richer but has heavier account/licensing integration. | Research/snapshot-only until daily PIT snapshot contracts exist. |
| Low-cost market/TDX backbone | Existing `tdxhub` plus experimental Tongdaxin MCP/xmtdx only behind probes | Project already uses TDX for K-line, F10, gpcw, xdxr, and block/industry truth. `xmtdx` advertises current and historical fund-flow commands, but this is a protocol-level community package and must be validated against field semantics, coverage, stability, and license constraints before it can affect `need_027`. | Keep as backbone/experimental candidate, not a `need_027` production replacement yet. |

## 2026-06-05 Controller Amendment: Capability Router

Do not model TuShare and `tdxhub` as a single global primary/fallback pair. Model
them as parallel capability owners. The controller decision must be made at the
data-need grain, using the cheapest source that satisfies the truth-source,
PIT/freshness, coverage, and reproducibility contract.

| Capability | Truth-source decision | Preferred route now | TuShare role | `tdxhub` / local role | Gate before production |
|---|---|---|---|---|---|
| Daily OHLCV, index bars, xdxr-derived adjustment | Trading truth is K-line + exchange calendar. | Keep current `tdxhub` production backbone until TuShare token probe and reconciliation pass. | 2000-point `daily` / `adj_factor` / `daily_basic` are strong primary candidates or cross-check feeds; call volume is not the blocker. | Current production backbone and fallback/cross-source reconciler. | Full-market date probe, price/volume/adj reconciliation, lineage and watermark gate. |
| CYQ / chip distribution metrics | CYQ is a derived transform of OHLCV + historical float shares, not an independent observed fact. | Build/validate local derived CYQ first. | 5000-point `cyq_perf` / `cyq_chips` are external benchmark/backfill candidates, not mandatory primary. | `price_kline_tdxhub` + `fact_holder_count_period` / `fact_financial_derived` can compute project CYQ; existing spec has TDX screenshot validation. | Implement `chip_distribution` service, compare against TuShare/TDX samples, record PIT float-share source, do not store full distributions unless needed. |
| Main/super-large/large/medium/small order flow (`need_027`) | Real order-flow bucket rows are the truth; rank snapshots and proxies are not exact evidence. | Remains blocked until exact probe passes. | 2000-point `moneyflow` is first low-cost candidate; 5000-point `moneyflow_dc` has more direct bucket fields; 6000-point `moneyflow_ths` can be a cross-source check. | Current project `tdxhub` path does not expose exact `need_027`; local stale `raw_fund_flow_daily` must not be revived without the gate. | Existing three-stock exact-flow probe, field mapping, date coverage, anti-scrape stability, PIT/freshness, writer/watermark, failure-queue resolution. |
| Holder count, top holders, F10 holder events | Report/F10 disclosures are the source; parsed canonical facts are derived evidence. | Keep `tdxhub_gpcw` / `tdxhub_f10` primary where already working. | Use only when it adds cheaper missing fields or a cleaner cross-check. | Current owner for holder aggregate/detail, common holders, fund holdings, and F10 event parsing. | Existing holder/F10 replay tests, source-date audit, data-health freshness. |
| THS/DC themes, board rotation, leader diffusion | Daily point-in-time membership and hotness snapshots are truth; current-only concept membership leaks. | Research/snapshot-only until daily PIT contract exists. | 6000/8000-point THS/DC endpoints are structured low-friction candidates. | TDX block data can provide stable taxonomy and cheap board membership, but not the full semantic/hotness layer. | Daily snapshot grain, membership namespace mapping, stale-membership counterexample, PIT join tests. |
| Sell-side reports / forecast consensus | Report publish date and broker forecast rows are truth. | Existing miaoxiang/aif10 remains current owner until TuShare paid data is justified. | `report_rc` needs 8000 formal permission; 10000+ is better for unrestricted backfill. | `tdxhub_gpcw` company forecasts are not sell-side consensus. | Small sample report-date probe, PIT availability, dedupe by broker/report, alpha lift or coverage proof. |

Cost rule: paying for a TuShare tier is justified only when it avoids a harder
local reconstruction or adds an observed field the local stack cannot reproduce.
If `tdxhub` plus deterministic local processing already produces the same
capability with a verifiable gate, TuShare should be benchmark/fallback, not an
automatic replacement.

## Project Facts

- `backend/config/tdx_data_need_coverage.yaml` keeps `need_027` blocked: current source is stale `raw_fund_flow_daily`, current TDX path does not provide the required capability, PIT is unknown, and production eligibility is blocked.
- `docs/chunkyctl_session_quickstart.md` requires `probe_source_capability.py --need027-exact-flow-gate` before treating `need_027` as production evidence.
- `backend/services/data_sources/clients_registry.py` currently registers `tdxhub`, `aif10`, `akshare`, and derived writers; TuShare and direct Tonghuashun/iFinD are not yet local source clients.
- `aif10` is not a valid exact-flow fallback today because it lacks `individual_fund_flow`; AkShare exact flow remains unstable in live probes.
- `docs/chip_distribution_cyq_spec.md` defines CYQ as a K-line + float-share transform and records validation against Tongdaxin screenshots; therefore TuShare paid CYQ should be evaluated as a benchmark/backfill source, not as the only path.
- `backend/services/data_sources/data_routes.py` still describes the historical three-source model (`tdxhub` / `aif10` / `akshare`). That is a current-state limitation, not a durable architecture rule once TuShare is added.

## External Evidence Checked

- TuShare `moneyflow`: stock-level money flow starts from 2010 and includes small, medium, large, and extra-large buy/sell amounts; threshold rules are documented as small < 50k, medium 50k-200k, large 200k-1m, extra-large >= 1m.
- TuShare `moneyflow_dc`: daily Eastmoney stock money flow starts from 2023-09-11 and includes main, extra-large, large, medium, and small net fields.
- TuShare THS/DC rotation endpoints include `ths_index`, `ths_member`, `ths_daily`, `moneyflow_ind_ths`, `moneyflow_cnt_ths`, `ths_hot`, `limit_list_ths`, `limit_cpt_list`, `tdx_index`, and `dc_member`.
- Tonghuashun iFinD exposes formal HTTP/Python endpoints for real-time quotes, historical quotes, basic data, data pools, snapshot/tick data, reports, smart stock picking, and trade-date queries; it is likely richer but heavier to authorize and operationalize.
- `tdx-mcp` is an MCP wrapper for public Tongdaxin quote servers and declares non-commercial learning-use constraints.
- `xmtdx` advertises `get_fund_flow()` and `get_history_fund_flow()` with extra-large/large/medium/small order-flow support, but it is not yet project-validated and should be treated as a probe candidate.

## 2026-06-05 iFinD MCP Trial Smoke

The user provided a trial iFinD MCP configuration with streamable HTTP endpoints
for stock, fund, EDB, news, bond, global-stock, and index services. The
Authorization token is sensitive: do not commit it, put it in shell arguments,
or copy it into project config. A read-only MCP protocol smoke verified 7
services and 28 tools:

| MCP service | Tool count | Relevant ChunkyMonkey capability |
|---|---:|---|
| `hexin-ifind-ds-stock-mcp` | 9 | A-share summaries, smart stock search, daily performance/technical indicators, basic info, shareholders, financials, risk indicators, events, ESG |
| `hexin-ifind-ds-index-mcp` | 2 | Index data and sector/concept board data |
| `hexin-ifind-ds-news-mcp` | 2 | News and disclosure semantic search with date range and result size |
| `hexin-ifind-ds-edb-mcp` | 1 | Macro and industry economic time series |
| `hexin-ifind-ds-fund-mcp` | 6 | Fund profile/performance/ownership/portfolio/financial/company data |
| `hexin-ifind-ds-bond-mcp` | 4 | Bond profile/market/financial/special data |
| `hexin-ifind-ds-global-stock-mcp` | 4 | HK/US stock profile/quotes/financial/events |

Official case checked: `https://mcp.51ifind.com/#/docs/example-stock-chanye`
uses an industry-chain workflow that combines news/notice search, smart stock
picking, stock financial comparison, and macro/industry EDB data. The bundled
example prompt asks for upstream/midstream/downstream segments, representative
companies, ROE/net margin/growth/PE/PB comparison, industry macro indicators,
and segment-level profitability/valuation analysis.

Pricing checked from `https://mcp.51ifind.com/#/pricing`:

| Tier | Monthly cost | Request quota | Rate limit | Incremental tools |
|---|---:|---:|---:|---|
| Trial | `0` | `2000` total trial requests | `2/s` | Base tools for connection and basic research validation |
| Personal | `40 CNY/month` or `399 CNY/year` | `5000/month` | `5/s` | Adds HK/US smart stock search and trending-news search |
| Enterprise | `5000 CNY/month` | `1,000,000/month` | `10/s` | Adds enterprise usage and EDB index search |

Read-only live smoke observations:

| Probe | Result | Project interpretation |
|---|---|---|
| `get_stock_performance` for `300750.SZ` on `2026-06-03..2026-06-04` | Returned close, price change, pct change, turnover amount, parameter metadata, and a daily-data freshness note. | Useful for ad hoc validation and cross-checks; do not replace the current `tdxhub` K-line backbone without reconciliation and watermark gates. |
| `sector_data` for the human-robot concept | Returned concept board code `001042_309119`, date-stamped component count `452`, and 5-day component average return. | Strong candidate for PIT board/theme snapshots and rotation features, but must be stored daily; current-only concept membership would leak. |
| `search_news` for human-robot chain leaders | Returned dated news snippets and URLs, including supply-chain and company-leader context. | Good for research leads and NLP/event evidence; not a direct production label without source quality and de-dup gates. |
| `search_notice` for robot annual-report business text | Returned dated disclosure snippets from annual reports. | Useful for company-business/chain-position evidence and report-text extraction. |
| `get_edb_data` for industrial robot output | Returned a standard table with dates, values, unit, and iFinD index id `M004203303`. | High value for industry-cycle and midstream demand/supply features; add an EDB index catalog before production. |
| `search_stocks` broad robot concept query | Returned an over-broad guard; narrower queries such as photovoltaic inverter companies and auto-parts market-cap screens returned stock tables. | Smart stock search is useful for interactive discovery, but production candidate generation needs deterministic query templates and result-size controls. |

Cost-performance decision: `tdxhub + iFinD MCP trial/personal` is the best
low-cost stack for maximizing current capability. Keep `tdxhub` as the free
production backbone for K-line, xdxr, GPCW/F10, holders, and deterministic local
CYQ. Use iFinD trial/personal for industry-chain semantic discovery, concept
board PIT snapshots, news/notice evidence, and EDB industry-cycle probes. Use
TuShare only where it adds structured historical rows that iFinD MCP does not
expose cleanly or where production backfill needs deterministic API fields:
`need_027` exact money flow, THS/DC membership/history backfills, report
consensus, or CYQ benchmark data.

## 2026-06-05 Local iFind Mirror Check

The user mirrored the official iFinD MCP site under
`/Users/dp/Documents/M/stock/iFind`. The mirror confirms the public docs and
frontend bundle used above, so this evidence is now locally reproducible without
re-fetching the website:

| Local page | Confirmed evidence | ChunkyMonkey interpretation |
|---|---|---|
| `mcp.51ifind.com/﹟/docs/product-data-scope.html` | Seven data domains: A-share, fund, bond, HK/US stock, index/sector, macro/industry EDB, announcement/news. Tool counts shown as A-share `9`, fund `7`, bond `4`, HK/US `5`, index/sector `2`, EDB `2`, news/notice `3`. High-frequency real-time quotes and order-book data are not supported by MCP. | Good breadth for research and small snapshots; not a replacement for tick/order-book or exact order-flow production data. |
| `mcp.51ifind.com/﹟/pricing.html` plus local JS bundle | Trial: `2000` total requests and `2/s`. Personal: `5000/month`, `5/s`, `40 CNY/month`, `399 CNY/year`. Enterprise: `1,000,000/month`, `10/s`, `5000 CNY/month`. Base A-share tools, `sector_data`, `get_edb_data`, `search_notice`, and `search_news` are available in trial/personal. `search_trending_news`, `search_funds`, and `search_global_stocks` require personal. `search_edb` requires enterprise. | Trial/personal is cost-effective for bounded daily semantic/PIT snapshots. Enterprise-only `search_edb` means EDB production should start from a curated local index catalog plus `get_edb_data`, not unbounded indicator search. |
| `mcp.51ifind.com/﹟/docs/example-stock-chanye.html` | Official industry-chain workflow combines news/notice search, smart stock picking, stock financial comparison, and macro/industry EDB. The example asks for upstream/midstream/downstream segments, representative companies, ROE/net margin/growth/PE/PB comparison, macro industry indicators, and segment-level profitability/valuation analysis. | Best fit is an analyst-assist and candidate-research workflow first. Production use needs deterministic templates, entity normalization, snapshot dates, source labels, and PIT-safe chain membership. |
| `mcp.51ifind.com/﹟/docs/example-stock-zhuti.html` | Official theme workflow uses smart stock search to identify concept-related stocks, then checks business relevance, financial indicators, and latest news. | Useful for theme discovery and leader-diffusion hypotheses; historical backtests cannot use current-only concept membership. |
| `mcp.51ifind.com/﹟/docs/example-macro-chanye.html` | Official macro-industry workflow uses EDB search/query, stock data, and news/notice to compare industries and cross-check macro trends against company profitability. | High-value path for industry-cycle features after an EDB index catalog, unit/frequency normalization, watermark, and source-date audit exist. |
| `mcp.51ifind.com/﹟/docs/skill-finance-data.html` | The downloadable skill integrates stock/fund/bond/HK-US/index-sector/macro/news-notice capabilities and expects the MCP key to be configured outside the docs. | Any local integration must keep secrets outside git and logs; project docs should store only capability metadata and smoke-test results. |

Controller decision from the local mirror: iFinD should enter the project as a
bounded research and snapshot source, not as a global vendor replacement. The
first production-shaped iFinD contracts should be daily `sector_data` snapshots,
announcement/news evidence snippets, and curated EDB series. Full production
promotion remains blocked until no-persist smoke tests prove field semantics,
quota cost, PIT grain, freshness, lineage, de-duplication, and downstream
consumer value.

Sidecar review note: an independent read-only check of the local mirror found no
evidence that changes this controller decision. iFinD's useful alpha directions
are sector/theme daily PIT snapshots, EDB industry-cycle series, news/notice
evidence snippets, company profile enrichment, and analyst-assist industry-chain
research. It should not own tick/order-book data, historical concept-membership
backfills, or `need_027` exact order-flow. Before any production promotion, run
fixed-template probes for `光伏`, `人形机器人`, and `低空经济`, record query
template hashes, result limits, source dates, provider endpoints, quota
consumption, de-dup keys, empty-result behavior, and PIT counterexamples.

## 2026-06-05 Cost Boundary: iFinD Trial/Personal vs TuShare 2000

This is a capability-cost decision, not a vendor-preference decision.

| Stack | Annual cash cost | Practical quota | Best use in this project | Do not use for |
|---|---:|---|---|---|
| `tdxhub` + iFinD trial | `0` | iFinD `2000` total trial requests, `2/s`; `tdxhub` local backbone unchanged | One-time MCP validation, 3-5 industry-chain templates, daily PIT snapshot contract design, small news/notice/EDB probes | Full-market production routes, historical backfill, repeated daily automation after the trial quota is consumed |
| `tdxhub` + iFinD personal | `399 CNY/year` or `40 CNY/month` | iFinD `5000/month`, about `166/day` if used evenly, `5/s` | Daily small-batch research snapshots: 10-20 themes, 20-50 news/notice queries, a small EDB/watchlist refresh, interactive chain/leader discovery | Full-market per-stock pulls, exact order-flow production, all-history reconstruction, unbounded NLP query loops |
| TuShare 2000 points | about `200 CNY/year` by current personal donation table | `200/min`, `100000/day/api` on eligible APIs; `moneyflow` is 2000-point eligible and not point-consuming per call | Structured historical rows and batch-friendly verification: K-line cross-check, `daily_basic`, financials, 2010+ `moneyflow` exact-flow probe | Semantic industry-chain discovery, news/notice/report text unless separate permissions or higher tiers are paid |
| TuShare 5000+ / 6000+ / 8000+ / 10000+ | capability-driven | Higher frequency or paid/special interfaces | Only if a specific capability passes sample value proof: DC/THS money flow, CYQ benchmark, THS/DC theme history, sell-side forecast/report data | Buying tiers before the no-persist probe proves fields, dates, PIT usability, and alpha/coverage value |

Boundary conclusion: maximize the free/cheap layer first. The strongest current
low-cost plan is `tdxhub` for stable bulk market facts plus iFinD trial/personal
for semantic/PIT research snapshots. TuShare 2000 is cheaper per year and much
better for structured batch APIs, so it remains the first exact-flow probe for
`need_027`. iFinD personal is better value than TuShare upgrades for
industry-chain research and leader-diffusion exploration, because it exposes the
semantic workflow directly through MCP. Production promotion still requires
daily snapshot grain, watermark, PIT join tests, de-dup/source-quality labels,
and a cache budget.

## Minimal Reversible Next Step

Do not add a writer yet.

1. Add a read-only candidate-source probe adapter shape that maps candidate provider rows into the existing `need_027` exact-flow contract:
   - `stock_code`
   - `trade_date`
   - `main_net_amount`
   - `super_large_net_amount`
   - `large_net_amount`
   - `medium_net_amount`
   - `small_net_amount`
   - source watermark and raw field lineage
2. First probe TuShare using the existing three sample stocks (`600519`, `000001`, `300750`) if a token is available.
3. Only after the probe passes should the controller design a persistent writer, watermark table, PIT/freshness evidence, and failure-queue resolution path.
4. For industry/theme/leader diffusion, define a daily snapshot contract first; do not backtest against current-only concept membership.
5. Add a capability-router source catalog before any production route change:
   - capability id and consumer
   - observed vs derived truth source
   - minimum source tier / permission
   - primary/fallback/cross-check provider
   - PIT key, watermark, and freshness SLA
   - production eligibility and failure mode
6. For iFinD, add a no-persist probe plan before any config promotion:
   - chain templates: `光伏`, `人形机器人`, `低空经济`
   - daily sector/concept snapshot grain and de-dup key
   - news/notice/EDB source quality labels
   - trial/personal quota budget and cache policy
   - production eligibility kept `research_only` until PIT/freshness/watermark gates exist

## Recommendation

| Rank | Source | Recommendation |
|---|---|---|
| 1 | Existing `tdxhub` + local processing | Keep as production backbone for K-line, xdxr, gpcw, F10/holders, and local CYQ computation. This is the default bulk layer until a replacement proves cheaper, cleaner, and equally reproducible. |
| 2 | Tonghuashun iFinD MCP trial/personal | Best low-cost semantic source for industry chain, concept boards, news/notice evidence, and industry EDB probes. Trial is enough for connection/probe; personal at 40 CNY/month is cost-effective for small daily research snapshots. |
| 3 | TuShare | Best structured candidate for standard daily facts and `need_027` probes. TuShare 2000 is enough for the first exact-flow probe; higher tiers should be capability-driven: 5000 for CYQ/DC flow, 6000 for THS flow/theme, 8000/10000 for reports/hotness/backfills. |
| 4 | Tongdaxin MCP/xmtdx | Keep as low-cost experimental probe and TDX backbone extension; not enough local evidence to promote it for `need_027` yet. |

Durable owner: source priority and production eligibility belong in `backend/config/tdx_data_need_coverage.yaml`; local client metadata belongs in `backend/services/data_sources/clients_registry.py`; proof belongs in `probe_source_capability.py`, `mart_data_source_failure_queue`, and future PIT/freshness audit outputs.
