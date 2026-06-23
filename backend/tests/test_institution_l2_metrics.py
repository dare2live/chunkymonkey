from __future__ import annotations

import duckdb

from services.institution_l2_metrics import institution_l2_score_cte, l2_profile_ctes


def test_institution_l2_score_cte_matches_stable_contract():
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE fact_institution_follow_backtest (
                cohort_scheme TEXT,
                split TEXT,
                cohort_key TEXT,
                entry_lag INTEGER,
                max_hold_days INTEGER,
                stop_loss DOUBLE,
                take_profit DOUBLE,
                sharpe DOUBLE,
                avg_pnl DOUBLE,
                win_rate DOUBLE,
                n_filled INTEGER
            )
            """
        )
        con.execute(
            """
            INSERT INTO fact_institution_follow_backtest VALUES
                ('institution_L2_pit_20240930', 'train', 'inst_a|软件服务', 1, 20, -0.08, 0.2, 2.0, 0.04, 0.6, 40),
                ('institution_L2_pit_20240930', 'holdout', 'inst_a|软件服务', 1, 20, -0.08, 0.2, 1.5, 0.03, 0.55, 20),
                ('institution_L2_pit_20240930', 'train', 'inst_b|软件服务', 1, 20, -0.08, 0.2, 2.0, 0.04, 0.6, 40),
                ('institution_L2_pit_20240930', 'holdout', 'inst_b|软件服务', 1, 20, -0.08, 0.2, -0.1, -0.01, 0.4, 20)
            """
        )

        rows = con.execute(
            f"""
            WITH {institution_l2_score_cte("l2_score")}
            SELECT institution_id, l2_name, verdict, stable_score, ho_n, ho_sharpe
              FROM l2_score
             ORDER BY institution_id
            """
        ).fetchall()

        assert rows[0][0] == "inst_a"
        assert rows[0][1] == "软件服务"
        assert rows[0][2] == "stable"
        assert rows[0][3] > 30
        assert rows[1][2] == "overfit"
    finally:
        con.close()


def test_l2_profile_ctes_summarize_scores_and_stock_count():
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE fact_institution_follow_backtest (
                cohort_scheme TEXT,
                split TEXT,
                cohort_key TEXT,
                entry_lag INTEGER,
                max_hold_days INTEGER,
                stop_loss DOUBLE,
                take_profit DOUBLE,
                sharpe DOUBLE,
                avg_pnl DOUBLE,
                win_rate DOUBLE,
                n_filled INTEGER
            )
            """
        )
        con.execute("CREATE TABLE dim_stock_dc_industry (stock_code TEXT, tdx_l2_name TEXT)")
        con.execute(
            """
            INSERT INTO fact_institution_follow_backtest VALUES
                ('institution_L2_pit_20240930', 'train', 'inst_a|软件服务', 1, 20, -0.08, 0.2, 2.0, 0.04, 0.6, 40),
                ('institution_L2_pit_20240930', 'holdout', 'inst_a|软件服务', 1, 20, -0.08, 0.2, 1.5, 0.03, 0.55, 20)
            """
        )
        con.execute(
            """
            INSERT INTO dim_stock_dc_industry VALUES
                ('000001', '软件服务'),
                ('000002', '软件服务')
            """
        )

        row = con.execute(
            f"""
            WITH {l2_profile_ctes("l2_score", "l2_profile")}
            SELECT *
              FROM l2_profile
             WHERE l2_name = '软件服务'
            """
        ).fetchone()

        assert row[1] == 2
        assert row[2] == 1
        assert row[3] == 1
        assert row[6] is not None
    finally:
        con.close()
