from __future__ import annotations

import os

import numpy as np
import optuna
import pandas as pd

from scripts import run_p0b_lambdamart_v6 as lambdamart_module
from scripts.run_p0b_lambdamart_v6 import (
    RankPanel,
    WindowSpec,
    _configure_optuna_parallelism,
    _rank_ic_stability_adjustment,
    assert_pit_strict,
    build_walk_forward_windows,
    remaining_trials_for_target,
    run_optuna,
)
from services.perf.prepared_panel import make_lambdarank_groups


def _panel_from_df(df: pd.DataFrame) -> RankPanel:
    df_sorted = df.sort_values(["signal_date", "stock_code"]).reset_index(drop=True)
    feature_cols = ["feat_a", "feat_b"]
    X, y_rel, groups = make_lambdarank_groups(
        df_sorted,
        df_sorted["signal_date"].drop_duplicates().tolist(),
        feature_cols=feature_cols,
    )
    assert int(groups.sum()) == len(df_sorted)
    return RankPanel(
        X=X,
        y_raw=df_sorted["fwd_cost_after_20d"].to_numpy(dtype=np.float32),
        y_relevance=y_rel,
        signal_dates=pd.to_datetime(df_sorted["signal_date"]).dt.strftime("%Y-%m-%d").to_numpy(),
        stock_codes=df_sorted["stock_code"].astype(str).to_numpy(),
        feature_columns=feature_cols,
    )


def test_group_sizes_correct():
    df = pd.DataFrame([
        {"stock_code": "A", "signal_date": "2024-01-02", "feat_a": 1.0, "feat_b": np.nan, "fwd_cost_after_20d": 0.01},
        {"stock_code": "B", "signal_date": "2024-01-02", "feat_a": 2.0, "feat_b": 2.0, "fwd_cost_after_20d": 0.02},
        {"stock_code": "C", "signal_date": "2024-01-02", "feat_a": 3.0, "feat_b": 3.0, "fwd_cost_after_20d": 0.03},
        {"stock_code": "A", "signal_date": "2024-01-03", "feat_a": 4.0, "feat_b": 4.0, "fwd_cost_after_20d": -0.01},
        {"stock_code": "B", "signal_date": "2024-01-03", "feat_a": 5.0, "feat_b": 5.0, "fwd_cost_after_20d": 0.04},
    ])

    X, y_rel, groups = make_lambdarank_groups(
        df,
        ["2024-01-02", "2024-01-03"],
        feature_cols=["feat_a", "feat_b"],
    )

    assert groups.tolist() == [3, 2]
    assert int(groups.sum()) == len(df)
    assert X.shape == (5, 2)
    assert y_rel.shape == (5,)
    assert X[0, 1] == -9999.0


def test_relevance_label_encoding():
    rows = []
    for i in range(10):
        rows.append({
            "stock_code": f"S{i:02d}",
            "signal_date": "2024-02-01",
            "feat_a": float(i),
            "feat_b": float(10 - i),
            "fwd_cost_after_20d": float(i),
        })
    df = pd.DataFrame(rows)

    _, y_rel, groups = make_lambdarank_groups(
        df,
        ["2024-02-01"],
        feature_cols=["feat_a", "feat_b"],
    )

    assert groups.tolist() == [10]
    assert y_rel.min() == 0
    assert y_rel.max() == 4
    assert y_rel[0] == 0
    assert y_rel[-1] == 4


def test_pit_strict_no_leakage():
    rows = []
    for m in range(13):
        signal_date = pd.Timestamp("2024-01-15") + pd.DateOffset(months=m)
        for s in range(2):
            rows.append({
                "stock_code": f"S{s:02d}",
                "signal_date": signal_date,
                "feat_a": float(m),
                "feat_b": float(s),
                "fwd_cost_after_20d": float(m + s),
            })
    rows.append({
        "stock_code": "S99",
        "signal_date": pd.Timestamp("2026-01-15"),
        "feat_a": 99.0,
        "feat_b": 99.0,
        "fwd_cost_after_20d": 99.0,
    })
    panel = _panel_from_df(pd.DataFrame(rows))

    windows = build_walk_forward_windows(
        panel,
        min_train_months=6,
        forward_months=1,
    )

    future_date = "2026-01-15"
    assert any(future_date in set(panel.signal_dates[w.test_idx]) for w in windows)
    for window in windows:
        train_dates = panel.signal_dates[window.train_idx]
        test_dates = panel.signal_dates[window.test_idx]
        assert_pit_strict(train_dates, test_dates)
        if future_date in set(test_dates):
            assert future_date not in set(train_dates)


def test_resume_trial_budget_counts_completed_trials_only():
    study = optuna.create_study(direction="maximize")
    study.add_trial(
        optuna.trial.create_trial(
            params={"x": 1},
            distributions={"x": optuna.distributions.IntDistribution(0, 2)},
            value=0.1,
        )
    )
    study.add_trial(optuna.trial.create_trial(state=optuna.trial.TrialState.PRUNED))
    study.add_trial(
        optuna.trial.create_trial(
            params={"x": 2},
            distributions={"x": optuna.distributions.IntDistribution(0, 2)},
            value=0.2,
        )
    )

    assert remaining_trials_for_target(study, 5) == 3
    assert remaining_trials_for_target(study, 2) == 0


