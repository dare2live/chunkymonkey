from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import run_optuna_feature_space as subject


pytestmark = pytest.mark.pipeline


def _seed_search_space(conn) -> None:
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
            rank_ic DOUBLE,
            abs_rank_ic DOUBLE,
            rank_direction INTEGER,
            coverage_pct DOUBLE,
            fold_count INTEGER,
            sign_stability DOUBLE,
            fold_valid_count INTEGER,
            fold_same_direction_rate DOUBLE,
            fold_rank_ic_std DOUBLE,
            long_short_spread DOUBLE,
            selection_role TEXT,
            selection_reason TEXT
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
            group_counts_json TEXT,
            config_json TEXT,
            built_at TEXT
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
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "protected_base", "base", 0.001, 0.001, 1, 100.0, 100, 0.5, 2, 0.5, 0.01, 0.0, "protected", "protected_baseline"),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "strong_price", "price", -0.090, 0.090, -1, 99.0, 100, 0.90, 4, 1.0, 0.02, -0.02, "candidate", "selected"),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "mid_price", "price", -0.050, 0.050, -1, 98.0, 100, 0.80, 4, 0.75, 0.03, -0.01, "candidate", "selected"),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "strong_flow", "flow", 0.070, 0.070, 1, 80.0, 100, 0.85, 4, 1.0, 0.02, 0.03, "candidate", "selected"),
        ("space_1", "assoc_1", "fact_feature_panel", "forward_ret_20d", "event_signal", "event", -0.040, 0.040, -1, 100.0, 100, 0.75, 4, 0.75, 0.01, -0.01, "candidate", "selected"),
    ]
    conn.executemany("INSERT INTO mart_feature_search_space VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    fold_rows = [
        ("assoc_1", "fold_001", "strong_price", -0.090),
        ("assoc_1", "fold_002", "strong_price", -0.080),
        ("assoc_1", "fold_003", "strong_price", -0.070),
        ("assoc_1", "fold_004", "strong_price", -0.060),
        ("assoc_1", "fold_001", "mid_price", -0.120),
        ("assoc_1", "fold_002", "mid_price", 0.020),
        ("assoc_1", "fold_003", "mid_price", -0.120),
        ("assoc_1", "fold_004", "mid_price", 0.020),
        ("assoc_1", "fold_001", "strong_flow", 0.070),
        ("assoc_1", "fold_002", "strong_flow", 0.060),
        ("assoc_1", "fold_003", "strong_flow", 0.050),
        ("assoc_1", "fold_004", "strong_flow", 0.040),
        ("assoc_1", "fold_001", "event_signal", -0.040),
        ("assoc_1", "fold_002", "event_signal", -0.035),
        ("assoc_1", "fold_003", "event_signal", -0.030),
        ("assoc_1", "fold_004", "event_signal", -0.025),
    ]
    conn.executemany("INSERT INTO mart_feature_association_fold VALUES (?, ?, ?, ?)", fold_rows)
    conn.execute(
        """
        INSERT INTO mart_feature_search_space_summary VALUES (
            'space_1', 'assoc_1', 'fact_feature_panel', 'forward_ret_20d',
            '[]', '{}', '{}', '2026-05-05'
        )
        """
    )


