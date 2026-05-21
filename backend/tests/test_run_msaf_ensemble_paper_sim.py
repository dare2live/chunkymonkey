from __future__ import annotations

import pandas as pd
import duckdb

from scripts.run_msaf_ensemble_paper_sim import (
    apply_cash_overlay,
    apply_score_exposure,
    apply_score_floor,
    apply_sniper_floor,
    apply_source_weight_override,
    compute_kpi,
    load_lambdamart_predictions,
    weighted_return_by_rank,
)
from services.strategies.regime.regime_state import RegimeVerdict


def test_apply_cash_overlay_keeps_default_cash_when_unset():
    assert apply_cash_overlay(regime_state="neutral", base_cash_pct=0.0) == 0.0
    assert apply_cash_overlay(regime_state="bear", base_cash_pct=0.6) == 0.6


def test_apply_cash_overlay_only_raises_cash_for_matching_regime():
    assert apply_cash_overlay(
        regime_state="neutral",
        base_cash_pct=0.0,
        neutral_cash_pct=0.3,
    ) == 0.3
    assert apply_cash_overlay(
        regime_state="bear",
        base_cash_pct=0.6,
        neutral_cash_pct=0.3,
    ) == 0.6


def test_apply_cash_overlay_never_lowers_existing_cash_or_exceeds_one():
    assert apply_cash_overlay(
        regime_state="bear",
        base_cash_pct=0.6,
        bear_cash_pct=0.3,
    ) == 0.6
    assert apply_cash_overlay(
        regime_state="bull",
        base_cash_pct=0.0,
        bull_cash_pct=1.5,
    ) == 1.0


