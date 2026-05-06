import pytest

from conftest import duck_mem
from services import return_engine as subject


def test_follow_entry_price_prefers_entry_day_vwap():
    price, method = subject._resolve_follow_entry_price(
        {
            "open": 9.8,
            "close": 10.0,
            "amount": 1000.0,
            "volume": 100.0,
        }
    )

    assert price == pytest.approx(10.0)
    assert method == "entry_day_vwap_qfq"


def test_follow_entry_price_adjusts_hand_volume_vwap():
    price, method = subject._resolve_follow_entry_price(
        {
            "open": 9.8,
            "close": 10.0,
            "amount": 100000.0,
            "volume": 100.0,
        }
    )

    assert price == pytest.approx(10.0)
    assert method == "entry_day_vwap_qfq_volume_hand_adjusted"


def test_follow_entry_price_adjusts_hand_volume_vwap_with_qfq_factor():
    price, method = subject._resolve_follow_entry_price(
        {
            "open": 789.0,
            "close": 810.5583,
            "amount": 4584230912.0,
            "volume": 173308.0,
            "factor": 3.0357990066842,
        }
    )

    assert price == pytest.approx(803.0099)
    assert method == "entry_day_vwap_qfq_volume_hand_factor_adjusted"


def test_follow_entry_price_falls_back_to_open_when_vwap_missing():
    price, method = subject._resolve_follow_entry_price(
        {
            "open": 9.8,
            "close": 10.0,
            "amount": None,
            "volume": None,
        }
    )

    assert price == pytest.approx(9.8)
    assert method == "entry_day_vwap_qfq_fallback_open"


def test_return_kline_materialization_allows_fast_stock_cache_reads():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE price_kline (
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
            );
            CREATE VIEW v_price_kline_qfq AS
            SELECT code, date, freq, adjust, open, high, low, close, volume, amount, 1.0 AS factor
              FROM price_kline
             WHERE freq = 'daily'
               AND adjust = 'qfq';
            INSERT INTO price_kline VALUES
                ('000001', '2026-01-02', 'daily', 'qfq', 10, 11, 9, 10.5, 100, 1050),
                ('000001', '2026-01-31', 'monthly', 'qfq', 10, 11, 9, 10.5, 100, 1050),
                ('000002', '2026-01-02', 'daily', 'qfq', 20, 21, 19, 20.5, 200, 4100);
            """
        )

        relations = subject._materialize_return_kline_relations(conn, ["000001"])
        cache = subject._StockKlineCache(
            conn,
            "000001",
            daily_relation=relations["daily_relation"],
            monthly_relation=relations["monthly_relation"],
        )

        assert relations["daily_rows"] == 1
        assert relations["monthly_rows"] == 1
        assert cache.daily["2026-01-02"]["close"] == 10.5
        assert cache.monthly[0]["close"] == 10.5
    finally:
        conn.close()
