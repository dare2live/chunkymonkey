import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts.seed_dim_data_asset import (
    _build_backend_table_reference_index,
    grep_readers,
    grep_writer,
    infer_asset_contract,
)
from services.workbench_read import build_workbench_data_sources


def test_infer_asset_contract_distinguishes_dense_kline_and_sparse_events():
    kline = infer_asset_contract(
        "price_kline_tdxhub",
        layer="raw",
        freshness="t+0",
        upstream_source="tdxhub.quotes",
    )
    assert kline["coverage_policy"] == "dense_active_a_stock_trading_days"
    assert kline["null_policy"] == "no_null_for_ohlcv_after_calendar"
    assert kline["pit_policy"] == "same_day_market_data_after_close"
    assert kline["quality_gate_level"] == "blocking"
    assert kline["intended_use"] == "primary_pricing_source"

    lhb = infer_asset_contract(
        "raw_lhb_daily",
        layer="raw",
        freshness="event",
        upstream_source="aif10:RPT_DAILYBILLBOARD_DETAILSNEW",
    )
    assert lhb["coverage_policy"] == "sparse_event_presence_only"
    assert lhb["null_policy"] == "no_event_is_absence_not_missing"
    assert lhb["model_eligibility"] == "encoded_auxiliary_only"
    assert lhb["strategy_eligibility"] == "attention_filter_context"


def test_infer_asset_contract_freezes_initial_shareholder_plan_event_purpose():
    contract = infer_asset_contract(
        "mart_shareholder_plan_initial_event",
        layer="mart",
        freshness="event",
        upstream_source="derived from fact_shareholder_plan_tdx_f10",
    )

    assert contract["asset_grain"] == "stock_code+initial_notice_date+subject+direction+plan_window"
    assert contract["pit_policy"] == "source_notice_date_equals_source_available_date"
    assert contract["intended_use"] == "initial_shareholder_plan_capital_attention_candidate"
    assert contract["model_eligibility"] == "research_candidate_after_validation"
    assert contract["quality_gate_level"] == "blocking"


def test_infer_asset_contract_marks_initial_shareholder_plan_panel_research_only():
    contract = infer_asset_contract(
        "mart_shareholder_plan_initial_feature_panel",
        layer="mart",
        freshness="on-demand",
        upstream_source="derived from fact_feature_panel + mart_shareholder_plan_initial_event",
    )

    assert contract["asset_grain"] == "feature_set_id+stock_code+trade_date"
    assert contract["null_policy"] == "event_absence_encoded_no_unclassified_nulls"
    assert contract["pit_policy"] == "initial_event_source_available_date_lte_signal_date"
    assert contract["model_eligibility"] == "not_production_model_input_research_only_until_walkforward_gate"
    assert contract["strategy_eligibility"] == "capital_attention_auxiliary_context_candidate"


def test_infer_asset_contract_marks_shareholder_plan_family_eval_as_research_evidence():
    contract = infer_asset_contract(
        "mart_shareholder_plan_feature_family_eval",
        layer="mart",
        freshness="on-demand",
        upstream_source="derived from fact_feature_panel + shareholder plan marts",
    )

    assert contract["asset_grain"] == "run_id+source_family+feature_name+label_name"
    assert contract["pit_policy"] == "compares_latest_state_and_initial_event_source_available_dates"
    assert contract["model_eligibility"] == "not_model_input_research_evidence_only"
    assert contract["strategy_eligibility"] == "feature_family_selection_evidence"
    assert contract["quality_gate_level"] == "monitor_only"


def test_infer_asset_contract_marks_shareholder_plan_walkforward_as_research_gate():
    contract = infer_asset_contract(
        "mart_shareholder_plan_family_walkforward_summary",
        layer="mart",
        freshness="on-demand",
        upstream_source="derived from mart_shareholder_plan_family_walkforward",
    )

    assert contract["asset_grain"] == "run_id+source_family+feature_name+label_name"
    assert contract["pit_policy"] == "inherits_shareholder_plan_family_walkforward_policy"
    assert contract["model_eligibility"] == "not_model_input_research_evidence_only"
    assert contract["strategy_eligibility"] == "candidate_validation_before_registry_change"
    assert contract["quality_gate_level"] == "monitor_only"


