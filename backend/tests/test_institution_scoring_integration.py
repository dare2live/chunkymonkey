import math
import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.scoring import calculate_institution_scores


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );

        CREATE TABLE mart_institution_profile (
            institution_id TEXT PRIMARY KEY,
            total_events INTEGER,
            avg_gain_30d REAL,
            avg_gain_60d REAL,
            avg_gain_120d REAL,
            win_rate_30d REAL,
            win_rate_60d REAL,
            win_rate_90d REAL,
            median_gain_30d REAL,
            median_max_drawdown_30d REAL,
            buy_event_count INTEGER,
            buy_avg_gain_30d REAL,
            buy_avg_gain_60d REAL,
            buy_avg_gain_120d REAL,
            buy_win_rate_30d REAL,
            buy_win_rate_60d REAL,
            buy_win_rate_120d REAL,
            buy_median_max_drawdown_30d REAL,
            avg_premium_pct REAL,
            safe_follow_event_count INTEGER,
            safe_follow_win_rate_30d REAL,
            safe_follow_avg_gain_30d REAL,
            safe_follow_avg_drawdown_30d REAL,
            signal_transfer_efficiency_30d REAL,
            quality_score REAL,
            followability_score REAL,
            score_basis TEXT,
            score_confidence TEXT,
            followability_confidence TEXT,
            main_industry_1 TEXT,
            main_industry_2 TEXT,
            main_industry_3 TEXT,
            best_industry_1 TEXT,
            best_industry_2 TEXT,
            best_industry_3 TEXT,
            concentration REAL,
            updated_at TEXT
        );

        CREATE TABLE mart_current_relationship (
            institution_id TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            sw_level2 TEXT
        );

        CREATE TABLE mart_institution_industry_stat (
            institution_id TEXT NOT NULL,
            sw_level TEXT NOT NULL,
            industry_name TEXT NOT NULL,
            sample_events INTEGER,
            avg_gain_30d REAL,
            win_rate_30d REAL,
            max_drawdown_30d REAL,
            PRIMARY KEY (institution_id, sw_level, industry_name)
        );
        """
    )
    return conn


def _insert_profile(conn, institution_id, **values):
    payload = {
        "institution_id": institution_id,
        "total_events": values.get("total_events", 0),
        "avg_gain_30d": values.get("avg_gain_30d"),
        "avg_gain_60d": values.get("avg_gain_60d"),
        "avg_gain_120d": values.get("avg_gain_120d"),
        "win_rate_30d": values.get("win_rate_30d"),
        "win_rate_60d": values.get("win_rate_60d"),
        "win_rate_90d": values.get("win_rate_90d"),
        "median_gain_30d": values.get("median_gain_30d"),
        "median_max_drawdown_30d": values.get("median_max_drawdown_30d"),
        "buy_event_count": values.get("buy_event_count"),
        "buy_avg_gain_30d": values.get("buy_avg_gain_30d"),
        "buy_avg_gain_60d": values.get("buy_avg_gain_60d"),
        "buy_avg_gain_120d": values.get("buy_avg_gain_120d"),
        "buy_win_rate_30d": values.get("buy_win_rate_30d"),
        "buy_win_rate_60d": values.get("buy_win_rate_60d"),
        "buy_win_rate_120d": values.get("buy_win_rate_120d"),
        "buy_median_max_drawdown_30d": values.get("buy_median_max_drawdown_30d"),
        "avg_premium_pct": values.get("avg_premium_pct", 0.0),
        "safe_follow_event_count": values.get("safe_follow_event_count", 0),
        "safe_follow_win_rate_30d": values.get("safe_follow_win_rate_30d", 0.0),
        "safe_follow_avg_gain_30d": values.get("safe_follow_avg_gain_30d", 0.0),
        "safe_follow_avg_drawdown_30d": values.get("safe_follow_avg_drawdown_30d", 0.0),
        "signal_transfer_efficiency_30d": values.get("signal_transfer_efficiency_30d", 0.0),
    }
    conn.execute(
        """
        INSERT INTO mart_institution_profile (
            institution_id,
            total_events,
            avg_gain_30d,
            avg_gain_60d,
            avg_gain_120d,
            win_rate_30d,
            win_rate_60d,
            win_rate_90d,
            median_gain_30d,
            median_max_drawdown_30d,
            buy_event_count,
            buy_avg_gain_30d,
            buy_avg_gain_60d,
            buy_avg_gain_120d,
            buy_win_rate_30d,
            buy_win_rate_60d,
            buy_win_rate_120d,
            buy_median_max_drawdown_30d,
            avg_premium_pct,
            safe_follow_event_count,
            safe_follow_win_rate_30d,
            safe_follow_avg_gain_30d,
            safe_follow_avg_drawdown_30d,
            signal_transfer_efficiency_30d
        ) VALUES (
            :institution_id,
            :total_events,
            :avg_gain_30d,
            :avg_gain_60d,
            :avg_gain_120d,
            :win_rate_30d,
            :win_rate_60d,
            :win_rate_90d,
            :median_gain_30d,
            :median_max_drawdown_30d,
            :buy_event_count,
            :buy_avg_gain_30d,
            :buy_avg_gain_60d,
            :buy_avg_gain_120d,
            :buy_win_rate_30d,
            :buy_win_rate_60d,
            :buy_win_rate_120d,
            :buy_median_max_drawdown_30d,
            :avg_premium_pct,
            :safe_follow_event_count,
            :safe_follow_win_rate_30d,
            :safe_follow_avg_gain_30d,
            :safe_follow_avg_drawdown_30d,
            :signal_transfer_efficiency_30d
        )
        """,
        payload,
    )


def test_buy_sample_confidence_dampens_small_sample_and_fills_industries():
    conn = _make_conn()
    try:
        _insert_profile(
            conn,
            "elite_small",
            total_events=3,
            buy_event_count=3,
            buy_avg_gain_30d=25,
            buy_avg_gain_60d=24,
            buy_avg_gain_120d=22,
            buy_win_rate_30d=82,
            buy_win_rate_60d=80,
            buy_win_rate_120d=78,
            median_gain_30d=23,
            buy_median_max_drawdown_30d=5,
            safe_follow_event_count=2,
            safe_follow_win_rate_30d=70,
            safe_follow_avg_gain_30d=16,
            safe_follow_avg_drawdown_30d=6,
            signal_transfer_efficiency_30d=75,
            avg_premium_pct=1.0,
        )
        _insert_profile(
            conn,
            "solid_large",
            total_events=16,
            buy_event_count=16,
            buy_avg_gain_30d=15,
            buy_avg_gain_60d=14,
            buy_avg_gain_120d=13,
            buy_win_rate_30d=64,
            buy_win_rate_60d=63,
            buy_win_rate_120d=62,
            median_gain_30d=14,
            buy_median_max_drawdown_30d=7,
            safe_follow_event_count=12,
            safe_follow_win_rate_30d=62,
            safe_follow_avg_gain_30d=10,
            safe_follow_avg_drawdown_30d=8,
            signal_transfer_efficiency_30d=60,
            avg_premium_pct=3.0,
        )
        _insert_profile(
            conn,
            "mid_large",
            total_events=12,
            buy_event_count=12,
            buy_avg_gain_30d=8,
            buy_avg_gain_60d=7,
            buy_avg_gain_120d=6,
            buy_win_rate_30d=50,
            buy_win_rate_60d=48,
            buy_win_rate_120d=47,
            median_gain_30d=6,
            buy_median_max_drawdown_30d=11,
            safe_follow_event_count=8,
            safe_follow_win_rate_30d=52,
            safe_follow_avg_gain_30d=7,
            safe_follow_avg_drawdown_30d=10,
            signal_transfer_efficiency_30d=48,
            avg_premium_pct=5.0,
        )
        _insert_profile(
            conn,
            "weak_large",
            total_events=18,
            buy_event_count=18,
            buy_avg_gain_30d=1,
            buy_avg_gain_60d=0,
            buy_avg_gain_120d=-2,
            buy_win_rate_30d=35,
            buy_win_rate_60d=33,
            buy_win_rate_120d=30,
            median_gain_30d=-1,
            buy_median_max_drawdown_30d=20,
            safe_follow_event_count=10,
            safe_follow_win_rate_30d=38,
            safe_follow_avg_gain_30d=1,
            safe_follow_avg_drawdown_30d=16,
            signal_transfer_efficiency_30d=20,
            avg_premium_pct=12.0,
        )

        conn.executemany(
            "INSERT INTO mart_current_relationship (institution_id, stock_code, sw_level2) VALUES (?, ?, ?)",
            [
                ("elite_small", "000001", "半导体"),
                ("elite_small", "000002", "半导体"),
                ("elite_small", "000003", "半导体"),
                ("solid_large", "600001", "金融"),
                ("solid_large", "600002", "金融"),
                ("mid_large", "300001", "医药"),
                ("weak_large", "002001", "地产"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO mart_institution_industry_stat (
                institution_id, sw_level, industry_name, sample_events,
                avg_gain_30d, win_rate_30d, max_drawdown_30d
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("elite_small", "level2", "半导体", 6, 26, 81, 5),
                ("solid_large", "level2", "金融", 12, 16, 62, 7),
                ("mid_large", "level2", "医药", 8, 9, 51, 11),
                ("weak_large", "level2", "地产", 9, 0, 34, 20),
            ],
        )
        conn.commit()

        scored = calculate_institution_scores(conn)

        assert scored == 4
        rows = {
            row["institution_id"]: row
            for row in conn.execute(
                "SELECT * FROM mart_institution_profile ORDER BY institution_id"
            ).fetchall()
        }

        elite = rows["elite_small"]
        solid = rows["solid_large"]
        weak = rows["weak_large"]

        assert elite["quality_score"] > weak["quality_score"]
        assert solid["quality_score"] > elite["quality_score"]
        assert elite["score_basis"] == "buy"
        assert elite["score_confidence"] == "medium"
        assert solid["score_confidence"] == "high"
        assert elite["followability_confidence"] == "low"
        assert solid["followability_confidence"] == "high"
        assert elite["main_industry_1"] == "半导体"
        assert elite["best_industry_1"] == "半导体"
        assert elite["concentration"] == 100.0
        assert elite["quality_score"] < 55.0
        assert elite["quality_score"] == round(
            elite["quality_score"] / min(1.0, math.sqrt(3 / 10.0))
            * min(1.0, math.sqrt(3 / 10.0)),
            2,
        )
    finally:
        conn.close()


def test_falls_back_to_all_event_metrics_when_buy_data_missing():
    conn = _make_conn()
    try:
        _insert_profile(
            conn,
            "fallback_a",
            total_events=20,
            avg_gain_30d=12,
            avg_gain_60d=10,
            avg_gain_120d=9,
            win_rate_30d=62,
            win_rate_60d=60,
            win_rate_90d=58,
            median_gain_30d=11,
            median_max_drawdown_30d=8,
            safe_follow_event_count=6,
            safe_follow_win_rate_30d=55,
            safe_follow_avg_gain_30d=8,
            safe_follow_avg_drawdown_30d=9,
            signal_transfer_efficiency_30d=50,
            avg_premium_pct=4,
        )
        _insert_profile(
            conn,
            "fallback_b",
            total_events=10,
            avg_gain_30d=3,
            avg_gain_60d=2,
            avg_gain_120d=1,
            win_rate_30d=42,
            win_rate_60d=40,
            win_rate_90d=39,
            median_gain_30d=2,
            median_max_drawdown_30d=16,
            safe_follow_event_count=2,
            safe_follow_win_rate_30d=42,
            safe_follow_avg_gain_30d=2,
            safe_follow_avg_drawdown_30d=15,
            signal_transfer_efficiency_30d=20,
            avg_premium_pct=10,
        )
        conn.commit()

        scored = calculate_institution_scores(conn)

        assert scored == 2
        rows = {
            row["institution_id"]: row
            for row in conn.execute(
                "SELECT institution_id, quality_score, score_basis FROM mart_institution_profile"
            ).fetchall()
        }
        assert rows["fallback_a"]["score_basis"] == "fallback_all"
        assert rows["fallback_b"]["score_basis"] == "fallback_all"
        assert rows["fallback_a"]["quality_score"] > rows["fallback_b"]["quality_score"]
    finally:
        conn.close()