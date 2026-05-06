"""Model explanation helpers for daily recommendation scoring."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ContributionRow:
    feature_name: str
    contribution: float


def _as_matrix(data: Any) -> np.ndarray:
    matrix = np.asarray(data, dtype=np.float64)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2:
        raise ValueError("data must be a 2D feature matrix")
    return matrix


def _date_keys(n_rows: int, dates: Any | None) -> np.ndarray:
    if dates is None:
        return np.zeros(n_rows, dtype=object)
    keys = np.asarray(dates, dtype=object).astype(str)
    if keys.shape[0] != n_rows:
        raise ValueError("dates length must match prediction rows")
    return keys


def _safe_array(values: Any, n_rows: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim == 0:
        arr = np.repeat(float(arr), n_rows)
    if arr.shape[0] != n_rows:
        raise ValueError("score length must match prediction rows")
    return arr


def _split_pred_contrib(pred_contrib: Any, n_features: int) -> tuple[np.ndarray, np.ndarray]:
    contrib = np.asarray(pred_contrib, dtype=np.float64)
    if contrib.ndim != 2 or contrib.shape[1] != n_features + 1:
        raise ValueError("pred_contrib output must be shaped (rows, features + bias)")
    return contrib[:, :n_features], contrib[:, n_features]


def _zscore_components(
    feature_contribs: np.ndarray,
    bias_values: np.ndarray,
    raw_scores: np.ndarray,
    dates: Any | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Transform raw additive parts through per-date cross-sectional z-score.

    For score = bias + sum(feature_contrib), z(score) =
    (bias - group_mean) / group_std + sum(feature_contrib / group_std).
    This is exact for the z-score transformation used by blend artifacts.
    """

    n_rows = raw_scores.shape[0]
    out_features = np.zeros_like(feature_contribs, dtype=np.float64)
    out_bias = np.zeros(n_rows, dtype=np.float64)
    keys = _date_keys(n_rows, dates)
    for key in np.unique(keys):
        idx = np.flatnonzero(keys == key)
        if idx.size == 0:
            continue
        group_scores = raw_scores[idx]
        finite = np.isfinite(group_scores)
        if finite.sum() < 2:
            continue
        mean = float(np.mean(group_scores[finite]))
        std = float(np.std(group_scores[finite]))
        if not math.isfinite(std) or std < 1e-12:
            continue
        valid_idx = idx[finite]
        out_features[valid_idx, :] = feature_contribs[valid_idx, :] / std
        out_bias[valid_idx] = (bias_values[valid_idx] - mean) / std
    return out_features, out_bias


