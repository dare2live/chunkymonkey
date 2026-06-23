import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services.stock_turtle_engine import build_stock_turtle_features, ensure_tables


def _make_conn():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE mart_stock_trend (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            latest_notice_date TEXT,
            latest_report_date TEXT
        );

        CREATE TABLE dim_stock_stage_latest (
            stock_code TEXT PRIMARY KEY,
            path_state TEXT,
            stock_gate TEXT,
            amount_ratio_20_120 REAL,
            volatility_20d REAL,
            amplitude_20d REAL,
            stage_score_v1 REAL
        );

        CREATE TABLE dim_stock_forecast_latest (
            stock_code TEXT PRIMARY KEY,
            model_id TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT,
            qlib_score REAL,
            qlib_percentile REAL,
            forecast_score_v1 REAL
        );

        CREATE TABLE dim_stock_dc_industry (
            stock_code TEXT PRIMARY KEY,
            tdx_l1 TEXT,
            tdx_l2 TEXT,
            tdx_l3 TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT,
            tdx_l3_name TEXT
        );
        """
    )
    return conn


def _make_market_conn():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE price_kline (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            freq TEXT NOT NULL DEFAULT 'daily',
            adjust TEXT NOT NULL DEFAULT 'qfq',
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            PRIMARY KEY (code, date, freq, adjust)
        );
        CREATE VIEW v_price_kline_qfq AS
            SELECT code, date, freq, adjust, open, high, low, close, volume, amount
              FROM price_kline
             WHERE freq = 'daily' AND adjust = 'qfq';
        """
    )
    return conn


def _insert_price_rows(conn, code: str, rows: list[tuple[str, float, float, float, float, float, float]]):
    conn.executemany(
        """
        INSERT INTO price_kline (
            code, date, freq, adjust, open, high, low, close, volume, amount
        ) VALUES (?, ?, 'daily', 'qfq', ?, ?, ?, ?, ?, ?)
        """,
        [(code, *row) for row in rows],
    )
    conn.commit()


