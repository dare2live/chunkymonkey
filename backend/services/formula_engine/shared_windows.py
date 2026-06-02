"""Formula engine shared windows.

Common evaluation horizons and time windows are configuration-owned so scripts
do not each carry their own copy of the same tuple.
"""
from __future__ import annotations

from pathlib import Path

import yaml


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "formula_shared_windows.yaml"


def _load_yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def load_holding_days(path: Path | None = None) -> tuple[int, ...]:
    """Load the shared holding-day tuple from YAML."""
    raw_path = path or CONFIG_PATH
    raw = _load_yaml(raw_path)
    holding_days = raw.get("holding_days")
    if holding_days is None:
        raise ValueError(f"{raw_path.name}: missing holding_days")
    if not isinstance(holding_days, list):
        raise ValueError(f"{raw_path.name}: holding_days must be a list")

    try:
        values = tuple(int(v) for v in holding_days)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{raw_path.name}: holding_days must be integers") from exc

    if not values:
        raise ValueError(f"{raw_path.name}: holding_days must not be empty")
    return values


HOLDING_DAYS = load_holding_days()
