# Tech Stack Cleanup Phase 3 - tdxhub Records Boundary

Date: 2026-05-04

## Scope

Phase 3 mapped ChunkyMonkey's live tdxhub usage and landed the first
records-first boundary in the holder research path.

## Current tdxhub Call Map

- `services.tdx_source`: connection pool and retry wrapper for `Quotes`.
- `services.akshare_client`: `bars`, `stocks`, `index_bars` for market data.
- `services.xdxr_client`: `xdxr`.
- `services.block_client`: `block`.
- `services.financial_client`: `finance`.
- `services.tdx_affair_client`: `Affair.files`, `Affair.parse`.
- `services.holders_resolver`: `HolderFetcher.fetch_text` plus holder parsing.
- `services.data_sources.sources.tdxhub`: registry adapter for quotes, kline,
  financial, block, stock list, and holder capabilities.
- Scripts still importing direct tdxhub classes: `build_price_kline_tdxhub.py`,
  `build_orderbook_snapshot.py`, `build_fundamental_quarterly.py`.

## Change

- Added `tdxhub.holders.parse_holders_records`.
- Added `tdxhub.holders.parse_research_records`.
- Added `HolderFetcher.fetch_research_records`.
- Updated ChunkyMonkey's tdxhub pin to
  `82f2776be015c4a632a51ffc78d4fe8febd4ea4c`.
- Kept legacy DataFrame helpers unchanged for existing callers.
- Switched ChunkyMonkey's holder source and data source adapter to consume
  `parse_research_records`.
- Converted records back to DataFrames only at the current ChunkyMonkey
  persistence boundary, where the ingest code still registers DataFrames with
  DuckDB.

## Validation

```bash
cd /Users/dp/Documents/M/stock/tdxhub
python3 -m pytest tests/test_holders.py -q
# 46 passed

cd /Users/dp/Documents/M/stock/chunky-monkey-v2
python3 -m pytest backend/tests/test_holders_resolver.py -q
# 10 passed

python3 -m pytest backend/tests/test_tdx_f10_extra_client.py -q
# 4 passed

python3 -m py_compile backend/services/holders_resolver.py backend/services/data_sources/sources/tdxhub.py
# passed
```

## Remaining Work

- Move the ChunkyMonkey holder ingest persistence layer off DataFrame
  registration.
- Add records variants for quote, kline, xdxr, financial, block, and stock list
  APIs before removing pandas from tdxhub runtime imports.
