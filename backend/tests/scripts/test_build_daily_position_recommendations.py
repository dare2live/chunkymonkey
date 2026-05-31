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