def _seed_rank_matrix_proxy(conn, *, gate_status: str = "pass") -> None:
    conn.execute(
        """
        CREATE TABLE mart_feature_rank_matrix_benchmark (
            run_id TEXT,
            gate_status TEXT,
            gate_blockers_json TEXT,
            gate_config_json TEXT,
            compared_pairs INTEGER,
            max_abs_rank_ic_delta DOUBLE,
            avg_abs_rank_ic_delta DOUBLE,
            matrix_duration_s DOUBLE,
            exact_run_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_feature_rank_matrix_proxy_stat (
            run_id TEXT,
            label_name TEXT,
            feature_name TEXT,
            rank_ic DOUBLE,
            long_short_spread DOUBLE,
            exact_rank_ic DOUBLE,
            abs_rank_ic_delta DOUBLE,
            daily_count INTEGER,
            valid_rank_rows BIGINT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO mart_feature_rank_matrix_benchmark VALUES
        ('rank_proxy_1', ?, ?, '{"max_abs_rank_ic_delta": 0.001}', 5, 0.0001, 0.00002, 1.2, 'assoc_1')
        """,
        [gate_status, "[]" if gate_status == "pass" else '["too_much_delta"]'],
    )
    rows = [
        ("rank_proxy_1", "forward_ret_20d", "protected_base", 0.001, 0.0, 0.001, 0.0, 4, 100),
        ("rank_proxy_1", "forward_ret_20d", "strong_price", -0.050, -0.01, -0.090, 0.00001, 4, 100),
        ("rank_proxy_1", "forward_ret_20d", "mid_price", -0.020, -0.01, -0.050, 0.00001, 4, 100),
        ("rank_proxy_1", "forward_ret_20d", "strong_flow", 0.030, 0.02, 0.070, 0.00001, 4, 100),
        ("rank_proxy_1", "forward_ret_20d", "event_signal", 0.200, 0.10, -0.040, 0.00001, 4, 100),
    ]
    conn.executemany("INSERT INTO mart_feature_rank_matrix_proxy_stat VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def test_run_optuna_feature_space_records_trials_and_selection():
    conn = duck_mem()
    try:
        _seed_search_space(conn)

        result = subject.run_optuna_feature_space(
            conn,
            search_space_run_id="space_1",
            run_id="optuna_space_unit",
            trials=8,
            max_features=3,
            seed=7,
        )
        trials = conn.execute(
            "SELECT COUNT(*) FROM mart_optuna_feature_space_trial WHERE run_id = 'optuna_space_unit'"
        ).fetchone()[0]
        model_row = conn.execute(
            "SELECT method, selected_features_json, trials FROM mart_model_selection_run WHERE run_id = 'optuna_space_unit'"
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'optuna_space_unit'"
        ).fetchone()
        selected = json.loads(model_row["selected_features_json"])

        assert result["trials"] == 8
        assert trials == 8
        assert model_row["method"] == "optuna_feature_space_proxy"
        assert model_row["trials"] == 8
        assert "protected_base" in selected
        assert len(selected) <= 3
        perf = json.loads(manifest["perf_summary_json"])
        assert perf["selected_count"] == len(selected)
        assert perf["best_subset_fold_metrics"]["subset_fold_count"] == 4.0
    finally:
        conn.close()


def test_run_optuna_feature_space_can_use_gate_passed_rank_matrix_proxy():
    conn = duck_mem()
    try:
        _seed_search_space(conn)
        _seed_rank_matrix_proxy(conn)

        result = subject.run_optuna_feature_space(
            conn,
            search_space_run_id="space_1",
            run_id="optuna_space_rank_proxy",
            trials=0,
            max_features=2,
            rank_matrix_run_id="rank_proxy_1",
        )
        model_row = conn.execute(
            """
            SELECT method, selected_features_json, notes
              FROM mart_model_selection_run
             WHERE run_id = 'optuna_space_rank_proxy'
            """
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'optuna_space_rank_proxy'"
        ).fetchone()
        selected = json.loads(model_row["selected_features_json"])
        notes = json.loads(model_row["notes"])
        perf = json.loads(manifest["perf_summary_json"])

        assert result["rank_matrix_gate_status"] == "pass"
        assert model_row["method"] == "optuna_feature_space_rank_matrix_proxy"
        assert "event_signal" in selected
        assert notes["rank_matrix_run_id"] == "rank_proxy_1"
        assert notes["rank_matrix_summary"]["gate_status"] == "pass"
        assert perf["rank_matrix_run_id"] == "rank_proxy_1"
        assert perf["rank_matrix_gate_status"] == "pass"
    finally:
        conn.close()


def test_run_optuna_feature_space_blocks_failed_rank_matrix_proxy_gate():
    conn = duck_mem()
    try:
        _seed_search_space(conn)
        _seed_rank_matrix_proxy(conn, gate_status="blocked")

        with pytest.raises(RuntimeError, match="not gate-pass"):
            subject.run_optuna_feature_space(
                conn,
                search_space_run_id="space_1",
                run_id="optuna_space_rank_proxy_blocked",
                trials=0,
                max_features=2,
                rank_matrix_run_id="rank_proxy_1",
            )
    finally:
        conn.close()


def test_subset_fold_metrics_direction_adjust_and_measure_combo_stability():
    conn = duck_mem()
    try:
        _seed_search_space(conn)
        features, summary = subject._load_search_space(conn, "space_1")
        fold_rank_ic = subject._load_fold_rank_ic_by_feature(conn, summary["source_association_run_id"])
        by_feature = {row["feature_name"]: row for row in features}

        stable = subject._subset_fold_metrics(
            [by_feature["strong_price"], by_feature["strong_flow"]],
            fold_rank_ic,
        )
        unstable = subject._subset_fold_metrics(
            [by_feature["strong_price"], by_feature["mid_price"]],
            fold_rank_ic,
        )

        assert stable["subset_fold_count"] == 4.0
        assert stable["subset_fold_mean"] > 0
        assert stable["subset_fold_std"] < unstable["subset_fold_std"]
        assert stable["subset_fold_min"] > unstable["subset_fold_min"]
    finally:
        conn.close()


def test_run_optuna_feature_space_zero_trials_uses_deterministic_baseline():
    conn = duck_mem()
    try:
        _seed_search_space(conn)

        result = subject.run_optuna_feature_space(
            conn,
            search_space_run_id="space_1",
            run_id="optuna_space_zero",
            trials=0,
            max_features=4,
        )
        trials = conn.execute(
            "SELECT COUNT(*) FROM mart_optuna_feature_space_trial WHERE run_id = 'optuna_space_zero'"
        ).fetchone()[0]

        assert result["trials"] == 0
        assert trials == 1
        assert result["selected_count"] <= 4
    finally:
        conn.close()


def test_run_optuna_feature_space_preserves_search_space_feature_set_id():
    conn = duck_mem()
    try:
        _seed_search_space(conn)
        conn.execute(
            """
            UPDATE mart_feature_search_space_summary
               SET config_json = '{"feature_set_id": "tdx_gpcw_auto_v1_pit"}'
             WHERE run_id = 'space_1'
            """
        )

        result = subject.run_optuna_feature_space(
            conn,
            search_space_run_id="space_1",
            run_id="optuna_space_candidate_set",
            trials=0,
            max_features=4,
        )
        model_row = conn.execute(
            """
            SELECT feature_set_id, notes
              FROM mart_model_selection_run
             WHERE run_id = 'optuna_space_candidate_set'
            """
        ).fetchone()
        notes = json.loads(model_row["notes"])

        assert result["feature_set_id"] == "tdx_gpcw_auto_v1_pit"
        assert model_row["feature_set_id"] == "tdx_gpcw_auto_v1_pit"
        assert notes["feature_set_id"] == "tdx_gpcw_auto_v1_pit"
    finally:
        conn.close()


def test_run_optuna_feature_space_can_resume_persistent_study(tmp_path):
    conn = duck_mem()
    try:
        _seed_search_space(conn)
        storage_url = f"sqlite:///{tmp_path / 'feature_space_study.sqlite3'}"

        first = subject.run_optuna_feature_space(
            conn,
            search_space_run_id="space_1",
            run_id="optuna_space_persist_1",
            trials=3,
            max_features=3,
            seed=7,
            storage_url=storage_url,
            study_name="space_resume",
        )
        second = subject.run_optuna_feature_space(
            conn,
            search_space_run_id="space_1",
            run_id="optuna_space_persist_2",
            trials=2,
            max_features=3,
            seed=7,
            storage_url=storage_url,
            study_name="space_resume",
        )
        second_rows = conn.execute(
            "SELECT COUNT(*) FROM mart_optuna_feature_space_trial WHERE run_id = 'optuna_space_persist_2'"
        ).fetchone()[0]

        assert first["study_name"] == "space_resume"
        assert first["study_total_trials"] == 3
        assert second["study_name"] == "space_resume"
        assert second["study_total_trials"] == 5
        assert second_rows == 2
    finally:
        conn.close()
