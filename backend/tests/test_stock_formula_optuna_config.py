import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.stock_formula_optuna_config import DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG, load_stock_formula_optuna_config


def test_stock_formula_optuna_config_loads_from_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "stock_formula_optuna.yaml"
    payload = {
        "min_n_per_bucket": 4,
        "min_win_high_conviction": 0.65,
        "min_n_high_conviction": 6,
    }
    cfg.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    loaded = load_stock_formula_optuna_config(cfg)

    assert loaded.min_n_per_bucket == 4
    assert loaded.min_win_high_conviction == pytest.approx(0.65)
    assert loaded.min_n_high_conviction == 6


def test_stock_formula_optuna_config_missing_key_fails(tmp_path: Path) -> None:
    cfg = tmp_path / "stock_formula_optuna.yaml"
    payload = {
        "min_n_per_bucket": 4,
        "min_win_high_conviction": 0.65,
    }
    cfg.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="missing stock-formula optuna key min_n_high_conviction"):
        load_stock_formula_optuna_config(cfg)


def test_stock_formula_optuna_default_loaded_from_repo_config() -> None:
    assert DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG.min_n_per_bucket == 3
