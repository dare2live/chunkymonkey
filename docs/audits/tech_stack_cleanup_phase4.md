# Tech Stack Cleanup Phase 4 - ChunkyMonkey Pandas Retirement

Date: 2026-05-05

## Scope

Phase 4 starts with the data-source adapter layer. The first closed loops
removed pandas from xdxr and margin sync clients after their fetch boundaries
were reduced to records.

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

## Validation

```bash
cd /Users/dp/Documents/M/stock/chunky-monkey-v2
rg -n "import pandas|from pandas|pd\.|DataFrame" backend/services/xdxr_client.py backend/tests/test_xdxr_client.py -S
# 0 matches

rg -n "import pandas|from pandas|pd\.|DataFrame" backend/services/margin_client.py backend/tests/test_margin_client.py -S
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
```

## Remaining Phase 4 Targets

- `backend/services/akshare_client.py`
- `backend/services/kline_source.py`
- `backend/services/lhb_client.py`
- `backend/services/qfii_client.py`
- `backend/services/institution_survey_client.py`
- pandas usage in scripts and model/backtest layers.
