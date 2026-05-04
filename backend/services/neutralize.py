"""Neutralization helpers for alpha scores.

The public helpers accept mappings, sequences, or Series-like objects that
expose ``items()``. Return values are plain dicts for keyed inputs and lists
for positional inputs.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _coerce_indexed(values) -> tuple[list[Any], dict[Any, Any], str]:
    if values is None:
        return [], {}, "list"
    if isinstance(values, Mapping):
        return list(values.keys()), dict(values), "dict"
    if isinstance(values, (str, bytes)):
        return [0], {0: values}, "list"

    items = getattr(values, "items", None)
    if callable(items):
        pairs = list(items())
        return [key for key, _value in pairs], dict(pairs), "dict"

    if isinstance(values, Sequence):
        keys = list(range(len(values)))
        return keys, {idx: values[idx] for idx in keys}, "list"

    try:
        rows = list(values)
    except TypeError:
        return [0], {0: values}, "list"
    keys = list(range(len(rows)))
    return keys, {idx: rows[idx] for idx in keys}, "list"


def _coerce_by_key(values, default_keys: list[Any]) -> dict[Any, Any]:
    _keys, indexed, kind = _coerce_indexed(values)
    if kind == "dict":
        return indexed
    return {
        key: indexed.get(pos)
        for pos, key in enumerate(default_keys)
    }


def _format_result(keys: list[Any], values_by_key: dict[Any, Any], kind: str):
    if kind == "dict":
        return {key: values_by_key[key] for key in keys if key in values_by_key}
    return [values_by_key.get(key) for key in keys]


def neutralize_by_group(scores, groups):
    """Subtract the within-group mean from each non-missing score."""
    keys, score_by_key, kind = _coerce_indexed(scores)
    group_by_key = _coerce_by_key(groups, keys)
    values_by_group: dict[Any, list[float]] = defaultdict(list)

    for key in keys:
        score = score_by_key.get(key)
        group = group_by_key.get(key)
        if _is_missing(score) or _is_missing(group):
            continue
        values_by_group[group].append(float(score))

    means = {
        group: sum(values) / len(values)
        for group, values in values_by_group.items()
        if values
    }
    result = {}
    for key in keys:
        score = score_by_key.get(key)
        group = group_by_key.get(key)
        if _is_missing(score) or group not in means:
            continue
        result[key] = float(score) - means[group]
    return _format_result(keys, result, kind)


def _quantile_bins(items: list[tuple[Any, float]], n_bins: int) -> dict[Any, int]:
    if not items:
        return {}
    bins = max(1, int(n_bins or 1))
    sorted_items = sorted(items, key=lambda item: (item[1], str(item[0])))
    if sorted_items[0][1] == sorted_items[-1][1]:
        return {key: 0 for key, _value in sorted_items}
    total = len(sorted_items)
    return {
        key: min(int(pos * bins / total), bins - 1)
        for pos, (key, _value) in enumerate(sorted_items)
    }


def neutralize_by_quintile(scores, x, n_bins: int = 5):
    """Bucket by x rank and subtract the within-bucket mean."""
    keys, score_by_key, kind = _coerce_indexed(scores)
    x_by_key = _coerce_by_key(x, keys)

    scored_items = []
    for key in keys:
        score = score_by_key.get(key)
        x_value = x_by_key.get(key)
        if _is_missing(score) or _is_missing(x_value):
            continue
        scored_items.append((key, float(x_value)))

    bins_by_key = _quantile_bins(scored_items, n_bins)
    values_by_bin: dict[int, list[float]] = defaultdict(list)
    for key, bin_id in bins_by_key.items():
        values_by_bin[bin_id].append(float(score_by_key[key]))

    means = {
        bin_id: sum(values) / len(values)
        for bin_id, values in values_by_bin.items()
        if values
    }
    result = {
        key: float(score_by_key[key]) - means[bin_id]
        for key, bin_id in bins_by_key.items()
        if bin_id in means
    }
    return _format_result(keys, result, kind)


def neutralize(scores, industry=None, market_cap=None, market_cap_bins: int = 5):
    """Apply industry de-mean first, then market-cap bucket de-mean."""
    keys, _score_by_key, kind = _coerce_indexed(scores)
    out = scores
    if industry is not None:
        out = neutralize_by_group(out, industry)
    if market_cap is not None:
        out = neutralize_by_quintile(out, market_cap, n_bins=market_cap_bins)

    _out_keys, out_by_key, out_kind = _coerce_indexed(out)
    if kind == "dict" and out_kind != "dict":
        out_by_key = {key: out_by_key.get(pos) for pos, key in enumerate(keys)}
    elif kind == "list" and out_kind == "dict":
        out_by_key = {key: out_by_key.get(key) for key in keys}
    return _format_result(keys, out_by_key, kind)


def standardize(scores):
    """Sample z-score standardization for non-missing scores."""
    keys, score_by_key, kind = _coerce_indexed(scores)
    values = [
        float(score_by_key[key])
        for key in keys
        if not _is_missing(score_by_key.get(key))
    ]
    if not values:
        return _format_result(keys, score_by_key, kind)

    mean = sum(values) / len(values)
    if len(values) < 2:
        return _format_result(
            keys,
            {
                key: None if _is_missing(score_by_key.get(key)) else float(score_by_key[key]) - mean
                for key in keys
            },
            kind,
        )

    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    sd = math.sqrt(variance)
    if not sd:
        return _format_result(
            keys,
            {
                key: None if _is_missing(score_by_key.get(key)) else float(score_by_key[key]) - mean
                for key in keys
            },
            kind,
        )
    return _format_result(
        keys,
        {
            key: None if _is_missing(score_by_key.get(key)) else (float(score_by_key[key]) - mean) / sd
            for key in keys
        },
        kind,
    )
