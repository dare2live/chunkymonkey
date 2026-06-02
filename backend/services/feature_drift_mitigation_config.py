"""Shared config for feature drift mitigation panel defaults."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "feature_drift_mitigation_panel.yaml"


@dataclass(frozen=True)
class FeatureDriftMitigationPanelConfig:
    recommendations: tuple[str, ...]
    transform_types: tuple[str, ...]
    regime_controls: tuple[str, ...]
    market_control_features: tuple[str, ...]
    winsor_low: float
    winsor_high: float
    bucket_count: int
    min_root_cause_max_psi: float


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def _require_str_list(raw: dict[str, Any], key: str, raw_path: Path) -> tuple[str, ...]:
    value = raw[key]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{raw_path.name}: {key} must be a non-empty list")
    out: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, str) or not item.strip():
            raise ValueError(f"{raw_path.name}: {key}[{idx}] must be a non-empty string")
        item = item.strip()
        if item in seen:
            raise ValueError(f"{raw_path.name}: {key}[{idx}] duplicates {item}")
        seen.add(item)
        out.append(item)
    return tuple(out)


def _require_int(raw: dict[str, Any], key: str, raw_path: Path) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{raw_path.name}: {key} must be an integer")
    return value


def _require_float(raw: dict[str, Any], key: str, raw_path: Path) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{raw_path.name}: {key} must be numeric")
    return float(value)


def load_feature_drift_mitigation_panel_config(path: Path | None = None) -> FeatureDriftMitigationPanelConfig:
    raw_path = path or CONFIG_PATH
    raw = _load_yaml(raw_path)
    try:
        recommendations = _require_str_list(raw, "recommendations", raw_path)
        transform_types = _require_str_list(raw, "transform_types", raw_path)
        regime_controls = _require_str_list(raw, "regime_controls", raw_path)
        market_control_features = _require_str_list(raw, "market_control_features", raw_path)
        winsor_low = _require_float(raw, "winsor_low", raw_path)
        winsor_high = _require_float(raw, "winsor_high", raw_path)
        bucket_count = _require_int(raw, "bucket_count", raw_path)
        min_root_cause_max_psi = _require_float(raw, "min_root_cause_max_psi", raw_path)
    except KeyError as exc:
        raise ValueError(f"{raw_path.name}: missing feature drift mitigation key {exc.args[0]}") from exc
    if not 0.0 <= winsor_low < winsor_high <= 1.0:
        raise ValueError(f"{raw_path.name}: winsor_low/high must satisfy 0 <= low < high <= 1")
    if bucket_count <= 0:
        raise ValueError(f"{raw_path.name}: bucket_count must be positive")
    if min_root_cause_max_psi <= 0.0:
        raise ValueError(f"{raw_path.name}: min_root_cause_max_psi must be positive")
    return FeatureDriftMitigationPanelConfig(
        recommendations=recommendations,
        transform_types=transform_types,
        regime_controls=regime_controls,
        market_control_features=market_control_features,
        winsor_low=winsor_low,
        winsor_high=winsor_high,
        bucket_count=bucket_count,
        min_root_cause_max_psi=min_root_cause_max_psi,
    )


DEFAULT_FEATURE_DRIFT_MITIGATION_PANEL_CONFIG = load_feature_drift_mitigation_panel_config()
