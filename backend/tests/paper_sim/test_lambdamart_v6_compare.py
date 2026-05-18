from __future__ import annotations

from conftest import duck_mem
from scripts.run_paper_sim_lambdamart_v6_compare import (
    COMPARE_TABLE,
    CompareRow,
    compute_rank_ic,
    ensure_compare_table,
    prediction_coverage,
    write_compare_rows,
)


def test_compute_rank_ic_uses_prediction_rows_from_db():
    with duck_mem() as conn:
        conn.execute(
            """
            CREATE TABLE mart_p0b_lambdamart_v6_predictions (
                stock_code TEXT,
                signal_date DATE,
                score DOUBLE,
                fwd_cost_after_5d DOUBLE,
                fwd_cost_after_10d DOUBLE,
                fwd_cost_after_20d DOUBLE,
                model_id TEXT
            )
            """
        )
        rows = [
            ("600001", "2024-07-01", 0.1, 0.01),
            ("600002", "2024-07-01", 0.2, 0.02),
            ("600003", "2024-07-01", 0.3, 0.03),
            ("600001", "2024-07-02", 0.3, 0.01),
            ("600002", "2024-07-02", 0.2, 0.02),
            ("600003", "2024-07-02", 0.1, 0.03),
        ]
        for stock_code, signal_date, score, label in rows:
            conn.execute(
                """
                INSERT INTO mart_p0b_lambdamart_v6_predictions
                VALUES (?, ?, ?, NULL, NULL, ?, ?)
                """,
                [stock_code, signal_date, score, label, "lambdamart_v6_20260518"],
            )

        coverage = prediction_coverage(
            conn,
            table="mart_p0b_lambdamart_v6_predictions",
            model_id="lambdamart_v6_20260518",
            start="2024-07-01",
            end="2024-07-02",
        )
        rank_ic, n_dates = compute_rank_ic(
            conn,
            table="mart_p0b_lambdamart_v6_predictions",
            model_id="lambdamart_v6_20260518",
            label_col="fwd_cost_after_20d",
            start="2024-07-01",
            end="2024-07-02",
        )

        assert coverage["n_rows"] == 6
        assert coverage["n_dates"] == 2
        assert n_dates == 2
        assert rank_ic == 0.0


def test_write_compare_rows_persists_kpi_table():
    with duck_mem() as conn:
        row = CompareRow(
            model_label="lambdamart_v6",
            model_id="lambdamart_v6_20260518",
            prediction_table="mart_p0b_lambdamart_v6_predictions",
            sim_run_id="cmp_lambdamart_v6",
            period_start="2024-07-01",
            period_end="2026-04-13",
            rank_ic=0.04,
            rank_ic_n_dates=400,
            sharpe=1.2,
            ann_ret=0.35,
            max_dd=-0.18,
            monthly_win_rate=0.58,
            source_kpi_built_at=None,
        )

        ensure_compare_table(conn)
        write_compare_rows(conn, comparison_id="cmp_1", rows=[row])

        saved = conn.execute(
            f"SELECT model_id, rank_ic, ann_ret, max_dd FROM {COMPARE_TABLE} WHERE comparison_id = 'cmp_1'"
        ).fetchone()
        assert saved["model_id"] == "lambdamart_v6_20260518"
        assert saved["rank_ic"] == 0.04
        assert saved["ann_ret"] == 0.35
        assert saved["max_dd"] == -0.18