def test_optuna_parallelism_caps_oversubscribed_inner_threads(monkeypatch):
    monkeypatch.setenv("OPTUNA_N_JOBS", "4")
    monkeypatch.setenv("OMP_NUM_THREADS", "32")
    monkeypatch.setattr("scripts.run_p0b_lambdamart_v6.os.cpu_count", lambda: 32)

    n_jobs, inner_threads = _configure_optuna_parallelism()

    assert n_jobs == 4
    assert inner_threads == 8
    assert os.environ["OMP_NUM_THREADS"] == "8"
    assert os.environ["LIGHTGBM_NUM_THREADS"] == "8"


def test_rank_ic_stability_adjustment_defaults_to_no_penalty():
    adjustment = _rank_ic_stability_adjustment([0.10, -0.02, 0.04])

    assert adjustment["window_rank_ic_mean"] == 0.04
    assert round(adjustment["window_rank_ic_positive_rate"], 6) == 0.666667
    assert round(adjustment["window_rank_ic_negative_rate"], 6) == 0.333333
    assert adjustment["rank_ic_stability_penalty"] == 0.0


def test_rank_ic_stability_adjustment_penalizes_std_and_negative_rate():
    adjustment = _rank_ic_stability_adjustment(
        [0.10, -0.02, 0.04],
        std_penalty_weight=2.0,
        negative_rate_penalty_weight=0.5,
    )

    assert adjustment["rank_ic_stability_penalty"] > 0.5 * adjustment["window_rank_ic_negative_rate"]
    assert adjustment["window_rank_ic_std"] > 0


def test_rank_ic_stability_adjustment_ignores_non_finite_values():
    adjustment = _rank_ic_stability_adjustment([0.10, None, float("nan"), "bad", -0.02])  # type: ignore[list-item]

    assert adjustment["window_rank_ic_mean"] == 0.04
    assert adjustment["window_rank_ic_positive_rate"] == 0.5
    assert adjustment["window_rank_ic_negative_rate"] == 0.5


def test_lambdamart_optuna_collects_window_rank_ic_for_stability_penalty(monkeypatch):
    monkeypatch.setenv("OPTUNA_N_JOBS", "1")
    monkeypatch.setenv("OMP_NUM_THREADS", "1")

    panel = RankPanel(
        X=np.zeros((4, 2), dtype=np.float32),
        y_raw=np.array([0.01, -0.01, 0.02, -0.02], dtype=np.float32),
        y_relevance=np.array([3, 1, 4, 0], dtype=np.int32),
        signal_dates=np.array(["2024-01-02", "2024-01-02", "2024-02-02", "2024-02-02"]),
        stock_codes=np.array(["A", "B", "A", "B"]),
        feature_columns=["feat_a", "feat_b"],
    )
    windows = [
        WindowSpec(
            train_idx=np.array([0, 1]),
            test_idx=np.array([2, 3]),
            train_start="2024-01-02",
            train_end="2024-01-02",
            test_start="2024-02-02",
            test_end="2024-02-02",
        ),
        WindowSpec(
            train_idx=np.array([2, 3]),
            test_idx=np.array([0, 1]),
            train_start="2024-02-02",
            train_end="2024-02-02",
            test_start="2024-01-02",
            test_end="2024-01-02",
        ),
    ]

    def _fake_run_window(panel, window, params, *, label_col):
        return pd.DataFrame({
            "stock_code": ["A", "B"],
            "signal_date": [window.test_start, window.test_start],
            "score": [1.0, 0.0],
            label_col: [0.02, -0.02],
            "relevance": [4, 0],
        })

    rank_ic_values = iter([0.10, -0.02, 0.04])

    def _fake_evaluate_predictions(df, *, label_col, top_k=5):
        return {
            "rank_ic": next(rank_ic_values),
            "ndcg5": 1.0,
            "ndcg10": 0.8,
            "ndcg20": 0.6,
            "top5_spread": 0.04,
            "top10_spread": 0.03,
            "top5_turnover": 1.0,
        }

    monkeypatch.setattr(lambdamart_module, "_run_lambdamart_window", _fake_run_window)
    monkeypatch.setattr(lambdamart_module, "evaluate_predictions", _fake_evaluate_predictions)

    result = run_optuna(
        model_name="lambdamart",
        panel=panel,
        windows=windows,
        label_col="fwd_cost_after_20d",
        n_trials=1,
        n_estimators=1,
        seed=7,
        turnover_limit=3.0,
        turnover_penalty_weight=0.02,
        top_k=5,
        window_rank_ic_std_penalty_weight=1.0,
        window_rank_ic_negative_rate_penalty_weight=0.5,
    )

    assert result.n_trials == 1
    assert round(result.metrics["window_rank_ic_mean"], 6) == 0.04
    assert result.metrics["window_rank_ic_negative_rate"] == 0.5
    assert result.metrics["rank_ic_stability_penalty"] > 0.25
    assert result.best_value < 0.86
