from pathlib import Path

from services.feature_registry import load_feature_registry


def test_feature_registry_loads_groups_and_excludes_labels():
    registry = load_feature_registry()

    inputs = set(registry.model_input_columns())

    assert "ret_20d" in inputs
    assert "roe" not in inputs
    assert "rz_balance" not in inputs
    assert "qfii_count_qoq" not in inputs
    assert "regime_flag" not in inputs
    assert "kline_source_name" not in inputs
    assert "kline_source_tier" not in inputs
    assert "kline_is_fallback" not in inputs
    assert "forward_ret_5d" not in inputs
    assert "forward_ret_10d" not in inputs
    assert "forward_ret_20d" not in inputs
    assert "forward_ret_60d" not in inputs
    assert "forward_ret_90d" not in inputs
    assert "follow_net_return_5d" not in inputs
    assert "follow_net_return_10d" not in inputs
    assert "follow_net_return_20d" not in inputs
    assert "follow_net_return_60d" not in inputs
    assert "follow_net_return_90d" not in inputs
    assert "lhb_inst_buy_count_30d" not in inputs
    assert registry.group_pit_release_lag_days("labels") == 90
    assert registry.group_pit_release_lag_days("fundamentals") == 90
    assert registry.features["lhb_inst_buy_count_30d"].feature_role == "capital_attention_auxiliary"
    assert registry.features["lhb_inst_buy_count_30d"].panel_density == "dense_daily_encoded"
    assert registry.features["lhb_inst_buy_count_30d"].null_policy == "encode_no_event_as_zero_or_days_since"
    plan_spec = registry.features["shareholder_plan_increase_count_180d"]
    assert plan_spec.model_input is False
    assert plan_spec.feature_role == "capital_attention_auxiliary"
    assert plan_spec.source_tables == ("smartmoney.fact_shareholder_plan_tdx_f10",)
    assert plan_spec.source_event_date_column == "source_notice_date"
    assert plan_spec.source_available_date_column == "source_available_date"
    assert registry.features["ret_20d"].null_policy == "rolling_warmup_only"
    assert registry.features["close"].null_policy == "no_null"
    assert registry.features["close"].feature_role == "price_level_context"
    assert registry.features["close"].model_input is False
    assert "close" not in registry.model_input_columns()
    assert registry.label_columns() == (
        "forward_ret_5d",
        "forward_ret_10d",
        "forward_ret_20d",
        "forward_ret_60d",
        "forward_ret_90d",
        "follow_net_return_5d",
        "follow_net_return_10d",
        "follow_net_return_20d",
        "follow_net_return_60d",
        "follow_net_return_90d",
    )
    research_inputs = set(registry.model_input_columns(production_ready_only=False))
    assert "roe" in research_inputs
    assert "rz_balance" in research_inputs
    assert "qfii_count_qoq" in research_inputs


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
