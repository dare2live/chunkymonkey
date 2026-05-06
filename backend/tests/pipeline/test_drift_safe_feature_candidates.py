from __future__ import annotations

import json

from conftest import duck_mem
from scripts import build_drift_safe_feature_candidates as subject


def _seed_candidate_inputs(conn) -> None:
    subject.ensure_tables(conn)
    conn.execute(
        """
        CREATE OR REPLACE TABLE mart_feature_search_space (
            run_id TEXT,
            source_association_run_id TEXT,
            panel_table TEXT,
            label_name TEXT,
            feature_name TEXT,
            feature_group TEXT,
            selection_role TEXT,
            selection_reason TEXT,
            rank_ic DOUBLE,
            abs_rank_ic DOUBLE,
            rank_direction INTEGER,
            coverage_pct DOUBLE,
            fold_count INTEGER,
            sign_stability DOUBLE,
            fold_valid_count INTEGER,
            fold_same_direction_rate DOUBLE,
            fold_rank_ic_std DOUBLE,
            long_short_spread DOUBLE
        )
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TABLE mart_feature_search_space_summary (
            run_id TEXT,
            source_association_run_id TEXT,
            panel_table TEXT,
            label_name TEXT,
            selected_features_json TEXT,
            config_json TEXT,
            built_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TABLE mart_model_lifecycle (
            model_id TEXT,
            status TEXT,
            deployed_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TABLE mart_feature_drift (
            snapshot_at TIMESTAMP,
            model_id TEXT,
            feature TEXT,
            psi DOUBLE,
            n_train INTEGER,
            n_recent INTEGER,
            window_days INTEGER,
            severity TEXT,
            notes TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TABLE mart_model_stability_search_trial (
            run_id TEXT,
            trial_number INTEGER,
            fold_metrics_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TABLE mart_feature_association_fold (
            run_id TEXT,
            fold_id TEXT,
            feature_name TEXT,
            rank_ic DOUBLE
        )
        """
    )
    rows = [
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "protected_base", "base", "protected", "baseline", 0.001, 0.001, 1, 20.0, 1, 0.50, 1, 0.50, 0.0, 0.0),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "stable_price", "price", "candidate", "selected", -0.080, 0.080, -1, 99.0, 100, 0.90, 4, 1.00, 0.01, -0.02),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "stable_flow", "flow", "candidate", "selected", 0.070, 0.070, 1, 95.0, 100, 0.85, 4, 0.90, 0.02, 0.03),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "stable_alpha", "alpha", "candidate", "selected", -0.060, 0.060, -1, 98.0, 100, 0.80, 4, 0.80, 0.02, -0.01),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "stable_event", "event", "candidate", "selected", 0.050, 0.050, 1, 100.0, 100, 0.70, 4, 0.75, 0.01, 0.02),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "latest_critical", "price", "candidate", "selected", -0.100, 0.100, -1, 99.0, 100, 0.90, 4, 1.00, 0.01, -0.02),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "historical_drift", "price", "candidate", "selected", -0.095, 0.095, -1, 99.0, 100, 0.90, 4, 1.00, 0.01, -0.02),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "low_coverage", "flow", "candidate", "selected", 0.200, 0.200, 1, 20.0, 100, 0.90, 4, 1.00, 0.01, 0.05),
    ]
    conn.executemany(
        """
        INSERT INTO mart_feature_search_space VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.execute(
        """
        INSERT INTO mart_feature_search_space_summary VALUES (
            'space_1', 'assoc_1', 'fact_feature_panel', 'forward_ret_20d',
            '[]', '{}', '2026-05-06'
        )
        """
    )
    conn.execute("INSERT INTO mart_model_lifecycle VALUES ('champion_1', 'champion', '2026-05-06', '2026-05-06')")
    conn.executemany(
        "INSERT INTO mart_feature_drift VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("2026-05-06", "champion_1", "latest_critical", 0.40, 100, 100, 30, "critical", ""),
            ("2026-05-06", "champion_1", "stable_price", 0.02, 100, 100, 30, "ok", ""),
            ("2026-05-06", "champion_1", "stable_flow", 0.03, 100, 100, 30, "ok", ""),
        ],
    )
    fold_metrics = [
        {
            "fold_id": 1,
            "feature_drift_psi_by_feature": {
                "historical_drift": 0.60,
                "stable_price": 0.03,
            },
        }
    ]
    conn.execute(
        "INSERT INTO mart_model_stability_search_trial VALUES (?, ?, ?)",
        ("hist_1", 0, json.dumps(fold_metrics)),
    )
    conn.executemany(
        "INSERT INTO mart_feature_association_fold VALUES (?, ?, ?, ?)",
        [
            ("assoc_1", "fold_1", "stable_price", -0.080),
            ("assoc_1", "fold_2", "stable_price", -0.070),
            ("assoc_1", "fold_3", "stable_price", -0.060),
            ("assoc_1", "fold_1", "stable_flow", 0.060),
            ("assoc_1", "fold_2", "stable_flow", 0.055),
            ("assoc_1", "fold_3", "stable_flow", 0.050),
            ("assoc_1", "fold_1", "stable_alpha", -0.050),
            ("assoc_1", "fold_2", "stable_alpha", 0.020),
            ("assoc_1", "fold_3", "stable_alpha", -0.050),
            ("assoc_1", "fold_1", "stable_event", 0.040),
            ("assoc_1", "fold_2", "stable_event", 0.035),
            ("assoc_1", "fold_3", "stable_event", 0.030),
        ],
    )


def test_build_drift_safe_candidates_excludes_latest_and_historical_drift():
    conn = duck_mem()
    try:
        _seed_candidate_inputs(conn)

        result = subject.build_drift_safe_feature_candidates(
            conn,
            search_space_run_id="space_1",
            run_id="drift_candidates_unit",
            historical_run_ids=["hist_1"],
            max_features=4,
            compact_features=3,
            min_features=3,
        )

        assert result["generated_count"] >= 1
        assert result["fold_rank_ic_features"] == 4
        assert any(candidate_id.endswith("_fold_stable") for candidate_id in result["candidate_ids"])
        assert result["excluded_features"]["latest_critical"].startswith("latest_drift_severity")
        assert result["excluded_features"]["historical_drift"].startswith("historical_drift_psi")
        assert result["excluded_features"]["low_coverage"].startswith("low_coverage")
        for features in result["selected_features_by_candidate"].values():
            assert "latest_critical" not in features
            assert "historical_drift" not in features
            assert "low_coverage" not in features
            assert "protected_base" in features

        model_rows = conn.execute(
            """
            SELECT run_id, method, selected_features_json, rejected_features_json
              FROM mart_model_selection_run
             WHERE method = 'drift_safe_candidate_generator'
             ORDER BY run_id
            """
        ).fetchall()
        feature_rows = conn.execute(
            """
            SELECT COUNT(*)
              FROM mart_drift_safe_candidate_feature
             WHERE run_id = 'drift_candidates_unit'
            """
        ).fetchone()[0]
        summary = conn.execute(
            """
            SELECT generated_count, excluded_features_json
              FROM mart_drift_safe_candidate_summary
             WHERE run_id = 'drift_candidates_unit'
            """
        ).fetchone()
        manifest = conn.execute(
            """
            SELECT perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'drift_candidates_unit'
            """
        ).fetchone()

        assert len(model_rows) == result["generated_count"]
        assert feature_rows >= result["generated_count"] * 3
        assert summary["generated_count"] == result["generated_count"]
        assert "historical_drift" in json.loads(summary["excluded_features_json"])
        assert json.loads(manifest["perf_summary_json"])["generated_count"] == result["generated_count"]
        assert json.loads(manifest["perf_summary_json"])["fold_rank_ic_features"] == 4
        assert json.loads(model_rows[0]["rejected_features_json"])["excluded"]["latest_critical"].startswith(
            "latest_drift"
        )
        fold_stable = conn.execute(
            """
            SELECT notes
              FROM mart_model_selection_run
             WHERE run_id = 'drift_candidates_unit_fold_stable'
            """
        ).fetchone()
        assert json.loads(fold_stable["notes"])["subset_fold_metrics"]["fold_count"] == 3.0
    finally:
        conn.close()


def test_latest_critical_drift_overrides_protected_role():
    conn = duck_mem()
    try:
        _seed_candidate_inputs(conn)
        conn.execute(
            """
            UPDATE mart_feature_search_space
               SET selection_role = 'protected'
             WHERE feature_name = 'latest_critical'
            """
        )

        result = subject.build_drift_safe_feature_candidates(
            conn,
            search_space_run_id="space_1",
            run_id="drift_candidates_protected_unit",
            historical_run_ids=["hist_1"],
            max_features=4,
            compact_features=3,
            min_features=3,
        )

        assert result["excluded_features"]["latest_critical"].startswith("latest_drift_severity")
        for features in result["selected_features_by_candidate"].values():
            assert "latest_critical" not in features
    finally:
        conn.close()
