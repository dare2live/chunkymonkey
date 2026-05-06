import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts import train_multidim_model as subject


def test_load_panel_returns_records_and_regime_features():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                regime_flag TEXT,
                forward_ret_20d REAL,
                ret_1d REAL,
                ret_5d REAL
            );
            INSERT INTO fact_feature_panel VALUES
                ('000001', '2026-01-01', 'up', 0.03, 0.01, 0.02),
                ('000002', '2026-01-01', 'down', NULL, 0.02, 0.03),
                ('000003', '2026-01-02', 'flat', -0.01, -0.01, 0.00);
            """
        )

        rows = subject.load_panel(
            conn,
            "2026-01-01",
            "2026-01-02",
            with_alpha158=False,
        )

        assert len(rows) == 2
        assert rows[0]["label_value"] == pytest.approx(0.03)
        assert rows[0]["regime_up"] == 1
        assert rows[0]["regime_down"] == 0
        assert rows[1]["regime_flat"] == 1
    finally:
        conn.close()


def test_load_panel_arrays_returns_numpy_columns_and_matrix():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                regime_flag TEXT,
                forward_ret_20d REAL,
                ret_1d REAL,
                ret_5d REAL
            );
            INSERT INTO fact_feature_panel VALUES
                ('000002', '2026-01-02', 'flat', -0.01, NULL, 0.00),
                ('000001', '2026-01-01', 'up', 0.03, 0.01, 0.02),
                ('000003', '2026-01-01', 'down', NULL, 0.02, 0.03);
            """
        )

        panel = subject.load_panel_arrays(
            conn,
            "2026-01-01",
            "2026-01-02",
            with_alpha158=False,
        )

        assert panel.row_count == 2
        assert panel.stock_codes.tolist() == ["000001", "000002"]
        assert panel.labels.dtype == np.float32
        assert panel.features["ret_1d"].dtype == np.float32
        assert panel.features["ret_1d"].tolist() == pytest.approx([0.01, 0.0])
        assert panel.features["regime_up"].tolist() == pytest.approx([1.0, 0.0])

        matrix = panel.matrix(["ret_1d", "ret_5d", "regime_flat"])

        assert matrix.dtype == np.float32
        np.testing.assert_allclose(matrix, np.array([[0.01, 0.02, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32))
    finally:
        conn.close()


def test_load_panel_arrays_accepts_requested_feature_columns():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                regime_flag TEXT,
                forward_ret_20d REAL,
                ret_1d REAL,
                custom_signal REAL
            );
            INSERT INTO fact_feature_panel VALUES
                ('000001', '2026-01-01', 'up', 0.03, 0.01, 0.70),
                ('000002', '2026-01-01', 'down', -0.01, NULL, 0.20);
            """
        )

        panel = subject.load_panel_arrays(
            conn,
            "2026-01-01",
            "2026-01-01",
            with_alpha158=False,
            requested_feature_cols=["custom_signal"],
        )

        assert "custom_signal" in panel.features
        assert panel.features["custom_signal"].tolist() == pytest.approx([0.70, 0.20])
    finally:
        conn.close()


def test_load_panel_arrays_can_limit_to_requested_feature_columns():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                regime_flag TEXT,
                forward_ret_20d REAL,
                ret_1d REAL,
                ret_5d REAL,
                custom_signal REAL
            );
            INSERT INTO fact_feature_panel VALUES
                ('000001', '2026-01-01', 'up', 0.03, 0.01, 0.02, 0.70);
            """
        )

        panel = subject.load_panel_arrays(
            conn,
            "2026-01-01",
            "2026-01-01",
            with_alpha158=False,
            requested_feature_cols=["custom_signal"],
            only_requested_feature_cols=True,
        )

        assert set(panel.features) == {"custom_signal", "regime_up", "regime_flat", "regime_down"}
    finally:
        conn.close()


def test_load_panel_arrays_handles_candidate_panel_without_regime_flag():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                forward_ret_20d REAL,
                custom_signal REAL
            );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('set_a', '000001', '2026-01-01', 0.03, 0.70),
                ('set_b', '000002', '2026-01-01', -0.01, 0.20);
            """
        )

        panel = subject.load_panel_arrays(
            conn,
            "2026-01-01",
            "2026-01-01",
            with_alpha158=False,
            feature_table="fact_feature_panel_candidate",
            feature_set_id="set_a",
            requested_feature_cols=["custom_signal"],
            only_requested_feature_cols=True,
        )

        assert panel.row_count == 1
        assert panel.stock_codes.tolist() == ["000001"]
        assert panel.features["custom_signal"].tolist() == pytest.approx([0.70])
        assert panel.features["regime_up"].tolist() == pytest.approx([0.0])
        assert panel.features["regime_flat"].tolist() == pytest.approx([0.0])
        assert panel.features["regime_down"].tolist() == pytest.approx([0.0])
    finally:
        conn.close()


def test_load_model_selection_run_returns_selected_features():
    conn = duck_mem()
    try:
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
                'selection_1', 'production_registry', 'optuna_feature_space_proxy',
                'forward_ret_20d', 1.23, '["custom_signal", "ret_1d", "custom_signal"]',
                '[]', 8, '{}', '2026-05-05'
            )
            """
        )

        row = subject.load_model_selection_run(conn, "selection_1")

        assert row["method"] == "optuna_feature_space_proxy"
        assert row["selected_features"] == ["custom_signal", "ret_1d"]
        assert row["label_name"] == "forward_ret_20d"
    finally:
        conn.close()