def test_load_lambdamart_predictions_falls_back_to_v6_table(tmp_path):
    db_path = tmp_path / "smartmoney.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            """
            CREATE TABLE mart_p0b_oos_predictions (
                signal_date DATE, stock_code VARCHAR, score DOUBLE, model_id VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mart_p0b_lambdamart_v6_predictions (
                signal_date DATE, stock_code VARCHAR, score DOUBLE, model_id VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mart_p0a_label_panel (
                signal_date DATE,
                stock_code VARCHAR,
                fwd_cost_after_5d DOUBLE,
                fwd_cost_after_10d DOUBLE,
                fwd_cost_after_20d DOUBLE
            )
            """
        )
        con.execute(
            "INSERT INTO mart_p0b_lambdamart_v6_predictions VALUES "
            "('2024-01-02', '600001', 0.7, 'candidate_v6')"
        )
        con.execute(
            "INSERT INTO mart_p0a_label_panel VALUES "
            "('2024-01-02', '600001', 0.01, 0.02, 0.03)"
        )
    finally:
        con.close()

    df = load_lambdamart_predictions(
        str(db_path),
        model_id="candidate_v6",
        start_date="2024-01-01",
        end_date="2024-01-31",
    )

    assert len(df) == 1
    assert df.attrs["prediction_table"] == "mart_p0b_lambdamart_v6_predictions"
    assert df.iloc[0]["fwd_cost_after_20d"] == 0.03


def test_apply_source_weight_override_preserves_cash_and_normalizes_sources():
    regime = RegimeVerdict(
        signal_date="2025-01-01",
        state="bear",
        hs300_close=1.0,
        hs300_ma60=1.1,
        above_ma60=False,
        ret_60d=-0.1,
        breadth_pct=None,
        weights={"lambdamart": 0.1, "sniper": 0.2, "institution": 0.1, "cash": 0.6},
        reasoning="test",
    )

    adjusted = apply_source_weight_override(
        regime,
        lambdamart_weight=0.7,
        sniper_weight=0.3,
        institution_weight=0.0,
    )

    assert adjusted.weights["cash"] == 0.6
    assert round(adjusted.weights["lambdamart"], 4) == 0.28
    assert round(adjusted.weights["sniper"], 4) == 0.12
    assert adjusted.weights["institution"] == 0.0
    assert round(sum(adjusted.weights.values()), 6) == 1.0


def test_compute_kpi_vol_target_uses_prior_observations_only():
    dates = pd.date_range("2024-01-01", periods=11, freq="D")
    results = [
        {
            "signal_date": d.strftime("%Y-%m-%d"),
            "top_k_codes": ["600001"],
            "cash_pct": 0.0,
        }
        for d in dates
    ]
    preds = pd.DataFrame(
        [
            {"signal_date": dates[0], "stock_code": "600001", "fwd_cost_after_5d": 0.10},
            {"signal_date": dates[5], "stock_code": "600001", "fwd_cost_after_5d": -0.10},
            {"signal_date": dates[10], "stock_code": "600001", "fwd_cost_after_5d": 0.10},
        ]
    )

    kpi = compute_kpi(
        results,
        preds,
        horizon="5d",
        target_ann_vol=0.10,
        vol_window=2,
        max_exposure=1.0,
    )

    assert kpi["n_obs"] == 3
    assert kpi["max_realized_exposure"] == 1.0
    assert kpi["min_realized_exposure"] < 1.0
    assert kpi["avg_exposure"] < 1.0


def test_apply_score_floor_keeps_unused_slots_in_cash():
    codes, scores, cash, dropped = apply_score_floor(
        top_k_codes=["600001", "600002", "600003", "600004"],
        top_k_scores=[0.8, 0.55, 0.40, 0.20],
        cash_pct=0.20,
        min_top_score=0.50,
    )

    assert codes == ["600001", "600002"]
    assert scores == [0.8, 0.55]
    assert dropped == 2
    assert cash == 0.60


def test_apply_score_floor_all_cash_when_every_pick_fails():
    codes, scores, cash, dropped = apply_score_floor(
        top_k_codes=["600001", "600002"],
        top_k_scores=[0.4, 0.3],
        cash_pct=0.0,
        min_top_score=0.5,
    )

    assert codes == []
    assert scores == []
    assert cash == 1.0
    assert dropped == 2


def test_apply_sniper_floor_keeps_unused_slots_in_cash():
    codes, scores, cash, dropped = apply_sniper_floor(
        top_k_codes=["600001", "600002", "600003"],
        top_k_scores=[0.8, 0.7, 0.6],
        sniper_scores=pd.Series({"600001": 0.6, "600002": 0.2, "600003": 0.5}),
        cash_pct=0.10,
        min_sniper_score=0.5,
    )

    assert codes == ["600001", "600003"]
    assert scores == [0.8, 0.6]
    assert dropped == 1
    assert cash == 0.40


def test_apply_sniper_floor_all_cash_when_scores_missing():
    codes, scores, cash, dropped = apply_sniper_floor(
        top_k_codes=["600001", "600002"],
        top_k_scores=[0.8, 0.7],
        sniper_scores=None,
        cash_pct=0.0,
        min_sniper_score=0.5,
    )

    assert codes == []
    assert scores == []
    assert cash == 1.0
    assert dropped == 2


def test_apply_score_exposure_keeps_default_when_unset():
    cash, exposure, avg_score = apply_score_exposure(
        top_k_scores=[0.4, 0.6],
        cash_pct=0.2,
    )

    assert cash == 0.2
    assert exposure == 1.0
    assert avg_score is None


def test_apply_score_exposure_smoothly_raises_cash_without_dropping_picks():
    cash, exposure, avg_score = apply_score_exposure(
        top_k_scores=[0.45, 0.55],
        cash_pct=0.20,
        score_exposure_floor=0.40,
        score_exposure_ceiling=0.60,
        score_min_exposure=0.25,
    )

    assert avg_score == 0.50
    assert exposure == 0.625
    assert cash == 0.50


def test_apply_score_exposure_uses_all_cash_for_empty_picks():
    cash, exposure, avg_score = apply_score_exposure(
        top_k_scores=[],
        cash_pct=0.20,
        score_exposure_floor=0.40,
        score_exposure_ceiling=0.60,
    )

    assert cash == 1.0
    assert exposure == 0.0
    assert avg_score is None


def test_weighted_return_by_rank_preserves_equal_weight_default():
    assert round(weighted_return_by_rank([0.10, 0.20]), 6) == 0.15


def test_weighted_return_by_rank_applies_top_heavy_decay():
    assert round(weighted_return_by_rank([0.10, 0.20], rank_decay=0.5), 6) == 0.133333
