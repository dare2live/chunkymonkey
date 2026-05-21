# N+1 IO-in-loop Audit Report

- Total findings: 19
- HIGH: 10
- MEDIUM: 0
- LOW: 9
- P-4 baseline: 19
- Scanned Python files: 620
- Scope: production-code (tests excluded)
- Mode: WARN-only

## Top 19 Findings

| Severity | File | Line | Pattern | Suggested fix | Snippet |
|---|---|---:|---|---|---|
| HIGH | `backend/scripts/build_architecture_inventory.py` | 647 | SQL_EXECUTE_IN_FOR_LOOP | Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL. | `for candidate in candidates: / row = conn.execute(f"SELECT CAST(MAX({_quote_ident(actual)}) AS VARCHAR) AS latest FROM {table_ref}").fetchone()` |
| HIGH | `backend/scripts/build_feature_association_duck.py` | 552 | SQL_EXECUTE_IN_FOR_LOOP | Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL. | `for fold in ranges: / conn.execute("DROP TABLE IF EXISTS __feature_assoc_fold_base")` |
| HIGH | `backend/scripts/build_feature_association_duck.py` | 553 | SQL_EXECUTE_IN_FOR_LOOP | Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL. | `for fold in ranges: / conn.execute(` |
| HIGH | `backend/scripts/build_tdx_gpcw_auto_features.py` | 492 | SQL_EXECUTE_IN_FOR_LOOP | Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL. | `for start in range(0, len(select_sql), chunk_size): / conn.execute(` |
| HIGH | `backend/scripts/build_temporal_synergy_research.py` | 724 | SQL_EXECUTE_IN_FOR_LOOP | Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL. | `for fold in ranges: / conn.execute(` |
| HIGH | `backend/scripts/ingest_profit_forecast_snapshot.py` | 224 | SQL_EXECUTE_IN_FOR_LOOP | Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL. | `for r in rows: / conn.execute(` |
| HIGH | `backend/scripts/materialize_follow_return_labels.py` | 575 | SQL_EXECUTE_IN_FOR_LOOP | Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL. | `for horizon, label in zip(horizons, labels, strict=True): / row = conn.execute(` |
| HIGH | `backend/scripts/prune_feature_panel_to_canonical_kline.py` | 130 | SQL_EXECUTE_IN_FOR_LOOP | Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL. | `for feature_table in feature_tables: / row = conn.execute(` |
| HIGH | `backend/services/ml_lifecycle/drift.py` | 507 | SQL_EXECUTE_IN_FOR_LOOP | Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL. | `for col in feature_columns: / t_rows = conn.execute(f"""` |
| HIGH | `backend/services/strategy_ensemble.py` | 158 | SQL_EXECUTE_IN_FOR_LOOP | Batch rows and move execute/executemany outside the loop, or replace the loop with set-based SQL. | `for src in alphas: / rows = conn.execute(src.sql).fetchall()` |
| LOW | `backend/scripts/audit_delivery_readiness.py` | 562 | READ_ONLY_QUERY_IN_FOR_LOOP | Prefetch read-only query results before the loop or join once in SQL instead of querying per iteration. | `for source_name, spec in SOURCES.items(): / r = con.execute(` |
| LOW | `backend/scripts/audit_tradeability.py` | 457 | READ_ONLY_QUERY_IN_FOR_LOOP | Prefetch read-only query results before the loop or join once in SQL instead of querying per iteration. | `for d, raw_susp in top_dates: / raw = mconn.execute(` |
| LOW | `backend/scripts/backfill_sector_momentum_history.py` | 295 | READ_ONLY_QUERY_IN_FOR_LOOP | Prefetch read-only query results before the loop or join once in SQL instead of querying per iteration. | `for sec in ('计算机', '食品饮料', '银行', '电子', '医药生物'): / r = smart.execute("""` |
| LOW | `backend/scripts/backfill_walkforward_eval.py` | 61 | READ_ONLY_QUERY_IN_FOR_LOOP | Prefetch read-only query results before the loop or join once in SQL instead of querying per iteration. | `for idx, (test_start, test_end, train_start, train_end, mv, fv, lv, wfm) in enumerate(windows): / df = con.execute("""` |
| LOW | `backend/scripts/build_executive_trade_events.py` | 316 | READ_ONLY_QUERY_IN_FOR_LOOP | Prefetch read-only query results before the loop or join once in SQL instead of querying per iteration. | `for i in range(0, len(codes), chunk): / cursor = mkt.execute(` |
| LOW | `backend/scripts/fill_missing_market_kline.py` | 70 | READ_ONLY_QUERY_IN_FOR_LOOP | Prefetch read-only query results before the loop or join once in SQL instead of querying per iteration. | `for code in codes: / rows = mkt_conn.execute(` |
| LOW | `backend/scripts/seed_dim_data_asset.py` | 881 | READ_ONLY_QUERY_IN_FOR_LOOP | Prefetch read-only query results before the loop or join once in SQL instead of querying per iteration. | `for r in rows: / cnt = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]` |
| LOW | `backend/services/stock_stage_engine.py` | 253 | READ_ONLY_QUERY_IN_FOR_LOOP | Prefetch read-only query results before the loop or join once in SQL instead of querying per iteration. | `for idx in range(0, len(codes), chunk_size): / rows = mkt_conn.execute(` |
| LOW | `backend/services/stock_turtle_engine.py` | 48 | READ_ONLY_QUERY_IN_FOR_LOOP | Prefetch read-only query results before the loop or join once in SQL instead of querying per iteration. | `for idx in range(0, len(codes), chunk_size): / rows = mkt_conn.execute(` |
