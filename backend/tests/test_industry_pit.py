from __future__ import annotations

import json

from conftest import duck_mem
from services.industry_pit import build_industry_pit, ensure_industry_pit_tables
from services.data_quality import record_global_data_quality_gate
from services.workbench_read import build_workbench_research


def _seed_industry_tables(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT,
            tdx_l1 TEXT,
            tdx_l2 TEXT,
            tdx_l3 TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT,
            tdx_l3_name TEXT
        );
        CREATE TABLE dim_stock_tdx_industry_history (
            stock_code TEXT,
            snapshot_date TEXT,
            tdx_l1 TEXT,
            tdx_l2 TEXT,
            tdx_l3 TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT,
            tdx_l3_name TEXT
        );
        CREATE TABLE signal_scope (
            stock_code TEXT,
            signal_date TEXT
        );
        """
    )


def test_build_industry_pit_blocks_current_label_fallback_in_historical_window() -> None:
    with duck_mem() as conn:
        _seed_industry_tables(conn)
        conn.executescript(
            """
            INSERT INTO dim_stock_tdx_industry VALUES
                ('000001', 'A', 'A1', 'A11', '行业A', '行业A1', '行业A11'),
                ('000002', 'B', 'B1', 'B11', '行业B', '行业B1', '行业B11');
            INSERT INTO dim_stock_tdx_industry_history VALUES
                ('000001', '2026-01-05', 'A', 'A1', 'A11', '行业A', '行业A1', '行业A11'),
                ('000001', '2026-01-10', 'C', 'C1', 'C11', '行业C', '行业C1', '行业C11');
            INSERT INTO signal_scope VALUES
                ('000001', '2025-12-30'),
                ('000001', '2026-01-06'),
                ('000002', '2026-01-06');
            """
        )

        result = build_industry_pit(
            conn,
            run_id="pit_blocked",
            signal_table="signal_scope",
            signal_date_column="signal_date",
        )

        assert result["pit_eligible"] is False
        assert result["fallback_signal_rows"] == 2
        assert result["observed_pit_signal_rows"] == 1
        assert "industry_current_label_fallback_in_signal_window" in result["blockers"]
        fallback = conn.execute(
            """
            SELECT effective_from, effective_to
              FROM mart_stock_industry_pit
             WHERE stock_code = '000001'
               AND source = 'current_label_fallback'
            """
        ).fetchone()
        assert fallback["effective_to"] == "2026-01-04"


def test_build_industry_pit_passes_when_signal_window_uses_observed_snapshots_only() -> None:
    with duck_mem() as conn:
        _seed_industry_tables(conn)
        conn.executescript(
            """
            INSERT INTO dim_stock_tdx_industry VALUES
                ('000001', 'A', 'A1', 'A11', '行业A', '行业A1', '行业A11');
            INSERT INTO dim_stock_tdx_industry_history VALUES
                ('000001', '2026-01-01', 'A', 'A1', 'A11', '行业A', '行业A1', '行业A11');
            INSERT INTO signal_scope VALUES
                ('000001', '2026-01-02'),
                ('000001', '2026-01-03');
            """
        )

        result = build_industry_pit(
            conn,
            run_id="pit_pass",
            signal_table="signal_scope",
            signal_date_column="signal_date",
        )

        assert result["pit_eligible"] is True
        assert result["fallback_signal_rows"] == 0
        assert result["missing_pit_rows"] == 0
        assert result["observed_pit_signal_rows"] == 2


def test_build_industry_pit_blocks_static_backfill_with_future_available_date() -> None:
    with duck_mem() as conn:
        _seed_industry_tables(conn)
        conn.execute("ALTER TABLE dim_stock_tdx_industry_history ADD COLUMN source_available_date TEXT")
        conn.executescript(
            """
            INSERT INTO dim_stock_tdx_industry VALUES
                ('000001', 'A', 'A1', 'A11', '行业A', '行业A1', '行业A11');
            INSERT INTO dim_stock_tdx_industry_history (
                stock_code, snapshot_date,
                tdx_l1, tdx_l2, tdx_l3,
                tdx_l1_name, tdx_l2_name, tdx_l3_name,
                source_available_date
            ) VALUES (
                '000001', '2024-01-02',
                'A', 'A1', 'A11',
                '行业A', '行业A1', '行业A11',
                '2026-05-17'
            );
            INSERT INTO signal_scope VALUES ('000001', '2024-01-03');
            """
        )

        result = build_industry_pit(
            conn,
            run_id="pit_static_backfill_blocked",
            signal_table="signal_scope",
            signal_date_column="signal_date",
        )

        assert result["pit_eligible"] is False
        assert result["observed_pit_signal_rows"] == 0
        assert result["fallback_signal_rows"] == 1
        assert "industry_current_label_fallback_in_signal_window" in result["blockers"]
        row = conn.execute(
            """
            SELECT source, confidence_level, is_historical_pit, source_available_date
              FROM mart_stock_industry_pit
             WHERE stock_code = '000001'
               AND effective_from = '2024-01-02'
            """
        ).fetchone()
        assert row["source"] == "tdx_industry_static_backfill"
        assert row["confidence_level"] == "current_label_fallback"
        assert row["is_historical_pit"] is False
        assert row["source_available_date"] == "2026-05-17"


def test_workbench_research_exposes_industry_pit_readiness() -> None:
    with duck_mem() as conn:
        _seed_industry_tables(conn)
        conn.executescript(
            """
            INSERT INTO dim_stock_tdx_industry VALUES
                ('000001', 'A', 'A1', 'A11', '行业A', '行业A1', '行业A11');
            INSERT INTO dim_stock_tdx_industry_history VALUES
                ('000001', '2026-01-01', 'A', 'A1', 'A11', '行业A', '行业A1', '行业A11');
            INSERT INTO signal_scope VALUES ('000001', '2025-12-30');
            """
        )
        build_industry_pit(
            conn,
            run_id="pit_frontend",
            signal_table="signal_scope",
            signal_date_column="signal_date",
        )

        research = build_workbench_research(conn)

        assert research["industry_pit"]["run_id"] == "pit_frontend"
        assert research["industry_pit"]["pit_eligible"] is False
        assert research["industry_pit"]["fallback_signal_rows"] == 1


def test_global_data_quality_warns_when_industry_pit_is_not_ready() -> None:
    with duck_mem() as conn:
        _seed_industry_tables(conn)
        ensure_industry_pit_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_industry_pit_quality (
                run_id, signal_table, signal_stock_column, signal_date_column,
                signal_row_count, history_snapshot_count, fallback_signal_rows,
                missing_pit_rows, missing_tdx_l1_rows, fallback_ratio, missing_ratio,
                pit_eligible, blockers_json, built_at
            )
            VALUES (
                'pit_quality_blocked', 'signal_scope', 'stock_code', 'signal_date',
                10, 1, 10, 0, 0, 1.0, 0.0, FALSE,
                ?, '2026-05-07T10:00:00'
            )
            """,
            (json.dumps(["industry_current_label_fallback_in_signal_window"]),),
        )
        conn.executescript(
            """
            CREATE TABLE dim_trading_calendar (trade_date TEXT, is_trading INTEGER);
            INSERT INTO dim_trading_calendar VALUES ('2026-05-06', 1);
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                close DOUBLE,
                kline_source_name TEXT,
                kline_source_tier INTEGER,
                kline_is_fallback BOOLEAN
            );
            INSERT INTO fact_feature_panel VALUES ('000001', '2026-05-06', 10, 'tdxhub_quote', 1, FALSE);
            """
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="dq_industry_pit_warning",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "pass"
        assert "industry_pit:latest_readiness:mart_industry_pit_quality" in result["warnings"]
        assert result["evidence"]["industry_pit"]["pit_eligible"] is False
