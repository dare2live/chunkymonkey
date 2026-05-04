# Tech Stack Cleanup Phase 4 - ChunkyMonkey Pandas Retirement

Date: 2026-05-05

## Scope

Phase 4 starts with the data-source adapter layer. The first closed loops
removed pandas from xdxr, margin, LHB, QFII, and institution survey sync
clients after their fetch boundaries were reduced to records. The next loop
converted the K-line source boundary and akshare K-line adapter to records.
Phase 4.2 then started with the standalone neutralization helpers.
The latest Phase 4.2 loop removed the last screening-engine dependency on
pandas/numpy and kept the shared technical indicator module importable with
plain sequence inputs.
The next service loop converted the small analytics and drift helpers so
service-level query convenience and PSI drift checks no longer require
pandas/numpy.
The external-attention loop then moved the akshare table boundary to cached
records and removed table-object assumptions from snapshot/detail helpers.
The holder loop completed service-level pandas removal by converting resolver
results to records and rewriting the holder ingest writer to use DuckDB
`executemany` instead of table-object registration.
The first standalone script loop converted tdxhub price K-line backfill to
records from pull through normalization and DuckDB writes.
The next standalone script loop converted tdxhub order-book snapshots to
records/native feature math and direct DuckDB writes.
The TDX boundary loop removed the remaining pandas-backed test fixtures by
teaching F10 extra and gpcw ingestion boundaries to consume records payloads
while preserving duck-typed table compatibility.
The daily TopK loop converted recommendation scoring and risk-summary
percentiles to records/native helpers.
The LHB event loop converted raw event aggregation, forward-return labeling,
and fact writes to records/native helpers.
The executive-trade event loop converted akshare payload normalization,
shareholder aggregation, forward-return labeling, and raw/fact writes to
records/native helpers.

## Change

- Removed `import pandas` from `backend/services/xdxr_client.py`.
- Removed DataFrame compatibility from the xdxr normalizer.
- Updated `backend/tests/test_xdxr_client.py` fixtures from DataFrame to
  records.
- Removed `import pandas` from `backend/services/margin_client.py`.
- Converted SH/SZ margin source fetchers to records immediately at the akshare
  boundary.
- Updated `backend/tests/test_margin_client.py` fixtures from DataFrame to
  records.
- Removed `import pandas` from `backend/services/lhb_client.py`.
- Converted LHB source fetch and canonical field mapping to records.
- Updated `backend/tests/test_lhb_client.py` fixtures from DataFrame to
  records.
- Removed `import pandas` from `backend/services/qfii_client.py`.
- Converted QFII per-symbol fetch, quarterly merge, and canonical field mapping
  to records.
- Updated `backend/tests/test_qfii_client.py` fixtures from DataFrame to
  records.
- Removed `import pandas` from `backend/services/institution_survey_client.py`.
- Converted institution survey fetch and write preparation to records.
- Added `backend/tests/test_institution_survey_client.py`.
- Removed `import pandas` from `backend/services/kline_source.py` and
  `backend/services/akshare_client.py`.
- Converted stock, ETF, and index K-line fetchers to return records instead of
  DataFrames.
- Converted K-line monthly aggregation to a records-native helper.
- Updated ETF sync, market sync, and gap-fill script call sites to consume
  records directly.
- Updated `backend/tests/test_kline_sources.py` K-line fixtures from DataFrame
  to records.
- Removed pandas from `backend/services/neutralize.py`.
- Converted neutralization helpers to return plain dicts for keyed inputs and
  lists for positional inputs.
- Added `backend/tests/test_neutralize.py`.
- Removed pandas from `backend/services/portfolio_backtest.py`.
- Converted portfolio backtest signal normalization to records.
- Fixed zero-target reductions so a fully sold position is removed from the
  open-position state.
- Added `backend/tests/test_portfolio_backtest.py`.
- Removed pandas from `backend/services/stock_turtle_engine.py`.
- Converted turtle ATR/channel calculations to records-native helpers.
- Removed pandas/numpy from `backend/services/event_simulator.py`.
- Converted event simulation inputs, price panels, position details, and
  summary statistics to records/native math.
