import asyncio
import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routers.institution as institution_router
import services.stock_validation as stock_validation


def test_get_stock_validation_report_omits_legacy_compare_payload(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        monkeypatch.setattr(stock_validation, "_load_pool_feedback", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(
            stock_validation,
            "_load_snapshot_pool_replay",
            lambda *_args, **_kwargs: {"coverage": {}, "baseline": {}, "by_pool": [], "history": []},
        )
        monkeypatch.setattr(stock_validation, "_load_anomalies", lambda *_args, **_kwargs: {"counts": {}})
        monkeypatch.setattr(
            stock_validation,
            "_load_attention_pool_linkage",
            lambda *_args, **_kwargs: {"summary": {}, "promoted_samples": [], "crowded_samples": []},
        )
        monkeypatch.setattr(
            stock_validation,
            "_load_turtle_validation",
            lambda *_args, **_kwargs: {"summary": {}, "hints": [], "state_distribution": [], "system_distribution": [], "score_bands": []},
        )
        monkeypatch.setattr(stock_validation, "_load_audit_snapshot", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(stock_validation, "_load_qlib_summary", lambda *_args, **_kwargs: {})

        report = stock_validation.get_stock_validation_report(conn)

        assert "legacy_compare" not in report
        assert "snapshot_rank_compare" not in report
        assert "overlap_top20" not in report["summary"]
        assert "overlap_top50" not in report["summary"]
        assert "overlap_top100" not in report["summary"]
        assert "snapshot_rank_matured_dates" not in report["summary"]
    finally:
        conn.close()


def test_load_pool_feedback_filters_by_sector_via_industry_context_table():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE mart_stock_trend (
            stock_code TEXT PRIMARY KEY,
            priority_pool TEXT,
            setup_tag TEXT,
            composite_cap_reason TEXT,
            external_attention_score REAL,
            external_crowding_penalty REAL,
            external_attention_signal TEXT,
            discovery_score REAL,
            company_quality_score REAL,
            stage_score REAL,
            forecast_score REAL,
            composite_priority_score REAL,
            raw_composite_priority_score REAL,
            price_20d_pct REAL
        );
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT PRIMARY KEY,
            tdx_l1 TEXT,
            tdx_l1_name TEXT
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO mart_stock_trend VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("000001", "A池", "setup", None, 80.0, 1.5, "外部确认增强", 70.0, 68.0, 60.0, 55.0, 78.0, 79.0, 6.0),
                ("000002", "B池", None, None, 40.0, 0.5, None, 58.0, 56.0, 52.0, 49.0, 61.0, 62.0, -2.0),
            ],
        )
        conn.executemany(
            "INSERT INTO dim_stock_tdx_industry VALUES (?, ?, ?)",
            [("000001", "T10", "电子"), ("000002", "T15", "银行")],
        )
        conn.commit()

        rows = stock_validation._load_pool_feedback(conn, sector="电子")

        assert len(rows) == 1
        assert rows[0]["priority_pool"] == "A池"
        assert rows[0]["total"] == 1
    finally:
        conn.close()


def test_load_snapshot_pool_replay_sector_filter_falls_back_to_tdx_industry():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fact_setup_snapshot (
            stock_code TEXT,
            snapshot_date TEXT,
            snapshot_tdx_l1_name TEXT,
            priority_pool TEXT,
            composite_priority_score REAL,
            matured_10d INTEGER,
            gain_10d REAL,
            max_drawdown_10d REAL,
            matured_30d INTEGER,
            gain_30d REAL,
            max_drawdown_30d REAL,
            matured_60d INTEGER,
            gain_60d REAL,
            max_drawdown_60d REAL
        );
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT,
            tdx_l1 TEXT,
            tdx_l1_name TEXT
        );
        """
    )
    try:
        conn.execute(
            "INSERT INTO fact_setup_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("000001", "2026-04-01", "", "A池", 82.0, 1, 3.0, -1.0, 1, 6.0, -2.0, 0, None, None),
        )
        conn.execute("INSERT INTO dim_stock_tdx_industry VALUES (?, ?, ?)", ("000001", "T10", "电子"))
        conn.commit()

        replay = stock_validation._load_snapshot_pool_replay(conn, sector="电子")

        assert replay["coverage"]["total_rows"] == 1
        assert replay["by_pool"][0]["priority_pool"] == "A池"
    finally:
        conn.close()


def test_load_anomalies_no_longer_requires_action_score_column():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE mart_stock_trend (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            priority_pool TEXT,
            stock_archetype TEXT,
            discovery_score REAL,
            company_quality_score REAL,
            stage_score REAL,
            forecast_score REAL,
            raw_composite_priority_score REAL,
            composite_priority_score REAL,
            priority_pool_reason TEXT,
            composite_cap_reason TEXT
        );
        """
    )
    try:
        conn.executemany(
            """
            INSERT INTO mart_stock_trend (
                stock_code, stock_name, priority_pool, stock_archetype,
                discovery_score, company_quality_score, stage_score, forecast_score,
                raw_composite_priority_score, composite_priority_score,
                priority_pool_reason, composite_cap_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "样本A", "B池", "成长型", 70.0, 68.0, 55.0, 52.0, 82.0, 74.0, "阶段封顶", "Stage capped"),
                ("000002", "样本B", "C池", "成长型", 58.0, 62.0, 35.0, 76.0, 61.0, 57.0, "阶段不足", None),
                ("000003", "样本C", "B池", "成长型", 63.0, 40.0, 66.0, 54.0, 64.0, 61.0, "质量不足", None),
            ],
        )
        conn.commit()

        anomalies = stock_validation._load_anomalies(conn)

        assert anomalies["capped_high_raw"]
        assert anomalies["forecast_stage_conflict"]
        assert anomalies["quality_gate_conflict"]
        assert "action_score" not in anomalies["capped_high_raw"][0]
    finally:
        conn.close()


def test_get_stock_scorecard_stats_includes_quality_source_summary(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE mart_stock_trend (
            stock_code TEXT PRIMARY KEY,
            company_quality_score_source TEXT,
            quality_feature_snapshot_date TEXT
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO mart_stock_trend (stock_code, company_quality_score_source, quality_feature_snapshot_date) VALUES (?, ?, ?)",
            [
                ("000001", "quality_feature_v1", "2024-03-31"),
                ("000002", "stock_scoring_v2", None),
                ("000003", "quality_feature_v1", "2024-06-30"),
            ],
        )
        conn.commit()

        monkeypatch.setattr(stock_validation, "_load_pool_feedback", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(
            stock_validation,
            "_load_snapshot_pool_replay",
            lambda *_args, **_kwargs: {"coverage": {}, "baseline": {}, "by_pool": []},
        )
        monkeypatch.setattr(stock_validation, "_load_archetype_distribution", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(stock_validation, "_load_qlib_summary", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(stock_validation, "_load_attention_calibration", lambda *_args, **_kwargs: {})

        stats = stock_validation.get_stock_scorecard_stats(conn)

        assert stats["quality_source_summary"] == {
            "total_stock_count": 3,
            "quality_feature_v1_count": 2,
            "stock_scoring_v2_count": 1,
            "other_source_count": 0,
            "latest_snapshot_date": "2024-06-30",
        }
    finally:
        conn.close()


def test_stock_scoring_breakdown_returns_quality_provenance(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE mart_stock_trend (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            leader_inst TEXT,
            leader_score REAL,
            consensus_count INTEGER,
            path_state TEXT,
            data_completeness REAL,
            latest_notice_date TEXT,
            discovery_score REAL,
            company_quality_score REAL,
            company_quality_score_source TEXT,
            quality_feature_snapshot_date TEXT,
            stage_score REAL,
            forecast_score REAL,
            forecast_score_effective REAL,
            raw_composite_priority_score REAL,
            composite_priority_score REAL,
            composite_cap_score REAL,
            composite_cap_reason TEXT,
            stock_archetype TEXT,
            priority_pool TEXT,
            priority_pool_reason TEXT,
            score_highlights TEXT,
            score_risks TEXT,
            setup_tag TEXT,
            setup_priority INTEGER,
            setup_reason TEXT,
            setup_confidence REAL,
            setup_level TEXT,
            setup_inst_name TEXT,
            setup_event_type TEXT,
            setup_industry_name TEXT,
            setup_score_raw REAL,
            setup_execution_gate TEXT,
            setup_execution_reason TEXT,
            industry_skill_raw REAL,
            industry_skill_grade TEXT,
            followability_grade TEXT,
            premium_grade TEXT,
            report_recency_grade TEXT,
            reliability_grade TEXT,
            crowding_bucket TEXT,
            crowding_yield_raw REAL,
            crowding_yield_grade TEXT,
            crowding_stability_raw REAL,
            crowding_stability_grade TEXT,
            crowding_fit_raw REAL,
            crowding_fit_grade TEXT,
            crowding_fit_sample INTEGER,
            crowding_fit_source TEXT,
            report_age_days INTEGER,
            turtle_execution_score REAL,
            turtle_breakout_score REAL,
            turtle_risk_score REAL,
            turtle_score_delta REAL,
            turtle_setup_state TEXT,
            turtle_preferred_system TEXT,
            turtle_reason TEXT
        );
        CREATE TABLE dim_stock_stage_latest (
            stock_code TEXT PRIMARY KEY,
            path_max_gain_pct REAL,
            path_max_drawdown_pct REAL,
            generic_stage_raw REAL,
            stage_type_adjust_raw REAL,
            stage_score_v1 REAL,
            stage_reason TEXT,
            max_drawdown_60d REAL,
            dist_ma250_pct REAL,
            above_ma250 INTEGER
        );
        CREATE TABLE dim_stock_forecast_latest (
            stock_code TEXT PRIMARY KEY,
            forecast_20d_score REAL,
            forecast_60d_excess_score REAL,
            forecast_risk_adjusted_score REAL,
            forecast_reason TEXT,
            model_id TEXT,
            predict_date TEXT,
            industry_relative_group TEXT
        );
        CREATE TABLE mart_current_relationship (
            stock_code TEXT PRIMARY KEY,
            tdx_l2 TEXT,
            notice_age_days INTEGER,
            price_entry REAL,
            return_to_now REAL,
            inst_ref_cost REAL,
            inst_cost_method TEXT,
            premium_pct REAL,
            premium_bucket TEXT,
            follow_gate TEXT
        );
        """
    )
    try:
        trend_columns = [
            "stock_code", "stock_name", "leader_inst", "leader_score", "consensus_count", "path_state",
            "data_completeness", "latest_notice_date", "discovery_score", "company_quality_score",
            "company_quality_score_source", "quality_feature_snapshot_date", "stage_score",
            "forecast_score", "forecast_score_effective", "raw_composite_priority_score",
            "composite_priority_score", "composite_cap_score", "composite_cap_reason",
            "stock_archetype", "priority_pool", "priority_pool_reason", "score_highlights",
            "score_risks", "setup_tag", "setup_priority", "setup_reason", "setup_confidence",
            "setup_level", "setup_inst_name", "setup_event_type", "setup_industry_name",
            "setup_score_raw", "setup_execution_gate", "setup_execution_reason",
            "industry_skill_raw", "industry_skill_grade", "followability_grade", "premium_grade",
            "report_recency_grade", "reliability_grade", "crowding_bucket", "crowding_yield_raw",
            "crowding_yield_grade", "crowding_stability_raw", "crowding_stability_grade",
            "crowding_fit_raw", "crowding_fit_grade", "crowding_fit_sample", "crowding_fit_source",
            "report_age_days", "turtle_execution_score", "turtle_breakout_score", "turtle_risk_score",
            "turtle_score_delta", "turtle_setup_state", "turtle_preferred_system", "turtle_reason",
        ]
        trend_values = (
            "000001", "样本A", "测试机构", 88.0, 3, "突破准备",
            92.0, "2024-04-10", 76.0, 71.5,
            "quality_feature_v1", "2024-03-31", 58.0,
            62.0, 59.0, 81.2,
            64.0, 64.0, "阶段封顶",
            "成长型", "B池", "阶段封顶", "盈利质量稳定",
            "财报覆盖仍需观察", "观察", 2, "等待突破", 0.8,
            "L2", "测试机构", "increase", "半导体",
            77.0, "watch", "等待趋势确认",
            68.0, "A", "A", "B",
            "A", "A", "中等", 3.5,
            "B", 4.2, "A", 5.1, "A", 12, "fit_v1",
            9, 54.0, 52.0, 48.0,
            6.0, "watch", "S1", "等待突破",
        )
        conn.execute(
            f"INSERT INTO mart_stock_trend ({', '.join(trend_columns)}) VALUES ({', '.join(['?'] * len(trend_columns))})",
            trend_values,
        )
        conn.execute(
            """
            INSERT INTO dim_stock_stage_latest (
                stock_code, path_max_gain_pct, path_max_drawdown_pct, generic_stage_raw,
                stage_type_adjust_raw, stage_score_v1, stage_reason, max_drawdown_60d,
                dist_ma250_pct, above_ma250
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("000001", 18.0, -7.0, 61.0, -3.0, 58.0, "阶段仍需确认", -12.0, 4.5, 1),
        )
        conn.execute(
            """
            INSERT INTO dim_stock_forecast_latest (
                stock_code, forecast_20d_score, forecast_60d_excess_score,
                forecast_risk_adjusted_score, forecast_reason, model_id, predict_date,
                industry_relative_group
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("000001", 63.0, 58.0, 55.0, "模型相对行业占优", "model-x", "2024-04-11", "前10%"),
        )
        conn.execute(
            """
            INSERT INTO mart_current_relationship (
                stock_code, tdx_l2, notice_age_days, price_entry, return_to_now,
                inst_ref_cost, inst_cost_method, premium_pct, premium_bucket, follow_gate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("000001", "半导体", 7, 12.3, 8.6, 11.8, "weighted", 4.2, "温和", "follow"),
        )
        conn.commit()

        monkeypatch.setattr(institution_router, "get_conn", lambda *args, **kwargs: conn)

        response = asyncio.run(institution_router.scoring_breakdown("stock", "000001"))

        assert response["ok"] is True
        assert response["company_quality_score_source"] == "quality_feature_v1"
        assert response["quality_feature_snapshot_date"] == "2024-03-31"
        assert response["quality_snapshot_date"] == "2024-03-31"
        assert response["raw_composite_priority_score"] == 81.2
        assert response["composite_cap_score"] == 64.0
        assert response["composite_cap_reason"] == "阶段封顶"
        assert response["priority_pool_reason"] == "阶段封顶"
        assert response["factors"]["quality"] == {
            "score": 71.5,
            "source_type": "quality_feature_v1",
            "snapshot_date": "2024-03-31",
            "source": "dim_stock_quality_latest.quality_score_v1 对齐最新财报后生效，否则回退 scoring.py stock_scoring_v2",
        }
        assert response["factors"]["price_path"]["follow_gate"] == "follow"
        assert response["factors"]["setup"]["crowding_fit_source"] == "fit_v1"
    finally:
        conn.close()