import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts import run_feature_ablation as subject
from services.model_feature_schema import BASE_FEATURE_COLS


def test_compute_ic_and_decile_metrics_use_daily_cross_sections():
    y_true = []
    y_pred = []
    dates = []
    for day in ("2026-04-01", "2026-04-02"):
        for idx in range(10):
            y_true.append(float(idx))
            y_pred.append(float(idx))
            dates.append(day)

    ic, rank_ic = subject.compute_ic(y_true, y_pred, dates)
    decile = subject.decile_metrics(y_true, y_pred, dates)

    assert ic == pytest.approx(1.0)
    assert rank_ic == pytest.approx(1.0)
    assert decile["top_avg"] == pytest.approx(9.0)
    assert decile["bot_avg"] == pytest.approx(0.0)
    assert decile["spread"] == pytest.approx(9.0)
    assert decile["winrate_top"] == pytest.approx(1.0)


def test_matrix_constructs_lightgbm_compatible_ndarray():
    rows = [
        {"a": 1, "b": None},
        {"a": "2.5", "b": 3},
    ]

    matrix = subject._matrix(rows, ["a", "b"])

    assert matrix.shape == (2, 2)
    assert matrix.dtype.name == "float32"
    dataset = subject.lgb.Dataset(matrix, label=[0.1, 0.2])
    dataset.construct()


def test_load_panel_records_adds_regime_flags_and_splits_dates():
    conn = duck_mem()
    feature = BASE_FEATURE_COLS[0]
    try:
        conn.execute(
            f"""
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                regime_flag TEXT,
                forward_ret_20d DOUBLE,
                {feature} DOUBLE
            )
            """
        )
        rows = []
        for day_idx in range(10):
            day = f"2026-04-{day_idx + 1:02d}"
            for code_idx in range(2):
                rows.append((
                    f"00000{code_idx + 1}",
                    day,
                    "up" if day_idx % 2 == 0 else "down",
                    float(day_idx + code_idx),
                    float(day_idx),
                ))
        conn.executemany(
            f"INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?)",
            rows,
        )

        panel = subject.load_panel_records(
            conn,
            "2026-04-01",
            "2026-04-10",
        )
        train, valid, holdout = subject.split_time_series_records(panel)

        assert len(panel) == 20
        assert panel[0][feature] == 0.0
        assert panel[0]["regime_up"] == 1
        assert panel[0]["regime_down"] == 0
        assert len({row["date"] for row in train}) == 7
        assert len({row["date"] for row in valid}) == 1
        assert len({row["date"] for row in holdout}) == 2
    finally:
        conn.close()