- Added `backend/tests/test_event_simulator.py`.
- Removed pandas/numpy and direct `ta_lib` dependency from
  `backend/services/sector_momentum.py`.
- Converted sector technical state and equal-weight index synthesis to
  records/native math.
- Removed pandas/numpy from `backend/services/screening_engine.py`.
- Converted TDX screening formulas 1/3/5 and K-line grouping to records/native
  sequence helpers.
- Removed pandas/numpy from `backend/services/ta_lib.py`.
- Converted the shared technical indicator helpers to plain sequence inputs and
  outputs.
- Fixed shared indicator missing-value handling so `NaN` is treated as missing
  in numeric helpers and false in condition helpers.
- Added `backend/tests/test_screening_engine.py` and
  `backend/tests/test_ta_lib.py`.
- Removed pandas from `backend/services/analytics.py`.
- Converted `analytics.sql()` to return records and handle statements without
  result sets.
- Removed numpy from `backend/services/ml_lifecycle/drift.py`.
- Converted PSI quantile, histogram, finite-value filtering, and severity checks
  to native Python math.
- Added `backend/tests/test_analytics.py` and `backend/tests/test_drift.py`.
- Removed pandas from `backend/services/external_attention.py`.
- Converted akshare calls, cache entries, snapshot normalization, detail series,
  research/news summaries, and timeline builders to records/native helpers.
- Added `backend/tests/test_external_attention.py`.
- Removed pandas from `backend/services/holders_resolver.py`.
- Converted `ResolverResult` holders, periods, plans, and trades payloads to
  records.
- Removed pandas/register usage from `backend/scripts/ingest_holders_tdxhub.py`.
- Converted holder raw/fact/control/plan/trade writes to direct DuckDB
  `executemany` paths and preserved duplicate-write idempotency.
- Updated `backend/tests/test_holders_resolver.py` and added
  `backend/tests/test_ingest_holders_tdxhub.py`.
- Removed pandas from `backend/scripts/build_price_kline_tdxhub.py`.
- Converted tdxhub K-line pull, dedupe/normalization, and
  `price_kline_tdxhub` writes to records and direct DuckDB `executemany`.
- Added `backend/tests/test_build_price_kline_tdxhub.py`.
- Removed pandas/numpy from `backend/scripts/build_orderbook_snapshot.py`.
- Converted order-book imbalance/spread/ratio calculations to native math and
  `fact_orderbook_snapshot` writes to direct DuckDB `executemany`.
- Added `backend/tests/test_build_orderbook_snapshot.py`.
- Removed pandas from `backend/tests/test_tdx_source.py` and
  `backend/tests/test_tdx_f10_extra_client.py`.
- Converted TDX F10 extra parser normalization to accept records payloads in
  addition to duck-typed table payloads.
- Converted gpcw sync row extraction and wide-row writes to a records adapter
  that keeps existing tdxhub table compatibility.
- Removed pandas/numpy from `backend/scripts/run_daily_topk.py`.
- Converted TopK scoring input, rank percentiles, per-regime truncation, and
  risk amount percentiles to records/native helpers.
- Added `backend/tests/test_run_daily_topk.py`.
- Removed pandas/numpy and DuckDB DataFrame registration from
  `backend/scripts/build_lhb_events.py`.
- Converted LHB raw reads, event dedupe/aggregation, forward-return labels, and
  fact writes to records/native helpers.
- Added `backend/tests/test_build_lhb_events.py`.
- Removed pandas/numpy and DuckDB DataFrame registration from
  `backend/scripts/build_executive_trade_events.py`.
- Converted executive-trade akshare payload normalization, shareholder
  aggregation, forward-return labels, and raw/fact writes to records/native
  helpers.
- Added `backend/tests/test_build_executive_trade_events.py`.

## Validation

