import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem  # noqa: E402
from services.screening_engine import run_all_screens  # noqa: E402


def test_run_all_screens_accepts_price_records():
    smart_conn = duck_mem()
    mkt_conn = duck_mem()
    try:
        smart_conn.executescript(
            """
            CREATE TABLE dim_active_a_stock (stock_code TEXT PRIMARY KEY, stock_name TEXT);
            CREATE TABLE excluded_stocks (stock_code TEXT PRIMARY KEY);
            CREATE TABLE dim_financial_latest (stock_code TEXT PRIMARY KEY, float_shares REAL);
            """
        )
        smart_conn.execute("INSERT INTO dim_active_a_stock VALUES (?, ?)", ("000001", "平安银行"))
        smart_conn.execute("INSERT INTO dim_financial_latest VALUES (?, ?)", ("000001", 1_000_000.0))
        smart_conn.commit()

        mkt_conn.executescript(
            """
            CREATE TABLE price_kline (
                code TEXT,
                date TEXT,
                freq TEXT,
                adjust TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL
            );
            CREATE VIEW v_price_kline_qfq AS
                SELECT code, date, freq, adjust, open, high, low, close, volume, amount
                  FROM price_kline
                 WHERE freq = 'daily' AND adjust = 'qfq';
            """
        )
        start = date.today() - timedelta(days=34)
        price_rows = []
        for idx in range(35):
            close = 10.0 + idx * 0.1
            price_rows.append((
                "000001",
                (start + timedelta(days=idx)).isoformat(),
                "daily",
                "qfq",
                close - 0.1,
                close + 0.2,
                close - 0.3,
                close,
                100_000.0 + idx,
                close * 100_000.0,
            ))
        mkt_conn.executemany("INSERT INTO price_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", price_rows)
        mkt_conn.commit()

        count = run_all_screens(smart_conn, mkt_conn)

        row = smart_conn.execute(
            "SELECT stock_code, stock_name, hit_count, float_market_cap FROM mart_stock_screening"
        ).fetchone()
        assert count == 1
        assert row["stock_code"] == "000001"
        assert row["stock_name"] == "平安银行"
        assert row["hit_count"] >= 0
        assert row["float_market_cap"] > 0
    finally:
        smart_conn.close()
        mkt_conn.close()
