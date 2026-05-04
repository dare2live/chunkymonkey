# Tech Stack Cleanup Phase 1 Findings

Date: 2026-05-04

Plan: `/Users/dp/Documents/M/stock/chunkymonkey_ui_design_goal_plan.md`

## Scope

Phase 1 inspected whether pandas, SQLite compatibility, old route labels, old DB paths, stale health results, and support-repo contracts could create false positives in ChunkyMonkey.

## Fixed In This Phase

- `data_health_snapshot.py --dry-run` now reports `green=147/yellow=0/red=0` locally. The original full-snapshot warning about downstream derived/experimental red/yellow assets is no longer reproduced.
- `tdxhub` top-level import no longer imports pandas. The support repo now exposes `tdxhub.paths.get_config_path`, and `import tdxhub` succeeds under a subprocess that blocks all `pandas` imports.
- `miaoxiang` no longer exposes a SQLite DDL dialect; the public DDL generator now allows only `duckdb` and `postgres`.
- ChunkyMonkey no longer uses old `smartmoney.db`, `market_data.db`, or `etf.db` test fixture names.
- Tests that checked `sqlite_master` or `PRAGMA table_info` were moved to DuckDB-native `information_schema` or `DESCRIBE`.
- Runtime `BEGIN IMMEDIATE` calls were replaced with DuckDB `BEGIN TRANSACTION`.
- Several runtime `_table_columns` helpers now use `DESCRIBE` instead of `PRAGMA table_info`.
- Hardcoded `/Users/dp/Documents/M/tdxhub` paths were replaced with sibling-checkout resolution from the current repo root.
- Frontend/source route labels were updated from retired direct-source names to the current `tdxhub / aif10 / akshare / retired` route model.
- `backend/requirements.txt` now pins tdxhub to support-repo commit `bf5b708a348168a6e51a3b452cef4fd573681f94` instead of a moving branch.

Support repo commits:

- `tdxhub`: `bf5b708 Make top-level import independent of pandas`
- `miaoxiang`: `a1501e3 Drop SQLite DDL dialect`

## Remaining Findings

The largest remaining blocker is still the records-first migration:

- ChunkyMonkey still calls tdxhub modules that return pandas DataFrames, especially `Quotes`, `Affair`, `holders`, and reader utilities.
- tdxhub runtime modules such as `quotes.py`, `protocol/base_socket_client.py`, `holders.py`, and `utils/__init__.py` still use pandas internally.
- ChunkyMonkey model and rebuild scripts still use pandas, `.df()`, `duck.register(DataFrame)`, and `read_sql_query` in non-trivial pipelines. These need staged rewrites because they affect feature, training, and backtest outputs.
- `duck_adapter.py` still contains SQLite compatibility polyfills. After the runtime PRAGMA/BEGIN cleanup, remaining hits are localized there plus test guidance docs.

After Phase 1 fixes, the repeatable Phase 0 scan reports:

```text
duckdb_allowed: 260
old_db_path: 0
old_external_link: 0
old_source_route: 0
pandas_docs: 84
pandas_runtime: 1211
pandas_test: 89
sqlite_docs: 3
sqlite_runtime: 8
sqlite_test: 11
```

The remaining `sqlite_runtime` hits are in `backend/services/duck_adapter.py`.

## Verification

Commands run:

```bash
python3 backend/scripts/data_health_snapshot.py --dry-run
python3 backend/scripts/audit_stale_references.py --phase0-only --output /tmp/phase0_after_fixes.json
python3 -m py_compile backend/scripts/audit_stale_references.py backend/scripts/data_health_snapshot.py backend/services/db.py
python3 -m pytest backend/tests/test_data_health_snapshot.py backend/tests/test_etf_db.py backend/tests/test_tdx_source.py backend/tests/test_block_client.py backend/tests/test_xdxr_client.py backend/tests/test_audit_financial.py -q
cd backend && python3 -m pytest tests/test_utils.py tests/test_kline_sources.py tests/test_tdx_source.py tests/test_audit_financial.py tests/test_scoring_engine.py tests/test_scoring_composite.py -p no:cacheprovider --tb=short -q
python3 -m pytest tests/test_quotes_utils.py -q
python3 -m py_compile aif10_scraper/orm/ddl.py aif10_scraper/orm/__init__.py scripts/generate_ddl.py scripts/probe_p6_targets.py
```

Results:

- ChunkyMonkey targeted tests: `26 passed`
- ChunkyMonkey CI offline subset: `57 passed`
- tdxhub targeted tests: `8 passed`
- data health dry run: `147 green / 0 yellow / 0 red`
- tdxhub no-pandas import simulation: passed
- miaoxiang SQLite residual scan: no matches

## Next Phase Queue

- Remove `duck_adapter.py` SQLite polyfills after remaining call sites and tests no longer require them.
- Add records-first tdxhub APIs for ChunkyMonkey call sites, starting with quotes bars, finance/gpcw, xdxr, block, and holders.
- Convert ChunkyMonkey feature/model scripts from pandas DataFrame contracts to DuckDB records/tables, then rebuild feature panels and rerun model/strategy validation.
