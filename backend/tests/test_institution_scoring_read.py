import asyncio
import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routers.institution as institution_router
import services.scoring as scoring
import services.institution_scoring_read as institution_scoring_read


def _make_scorecard_stats_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE mart_institution_profile (
            institution_id TEXT PRIMARY KEY,
            inst_type TEXT,
            quality_score REAL,
            followability_score REAL,
            score_basis TEXT,
            score_confidence TEXT,
            followability_confidence TEXT,
            safe_follow_event_count INTEGER,
            avg_premium_pct REAL,
            buy_event_count INTEGER,
            followability_hint TEXT
        );
        """
    )
    return conn


def _make_breakdown_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE mart_institution_profile (
            institution_id TEXT PRIMARY KEY,
            quality_score REAL,
            total_events INTEGER,
            followability_score REAL,
            followability_confidence TEXT,
            buy_event_count INTEGER,
            buy_avg_gain_30d REAL,
            buy_avg_gain_60d REAL,
            buy_avg_gain_120d REAL,
            buy_win_rate_30d REAL,
            buy_win_rate_60d REAL,
            buy_win_rate_120d REAL,
            buy_median_max_drawdown_30d REAL,
            median_gain_30d REAL,
            avg_gain_30d REAL,
            avg_gain_60d REAL,
            avg_gain_120d REAL,
            win_rate_30d REAL,
            win_rate_60d REAL,
            win_rate_90d REAL,
            median_max_drawdown_30d REAL,
            avg_premium_pct REAL,
            safe_follow_event_count INTEGER,
            safe_follow_win_rate_30d REAL,
            safe_follow_avg_gain_30d REAL,
            safe_follow_avg_drawdown_30d REAL,
            premium_discount_event_count INTEGER,
            premium_discount_win_rate_30d REAL,
            premium_near_cost_event_count INTEGER,
            premium_near_cost_win_rate_30d REAL,
            premium_premium_event_count INTEGER,
            premium_premium_win_rate_30d REAL,
            premium_high_event_count INTEGER,
            premium_high_win_rate_30d REAL,
            signal_transfer_efficiency_30d REAL,
            followability_hint TEXT,
            score_basis TEXT,
            score_confidence TEXT,
            main_industry_1 TEXT,
            best_industry_1 TEXT,
            concentration REAL,
            data_completeness REAL
        );
        """
    )
    return conn


def test_load_institution_scorecard_stats_summarizes_distribution():
    conn = _make_scorecard_stats_conn()
    try:
        conn.executemany(
            "INSERT INTO mart_institution_profile VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("inst_a", "fund", 72.0, 68.0, "buy", "high", "high", 5, 1.2, 10, "样本充足"),
                ("inst_b", "fund", 58.0, 40.0, "fallback_all", "medium", "low", 0, 4.8, 0, "样本偏少"),
                ("inst_c", None, 66.0, 71.0, "buy", "high", "medium", 3, 2.0, 8, None),
            ],
        )
        conn.commit()

        payload = institution_scoring_read.load_institution_scorecard_stats(conn)

        assert payload["summary"]["total"] == 3
        assert payload["summary"]["buy_basis_count"] == 2
        assert payload["summary"]["fallback_basis_count"] == 1
        assert payload["summary"]["quality_high_conf_count"] == 2
        assert payload["summary"]["follow_high_conf_count"] == 1
        assert payload["summary"]["safe_follow_inst_count"] == 2
        assert payload["type_top"][0]["inst_type"] == "fund"
        assert payload["type_top"][0]["total"] == 2
        assert payload["hint_top"][0]["followability_hint"] == "未标注"
        assert payload["confidence"]["quality"][0]["confidence"] == "high"
    finally:
        conn.close()


