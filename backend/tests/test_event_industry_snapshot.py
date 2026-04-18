import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import updater


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fact_institution_event (
            institution_id TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            report_date TEXT NOT NULL,
            notice_date TEXT,
            gain_30d REAL,
            gain_60d REAL,
            gain_90d REAL,
            gain_120d REAL,
            max_drawdown_30d REAL,
            max_drawdown_60d REAL,
            PRIMARY KEY (institution_id, stock_code, report_date)
        );

        CREATE TABLE dim_stock_industry (
            stock_code TEXT PRIMARY KEY,
            sw_level1 TEXT,
            sw_level2 TEXT,
            sw_level3 TEXT,
            sw_code TEXT,
            updated_at TEXT
        );

        CREATE TABLE fact_institution_event_industry_snapshot (
            institution_id TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            report_date TEXT NOT NULL,
            notice_date TEXT,
            sw_level1 TEXT,
            sw_level2 TEXT,
            sw_level3 TEXT,
            sw_code TEXT,
            snapshot_source TEXT,
            industry_updated_at TEXT,
            captured_at TEXT,
            PRIMARY KEY (institution_id, stock_code, report_date)
        );

        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            enabled INTEGER,
            blacklisted INTEGER,
            merged_into TEXT
        );

        CREATE TABLE mart_institution_industry_stat (
            institution_id TEXT NOT NULL,
            sw_level TEXT NOT NULL,
            industry_name TEXT NOT NULL,
            sample_events INTEGER DEFAULT 0,
            avg_gain_30d REAL,
            avg_gain_60d REAL,
            avg_gain_90d REAL,
            avg_gain_120d REAL,
            win_rate_30d REAL,
            win_rate_60d REAL,
            win_rate_90d REAL,
            total_win_rate REAL,
            max_drawdown_30d REAL,
            max_drawdown_60d REAL,
            updated_at TEXT,
            PRIMARY KEY (institution_id, sw_level, industry_name)
        );
        """
    )
    return conn


def test_capture_missing_event_industry_snapshots_preserves_existing_rows():
    conn = _make_conn()
    try:
        conn.executemany(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("inst_a", "600001", "2026-03-31", "2026-04-10", 8.0, 10.0, 12.0, 15.0, -4.0, -6.0),
                ("inst_a", "600002", "2026-03-31", "2026-04-10", 5.0, 6.0, 7.0, 8.0, -2.0, -3.0),
            ],
        )
        conn.executemany(
            "INSERT INTO dim_stock_industry VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("600001", "新电子", "新半导体", "新芯片", "TDX001", "2026-04-18T09:00:00"),
                ("600002", "银行", "股份行", "全国性股份行", "TDX002", "2026-04-18T09:00:00"),
            ],
        )
        conn.execute(
            """
            INSERT INTO fact_institution_event_industry_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "inst_a",
                "600001",
                "2026-03-31",
                "2026-04-10",
                "旧电子",
                "旧半导体",
                "旧芯片",
                "OLD001",
                "legacy_snapshot",
                "2026-04-01T09:00:00",
                "2026-04-01T09:05:00",
            ),
        )
        conn.commit()

        result = updater._capture_missing_event_industry_snapshots(
            conn,
            snapshot_source="pre_sync_dim_stock_industry",
        )

        assert result == {"inserted": 1, "pending": 0, "pending_without_dim": 0}

        preserved = conn.execute(
            "SELECT sw_level1, sw_code, snapshot_source FROM fact_institution_event_industry_snapshot WHERE stock_code = '600001'"
        ).fetchone()
        assert preserved["sw_level1"] == "旧电子"
        assert preserved["sw_code"] == "OLD001"
        assert preserved["snapshot_source"] == "legacy_snapshot"

        inserted = conn.execute(
            "SELECT sw_level1, sw_level2, sw_level3, sw_code, snapshot_source FROM fact_institution_event_industry_snapshot WHERE stock_code = '600002'"
        ).fetchone()
        assert inserted["sw_level1"] == "银行"
        assert inserted["sw_level2"] == "股份行"
        assert inserted["sw_level3"] == "全国性股份行"
        assert inserted["sw_code"] == "TDX002"
        assert inserted["snapshot_source"] == "pre_sync_dim_stock_industry"
    finally:
        conn.close()


def test_build_industry_stat_uses_event_industry_snapshot_not_current_dim(monkeypatch):
    conn = _make_conn()
    try:
        monkeypatch.setattr(updater, "_raise_if_stop", lambda: None)
        conn.execute(
            "INSERT INTO inst_institutions VALUES (?, ?, ?, ?)",
            ("inst_a", 1, 0, None),
        )
        conn.execute(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("inst_a", "600001", "2026-03-31", "2026-04-10", 8.0, 12.0, 15.0, 20.0, -4.0, -6.0),
        )
        conn.execute(
            "INSERT INTO dim_stock_industry VALUES (?, ?, ?, ?, ?, ?)",
            ("600001", "新电子", "新半导体", "新芯片", "TDX001", "2026-04-18T09:00:00"),
        )
        conn.execute(
            """
            INSERT INTO fact_institution_event_industry_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "inst_a",
                "600001",
                "2026-03-31",
                "2026-04-10",
                "旧电子",
                "旧半导体",
                "旧芯片",
                "OLD001",
                "legacy_snapshot",
                "2026-04-01T09:00:00",
                "2026-04-01T09:05:00",
            ),
        )
        conn.commit()

        written = updater._step_build_industry_stat_sync(conn)

        assert written == 3
        rows = conn.execute(
            "SELECT sw_level, industry_name, avg_gain_30d FROM mart_institution_industry_stat ORDER BY sw_level"
        ).fetchall()
        assert [(row["sw_level"], row["industry_name"]) for row in rows] == [
            ("level1", "旧电子"),
            ("level2", "旧半导体"),
            ("level3", "旧芯片"),
        ]
        assert all(row["avg_gain_30d"] == 8.0 for row in rows)
    finally:
        conn.close()