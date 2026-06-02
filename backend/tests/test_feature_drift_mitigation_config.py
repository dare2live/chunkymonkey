import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.feature_drift_mitigation_config import (
    DEFAULT_FEATURE_DRIFT_MITIGATION_PANEL_CONFIG,
    load_feature_drift_mitigation_panel_config,
)


def test_feature_drift_mitigation_config_loads_from_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "feature_drift_mitigation_panel.yaml"
    payload = {
        "recommendations": ["exclude_or_transform_before_next_large_study"],
        "transform_types": ["xs_rank", "xs_winsor"],
        "regime_controls": ["regime_up", "regime_flat"],
        "market_control_features": ["hs300_ret_20d"],
        "winsor_low": 0.05,
        "winsor_high": 0.95,
        "bucket_count": 7,
        "min_root_cause_max_psi": 0.3,
    }
    cfg.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    loaded = load_feature_drift_mitigation_panel_config(cfg)

    assert loaded.recommendations == ("exclude_or_transform_before_next_large_study",)
    assert loaded.transform_types == ("xs_rank", "xs_winsor")
    assert loaded.regime_controls == ("regime_up", "regime_flat")
    assert loaded.market_control_features == ("hs300_ret_20d",)
    assert loaded.winsor_low == pytest.approx(0.05)
    assert loaded.winsor_high == pytest.approx(0.95)
    assert loaded.bucket_count == 7
    assert loaded.min_root_cause_max_psi == pytest.approx(0.3)


def test_feature_drift_mitigation_config_missing_key_fails(tmp_path: Path) -> None:
    cfg = tmp_path / "feature_drift_mitigation_panel.yaml"
    payload = {
        "recommendations": ["exclude_or_transform_before_next_large_study"],
        "transform_types": ["xs_rank", "xs_winsor"],
        "regime_controls": ["regime_up", "regime_flat"],
        "market_control_features": ["hs300_ret_20d"],
        "winsor_low": 0.05,
        "winsor_high": 0.95,
        "bucket_count": 7,
    }
    cfg.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="missing feature drift mitigation key min_root_cause_max_psi"):
        load_feature_drift_mitigation_panel_config(cfg)


def test_feature_drift_mitigation_default_loaded_from_repo_config() -> None:
    assert DEFAULT_FEATURE_DRIFT_MITIGATION_PANEL_CONFIG.bucket_count == 5