def test_build_stock_turtle_features_materializes_breakout_and_reference_levels():
    conn = _make_conn()
    mkt_conn = _make_market_conn()
    try:
        ensure_tables(conn)
        conn.execute(
            "INSERT INTO mart_stock_trend (stock_code, stock_name, latest_notice_date, latest_report_date) VALUES (?, ?, ?, ?)",
            ("600001", "海龟突破", "2026-04-10", "2026-03-31"),
        )
        conn.execute(
            """
            INSERT INTO dim_stock_stage_latest (
                stock_code, path_state, stock_gate, amount_ratio_20_120,
                volatility_20d, amplitude_20d, stage_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("600001", "温和验证", "跟随", 1.6, 2.3, 15.0, 78.0),
        )
        conn.execute(
            """
            INSERT INTO dim_stock_forecast_latest (
                stock_code, model_id, tdx_l1_name, tdx_l2_name,
                qlib_score, qlib_percentile, forecast_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("600001", "model_turtle", "电子", "芯片", 0.91, 88.0, 74.0),
        )
        conn.commit()

        start = date.today() - timedelta(days=59)
        price_rows = []
        for idx in range(60):
            current_day = (start + timedelta(days=idx)).isoformat()
            if idx < 59:
                high = 100 + idx
                low = high - 4
                close = high - 0.5
                open_price = high - 1.5
            else:
                high = 161.0
                low = 156.0
                close = 160.0
                open_price = 158.5
            volume = 1_000_000 + idx * 5_000
            amount = volume * close
            price_rows.append((current_day, open_price, high, low, close, volume, amount))
        _insert_price_rows(mkt_conn, "600001", price_rows)

        inserted = build_stock_turtle_features(conn, mkt_conn, snapshot_date="2026-04-13")

        assert inserted == 1
        row = conn.execute(
            "SELECT * FROM dim_stock_turtle_latest WHERE stock_code = ?",
            ("600001",),
        ).fetchone()
        assert row["entry_level_20"] == 158.0
        assert row["entry_level_55"] == 158.0
        assert row["entry_signal_20"] == 1
        assert row["entry_signal_55"] == 1
        assert row["exit_signal_10"] == 0
        assert row["exit_signal_20"] == 0
        assert row["preferred_system"] == "S2"
        assert row["turtle_setup_state"] == "S2突破触发"
        assert row["breakout_dist_20_pct"] > 0
        assert row["breakout_dist_55_pct"] > 0
        assert row["atr_14"] is not None and row["atr_14"] > 0
        # DuckDB REAL = FLOAT32; 与 Python float64 复算后用 approx 比较.
        assert row["stop_level_55_2n"] == pytest.approx(row["entry_level_55"] - 2 * row["atr_14"], abs=1e-3)
        assert row["add_level_55_1"] == pytest.approx(row["entry_level_55"] + 0.5 * row["atr_14"], abs=1e-3)
        assert row["add_level_55_2"] == pytest.approx(row["entry_level_55"] + 1.0 * row["atr_14"], abs=1e-3)
        assert row["add_level_55_3"] == pytest.approx(row["entry_level_55"] + 1.5 * row["atr_14"], abs=1e-3)
        assert row["turtle_execution_score_v1"] >= 65
    finally:
        mkt_conn.close()
        conn.close()


def test_build_stock_turtle_features_marks_exit_state_on_break_of_exit_channels():
    conn = _make_conn()
    mkt_conn = _make_market_conn()
    try:
        ensure_tables(conn)
        conn.execute(
            "INSERT INTO mart_stock_trend (stock_code, stock_name, latest_notice_date, latest_report_date) VALUES (?, ?, ?, ?)",
            ("000001", "海龟退出", "2026-04-10", "2026-03-31"),
        )
        conn.execute(
            """
            INSERT INTO dim_stock_stage_latest (
                stock_code, path_state, stock_gate, amount_ratio_20_120,
                volatility_20d, amplitude_20d, stage_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("000001", "失效破坏", "回避", 0.8, 6.4, 38.0, 32.0),
        )
        conn.execute(
            """
            INSERT INTO dim_stock_forecast_latest (
                stock_code, model_id, tdx_l1_name, tdx_l2_name,
                qlib_score, qlib_percentile, forecast_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("000001", "model_turtle", "消费", "食品", 0.31, 28.0, 35.0),
        )
        conn.commit()

        start = date.today() - timedelta(days=24)
        price_rows = []
        for idx in range(25):
            current_day = (start + timedelta(days=idx)).isoformat()
            if idx < 24:
                high = 100.0
                low = 95.0
                close = 97.0
                open_price = 96.0
            else:
                high = 92.0
                low = 89.0
                close = 90.0
                open_price = 91.0
            volume = 800_000 + idx * 3_000
            amount = volume * close
            price_rows.append((current_day, open_price, high, low, close, volume, amount))
        _insert_price_rows(mkt_conn, "000001", price_rows)

        inserted = build_stock_turtle_features(conn, mkt_conn, snapshot_date="2026-04-13")

        assert inserted == 1
        row = conn.execute(
            "SELECT * FROM dim_stock_turtle_latest WHERE stock_code = ?",
            ("000001",),
        ).fetchone()
        assert row["entry_signal_20"] == 0
        assert row["entry_signal_55"] == 0
        assert row["exit_signal_10"] == 1
        assert row["exit_signal_20"] == 1
        assert row["exit_level_10"] == 95.0
        assert row["exit_level_20"] == 95.0
        assert row["turtle_setup_state"] == "20日退出触发"
        assert row["turtle_execution_score_v1"] < 50
        assert row["turtle_reason"]
    finally:
        mkt_conn.close()
        conn.close()


def test_build_stock_turtle_features_falls_back_to_shared_industry_alias_map(monkeypatch):
    conn = _make_conn()
    mkt_conn = _make_market_conn()
    try:
        ensure_tables(conn)
        monkeypatch.setattr(
            "services.stock_turtle_engine.load_industry_map",
            lambda _conn: {"600010": {"tdx_l1_name": "电子", "tdx_l2_name": "芯片"}},
        )
        conn.execute(
            "INSERT INTO mart_stock_trend (stock_code, stock_name, latest_notice_date, latest_report_date) VALUES (?, ?, ?, ?)",
            ("600010", "海龟别名", "2026-04-10", "2026-03-31"),
        )
        conn.execute(
            """
            INSERT INTO dim_stock_stage_latest (
                stock_code, path_state, stock_gate, amount_ratio_20_120,
                volatility_20d, amplitude_20d, stage_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("600010", "温和验证", "跟随", 1.5, 2.4, 15.0, 76.0),
        )
        conn.execute(
            """
            INSERT INTO dim_stock_forecast_latest (
                stock_code, model_id, tdx_l1_name, tdx_l2_name,
                qlib_score, qlib_percentile, forecast_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("600010", "model_alias_turtle", "", "", 0.86, 84.0, 73.0),
        )
        conn.commit()

        start = date.today() - timedelta(days=59)
        price_rows = []
        for idx in range(60):
            current_day = (start + timedelta(days=idx)).isoformat()
            high = 110 + idx
            low = high - 4
            close = high - 0.5
            open_price = high - 1.5
            volume = 900_000 + idx * 4_000
            amount = volume * close
            price_rows.append((current_day, open_price, high, low, close, volume, amount))
        _insert_price_rows(mkt_conn, "600010", price_rows)

        inserted = build_stock_turtle_features(conn, mkt_conn, snapshot_date="2026-04-13")

        row = conn.execute(
            "SELECT tdx_l1_name, tdx_l2_name FROM dim_stock_turtle_latest WHERE stock_code = ?",
            ("600010",),
        ).fetchone()
        assert inserted == 1
        assert row["tdx_l1_name"] == "电子"
        assert row["tdx_l2_name"] == "芯片"
    finally:
        mkt_conn.close()
        conn.close()


def test_build_stock_turtle_features_ignores_stage_gate_when_scoring_risk():
    conn = _make_conn()
    mkt_conn = _make_market_conn()
    try:
        ensure_tables(conn)
        conn.execute(
            "INSERT INTO mart_stock_trend (stock_code, stock_name, latest_notice_date, latest_report_date) VALUES (?, ?, ?, ?)",
            ("600009", "海龟去闸门", "2026-04-10", "2026-03-31"),
        )
        conn.execute(
            """
            INSERT INTO dim_stock_stage_latest (
                stock_code, path_state, stock_gate, amount_ratio_20_120,
                volatility_20d, amplitude_20d, stage_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("600009", "温和验证", "跟随", 1.3, 2.8, 16.0, 72.0),
        )
        conn.execute(
            """
            INSERT INTO dim_stock_forecast_latest (
                stock_code, model_id, tdx_l1_name, tdx_l2_name,
                qlib_score, qlib_percentile, forecast_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("600009", "model_gate_free", "电子", "芯片", 0.82, 79.0, 71.0),
        )
        conn.commit()

        start = date.today() - timedelta(days=59)
        price_rows = []
        for idx in range(60):
            current_day = (start + timedelta(days=idx)).isoformat()
            high = 120 + idx * 0.8
            low = high - 3.5
            close = high - 0.3
            open_price = high - 1.1
            volume = 900_000 + idx * 4_000
            amount = volume * close
            price_rows.append((current_day, open_price, high, low, close, volume, amount))
        _insert_price_rows(mkt_conn, "600009", price_rows)

        build_stock_turtle_features(conn, mkt_conn, snapshot_date="2026-04-13")
        follow_row = conn.execute(
            "SELECT turtle_risk_score, turtle_execution_score_v1 FROM dim_stock_turtle_latest WHERE stock_code = ?",
            ("600009",),
        ).fetchone()

        conn.execute(
            "UPDATE dim_stock_stage_latest SET stock_gate = ? WHERE stock_code = ?",
            ("回避", "600009"),
        )
        conn.commit()

        build_stock_turtle_features(conn, mkt_conn, snapshot_date="2026-04-14")
        avoid_row = conn.execute(
            "SELECT turtle_risk_score, turtle_execution_score_v1 FROM dim_stock_turtle_latest WHERE stock_code = ?",
            ("600009",),
        ).fetchone()

        assert follow_row["turtle_risk_score"] == avoid_row["turtle_risk_score"]
        assert follow_row["turtle_execution_score_v1"] == avoid_row["turtle_execution_score_v1"]
    finally:
        mkt_conn.close()
        conn.close()
