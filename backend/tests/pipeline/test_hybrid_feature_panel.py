from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_hybrid_feature_panel as subject


pytestmark = pytest.mark.pipeline


def _seed_inputs(conn) -> None:
    conn.execute(
        """
        CREATE TABLE fact_feature_panel (
            stock_code TEXT,
            date TEXT,
            regime_flag TEXT,
            forward_ret_5d REAL,
            forward_ret_20d REAL,
            base_a REAL,
            base_b REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_feature_panel_candidate (
            feature_set_id TEXT,
            stock_code TEXT,
            date TEXT,
            forward_ret_20d REAL,
            extra_x REAL,
            extra_y REAL,
            built_at TEXT,
            PRIMARY KEY (feature_set_id, stock_code, date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_model_selection_run (
            run_id TEXT,
            feature_set_id TEXT,
            method TEXT,
            label_name TEXT,
            objective_score DOUBLE,
            selected_features_json TEXT,
            rejected_features_json TEXT,
            trials INTEGER,
            notes TEXT,
            built_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("000001", "2026-01-01", "up", 0.01, 0.03, 1.0, 10.0),
            ("000002", "2026-01-01", "down", -0.01, -0.02, 2.0, 20.0),
        ],
    )
    conn.executemany(
        "INSERT INTO fact_feature_panel_candidate VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("extra_set", "000001", "2026-01-01", 0.03, 100.0, 1000.0, "2026-01-02"),
            ("extra_set", "000002", "2026-01-01", -0.02, 200.0, 2000.0, "2026-01-02"),
            ("other_set", "000001", "2026-01-01", 0.03, 999.0, 9999.0, "2026-01-02"),
        ],
    )
    conn.executemany(
        "INSERT INTO mart_model_selection_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "base_run",
                "production_registry",
                "drift_safe_candidate_generator",
                "forward_ret_20d",
                None,
                '["base_a", "base_b"]',
                "[]",
                0,
                "{}",
                "2026-01-02",
            ),
            (
                "extra_run",
                "extra_set",
                "optuna_feature_space_proxy",
                "forward_ret_20d",
                None,
                '["extra_x"]',
                "[]",
                0,
                "{}",
                "2026-01-02",
            ),
        ],
    )


def test_build_hybrid_feature_panel_writes_joined_panel_and_model_selection_run():
    conn = duck_mem()
    try:
        _seed_inputs(conn)

        result = subject.build_hybrid_feature_panel(
            conn,
            base_model_selection_run_id="base_run",
            extra_model_selection_run_id="extra_run",
            output_feature_set_id="hybrid_set",
            run_id="hybrid_build_unit",
            model_selection_run_id="hybrid_selection_unit",
            start_date="2026-01-01",
            end_date="2026-01-01",
        )
        row = conn.execute(
            """
            SELECT feature_set_id, stock_code, date, regime_flag,
                   forward_ret_5d, forward_ret_20d, base_a, base_b,
                   extra_x
              FROM fact_feature_panel_candidate
             WHERE feature_set_id = 'hybrid_set'
               AND stock_code = '000001'
            """
        ).fetchone()
        model_run = conn.execute(
            """
            SELECT feature_set_id, method, selected_features_json, notes
              FROM mart_model_selection_run
             WHERE run_id = 'hybrid_selection_unit'
            """
        ).fetchone()
        build = conn.execute(
            """
            SELECT row_count, stock_count, model_selection_run_id,
                   selected_features_json
              FROM mart_hybrid_feature_panel_build
             WHERE run_id = 'hybrid_build_unit'
            """
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'hybrid_build_unit'"
        ).fetchone()

        assert result["row_count"] == 2
        assert result["selected_features"] == ["base_a", "base_b", "extra_x"]
        assert row["regime_flag"] == "up"
        assert row["forward_ret_5d"] == pytest.approx(0.01)
        assert row["forward_ret_20d"] == pytest.approx(0.03)
        assert row["base_a"] == pytest.approx(1.0)
        assert row["base_b"] == pytest.approx(10.0)
        assert row["extra_x"] == pytest.approx(100.0)
        assert model_run["feature_set_id"] == "hybrid_set"
        assert model_run["method"] == "hybrid_feature_panel_builder"
        assert json.loads(model_run["selected_features_json"]) == ["base_a", "base_b", "extra_x"]
        assert json.loads(model_run["notes"])["extra_feature_set_id"] == "extra_set"
        assert build["row_count"] == 2
        assert build["stock_count"] == 2
        assert build["model_selection_run_id"] == "hybrid_selection_unit"
        assert json.loads(build["selected_features_json"]) == ["base_a", "base_b", "extra_x"]
        perf = json.loads(manifest["perf_summary_json"])
        assert perf["output_feature_set_id"] == "hybrid_set"
        assert perf["selected_features"] == 3
    finally:
        conn.close()


def test_build_hybrid_feature_panel_rejects_overlapping_feature_names():
    conn = duck_mem()
    try:
        _seed_inputs(conn)
        conn.execute(
            """
            UPDATE mart_model_selection_run
               SET selected_features_json = '["base_a", "extra_x"]'
             WHERE run_id = 'extra_run'
            """
        )
        conn.execute("ALTER TABLE fact_feature_panel_candidate ADD COLUMN base_a REAL")

        with pytest.raises(RuntimeError, match="overlap"):
            subject.build_hybrid_feature_panel(
                conn,
                base_model_selection_run_id="base_run",
                extra_model_selection_run_id="extra_run",
                output_feature_set_id="hybrid_bad",
                run_id="hybrid_bad_unit",
            )
    finally:
        conn.close()
