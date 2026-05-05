import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem  # noqa: E402
from scripts.evaluate_holding_topk import evaluate_grid, load_prediction_panel, write_results  # noqa: E402


def test_evaluate_holding_topk_grid_and_write_results():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE mart_multidim_prediction (
                model_id TEXT,
                stock_code TEXT,
                date TEXT,
                pred_score DOUBLE,
                rank_in_date INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                forward_ret_5d DOUBLE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE dim_stock_tdx_industry (
                stock_code TEXT,
                tdx_l1 TEXT,
                tdx_l1_name TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_multidim_prediction VALUES
            ('m1', '000001', '2026-01-02', 0.90, 1),
            ('m1', '000002', '2026-01-02', 0.80, 2),
            ('m1', '000003', '2026-01-02', 0.20, 3),
            ('m1', '000004', '2026-01-02', 0.10, 4),
            ('m1', '000002', '2026-01-03', 0.95, 1),
            ('m1', '000001', '2026-01-03', 0.85, 2),
            ('m1', '000003', '2026-01-03', 0.30, 3),
            ('m1', '000004', '2026-01-03', 0.05, 4)
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('000001', '2026-01-02', 0.10),
            ('000002', '2026-01-02', 0.05),
            ('000003', '2026-01-02', -0.01),
            ('000004', '2026-01-02', -0.02),
            ('000002', '2026-01-03', -0.01),
            ('000001', '2026-01-03', 0.02),
            ('000003', '2026-01-03', 0.00),
            ('000004', '2026-01-03', -0.05)
            """
        )
        conn.execute(
            """
            INSERT INTO dim_stock_tdx_industry VALUES
            ('000001', 'tech', 'Tech'),
            ('000002', 'bank', 'Bank'),
            ('000003', 'tech', 'Tech'),
            ('000004', 'bank', 'Bank')
            """
        )

        panel = load_prediction_panel(
            conn,
            model_id="m1",
            feature_table="fact_feature_panel",
            feature_set_id=None,
            labels=["forward_ret_5d"],
        )
        rows = evaluate_grid(
            panel,
            model_id="m1",
            feature_table="fact_feature_panel",
            feature_set_id=None,
            horizons=[5],
            top_sizes=[2],
            cost_bps=30.0,
            run_id="test_holding_topk",
        )

        assert len(rows) == 1
        row = rows[0]
        assert row["n_dates"] == 2
        assert row["n_signals"] == 4
        assert row["avg_top_return"] == pytest.approx(0.04)
        assert row["avg_benchmark_return"] == pytest.approx(0.01)
        assert row["avg_excess_return"] == pytest.approx(0.03)
        assert row["long_short_spread"] == pytest.approx(0.06)
        assert row["winrate"] == pytest.approx(0.75)
        assert row["avg_turnover"] == pytest.approx(0.0)
        assert row["avg_industry_hhi"] == pytest.approx(0.5)
        assert row["recommendation"] == "keep_candidate"

        write_results(conn, rows)
        written = conn.execute(
            """
            SELECT avg_top_return, recommendation
              FROM mart_model_holding_topk_eval
             WHERE run_id = 'test_holding_topk'
            """
        ).fetchone()
        assert written["avg_top_return"] == pytest.approx(0.04)
        assert written["recommendation"] == "keep_candidate"
    finally:
        conn.close()
