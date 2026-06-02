"""Shared stock feature bins.

These are reused by multiple scripts that bucket volume, amount, and p60
signals. Keep the bin policy in one config-owned place instead of repeating
the same literals in each script.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "shared_feature_bins.yaml"


@dataclass(frozen=True)
class SharedFeatureBinsConfig:
    vol_bins: tuple[tuple[float, float, str], ...]
    amt_bins: tuple[tuple[float, float, str], ...]
    p60_bins: tuple[tuple[float, float, str], ...]


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def _load_bins(raw: dict[str, Any], key: str, raw_path: Path) -> tuple[tuple[float, float, str], ...]:
    value = raw[key]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{raw_path.name}: {key} must be a non-empty list")
    bins: list[tuple[float, float, str]] = []
    for idx, item in enumerate(value):
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise ValueError(f"{raw_path.name}: {key}[{idx}] must be a 3-item list")
        lo_raw, hi_raw, label_raw = item
        if isinstance(lo_raw, bool) or not isinstance(lo_raw, (int, float)):
            raise ValueError(f"{raw_path.name}: {key}[{idx}].lo must be numeric")
        if isinstance(hi_raw, bool) or not isinstance(hi_raw, (int, float)):
            raise ValueError(f"{raw_path.name}: {key}[{idx}].hi must be numeric")
        if isinstance(label_raw, bool) or not isinstance(label_raw, str):
            raise ValueError(f"{raw_path.name}: {key}[{idx}].label must be a string")
        lo = float(lo_raw)
        hi = float(hi_raw)
        if hi <= lo:
            raise ValueError(f"{raw_path.name}: {key}[{idx}] upper bound must exceed lower bound")
        bins.append((lo, hi, label_raw))
    return tuple(bins)


def load_shared_feature_bins_config(path: Path | None = None) -> SharedFeatureBinsConfig:
    raw_path = path or CONFIG_PATH
    raw = _load_yaml(raw_path)
    try:
        vol_bins = _load_bins(raw, "vol_bins", raw_path)
        amt_bins = _load_bins(raw, "amt_bins", raw_path)
        p60_bins = _load_bins(raw, "p60_bins", raw_path)
    except KeyError as exc:
        raise ValueError(f"{raw_path.name}: missing shared feature bins key {exc.args[0]}") from exc
    return SharedFeatureBinsConfig(vol_bins=vol_bins, amt_bins=amt_bins, p60_bins=p60_bins)


DEFAULT_SHARED_FEATURE_BINS_CONFIG = load_shared_feature_bins_config()
