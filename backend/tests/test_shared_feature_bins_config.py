import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.shared_feature_bins_config import DEFAULT_SHARED_FEATURE_BINS_CONFIG, load_shared_feature_bins_config


def test_shared_feature_bins_config_loads_from_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "shared_feature_bins.yaml"
    payload = {
        "vol_bins": [[0, 0.8, "low"], [0.8, 99, "high"]],
        "amt_bins": [[0, 1.0, "flat"], [1.0, 99, "spike"]],
        "p60_bins": [[0, 0.5, "low"], [0.5, 99, "high"]],
    }
    cfg.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    loaded = load_shared_feature_bins_config(cfg)

    assert loaded.vol_bins == ((0.0, 0.8, "low"), (0.8, 99.0, "high"))
    assert loaded.amt_bins == ((0.0, 1.0, "flat"), (1.0, 99.0, "spike"))
    assert loaded.p60_bins == ((0.0, 0.5, "low"), (0.5, 99.0, "high"))


def test_shared_feature_bins_missing_key_fails(tmp_path: Path) -> None:
    cfg = tmp_path / "shared_feature_bins.yaml"
    payload = {
        "vol_bins": [[0, 0.8, "low"], [0.8, 99, "high"]],
        "amt_bins": [[0, 1.0, "flat"], [1.0, 99, "spike"]],
    }
    cfg.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="missing shared feature bins key p60_bins"):
        load_shared_feature_bins_config(cfg)


def test_shared_feature_bins_default_loaded_from_repo_config() -> None:
    assert DEFAULT_SHARED_FEATURE_BINS_CONFIG.vol_bins[0] == (0.0, 0.7, "缩量")
