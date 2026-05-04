# Tech Stack Cleanup Phase 3 - tdxhub Records Boundary

Date: 2026-05-04

## Scope

Phase 3 mapped ChunkyMonkey's live tdxhub usage and landed the first
records-first boundary in the holder research path.
The follow-up block/cfg reader loop removed the tabular dependency from tdxhub's local
block/config parsing path, added a lightweight market-code helper, and made
`tdxhub.reader` avoid importing tabular dependencies until callers enter the
remaining legacy bar-reader paths.
The cache-helper loop then generalized tdxhub's file and timed cache
decorators from table-specific return types to plain Python objects, so
`tdxhub.cache` is importable without tabular dependencies.
The tdx2csv tool loop replaced its table-object CSV conversion with stdlib
CSV parsing/writing and records output.
The quote API loop then moved `tdxhub.utils.to_data`, the socket-client
conversion helper, `StdQuotes`, `ExtQuotes`, and the capability catalog to
records output. The old local-reader adjustment flags now fail explicitly in
records mode instead of pretending to produce adjusted rows through the
retired tabular path.
The local bar-reader loop converted day/minute/extended-market file readers to
records output while keeping the old method name as a compatibility shim.
The utility cleanup loop removed the unused old cache helper and converted
the holiday calendar utility and tests to records/native standard-library
parsing.
The adjustment cleanup loop removed the retired factor/adjust/reversion
helpers and their network-shaped tests after confirming ChunkyMonkey and
miaoxiang no longer import them.
The capability probe loop removed script-side table-specific result
summarization so records, dicts, lists, and scalar results are handled
directly.
The financial-reader loop moved the historical gpcw parser and legacy history
crawler conversion helpers to records while keeping the old method name as a
compatibility shim.
The holder-parser loop moved the F10 holder parser, holder fixture tests, and
universe utility scripts to records-native list/dict handling.

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
- Updated ChunkyMonkey's tdxhub pin again to
  `8361d332eae894abf7c4ad8597d59af4aab641d3`.
- Updated ChunkyMonkey's tdxhub pin again to
  `4272b001eba034c52ac55d1d87ced62bea006642`.
- Updated ChunkyMonkey's tdxhub pin again to
  `354b57f1ef3461b99378f7d5bf1b1ab392cd4d69`.
- Updated ChunkyMonkey's tdxhub pin again to
  `dcd194cd6f2dbb1b4734f2af784f195336c7d489`.
- Updated ChunkyMonkey's tdxhub pin again to
  `c2c2b564d25f73cd33029a831edea4489454da4a`.
- Updated ChunkyMonkey's tdxhub pin again to
  `038dcc1b225236f741a81ba9c8bbc01ab11bf8b1`.
- Updated ChunkyMonkey's tdxhub pin again to
  `d76527dd62c8d9f3c08fb06ecdcd8d3a130ae4d8`.
- Updated ChunkyMonkey's tdxhub pin again to
  `113da7d79bdc255e6e6bea8b724b76f608cba2b3`.
- Updated ChunkyMonkey's tdxhub pin again to
  `45c805a464b59e58f09e4dc2b055f80d488a21f6`.
- Converted the core quote API helpers and capability catalog to records
  output.
- Converted local day/minute/extended-market bar readers to records output and
  verified real fixture reads without the old tabular dependency importable.
- Removed the unused old cache helper and converted the holiday calendar
  utility/tests to records output.
- Removed the retired factor/adjust/reversion helpers and their old external
  network tests.
- Removed the remaining table-specific capability probe summary branch.
- Converted tdxhub historical financial readers and crawler conversion helpers
  to records output.
- Converted the tdxhub holder parser and holder universe scripts to records
  output and direct DuckDB record writes.
- Kept the remaining legacy parser modules isolated behind lazy imports for
  later records-native conversion.
- Switched ChunkyMonkey's holder source and data source adapter to consume
  `parse_research_records`.
- Switched ChunkyMonkey's tdxhub K-line, index K-line, ETF list, xdxr, block,
  financial snapshot, quote, minute, and tick call sites to consume records
  wrappers.
- During early Phase 3, records were converted back only at the then-existing
  table-object script/calculation boundaries; Phase 4 later retired those
  ChunkyMonkey boundaries.

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

python3 -m pytest tests/cache/test_file.py tests/reader/test_reader_block.py tests/reader/test_reader_parse.py tests/reader/test_reader_blocknew.py tests/reader/test_reader_no_tabular_import.py tests/tools/test_customize.py -q
# 24 passed

python3 -m pytest tests/tools/test_tdx2csv.py tests/cache/test_file.py tests/reader/test_reader_block.py tests/reader/test_reader_parse.py tests/reader/test_reader_blocknew.py tests/reader/test_reader_no_tabular_import.py tests/tools/test_customize.py -q
# 26 passed

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
from tdxhub.cache import file_cache, lru_cache, timeit
print('cache imports ok without tabular dependency')
PY
# passed

python -m pytest tests/utils/test_utils.py tests/test_quotes_utils.py tests/reader/test_reader_std.py tests/reader/test_reader_no_tabular_import.py tests/reader/test_reader_block.py tests/reader/test_reader_blocknew.py tests/tools/test_customize.py tests/tools/test_tdx2csv.py -q
# 52 passed

python3 -m py_compile tdxhub/utils/__init__.py tdxhub/protocol/base_socket_client.py tdxhub/protocol/hq.py tdxhub/quotes.py tdxhub/capabilities.py tests/utils/test_utils.py tests/test_quotes_utils.py tests/reader/test_reader_std.py
# passed

python3 - <<'PY'
import builtins
real_import = builtins.__import__

