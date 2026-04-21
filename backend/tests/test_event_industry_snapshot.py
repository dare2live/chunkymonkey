"""
Phase 3b-1: 事件行业聚合口径迁移测试

- 原 _capture_missing_event_industry_snapshots / dim_stock_industry 快照路径已删除
  (Phase 2 申万源退役后 dim_stock_industry 被 DROP, 快照补齐函数永久失效)
- 本文件保留 SW 路径契约: _step_build_industry_stat_sync 按当前
  dim_stock_sw_industry 聚合, 写入 industry_code + industry_name (字段名仍为 tdx_code 兼容下游).
"""

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

        CREATE TABLE dim_stock_sw_industry (
            stock_code     TEXT PRIMARY KEY,
            sw_l1          TEXT,
            sw_l2          TEXT,
            sw_l3          TEXT,
            sw_l1_name     TEXT,
            sw_l2_name     TEXT,
            sw_l3_name     TEXT,
            updated_at     TEXT
        );

        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            enabled INTEGER,
            blacklisted INTEGER,
            merged_into TEXT
        );

        CREATE TABLE mart_institution_industry_stat (
            institution_id TEXT NOT NULL,
            industry_level TEXT NOT NULL,
            industry_name TEXT NOT NULL,
            tdx_code TEXT,
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
            PRIMARY KEY (institution_id, industry_level, industry_name)
        );
        """
    )
    return conn


def test_build_industry_stat_joins_dim_stock_sw_industry(monkeypatch):
    conn = _make_conn()
    try:
        monkeypatch.setattr(updater, "_raise_if_stop", lambda: None)
        conn.execute(
            "INSERT INTO inst_institutions VALUES (?, ?, ?, ?)",
            ("inst_a", 1, 0, None),
        )
        conn.executemany(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                # 两条事件同属 T12 信息产业 / T1204 计算机 / T120401 软件
                ("inst_a", "600001", "2026-03-31", "2026-04-10", 8.0, 12.0, 15.0, 20.0, -4.0, -6.0),
                ("inst_a", "600002", "2026-03-31", "2026-04-10", 4.0, -2.0, 6.0, 10.0, -5.0, -7.0),
            ],
        )
        conn.executemany(
            "INSERT INTO dim_stock_sw_industry VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("600001", "T12", "T1204", "T120401", "信息产业", "计算机", "软件", "2026-04-19T09:00"),
                ("600002", "T12", "T1204", "T120401", "信息产业", "计算机", "软件", "2026-04-19T09:00"),
            ],
        )
        conn.commit()

        written = updater._step_build_industry_stat_sync(conn)

        # 两条事件在 L1/L2/L3 各归一组 → 3 行
        assert written == 3
        rows = conn.execute(
            "SELECT industry_level, industry_name, tdx_code, sample_events, avg_gain_30d "
            "FROM mart_institution_industry_stat ORDER BY industry_level"
        ).fetchall()
        assert [(r["industry_level"], r["industry_name"], r["tdx_code"]) for r in rows] == [
            ("level1", "信息产业", "T12"),
            ("level2", "计算机", "T1204"),
            ("level3", "软件", "T120401"),
        ]
        assert all(r["sample_events"] == 2 for r in rows)
        # 两条 gain_30d 为 8.0 和 4.0, 平均 6.0
        assert all(abs(r["avg_gain_30d"] - 6.0) < 1e-6 for r in rows)
    finally:
        conn.close()


def test_build_industry_stat_skips_events_without_industry(monkeypatch):
    """dim 中没有该股票的事件应被 INNER JOIN 过滤掉, 不占样本"""
    conn = _make_conn()
    try:
        monkeypatch.setattr(updater, "_raise_if_stop", lambda: None)
        conn.execute(
            "INSERT INTO inst_institutions VALUES (?, ?, ?, ?)",
            ("inst_a", 1, 0, None),
        )
        conn.executemany(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("inst_a", "600001", "2026-03-31", "2026-04-10", 8.0, 12.0, 15.0, 20.0, -4.0, -6.0),
                # 600999 在 dim 中缺失 → 应被排除
                ("inst_a", "600999", "2026-03-31", "2026-04-10", -5.0, -6.0, -7.0, -8.0, -9.0, -10.0),
            ],
        )
        conn.execute(
            "INSERT INTO dim_stock_sw_industry VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("600001", "T10", "T1001", "T100101", "金融", "银行", "国有行", "2026-04-19T09:00"),
        )
        conn.commit()

        written = updater._step_build_industry_stat_sync(conn)

        assert written == 3
        # 只应聚合 600001 的 gain_30d = 8.0
        row = conn.execute(
            "SELECT avg_gain_30d, sample_events FROM mart_institution_industry_stat "
            "WHERE industry_level='level1'"
        ).fetchone()
        assert row["sample_events"] == 1
        assert abs(row["avg_gain_30d"] - 8.0) < 1e-6
    finally:
        conn.close()


def test_capture_function_removed():
    """Phase 3b-1: 确认 _capture_missing_event_industry_snapshots 已从 updater 删除"""
    assert not hasattr(updater, "_capture_missing_event_industry_snapshots")
