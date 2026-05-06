from __future__ import annotations

import json

import numpy as np
import pytest

from conftest import duck_mem
from scripts import run_optuna_model_stability_search as subject


pytestmark = pytest.mark.pipeline


class FakePanel:
    dates = np.array([f"2026-01-{idx + 1:02d}" for idx in range(14)], dtype=object)
    columns = {"f1"}
    row_count = 14


def _seed_model_selection(conn) -> None:
    subject.ensure_tables(conn)
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
            'forward_ret_20d', 1.0, '["f1"]', '[]', 8, '{}', '2026-05-06'
        )
        """
    )


def test_score_metrics_passes_stable_candidate():
    score, status, reason = subject.score_metrics(
        {
            "holdout_rank_ic": 0.09,
            "holdout_long_short_spread": 0.012,
            "holdout_winrate_top": 0.67,
            "walkforward_avg_rank_ic": 0.033,
            "walkforward_std_rank_ic": 0.029,
            "walkforward_avg_spread": 0.010,
            "ok_folds": 6,
        },
        min_holdout_rank_ic=0.04,
        min_holdout_spread=0.009,
        min_walkforward_avg_rank_ic=0.015,
        max_walkforward_std_rank_ic=0.03,
        min_ok_folds=4,
    )

    assert status == "pass"
    assert reason is None
    assert score > 0


def test_score_metrics_rejects_unstable_candidate():
    _score, status, reason = subject.score_metrics(
        {
            "holdout_rank_ic": 0.10,
            "holdout_long_short_spread": 0.012,
            "holdout_winrate_top": 0.67,
            "walkforward_avg_rank_ic": 0.04,
            "walkforward_std_rank_ic": 0.06,
            "walkforward_avg_spread": 0.010,
            "ok_folds": 6,
        },
        min_holdout_rank_ic=0.04,
        min_holdout_spread=0.009,
        min_walkforward_avg_rank_ic=0.015,
        max_walkforward_std_rank_ic=0.03,
        min_ok_folds=4,
    )

    assert status == "fail"
    assert "walkforward_std_rank_ic" in reason


def test_topk_portfolio_metrics_apply_turnover_cost_and_drawdown():
    metrics = subject._topk_portfolio_metrics(
        pred=np.array([0.9, 0.1, 0.8, 0.2], dtype=np.float32),
        y=np.array([0.02, -0.01, -0.05, 0.01], dtype=np.float32),
        dates=np.array(["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"], dtype=object),
        codes=np.array(["000001", "000002", "000002", "000001"], dtype=object),
        topk_size=1,
        cost_bps=10,
    )

    assert metrics["periods"] == 2
    assert metrics["topk_turnover"] == pytest.approx(1.0)
    assert metrics["topk_net_return"] == pytest.approx((0.019 + -0.051) / 2)
    assert metrics["topk_max_drawdown"] < 0


def test_feature_drift_metrics_compute_feature_psi():
    class DriftPanel:
        def matrix(self, feature_cols, indices):
            data = np.array(
                [
                    [1.0, 10.0],
                    [1.1, 11.0],
                    [1.2, 12.0],
                    [1.3, 13.0],
                    [3.0, 10.5],
                    [3.1, 11.5],
                    [3.2, 12.5],
                    [3.3, 13.5],
                ],
                dtype=np.float64,
            )
            return data[indices]

    metrics = subject._feature_drift_metrics(
        DriftPanel(),
        ["shifted", "stable"],
        np.array([0, 1, 2, 3]),
        np.array([4, 5, 6, 7]),
        bins=4,
    )

    assert metrics["feature_drift_psi_max"] > 0
    assert metrics["feature_drift_psi_avg"] > 0
    assert set(metrics["feature_drift_psi_by_feature"]) == {"shifted", "stable"}


def test_evaluation_cache_reuses_parameter_independent_feature_drift():
    class DriftPanel:
        def __init__(self):
            self.matrix_calls = 0
            self._matrix = np.array(
                [
                    [1.0, 10.0],
                    [1.1, 11.0],
                    [1.2, 12.0],
                    [1.3, 13.0],
                    [3.0, 10.5],
                    [3.1, 11.5],
                    [3.2, 12.5],
                    [3.3, 13.5],
                ],
                dtype=np.float64,
            )

        def matrix(self, feature_cols, indices):
            self.matrix_calls += 1
            return self._matrix[np.asarray(indices)]

    panel = DriftPanel()
    cache = subject.EvaluationArtifactsCache(cache_matrices=False, cache_feature_drift=True)

    first = subject._feature_drift_metrics(
        panel,
        ["shifted", "stable"],
        np.array([0, 1, 2, 3]),
        np.array([4, 5, 6, 7]),
        bins=4,
        eval_cache=cache,
    )
    second = subject._feature_drift_metrics(
        panel,
        ["shifted", "stable"],
        np.array([0, 1, 2, 3]),
        np.array([4, 5, 6, 7]),
        bins=4,
        eval_cache=cache,
    )

    summary = cache.summary()
    assert first == second
    assert panel.matrix_calls == 2
    assert summary["entries"]["feature_drift"] == 1
    assert summary["hits"]["feature_drift"] == 1
    assert summary["misses"]["feature_drift"] == 1


def test_evaluate_params_records_fold_feature_drift_detail(monkeypatch):
    class EvalPanel:
        dates = np.array([f"2026-01-{idx + 1:02d}" for idx in range(12)], dtype=object)
        columns = {"shifted", "stable"}

        def __init__(self):
            self._matrix = np.array(
                [
                    [1.0, 10.0],
                    [1.1, 10.1],
                    [1.2, 10.2],
                    [1.3, 10.3],
                    [1.4, 10.4],
                    [1.5, 10.5],
                    [1.6, 10.6],
                    [1.7, 10.7],
                    [3.0, 10.8],
                    [3.1, 10.9],
                    [3.2, 11.0],
                    [3.3, 11.1],
                ],
                dtype=np.float64,
            )

        def matrix(self, feature_cols, indices):
            col_map = {"shifted": 0, "stable": 1}
            cols = [col_map[col] for col in feature_cols]
            return self._matrix[np.asarray(indices)][:, cols]

        def codes_for(self, indices):
            return np.array([f"{idx:06d}" for idx in np.asarray(indices)], dtype=object)

        def dates_for(self, indices):
            return self.dates[np.asarray(indices)]

    def fake_fit_predict(panel, feature_cols, train_indices, test_indices, params, *, num_round, model_family, **_kwargs):
        pred = np.linspace(0.1, 0.9, len(test_indices), dtype=np.float64)
        y = np.linspace(0.2, 1.0, len(test_indices), dtype=np.float64)
        return pred, y, panel.dates[np.asarray(test_indices)]

    monkeypatch.setattr(subject, "_fit_predict", fake_fit_predict)
    monkeypatch.setattr(subject, "compute_ic", lambda y, pred, dates: (0.02, 0.04))
    monkeypatch.setattr(subject, "decile_metrics", lambda y, pred, dates: {"spread": 0.01, "winrate_top": 0.6})
    monkeypatch.setattr(
        subject,
        "_topk_portfolio_metrics",
        lambda *args, **kwargs: {
            "topk_net_return": 0.01,
            "topk_turnover": 0.2,
            "topk_max_drawdown": -0.01,
        },
    )
    monkeypatch.setattr(subject, "_quality_from_predictions", lambda *args, **kwargs: ("ok", 12, 10))

    metrics = subject.evaluate_params(
        EvalPanel(),
        ["shifted", "stable"],
        {"model_family": "lightgbm"},
        folds=[
            {
                "fold_id": 1,
                "train": ("2026-01-01", "2026-01-06"),
                "valid": ("2026-01-07", "2026-01-08"),
                "test": ("2026-01-09", "2026-01-12"),
            }
        ],
        num_round=1,
        drift_bins=4,
    )

    fold = metrics["fold_metrics"][0]
    assert fold["feature_drift_psi_max"] > 0
    assert set(fold["feature_drift_psi_by_feature"]) == {"shifted", "stable"}


def test_runtime_params_support_linear_model_families():
    ridge = subject._runtime_params({"alpha": 2.5}, model_family="ridge")
    elastic = subject._runtime_params({"alpha": 0.2, "l1_ratio": 0.4}, model_family="elastic_net")

    assert ridge == {"model_family": "ridge", "alpha": 2.5}
    assert elastic["model_family"] == "elastic_net"
    assert elastic["alpha"] == pytest.approx(0.2)
    assert elastic["l1_ratio"] == pytest.approx(0.4)
    assert elastic["max_iter"] == subject.DEFAULT_ELASTIC_NET_PARAMS["max_iter"]


def test_runtime_params_support_lightgbm_ridge_blend():
    params = subject._runtime_params(
        {
            "num_leaves": 15,
            "ridge_weight": 0.65,
            "ridge_alpha": 2.0,
        },
        model_family="lightgbm_ridge_blend",
        num_threads=2,
    )

    assert params["model_family"] == "lightgbm_ridge_blend"
    assert params["objective"] == "regression"
    assert params["num_leaves"] == 15
    assert params["ridge_weight"] == pytest.approx(0.65)
    assert params["ridge_alpha"] == pytest.approx(2.0)
    assert params["num_threads"] == 2


def test_runtime_params_support_lightgbm_ranker():
    params = subject._runtime_params({"num_leaves": 15}, model_family="lightgbm_ranker", num_threads=2)

    assert params["model_family"] == "lightgbm_ranker"
    assert params["objective"] == "lambdarank"
    assert params["metric"] == "ndcg"
    assert params["label_gain"] == [0, 1, 3, 7, 15]
    assert params["num_leaves"] == 15
    assert params["num_threads"] == 2


def test_blend_predictions_by_date_uses_cross_sectional_scores():
    lightgbm_pred = np.array([1.0, 2.0, 10.0, 20.0], dtype=np.float64)
    ridge_pred = np.array([2.0, 1.0, 20.0, 10.0], dtype=np.float64)
    dates = np.array(["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"], dtype=object)

    blended = subject._blend_predictions_by_date(
        lightgbm_pred,
        ridge_pred,
        dates,
        ridge_weight=0.25,
    )

    np.testing.assert_allclose(blended, np.array([-0.5, 0.5, -0.5, 0.5], dtype=np.float64))


def test_ranker_relevance_and_group_order_are_per_date():
    labels = np.array([0.10, 0.30, 0.20, -0.10, 0.00, 0.10], dtype=np.float64)
    dates = np.array(["2026-01-02", "2026-01-02", "2026-01-02", "2026-01-01", "2026-01-01", "2026-01-01"])

    relevance = subject._ranker_relevance_by_date(labels, dates, bins=5)
    order, groups = subject._ranker_group_order(dates)

    assert relevance.tolist() == [0, 4, 2, 0, 2, 4]
    assert dates[order].tolist() == ["2026-01-01", "2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02", "2026-01-02"]
    assert groups == [3, 3]


def test_ranker_artifact_cache_reuses_group_and_relevance_work(monkeypatch):
    class RankerPanel:
        dates = np.array(["2026-01-01"] * 3 + ["2026-01-02"] * 3 + ["2026-01-03"] * 2, dtype=object)
        labels = np.array([0.1, 0.3, 0.2, -0.1, 0.0, 0.1, 0.4, -0.2], dtype=np.float32)
        stock_codes = np.array([f"{idx:06d}" for idx in range(8)], dtype=object)
        _matrix = np.arange(16, dtype=np.float32).reshape(8, 2)

        def __init__(self):
            self.matrix_calls = 0

        def matrix(self, feature_cols, indices):
            self.matrix_calls += 1
            return self._matrix[np.asarray(indices)]

        def labels_for(self, indices):
            return self.labels[np.asarray(indices)]

        def dates_for(self, indices):
            return self.dates[np.asarray(indices)]

    captured = []

    class FakeDataset:
        def __init__(self, data, label=None, group=None, feature_name=None):
            self.data = data
            self.label = label
            self.group = group
            self.feature_name = feature_name

    class FakeModel:
        def predict(self, x):
            return np.arange(len(x), dtype=np.float32)

    def fake_train(params, dataset, num_boost_round):
        captured.append(dataset)
        return FakeModel()

    monkeypatch.setattr(subject.lgb, "Dataset", FakeDataset)
    monkeypatch.setattr(subject.lgb, "train", fake_train)

    panel = RankerPanel()
    cache = subject.RankerArtifactsCache()
    eval_cache = subject.EvaluationArtifactsCache(cache_matrices=True)
    timer = subject.PerfTimer()
    train = np.array([0, 1, 2, 3, 4, 5])
    test = np.array([6, 7])

    pred_1, y_1, dates_1 = subject._fit_predict(
        panel,
        ["f1", "f2"],
        train,
        test,
        {"model_family": "lightgbm_ranker", "objective": "lambdarank"},
        num_round=1,
        model_family="lightgbm_ranker",
        ranker_cache=cache,
        eval_cache=eval_cache,
        perf_timer=timer,
    )
    pred_2, y_2, dates_2 = subject._fit_predict(
        panel,
        ["f1", "f2"],
        train,
        test,
        {"model_family": "lightgbm_ranker", "objective": "lambdarank"},
        num_round=1,
        model_family="lightgbm_ranker",
        ranker_cache=cache,
        eval_cache=eval_cache,
        perf_timer=timer,
    )

    eval_summary = eval_cache.summary()
    assert pred_1.tolist() == pred_2.tolist() == [0.0, 1.0]
    assert y_1.tolist() == pytest.approx([0.4, -0.2])
    assert y_2.tolist() == pytest.approx([0.4, -0.2])
    assert dates_1.tolist() == dates_2.tolist() == ["2026-01-03", "2026-01-03"]
    assert cache.misses == 1
    assert cache.hits == 1
    assert captured[0].group == [3, 3]
    assert captured[0].label.tolist() == [0, 4, 2, 0, 2, 4]
    assert timer.summary()["counts"]["ranker_artifacts"] == 2
    assert cache.summary()["entries"] == 1
    assert panel.matrix_calls == 2
    assert eval_summary["entries"]["matrix"] == 2
    assert eval_summary["hits"]["matrix"] == 2
    assert eval_summary["misses"]["matrix"] == 2


def test_ranker_cached_and_uncached_evaluation_are_equivalent(monkeypatch):
    unique_dates = np.array([f"2026-01-{idx + 1:02d}" for idx in range(18)], dtype=object)
    codes = np.array([f"{idx:06d}" for idx in range(12)], dtype=object)
    day_idx = np.repeat(np.arange(len(unique_dates), dtype=np.float32), len(codes))
    stock_idx = np.tile(np.arange(len(codes), dtype=np.float32), len(unique_dates))

    class RankerPanel:
        dates = np.repeat(unique_dates, len(codes))
        stock_codes = np.tile(codes, len(unique_dates))
        labels = ((stock_idx - 5.5) / 50.0 + day_idx / 1000.0).astype(np.float32)
        features = {
            "f1": (stock_idx / 11.0 + day_idx / 100.0).astype(np.float32),
            "f2": np.sin((stock_idx + 1.0) * (day_idx + 1.0) / 25.0).astype(np.float32),
        }
        columns = {"f1", "f2"}
        row_count = int(labels.shape[0])

        def matrix(self, feature_cols, indices=None):
            idx = np.arange(self.row_count) if indices is None else np.asarray(indices)
            return np.column_stack([self.features[col][idx] for col in feature_cols]).astype(np.float32, copy=False)

        def labels_for(self, indices):
            return self.labels[np.asarray(indices)]

        def dates_for(self, indices):
            return self.dates[np.asarray(indices)]

        def codes_for(self, indices):
            return self.stock_codes[np.asarray(indices)]

    class FakeDataset:
        def __init__(self, data, label=None, group=None, feature_name=None):
            self.data = np.asarray(data, dtype=np.float32)
            self.label = np.asarray(label if label is not None else [], dtype=np.float32)
            self.group = list(group or [])
            self.feature_name = feature_name

    class FakeModel:
        def __init__(self, dataset):
            self.weights = np.array([0.7, -0.2], dtype=np.float32)
            self.bias = float(np.mean(dataset.label) / 100.0 + np.mean(dataset.group) / 10000.0)

        def predict(self, x):
            return np.asarray(x, dtype=np.float32) @ self.weights + self.bias

    monkeypatch.setattr(subject.lgb, "Dataset", FakeDataset)
    def fake_train(_params, dataset, num_boost_round):
        return FakeModel(dataset)

    monkeypatch.setattr(subject.lgb, "train", fake_train)

    panel = RankerPanel()
    folds = [
        {
            "fold_id": 1,
            "train": ("2026-01-01", "2026-01-08"),
            "valid": ("2026-01-09", "2026-01-10"),
            "test": ("2026-01-11", "2026-01-12"),
        },
        {
            "fold_id": 2,
            "train": ("2026-01-03", "2026-01-10"),
            "valid": ("2026-01-11", "2026-01-12"),
            "test": ("2026-01-13", "2026-01-14"),
        },
    ]
    params = {"model_family": "lightgbm_ranker", "objective": "lambdarank"}
    train = np.flatnonzero((panel.dates >= "2026-01-01") & (panel.dates <= "2026-01-10"))
    test = np.flatnonzero((panel.dates >= "2026-01-11") & (panel.dates <= "2026-01-12"))

    uncached_pred, uncached_y, uncached_dates = subject._fit_predict(
        panel,
        ["f1", "f2"],
        train,
        test,
        params,
        num_round=3,
        model_family="lightgbm_ranker",
        ranker_cache=None,
    )
    cache = subject.RankerArtifactsCache()
    cached_pred, cached_y, cached_dates = subject._fit_predict(
        panel,
        ["f1", "f2"],
        train,
        test,
        params,
        num_round=3,
        model_family="lightgbm_ranker",
        ranker_cache=cache,
    )
    assert cached_pred.tolist() == pytest.approx(uncached_pred.tolist())
    assert cached_y.tolist() == pytest.approx(uncached_y.tolist())
    assert cached_dates.tolist() == uncached_dates.tolist()

    uncached_metrics = subject.evaluate_params(
        panel,
        ["f1", "f2"],
        params,
        folds=folds,
        num_round=3,
        distinct_threshold=3,
        topk_size=3,
        cost_bps=0.0,
        drift_bins=5,
        model_family="lightgbm_ranker",
    )
    warm_cache = subject.RankerArtifactsCache()
    timer = subject.PerfTimer()
    eval_plan = subject.prepare_evaluation_plan(
        panel,
        folds,
        model_family="lightgbm_ranker",
        ranker_cache=warm_cache,
        perf_timer=timer,
    )
    cached_metrics = subject.evaluate_params(
        panel,
        ["f1", "f2"],
        params,
        folds=folds,
        num_round=3,
        distinct_threshold=3,
        topk_size=3,
        cost_bps=0.0,
        drift_bins=5,
        model_family="lightgbm_ranker",
        eval_plan=eval_plan,
        ranker_cache=warm_cache,
        perf_timer=timer,
    )

    metric_keys = [
        "holdout_rank_ic",
        "holdout_long_short_spread",
        "holdout_topk_net_return",
        "holdout_feature_drift_psi_max",
        "walkforward_avg_rank_ic",
        "walkforward_std_rank_ic",
        "walkforward_avg_topk_net_return",
        "walkforward_worst_topk_drawdown",
        "walkforward_worst_feature_drift_psi",
        "ok_folds",
    ]
    for key in metric_keys:
        assert cached_metrics[key] == pytest.approx(uncached_metrics[key])
    for cached_fold, uncached_fold in zip(cached_metrics["fold_metrics"], uncached_metrics["fold_metrics"]):
        assert cached_fold["rank_ic"] == pytest.approx(uncached_fold["rank_ic"])
        assert cached_fold["topk_net_return"] == pytest.approx(uncached_fold["topk_net_return"])
        assert cached_fold["feature_drift_psi_max"] == pytest.approx(uncached_fold["feature_drift_psi_max"])
    assert warm_cache.summary()["entries"] == 3
    assert warm_cache.summary()["hits"] == 3
    assert warm_cache.summary()["misses"] == 3


def test_score_metrics_rejects_excessive_topk_drawdown():
    _score, status, reason = subject.score_metrics(
        {
            "holdout_rank_ic": 0.10,
            "holdout_long_short_spread": 0.012,
            "holdout_winrate_top": 0.67,
            "holdout_topk_net_return": 0.01,
            "walkforward_avg_rank_ic": 0.04,
            "walkforward_std_rank_ic": 0.01,
            "walkforward_avg_spread": 0.010,
            "walkforward_avg_topk_net_return": 0.01,
            "walkforward_avg_topk_turnover": 0.2,
            "walkforward_worst_topk_drawdown": -0.25,
            "ok_folds": 6,
        },
        min_holdout_rank_ic=0.04,
        min_holdout_spread=0.009,
        min_walkforward_avg_rank_ic=0.015,
        max_walkforward_std_rank_ic=0.03,
        min_ok_folds=4,
        max_topk_drawdown=0.20,
    )

    assert status == "fail"
    assert "walkforward_topk_drawdown" in reason


def test_score_metrics_rejects_excessive_feature_drift():
    _score, status, reason = subject.score_metrics(
        {
            "holdout_rank_ic": 0.10,
            "holdout_long_short_spread": 0.012,
            "holdout_winrate_top": 0.67,
            "holdout_topk_net_return": 0.01,
            "holdout_feature_drift_psi_max": 0.55,
            "walkforward_avg_rank_ic": 0.04,
            "walkforward_std_rank_ic": 0.01,
            "walkforward_avg_spread": 0.010,
            "walkforward_avg_topk_net_return": 0.01,
            "walkforward_avg_topk_turnover": 0.2,
            "walkforward_worst_topk_drawdown": -0.05,
            "walkforward_worst_feature_drift_psi": 0.65,
            "ok_folds": 6,
        },
        min_holdout_rank_ic=0.04,
        min_holdout_spread=0.009,
        min_walkforward_avg_rank_ic=0.015,
        max_walkforward_std_rank_ic=0.03,
        min_ok_folds=4,
        max_topk_drawdown=0.20,
        max_feature_drift_psi=0.50,
    )

    assert status == "fail"
    assert "feature_drift_psi" in reason


def test_zero_trial_search_records_trial_summary_and_manifest(monkeypatch):
    conn = duck_mem()
    try:
        _seed_model_selection(conn)
        monkeypatch.setattr(subject, "load_panel_arrays", lambda *args, **kwargs: FakePanel())
        monkeypatch.setattr(
            subject,
            "evaluate_params",
            lambda *args, **kwargs: {
                "holdout_ic": 0.03,
                "holdout_rank_ic": 0.09,
                "holdout_long_short_spread": 0.012,
                "holdout_winrate_top": 0.67,
                "holdout_topk_net_return": 0.008,
                "holdout_topk_turnover": 0.2,
                "holdout_topk_max_drawdown": -0.01,
                "holdout_feature_drift_psi_avg": 0.04,
                "holdout_feature_drift_psi_max": 0.08,
                "walkforward_avg_rank_ic": 0.033,
                "walkforward_std_rank_ic": 0.029,
                "walkforward_avg_spread": 0.010,
                "walkforward_avg_topk_net_return": 0.007,
                "walkforward_avg_topk_turnover": 0.25,
                "walkforward_worst_topk_drawdown": -0.02,
                "walkforward_avg_feature_drift_psi": 0.05,
                "walkforward_worst_feature_drift_psi": 0.09,
                "ok_folds": 6,
                "fold_metrics": [
                    {
                        "fold_id": 1,
                        "rank_ic": 0.033,
                        "topk_net_return": 0.007,
                        "feature_drift_psi_max": 0.09,
                        "quality": "ok",
                    }
                ],
            },
        )

        result = subject.run_optuna_model_stability_search(
            conn,
            model_selection_run_id="selection_1",
            run_id="stable_search_unit",
            trials=0,
            train_days=4,
            valid_days=2,
            test_days=2,
            step_days=2,
            max_folds=2,
            max_topk_drawdown=0.20,
            storage_url=None,
        )
        trial = conn.execute(
            """
            SELECT status, model_family, topk_size, params_json, perf_summary_json,
                   holdout_topk_net_return,
                   walkforward_avg_topk_net_return, walkforward_worst_topk_drawdown,
                   holdout_feature_drift_psi_max, walkforward_worst_feature_drift_psi
              FROM mart_model_stability_search_trial
             WHERE run_id = 'stable_search_unit'
            """
        ).fetchone()
        summary = conn.execute(
            "SELECT best_params_json, config_json FROM mart_model_stability_search_summary WHERE run_id = 'stable_search_unit'"
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'stable_search_unit'"
        ).fetchone()

        assert result["best_status"] == "pass"
        assert result["model_family"] == "lightgbm"
        assert result["best_topk_size"] == 50
        assert trial["status"] == "pass"
        assert trial["model_family"] == "lightgbm"
        assert trial["topk_size"] == 50
        assert json.loads(trial["params_json"])["feature_pre_filter"] is False
        assert json.loads(trial["perf_summary_json"])["seconds"]["evaluate_params_s"] >= 0
        assert trial["holdout_topk_net_return"] == pytest.approx(0.008)
        assert trial["walkforward_avg_topk_net_return"] == pytest.approx(0.007)
        assert trial["walkforward_worst_topk_drawdown"] == pytest.approx(-0.02)
        assert trial["holdout_feature_drift_psi_max"] == pytest.approx(0.08)
        assert trial["walkforward_worst_feature_drift_psi"] == pytest.approx(0.09)
        assert json.loads(summary["best_params_json"])["min_data_in_leaf"] == 1000
        assert json.loads(summary["config_json"])["model_family"] == "lightgbm"
        assert json.loads(summary["config_json"])["thresholds"]["max_topk_drawdown"] == pytest.approx(0.20)
        assert json.loads(summary["config_json"])["thresholds"]["max_feature_drift_psi"] == pytest.approx(1.00)
        assert json.loads(summary["config_json"])["best_status"] == "pass"
        manifest_perf = json.loads(manifest["perf_summary_json"])
        assert manifest_perf["best_status"] == "pass"
        assert manifest_perf["best_topk_size"] == 50
        assert manifest_perf["timing"]["seconds"]["evaluate_params_s"] >= 0
        assert manifest_perf["ranker_cache"]["enabled"] is False
        assert manifest_perf["evaluation_cache"]["enabled"] is True
        assert manifest_perf["evaluation_cache"]["cache_feature_drift"] is True
        assert manifest_perf["best_metrics"]["walkforward_avg_topk_net_return"] == pytest.approx(0.007)
        assert manifest_perf["best_metrics"]["walkforward_worst_feature_drift_psi"] == pytest.approx(0.09)
    finally:
        conn.close()


def test_zero_trial_search_can_compare_topk_policy_choices(monkeypatch):
    conn = duck_mem()
    try:
        _seed_model_selection(conn)
        monkeypatch.setattr(subject, "load_panel_arrays", lambda *args, **kwargs: FakePanel())

        def fake_evaluate_params(*args, **kwargs):
            topk_size = int(kwargs["topk_size"])
            topk_bonus = topk_size / 10000.0
            return {
                "holdout_ic": 0.03,
                "holdout_rank_ic": 0.09,
                "holdout_long_short_spread": 0.012,
                "holdout_winrate_top": 0.67,
                "holdout_topk_net_return": topk_bonus,
                "holdout_topk_turnover": 0.2,
                "holdout_topk_max_drawdown": -0.01,
                "holdout_feature_drift_psi_avg": 0.04,
                "holdout_feature_drift_psi_max": 0.08,
                "walkforward_avg_rank_ic": 0.033,
                "walkforward_std_rank_ic": 0.029,
                "walkforward_avg_spread": 0.010,
                "walkforward_avg_topk_net_return": topk_bonus,
                "walkforward_avg_topk_turnover": 0.25,
                "walkforward_worst_topk_drawdown": -0.02,
                "walkforward_avg_feature_drift_psi": 0.05,
                "walkforward_worst_feature_drift_psi": 0.09,
                "ok_folds": 6,
                "fold_metrics": [],
            }

        monkeypatch.setattr(subject, "evaluate_params", fake_evaluate_params)

        result = subject.run_optuna_model_stability_search(
            conn,
            model_selection_run_id="selection_1",
            run_id="topk_policy_unit",
            trials=0,
            topk_size_choices=[20, 100],
            storage_url=None,
        )
        rows = conn.execute(
            """
            SELECT trial_number, topk_size
              FROM mart_model_stability_search_trial
             WHERE run_id = 'topk_policy_unit'
             ORDER BY trial_number
            """
        ).fetchall()
        config = conn.execute(
            "SELECT config_json FROM mart_model_stability_search_summary WHERE run_id = 'topk_policy_unit'"
        ).fetchone()["config_json"]

        assert [(row["trial_number"], row["topk_size"]) for row in rows] == [(0, 20), (1, 100)]
        assert result["best_topk_size"] == 100
        assert json.loads(config)["best_topk_size"] == 100
        assert json.loads(config)["topk_size_choices"] == [20, 100]
    finally:
        conn.close()


def test_zero_trial_search_records_ridge_model_family(monkeypatch):
    conn = duck_mem()
    try:
        _seed_model_selection(conn)
        monkeypatch.setattr(subject, "load_panel_arrays", lambda *args, **kwargs: FakePanel())
        monkeypatch.setattr(
            subject,
            "evaluate_params",
            lambda *args, **kwargs: {
                "holdout_ic": 0.02,
                "holdout_rank_ic": 0.05,
                "holdout_long_short_spread": 0.010,
                "holdout_winrate_top": 0.60,
                "holdout_topk_net_return": 0.004,
                "holdout_topk_turnover": 0.2,
                "holdout_topk_max_drawdown": -0.01,
                "holdout_feature_drift_psi_avg": 0.04,
                "holdout_feature_drift_psi_max": 0.08,
                "walkforward_avg_rank_ic": 0.025,
                "walkforward_std_rank_ic": 0.015,
                "walkforward_avg_spread": 0.006,
                "walkforward_avg_topk_net_return": 0.005,
                "walkforward_avg_topk_turnover": 0.2,
                "walkforward_worst_topk_drawdown": -0.02,
                "walkforward_avg_feature_drift_psi": 0.05,
                "walkforward_worst_feature_drift_psi": 0.09,
                "ok_folds": 4,
                "fold_metrics": [],
            },
        )

        result = subject.run_optuna_model_stability_search(
            conn,
            model_selection_run_id="selection_1",
            run_id="ridge_search_unit",
            trials=0,
            model_family="ridge",
            min_holdout_rank_ic=0.04,
            min_holdout_spread=0.005,
            min_walkforward_avg_rank_ic=0.015,
            max_walkforward_std_rank_ic=0.03,
            min_ok_folds=4,
            storage_url=None,
        )
        row = conn.execute(
            """
            SELECT model_family, params_json
              FROM mart_model_stability_search_trial
             WHERE run_id = 'ridge_search_unit'
            """
        ).fetchone()
        summary = conn.execute(
            "SELECT config_json FROM mart_model_stability_search_summary WHERE run_id = 'ridge_search_unit'"
        ).fetchone()

        assert result["model_family"] == "ridge"
        assert row["model_family"] == "ridge"
        assert json.loads(row["params_json"])["model_family"] == "ridge"
        assert json.loads(row["params_json"])["alpha"] == pytest.approx(1.0)
        assert json.loads(summary["config_json"])["model_family"] == "ridge"
    finally:
        conn.close()


def test_sqlite_storage_parent_is_created(tmp_path):
    storage_path = tmp_path / "nested" / "optuna" / "study.db"
    storage_url = f"sqlite:///{storage_path}"

    assert subject._ensure_sqlite_storage_parent(storage_url) == storage_url
    assert storage_path.parent.exists()
    assert subject._ensure_sqlite_storage_parent("sqlite:///:memory:") == "sqlite:///:memory:"
