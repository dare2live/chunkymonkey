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
