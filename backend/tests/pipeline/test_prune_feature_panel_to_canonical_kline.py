from __future__ import annotations

import duckdb
import pytest

from scripts.prune_feature_panel_to_canonical_kline import prune_feature_panel_to_canonical_kline


pytestmark = pytest.mark.pipeline


def test_prune_feature_panel_to_canonical_kline_removes_rows_without_valid_signal_price():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE SCHEMA market")
        conn.execute(
            """
            CREATE TABLE market.price_kline_tdxhub (
                code TEXT,
                date TEXT,
                freq TEXT,
                adjust TEXT,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                amount DOUBLE
            )
            """
        )
        conn.execute(
            """
            CREATE VIEW market.v_price_kline_qfq AS
            SELECT code, date, freq, adjust, open, high, low, close, volume, amount
              FROM market.price_kline_tdxhub
             WHERE open > 0 AND close > 0
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                feature_value DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO market.price_kline_tdxhub
            VALUES ('000001', '2026-01-02', 'daily', 'qfq', 10, 11, 9, 10.5, 100, 1050)
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 1.0)")
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-03', 2.0)")

        result = prune_feature_panel_to_canonical_kline(
            conn,
            feature_tables=["fact_feature_panel"],
            run_id="prune_unit",
        )

        rows = conn.execute("SELECT stock_code, date, feature_value FROM fact_feature_panel").fetchall()
        audit = conn.execute(
            """
            SELECT missing_signal_count, pruned_count
              FROM mart_feature_panel_prune_run
             WHERE run_id = 'prune_unit'
               AND feature_table = 'fact_feature_panel'
            """
        ).fetchone()

        assert result["feature_tables"][0]["pruned_count"] == 1
        assert rows == [("000001", "2026-01-02", 1.0)]
        assert audit == (1, 1)
    finally:
        conn.close()
