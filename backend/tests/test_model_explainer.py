from __future__ import annotations

import numpy as np
import pytest

from services.model_artifacts import CrossSectionalLightGBMRidgeBlend
from services.model_explainer import explain_prediction_batch


class FakeLightGBM:
    def __init__(self, coef, bias=0.0):
        self.coef = np.asarray(coef, dtype=float)
        self.bias = float(bias)

    def predict(self, data, pred_contrib=False, **_kwargs):
        x = np.asarray(data, dtype=float)
        parts = x * self.coef
        if pred_contrib:
            bias = np.full((x.shape[0], 1), self.bias)
            return np.column_stack([parts, bias])
        return parts.sum(axis=1) + self.bias

    def feature_importance(self, importance_type="gain"):
        return np.abs(self.coef)

    def num_feature(self):
        return len(self.coef)


class FakeRidge:
    def __init__(self, coef, intercept=0.0):
        self.coef_ = np.asarray(coef, dtype=float)
        self.intercept_ = float(intercept)

    def predict(self, data):
        x = np.asarray(data, dtype=float)
        return x @ self.coef_ + self.intercept_


def test_lightgbm_contributions_are_additive():
    model = FakeLightGBM([2.0, -1.0], bias=0.5)
    x = [[1.0, 3.0], [2.0, 4.0]]
    scores = model.predict(x)

    result = explain_prediction_batch(model, x, ["a", "b"], scores=scores)

    assert result["status"] == "exact"
    assert result["max_abs_error"] == pytest.approx(0.0)
    assert result["rows"][0]["base_value"] == pytest.approx(0.5)
    assert result["rows"][0]["features"][0]["contribution"] == pytest.approx(2.0)
    assert result["rows"][0]["features"][1]["contribution"] == pytest.approx(-3.0)


def test_blend_contributions_match_cross_sectional_zscore_prediction():
    lgb = FakeLightGBM([1.0, 0.0], bias=0.0)
    ridge = FakeRidge([0.0, 2.0], intercept=0.0)
    model = CrossSectionalLightGBMRidgeBlend(
        lightgbm_model=lgb,
        ridge_model=ridge,
        ridge_weight=0.5,
        feature_names=["a", "b"],
    )
    x = [[1.0, 1.0], [2.0, 2.0], [3.0, 4.0]]
    scores = model.predict(x, dates=["d1", "d1", "d1"])

    result = explain_prediction_batch(
        model,
        x,
        ["a", "b"],
        dates=["d1", "d1", "d1"],
        scores=scores,
    )

    assert result["status"] == "exact"
    assert result["max_abs_error"] < 1e-12
    for row, score in zip(result["rows"], scores):
        assert row["sum_contributions"] == pytest.approx(score)
