import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts.seed_dim_data_asset import (
    _apply_manual_asset_overrides,
    _build_backend_table_reference_index,
    get_all_tables,
    grep_readers,
    grep_writer,
    infer_asset_contract,
    infer_freshness,
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

    trigger = infer_asset_contract(
        "fact_technical_trigger",
        layer="fact",
        freshness="event",
        upstream_source="derived from build_formula_signals_history + build_signal_context",
    )
    assert trigger["coverage_policy"] == "sparse_event_presence_only"
    assert trigger["null_policy"] == "no_event_is_absence_not_missing"
    assert trigger["quality_gate_level"] == "warning"
    assert trigger["strategy_eligibility"] == "attention_filter_context"


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


def test_infer_asset_contract_marks_architecture_cleanup_plan_as_on_demand_governance():
    freshness = infer_freshness("mart_architecture_cleanup_plan", "mart")
    assert freshness == ("on-demand", 24 * 30)

    contract = infer_asset_contract(
        "mart_architecture_cleanup_plan",
        layer="mart",
        freshness=freshness[0],
        upstream_source="derived (writer: backend/scripts/plan_architecture_cleanup.py)",
    )

    assert contract["asset_grain"] == "run_id+asset_id"
    assert contract["asset_cadence"] == "on_demand"
    assert contract["coverage_policy"] == "workflow_dependent"
    assert contract["intended_use"] == "governance_context"
    assert contract["quality_gate_level"] == "monitor_only"


def test_seed_dim_data_asset_preserves_manual_governance_fields():
    auto_governance = infer_asset_contract(
        "mart_custom_model_output",
        layer="mart",
        freshness="on-demand",
        upstream_source="derived (writer: backend/scripts/custom.py)",
    )
    prev = {
        "auto_discovered": False,
        "purpose": "manual purpose",
        "upstream_source": "manual source",
        "source_tier": 9,
        "expected_freshness": "manual cadence",
        "sla_hours": 123,
        "asset_grain": "manual grain",
        "coverage_policy": "manual coverage",
        "model_eligibility": "manual model eligibility",
    }

    purpose, upstream, source_tier, freshness, sla, governance = (
        _apply_manual_asset_overrides(
            prev,
            force_overwrite=False,
            purpose=None,
            upstream="auto source",
            source_tier=1,
            freshness="t+0",
            sla=24,
            governance=auto_governance,
        )
    )

    assert purpose == "manual purpose"
    assert upstream == "manual source"
    assert source_tier == 9
    assert freshness == "manual cadence"
    assert sla == 123
    assert governance["asset_grain"] == "manual grain"
    assert governance["coverage_policy"] == "manual coverage"
    assert governance["model_eligibility"] == "manual model eligibility"
    assert governance["pit_policy"] == auto_governance["pit_policy"]


def test_seed_dim_data_asset_force_overwrite_keeps_auto_governance_fields():
    auto_governance = infer_asset_contract(
        "mart_custom_model_output",
        layer="mart",
        freshness="on-demand",
        upstream_source="derived (writer: backend/scripts/custom.py)",
    )
    prev = {
        "auto_discovered": False,
        "purpose": "manual purpose",
        "upstream_source": "manual source",
        "source_tier": 9,
        "expected_freshness": "manual cadence",
        "sla_hours": 123,
        "asset_grain": "manual grain",
    }

    purpose, upstream, source_tier, freshness, sla, governance = (
        _apply_manual_asset_overrides(
            prev,
            force_overwrite=True,
            purpose=None,
            upstream="auto source",
            source_tier=1,
            freshness="t+0",
            sla=24,
            governance=auto_governance,
        )
    )

    assert purpose is None
    assert upstream == "auto source"
    assert source_tier == 1
    assert freshness == "t+0"
    assert sla == 24
    assert governance["asset_grain"] == auto_governance["asset_grain"]


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


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _RecordingTableConn:
    def __init__(self):
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)
        if "information_schema.tables" in sql:
            return _Rows([("mart_custom_asset",), ("raw_custom_source",)])
        if "COUNT(*) AS row_count" in sql:
            return _Rows([("mart_custom_asset", 3), ("raw_custom_source", 2)])
        raise AssertionError(f"unexpected SQL: {sql}")


def test_seed_dim_data_asset_batches_table_row_counts():
    conn = _RecordingTableConn()

    assert get_all_tables(conn) == [
        ("mart_custom_asset", 3),
        ("raw_custom_source", 2),
    ]
    count_queries = [sql for sql in conn.sql if "COUNT(*) AS row_count" in sql]
    assert len(count_queries) == 1
    assert "UNION ALL" in count_queries[0]


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


def test_seed_dim_data_asset_combined_reference_pattern_preserves_writer_variants():
    text_index = [
        (
            "backend/services/write_variants.py",
            "\n".join(
                [
                    "conn.execute('UPDATE analytics.fact_custom_asset SET x = 1')",
                    "conn.execute('DELETE FROM raw_delete_asset WHERE x = 0')",
                    "conn.register('raw_registered_asset', df)",
                    "df.to_sql('mart_pandas_asset', conn)",
                ]
            ),
        ),
        (
            "backend/scripts/copy_asset.py",
            "conn.execute('COPY raw_copy_asset FROM \\'/tmp/raw_copy_asset.csv\\'')",
        ),
        (
            "backend/scripts/create_asset.py",
            "conn.execute('CREATE OR REPLACE TABLE mart_create_asset AS SELECT * FROM source_table')",
        ),
        (
            "backend/services/custom_read.py",
            (
                "conn.execute('SELECT * FROM analytics.fact_custom_asset "
                "JOIN mart_pandas_asset ON 1=1 JOIN fact_custom_asset ON 1=1')"
            ),
        ),
        (
            "backend/services/schema_only.py",
            "conn.execute('CREATE TABLE IF NOT EXISTS mart_schema_only (id INTEGER)')",
        ),
    ]

    writers, readers = _build_backend_table_reference_index(
        [
            "fact_custom_asset",
            "raw_delete_asset",
            "raw_registered_asset",
            "mart_pandas_asset",
            "raw_copy_asset",
            "mart_create_asset",
            "mart_schema_only",
        ],
        text_index,
    )

    assert writers["fact_custom_asset"] == "backend/services/write_variants.py"
    assert writers["raw_delete_asset"] == "backend/services/write_variants.py"
    assert writers["raw_registered_asset"] == "backend/services/write_variants.py"
    assert writers["mart_pandas_asset"] == "backend/services/write_variants.py"
    assert writers["raw_copy_asset"] == "backend/scripts/copy_asset.py"
    assert writers["mart_create_asset"] == "backend/scripts/create_asset.py"
    assert "mart_schema_only" not in writers
    assert readers["fact_custom_asset"] == ["backend/services/custom_read.py"]
    assert readers["mart_pandas_asset"] == ["backend/services/custom_read.py"]


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
