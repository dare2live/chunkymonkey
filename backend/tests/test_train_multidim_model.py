import sys
from pathlib import Path

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
