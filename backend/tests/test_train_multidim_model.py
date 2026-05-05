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
    assert subject.feature_group_uses_alpha158("base_alpha158") is True
    assert subject.feature_group_uses_alpha158("base_dense_v2_alpha158") is True
    assert subject.feature_group_uses_alpha158("legacy_full") is True


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