def test_infer_asset_contract_marks_mtm_rerank_as_research_gate():
    contract = infer_asset_contract(
        "mart_synergy_policy_mtm_rerank",
        layer="mart",
        freshness="on-demand",
        upstream_source="derived from mart_optuna_synergy_trial + validate_synergy_policy_mark_to_market",
    )

    assert contract["asset_grain"] == "run_id+optuna_run_id+trial_number"
    assert contract["pit_policy"] == "inherits_synergy_policy_candidate_and_tdxhub_mtm_policy"
    assert contract["model_eligibility"] == "not_model_input_research_evidence_only"
    assert contract["strategy_eligibility"] == "research_rerank_gate_before_candidate_promotion"


def test_infer_asset_contract_marks_mtm_strategy_sweep_as_research_gate():
    contract = infer_asset_contract(
        "mart_synergy_policy_mtm_strategy_sweep",
        layer="mart",
        freshness="on-demand",
        upstream_source="derived from mart_synergy_policy_candidate + validate_synergy_policy_mark_to_market",
    )

    assert contract["asset_grain"] == "run_id+variant_id"
    assert contract["pit_policy"] == "inherits_synergy_policy_candidate_tdxhub_mtm_and_market_state_filter_policy"
    assert contract["model_eligibility"] == "not_model_input_research_evidence_only"
    assert contract["strategy_eligibility"] == "strategy_parameter_gate_before_candidate_promotion"


def test_infer_asset_contract_marks_industry_pit_as_constraint_gate():
    contract = infer_asset_contract(
        "mart_stock_industry_pit",
        layer="mart",
        freshness="on-demand",
        upstream_source="derived from dim_stock_tdx_industry_history + dim_stock_tdx_industry",
    )

    assert contract["asset_grain"] == "stock_code+effective_date_range"
    assert (
        contract["pit_policy"]
        == "latest_industry_snapshot_lte_signal_date_current_label_fallback_blocked"
    )
    assert contract["model_eligibility"] == "not_model_input"
    assert contract["strategy_eligibility"] == "blocked_until_mart_industry_pit_quality_passes"
    assert contract["quality_gate_level"] == "blocking_when_industry_constraints_enabled"


def test_infer_asset_contract_marks_raw_tdx_industry_snapshot_as_lineage_source():
    contract = infer_asset_contract(
        "raw_tdx_industry_file_snapshot",
        layer="raw",
        freshness="weekly",
        upstream_source="tdxhub.block (tdxhy.cfg)",
    )

    assert contract["asset_grain"] == "snapshot_date+raw_hash"
    assert contract["pit_policy"] == "source_snapshot_before_parsed_dimension_rows"
    assert contract["intended_use"] == "audit_and_future_industry_pit_backfill_source"
    assert contract["strategy_eligibility"] == "raw_lineage_for_industry_constraints"


def test_infer_asset_contract_marks_industry_pit_quality_as_required_gate():
    contract = infer_asset_contract(
        "mart_industry_pit_quality",
        layer="mart",
        freshness="on-demand",
        upstream_source="derived from mart_stock_industry_pit + configured signal table",
    )

    assert contract["asset_grain"] == "run_id+signal_table"
    assert contract["pit_policy"] == "documents_industry_pit_eligibility_before_strategy_use"
    assert contract["strategy_eligibility"] == "required_gate_for_industry_concentration_parameters"


def test_seed_dim_data_asset_reuses_backend_text_index_for_writer_and_readers():
    text_index = [
        (
            "backend/scripts/build_custom_table.py",
            "conn.execute('INSERT INTO mart_custom_asset SELECT * FROM source_table')",
        ),
        (
            "backend/services/custom_read.py",
            "rows = conn.execute('SELECT * FROM mart_custom_asset').fetchall()",
        ),
    ]

    assert grep_writer("mart_custom_asset", text_index) == "backend/scripts/build_custom_table.py"
    assert grep_readers("mart_custom_asset", text_index) == ["backend/services/custom_read.py"]


