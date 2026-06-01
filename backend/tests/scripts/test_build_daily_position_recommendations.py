from __future__ import annotations


def test_daily_position_recommendation_ddl_preserves_existing_history():
    """DDL must not drop historical recommendation dates before a new date refresh."""
    from scripts.build_daily_position_recommendations import DDL
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        conn.executescript(DDL)
        conn.execute(
            """
            INSERT INTO mart_daily_position_recommendation
              (signal_date, buy_date, profile_id, rank_in_profile, stock_code,
               formula_id, formula_variant)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "2026-05-19",
                "2026-05-20",
                "short",
                1,
                "600519",
                "macd_golden_cross",
                "macd_golden_cross",
            ],
        )

        conn.executescript(DDL)

        rows = conn.execute(
            """
            SELECT signal_date, buy_date, profile_id, stock_code, formula_id, formula_variant
              FROM mart_daily_position_recommendation
            """
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (
                "2026-05-19",
                "2026-05-20",
                "short",
                "600519",
                "macd_golden_cross",
                "macd_golden_cross",
            )
        ]
    finally:
        conn.close()


def test_pit_diagnostic_rows_include_governance_reason():
    """Missing PIT rows should surface the latest governance rejection reason."""
    from scripts.build_daily_position_recommendations import DDL, _build_pit_diagnostic_rows
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        conn.executescript(DDL)
        conn.execute(
            """
            CREATE TABLE mart_per_stock_stage_strategy_optimal_pit (
                stock_code TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                stage_filter TEXT,
                cutoff_date TEXT,
                oos_n_traded INTEGER,
                n_traded INTEGER,
                oos_avg_ret DOUBLE,
                avg_ret DOUBLE,
                optimal_stop_pct DOUBLE,
                oos_sharpe DOUBLE,
                sharpe DOUBLE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_optuna_governance_log (
                run_id TEXT,
                rejected_at TIMESTAMP,
                stock_code TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                stage_filter TEXT,
                reason TEXT,
                record_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_optuna_governance_log
              (run_id, rejected_at, stock_code, formula_id, formula_variant,
               stage_filter, reason, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "run-1",
                "2026-05-15 19:43:34",
                "605580",
                "turtle_breakout_55",
                "turtle_breakout_55",
                "1.5",
                "oos_n_traded=1 < min_test_signals=3 (OOS 样本太少, 不可信)",
                None,
            ],
        )
        conn.execute(
            """
            INSERT INTO fact_optuna_governance_log
              (run_id, rejected_at, stock_code, formula_id, formula_variant,
               stage_filter, reason, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "run-2",
                "2026-05-16 09:10:11",
                "605580",
                "turtle_breakout_55",
                "turtle_breakout_55",
                "1.5",
                "oos_win_rate=1.00 超出现实区间 [0, 0.95]",
                None,
            ],
        )

        all_rows = [
            (
                "2026-05-29",
                "2026-05-30",
                "short",
                1,
                "605580",
                "turtle_breakout_55",
                "turtle_breakout_55",
                None,
                None,
                None,
                "1.5",
                None,
                "cross_stage_fallback",
            )
        ]

        rows = _build_pit_diagnostic_rows(conn, "2026-05-29", all_rows)
        assert len(rows) == 1

        row = rows[0]
        assert row[12] == "stock_missing_pit"
        assert row[13] == 2
        assert row[14] == "oos_win_rate=1.00 超出现实区间 [0, 0.95]"
        assert str(row[15]).startswith("2026-05-16 09:10:11")
    finally:
        conn.close()


def test_pit_diagnostic_columns_are_added_to_existing_table():
    """Migration helper must backfill governance columns onto legacy diagnostic tables."""
    from scripts.build_daily_position_recommendations import _ensure_pit_diagnostic_columns
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_daily_position_recommendation_pit_diagnostic (
                signal_date TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                rank_in_profile INTEGER NOT NULL,
                stock_code TEXT NOT NULL,
                formula_id TEXT,
                formula_variant TEXT,
                stage_bin TEXT,
                match_tier TEXT,
                pit_exact_stage_rows INTEGER NOT NULL DEFAULT 0,
                pit_same_formula_rows INTEGER NOT NULL DEFAULT 0,
                pit_same_stock_rows INTEGER NOT NULL DEFAULT 0,
                latest_pit_cutoff_date TEXT,
                missing_reason TEXT NOT NULL,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (signal_date, profile_id, stock_code, formula_id, formula_variant)
            )
            """
        )

        _ensure_pit_diagnostic_columns(conn)

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info('mart_daily_position_recommendation_pit_diagnostic')"
            ).fetchall()
        }
        assert {"governance_reject_count", "governance_latest_reason", "governance_latest_rejected_at"}.issubset(columns)
    finally:
        conn.close()
