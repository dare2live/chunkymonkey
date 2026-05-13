"""Phase δ D2 — driver.run_paper_day e2e 集成测试。

种 5 日 fixture, 验证完整 step + 跨日持仓状态保持。
"""
from __future__ import annotations

import pytest


def _seed_main(conn):
    """主 DB 种入 mart_daily_recommendation + mart_stock_trade_plan + 交易日历。"""
    from services.paper_engine.ddl import ensure_paper_tables
    ensure_paper_tables(conn)

    conn.executescript("""
        CREATE TABLE mart_daily_recommendation (
          snapshot_date TEXT, stock_code TEXT, model_id TEXT, rank_in_date BIGINT,
          pred_score DOUBLE, percentile DOUBLE, is_primary BOOLEAN
        );
        INSERT INTO mart_daily_recommendation VALUES
          ('2026-05-08', '600519', 'champion', 1, 0.9, 0.95, TRUE),
          ('2026-05-08', '000001', 'champion', 2, 0.7, 0.85, TRUE),
          ('2026-05-08', '300750', 'champion', 3, 0.6, 0.80, TRUE);

        CREATE TABLE mart_stock_trade_plan (
          stock_code TEXT, plan_date TEXT, model_id TEXT,
          entry_target_price REAL, entry_aggressive_price REAL, entry_max_price REAL,
          exit_target_1_price REAL, exit_stop_price REAL,
          risk_reward_ratio REAL, expected_horizon_days INTEGER, atr_14 REAL
        );
        INSERT INTO mart_stock_trade_plan VALUES
          ('600519', '2026-05-08', 'v1', 1800.0, 1820.0, 1850.0, 1860.0, 1750.0, 1.2, 20, 30.0),
          ('000001', '2026-05-08', 'v1', 12.0, 12.1, 12.3, 12.5, 11.6, 1.2, 20, 0.2),
          ('300750', '2026-05-08', 'v1', 200.0, 202.0, 205.0, 215.0, 190.0, 1.5, 20, 5.0);

        CREATE TABLE dim_trading_calendar (trade_date TEXT, is_trading INTEGER);
        INSERT INTO dim_trading_calendar VALUES
          ('2026-05-07', 1), ('2026-05-08', 1), ('2026-05-09', 1), ('2026-05-10', 1), ('2026-05-12', 1);
    """)
    conn.commit()


def _seed_market(mkt_conn):
    """市场 DB 种入 v_price_kline_qfq 5 日 3 股。"""
    mkt_conn.executescript("""
        CREATE VIEW v_price_kline_qfq AS
        SELECT * FROM (VALUES
          ('600519', '2026-05-07', 1790.0, 1800.0, 1785.0, 1795.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-08', 1795.0, 1810.0, 1790.0, 1805.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-09', 1805.0, 1820.0, 1800.0, 1815.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-10', 1815.0, 1825.0, 1810.0, 1820.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('600519', '2026-05-12', 1820.0, 1830.0, 1815.0, 1825.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-07', 11.9, 12.0, 11.85, 11.95, 1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-08', 11.95, 12.1, 11.9, 12.05, 1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-09', 12.05, 12.2, 12.0, 12.15, 1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-10', 12.15, 12.25, 12.1, 12.2, 1000.0, 100.0, 'daily', 'qfq'),
          ('000001', '2026-05-12', 12.2, 12.3, 12.15, 12.25, 1000.0, 100.0, 'daily', 'qfq'),
          ('300750', '2026-05-07', 199.0, 200.0, 198.0, 199.5, 1000.0, 100.0, 'daily', 'qfq'),
          ('300750', '2026-05-08', 199.5, 201.0, 199.0, 200.5, 1000.0, 100.0, 'daily', 'qfq'),
          ('300750', '2026-05-09', 200.5, 203.0, 200.0, 202.5, 1000.0, 100.0, 'daily', 'qfq'),
          ('300750', '2026-05-10', 202.5, 205.0, 202.0, 204.0, 1000.0, 100.0, 'daily', 'qfq'),
          ('300750', '2026-05-12', 204.0, 206.0, 203.5, 205.0, 1000.0, 100.0, 'daily', 'qfq')
        ) t(code, date, open, high, low, close, volume, amount, freq, adjust);
    """)
    mkt_conn.commit()


