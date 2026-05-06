"""Pickle-stable model artifacts used by training and daily scoring."""
from __future__ import annotations

import math
from typing import Any

import numpy as np


BLEND_CONTROL_PARAMS = {"model_family", "ridge_weight", "ridge_alpha"}
DEFAULT_RIDGE_WEIGHT = 0.70
DEFAULT_RIDGE_ALPHA = 1.0


def lightgbm_regression_params(params: dict[str, Any]) -> dict[str, Any]:
    """Return params accepted by LightGBM regression training/prediction."""

    return {
        key: value
        for key, value in dict(params or {}).items()
        if key not in BLEND_CONTROL_PARAMS
    }


def cross_sectional_zscore(values: Any, dates: Any | None = None) -> np.ndarray:
    """Z-score prediction values within each date; no dates means one batch."""

    scores = np.asarray(values, dtype=np.float64)
    if dates is None:
        date_keys = np.zeros(scores.shape[0], dtype=object)
    else:
        date_keys = np.asarray(dates, dtype=object).astype(str)
        if date_keys.shape[0] != scores.shape[0]:
            raise ValueError("dates length must match prediction length")

    out = np.zeros(scores.shape[0], dtype=np.float64)
    for day in np.unique(date_keys):
        idx = np.flatnonzero(date_keys == day)
        if idx.size == 0:
            continue
        day_values = scores[idx]
        finite = np.isfinite(day_values)
        if finite.sum() < 2:
            continue
        mean = float(np.mean(day_values[finite]))
        std = float(np.std(day_values[finite]))
        if not math.isfinite(std) or std < 1e-12:
            continue
        out[idx[finite]] = (day_values[finite] - mean) / std
    return out


class CrossSectionalLightGBMRidgeBlend:
    """LightGBM + Ridge artifact with the same predict surface as a Booster.

    `run_daily_topk.py` scores one snapshot date at a time, so omitting `dates`
    makes the whole prediction batch a single cross-section. Walk-forward and
    holdout validation pass dates explicitly to keep per-date normalization.
    """

    def __init__(
        self,
        *,
        lightgbm_model: Any,
        ridge_model: Any,
        ridge_weight: float = DEFAULT_RIDGE_WEIGHT,
        ridge_alpha: float = DEFAULT_RIDGE_ALPHA,
        feature_names: list[str] | None = None,
    ) -> None:
        self.lightgbm_model = lightgbm_model
        self.ridge_model = ridge_model
        self.ridge_weight = min(max(float(ridge_weight), 0.0), 1.0)
        self.ridge_alpha = float(ridge_alpha)
        self.feature_names = list(feature_names or [])
        self.model_family = "lightgbm_ridge_blend"

    def predict(self, data: Any, dates: Any | None = None, **kwargs: Any) -> np.ndarray:
        lightgbm_pred = np.asarray(self.lightgbm_model.predict(data, **kwargs), dtype=np.float64)
        ridge_pred = np.asarray(self.ridge_model.predict(data), dtype=np.float64)
        lgb_z = cross_sectional_zscore(lightgbm_pred, dates)
        ridge_z = cross_sectional_zscore(ridge_pred, dates)
        return (1.0 - self.ridge_weight) * lgb_z + self.ridge_weight * ridge_z

    def feature_importance(self, importance_type: str = "gain") -> np.ndarray:
        if hasattr(self.lightgbm_model, "feature_importance"):
            return np.asarray(
                self.lightgbm_model.feature_importance(importance_type=importance_type)
            )
        return np.zeros(len(self.feature_names), dtype=np.float64)

    def num_feature(self) -> int:
        if hasattr(self.lightgbm_model, "num_feature"):
            return int(self.lightgbm_model.num_feature())
        return len(self.feature_names)
