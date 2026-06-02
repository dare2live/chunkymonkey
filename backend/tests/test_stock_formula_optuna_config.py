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
        "vol_bins": [[0, 0.8, "low"], [0.8, 99, "high"]],
        "amt_bins": [[0, 1.0, "flat"], [1.0, 99, "spike"]],
        "p60_bins": [[0, 0.5, "low"], [0.5, 99, "high"]],
    }
    cfg.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    loaded = load_stock_formula_optuna_config(cfg)

    assert loaded.min_n_per_bucket == 4
    assert loaded.min_win_high_conviction == pytest.approx(0.65)
    assert loaded.min_n_high_conviction == 6
    assert loaded.vol_bins == ((0.0, 0.8, "low"), (0.8, 99.0, "high"))
    assert loaded.amt_bins == ((0.0, 1.0, "flat"), (1.0, 99.0, "spike"))
    assert loaded.p60_bins == ((0.0, 0.5, "low"), (0.5, 99.0, "high"))


def test_stock_formula_optuna_config_missing_key_fails(tmp_path: Path) -> None:
    cfg = tmp_path / "stock_formula_optuna.yaml"
    payload = {
        "min_n_per_bucket": 4,
        "min_win_high_conviction": 0.65,
        "min_n_high_conviction": 6,
        "vol_bins": [[0, 0.8, "low"], [0.8, 99, "high"]],
        "amt_bins": [[0, 1.0, "flat"], [1.0, 99, "spike"]],
    }
    cfg.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="missing stock-formula optuna key p60_bins"):
        load_stock_formula_optuna_config(cfg)


def test_stock_formula_optuna_default_loaded_from_repo_config() -> None:
    assert DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG.min_n_per_bucket == 3
    assert DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG.vol_bins[0] == (0.0, 0.7, "缩量")