def test_seed_dim_data_asset_builds_reference_maps_in_one_pass():
    text_index = [
        (
            "backend/scripts/build_custom_table.py",
            "conn.execute('INSERT INTO mart_custom_asset SELECT * FROM source_table')",
        ),
        (
            "backend/services/custom_read.py",
            "rows = conn.execute('SELECT * FROM mart_custom_asset JOIN dim_custom ON 1=1').fetchall()",
        ),
        (
            "backend/services/schema_only.py",
            "conn.execute('CREATE TABLE IF NOT EXISTS mart_custom_asset (id INTEGER)')",
        ),
    ]

    writers, readers = _build_backend_table_reference_index(
        ["mart_custom_asset", "dim_custom"],
        text_index,
    )

    assert writers["mart_custom_asset"] == "backend/scripts/build_custom_table.py"
    assert "backend/services/schema_only.py" not in writers.values()
    assert readers["mart_custom_asset"] == ["backend/services/custom_read.py"]
    assert readers["dim_custom"] == ["backend/services/custom_read.py"]


def test_workbench_data_sources_exposes_asset_governance_contracts():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE dim_data_asset (
                table_name TEXT,
                layer TEXT,
                purpose TEXT,
                writer_module TEXT,
                upstream_source TEXT,
                source_tier INTEGER,
                expected_freshness TEXT,
                sla_hours DOUBLE,
                deprecation_status TEXT,
                asset_grain TEXT,
                asset_cadence TEXT,
                coverage_policy TEXT,
                null_policy TEXT,
                pit_policy TEXT,
                intended_use TEXT,
                model_eligibility TEXT,
                strategy_eligibility TEXT,
                frontend_visibility TEXT,
                quality_gate_level TEXT
            );
            INSERT INTO dim_data_asset VALUES
                ('price_kline_tdxhub', 'raw', 'kline', 'build_price_kline_tdxhub', 'tdxhub.quotes', 1, 't+0', 24, 'active',
                 'stock_code+trade_date', 'trading_day_daily', 'dense_active_a_stock_trading_days',
                 'no_null_for_ohlcv_after_calendar', 'same_day_market_data_after_close',
                 'primary_pricing_source', 'derive_features_only',
                 'entry_exit_pricing_and_trend', 'governance_visible', 'blocking'),
                ('raw_lhb_daily', 'raw', 'lhb', 'services.lhb_client', 'aif10:RPT_DAILYBILLBOARD_DETAILSNEW', 2, 'event', 48, 'active',
                 'stock_code+event', 'event_driven', 'sparse_event_presence_only',
                 'no_event_is_absence_not_missing', 'source_notice_or_event_date_required',
                 'attention_signal_or_context', 'encoded_auxiliary_only',
                 'attention_filter_context', 'governance_visible', 'warning');

            CREATE TABLE mart_data_health (
                table_name TEXT,
                snapshot_at TEXT,
                row_count INTEGER,
                last_data_date TEXT,
                freshness_hours DOUBLE,
                freshness_ok BOOLEAN,
                severity TEXT,
                issue_summary TEXT,
                source_tier_dist TEXT
            );
            INSERT INTO mart_data_health VALUES
                ('price_kline_tdxhub', '2026-05-07T10:00:00', 100, '2026-05-06', 1.0, TRUE, 'green', NULL, '{"1":100}'),
                ('raw_lhb_daily', '2026-05-07T10:00:00', 7, '2026-05-06', 1.0, TRUE, 'green', NULL, '{"2":7}');
            """
        )

        result = build_workbench_data_sources(conn, as_of_date="2026-05-07")

        items = {row["table_name"]: row for row in result["asset_health"]["items"]}
        assert items["price_kline_tdxhub"]["coverage_policy"] == "dense_active_a_stock_trading_days"
        assert items["price_kline_tdxhub"]["quality_gate_level"] == "blocking"
        assert items["raw_lhb_daily"]["coverage_policy"] == "sparse_event_presence_only"
        assert items["raw_lhb_daily"]["model_eligibility"] == "encoded_auxiliary_only"
        assert result["asset_health"]["governance_counts"]["coverage_policy"] == {
            "dense_active_a_stock_trading_days": 1,
            "sparse_event_presence_only": 1,
        }
