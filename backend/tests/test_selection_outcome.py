"""Phase ε D1 — outcome.py 单测。"""
from __future__ import annotations

import pytest


@pytest.fixture
def seeded():
    """主 conn + market conn 各种 7 天 K 线 + 2 个 selection log 事件。"""
    from services.duck_adapter import connect as duck_connect
    from services.selection.ddl import ensure_selection_tables

    conn = duck_connect(":memory:")
    mkt = duck_connect(":memory:")
    ensure_selection_tables(conn)

    # 种 selection log (2 个事件)
    conn.execute(
        """INSERT INTO fact_stock_selection_log
           (select_date, stock_code, select_source, source_id, horizon_days)
           VALUES ('2026-05-01', '600519', 'formula', 'macd_golden_cross', 20),
                  ('2026-05-01', '000001', 'daily_topk', 'champion', 20)"""
    )
    conn.commit()

    # 种 K 线 (5/1 entry, 后续涨)
    mkt.executescript("""
        CREATE VIEW v_price_kline_qfq AS
        SELECT * FROM (VALUES
          ('600519', '2026-05-01', 0.0, 0.0, 0.0, 100.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-02', 0.0, 0.0, 0.0, 105.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-03', 0.0, 0.0, 0.0, 106.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-04', 0.0, 0.0, 0.0, 108.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-05', 0.0, 0.0, 0.0, 109.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-06', 0.0, 0.0, 0.0, 112.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-01', 0.0, 0.0, 0.0, 50.0,  1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-02', 0.0, 0.0, 0.0, 49.0,  1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-03', 0.0, 0.0, 0.0, 48.0,  1000.0, 100.0, 'daily', 'qfq')
        ) t(code, date, open, high, low, close, volume, amount, freq, adjust);
    """)
    mkt.commit()
    yield conn, mkt
    conn.close()
    mkt.close()


class TestComputeOutcomesForPeriod:
    def test_writes_outcomes(self, seeded):
        from services.selection.outcome import compute_outcomes_for_period
        conn, mkt = seeded
        n = compute_outcomes_for_period(conn, mkt, "2026-05-01", "2026-05-01")
        assert n == 2

    def test_entry_price_correct(self, seeded):
        from services.selection.outcome import compute_outcomes_for_period
        conn, mkt = seeded
        compute_outcomes_for_period(conn, mkt, "2026-05-01", "2026-05-01")
        rows = conn.execute(
            "SELECT stock_code, entry_price FROM mart_stock_selection_outcome ORDER BY stock_code"
        ).fetchall()
        # 000001 entry = 50.0
        assert rows[0][0] == "000001"
        assert rows[0][1] == 50.0
        # 600519 entry = 100.0
        assert rows[1][0] == "600519"
        assert rows[1][1] == 100.0

    def test_days_to_t1_threshold(self, seeded):
        """600519 D+1=105 (+5%) → days_to_t1 = 1; 000001 跌 → None。"""
        from services.selection.outcome import compute_outcomes_for_period
        conn, mkt = seeded
        compute_outcomes_for_period(conn, mkt, "2026-05-01", "2026-05-01")
        rows = conn.execute(
            "SELECT stock_code, days_to_t1 FROM mart_stock_selection_outcome ORDER BY stock_code"
        ).fetchall()
        # 000001: 涨幅最大 -0.04 → 未达 +5% → None
        assert rows[0][1] is None
        # 600519: D+1 = 5% ✓ → 1
        assert rows[1][1] == 1

    def test_atomic_rollback(self, seeded):
        """模拟 executemany 抛错 → ROLLBACK, 表保持空。"""
        from unittest.mock import patch
        from services.selection.outcome import compute_outcomes_for_period
        conn, mkt = seeded
        real = conn.executemany
        def explode(*a, **kw):
            raise RuntimeError("simulated")
        with patch.object(conn, "executemany", side_effect=explode):
            try:
                compute_outcomes_for_period(conn, mkt, "2026-05-01", "2026-05-01")
                raise AssertionError("应抛")
            except RuntimeError:
                pass
        n = conn.execute("SELECT COUNT(*) FROM mart_stock_selection_outcome").fetchone()[0]
        assert n == 0
