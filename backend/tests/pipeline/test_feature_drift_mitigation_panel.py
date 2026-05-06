from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_feature_drift_mitigation_panel as subject


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
            ret_60d REAL,
            ma_ratio_60 REAL,
            stable_flow REAL,
            hs300_ret_20d REAL,
            hs300_ret_60d REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_model_selection_run (
            run_id TEXT PRIMARY KEY,
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
    conn.execute(
        """
        CREATE TABLE mart_feature_drift_root_cause_summary (
            run_id TEXT,
            source_run_id TEXT,
            feature_name TEXT,
            offender_count INTEGER,
            severe_count INTEGER,
            max_psi DOUBLE,
            recommendation TEXT,
            built_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("000001", "2026-01-01", "up", 0.01, 0.03, -0.20, 0.80, 10.0, 0.03, 0.08),
            ("000002", "2026-01-01", "flat", 0.02, 0.04, 0.00, 1.00, 20.0, 0.03, 0.08),
            ("000003", "2026-01-01", "down", -0.01, -0.02, 0.20, 1.20, 30.0, 0.03, 0.08),
            ("000001", "2026-01-02", "up", 0.01, 0.03, -0.30, 0.70, 11.0, -0.01, 0.02),
            ("000002", "2026-01-02", "flat", 0.02, 0.04, 0.10, 1.05, 21.0, -0.01, 0.02),
            ("000003", "2026-01-02", "down", -0.01, -0.02, 0.30, 1.30, 31.0, -0.01, 0.02),
        ],
    )
    conn.execute(
        """
        INSERT INTO mart_model_selection_run VALUES (
            'base_selection', 'production_registry', 'drift_safe_candidate_generator',
            'forward_ret_20d', NULL, '["ret_60d", "ma_ratio_60", "stable_flow"]',
            '[]', 0, '{}', '2026-01-03'
        )
        """
    )
    conn.executemany(
        "INSERT INTO mart_feature_drift_root_cause_summary VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "root_1",
                "stable_run",
                "ret_60d",
                5,
                2,
                0.72,
                "exclude_or_transform_before_next_large_study",
                "2026-01-03",
            ),
            (
                "root_1",
                "stable_run",
                "ma_ratio_60",
                4,
                1,
                0.44,
                "winsorize_bucket_or_regime_split",
                "2026-01-03",
            ),
        ],
    )


def test_build_feature_drift_mitigation_panel_replaces_root_cause_features():
    with duck_mem() as conn:
        _seed_inputs(conn)

        result = subject.build_feature_drift_mitigation_panel(
            conn,
            base_model_selection_run_id="base_selection",
            output_feature_set_id="mitigated_set",
            run_id="mitigation_unit",
            model_selection_run_id="mitigated_selection",
            root_cause_run_id="root_1",
            transform_types=["xs_rank", "xs_winsor", "xs_bucket5"],
            include_regime_controls=True,
            include_market_controls=True,
            market_control_features=["hs300_ret_20d"],
            winsor_low=0.25,
            winsor_high=0.75,
            start_date="2026-01-01",
            end_date="2026-01-02",
        )

        assert result["mitigated_features"] == ["ret_60d", "ma_ratio_60"]
        assert result["row_count"] == 6
        assert "stable_flow" in result["selected_features"]
        assert "ret_60d" not in result["selected_features"]
        assert result["control_features"] == ["regime_up", "regime_flat", "regime_down", "hs300_ret_20d"]
        assert "regime_up" in result["selected_features"]
        assert "hs300_ret_20d" in result["selected_features"]
        assert "ret_60d_xs_rank" in result["selected_features"]
        assert "ret_60d_xs_winsor" in result["selected_features"]
        assert "ret_60d_xs_bucket5" in result["selected_features"]

        row = conn.execute(
            """
            SELECT feature_set_id, stock_code, date, regime_flag,
                   forward_ret_5d, forward_ret_20d, stable_flow,
                   regime_up, regime_flat, regime_down, hs300_ret_20d,
                   ret_60d_xs_rank, ret_60d_xs_winsor, ret_60d_xs_bucket5,
                   ma_ratio_60_xs_rank
              FROM fact_feature_panel_candidate
             WHERE feature_set_id = 'mitigated_set'
               AND stock_code = '000001'
               AND date = '2026-01-01'
            """
        ).fetchone()
        model_run = conn.execute(
            """
            SELECT feature_set_id, method, selected_features_json,
                   rejected_features_json, notes
              FROM mart_model_selection_run
             WHERE run_id = 'mitigated_selection'
            """
        ).fetchone()
        build = conn.execute(
            """
            SELECT row_count, stock_count, transformed_features_json,
                   selected_features_json
              FROM mart_feature_drift_mitigation_panel_build
             WHERE run_id = 'mitigation_unit'
            """
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'mitigation_unit'"
        ).fetchone()

        assert row["feature_set_id"] == "mitigated_set"
        assert row["regime_flag"] == "up"
        assert row["forward_ret_20d"] == pytest.approx(0.03)
        assert row["stable_flow"] == pytest.approx(10.0)
        assert row["regime_up"] == pytest.approx(1.0)
        assert row["regime_flat"] == pytest.approx(0.0)
        assert row["regime_down"] == pytest.approx(0.0)
        assert row["hs300_ret_20d"] == pytest.approx(0.03)
        assert row["ret_60d_xs_rank"] == pytest.approx(0.0)
        assert row["ret_60d_xs_winsor"] == pytest.approx(-0.10)
        assert row["ret_60d_xs_bucket5"] == pytest.approx(1.0)
        assert row["ma_ratio_60_xs_rank"] == pytest.approx(0.0)
        assert model_run["feature_set_id"] == "mitigated_set"
        assert model_run["method"] == "feature_drift_mitigation_panel_builder"
        assert json.loads(model_run["selected_features_json"]) == result["selected_features"]
        assert json.loads(model_run["rejected_features_json"])["mitigated_original_features"] == [
            "ret_60d",
            "ma_ratio_60",
        ]
        assert json.loads(model_run["notes"])["root_cause_run_id"] == "root_1"
        assert build["row_count"] == 6
        assert build["stock_count"] == 3
        assert "ret_60d_xs_rank" in json.loads(build["selected_features_json"])
        assert json.loads(build["transformed_features_json"])["ret_60d"] == [
            "ret_60d_xs_rank",
            "ret_60d_xs_winsor",
            "ret_60d_xs_bucket5",
        ]
        perf = json.loads(manifest["perf_summary_json"])
        assert perf["mitigated_features"] == 2
        assert perf["control_features"] == 4
        assert perf["row_count"] == 6


def test_build_feature_drift_mitigation_panel_requires_matching_selected_feature():
    with duck_mem() as conn:
        _seed_inputs(conn)

        with pytest.raises(RuntimeError, match="no selected features matched"):
            subject.build_feature_drift_mitigation_panel(
                conn,
                base_model_selection_run_id="base_selection",
                output_feature_set_id="mitigated_set",
                run_id="mitigation_no_match",
                explicit_features=["not_selected"],
            )
