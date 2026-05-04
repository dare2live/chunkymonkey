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

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0
```

## Remaining Phase 4 Targets

- pandas usage in scripts and model/backtest layers.
