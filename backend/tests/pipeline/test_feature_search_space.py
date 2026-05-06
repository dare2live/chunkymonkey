from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_feature_search_space as subject


pytestmark = pytest.mark.pipeline


def _seed_association(conn) -> None:
    subject.ensure_tables(conn)
    conn.execute(
        """
        CREATE TABLE mart_feature_association_stat (
            run_id TEXT,
            panel_table TEXT,
            label_name TEXT,
            feature_name TEXT,
            feature_group TEXT,
            coverage_pct DOUBLE,
            rank_ic DOUBLE,
            fold_count INTEGER,
            fold_same_sign_rate DOUBLE,
            long_short_spread DOUBLE,
            built_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_feature_correlation_cluster (
            run_id TEXT,
            cluster_id TEXT,
            feature_name TEXT,
            representative_feature TEXT,
            corr_to_representative DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_feature_association_fold (
            run_id TEXT,
            feature_name TEXT,
            rank_ic DOUBLE
        )
        """
    )
    rows = [
        ("assoc_1", "fact_feature_panel", "forward_ret_20d", "strong_neg", "price", 99.0, -0.080, 100, 0.05, -0.01, "2026-05-05"),
        ("assoc_1", "fact_feature_panel", "forward_ret_20d", "dup_strong_neg", "price", 98.0, -0.075, 100, 0.05, -0.01, "2026-05-05"),
        ("assoc_1", "fact_feature_panel", "forward_ret_20d", "strong_pos", "flow", 80.0, 0.060, 100, 0.92, 0.02, "2026-05-05"),
        ("assoc_1", "fact_feature_panel", "forward_ret_20d", "low_coverage", "flow", 20.0, 0.100, 100, 0.90, 0.03, "2026-05-05"),
        ("assoc_1", "fact_feature_panel", "forward_ret_20d", "low_ic", "event", 90.0, 0.005, 100, 0.90, 0.01, "2026-05-05"),
        ("assoc_1", "fact_feature_panel", "forward_ret_20d", "protected_weak", "base", 50.0, 0.001, 1, 0.50, 0.0, "2026-05-05"),
    ]
    conn.executemany("INSERT INTO mart_feature_association_stat VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.executemany(
        "INSERT INTO mart_feature_correlation_cluster VALUES (?, ?, ?, ?, ?)",
        [
            ("assoc_1", "cluster_001", "strong_neg", "strong_neg", 1.0),
            ("assoc_1", "cluster_001", "dup_strong_neg", "strong_neg", 0.99),
            ("assoc_1", "cluster_002", "strong_pos", "strong_pos", 1.0),
            ("assoc_1", "cluster_003", "low_coverage", "low_coverage", 1.0),
            ("assoc_1", "cluster_004", "low_ic", "low_ic", 1.0),
            ("assoc_1", "cluster_005", "protected_weak", "protected_weak", 1.0),
        ],
    )
    conn.executemany(
        "INSERT INTO mart_feature_association_fold VALUES (?, ?, ?)",
        [
            ("assoc_1", "strong_neg", -0.080),
            ("assoc_1", "strong_neg", -0.070),
            ("assoc_1", "strong_pos", 0.060),
            ("assoc_1", "strong_pos", -0.010),
            ("assoc_1", "dup_strong_neg", -0.070),
            ("assoc_1", "protected_weak", 0.001),
        ],
    )


