import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import setup_validation


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fact_setup_snapshot (
            snapshot_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            setup_priority INTEGER,
            setup_execution_gate TEXT,
            composite_priority_score REAL,
            discovery_score REAL,
            setup_score_raw REAL,
            matured_10d INTEGER DEFAULT 0,
            matured_30d INTEGER DEFAULT 0,
            matured_60d INTEGER DEFAULT 0,
            gain_10d REAL,
            gain_30d REAL,
            gain_60d REAL,
            max_drawdown_10d REAL,
            max_drawdown_30d REAL,
            max_drawdown_60d REAL
        );

        CREATE TABLE research_setup_replay_summary (
            group_name TEXT PRIMARY KEY,
            sample_count INTEGER,
            avg_gain_10d REAL,
            avg_gain_30d REAL,
            avg_gain_60d REAL,
            avg_gain_120d REAL,
            win_rate_10d REAL,
            win_rate_30d REAL,
            win_rate_60d REAL,
            win_rate_120d REAL,
            avg_drawdown_30d REAL,
            avg_drawdown_60d REAL,
            uplift_vs_baseline_30d REAL
        );

        CREATE TABLE research_setup_replay_factor (
            factor_name TEXT,
            factor_value TEXT,
            sample_count INTEGER,
            avg_gain_30d REAL,
            avg_gain_60d REAL,
            avg_gain_120d REAL,
            win_rate_30d REAL,
            win_rate_60d REAL,
            win_rate_120d REAL,
            avg_drawdown_30d REAL,
            uplift_vs_baseline_30d REAL
        );
        """
    )
    return conn


def test_setup_validation_report_keeps_setup_as_supporting_signal_until_forward_matures(monkeypatch):
    conn = _make_conn()
    try:
        conn.executemany(
            """
            INSERT INTO fact_setup_snapshot (
                snapshot_date, stock_code, setup_priority, setup_execution_gate,
                composite_priority_score, discovery_score, setup_score_raw,
                matured_10d, matured_30d, matured_60d,
                gain_10d, gain_30d, gain_60d,
                max_drawdown_10d, max_drawdown_30d, max_drawdown_60d
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("2026-04-10", "000001", 1, "follow", 86.0, 88.0, 92.0, 0, 0, 0, None, None, None, None, None, None),
                ("2026-04-10", "000002", 2, "watch", 74.0, 70.0, 80.0, 0, 0, 0, None, None, None, None, None, None),
                ("2026-04-10", "000003", 4, "observe", 58.0, 55.0, 60.0, 0, 0, 0, None, None, None, None, None, None),
                ("2026-04-09", "000004", 5, "observe", 52.0, 49.0, 50.0, 0, 0, 0, None, None, None, None, None, None),
            ],
        )
        conn.executemany(
            """
            INSERT INTO research_setup_replay_summary (
                group_name, sample_count, avg_gain_10d, avg_gain_30d, avg_gain_60d, avg_gain_120d,
                win_rate_10d, win_rate_30d, win_rate_60d, win_rate_120d,
                avg_drawdown_30d, avg_drawdown_60d, uplift_vs_baseline_30d
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("baseline_all_buy", 120, 1.2, 3.1, 5.5, 9.0, 52.0, 54.0, 56.0, 58.0, 8.2, 12.5, 0.0),
                ("setup_hit_all", 64, 2.5, 6.8, 10.2, 15.0, 60.0, 64.0, 66.0, 70.0, 6.1, 9.4, 3.7),
            ],
        )
        conn.executemany(
            """
            INSERT INTO research_setup_replay_factor (
                factor_name, factor_value, sample_count,
                avg_gain_30d, avg_gain_60d, avg_gain_120d,
                win_rate_30d, win_rate_60d, win_rate_120d,
                avg_drawdown_30d, uplift_vs_baseline_30d
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("setup_priority", "1", 20, 8.5, 12.5, 18.0, 70.0, 72.0, 75.0, 5.0, 5.4),
                ("setup_priority", "2", 18, 7.2, 10.6, 15.8, 66.0, 68.0, 71.0, 5.6, 4.1),
                ("setup_priority", "3", 16, 5.9, 8.2, 12.0, 60.0, 62.0, 66.0, 6.4, 2.8),
                ("setup_priority", "4", 15, 2.8, 3.9, 6.2, 52.0, 53.0, 55.0, 8.1, -0.3),
                ("setup_priority", "5", 14, 1.5, 2.1, 3.8, 48.0, 49.0, 50.0, 9.0, -1.1),
                ("setup_execution_gate", "follow", 22, 6.3, 9.0, 13.0, 68.0, 70.0, 72.0, 5.4, 3.2),
                ("setup_execution_gate", "watch", 20, 7.0, 9.5, 13.8, 64.0, 66.0, 68.0, 6.0, 3.9),
                ("setup_execution_gate", "observe", 18, 3.0, 4.0, 6.0, 52.0, 54.0, 55.0, 8.4, 0.1),
            ],
        )
        conn.commit()
        monkeypatch.setattr(setup_validation, "_market_latest_trade_date", lambda: "2026-04-12")

        report = setup_validation.get_setup_validation_report(conn)

        assert report["forward"]["latest_snapshot_date"] == "2026-04-10"
        assert report["forward"]["snapshot_lag_days"] == 2
        assert report["decision"]["should_change_scoring"] is False
        assert report["decision"]["forward_ready"] is False
        assert report["replay"]["baseline"]["sample_count"] == 120
        assert report["replay"]["setup_hit"]["avg_gain_30d"] == 6.8
        assert report["forward"]["latest_gate_groups"][0]["group_value"] == "follow"
        assert any("Setup 命中整体优于全量买入基线" in text for text in report["insights"])
        assert any("前瞻快照链路已经接通" in text for text in report["insights"])
        assert any("前瞻快照尚未形成成熟后验样本" in text for text in report["decision"]["reasons"])
    finally:
        conn.close()