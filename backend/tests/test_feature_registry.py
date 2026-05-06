from pathlib import Path

from services.feature_registry import load_feature_registry


def test_feature_registry_loads_groups_and_excludes_labels():
    registry = load_feature_registry()

    inputs = set(registry.model_input_columns())

    assert "ret_20d" in inputs
    assert "roe" in inputs
    assert "regime_flag" not in inputs
    assert "kline_source_name" not in inputs
    assert "kline_source_tier" not in inputs
    assert "kline_is_fallback" not in inputs
    assert "forward_ret_5d" not in inputs
    assert "forward_ret_10d" not in inputs
    assert "forward_ret_20d" not in inputs
    assert "forward_ret_60d" not in inputs
    assert "forward_ret_90d" not in inputs
    assert registry.group_pit_release_lag_days("labels") == 90
    assert registry.group_pit_release_lag_days("fundamentals") == 90
    assert registry.label_columns() == (
        "forward_ret_5d",
        "forward_ret_10d",
        "forward_ret_20d",
        "forward_ret_60d",
        "forward_ret_90d",
    )


def test_feature_registry_respects_disabled_feature(tmp_path: Path):
    config = tmp_path / "feature_registry.yaml"
    config.write_text(
        """
version: 1
model_input_excluded:
  - id
groups:
  base:
    enabled: true
    production_ready: true
    features:
      - keep_me
      - drop_me:
          enabled: false
      - label_me:
          label: true
          model_input: false
""",
        encoding="utf-8",
    )

    registry = load_feature_registry(config)

    assert registry.model_input_columns() == ("keep_me",)
    assert registry.model_input_columns(include_disabled=True) == ("keep_me", "drop_me")
    assert registry.label_columns() == ("label_me",)
