from __future__ import annotations

import pickle

import numpy as np
import pytest

from services.model_artifacts import (
    CrossSectionalLightGBMRidgeBlend,
    cross_sectional_zscore,
    lightgbm_regression_params,
)


class _FakeLightGBM:
    def __init__(self, pred: list[float], importances: list[float] | None = None) -> None:
        self.pred = np.asarray(pred, dtype=np.float64)
        self.importances = np.asarray(importances or [1.0, 2.0], dtype=np.float64)

    def predict(self, data, **_kwargs):
        return self.pred[: len(data)]

    def feature_importance(self, importance_type: str = "gain"):
        return self.importances

    def num_feature(self):
        return len(self.importances)


class _FakeRidge:
    def __init__(self, pred: list[float]) -> None:
        self.pred = np.asarray(pred, dtype=np.float64)

    def predict(self, data):
        return self.pred[: len(data)]


def test_cross_sectional_zscore_groups_by_date() -> None:
    out = cross_sectional_zscore(
        [1.0, 2.0, 10.0, 20.0],
        ["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"],
    )

    assert out.tolist() == pytest.approx([-1.0, 1.0, -1.0, 1.0])


def test_lightgbm_ridge_blend_matches_daily_normalized_formula() -> None:
    model = CrossSectionalLightGBMRidgeBlend(
        lightgbm_model=_FakeLightGBM([1.0, 2.0, 10.0, 20.0], [4.0, 2.0]),
        ridge_model=_FakeRidge([4.0, 3.0, 40.0, 30.0]),
        ridge_weight=0.75,
        feature_names=["a", "b"],
    )

    pred = model.predict(
        [[0.0], [0.0], [0.0], [0.0]],
        dates=["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"],
        predict_disable_shape_check=False,
    )

    assert pred.tolist() == pytest.approx([0.5, -0.5, 0.5, -0.5])
    assert model.num_feature() == 2
    assert model.feature_importance().tolist() == pytest.approx([4.0, 2.0])


def test_lightgbm_ridge_blend_pickle_roundtrip() -> None:
    model = CrossSectionalLightGBMRidgeBlend(
        lightgbm_model=_FakeLightGBM([1.0, 2.0]),
        ridge_model=_FakeRidge([2.0, 1.0]),
        ridge_weight=0.5,
        feature_names=["a", "b"],
    )

    loaded = pickle.loads(pickle.dumps(model))

    assert loaded.predict([[0.0], [0.0]]).tolist() == pytest.approx([0.0, 0.0])
    assert loaded.model_family == "lightgbm_ridge_blend"


def test_lightgbm_regression_params_strips_blend_controls() -> None:
    params = lightgbm_regression_params(
        {"model_family": "lightgbm_ridge_blend", "ridge_weight": 0.7, "num_leaves": 31}
    )

    assert params == {"num_leaves": 31}