class TestRunPaperDay:
    def test_initial_day_no_prev_creates_nav_row(self):
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.driver import run_paper_day

        conn = duck_connect(":memory:")
        mkt = duck_connect(":memory:")
        try:
            _seed_main(conn)
            _seed_market(mkt)
            # initial=5M, max=3 → 每槽 1.5M, 茅台 1.5M/1800 = 833 股, floor 800
            result = run_paper_day(
                conn=conn, mkt_conn=mkt, snapshot_date="2026-05-08", prev_date="2026-05-07",
                initial_capital=5_000_000.0, max_positions=3,
            )
            assert result["snapshot_date"] == "2026-05-08"
            assert result["n_entries"] == 3   # 3 推荐都入场 (现金足够)
            assert result["n_exits"] == 0
            assert result["position_count"] == 3
            # nav 接近 initial_capital
            assert 4_900_000 < result["nav_value"] < 5_050_000
            # mart_paper_nav 有 1 行
            n_nav = conn.execute("SELECT COUNT(*) FROM mart_paper_nav").fetchone()[0]
            assert n_nav == 1
            n_buy = conn.execute("SELECT COUNT(*) FROM fact_paper_position WHERE side='buy'").fetchone()[0]
            assert n_buy == 3
        finally:
            conn.close()
            mkt.close()

    def test_insufficient_cash_rejects_expensive_picks(self):
        """initial=1M / max_positions=20 → per_slot=45k 买不起 茅台 (180k/手), 只能买便宜的。"""
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.driver import run_paper_day

        conn = duck_connect(":memory:")
        mkt = duck_connect(":memory:")
        try:
            _seed_main(conn)
            _seed_market(mkt)
            result = run_paper_day(
                conn=conn, mkt_conn=mkt, snapshot_date="2026-05-08", prev_date="2026-05-07",
                initial_capital=1_000_000.0, max_positions=20,
            )
            # 茅台买不起 (45k 槽位 / 1800 元/股 → 0 手); 平安/宁王 OK
            assert result["n_entries"] == 2
            assert result["position_count"] == 2
        finally:
            conn.close()
            mkt.close()

    def test_idempotent_same_day(self):
        """同日跑两次, NAV 表保持 1 行 (DELETE+INSERT)。"""
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.driver import run_paper_day

        conn = duck_connect(":memory:")
        mkt = duck_connect(":memory:")
        try:
            _seed_main(conn)
            _seed_market(mkt)
            run_paper_day(conn=conn, mkt_conn=mkt, snapshot_date="2026-05-08", prev_date="2026-05-07")
            run_paper_day(conn=conn, mkt_conn=mkt, snapshot_date="2026-05-08", prev_date="2026-05-07")
            n_nav = conn.execute("SELECT COUNT(*) FROM mart_paper_nav").fetchone()[0]
            assert n_nav == 1
        finally:
            conn.close()
            mkt.close()

    def test_atomic_rollback_on_executemany_error(self):
        """模拟 executemany 抛错, 验证 4 表事务回滚。"""
        from unittest.mock import patch
        from services.duck_adapter import connect as duck_connect
        from services.paper_engine.driver import run_paper_day

        conn = duck_connect(":memory:")
        mkt = duck_connect(":memory:")
        try:
            _seed_main(conn)
            _seed_market(mkt)
            real_executemany = conn.executemany
            def explode(*a, **kw):
                raise RuntimeError("模拟 SIGPIPE")
            with patch.object(conn, "executemany", side_effect=explode):
                try:
                    run_paper_day(conn=conn, mkt_conn=mkt, snapshot_date="2026-05-08", prev_date="2026-05-07")
                    raise AssertionError("应该抛")
                except RuntimeError as e:
                    assert "模拟 SIGPIPE" in str(e)
            # 关键: mart_paper_nav 仍是空 (整事务回滚)
            n_nav = conn.execute("SELECT COUNT(*) FROM mart_paper_nav").fetchone()[0]
            assert n_nav == 0
            n_buy = conn.execute("SELECT COUNT(*) FROM fact_paper_position").fetchone()[0]
            assert n_buy == 0
        finally:
            conn.close()
            mkt.close()