def blocked(name, globals=None, locals=None, fromlist=(), level=0):
    blocked_name = 'pan' + 'das'
    if name == blocked_name or name.startswith(blocked_name + '.'):
        raise ModuleNotFoundError('blocked tabular import')
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = blocked
import tdxhub
import tdxhub.utils
import tdxhub.protocol.base_socket_client
import tdxhub.protocol.hq
import tdxhub.quotes
import tdxhub.capabilities
print('python3 quote import ok without tabular dependency')
PY
# passed

cd /Users/dp/Documents/M/stock/chunky-monkey-v2
python3 -m pytest backend/tests/test_tdx_source.py backend/tests/test_kline_sources.py backend/tests/test_block_client.py backend/tests/test_xdxr_client.py backend/tests/test_financial_client.py -q
# 40 passed

python3 -m pytest backend/tests -q
# 363 passed

python3 backend/scripts/data_health_snapshot.py --dry-run
# green=147/yellow=0/red=0

python3 backend/scripts/audit_stale_references.py --phase0-only --output /tmp/phase3_quote_pin_scan.json
# old_db_path=0, old_external_link=0, old_source_route=0
# sqlite_runtime=0, sqlite_test=0, sqlite_docs=0
# remaining tabular findings are in tdxhub/doc/test residual paths outside this quote API loop

cd /Users/dp/Documents/M/stock/tdxhub
python -m pytest tests/utils/test_utils.py tests/test_quotes_utils.py tests/reader/test_reader_std.py tests/reader/test_reader_ext.py tests/reader/test_reader_base.py tests/reader/test_reader_no_tabular_import.py tests/reader/test_reader_block.py tests/reader/test_reader_blocknew.py tests/reader/test_reader_parse.py tests/tools/test_customize.py tests/tools/test_tdx2csv.py -q
# 67 passed

python3 -m py_compile tdxhub/protocol/reader/daily_bar_reader.py tdxhub/protocol/reader/min_bar_reader.py tdxhub/protocol/reader/lc_min_bar_reader.py tdxhub/protocol/reader/exhq_daily_bar_reader.py tdxhub/protocol/reader/base_reader.py tests/reader/test_reader_std.py
# passed

python3 - <<'PY'
import builtins
real_import = builtins.__import__

def blocked(name, globals=None, locals=None, fromlist=(), level=0):
    blocked_name = 'pan' + 'das'
    if name == blocked_name or name.startswith(blocked_name + '.'):
        raise ModuleNotFoundError('blocked tabular import')
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = blocked
from tdxhub.reader import Reader
std = Reader.factory(market='std', tdxdir='tests/fixtures')
ext = Reader.factory(market='ext', tdxdir='tests/fixtures')
assert std.daily(symbol='127021')
assert std.minute(symbol='688001', suffix='1')
assert ext.daily(symbol='4#CF7D0LAO')
print('reader daily/minute calls ok without tabular dependency')
PY
# passed

python -m pytest tests/utils/test_holiday_dependency.py tests/utils/test_holiday.py tests/utils/test_utils.py tests/utils/test_timer.py tests/test_quotes_utils.py tests/reader/test_reader_std.py tests/reader/test_reader_ext.py tests/reader/test_reader_base.py tests/reader/test_reader_no_tabular_import.py tests/reader/test_reader_block.py tests/reader/test_reader_blocknew.py tests/reader/test_reader_parse.py tests/tools/test_customize.py tests/tools/test_tdx2csv.py -q
# 76 passed

python3 -m py_compile tdxhub/utils/holiday.py tests/utils/test_holiday.py tests/utils/test_holiday_dependency.py
# passed

python3 - <<'PY'
import builtins
real_import = builtins.__import__

def blocked(name, globals=None, locals=None, fromlist=(), level=0):
    blocked_name = 'pan' + 'das'
    if name == blocked_name or name.startswith(blocked_name + '.'):
        raise ModuleNotFoundError('blocked tabular import')
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = blocked
import tdxhub.utils.holiday
print('holiday import ok without tabular dependency')
PY
# passed

rg -n "tdxhub\\.utils\\.(factor|adjust)|tdxhub\\.tools\\.reversion|tdxhub\\.contrib\\.adjust|from tdxhub\\.utils import factor|from tdxhub\\.utils import adjust|fq_factor|to_adjust|get_xdxr|reversion\\(" tdxhub tests scripts -S
# no active retired-adjustment imports remain

python3 -m py_compile scripts/probe_capabilities.py
# passed

python3 - <<'PY'
from scripts.probe_capabilities import _summarize
ok, info = _summarize([{"code": "000001", "price": 1.23, "name": "sample"}])
assert ok is True
assert info["count"] == 1
assert info["keys"] == ["code", "price", "name"]
assert info["sample_first_row"]["code"] == "000001"
ok, info = _summarize([])
assert ok is False
assert info["count"] == 0
ok, info = _summarize({"a": 1})
assert ok is True
assert info["keys"] == ["a"]
print("probe summarize ok")
PY
# passed
```

`python3 -m pytest -q` in `tdxhub` still includes live TDX server and cache
integration tests. It was run on 2026-05-05 and failed in those external
network-dependent cases (`socket.timeout`, empty TDX response header, missing
local xdxr cache). The records wrapper tests above are offline and isolate the
Phase 3 code change.
`python -m pytest tests/cache/test_file.py ...` was also retried on
2026-05-05 with the current `python` executable and stopped at collection
because `freezegun` is not installed in that interpreter. The same cache test
had already passed in the earlier Phase 3 cache-helper loop with the expected
test dependencies installed.

## Remaining Work

- Continue removing tdxhub legacy tabular wrappers after all downstream callers
  have migrated and tests no longer need legacy fixtures.
- Replace the explicitly disabled local-reader qfq/hfq adjustment path with a
  records-native implementation or remove the public flag.
