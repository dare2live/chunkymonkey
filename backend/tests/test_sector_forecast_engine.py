import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.qlib_full_engine import ensure_tables as ensure_qlib_tables
from services.sector_forecast_engine import (
    build_sector_forecast_features,
    ensure_tables as ensure_sector_forecast_tables,
    get_latest_sector_forecast_snapshot,
)
from services.stock_forecast_engine import ensure_tables as ensure_stock_forecast_tables


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_qlib_tables(conn)
    ensure_stock_forecast_tables(conn)
    ensure_sector_forecast_tables(conn)
    conn.executescript(
        """
        CREATE TABLE mart_sector_momentum (
            sector_name TEXT PRIMARY KEY,
            rotation_score REAL,
            rotation_rank INTEGER,
            rotation_rank_1m INTEGER,
            rotation_rank_3m INTEGER,
            rotation_bucket TEXT,
            trend_state TEXT,
            momentum_score REAL
        );
        """
    )
    return conn


def _seed_sector_momentum(conn):
    conn.executemany(
        """
        INSERT INTO mart_sector_momentum (
            sector_name, rotation_score, rotation_rank, rotation_rank_1m,
            rotation_rank_3m, rotation_bucket, trend_state, momentum_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("电子", 78.0, 1, 1, 3, "leader", "recovering", 72.0),
            ("银行", 61.0, 4, 4, 6, "neutral", "bullish", 68.0),
            ("计算机", 45.0, 9, 10, 8, "blacklist", "weakening", 41.0),
        ],
    )


def _seed_forecast_rows(conn, model_id: str, snapshot_date: str, sector_name: str, base_score: float):
    for idx in range(5):
        conn.execute(
            """
            INSERT INTO dim_stock_forecast_latest (
                stock_code, snapshot_date, model_id, predict_date, stock_name,
                sw_level1, sw_level2, qlib_score, qlib_rank, qlib_percentile,
                industry_qlib_percentile, industry_relative_group,
                volatility_20d, max_drawdown_60d, volatility_rank, drawdown_rank,
                forecast_20d_score, forecast_60d_excess_score,
                forecast_risk_adjusted_score, forecast_score_v1, forecast_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{sector_name[:1]}{idx:05d}",
                snapshot_date,
                model_id,
                snapshot_date,
                f"{sector_name}样本{idx}",
                sector_name,
                f"{sector_name}二级",
                0.9 - idx * 0.03,
                idx + 1,
                base_score - idx,
                base_score - idx * 0.8,
                f"一级行业:{sector_name}",
                10 + idx,
                5 + idx,
                70 - idx,
                75 - idx,
                base_score - idx,
                (base_score - 8) - idx,
                (base_score - 4) - idx,
                (base_score - 5) - idx,
                "测试样本",
                f"2026-04-15T10:0{idx}:00",
            ),
        )


def test_build_sector_forecast_features_prefers_active_model_over_latest_trained():
    conn = _make_conn()
    try:
        conn.executemany(
            "INSERT INTO qlib_model_state (model_id, status, created_at, is_active) VALUES (?, ?, ?, ?)",
            [
                ("model_active", "trained", "2026-04-13T10:00:00", 1),
                ("model_latest", "trained", "2026-04-15T10:00:00", 0),
            ],
        )
        _seed_sector_momentum(conn)
        _seed_forecast_rows(conn, "model_active", "2026-04-13", "电子", 86.0)
        _seed_forecast_rows(conn, "model_active", "2026-04-13", "银行", 72.0)
        conn.commit()

        inserted = build_sector_forecast_features(conn)

        assert inserted == 2
        rows = conn.execute(
            "SELECT sector_name, model_id, snapshot_date FROM dim_sector_forecast_latest ORDER BY next_rotation_score DESC"
        ).fetchall()
        assert len(rows) == 2
        assert {row["model_id"] for row in rows} == {"model_active"}
        assert {row["snapshot_date"] for row in rows} == {"2026-04-13"}
        assert rows[0]["sector_name"] == "电子"
    finally:
        conn.close()


def test_get_latest_sector_forecast_snapshot_rebuilds_when_latest_table_is_stale():
    conn = _make_conn()
    try:
        conn.execute(
            "INSERT INTO qlib_model_state (model_id, status, created_at, is_active) VALUES (?, ?, ?, ?)",
            ("model_active", "trained", "2026-04-13T10:00:00", 1),
        )
        _seed_sector_momentum(conn)
        _seed_forecast_rows(conn, "model_active", "2026-04-13", "电子", 84.0)
        _seed_forecast_rows(conn, "model_active", "2026-04-13", "银行", 70.0)
        conn.execute(
            """
            INSERT INTO dim_sector_forecast_latest (
                sector_name, snapshot_date, model_id, stock_count,
                next_rotation_score, next_rotation_label, next_rotation_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("旧行业", "2026-04-12", "stale_model", 5, 55.0, "继续观察", "旧快照", "2026-04-12T10:00:00"),
        )
        conn.commit()

        rows = get_latest_sector_forecast_snapshot(conn, limit=5, auto_build=True)

        assert len(rows) == 2
        assert {row["model_id"] for row in rows} == {"model_active"}
        assert {row["snapshot_date"] for row in rows} == {"2026-04-13"}
        latest_rows = conn.execute(
            "SELECT sector_name, model_id, snapshot_date FROM dim_sector_forecast_latest ORDER BY next_rotation_score DESC"
        ).fetchall()
        assert {row["sector_name"] for row in latest_rows} == {"电子", "银行"}
    finally:
        conn.close()