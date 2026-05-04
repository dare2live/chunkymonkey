# Tech Stack Cleanup Phase 3 - tdxhub Records Boundary

Date: 2026-05-04

## Scope

Phase 3 mapped ChunkyMonkey's live tdxhub usage and landed the first
records-first boundary in the holder research path.
The follow-up block/cfg reader loop removed pandas from tdxhub's local
block/config parsing path, added a lightweight market-code helper, and made
`tdxhub.reader` avoid importing tabular dependencies until callers enter the
remaining legacy bar-reader paths.

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
- Added records-first wrappers for the ChunkyMonkey-used `Quotes` paths:
  `quotes_records`, `bars_records`, `index_bars_records`, `stocks_records`,
  `xdxr_records`, `finance_records`, `block_records`, `minute_records`,
  `minutes_records`, `transaction_records`, and `transactions_records`.
- Updated ChunkyMonkey's tdxhub pin to
  `ca5f9ee09b5f6ee415e22b0454c79e5ed4bd9fc5`.
- Updated ChunkyMonkey's tdxhub pin again to
  `b72825b894f2fcc1bdb7fcc8ac0859ee15bed8ef`.
- Kept legacy DataFrame helpers unchanged for existing callers.
- Switched ChunkyMonkey's holder source and data source adapter to consume
  `parse_research_records`.
- Switched ChunkyMonkey's tdxhub K-line, index K-line, ETF list, xdxr, block,
  financial snapshot, quote, minute, and tick call sites to consume records
  wrappers.
- Converted records back to DataFrames only at the current ChunkyMonkey
  pandas-era script/calculation boundaries that will be removed during Phase 4.

## Contract Scan

```bash
rg -n "client\.(bars|index_bars|stocks|xdxr|finance|block|quotes|minute|minutes|transaction|transactions)\(" \
  backend/services backend/scripts -S
# 0 matches
```

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

cd /Users/dp/Documents/M/stock/tdxhub
python3 -m pytest tests/test_quotes_utils.py tests/test_holders.py -q
# 56 passed

cd /Users/dp/Documents/M/stock/chunky-monkey-v2
python3 -m pytest backend/tests/test_block_client.py backend/tests/test_xdxr_client.py backend/tests/test_financial_client.py backend/tests/test_tdx_source.py backend/tests/test_kline_sources.py -q
# 40 passed

python3 -m pytest backend/tests/test_utils.py backend/tests/test_kline_sources.py backend/tests/test_tdx_source.py backend/tests/test_audit_financial.py backend/tests/test_scoring_engine.py backend/tests/test_scoring_composite.py -p no:cacheprovider --tb=short -q
# 57 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

python3 backend/scripts/audit_stale_references.py --phase0-only --output /tmp/phase3_records_scan.json
# old_db_path=0, old_external_link=0, old_source_route=0
# sqlite_runtime=0, sqlite_test=0, sqlite_docs=0

cd /Users/dp/Documents/M/stock/tdxhub
python3 -m pytest tests/reader/test_reader_block.py tests/reader/test_reader_parse.py tests/reader/test_reader_blocknew.py tests/reader/test_reader_no_tabular_import.py tests/tools/test_customize.py -q
# 21 passed

python3 - <<'PY'
import importlib.abc
import sys

class BlockTabular(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = 'pan' + 'das'
        if fullname == blocked or fullname.startswith(blocked + '.'):
            raise ImportError('blocked tabular dependency import')
        return None

sys.meta_path.insert(0, BlockTabular())
import tdxhub
from tdxhub.parse import BaseParse
from tdxhub.reader import StdReader
from tdxhub.tools.customize import Customize
print('top-level and block reader paths import ok without tabular dependency')
PY
# passed
```

`python3 -m pytest -q` in `tdxhub` still includes live TDX server and cache
integration tests. It was run on 2026-05-05 and failed in those external
network-dependent cases (`socket.timeout`, empty TDX response header, missing
local xdxr cache). The records wrapper tests above are offline and isolate the
Phase 3 code change.

## Remaining Work

- Move the ChunkyMonkey holder ingest persistence layer off DataFrame
  registration.
- Continue removing tdxhub legacy tabular wrappers after all downstream callers
  have migrated and tests no longer need legacy fixtures.
