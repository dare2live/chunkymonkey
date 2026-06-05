from __future__ import annotations

from pathlib import Path

import pytest

from services.stage_opt_candidate_supply import load_stage_opt_candidate_supply_contract


def test_default_stage_opt_candidate_supply_contract_declares_source_semantics() -> None:
    contract = load_stage_opt_candidate_supply_contract()

    assert contract.version == 1
    assert contract.allowed_stage_set == {"1", "1.5", "2", "3", "4"}
    assert contract.min_signals_per_key == 5
    assert contract.to_report()["readiness"] == {"min_signals_per_key": 5}
    trigger_source = contract.source("fact_technical_trigger")
    macd_state_source = contract.source("mart_macd_state_history")
    assert trigger_source.semantic_role == "trade_trigger"
    assert trigger_source.required_columns == ("stock_code", "date", "formula_id", "formula_variant")
    assert trigger_source.join_checks[0].table == "sm.fact_signal_context"
    assert trigger_source.join_checks[0].source_columns == ("stock_code", "date")
    assert trigger_source.join_checks[0].target_columns == ("stock_code", "date")
    assert trigger_source.join_checks[0].required_columns == ("stock_code", "date", "technical_stage")
    assert trigger_source.join_checks[1].table == "v_price_kline_qfq"
    assert trigger_source.join_checks[1].source_columns == ("stock_code", "date")
    assert trigger_source.join_checks[1].target_columns == ("code", "date")
    assert trigger_source.join_checks[1].required_columns == ("code", "date", "freq", "adjust")
    assert trigger_source.include_for_formula_filter(["reversal_1m_deep"])
    assert macd_state_source.semantic_role == "diagnostic_state_history"
    assert macd_state_source.required_columns == ("stock_code", "date", "formula_id", "formula_variant", "state")
    assert macd_state_source.include_formula_ids == ("macd_golden_cross",)
    assert macd_state_source.include_for_formula_filter(None)
    assert macd_state_source.include_for_formula_filter(["macd_golden_cross"])
    assert not macd_state_source.include_for_formula_filter(["reversal_1m_deep"])
    trigger_source.require_consumer("audit_stage_opt_candidate_supply")
    macd_state_source.require_consumer("audit_stage_opt_candidate_supply")
    with pytest.raises(ValueError, match="does not allow consumer"):
        macd_state_source.require_consumer("optimize_per_stock_stage_strategy")


def test_formula_scopes_combine_runtime_registry_with_config_overrides() -> None:
    contract = load_stage_opt_candidate_supply_contract()

    assert contract.formula_scopes(
        "gs_raw_buy",
        live_formula_ids=("gs_raw_buy",),
        registered_formula_ids=("gs_raw_buy",),
    ) == ["live", "research_challenger"]
    assert contract.formula_scopes(
        "held_back_formula",
        live_formula_ids=(),
        registered_formula_ids=("held_back_formula",),
    ) == ["registered_non_live"]
    assert contract.formula_scopes(
        "external_formula",
        live_formula_ids=(),
        registered_formula_ids=(),
    ) == ["unregistered"]


def test_loader_rejects_unsafe_table_names(tmp_path: Path) -> None:
    config_path = tmp_path / "stage_opt_candidate_supply.yaml"
    config_path.write_text(
        """
version: 1
allowed_stage_bins: ["1"]
readiness:
  min_signals_per_key: 5
sources:
  - source_id: bad
    table: "sm.fact;DROP TABLE x"
    semantic_role: trade_trigger
    eligibility: stage_opt_candidate_supply
    pit_status: signal_date_pit
    grain: [stock_code]
    required_joins: [none]
    allowed_consumers: [audit_stage_opt_candidate_supply]
formula_scope_overrides: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid table name"):
        load_stage_opt_candidate_supply_contract(config_path)


def test_loader_requires_readiness_min_signals_per_key(tmp_path: Path) -> None:
    config_path = tmp_path / "stage_opt_candidate_supply.yaml"
    config_path.write_text(
        """
version: 1
allowed_stage_bins: ["1"]
readiness:
  min_signals_per_key: 0
sources:
  - source_id: trigger
    table: sm.fact_technical_trigger
    semantic_role: trade_trigger
    eligibility: stage_opt_candidate_supply
    pit_status: signal_date_pit
    grain: [stock_code]
    required_joins: [none]
    allowed_consumers: [audit_stage_opt_candidate_supply]
formula_scope_overrides: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="readiness.min_signals_per_key"):
        load_stage_opt_candidate_supply_contract(config_path)


def test_loader_rejects_grain_columns_missing_from_required_columns(tmp_path: Path) -> None:
    config_path = tmp_path / "stage_opt_candidate_supply.yaml"
    config_path.write_text(
        """
version: 1
allowed_stage_bins: ["1"]
readiness:
  min_signals_per_key: 5
sources:
  - source_id: trigger
    table: sm.fact_technical_trigger
    semantic_role: trade_trigger
    eligibility: stage_opt_candidate_supply
    pit_status: signal_date_pit
    grain: [stock_code, date]
    required_columns: [stock_code]
    required_joins: [sm.fact_signal_context on stock_code,date]
    join_checks:
      - table: sm.fact_signal_context
        source_columns: [stock_code]
        target_columns: [stock_code]
        required_columns: [stock_code]
    allowed_consumers: [audit_stage_opt_candidate_supply]
formula_scope_overrides: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source trigger grain columns missing from required_columns: date"):
        load_stage_opt_candidate_supply_contract(config_path)


def test_loader_rejects_join_columns_missing_from_declared_columns(tmp_path: Path) -> None:
    config_path = tmp_path / "stage_opt_candidate_supply.yaml"
    config_path.write_text(
        """
version: 1
allowed_stage_bins: ["1"]
readiness:
  min_signals_per_key: 5
sources:
  - source_id: trigger
    table: sm.fact_technical_trigger
    semantic_role: trade_trigger
    eligibility: stage_opt_candidate_supply
    pit_status: signal_date_pit
    grain: [stock_code, date]
    required_columns: [stock_code, date]
    required_joins: [v_price_kline_qfq daily qfq bars]
    join_checks:
      - table: v_price_kline_qfq
        source_columns: [stock_code, date]
        target_columns: [code, date]
        required_columns: [date]
    allowed_consumers: [audit_stage_opt_candidate_supply]
formula_scope_overrides: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="join v_price_kline_qfq target_columns columns missing from join required_columns: code"):
        load_stage_opt_candidate_supply_contract(config_path)
