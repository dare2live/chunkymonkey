import pytest

from conftest import duck_mem
from scripts.train_tdx_challenger_model import (
    _candidate_features_for_set,
    _long_short,
    _mean_rank_ic,
    _rank_score,
)


def test_rank_score_averages_daily_feature_percentiles():
    rows = [
        {"date": "2026-04-28", "stock_code": "000001", "f1": 1.0, "f2": 3.0},
        {"date": "2026-04-28", "stock_code": "000002", "f1": 2.0, "f2": 2.0},
        {"date": "2026-04-28", "stock_code": "000003", "f1": 3.0, "f2": 1.0},
    ]

    scored = _rank_score(rows, ["f1", "f2"], "__score")

    assert [row["__score"] for row in scored] == pytest.approx([
        (1 / 3 + 1.0) / 2,
        (2 / 3 + 2 / 3) / 2,
        (1.0 + 1 / 3) / 2,
    ])


def test_mean_rank_ic_uses_spearman_by_date():
    rows = []
    for idx in range(12):
        rows.append({
            "date": "2026-04-28",
            "__score": float(idx),
            "forward_ret_20d": float(idx * 2),
        })

    assert _mean_rank_ic(rows, "__score") == pytest.approx(1.0)


def test_long_short_computes_spread_and_drawdown():
    rows = []
    for idx in range(20):
        rows.append({
            "date": "2026-04-28",
            "__score": float(idx),
            "forward_ret_20d": 0.1 if idx >= 18 else -0.05,
        })

    spread, max_drawdown = _long_short(rows, "__score")

    assert spread == pytest.approx(0.1)
    assert max_drawdown == pytest.approx(0.0)


def test_candidate_features_for_set_uses_candidate_table_columns():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                forward_ret_20d DOUBLE,
                top10_concentration_change DOUBLE
            )
            """
        )

        features = _candidate_features_for_set(conn, "tdx_candidate_v1")

        assert features == ["top10_concentration_change"]
    finally:
        conn.close()