def test_build_institution_scoring_breakdown_payload_uses_buy_sample_weights():
    payload = institution_scoring_read.build_institution_scoring_breakdown_payload(
        {
            "institution_id": "inst_a",
            "quality_score": 76.0,
            "followability_score": 63.0,
            "followability_confidence": "high",
            "buy_event_count": 9,
            "buy_avg_gain_30d": 11.4,
            "buy_avg_gain_60d": 13.5,
            "buy_avg_gain_120d": 16.2,
            "buy_win_rate_30d": 66.0,
            "buy_win_rate_60d": 70.0,
            "buy_win_rate_120d": 72.0,
            "buy_median_max_drawdown_30d": 7.5,
            "total_events": 12,
            "avg_gain_30d": 8.0,
            "avg_gain_60d": 9.0,
            "avg_gain_120d": 10.0,
            "win_rate_30d": 55.0,
            "win_rate_60d": 57.0,
            "win_rate_90d": 59.0,
            "median_max_drawdown_30d": 9.0,
            "avg_premium_pct": 2.2,
            "safe_follow_event_count": 6,
            "safe_follow_win_rate_30d": 62.0,
            "safe_follow_avg_gain_30d": 8.8,
            "safe_follow_avg_drawdown_30d": 5.1,
            "premium_discount_event_count": 2,
            "premium_discount_win_rate_30d": 68.0,
            "premium_near_cost_event_count": 3,
            "premium_near_cost_win_rate_30d": 64.0,
            "premium_premium_event_count": 1,
            "premium_premium_win_rate_30d": 40.0,
            "premium_high_event_count": 0,
            "premium_high_win_rate_30d": None,
            "signal_transfer_efficiency_30d": 75.0,
            "followability_hint": "样本充足",
            "score_basis": "buy",
            "score_confidence": "medium",
            "main_industry_1": "电子",
            "best_industry_1": "半导体",
            "concentration": 28.5,
            "data_completeness": 91.0,
        },
        config={"sample_weight": 15, "gain_30d_weight": 12, "stability_weight": 6},
    )

    assert payload["object_id"] == "inst_a"
    assert payload["confidence_factor"] == 0.949
    assert payload["factors"][0]["label"] == "买入事件数"
    assert payload["factors"][0]["weight"] == 15
    assert payload["factors"][1]["raw_value"] == 11.4
    assert payload["industry"]["best_industry"] == "半导体"
    assert payload["followability"]["signal_transfer_efficiency_30d"] == 75.0


def test_load_institution_scoring_breakdown_queries_shared_payload(monkeypatch):
    conn = _make_breakdown_conn()
    try:
        conn.execute(
            """
            INSERT INTO mart_institution_profile VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "inst_a", 74.0, 14, 61.0, "high", 9, 11.0, 13.0, 15.0,
                67.0, 69.0, 72.0, 6.5, 10.0, 8.5, 9.0, 11.0,
                55.0, 58.0, 60.0, 8.0, 2.5, 5, 63.0, 8.2, 4.1,
                2, 70.0, 3, 66.0, 1, 40.0, 0, None, 78.0,
                "样本充足", "buy", "high", "电子", "半导体", 26.0, 88.0,
            ),
        )
        conn.commit()
        monkeypatch.setattr(
            institution_scoring_read,
            "load_scoring_config",
            lambda _conn, _prefix: {"gain_60d_weight": 11, "drawdown_weight": 5},
        )

        payload = institution_scoring_read.load_institution_scoring_breakdown(conn, "inst_a")

        assert payload["ok"] is True
        assert payload["quality_score"] == 74.0
        assert payload["factors"][2]["weight"] == 11
        assert payload["factors"][7]["weight"] == 5
        assert payload["followability"]["premium_near_cost_event_count"] == 3
    finally:
        conn.close()


def test_institution_scoring_breakdown_route_uses_shared_service(monkeypatch):
    conn = _make_breakdown_conn()
    try:
        conn.execute(
            """
            INSERT INTO mart_institution_profile VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "inst_route", 71.0, 9, 58.0, "medium", 4, 7.0, 8.0, 9.5,
                60.0, 61.0, 62.0, 8.2, 6.8, 6.0, 6.5, 7.0,
                52.0, 53.0, 54.0, 9.2, 3.1, 2, 55.0, 6.2, 5.4,
                1, 65.0, 1, 60.0, 0, None, 0, None, 64.0,
                "样本偏少", "buy", "medium", "机械设备", "自动化设备", 18.0, 80.0,
            ),
        )
        conn.commit()
        monkeypatch.setattr(institution_router, "get_conn", lambda *args, **kwargs: conn)
        monkeypatch.setattr(
            institution_scoring_read,
            "load_scoring_config",
            lambda _conn, _prefix: {"sample_weight": 7},
        )

        response = asyncio.run(institution_router.scoring_breakdown("institution", "inst_route"))

        assert response["ok"] is True
        assert response["card_type"] == "institution"
        assert response["object_id"] == "inst_route"
        assert response["factors"][0]["weight"] == 7
        assert response["industry"]["main_industry"] == "机械设备"
    finally:
        conn.close()


def test_delete_scoring_config_removes_only_requested_prefix():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE app_settings (
            key TEXT,
            value TEXT,
            updated_at TEXT
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO app_settings VALUES (?, ?, ?)",
            [
                ("scoring.stock.alpha", "1", "2026-04-16T10:00:00"),
                ("scoring.stock.beta", "2", "2026-04-16T10:00:00"),
                ("scoring.institution.alpha", "3", "2026-04-16T10:00:00"),
            ],
        )
        conn.commit()

        scoring.delete_scoring_config(conn, "scoring.stock")

        rows = conn.execute("SELECT key FROM app_settings ORDER BY key").fetchall()
        assert [row["key"] for row in rows] == ["scoring.institution.alpha"]
    finally:
        conn.close()