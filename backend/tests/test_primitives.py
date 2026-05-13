"""Phase ε+ §3.4 — 10 项基础设施单测。"""
from __future__ import annotations

import pytest


@pytest.fixture
def conn():
    from services.duck_adapter import connect as duck_connect
    from services.primitives.ddl import ensure_primitives_tables
    c = duck_connect(":memory:")
    ensure_primitives_tables(c)
    yield c
    c.close()


class TestDDL:
    def test_ensure_creates_all_tables(self, conn):
        names = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()}
        expected = {
            "dim_price_limit_rules", "dim_market_segment", "dim_trading_rule",
            "dim_fee_schedule", "dim_trading_session",
            "fact_daily_price_status",
            "dim_liquidity_threshold", "fact_stock_liquidity_daily",
            "dim_listing_status",
            "dim_style_factor", "fact_stock_style_daily",
            "fact_stock_market_cap_daily",
        }
        assert expected.issubset(names)

    def test_idempotent(self, conn):
        from services.primitives.ddl import ensure_primitives_tables
        for _ in range(3):
            ensure_primitives_tables(conn)
        n = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='dim_price_limit_rules'"
        ).fetchone()[0]
        assert n == 1


class TestSeedRules:
    def test_seed_price_limit_rules(self, conn):
        from services.primitives.seed import seed_price_limit_rules
        n = seed_price_limit_rules(conn)
        assert n >= 10
        # 主板正常股 ±10%
        r = conn.execute(
            "SELECT limit_up_pct, limit_down_pct FROM dim_price_limit_rules WHERE rule_id='main_normal'"
        ).fetchone()
        assert abs(r[0] - 0.10) < 1e-5
        assert abs(r[1] - (-0.10)) < 1e-5
        # 创业板 ±20%
        r = conn.execute(
            "SELECT limit_up_pct FROM dim_price_limit_rules WHERE rule_id='chinext_normal'"
        ).fetchone()
        assert abs(r[0] - 0.20) < 1e-5
        # 主板 ST ±5%
        r = conn.execute(
            "SELECT limit_up_pct FROM dim_price_limit_rules WHERE rule_id='main_st'"
        ).fetchone()
        assert abs(r[0] - 0.05) < 1e-5

    def test_seed_market_segments(self, conn):
        from services.primitives.seed import seed_market_segments
        n = seed_market_segments(conn)
        assert n == 5  # 上海主板/深圳主板/创业板/科创板/北交所
        # 创业板规则
        r = conn.execute(
            "SELECT code_prefix FROM dim_market_segment WHERE segment_id='chinext'"
        ).fetchone()
        assert "300" in r[0]

    def test_seed_trading_rules_t1(self, conn):
        from services.primitives.seed import seed_trading_rules
        n = seed_trading_rules(conn)
        assert n == 4
        # 主板 100 股一手
        r = conn.execute(
            "SELECT min_lot_size, settlement_cycle FROM dim_trading_rule WHERE rule_id='main_t1'"
        ).fetchone()
        assert r[0] == 100
        assert r[1] == "T+1"
        # 科创板 200 股一手
        r = conn.execute(
            "SELECT min_lot_size FROM dim_trading_rule WHERE rule_id='star_t1'"
        ).fetchone()
        assert r[0] == 200

    def test_seed_fee_schedule(self, conn):
        from services.primitives.seed import seed_fee_schedule
        n = seed_fee_schedule(conn)
        assert n >= 3
        # 印花税: 万 5 (0.0005), 单边卖出
        r = conn.execute(
            "SELECT rate_pct, side FROM dim_fee_schedule WHERE fee_id='stamp_tax_sell'"
        ).fetchone()
        assert abs(r[0] - 0.0005) < 1e-6
        assert r[1] == "sell"

    def test_seed_trading_sessions(self, conn):
        from services.primitives.seed import seed_trading_sessions
        n = seed_trading_sessions(conn)
        assert n == 4  # 开盘集合 / 上午连续 / 下午连续 / 收盘集合
        # 开盘集合竞价 9:15-9:25
        r = conn.execute(
            "SELECT start_time, end_time FROM dim_trading_session WHERE session_id='open_call'"
        ).fetchone()
        assert r[0] == "09:15"
        assert r[1] == "09:25"

    def test_seed_liquidity_thresholds(self, conn):
        from services.primitives.seed import seed_liquidity_thresholds
        n = seed_liquidity_thresholds(conn)
        assert n == 4
        # 主板 5000 万
        r = conn.execute(
            "SELECT min_amount_20d FROM dim_liquidity_threshold WHERE market_segment='main'"
        ).fetchone()
        assert r[0] == 50_000_000.0

    def test_seed_style_factors(self, conn):
        from services.primitives.seed import seed_style_factors
        n = seed_style_factors(conn)
        assert n == 6  # size/value/momentum/quality/volatility/liquidity
        # 全 6 个
        ids = {r[0] for r in conn.execute("SELECT factor_id FROM dim_style_factor").fetchall()}
        assert ids == {"size", "value", "momentum", "quality", "volatility", "liquidity"}


class TestSeedAll:
    def test_seed_all_primitives(self, conn):
        from services.primitives.seed import seed_all_primitives
        result = seed_all_primitives(conn)
        assert len(result) == 7
        # 总 seed 行数 ≥ 30
        total = sum(result.values())
        assert total >= 30
