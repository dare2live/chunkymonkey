"""Phase δ D1 — benchmarks.py 单测。"""
from __future__ import annotations

import pytest


def _seed_market(mkt_conn):
    """种入 price_kline (HS300) + v_price_kline_qfq 用于基准测试。"""
    mkt_conn.executescript("""
        CREATE TABLE price_kline (
          code TEXT, date TEXT, close DOUBLE
        );
        INSERT INTO price_kline VALUES
          ('000300', '2026-05-08', 3000.0),
          ('000300', '2026-05-09', 3030.0),
          ('000300', '2026-05-10', 3060.0),
          ('000300', '2026-05-12', 3090.0);
        CREATE VIEW v_price_kline_qfq AS
        SELECT * FROM (VALUES
          ('600519', '2026-05-08', 0.0, 0.0, 0.0, 1800.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-09', 0.0, 0.0, 0.0, 1818.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-10', 0.0, 0.0, 0.0, 1836.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-12', 0.0, 0.0, 0.0, 1854.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-08', 0.0, 0.0, 0.0, 12.0,   1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-09', 0.0, 0.0, 0.0, 12.12,  1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-10', 0.0, 0.0, 0.0, 12.24,  1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-12', 0.0, 0.0, 0.0, 12.36,  1000.0, 100.0, 'daily', 'qfq')
        ) t(code, date, open, high, low, close, volume, amount, freq, adjust);
    """)
    mkt_conn.commit()


class TestHS300NavSeries:
    def test_normalized_to_1_at_start(self):
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.benchmarks import hs300_nav_series

        mkt = duck_connect(":memory:")
        try:
            _seed_market(mkt)
            nav = hs300_nav_series(mkt, "2026-05-08", "2026-05-12")
            assert len(nav) == 4
            assert nav["2026-05-08"] == 1.0
            assert abs(nav["2026-05-09"] - 1.01) < 1e-3  # 3030/3000
            assert abs(nav["2026-05-12"] - 1.03) < 1e-3
        finally:
            mkt.close()

    def test_empty_returns_empty(self):
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.benchmarks import hs300_nav_series

        mkt = duck_connect(":memory:")
        try:
            _seed_market(mkt)
            nav = hs300_nav_series(mkt, "2099-01-01", "2099-12-31")
            assert nav == {}
        finally:
            mkt.close()


class TestEqualWeightNavSeries:
    def test_two_stocks_growing_1pct(self):
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.benchmarks import equal_weight_nav_series

        mkt = duck_connect(":memory:")
        try:
            _seed_market(mkt)
            nav = equal_weight_nav_series(mkt, ["600519", "000001"], "2026-05-08", "2026-05-12")
            assert nav["2026-05-08"] == 1.0
            # 两股都涨 1% → 等权也涨 1%
            assert abs(nav["2026-05-09"] - 1.01) < 1e-3
        finally:
            mkt.close()


class TestCombineBenchmarks:
    def test_align_curves(self):
        from services.paper_engine.benchmarks import combine_benchmarks

        main = [
            {"date": "D1", "total": 1_000_000, "cash": 0, "position_count": 5},
            {"date": "D2", "total": 1_020_000, "cash": 0, "position_count": 5},
        ]
        hs300 = {"D1": 1.0, "D2": 1.01}
        eqw   = {"D1": 1.0, "D2": 1.005}
        out = combine_benchmarks(main, hs300, eqw)
        assert len(out) == 2
        # D2: main cum_ret=0.02, hs300_cum=0.01 → vs_hs300 = 0.01
        d2 = out[1]
        assert abs(d2["nav"] - 1.02) < 1e-6
        assert abs(d2["cum_ret"] - 0.02) < 1e-6
        assert abs(d2["hs300_cum_ret"] - 0.01) < 1e-6
        assert abs(d2["vs_hs300_cum_ret"] - 0.01) < 1e-6
        assert abs(d2["vs_eqw_cum_ret"] - 0.015) < 1e-6
