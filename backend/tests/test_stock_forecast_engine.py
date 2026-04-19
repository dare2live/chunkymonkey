import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.qlib_full_engine import ensure_tables as ensure_qlib_tables
from services.stock_forecast_engine import apply_forecast_score_aliases, build_stock_forecast_features, ensure_tables
from services.utils import clamp_score


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT PRIMARY KEY,
            tdx_l1 TEXT,
            tdx_l2 TEXT
        );

        CREATE TABLE dim_stock_stage_latest (
            stock_code TEXT PRIMARY KEY,
            volatility_20d REAL,
            max_drawdown_60d REAL
        );
        """
    )
    return conn


def test_build_stock_forecast_features_prefers_tdx2_group_and_matches_formula(monkeypatch):
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
                "INSERT INTO dim_stock_tdx_industry (stock_code, tdx_l1, tdx_l2) VALUES (?, ?, ?)",
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
        assert row["industry_relative_group"] == "TDX2:芯片"
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
        assert "Qlib截面排序较强" in row["forecast_reason"]
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


def test_build_stock_forecast_features_prefers_active_model_and_marks_global_fallback(monkeypatch):
    conn = _make_conn()
    try:
        ensure_qlib_tables(conn)
        monkeypatch.setattr(
            "services.stock_forecast_engine.sync_latest_predictions_to_stock_trend",
            lambda smart_conn, model_id=None: 0,
        )

        conn.executemany(
            "INSERT INTO qlib_model_state (model_id, status, created_at, is_active) VALUES (?, ?, ?, ?)",
            [
                ("model_active", "trained", "2026-04-12T09:00:00", 1),
                ("model_latest", "trained", "2026-04-13T09:00:00", 0),
            ],
        )
        for idx in range(3):
            code = f"00{idx + 1:04d}"
            conn.execute(
                """
                INSERT INTO qlib_predictions (
                    model_id, stock_code, stock_name, predict_date,
                    qlib_score, qlib_rank, qlib_percentile
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("model_active", code, f"回退股{idx}", "2026-04-13", 0.8 - idx * 0.1, idx + 1, 95.0 - idx * 20.0),
            )
            conn.execute(
                "INSERT INTO dim_stock_industry (stock_code, sw_level1, sw_level2) VALUES (?, ?, ?)",
                (code, "电子", f"小组{idx}"),
            )
            conn.execute(
                "INSERT INTO dim_stock_stage_latest (stock_code, volatility_20d, max_drawdown_60d) VALUES (?, ?, ?)",
                (code, 10 + idx, 5 + idx),
            )
        conn.commit()

        inserted = build_stock_forecast_features(conn, snapshot_date="2026-04-13")

        assert inserted == 3
        rows = conn.execute(
            "SELECT stock_code, model_id, industry_relative_group FROM dim_stock_forecast_latest ORDER BY stock_code"
        ).fetchall()
        assert {row["model_id"] for row in rows} == {"model_active"}
        assert {row["industry_relative_group"] for row in rows} == {"全市场回退"}
    finally:
        conn.close()


def test_build_stock_forecast_features_uses_shared_industry_alias_map(monkeypatch):
    conn = _make_conn()
    try:
        ensure_qlib_tables(conn)
        monkeypatch.setattr(
            "services.stock_forecast_engine.sync_latest_predictions_to_stock_trend",
            lambda smart_conn, model_id=None: 0,
        )
        monkeypatch.setattr(
            "services.stock_forecast_engine.load_industry_map",
            lambda _conn: {
                f"60{idx:04d}": {"industry_level1": "电子", "industry_level2": "芯片"}
                for idx in range(15)
            },
        )

        conn.execute(
            "INSERT INTO qlib_model_state (model_id, status, created_at) VALUES (?, ?, ?)",
            ("model_alias", "trained", "2026-04-13T09:00:00"),
        )
        for idx in range(15):
            code = f"60{idx:04d}"
            conn.execute(
                """
                INSERT INTO qlib_predictions (
                    model_id, stock_code, stock_name, predict_date,
                    qlib_score, qlib_rank, qlib_percentile
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("model_alias", code, f"别名股{idx}", "2026-04-13", 0.9 - idx * 0.01, idx + 1, 95.0 - idx * 2),
            )
            conn.execute(
                "INSERT INTO dim_stock_stage_latest (stock_code, volatility_20d, max_drawdown_60d) VALUES (?, ?, ?)",
                (code, 10 + idx, 5 + idx),
            )
        conn.commit()

        inserted = build_stock_forecast_features(conn, snapshot_date="2026-04-13")

        row = conn.execute(
            "SELECT sw_level1, sw_level2, industry_relative_group FROM dim_stock_forecast_latest WHERE stock_code = ?",
            ("600000",),
        ).fetchone()
        assert inserted == 15
        assert row["sw_level1"] == "电子"
        assert row["sw_level2"] == "芯片"
        assert row["industry_relative_group"] == "二级行业:芯片"
    finally:
        conn.close()


def test_apply_forecast_score_aliases_normalizes_legacy_industry_group_labels():
    level2_row = apply_forecast_score_aliases({
        "forecast_20d_score": 61.0,
        "forecast_60d_excess_score": 58.0,
        "industry_relative_group": "SW2:芯片",
        "forecast_industry_relative_group": "ALL_FALLBACK",
    })

    assert level2_row["industry_relative_group"] == "二级行业:芯片"
    assert level2_row["forecast_industry_relative_group"] == "全市场回退"

    level1_row = apply_forecast_score_aliases({"industry_relative_group": "SW1:电子"})
    assert level1_row["industry_relative_group"] == "一级行业:电子"