def test_build_feature_search_space_prefilters_by_evidence_and_clusters():
    conn = duck_mem()
    try:
        _seed_association(conn)

        result = subject.build_feature_search_space(
            conn,
            association_run_id="assoc_1",
            run_id="space_1",
            min_abs_rank_ic=0.02,
            min_coverage_pct=60.0,
            min_fold_count=20,
            min_sign_stability=0.70,
            max_features=3,
            protected_features=["protected_weak"],
        )
        rows = conn.execute(
            """
            SELECT feature_name, selection_role, selection_reason, rank_direction,
                   sign_stability, fold_valid_count, fold_same_direction_rate,
                   fold_rank_ic_std, representative_feature
              FROM mart_feature_search_space
             WHERE run_id = 'space_1'
            """
        ).fetchall()
        by_feature = {row["feature_name"]: row for row in rows}
        summary = conn.execute(
            "SELECT selected_features_json, rejected_features_json FROM mart_feature_search_space_summary WHERE run_id = 'space_1'"
        ).fetchone()
        model_run = conn.execute(
            "SELECT method, selected_features_json FROM mart_model_selection_run WHERE run_id = 'space_1'"
        ).fetchone()

        assert result["selected_count"] == 3
        assert by_feature["protected_weak"]["selection_role"] == "protected"
        assert by_feature["strong_neg"]["selection_role"] == "candidate"
        assert by_feature["strong_neg"]["rank_direction"] == -1
        assert by_feature["strong_neg"]["sign_stability"] == pytest.approx(0.95)
        assert by_feature["strong_neg"]["fold_valid_count"] == 2
        assert by_feature["strong_neg"]["fold_same_direction_rate"] == pytest.approx(1.0)
        assert by_feature["strong_neg"]["fold_rank_ic_std"] == pytest.approx(0.007071, rel=1e-3)
        assert by_feature["strong_pos"]["fold_same_direction_rate"] == pytest.approx(0.5)
        assert by_feature["dup_strong_neg"]["selection_reason"] == "cluster_redundant:strong_neg"
        assert by_feature["low_coverage"]["selection_reason"].startswith("low_coverage")
        assert by_feature["low_ic"]["selection_reason"].startswith("low_abs_rank_ic")
        assert json.loads(summary["selected_features_json"]) == result["selected_features"]
        assert json.loads(summary["rejected_features_json"])["dup_strong_neg"] == "cluster_redundant:strong_neg"
        assert model_run["method"] == "association_cluster_prefilter"
        assert json.loads(model_run["selected_features_json"]) == result["selected_features"]
    finally:
        conn.close()


def test_feature_search_space_budget_excludes_lower_rank_candidates():
    conn = duck_mem()
    try:
        _seed_association(conn)

        result = subject.build_feature_search_space(
            conn,
            association_run_id="assoc_1",
            run_id="space_budget",
            min_abs_rank_ic=0.02,
            min_coverage_pct=60.0,
            min_fold_count=20,
            min_sign_stability=0.70,
            max_features=1,
        )
        roles = {
            row["feature_name"]: row["selection_reason"]
            for row in conn.execute(
                "SELECT feature_name, selection_reason FROM mart_feature_search_space WHERE run_id = 'space_budget'"
            ).fetchall()
        }

        assert result["selected_count"] == 1
        assert "strong_neg" in result["selected_features"]
        assert roles["strong_pos"] == "feature_budget"
    finally:
        conn.close()


def test_feature_search_space_can_filter_fold_level_instability():
    conn = duck_mem()
    try:
        _seed_association(conn)

        by_direction = subject.build_feature_search_space(
            conn,
            association_run_id="assoc_1",
            run_id="space_fold_direction",
            min_abs_rank_ic=0.02,
            min_coverage_pct=60.0,
            min_fold_count=20,
            min_sign_stability=0.70,
            min_fold_same_direction_rate=0.75,
            max_features=3,
        )
        direction_rows = {
            row["feature_name"]: row["selection_reason"]
            for row in conn.execute(
                """
                SELECT feature_name, selection_reason
                  FROM mart_feature_search_space
                 WHERE run_id = 'space_fold_direction'
                """
            ).fetchall()
        }

        assert "strong_neg" in by_direction["selected_features"]
        assert direction_rows["strong_pos"].startswith("low_fold_same_direction_rate")

        by_std = subject.build_feature_search_space(
            conn,
            association_run_id="assoc_1",
            run_id="space_fold_std",
            min_abs_rank_ic=0.02,
            min_coverage_pct=60.0,
            min_fold_count=20,
            min_sign_stability=0.70,
            max_fold_rank_ic_std=0.01,
            max_features=3,
        )
        std_rows = {
            row["feature_name"]: row["selection_reason"]
            for row in conn.execute(
                """
                SELECT feature_name, selection_reason
                  FROM mart_feature_search_space
                 WHERE run_id = 'space_fold_std'
                """
            ).fetchall()
        }

        assert "strong_neg" in by_std["selected_features"]
        assert std_rows["strong_pos"].startswith("high_fold_rank_ic_std")
        assert std_rows["dup_strong_neg"] == "missing_fold_rank_ic_std"
    finally:
        conn.close()