def _linear_contrib_parts(model: Any, matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    coef = np.asarray(getattr(model, "coef_"), dtype=np.float64)
    if coef.ndim > 1:
        coef = coef.reshape(-1)
    if coef.shape[0] != matrix.shape[1]:
        raise ValueError("linear model coefficient count does not match feature count")
    intercept_raw = getattr(model, "intercept_", 0.0)
    intercept_arr = np.asarray(intercept_raw, dtype=np.float64)
    intercept = float(intercept_arr.reshape(-1)[0]) if intercept_arr.size else 0.0
    feature_contribs = matrix * coef
    bias = np.repeat(intercept, matrix.shape[0])
    scores = feature_contribs.sum(axis=1) + bias
    return feature_contribs, bias, scores


def _lightgbm_contrib_parts(model: Any, matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pred_contrib = model.predict(
        matrix,
        pred_contrib=True,
        predict_disable_shape_check=False,
    )
    feature_contribs, bias = _split_pred_contrib(pred_contrib, matrix.shape[1])
    scores = feature_contribs.sum(axis=1) + bias
    return feature_contribs, bias, scores


def _is_blend_model(model: Any) -> bool:
    return hasattr(model, "lightgbm_model") and hasattr(model, "ridge_model")


def _explain_blend(model: Any, matrix: np.ndarray, dates: Any | None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lgb_features, lgb_bias, lgb_raw_scores = _lightgbm_contrib_parts(model.lightgbm_model, matrix)
    ridge_features, ridge_bias, ridge_raw_scores = _linear_contrib_parts(model.ridge_model, matrix)
    lgb_z_features, lgb_z_bias = _zscore_components(lgb_features, lgb_bias, lgb_raw_scores, dates)
    ridge_z_features, ridge_z_bias = _zscore_components(ridge_features, ridge_bias, ridge_raw_scores, dates)
    ridge_weight = min(max(float(getattr(model, "ridge_weight", 0.70)), 0.0), 1.0)
    feature_contribs = (1.0 - ridge_weight) * lgb_z_features + ridge_weight * ridge_z_features
    bias = (1.0 - ridge_weight) * lgb_z_bias + ridge_weight * ridge_z_bias
    scores = feature_contribs.sum(axis=1) + bias
    return feature_contribs, bias, scores


def explain_prediction_batch(
    model: Any,
    data: Any,
    feature_names: list[str],
    *,
    dates: Any | None = None,
    scores: Any | None = None,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Return exact additive feature contributions when supported.

    Supported families:
    - LightGBM Booster/sklearn models exposing ``predict(pred_contrib=True)``.
    - Linear/Ridge-like estimators exposing ``coef_`` and optional
      ``intercept_``.
    - ``CrossSectionalLightGBMRidgeBlend`` artifacts used by this project.
    """

    matrix = _as_matrix(data)
    if len(feature_names) != matrix.shape[1]:
        raise ValueError("feature_names length must match feature matrix width")

    try:
        if _is_blend_model(model):
            feature_contribs, bias, additive_scores = _explain_blend(model, matrix, dates)
            model_family = getattr(model, "model_family", "lightgbm_ridge_blend")
        elif hasattr(model, "coef_"):
            feature_contribs, bias, raw_scores = _linear_contrib_parts(model, matrix)
            additive_scores = raw_scores
            model_family = model.__class__.__name__
        else:
            feature_contribs, bias, raw_scores = _lightgbm_contrib_parts(model, matrix)
            additive_scores = raw_scores
            model_family = getattr(model, "model_family", model.__class__.__name__)
    except Exception as exc:
        return {
            "status": "unsupported",
            "model_family": getattr(model, "model_family", model.__class__.__name__),
            "reason": f"{type(exc).__name__}: {exc}",
            "rows": [],
            "max_abs_error": None,
        }

    expected_scores = _safe_array(scores, matrix.shape[0]) if scores is not None else additive_scores
    rows = []
    max_abs_error = 0.0
    for idx in range(matrix.shape[0]):
        contribs = [
            ContributionRow(name, float(feature_contribs[idx, col_idx]))
            for col_idx, name in enumerate(feature_names)
        ]
        add_sum = float(bias[idx] + feature_contribs[idx, :].sum())
        score = float(expected_scores[idx])
        error = abs(add_sum - score)
        max_abs_error = max(max_abs_error, error)
        abs_total = float(np.sum(np.abs(feature_contribs[idx, :])))
        rows.append(
            {
                "base_value": float(bias[idx]),
                "score": score,
                "sum_contributions": add_sum,
                "additivity_error": error,
                "features": [
                    {
                        "feature_name": item.feature_name,
                        "contribution": item.contribution,
                        "contribution_pct": (
                            abs(item.contribution) / abs_total if abs_total > 1e-12 else 0.0
                        ),
                        "direction": "positive" if item.contribution > 0 else (
                            "negative" if item.contribution < 0 else "flat"
                        ),
                    }
                    for item in contribs
                ],
            }
        )

    return {
        "status": "exact" if max_abs_error <= tolerance else "failed_additivity",
        "model_family": model_family,
        "reason": None if max_abs_error <= tolerance else f"max_abs_error={max_abs_error:.6g}",
        "rows": rows,
        "max_abs_error": max_abs_error,
    }


def top_contributors(explanation_row: dict[str, Any], *, limit: int = 8) -> list[dict[str, Any]]:
    features = explanation_row.get("features") if isinstance(explanation_row, dict) else []
    if not isinstance(features, list):
        return []
    ranked = sorted(
        [item for item in features if isinstance(item, dict)],
        key=lambda item: abs(float(item.get("contribution") or 0.0)),
        reverse=True,
    )
    return ranked[: int(limit)]
