"""Shared config for stock-formula optuna/grid-search thresholds."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "stock_formula_optuna.yaml"


@dataclass(frozen=True)
class StockFormulaOptunaConfig:
    min_n_per_bucket: int
    min_win_high_conviction: float
    min_n_high_conviction: int


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


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


def load_stock_formula_optuna_config(path: Path | None = None) -> StockFormulaOptunaConfig:
    raw_path = path or CONFIG_PATH
    raw = _load_yaml(raw_path)
    try:
        min_n_per_bucket = _require_int(raw, "min_n_per_bucket", raw_path)
        min_win_high_conviction = _require_float(raw, "min_win_high_conviction", raw_path)
        min_n_high_conviction = _require_int(raw, "min_n_high_conviction", raw_path)
    except KeyError as exc:
        raise ValueError(f"{raw_path.name}: missing stock-formula optuna key {exc.args[0]}") from exc
    if min_n_per_bucket <= 0:
        raise ValueError(f"{raw_path.name}: min_n_per_bucket must be positive")
    if not 0.0 <= min_win_high_conviction <= 1.0:
        raise ValueError(f"{raw_path.name}: min_win_high_conviction must be between 0 and 1")
    if min_n_high_conviction <= 0:
        raise ValueError(f"{raw_path.name}: min_n_high_conviction must be positive")
    return StockFormulaOptunaConfig(
        min_n_per_bucket=min_n_per_bucket,
        min_win_high_conviction=min_win_high_conviction,
        min_n_high_conviction=min_n_high_conviction,
    )


DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG = load_stock_formula_optuna_config()
