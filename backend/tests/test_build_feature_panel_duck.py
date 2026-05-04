import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import build_feature_panel_duck as subject


def _iso_days(start: date, count: int) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


def _yyyymmdd(day: str) -> str:
    return day.replace("-", "")


def _seed_minimal_sources(con):
    con.execute("CREATE SCHEMA market")
    con.execute("CREATE SCHEMA smartmoney")
    subject.execute_script(
        con,
        """
        CREATE TABLE market.price_kline_tdxhub (
            code TEXT,
            date TEXT,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            amount DOUBLE,
            freq TEXT,
            adjust TEXT
        );
        CREATE TABLE market.price_kline AS SELECT * FROM market.price_kline_tdxhub WHERE FALSE;
        CREATE TABLE smartmoney.raw_margin_daily (
            stock_code TEXT,
            trade_date TEXT,
            rz_balance DOUBLE
        );
        CREATE TABLE smartmoney.fact_institution_event (
            stock_code TEXT,
            notice_date TEXT
        );
        CREATE TABLE smartmoney.fact_executive_trade_event (
            stock_code TEXT,
            notice_date TEXT,
            direction TEXT,
            total_change_pct_total DOUBLE
        );
        CREATE TABLE smartmoney.fact_lhb_event (
            stock_code TEXT,
            trade_date TEXT,
            is_inst_net_buy INTEGER
        );
        CREATE TABLE smartmoney.fact_fundamental_quarterly (
            stock_code TEXT,
            report_date TEXT,
            shareholder_count DOUBLE,
            inst_count DOUBLE,
            fund_count DOUBLE,
            qfii_count DOUBLE,
            yjyg_lower_pct DOUBLE,
            yjyg_upper_pct DOUBLE,
            roe DOUBLE,
            eps_basic DOUBLE
        );
        CREATE TABLE smartmoney.dim_stock_tdx_industry (
            stock_code TEXT,
            tdx_l1 TEXT
        );
        """
    )
    days = _iso_days(date(2026, 1, 1), 45)
    kline_rows = []
    margin_rows = []
    for idx, day in enumerate(days):
        for offset, code in enumerate(["000001", "000002", "510300"]):
            close = 10.0 + idx * (0.2 + offset * 0.03) + offset
            kline_rows.append((
                code,
                day,
                close - 0.1,
                close + 0.2,
                close - 0.3,
                close,
                1000.0 + idx * 10 + offset,
                1_000_000.0 + idx * 1000 + offset,
                "daily",
                "qfq",
            ))
            if code != "510300":
                margin_rows.append((code, _yyyymmdd(day), 1000.0 + idx * 5 + offset))
    con.executemany("INSERT INTO market.price_kline_tdxhub VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kline_rows)
    con.executemany("INSERT INTO smartmoney.raw_margin_daily VALUES (?, ?, ?)", margin_rows)
    con.executemany(
        "INSERT INTO smartmoney.fact_institution_event VALUES (?, ?)",
        [("000001", "2026-01-03"), ("000002", "20260104")],
    )
    con.executemany(
        "INSERT INTO smartmoney.fact_executive_trade_event VALUES (?, ?, ?, ?)",
        [("000001", "2026-01-05", "buy", 1.5), ("000002", "20260106", "buy", 0.5)],
    )
    con.executemany(
        "INSERT INTO smartmoney.fact_lhb_event VALUES (?, ?, ?)",
        [("000001", "2026-01-07", 1), ("000002", "20260108", 1)],
    )
    con.executemany(
        "INSERT INTO smartmoney.fact_fundamental_quarterly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("000001", "20251231", 100.0, 5.0, 2.0, 1.0, 0.1, 0.2, 0.12, 0.8),
            ("000002", "20251231", 120.0, 6.0, 3.0, 1.0, 0.2, 0.3, 0.10, 0.7),
        ],
    )
    con.executemany(
        "INSERT INTO smartmoney.dim_stock_tdx_industry VALUES (?, ?)",
        [("000001", "bank"), ("000002", "tech")],
    )


def test_build_panel_writes_fact_feature_panel_without_dataframe(monkeypatch):
    con = duckdb.connect(":memory:")
    try:
        _seed_minimal_sources(con)
        monkeypatch.setattr(subject, "get_duck", lambda writable=True: con)

        summary = subject.build_panel("2026-01-01")
        sample = con.execute(
            """
            SELECT stock_code, ret_5d, ret_20d, momentum_diff,
                   inst_event_count_30d, exec_buy_ge1_count_90d,
                   days_since_exec_buy, regime_flag, forward_ret_20d
            FROM fact_feature_panel
            WHERE stock_code = '000001'
            ORDER BY date
            LIMIT 1
            """
        ).fetchone()

        assert summary["rows"] > 0
        assert summary["label_non_null"] > 0
        assert sample[0] == "000001"
        assert sample[4] >= 0
        assert sample[5] >= 0
        assert sample[6] >= -1
        assert sample[7] in {"na", "up", "down", "flat", None}
    finally:
        con.close()
