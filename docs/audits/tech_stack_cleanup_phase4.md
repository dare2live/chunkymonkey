# Tech Stack Cleanup Phase 4 - ChunkyMonkey Pandas Retirement

Date: 2026-05-05

## Scope

Phase 4 starts with the data-source adapter layer. The first small closed loop
removed pandas from the xdxr sync client after Phase 3 moved the tdxhub xdxr
boundary to records.

## Change

- Removed `import pandas` from `backend/services/xdxr_client.py`.
- Removed DataFrame compatibility from the xdxr normalizer.
- Updated `backend/tests/test_xdxr_client.py` fixtures from DataFrame to
  records.

## Validation

```bash
cd /Users/dp/Documents/M/stock/chunky-monkey-v2
rg -n "import pandas|from pandas|pd\.|DataFrame" backend/services/xdxr_client.py backend/tests/test_xdxr_client.py -S
# 0 matches

python3 -m py_compile backend/services/xdxr_client.py backend/tests/test_xdxr_client.py
# passed

python3 -m pytest backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 6 passed

python3 -m pytest backend/tests/test_data_health_snapshot.py backend/tests/test_xdxr_client.py backend/tests/test_block_client.py -q
# 10 passed
```

## Remaining Phase 4 Targets

- `backend/services/akshare_client.py`
- `backend/services/kline_source.py`
- `backend/services/lhb_client.py`
- `backend/services/qfii_client.py`
- `backend/services/margin_client.py`
- `backend/services/institution_survey_client.py`
- pandas usage in scripts and model/backtest layers.
