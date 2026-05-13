"""Phase γ D1 — services/picture/ddl.py 单测。

验证: 5 张表全部创建 + 幂等 + 主键正确。
"""
from __future__ import annotations


class TestEnsurePictureTables:
    """DDL helpers in services/picture/ddl.py。"""

    def test_ensure_creates_five_tables(self):
        from services.duck_adapter import connect as duck_connect
        from services.picture.ddl import ensure_picture_tables

        conn = duck_connect(":memory:")
        try:
            ensure_picture_tables(conn)
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
            names = {t[0] for t in tables}
            assert "fact_stock_fundamental_stage_daily" in names
            assert "fact_stock_type_daily"              in names
            assert "dim_stock_stage_days"               in names
            assert "mart_stock_picture_daily"           in names
            assert "mart_stock_trade_plan"              in names
        finally:
            conn.close()

    def test_ensure_is_idempotent(self):
        """幂等 = 多次调用不报错且不重建表。"""
        from services.duck_adapter import connect as duck_connect
        from services.picture.ddl import ensure_picture_tables

        conn = duck_connect(":memory:")
        try:
            ensure_picture_tables(conn)
            ensure_picture_tables(conn)
            ensure_picture_tables(conn)
            n = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name='mart_stock_picture_daily'"
            ).fetchone()[0]
            assert n == 1
        finally:
            conn.close()

    def test_picture_daily_supports_insert(self):
        """smoke: 写一行 + 读出来字段保真。"""
        from services.duck_adapter import connect as duck_connect
        from services.picture.ddl import ensure_picture_tables

        conn = duck_connect(":memory:")
        try:
            ensure_picture_tables(conn)
            conn.execute(
                """
                INSERT INTO mart_stock_picture_daily
                  (stock_code, snapshot_date, latest_close, chg_pct,
                   fundamental_stage, fundamental_stage_days,
                   technical_stage, technical_stage_days,
                   primary_type, valuation_pe)
                VALUES ('600519', '2026-05-12', 1800.5, 0.012,
                        '温和验证', 30, '2', 60, '业绩驱动', 32.5)
                """
            )
            conn.commit()
            row = conn.execute(
                "SELECT fundamental_stage, technical_stage, valuation_pe "
                "FROM mart_stock_picture_daily WHERE stock_code='600519'"
            ).fetchone()
            assert row[0] == "温和验证"
            assert row[1] == "2"
            assert abs(row[2] - 32.5) < 1e-6
        finally:
            conn.close()

    def test_trade_plan_primary_key_includes_model_id(self):
        """同一 stock + plan_date, 不同 model_id 允许共存 (Champion vs Challenger)。"""
        from services.duck_adapter import connect as duck_connect
        from services.picture.ddl import ensure_picture_tables

        conn = duck_connect(":memory:")
        try:
            ensure_picture_tables(conn)
            for model_id, target in (("v1", 100.0), ("challenger_v2", 105.0)):
                conn.execute(
                    """
                    INSERT INTO mart_stock_trade_plan
                      (stock_code, plan_date, model_id, entry_target_price)
                    VALUES (?, ?, ?, ?)
                    """,
                    ["600519", "2026-05-12", model_id, target],
                )
            conn.commit()
            n = conn.execute(
                "SELECT COUNT(*) FROM mart_stock_trade_plan WHERE stock_code='600519'"
            ).fetchone()[0]
            assert n == 2
        finally:
            conn.close()
