"""Phase ε D1 — DDL 单测。"""
from __future__ import annotations


class TestEnsureSelectionTables:
    def test_ensure_creates_four_tables(self):
        from services.duck_adapter import connect as duck_connect
        from services.selection.ddl import ensure_selection_tables

        conn = duck_connect(":memory:")
        try:
            ensure_selection_tables(conn)
            names = {r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()}
            assert "fact_stock_selection_log" in names
            assert "mart_stock_selection_outcome" in names
            assert "mart_stock_selection_summary" in names
            assert "mart_formula_weight_history" in names
        finally:
            conn.close()

    def test_idempotent(self):
        from services.duck_adapter import connect as duck_connect
        from services.selection.ddl import ensure_selection_tables

        conn = duck_connect(":memory:")
        try:
            for _ in range(3):
                ensure_selection_tables(conn)
            n = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='fact_stock_selection_log'"
            ).fetchone()[0]
            assert n == 1
        finally:
            conn.close()

    def test_log_supports_insert(self):
        from services.duck_adapter import connect as duck_connect
        from services.selection.ddl import ensure_selection_tables

        conn = duck_connect(":memory:")
        try:
            ensure_selection_tables(conn)
            conn.execute(
                """INSERT INTO fact_stock_selection_log
                   (select_date, stock_code, select_source, source_id,
                    rank_in_date, pred_score, strength, state, horizon_days)
                   VALUES ('2026-05-12', '600519', 'daily_topk', 'champion', 1, 0.85, NULL, NULL, 20)"""
            )
            conn.commit()
            n = conn.execute("SELECT COUNT(*) FROM fact_stock_selection_log").fetchone()[0]
            assert n == 1
        finally:
            conn.close()
