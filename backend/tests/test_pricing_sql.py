from conftest import duck_mem

from services.pricing_sql import qfq_vwap_expr, qfq_vwap_method_expr


def test_qfq_vwap_expr_uses_factor_for_hand_volume_qfq_rows() -> None:
    with duck_mem() as conn:
        conn.execute(
            """
            CREATE TABLE px (
                amount DOUBLE,
                volume DOUBLE,
                close DOUBLE,
                factor DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO px VALUES (4584230912.0, 173308.0, 810.5583347846815, 3.0357990066842)")

        row = conn.execute(
            f"""
            SELECT {qfq_vwap_expr(amount='amount', volume='volume', close='close', factor='factor')} AS entry_price,
                   {qfq_vwap_method_expr(amount='amount', volume='volume', close='close', factor='factor')} AS method
              FROM px
            """
        ).fetchone()

        assert abs(row["entry_price"] - 803.0098811976715) < 1e-6
        assert row["method"] == "signal_day_vwap_qfq_volume_hand_factor_adjusted"
