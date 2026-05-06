import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts import run_multidim_walkforward as subject
from services.model_feature_schema import BASE_FEATURE_COLS


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def test_walkforward_load_panel_records_and_feature_group_resolution():
    conn = duck_mem()
    feature = BASE_FEATURE_COLS[0]
    try:
        conn.execute(
            f"""
            CREATE TABLE fact_feature_panel (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                regime_flag TEXT,
                forward_ret_20d DOUBLE,
                {_quote(feature)} DOUBLE
            )
            """
        )
        rows = []
        for day_idx in range(12):
            day = f"2026-04-{day_idx + 1:02d}"
            for code_idx in range(2):
                rows.append((
                    "main_set",
                    f"00000{code_idx + 1}",
                    day,
                    "flat",
                    float(day_idx),
                    float(code_idx + day_idx),
                ))
        rows.append(("other_set", "000003", "2026-04-01", "up", 1.0, 1.0))
        conn.executemany(
            f"INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )

        panel = subject.load_panel_records(
            conn,
            "2026-04-01",
            "2026-04-12",
            label_name="forward_ret_20d",
            feature_table="fact_feature_panel",
            feature_set_id="main_set",
        )
        features, schema = subject.resolve_feature_group("base", panel, regime_aware=True)
        folds = subject.build_folds(
            sorted({row["date"] for row in panel}),
            train_days=6,
            valid_days=2,
            test_days=2,
            step_days=2,
        )

        assert len(panel) == 24
        assert feature in features
        assert "regime_flat" in features
        assert schema == "m7_base_v1_regime"
        assert folds[0]["train"] == ("2026-04-01", "2026-04-06")
        assert folds[0]["valid"] == ("2026-04-07", "2026-04-08")
        assert folds[0]["test"] == ("2026-04-09", "2026-04-10")
        assert subject.feature_group_uses_alpha158("base_dense_v2") is False
        assert subject.feature_group_uses_alpha158("model_selection_run") is False
        assert subject.feature_group_uses_alpha158("legacy_full") is True
    finally:
        conn.close()


def test_walkforward_default_params_disable_lightgbm_feature_prefilter():
    assert subject.DEFAULT_PARAMS["feature_pre_filter"] is False


def test_prediction_rows_score_profile_and_write_fold():
    conn = duck_mem()
    try:
        test_rows = [
            {"stock_code": "000001", "date": "2026-04-01"},
            {"stock_code": "000002", "date": "2026-04-01"},
            {"stock_code": "000003", "date": "2026-04-01"},
            {"stock_code": "000001", "date": "2026-04-02"},
            {"stock_code": "000002", "date": "2026-04-02"},
        ]
        pred_rows = subject._prediction_rows("wf_1", 1, test_rows, [0.2, 0.5, 0.5, 0.1, 0.3])
        median, minimum = subject._score_profile(pred_rows)
        row = {
            "run_id": "wf_1",
            "fold_id": 1,
            "model_id": "model_1",
            "feature_schema_version": "walkforward_test",
            "label_name": "forward_ret_20d",
            "train_start": "2026-01-01",
            "train_end": "2026-01-31",
            "valid_start": "2026-02-01",
            "valid_end": "2026-02-28",
            "test_start": "2026-03-01",
            "test_end": "2026-03-31",
            "n_train": 10,
            "n_valid": 5,
            "n_test": 5,
            "n_features": 1,
            "params_json": "{}",
            "test_ic": 0.1,
            "test_rank_ic": 0.2,
            "test_top_decile_avg": 0.03,
            "test_bottom_decile_avg": -0.01,
            "test_long_short_spread": 0.04,
            "test_winrate_top": 0.6,
            "test_market_state": "flat",
            "test_mean_forward_ret": 0.01,
            "best_iteration": 400,
            "daily_distinct_score_median": median,
            "daily_distinct_score_min": minimum,
            "quality_flag": "degenerate",
            "built_at": "2026-05-05T00:00:00",
        }

        subject.write_fold(conn, row, pred_rows)
        stored = conn.execute(
            "SELECT daily_distinct_score_median, daily_distinct_score_min "
            "FROM mart_model_walkforward_fold"
        ).fetchone()
        first_pred = conn.execute(
            """
            SELECT rank_in_date, percentile
            FROM mart_model_walkforward_prediction
            WHERE stock_code = '000001' AND date = '2026-04-01'
            """
        ).fetchone()

        assert median == pytest.approx(2.0)
        assert minimum == 2
        assert stored["daily_distinct_score_median"] == 2.0
        assert stored["daily_distinct_score_min"] == 2
        assert first_pred["rank_in_date"] == 3
        assert first_pred["percentile"] == pytest.approx(1 / 3)
    finally:
        conn.close()


def test_prediction_columns_score_profile_topk_and_bulk_write():
    conn = duck_mem()
    try:
        pred_columns = subject._prediction_columns(
            "wf_1",
            1,
            np.array(["000001", "000002", "000003", "000001", "000002"], dtype=object),
            np.array(["2026-04-01", "2026-04-01", "2026-04-01", "2026-04-02", "2026-04-02"], dtype=object),
            [0.2, 0.5, 0.5, 0.1, 0.3],
        )
        median, minimum = subject._score_profile_columns(pred_columns)
        topk = subject._filter_prediction_columns_topk(pred_columns, 1)
        row = {
            "run_id": "wf_1",
            "fold_id": 1,
            "model_id": "model_1",
            "feature_schema_version": "walkforward_test",
            "label_name": "forward_ret_20d",
            "train_start": "2026-01-01",
            "train_end": "2026-01-31",
            "valid_start": "2026-02-01",
            "valid_end": "2026-02-28",
            "test_start": "2026-03-01",
            "test_end": "2026-03-31",
            "n_train": 10,
            "n_valid": 5,
            "n_test": 5,
            "n_features": 1,
            "params_json": "{}",
            "test_ic": 0.1,
            "test_rank_ic": 0.2,
            "test_top_decile_avg": 0.03,
            "test_bottom_decile_avg": -0.01,
            "test_long_short_spread": 0.04,
            "test_winrate_top": 0.6,
            "test_market_state": "flat",
            "test_mean_forward_ret": 0.01,
            "best_iteration": 400,
            "daily_distinct_score_median": median,
            "daily_distinct_score_min": minimum,
            "quality_flag": "degenerate",
            "built_at": "2026-05-05T00:00:00",
        }

        subject.write_fold(conn, row, pred_columns=topk)
        stored = conn.execute(
            """
            SELECT date, stock_code, rank_in_date
            FROM mart_model_walkforward_prediction
            ORDER BY date, stock_code
            """
        ).fetchall()

        assert median == pytest.approx(2.0)
        assert minimum == 2
        assert len(topk["pred_score"]) == 2
        assert [tuple(row) for row in stored] == [
            ("2026-04-01", "000002", 1),
            ("2026-04-02", "000002", 1),
        ]
    finally:
        conn.close()
