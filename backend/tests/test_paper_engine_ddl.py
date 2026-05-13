"""Phase δ D1 — DDL 单测.

Phase ψ.5: mart_decision_outcome 表退役 (0 rows / 0 reads / 0 writes), ensure
只建剩下 3 张. 单测同步.
"""
from __future__ import annotations


class TestEnsurePaperTables:
    def test_ensure_creates_three_tables(self):
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.ddl import ensure_paper_tables

        conn = duck_connect(":memory:")
        try:
            ensure_paper_tables(conn)
            names = {r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()}
            assert "mart_paper_nav" in names
            assert "fact_paper_position" in names
            assert "mart_signal_ic" in names
            # mart_decision_outcome retired Phase ψ.5
            assert "mart_decision_outcome" not in names
        finally:
            conn.close()

    def test_ensure_is_idempotent(self):
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.ddl import ensure_paper_tables

        conn = duck_connect(":memory:")
        try:
            ensure_paper_tables(conn)
            ensure_paper_tables(conn)
            ensure_paper_tables(conn)
            n = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='mart_paper_nav'"
            ).fetchone()[0]
            assert n == 1
        finally:
            conn.close()

    def test_paper_nav_supports_insert(self):
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.ddl import ensure_paper_tables

        conn = duck_connect(":memory:")
        try:
            ensure_paper_tables(conn)
            conn.execute(
                """
                INSERT INTO mart_paper_nav
                  (snapshot_date, nav, nav_value, daily_ret, cum_ret,
                   hs300_nav, hs300_cum_ret, vs_hs300_cum_ret,
                   eqw_nav, eqw_cum_ret, vs_eqw_cum_ret,
                   cash, position_count, drawdown)
                VALUES ('2026-05-12', 1.05, 1050000.0, 0.012, 0.05,
                        1.03, 0.03, 0.02, 1.04, 0.04, 0.01,
                        50000.0, 20, -0.01)
                """
            )
            conn.commit()
            r = conn.execute(
                "SELECT nav, position_count FROM mart_paper_nav WHERE snapshot_date='2026-05-12'"
            ).fetchone()
            assert abs(r[0] - 1.05) < 1e-6
            assert r[1] == 20
        finally:
            conn.close()

    def test_paper_position_pk_per_event(self):
        """同股 / 同日 / 不同 side 允许共存 (buy + sell 都可在同一日发生)。"""
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.ddl import ensure_paper_tables

        conn = duck_connect(":memory:")
        try:
            ensure_paper_tables(conn)
            for side in ("buy", "sell"):
                conn.execute(
                    "INSERT INTO fact_paper_position (event_date, stock_code, side, qty) VALUES ('2026-05-12', '600519', ?, 100)",
                    [side],
                )
            conn.commit()
            n = conn.execute(
                "SELECT COUNT(*) FROM fact_paper_position WHERE stock_code='600519'"
            ).fetchone()[0]
            assert n == 2
        finally:
            conn.close()
