import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

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
            adjust TEXT,
            source_name TEXT,
            source_tier SMALLINT,
            is_fallback BOOLEAN
        );
        CREATE TABLE market.price_kline AS SELECT * FROM market.price_kline_tdxhub WHERE FALSE;
        CREATE VIEW market.v_price_kline_qfq AS
            SELECT code, date, freq, adjust, open, high, low, close, volume, amount,
                   source_name, source_tier, is_fallback
            FROM market.price_kline_tdxhub;
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
        CREATE TABLE smartmoney.fact_shareholder_plan_tdx_f10 (
            stock_code TEXT,
            source_available_date TEXT,
            source_notice_date TEXT,
            direction TEXT,
            progress TEXT,
            target_amount_min BIGINT,
            target_amount_max BIGINT,
            fetched_at TEXT
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
                "tdxhub",
                1,
                False,
            ))
            if code != "510300":
                margin_day = day if idx % 2 else _yyyymmdd(day)
                margin_rows.append((code, margin_day, 1000.0 + idx * 5 + offset))
    con.executemany("INSERT INTO market.price_kline_tdxhub VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kline_rows)
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
        "INSERT INTO smartmoney.fact_shareholder_plan_tdx_f10 VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("000001", "2026-01-04", "2026-01-04", "增持计划", "完成", 3_000_000_000, 3_300_000_000, "2026-01-04T10:00:00"),
            ("000001", "2026-01-10", "2026-01-10", "减持计划", "进行中", None, 500_000_000, "2026-01-10T10:00:00"),
        ],
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


def test_build_panel_writes_fact_feature_panel_without_dataframe():
    con = duckdb.connect(":memory:")
    try:
        _seed_minimal_sources(con)

        summary = subject._build_panel_with_connection(con, "2026-01-01")
        sample = con.execute(
            """
            SELECT stock_code, ret_5d, ret_20d, momentum_diff,
                   inst_event_count_30d, exec_buy_ge1_count_90d,
                   days_since_exec_buy, regime_flag,
                   forward_ret_5d, forward_ret_10d, forward_ret_20d, forward_ret_60d,
                   forward_ret_90d,
                   rz_balance,
                   yjyg_lower_pct, kline_source_name, kline_source_tier,
                   kline_is_fallback
            FROM fact_feature_panel
            WHERE stock_code = '000001'
              AND date = '2026-01-02'
            """
        ).fetchone()

        assert summary["rows"] > 0
        assert summary["label_non_null"] > 0
        assert sample[0] == "000001"
        assert sample[4] >= 0
        assert sample[5] >= 0
        assert sample[6] >= -1
        assert sample[7] in {"na", "up", "down", "flat", None}
        assert sample[8] is not None
        assert sample[9] is not None
        assert sample[10] is not None
        assert sample[11] is None
        assert sample[12] is None
        assert sample[13] is not None
        assert sample[14] is None
        assert sample[15] == "tdxhub"
        assert sample[16] == 1
        assert sample[17] is False
        plan_sample = con.execute(
            """
            SELECT shareholder_plan_increase_count_180d,
                   shareholder_plan_decrease_count_180d,
                   shareholder_plan_completed_count_180d,
                   shareholder_plan_increase_amount_max_180d,
                   shareholder_plan_decrease_amount_max_180d,
                   days_since_shareholder_plan_increase,
                   days_since_shareholder_plan_decrease
              FROM fact_feature_panel
             WHERE stock_code = '000001'
               AND date = '2026-01-05'
            """
        ).fetchone()
        assert plan_sample[0] == 1
        assert plan_sample[1] == 0
        assert plan_sample[2] == 1
        assert plan_sample[3] == pytest.approx(3_300_000_000)
        assert plan_sample[4] == pytest.approx(0.0)
        assert plan_sample[5] == 1
        assert plan_sample[6] == -1
    finally:
        con.close()