def test_load_model_stability_search_run_returns_best_params():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE mart_model_stability_search_summary (
                run_id TEXT,
                model_selection_run_id TEXT,
                feature_table TEXT,
                feature_set_id TEXT,
                label_name TEXT,
                selected_features_json TEXT,
                best_trial_number INTEGER,
                best_params_json TEXT,
                objective_score DOUBLE,
                trials INTEGER,
                study_name TEXT,
                study_total_trials INTEGER,
                config_json TEXT,
                built_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_model_stability_search_summary VALUES (
                'stable_1', 'selection_1', 'fact_feature_panel', NULL,
                'forward_ret_20d', '["ret_20d"]', 3,
                '{"num_leaves": 31, "min_data_in_leaf": 1000}',
                0.12, 8, 'study_1', 8, '{}', '2026-05-06'
            )
            """
        )

        row = subject.load_model_stability_search_run(conn, "stable_1")

        assert row["model_selection_run_id"] == "selection_1"
        assert row["selected_features"] == ["ret_20d"]
        assert row["best_params"]["num_leaves"] == 31
        assert row["best_trial_number"] == 3
    finally:
        conn.close()


def test_resolve_feature_group_uses_record_columns():
    rows = [
        {
            "ret_1d": 0.01,
            "ret_5d": 0.02,
            "ret_20d_rank": 0.9,
            "regime_up": 1,
            "a158_alpha": 0.5,
        }
    ]

    cols, tag = subject.resolve_feature_group("base_dense_v2_alpha158", rows, regime_aware=True)

    assert cols == ["ret_1d", "ret_5d", "ret_20d_rank", "a158_alpha", "regime_up"]
    assert tag == "m7_base_dense_v2_alpha158_v1_regime"


def test_alpha158_is_only_required_by_alpha_feature_groups():
    assert subject.feature_group_uses_alpha158("base") is False
    assert subject.feature_group_uses_alpha158("base_dense_v2") is False
    assert subject.feature_group_uses_alpha158("tdx_keep_v1") is False
    assert subject.feature_group_uses_alpha158("model_selection_run") is False
    assert subject.feature_group_uses_alpha158("base_alpha158") is True
    assert subject.feature_group_uses_alpha158("base_dense_v2_alpha158") is True
    assert subject.feature_group_uses_alpha158("legacy_full") is True


def test_resolve_model_selection_feature_group_preserves_selection_order():
    cols, tag = subject.resolve_feature_group_from_columns(
        "model_selection_run",
        {"ret_60d", "ma_ratio_60", "regime_up"},
        regime_aware=True,
        selection_features=["ma_ratio_60", "ret_60d"],
        selection_schema_tag="model_selection_selection_1",
    )

    assert cols == ["ma_ratio_60", "ret_60d", "regime_up"]
    assert tag == "model_selection_selection_1_regime"


def test_resolve_model_selection_feature_group_rejects_missing_features():
    with pytest.raises(RuntimeError, match="缺少 selected 特征"):
        subject.resolve_feature_group_from_columns(
            "model_selection_run",
            {"ret_60d"},
            regime_aware=False,
            selection_features=["ma_ratio_60", "ret_60d"],
        )


def test_apply_production_feature_contract_filters_source_gap_and_auxiliary_features():
    cols, excluded = subject.apply_production_feature_contract(
        [
            "ret_60d",
            "ret_60d_rank",
            "inst_count_qoq",
            "lhb_inst_buy_count_30d",
            "rz_balance",
            "hs300_ret_20d",
            "hs300_ret_60d",
            "regime_up",
        ],
        feature_group="base_dense_v2",
        strict=False,
    )

    assert cols == ["ret_60d", "ret_60d_rank", "regime_up"]
    reasons = {row["feature_name"]: row["reason"] for row in excluded}
    assert reasons["inst_count_qoq"] == "not_production_ready"
    assert reasons["lhb_inst_buy_count_30d"] == "not_model_input"
    assert reasons["rz_balance"] == "not_production_ready"
    assert reasons["hs300_ret_20d"] == "not_model_input"
    assert reasons["hs300_ret_60d"] == "not_model_input"


def test_apply_production_feature_contract_blocks_explicit_bad_selection():
    with pytest.raises(RuntimeError, match="feature contract disallows explicit"):
        subject.apply_production_feature_contract(
            ["ret_60d", "inst_count_qoq"],
            feature_group="model_selection_run",
            strict=True,
        )


def test_best_params_disable_lightgbm_feature_prefilter_for_optuna():
    class Study:
        best_params = {"min_data_in_leaf": 100}

    params = subject._best_params(Study())

    assert params["feature_pre_filter"] is False


def test_fixed_params_json_adds_lightgbm_runtime_defaults():
    params = subject._fixed_params_from_json('{"num_leaves": 31, "min_data_in_leaf": 1000}')

    assert params["num_leaves"] == 31
    assert params["min_data_in_leaf"] == 1000
    assert params["model_family"] == "lightgbm"
    assert params["objective"] == "regression"
    assert params["metric"] == "rmse"
    assert params["feature_pre_filter"] is False
    assert params["seed"] == 42


def test_fixed_params_json_preserves_lightgbm_ridge_blend_controls():
    params = subject._fixed_params_from_json(
        '{"model_family": "lightgbm_ridge_blend", "ridge_weight": 0.65, "ridge_alpha": 2.5, "num_leaves": 31}',
        model_family="lightgbm_ridge_blend",
    )

    assert params["model_family"] == "lightgbm_ridge_blend"
    assert params["ridge_weight"] == pytest.approx(0.65)
    assert params["ridge_alpha"] == pytest.approx(2.5)
    assert params["num_leaves"] == 31
    assert params["objective"] == "regression"
    assert subject._resolve_training_model_family("auto", params) == "lightgbm_ridge_blend"


def test_fixed_params_json_rejects_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        subject._fixed_params_from_json("[1, 2, 3]")


def test_split_time_series_indices_match_date_order():
    dates = np.array(["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"], dtype=object)

    train_idx, valid_idx, holdout_idx = subject.split_time_series_indices(
        dates,
        train_ratio=0.5,
        valid_ratio=0.25,
    )

    assert train_idx.tolist() == [0, 1, 2]
    assert valid_idx.tolist() == [3]
    assert holdout_idx.tolist() == [4]


def test_prediction_rows_rank_scores_without_dataframe_registration():
    rows = subject._prediction_rows(
        "model_a",
        [
            {"stock_code": "000001", "date": "2026-01-01"},
            {"stock_code": "000002", "date": "2026-01-01"},
            {"stock_code": "000003", "date": "2026-01-01"},
        ],
        [0.2, 0.4, 0.4],
    )

    by_code = {row[1]: row for row in rows}
    assert by_code["000001"][4] == 3
    assert by_code["000001"][5] == pytest.approx(1 / 3)
    assert by_code["000002"][4] == 1
    assert by_code["000002"][5] == pytest.approx(5 / 6)
    assert by_code["000003"][4] == 1


def test_prediction_rows_arrays_match_record_semantics():
    rows = subject._prediction_rows_arrays(
        "model_a",
        np.array(["000001", "000002", "000003"], dtype=object),
        np.array(["2026-01-01", "2026-01-01", "2026-01-01"], dtype=object),
        [0.2, 0.4, 0.4],
    )

    by_code = {row[1]: row for row in rows}
    assert by_code["000001"][4] == 3
    assert by_code["000001"][5] == pytest.approx(1 / 3)
    assert by_code["000002"][4] == 1
    assert by_code["000002"][5] == pytest.approx(5 / 6)
    assert by_code["000003"][4] == 1


def test_prediction_column_arrays_and_bulk_insert():
    columns = subject._prediction_column_arrays(
        "model_a",
        np.array(["000001", "000002", "000003", "000004"], dtype=object),
        np.array(["2026-01-01", "2026-01-01", "2026-01-01", "2026-01-02"], dtype=object),
        [0.2, 0.4, 0.4, 0.1],
    )

    assert columns["rank_in_date"].tolist() == [3, 1, 1, 1]
    assert columns["percentile"].tolist() == pytest.approx([1 / 3, 5 / 6, 5 / 6, 1.0])

    conn = duck_mem()
    try:
        subject.ensure_model_schema(conn)
        subject._persist_prediction_arrays(
            conn,
            "model_a",
            columns["stock_code"],
            columns["date"],
            columns["pred_score"],
        )
        stored = conn.execute(
            """
            SELECT stock_code, rank_in_date, percentile
            FROM mart_multidim_prediction
            ORDER BY stock_code
            """
        ).fetchall()

        assert [(row[0], row[1]) for row in stored] == [
            ("000001", 3),
            ("000002", 1),
            ("000003", 1),
            ("000004", 1),
        ]
        assert [row[2] for row in stored] == pytest.approx([1 / 3, 5 / 6, 5 / 6, 1.0])
    finally:
        conn.close()