def test_feature_search_space_can_override_coverage_from_production_panel():
    conn = duck_mem()
    try:
        _seed_association(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                strong_neg DOUBLE,
                strong_pos DOUBLE,
                dup_strong_neg DOUBLE,
                low_coverage DOUBLE,
                low_ic DOUBLE,
                protected_weak DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('000001', '2026-01-01', 1, 1, 1, 1, 1, 1),
            ('000002', '2026-01-01', 1, NULL, 1, NULL, 1, 1)
            """
        )

        result = subject.build_feature_search_space(
            conn,
            association_run_id="assoc_1",
            run_id="space_coverage_override",
            min_abs_rank_ic=0.02,
            min_coverage_pct=60.0,
            min_fold_count=20,
            min_sign_stability=0.70,
            max_features=3,
            coverage_table="fact_feature_panel",
        )
        strong_pos = conn.execute(
            """
            SELECT coverage_pct, selection_role, selection_reason
            FROM mart_feature_search_space
            WHERE run_id = 'space_coverage_override'
              AND feature_name = 'strong_pos'
            """
        ).fetchone()
        summary = conn.execute(
            "SELECT config_json FROM mart_feature_search_space_summary WHERE run_id = 'space_coverage_override'"
        ).fetchone()

        assert strong_pos["coverage_pct"] == pytest.approx(50.0)
        assert strong_pos["selection_role"] == "excluded"
        assert strong_pos["selection_reason"].startswith("low_coverage")
        assert "strong_pos" not in result["selected_features"]
        assert json.loads(summary["config_json"])["coverage_override"] is True
    finally:
        conn.close()


def test_feature_search_space_records_custom_feature_set_id():
    conn = duck_mem()
    try:
        _seed_association(conn)

        result = subject.build_feature_search_space(
            conn,
            association_run_id="assoc_1",
            run_id="space_candidate_panel",
            feature_set_id="tdx_gpcw_auto_v1_pit",
            min_abs_rank_ic=0.02,
            min_coverage_pct=60.0,
            min_fold_count=20,
            min_sign_stability=0.70,
            max_features=2,
            auto_coverage_table=False,
        )
        model_run = conn.execute(
            """
            SELECT feature_set_id, selected_features_json, notes
              FROM mart_model_selection_run
             WHERE run_id = 'space_candidate_panel'
            """
        ).fetchone()
        summary = conn.execute(
            """
            SELECT config_json
              FROM mart_feature_search_space_summary
             WHERE run_id = 'space_candidate_panel'
            """
        ).fetchone()

        assert model_run["feature_set_id"] == "tdx_gpcw_auto_v1_pit"
        assert json.loads(model_run["selected_features_json"]) == result["selected_features"]
        assert json.loads(summary["config_json"])["feature_set_id"] == "tdx_gpcw_auto_v1_pit"
        assert json.loads(model_run["notes"])["config"]["feature_set_id"] == "tdx_gpcw_auto_v1_pit"
    finally:
        conn.close()


def test_feature_search_space_uses_panel_coverage_override_by_default():
    conn = duck_mem()
    try:
        _seed_association(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                strong_neg DOUBLE,
                strong_pos DOUBLE,
                dup_strong_neg DOUBLE,
                low_coverage DOUBLE,
                low_ic DOUBLE,
                protected_weak DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('000001', '2026-01-01', 1, 1, 1, 1, 1, 1),
            ('000002', '2026-01-01', 1, NULL, 1, NULL, 1, 1)
            """
        )

        result = subject.build_feature_search_space(
            conn,
            association_run_id="assoc_1",
            run_id="space_default_coverage_override",
            min_abs_rank_ic=0.02,
            min_coverage_pct=60.0,
            min_fold_count=20,
            min_sign_stability=0.70,
            max_features=3,
        )
        strong_pos = conn.execute(
            """
            SELECT coverage_pct, selection_role, selection_reason
              FROM mart_feature_search_space
             WHERE run_id = 'space_default_coverage_override'
               AND feature_name = 'strong_pos'
            """
        ).fetchone()
        summary = conn.execute(
            """
            SELECT config_json
              FROM mart_feature_search_space_summary
             WHERE run_id = 'space_default_coverage_override'
            """
        ).fetchone()
        config = json.loads(summary["config_json"])

        assert strong_pos["coverage_pct"] == pytest.approx(50.0)
        assert strong_pos["selection_role"] == "excluded"
        assert strong_pos["selection_reason"].startswith("low_coverage")
        assert "strong_pos" not in result["selected_features"]
        assert config["coverage_table"] == "fact_feature_panel"
        assert config["auto_coverage_table"] is True
        assert config["coverage_override"] is True
    finally:
        conn.close()
