"""Typed access to the current Tier0B taxonomy namespace policy."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "taxonomy.yaml"


def load_taxonomy_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg = yaml.safe_load(Path(path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
    if cfg.get("cross_namespace_fallback") != "forbidden":
        raise ValueError("taxonomy cross_namespace_fallback must be forbidden")
    namespaces = cfg.get("namespaces")
    if not isinstance(namespaces, dict) or not namespaces:
        raise ValueError("taxonomy namespaces must be a non-empty mapping")
    return cfg


def source_level_map(namespace: str, path: str | Path | None = None) -> dict[str, str]:
    cfg = load_taxonomy_config(path)
    entry = cfg["namespaces"].get(namespace)
    if not isinstance(entry, dict):
        raise KeyError(f"unknown taxonomy namespace: {namespace}")
    mapping = entry.get("source_level_map")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError(f"taxonomy namespace has no source_level_map: {namespace}")
    out = {str(source): str(level) for source, level in mapping.items()}
    if set(out.values()) != {"L1", "L2", "L3"}:
        raise ValueError(f"taxonomy level map must cover L1/L2/L3 exactly: {namespace}")
    return out


def source_content_type(namespace: str, path: str | Path | None = None) -> str:
    """Return the provider content-type label owned by one taxonomy namespace."""
    cfg = load_taxonomy_config(path)
    entry = cfg["namespaces"].get(namespace)
    if not isinstance(entry, dict):
        raise KeyError(f"unknown taxonomy namespace: {namespace}")
    value = entry.get("source_content_type")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"taxonomy namespace has no source_content_type: {namespace}")
    return value


def source_index_type(namespace: str, path: str | Path | None = None) -> str:
    """Return the provider dc_index type owned by one DC namespace."""
    cfg = load_taxonomy_config(path)
    entry = cfg["namespaces"].get(namespace)
    if not isinstance(entry, dict):
        raise KeyError(f"unknown taxonomy namespace: {namespace}")
    value = entry.get("source_index_type")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"taxonomy namespace has no source_index_type: {namespace}")
    return value


def current_snapshot_quality_floor(
    namespace: str,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Return typed fail-closed coverage floors for one current-snapshot namespace."""
    cfg = load_taxonomy_config(path)
    entry = cfg["namespaces"].get(namespace)
    if not isinstance(entry, dict):
        raise KeyError(f"unknown taxonomy namespace: {namespace}")
    floor = entry.get("current_snapshot_quality_floor")
    if not isinstance(floor, dict):
        raise ValueError(f"taxonomy namespace has no current snapshot quality floor: {namespace}")
    measured_trade_date = floor.get("measured_trade_date")
    if not isinstance(measured_trade_date, str) or len(measured_trade_date) != 8:
        raise ValueError(f"invalid measured_trade_date for taxonomy namespace: {namespace}")
    numeric = {
        key: value
        for key, value in floor.items()
        if key.startswith("min_") and key != "min_nodes_by_level"
    }
    if not numeric or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
                          for value in numeric.values()):
        raise ValueError(f"invalid taxonomy quality floor values: {namespace}")
    if namespace == "dc_industry":
        levels = floor.get("min_nodes_by_level")
        if not isinstance(levels, dict) or set(levels) != {"L1", "L2", "L3"}:
            raise ValueError("dc_industry quality floor must define L1/L2/L3")
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in levels.values()):
            raise ValueError("dc_industry level floors must be positive integers")
    return floor


__all__ = [
    "CONFIG_PATH",
    "current_snapshot_quality_floor",
    "load_taxonomy_config",
    "source_content_type",
    "source_index_type",
    "source_level_map",
]