def test_full_build_uses_prior_kline_buffer_but_writes_requested_start_only():
    con = duckdb.connect(":memory:")
    try:
        _seed_minimal_sources(con)

        summary = subject._build_panel_with_connection(con, "2026-01-11")
        row = con.execute(
            """
            SELECT MIN(date), MAX(date)
              FROM fact_feature_panel
            """
        ).fetchone()
        sample = con.execute(
            """
            SELECT close, ret_1d, ret_5d, ma_ratio_5, amount_chg_5d
              FROM fact_feature_panel
             WHERE stock_code = '000001'
               AND date = '2026-01-11'
            """
        ).fetchone()

        assert summary["rows"] > 0
        assert row[0] == "2026-01-11"
        assert row[1] >= "2026-01-11"
        assert sample[0] is not None
        assert sample[1] is not None
        assert sample[2] is not None
        assert sample[3] is not None
        assert sample[4] is not None
    finally:
        con.close()


def test_full_build_uses_active_a_stock_universe_when_available():
    con = duckdb.connect(":memory:")
    try:
        _seed_minimal_sources(con)
        con.execute(
            """
            CREATE TABLE smartmoney.dim_active_a_stock (
                stock_code TEXT,
                stock_name TEXT,
                market TEXT,
                source TEXT,
                updated_at TEXT
            )
            """
        )
        con.executemany(
            "INSERT INTO smartmoney.dim_active_a_stock VALUES (?, ?, ?, ?, ?)",
            [
                ("000001", "平安银行", "SZ", "fixture", "2026-05-06"),
                ("000002", "万科A", "SZ", "fixture", "2026-05-06"),
            ],
        )

        subject._build_panel_with_connection(con, "2026-01-01")
        codes = {
            row[0]
            for row in con.execute("SELECT DISTINCT stock_code FROM fact_feature_panel").fetchall()
        }

        assert codes == {"000001", "000002"}
    finally:
        con.close()


def test_market_regime_prefers_hs300_index_over_etf_proxy():
    con = duckdb.connect(":memory:")
    try:
        _seed_minimal_sources(con)
        days = _iso_days(date(2026, 1, 1), 45)
        index_rows = []
        for idx, day in enumerate(days):
            close = 100.0 + idx * 2.0
            index_rows.append((
                "000300",
                day,
                close - 0.5,
                close + 0.5,
                close - 1.0,
                close,
                10_000.0 + idx,
                10_000_000.0 + idx,
                "daily",
                "qfq",
                "tdxhub_index",
                1,
                False,
            ))
        con.executemany("INSERT INTO market.price_kline_tdxhub VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", index_rows)

        subject._build_panel_with_connection(con, "2026-01-01")
        actual = con.execute(
            """
            SELECT hs300_ret_20d
              FROM fact_feature_panel
             WHERE stock_code = '000001'
               AND date = '2026-01-31'
            """
        ).fetchone()[0]
        expected = (100.0 + 30 * 2.0) / (100.0 + 10 * 2.0) - 1

        assert actual == pytest.approx(expected)
    finally:
        con.close()


def test_ensure_fact_panel_schema_adds_new_horizon_label_columns_to_existing_table():
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                close REAL,
                forward_ret_20d REAL
            )
            """
        )

        subject._ensure_fact_panel_schema(con)
        cols = {row[1] for row in con.execute("PRAGMA table_info('fact_feature_panel')").fetchall()}

        assert {
            "forward_ret_5d",
            "forward_ret_10d",
            "forward_ret_60d",
            "forward_ret_90d",
            "follow_net_return_5d",
            "follow_net_return_10d",
            "follow_net_return_20d",
            "follow_net_return_60d",
            "follow_net_return_90d",
            "shareholder_plan_increase_count_180d",
            "shareholder_plan_decrease_count_180d",
            "shareholder_plan_completed_count_180d",
            "shareholder_plan_increase_amount_max_180d",
            "shareholder_plan_decrease_amount_max_180d",
            "days_since_shareholder_plan_increase",
            "days_since_shareholder_plan_decrease",
        }.issubset(cols)
    finally:
        con.close()


def test_feature_registry_covers_panel_and_keeps_labels_out_of_inputs():
    registry_result = subject.validate_feature_registry()
    inputs = subject.feature_input_columns()

    assert registry_result["status"] == "passed"
    assert "ret_20d" in inputs
    for label in ("forward_ret_5d", "forward_ret_10d", "forward_ret_20d", "forward_ret_60d", "forward_ret_90d"):
        assert label not in inputs
    for label in (
        "follow_net_return_5d",
        "follow_net_return_10d",
        "follow_net_return_20d",
        "follow_net_return_60d",
        "follow_net_return_90d",
    ):
        assert label not in inputs
