import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.qlib_full_engine import ensure_tables as ensure_qlib_tables
from services.stock_forecast_engine import build_stock_forecast_features, ensure_tables
from services.utils import clamp_score


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dim_stock_industry (
            stock_code TEXT PRIMARY KEY,
            sw_level1 TEXT,
            sw_level2 TEXT
        );

        CREATE TABLE dim_stock_stage_latest (
            stock_code TEXT PRIMARY KEY,
            volatility_20d REAL,
            max_drawdown_60d REAL
        );
        """
    )
    return conn


def test_build_stock_forecast_features_prefers_sw2_group_and_matches_formula(monkeypatch):
    conn = _make_conn()
    try:
        ensure_qlib_tables(conn)
        monkeypatch.setattr(
            "services.stock_forecast_engine.sync_latest_predictions_to_stock_trend",
            lambda smart_conn, model_id=None: 0,
        )

        conn.execute(
            "INSERT INTO qlib_model_state (model_id, status, created_at) VALUES (?, ?, ?)",
            ("model_1", "trained", "2026-04-13T09:00:00"),
        )
        for idx in range(15):
            code = f"60{idx:04d}"
            qlib_score = round(1.0 - idx * 0.02, 4)
            qlib_percentile = round((1 - idx / 14) * 100, 2)
            conn.execute(
                """
                INSERT INTO qlib_predictions (
                    model_id, stock_code, stock_name, predict_date,
                    qlib_score, qlib_rank, qlib_percentile
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("model_1", code, f"芯片股{idx}", "2026-04-13", qlib_score, idx + 1, qlib_percentile),
            )
            conn.execute(
                "INSERT INTO dim_stock_industry (stock_code, sw_level1, sw_level2) VALUES (?, ?, ?)",
                (code, "电子", "芯片"),
            )
            conn.execute(
                "INSERT INTO dim_stock_stage_latest (stock_code, volatility_20d, max_drawdown_60d) VALUES (?, ?, ?)",
                (code, 10 + idx, 5 + idx),
            )
        conn.commit()

        inserted = build_stock_forecast_features(conn, snapshot_date="2026-04-13")

        assert inserted == 15
        row = conn.execute(
            "SELECT * FROM dim_stock_forecast_latest WHERE stock_code = ?",
            ("600000",),
        ).fetchone()
        assert row["industry_relative_group"] == "SW2:芯片"
        assert row["forecast_20d_score"] == row["qlib_percentile"]

        expected_risk = clamp_score(
            row["forecast_20d_score"] * 0.55
            + row["volatility_rank"] * 0.25
            + row["drawdown_rank"] * 0.20
        )
        expected_total = clamp_score(
            row["forecast_20d_score"] * 0.40
            + row["forecast_60d_excess_score"] * 0.40
            + expected_risk * 0.20
        )

        assert row["forecast_risk_adjusted_score"] == expected_risk
        assert row["forecast_score_v1"] == expected_total
        assert "Qlib短期预测较强" in row["forecast_reason"]
    finally:
        conn.close()


def test_build_stock_forecast_features_clears_latest_when_no_trained_model():
    conn = _make_conn()
    try:
        ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO dim_stock_forecast_latest (
                stock_code, snapshot_date, model_id, forecast_score_v1
            ) VALUES (?, ?, ?, ?)
            """,
            ("000001", "2026-04-12", "stale_model", 88.0),
        )
        conn.commit()

        inserted = build_stock_forecast_features(conn, snapshot_date="2026-04-13")

        assert inserted == 0
        remaining = conn.execute("SELECT COUNT(*) FROM dim_stock_forecast_latest").fetchone()[0]
        assert remaining == 0
    finally:
        conn.close()