```bash
cd /Users/dp/Documents/M/stock/chunky-monkey-v2
rg -n "import pandas|from pandas|pd\.|DataFrame" backend/services/xdxr_client.py backend/tests/test_xdxr_client.py -S
# 0 matches

rg -n "import pandas|from pandas|pd\.|DataFrame" backend/services/margin_client.py backend/tests/test_margin_client.py -S
# 0 matches

rg -n "import pandas|from pandas|pd\.|DataFrame" backend/services/lhb_client.py backend/tests/test_lhb_client.py -S
# 0 matches

rg -n "import pandas|from pandas|pd\.|DataFrame" backend/services/qfii_client.py backend/tests/test_qfii_client.py -S
# 0 matches

rg -n "import pandas|from pandas|pd\.|DataFrame" backend/services/institution_survey_client.py backend/tests/test_institution_survey_client.py -S
# 0 matches

python3 -m py_compile backend/services/xdxr_client.py backend/tests/test_xdxr_client.py
# passed

python3 -m pytest backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 6 passed

python3 -m pytest backend/tests/test_data_health_snapshot.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 10 passed

python3 -m pytest backend/tests/test_margin_client.py -q
# 11 passed

python3 -m pytest backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 17 passed

python3 -m pytest backend/tests/test_lhb_client.py -q
# 8 passed

python3 -m pytest backend/tests/test_lhb_client.py backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 25 passed

python3 -m pytest backend/tests/test_qfii_client.py -q
# 9 passed

python3 -m pytest backend/tests/test_qfii_client.py backend/tests/test_lhb_client.py backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 34 passed

python3 -m pytest backend/tests/test_institution_survey_client.py -q
# 2 passed

python3 -m pytest backend/tests/test_institution_survey_client.py backend/tests/test_qfii_client.py backend/tests/test_lhb_client.py backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 36 passed

rg -n "import pandas|from pandas|pd\.|DataFrame" backend/services/kline_source.py backend/services/akshare_client.py backend/tests/test_kline_sources.py backend/scripts/fill_missing_market_kline.py -S
# 0 code matches

python3 -m py_compile backend/services/kline_source.py backend/services/akshare_client.py backend/services/etf_engine.py backend/routers/updater.py backend/scripts/fill_missing_market_kline.py backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py
# passed

python3 -m pytest backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py -q
# 25 passed

python3 -m pytest backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py backend/tests/test_data_health_snapshot.py -q
# 29 passed

python3 -m pytest backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py backend/tests/test_institution_survey_client.py backend/tests/test_qfii_client.py backend/tests/test_lhb_client.py backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 61 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

rg -n "pandas|pd\.|DataFrame" backend/services/neutralize.py backend/tests/test_neutralize.py -S
# 0 matches

python3 -m py_compile backend/services/neutralize.py backend/tests/test_neutralize.py
# passed

python3 -m pytest backend/tests/test_neutralize.py -q
# 4 passed

python3 -m pytest backend/tests/test_neutralize.py backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py backend/tests/test_institution_survey_client.py backend/tests/test_qfii_client.py backend/tests/test_lhb_client.py backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 65 passed

rg -n "pandas|pd\.|DataFrame" backend/services/portfolio_backtest.py backend/tests/test_portfolio_backtest.py -S
# 0 matches

python3 -m py_compile backend/services/portfolio_backtest.py backend/tests/test_portfolio_backtest.py
# passed

python3 -m pytest backend/tests/test_portfolio_backtest.py -q
# 2 passed

python3 -m pytest backend/tests/test_portfolio_backtest.py backend/tests/test_neutralize.py backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py backend/tests/test_institution_survey_client.py backend/tests/test_qfii_client.py backend/tests/test_lhb_client.py backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 67 passed

rg -n "import pandas|from pandas|pd\.|DataFrame" backend/services/stock_turtle_engine.py backend/tests/test_stock_turtle_engine.py -S
# 0 matches

python3 -m py_compile backend/services/stock_turtle_engine.py backend/tests/test_stock_turtle_engine.py
# passed

python3 -m pytest backend/tests/test_stock_turtle_engine.py -q
# 4 passed

python3 -m pytest backend/tests/test_stock_turtle_engine.py backend/tests/test_portfolio_backtest.py backend/tests/test_neutralize.py backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py backend/tests/test_institution_survey_client.py backend/tests/test_qfii_client.py backend/tests/test_lhb_client.py backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 71 passed

rg -n "pandas|pd\.|DataFrame|numpy|np\." backend/services/event_simulator.py backend/tests/test_event_simulator.py -S
# 0 matches

python3 -m py_compile backend/services/event_simulator.py backend/tests/test_event_simulator.py
# passed

python3 -m pytest backend/tests/test_event_simulator.py -q
# 3 passed

python3 -m pytest backend/tests/test_event_simulator.py backend/tests/test_stock_turtle_engine.py backend/tests/test_portfolio_backtest.py backend/tests/test_neutralize.py backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py backend/tests/test_institution_survey_client.py backend/tests/test_qfii_client.py backend/tests/test_lhb_client.py backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 74 passed

rg -n "pandas|pd\.|DataFrame|numpy|np\." backend/services/sector_momentum.py backend/tests/test_sector_momentum.py -S
# 0 matches

python3 -m py_compile backend/services/sector_momentum.py backend/tests/test_sector_momentum.py
# passed

python3 -m pytest backend/tests/test_sector_momentum.py -q
# 2 passed

python3 -m pytest backend/tests/test_sector_momentum.py backend/tests/test_event_simulator.py backend/tests/test_stock_turtle_engine.py backend/tests/test_portfolio_backtest.py backend/tests/test_neutralize.py backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py backend/tests/test_institution_survey_client.py backend/tests/test_qfii_client.py backend/tests/test_lhb_client.py backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 76 passed

rg -n "pandas|pd\.|DataFrame|numpy|np\.|services\.ta_lib" backend/services/screening_engine.py backend/services/ta_lib.py backend/tests/test_screening_engine.py backend/tests/test_ta_lib.py -S
# 0 matches

python3 -m py_compile backend/services/screening_engine.py backend/services/ta_lib.py backend/tests/test_screening_engine.py backend/tests/test_ta_lib.py
# passed

python3 -m pytest backend/tests/test_ta_lib.py backend/tests/test_screening_engine.py -q
# 3 passed

python3 -m pytest backend/tests/test_ta_lib.py backend/tests/test_screening_engine.py backend/tests/test_sector_momentum.py backend/tests/test_event_simulator.py backend/tests/test_stock_turtle_engine.py backend/tests/test_portfolio_backtest.py backend/tests/test_neutralize.py backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py backend/tests/test_institution_survey_client.py backend/tests/test_qfii_client.py backend/tests/test_lhb_client.py backend/tests/test_margin_client.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 79 passed

rg -n "pandas|pd\.|DataFrame|numpy|np\." backend/services/analytics.py backend/services/ml_lifecycle/drift.py backend/tests/test_analytics.py backend/tests/test_drift.py -S
# 0 matches

python3 -m py_compile backend/services/analytics.py backend/services/ml_lifecycle/drift.py backend/tests/test_analytics.py backend/tests/test_drift.py
# passed

python3 -m pytest backend/tests/test_analytics.py backend/tests/test_drift.py backend/tests/test_ta_lib.py backend/tests/test_screening_engine.py -q
# 7 passed

python3 -m pytest backend/tests -q
# 314 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

rg -n "pandas|pd\.|DataFrame|numpy|np\.|_call_akshare_df|iterrows\(|\.empty" backend/services/external_attention.py backend/tests/test_external_attention.py -S
# 0 matches

python3 -m py_compile backend/services/external_attention.py backend/tests/test_external_attention.py
# passed

python3 -m pytest backend/tests/test_external_attention.py backend/tests/test_external_attention_sync_plan.py backend/tests/test_stock_detail_read.py -q
# 26 passed

python3 -m pytest backend/tests -q
# 317 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

rg -n "pandas|pd\.|DataFrame|read_sql_query|\.to_sql\(|\.df\(|register\(" backend/services/holders_resolver.py backend/scripts/ingest_holders_tdxhub.py backend/tests/test_holders_resolver.py backend/tests/test_ingest_holders_tdxhub.py -S
# 0 matches

rg -n "import pandas|from pandas|pd\.|DataFrame|import numpy|from numpy|np\." backend/services -S
# 0 matches

python3 -m py_compile backend/services/holders_resolver.py backend/scripts/ingest_holders_tdxhub.py backend/tests/test_holders_resolver.py backend/tests/test_ingest_holders_tdxhub.py
# passed

python3 -m pytest backend/tests/test_holders_resolver.py backend/tests/test_ingest_holders_tdxhub.py -q
# 11 passed

python3 -m pytest backend/tests -q
# 318 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

rg -n "pandas|pd\.|DataFrame|read_sql_query|\.to_sql\(|\.df\(|register\(" backend/scripts/build_price_kline_tdxhub.py backend/tests/test_build_price_kline_tdxhub.py -S
# 0 matches

python3 -m py_compile backend/scripts/build_price_kline_tdxhub.py backend/tests/test_build_price_kline_tdxhub.py
# passed

python3 -m pytest backend/tests/test_build_price_kline_tdxhub.py backend/tests/test_holders_resolver.py backend/tests/test_ingest_holders_tdxhub.py -q
# 13 passed

python3 -m pytest backend/tests -q
# 320 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

rg -n "pandas|pd\.|DataFrame|numpy|np\.|read_sql_query|\.to_sql\(|\.df\(|register\(" backend/scripts/build_orderbook_snapshot.py backend/tests/test_build_orderbook_snapshot.py -S
# 0 matches

python3 -m py_compile backend/scripts/build_orderbook_snapshot.py backend/tests/test_build_orderbook_snapshot.py
# passed

python3 -m pytest backend/tests/test_build_orderbook_snapshot.py backend/tests/test_build_price_kline_tdxhub.py -q
# 4 passed

python3 -m pytest backend/tests -q
# 322 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

rg -n "import pandas|from pandas|pd\.|DataFrame" backend/tests/test_tdx_source.py backend/tests/test_tdx_f10_extra_client.py backend/services/tdx_affair_client.py backend/services/tdx_f10_extra_client.py -S
# 0 matches

rg -n "import pandas|from pandas|pd\.|DataFrame" backend/tests -S
# 0 matches

python3 -m py_compile backend/services/tdx_f10_extra_client.py backend/services/tdx_affair_client.py backend/tests/test_tdx_source.py backend/tests/test_tdx_f10_extra_client.py
# passed

python3 -m pytest backend/tests/test_tdx_source.py backend/tests/test_tdx_f10_extra_client.py -q
# 18 passed

python3 -m pytest backend/tests -q
# 322 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

rg -n "import pandas|from pandas|pd\.|DataFrame|read_sql_query|\.to_sql\(|\.df\(|register\(|import numpy|from numpy|np\." backend/scripts/run_daily_topk.py backend/tests/test_run_daily_topk.py -S
# 0 matches

python3 -m py_compile backend/scripts/run_daily_topk.py backend/tests/test_run_daily_topk.py
# passed

python3 -m pytest backend/tests/test_run_daily_topk.py backend/tests/test_tdx_keep_productionization.py -q
# 8 passed

python3 -m pytest backend/tests -q
# 325 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

rg -n "import pandas|from pandas|pd\.|DataFrame|read_sql_query|\.to_sql\(|\.df\(|register\(|import numpy|from numpy|np\." backend/scripts/build_lhb_events.py backend/tests/test_build_lhb_events.py -S
# 0 matches

python3 -m py_compile backend/scripts/build_lhb_events.py backend/tests/test_build_lhb_events.py
# passed

python3 -m pytest backend/tests/test_build_lhb_events.py -q
# 3 passed

python3 backend/scripts/build_lhb_events.py --dry-run
# raw=62301, deduped_events=52550, forward_coverage_20d=73.4%, forward_coverage_60d=73.4%

python3 -m pytest backend/tests -q
# 328 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

rg -n "import pandas|from pandas|pd\.|DataFrame|read_sql_query|\.to_sql\(|\.df\(|register\(|import numpy|from numpy|np\." backend/scripts/build_executive_trade_events.py backend/tests/test_build_executive_trade_events.py -S
# 0 matches

python3 -m py_compile backend/scripts/build_executive_trade_events.py backend/tests/test_build_executive_trade_events.py
# passed

python3 -m pytest backend/tests/test_build_executive_trade_events.py -q
# 4 passed

python3 backend/scripts/build_executive_trade_events.py --dry-run
# raw=143825, events=68281, forward_coverage_20d=82.3%, forward_coverage_60d=82.3%

python3 -m pytest backend/tests -q
# 332 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0
```

## Remaining Phase 4 Targets

- pandas usage in scripts and model/backtest layers.
