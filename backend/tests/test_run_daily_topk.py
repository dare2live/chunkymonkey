import pytest
import json

from scripts.run_daily_topk import (
    DDL,
    build_key_features_json,
    demote_existing_primary_recommendations,
    _feature_value,
    _percentile_linear,
    _rank_percentiles,
    _top_by_regime,
    write_recommendation_explanations,
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


def test_feature_value_normalizes_missing_and_non_finite_values_to_training_default():
    assert _feature_value(None) == 0.0
    assert _feature_value("not-a-number") == 0.0
    assert _feature_value(float("nan")) == 0.0
    assert _feature_value(float("inf")) == 0.0
    assert _feature_value("-inf") == 0.0
    assert _feature_value("1.25") == pytest.approx(1.25)


def test_build_key_features_json_includes_stock_feature_values():
    payload = json.loads(
        build_key_features_json(
            {"ret_20d": 0.12, "bad": float("nan"), "raw_text": "x"},
            [("ret_20d", 3.0), ("bad", 2.0), ("raw_text", 1.0)],
        )
    )

    assert payload["model_top_features"][0] == {"name": "ret_20d", "importance": 3.0}
    assert payload["stock_feature_values"][0] == {
        "name": "ret_20d",
        "importance": 3.0,
        "raw_value": 0.12,
        "model_value": 0.12,
    }
    assert payload["stock_feature_values"][1]["raw_value"] is None
    assert payload["stock_feature_values"][1]["model_value"] == 0.0
    assert payload["stock_feature_values"][2]["model_value"] == 0.0


def test_build_key_features_json_includes_exact_contributions_when_available():
    payload = json.loads(
        build_key_features_json(
            {"ret_20d": 0.12},
            [("ret_20d", 3.0)],
            top_contribution_rows=[
                {
                    "name": "ret_20d",
                    "raw_value": 0.12,
                    "model_value": 0.12,
                    "contribution": 0.08,
                    "contribution_pct": 1.0,
                    "direction": "positive",
                }
            ],
            explanation_status="exact",
            base_value=0.01,
            additivity_error=0.0,
        )
    )

    assert payload["explanation_status"] == "exact"
    assert payload["base_value"] == pytest.approx(0.01)
    assert payload["stock_feature_contributions"][0]["contribution"] == pytest.approx(0.08)


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


def test_write_recommendation_explanations_persists_feature_rows_and_summary():
    with duck_mem() as conn:
        conn.executescript(DDL)
        count = write_recommendation_explanations(
            conn,
            output_rows=[
                {
                    "snapshot_date": "2026-05-06",
                    "stock_code": "000001",
                    "model_id": "model_1",
                    "rank_in_date": 1,
                    "pred_score": 0.15,
                    "run_mode": "champion",
                }
            ],
            source_rows=[{"ret_20d": 0.12, "ma_ratio_60": 1.05}],
            feature_cols=["ret_20d", "ma_ratio_60"],
            explanation={
                "status": "exact",
                "model_family": "FakeLightGBM",
                "max_abs_error": 0.0,
                "reason": None,
                "rows": [
                    {
                        "base_value": 0.01,
                        "score": 0.15,
                        "additivity_error": 0.0,
                        "features": [
                            {
                                "feature_name": "ret_20d",
                                "contribution": 0.12,
                                "contribution_pct": 0.8,
                                "direction": "positive",
                            },
                            {
                                "feature_name": "ma_ratio_60",
                                "contribution": 0.02,
                                "contribution_pct": 0.2,
                                "direction": "positive",
                            },
                        ],
                    }
                ],
            },
            built_at="2026-05-06T10:00:00",
        )
        summary = conn.execute(
            "SELECT explainer_status, row_count, feature_count FROM mart_model_explanation"
        ).fetchone()
        rows = conn.execute(
            """
            SELECT feature_name, contribution, raw_value, model_value, base_value
              FROM mart_daily_recommendation_explanation
             ORDER BY feature_name
            """
        ).fetchall()

        assert count == 2
        assert summary["explainer_status"] == "exact"
        assert summary["row_count"] == 1
        assert summary["feature_count"] == 2
        assert rows[1]["feature_name"] == "ret_20d"
        assert rows[1]["contribution"] == pytest.approx(0.12)
        assert json.loads(rows[1]["raw_value"]) == pytest.approx(0.12)
        assert rows[1]["model_value"] == pytest.approx(0.12)
        assert rows[1]["base_value"] == pytest.approx(0.01)
