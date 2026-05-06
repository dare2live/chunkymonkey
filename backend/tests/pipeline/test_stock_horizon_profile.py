from __future__ import annotations

import pytest

from conftest import duck_mem
from scripts import build_stock_horizon_profile as subject


pytestmark = pytest.mark.pipeline


def test_stock_horizon_profile_selects_best_horizon_and_feature_effects():
    conn = duck_mem()
    try:
        subject.ensure_tables(conn)
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
        conn.execute(
            """
            INSERT INTO mart_model_selection_run VALUES (
                'selection_horizon', 'profile_set', 'unit',
                'forward_ret_60d', 1.0, '["f_good", "f_bad"]',
                '[]', 0, '{}', '2026-05-06'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                forward_ret_5d DOUBLE,
                forward_ret_60d DOUBLE,
                forward_ret_90d DOUBLE,
                f_good DOUBLE,
                f_bad DOUBLE
            )
            """
        )
        rows = []
        for idx in range(30):
            rows.append(
                (
                    "profile_set",
                    "000001",
                    f"2026-01-{idx + 1:02d}",
                    0.001,
                    0.010,
                    0.020 + idx * 0.002,
                    float(idx),
                    float(30 - idx),
                )
            )
        conn.executemany(
            "INSERT INTO fact_feature_panel_candidate VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

        result = subject.build_stock_horizon_profile(
            conn,
            run_id="stock_horizon_unit",
            feature_table="fact_feature_panel_candidate",
            feature_set_id="profile_set",
            model_selection_run_id="selection_horizon",
            labels=["forward_ret_5d", "forward_ret_60d", "forward_ret_90d"],
            min_observations=10,
            top_features_per_stock=0,
        )

        best = conn.execute(
            """
            SELECT stock_code, label_name, horizon_days, is_best,
                   compounded_return, max_drawdown, path_obs_count
              FROM mart_stock_horizon_profile
             WHERE run_id = 'stock_horizon_unit'
               AND stock_code = '000001'
               AND is_best
            """
        ).fetchone()
        effect = conn.execute(
            """
            SELECT feature_name, label_name, corr, abs_corr_rank, effect_direction
              FROM mart_stock_horizon_feature_effect
             WHERE run_id = 'stock_horizon_unit'
               AND stock_code = '000001'
               AND label_name = 'forward_ret_90d'
             ORDER BY abs_corr_rank
             LIMIT 1
            """
        ).fetchone()
        selection = conn.execute(
            """
            SELECT baseline_label, selected_label, selected_horizon_days,
                   gate_status, selected_horizon_confidence
              FROM mart_stock_horizon_selection
             WHERE run_id = 'stock_horizon_unit'
               AND stock_code = '000001'
            """
        ).fetchone()

        assert result["profile_count"] == 3
        assert result["best_count"] == 1
        assert result["effect_count"] == 2
        assert result["selection_count"] == 1
        assert result["selected_non_baseline_count"] == 1
        assert best["label_name"] == "forward_ret_90d"
        assert best["horizon_days"] == 90
        assert best["compounded_return"] > 0
        assert best["max_drawdown"] == pytest.approx(0.0)
        assert best["path_obs_count"] == 1
        assert effect["feature_name"] == "f_good"
        assert effect["abs_corr_rank"] == 1
        assert effect["effect_direction"] == "positive"
        assert effect["corr"] == pytest.approx(1.0)
        assert selection["baseline_label"] == "forward_ret_60d"
        assert selection["selected_label"] == "forward_ret_90d"
        assert selection["selected_horizon_days"] == 90
        assert selection["gate_status"] == "selected"
        assert selection["selected_horizon_confidence"] >= 0.55
    finally:
        conn.close()


def test_stock_horizon_selection_falls_back_to_60d_when_candidate_drawdown_fails():
    conn = duck_mem()
    try:
        subject.ensure_tables(conn)
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
        conn.execute(
            """
            INSERT INTO mart_model_selection_run VALUES (
                'selection_horizon', 'profile_set', 'unit',
                'forward_ret_60d', 1.0, '["f_good"]',
                '[]', 0, '{}', '2026-05-06'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                forward_ret_60d DOUBLE,
                forward_ret_90d DOUBLE,
                f_good DOUBLE
            )
            """
        )
        rows = []
        for idx in range(30):
            candidate = 0.03
            if idx % 3 == 1:
                candidate = -0.50
            rows.append(
                (
                    "profile_set",
                    "000001",
                    f"2026-01-{idx + 1:02d}",
                    0.01,
                    candidate,
                    float(idx),
                )
            )
        conn.executemany(
            "INSERT INTO fact_feature_panel_candidate VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )

        subject.build_stock_horizon_profile(
            conn,
            run_id="stock_horizon_fallback",
            feature_table="fact_feature_panel_candidate",
            feature_set_id="profile_set",
            model_selection_run_id="selection_horizon",
            labels=["forward_ret_60d", "forward_ret_90d"],
            min_observations=10,
            max_candidate_drawdown=0.20,
        )

        selection = conn.execute(
            """
            SELECT selected_label, selected_horizon_days, gate_status, fallback_reason
              FROM mart_stock_horizon_selection
             WHERE run_id = 'stock_horizon_fallback'
               AND stock_code = '000001'
            """
        ).fetchone()

        assert selection["selected_label"] == "forward_ret_60d"
        assert selection["selected_horizon_days"] == 60
        assert selection["gate_status"] == "baseline"
        assert selection["fallback_reason"] == "baseline_best_or_no_candidate_passed"
    finally:
        conn.close()
