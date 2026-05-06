import pytest

from scripts.run_daily_topk import (
    DDL,
    demote_existing_primary_recommendations,
    _percentile_linear,
    _rank_percentiles,
    _top_by_regime,
)
from conftest import duck_mem


def test_rank_percentiles_matches_average_tie_semantics():
    assert _rank_percentiles([0.2, 0.4, 0.4, 0.1]) == pytest.approx([
        0.5,
        0.875,
        0.875,
        0.25,
    ])


def test_percentile_linear_interpolates_without_numpy():
    assert _percentile_linear([10, 20, 30, 40], 25) == pytest.approx(17.5)
    assert _percentile_linear([10, 20, 30, 40], 50) == pytest.approx(25.0)


def test_top_by_regime_keeps_each_group_limit_in_score_order():
    rows = [
        {"stock_code": "000001", "regime_flag": "up"},
        {"stock_code": "000002", "regime_flag": "down"},
        {"stock_code": "000003", "regime_flag": "up"},
        {"stock_code": "000004", "regime_flag": "up"},
        {"stock_code": "000005", "regime_flag": "down"},
    ]

    selected = _top_by_regime(rows, 2)

    assert [row["stock_code"] for row in selected] == [
        "000001",
        "000002",
        "000003",
        "000005",
    ]


def test_demote_existing_primary_recommendations_keeps_one_primary_model_per_date():
    with duck_mem() as conn:
        conn.executescript(DDL)
        for table in ("mart_daily_recommendation", "mart_daily_topk_view_cache"):
            conn.execute(
                f"""
                INSERT INTO {table}
                (snapshot_date, stock_code, model_id, rank_in_date, pred_score,
                 percentile, regime_flag, key_features_json, track_id,
                 is_primary, run_mode, built_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "2026-05-06",
                    "000001",
                    "old_champion",
                    1,
                    0.9,
                    1.0,
                    "risk_on",
                    "{}",
                    "primary",
                    True,
                    "champion",
                    "2026-05-06T09:00:00",
                ),
            )

        demote_existing_primary_recommendations(
            conn,
            snapshot_date="2026-05-06",
            model_id="new_champion",
        )

        for table in ("mart_daily_recommendation", "mart_daily_topk_view_cache"):
            row = conn.execute(
                f"""
                SELECT is_primary
                  FROM {table}
                 WHERE snapshot_date = '2026-05-06'
                   AND model_id = 'old_champion'
                """
            ).fetchone()
            assert row["is_primary"] is False
