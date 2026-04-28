import sys
from datetime import date, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
import services.sector_momentum as sector_momentum


def _make_smart_conn():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT,
            tdx_l1 TEXT,
            tdx_l2 TEXT,
            tdx_l3 TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT,
            tdx_l3_name TEXT
        );

        CREATE TABLE fact_institution_event (
            stock_code TEXT,
            institution_id TEXT,
            event_type TEXT,
            report_date TEXT
        );
        """
    )
    return conn


def _make_market_conn():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE price_kline (
            code TEXT,
            date TEXT,
            close REAL,
            high REAL,
            low REAL,
            freq TEXT,
            adjust TEXT
        );
        """
    )
    return conn


def _seed_industry_rows(conn):
    conn.executemany(
        "INSERT INTO dim_stock_tdx_industry (stock_code, tdx_l1, tdx_l2, tdx_l3, tdx_l1_name, tdx_l2_name, tdx_l3_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (f"6000{idx:02d}", "T10", "T1001", f"T100{idx:03d}", "电子", "半导体", f"芯片{idx}")
            for idx in range(1, 6)
        ],
    )
    conn.commit()


def _seed_price_rows(conn):
    start = date(2026, 1, 1)
    rows = []
    for idx in range(1, 6):
        code = f"6000{idx:02d}"
        for day in range(80):
            close = 10.0 + idx * 0.2 + day * 0.15
            rows.append(
                (
                    code,
                    (start + timedelta(days=day)).strftime("%Y-%m-%d"),
                    round(close, 2),
                    round(close * 1.02, 2),
                    round(close * 0.98, 2),
                    "daily",
                    "qfq",
                )
            )
    conn.executemany("INSERT INTO price_kline VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()


def test_calc_sector_momentum_builds_sector_snapshot():
    smart_conn = _make_smart_conn()
    mkt_conn = _make_market_conn()
    try:
        _seed_industry_rows(smart_conn)
        _seed_price_rows(mkt_conn)

        count = sector_momentum.calc_sector_momentum(smart_conn, mkt_conn)

        row = smart_conn.execute(
            "SELECT sector_name, sector_level, momentum_score, return_1m FROM mart_sector_momentum"
        ).fetchone()
        assert count == 1
        assert row["sector_name"] == "电子"
        assert row["sector_level"] == "L1"
        assert row["momentum_score"] is not None
        assert row["return_1m"] > 0
    finally:
        smart_conn.close()
        mkt_conn.close()


def test_calc_dual_confirm_maps_recent_events_to_sector_name():
    conn = _make_smart_conn()
    try:
        sector_momentum.ensure_tables(conn)
        conn.execute(
            "INSERT INTO mart_sector_momentum (sector_name, momentum_score, trend_state, macd_cross) VALUES (?, ?, ?, ?)",
            ("电子", 72.0, "recovering", 1),
        )
        conn.execute(
            "INSERT INTO dim_stock_tdx_industry (stock_code, tdx_l1, tdx_l2, tdx_l3, tdx_l1_name, tdx_l2_name, tdx_l3_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("600001", "T10", "T1001", "T100101", "电子", "半导体", "芯片设计"),
        )
        conn.execute(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?)",
            ("600001", "inst_a", "new_entry", date.today().strftime("%Y-%m-%d")),
        )
        conn.commit()

        count = sector_momentum.calc_dual_confirm(conn)

        row = conn.execute(
            "SELECT sector_name, sector_momentum_score, dual_confirm FROM mart_dual_confirm"
        ).fetchone()
        assert count == 1
        assert row["sector_name"] == "电子"
        assert row["sector_momentum_score"] == 72.0
        assert row["dual_confirm"] == 1
    finally:
        conn.close()