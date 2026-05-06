from __future__ import annotations

import json

from conftest import duck_mem
import services.data_quality as data_quality
from services.data_processing_monitor import ensure_data_processing_monitor_tables
from services.data_quality import record_global_data_quality_gate
from services.pipeline_manifest import record_pipeline_run
from services.pricing_policy import load_pricing_label_policy


def _seed_calendar(conn) -> None:
    conn.execute(
        """
        CREATE TABLE dim_trading_calendar (
            trade_date TEXT,
            is_trading INTEGER
        )
        """
    )
    conn.executemany(
        "INSERT INTO dim_trading_calendar VALUES (?, ?)",
        [
            ("2026-01-02", 1),
            ("2026-01-05", 1),
            ("2026-01-06", 1),
        ],
    )


def test_global_data_quality_gate_blocks_any_unclassified_feature_null() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE,
                follow_net_return_60d DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', NULL, 0.05)")

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_null_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )
        detail = conn.execute(
            """
            SELECT status, violation_count, reason
              FROM mart_global_data_quality_detail
             WHERE gate_run_id = 'global_dq_null_unit'
               AND domain = 'feature_panel_nulls'
               AND column_name = 'amount_20d'
            """
        ).fetchone()

        assert result["gate_status"] == "blocked"
        assert "feature_panel_nulls:unclassified_nulls:fact_feature_panel:amount_20d" in result["blockers"]
        assert detail["status"] == "fail"
        assert detail["violation_count"] == 1
        assert "not classified" in detail["reason"]


def test_candidate_contract_seed_ignores_deleted_feature_sets() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                signal DOUBLE
            );
            CREATE TABLE mart_model_selection_run (
                run_id TEXT,
                feature_set_id TEXT,
                selected_features_json TEXT,
                promote_to_champion BOOLEAN,
                built_at TEXT
            );
            INSERT INTO mart_model_selection_run VALUES
                ('old_run', 'deleted_set', '["signal"]', FALSE, '2026-05-06T00:00:00');
            """
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_deleted_candidate_contract_unit",
            feature_tables=["fact_feature_panel_candidate"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )
        contract_count = conn.execute(
            "SELECT COUNT(*) AS n FROM mart_candidate_feature_set_contract"
        ).fetchone()["n"]

        assert result["gate_status"] == "pass"
        assert contract_count == 0


def test_global_data_quality_gate_allows_only_classified_immature_follow_label_nulls() -> None:
    policy = load_pricing_label_policy()
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE,
                follow_net_return_60d DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0, NULL)")
        conn.executescript(
            """
            CREATE TABLE mart_follow_return_label_build (
                run_id TEXT,
                feature_table TEXT,
                policy_id TEXT,
                policy_hash TEXT,
                event_calc_version TEXT,
                price_adjustment TEXT,
                transaction_cost_bps DOUBLE,
                horizons_json TEXT,
                labels_json TEXT,
                row_count BIGINT,
                label_non_null_json TEXT,
                label_coverage_json TEXT,
                min_date TEXT,
                max_date TEXT,
                built_at TEXT
            );
            CREATE TABLE mart_follow_return_label_quality (
                run_id TEXT,
                feature_table TEXT,
                label_name TEXT,
                horizon_days INTEGER,
                policy_id TEXT,
                policy_hash TEXT,
                event_calc_version TEXT,
                row_count BIGINT,
                non_null_count BIGINT,
                null_count BIGINT,
                immature_null_count BIGINT,
                mature_null_count BIGINT,
                missing_signal_kline_count BIGINT,
                missing_entry_price_count BIGINT,
                missing_exit_price_count BIGINT,
                unclassified_null_count BIGINT,
                min_date TEXT,
                max_date TEXT,
                stock_max_date_min TEXT,
                stock_max_date_max TEXT,
                global_market_max_date TEXT,
                built_at TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO mart_follow_return_label_build
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "label_build_unit",
                "fact_feature_panel",
                policy.policy_id,
                policy.policy_hash(),
                policy.event_calc_version,
                policy.price_adjustment,
                policy.transaction_cost_bps,
                "[60]",
                json.dumps(["follow_net_return_60d"]),
                1,
                json.dumps({"follow_net_return_60d": 0}),
                json.dumps({"follow_net_return_60d": 0.0}),
                "2026-01-02",
                "2026-01-02",
                "2026-05-06T00:00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO mart_follow_return_label_quality
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "label_build_unit",
                "fact_feature_panel",
                "follow_net_return_60d",
                60,
                policy.policy_id,
                policy.policy_hash(),
                policy.event_calc_version,
                1,
                0,
                1,
                1,
                0,
                0,
                0,
                0,
                0,
                "2026-01-02",
                "2026-01-02",
                "2026-01-02",
                "2026-01-02",
                "2026-01-02",
                "2026-05-06T00:00:00",
            ),
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_immature_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )
        detail = conn.execute(
            """
            SELECT status, reason
              FROM mart_global_data_quality_detail
             WHERE gate_run_id = 'global_dq_immature_unit'
               AND domain = 'feature_panel_nulls'
               AND column_name = 'follow_net_return_60d'
            """
        ).fetchone()

        assert result["gate_status"] == "pass"
        assert result["blockers"] == []
        assert detail["status"] == "pass"
        assert "future immature" in detail["reason"]


def test_global_data_quality_gate_allows_registry_classified_optional_null() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', NULL)")
        record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_create_registry",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )
        conn.execute(
            """
            INSERT INTO mart_feature_null_policy (
                policy_key, table_name, column_pattern, match_type,
                null_class, null_reason, source_family,
                train_blocking, production_allowed, enabled, notes, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test_amount_optional",
                "fact_feature_panel",
                "amount_20d",
                "exact",
                "fixture_optional_source_absent",
                "test fixture explicitly classifies this nullable optional source",
                "fixture",
                False,
                True,
                True,
                "unit",
                "2026-05-06T00:00:00",
            ),
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_optional_registry",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )
        detail = conn.execute(
            """
            SELECT status, check_name, violation_count, reason
              FROM mart_global_data_quality_detail
             WHERE gate_run_id = 'global_dq_optional_registry'
               AND domain = 'feature_panel_nulls'
               AND column_name = 'amount_20d'
            """
        ).fetchone()

        assert result["gate_status"] == "pass"
        assert detail["status"] == "pass"
        assert detail["check_name"] == "classified_nulls"
        assert detail["violation_count"] == 0
        assert "fixture_optional_source_absent" in detail["reason"]


def test_global_data_quality_gate_blocks_registry_classified_train_blocking_null() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                source_gap_feature DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', NULL)")
        record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_create_train_blocking_registry",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )
        conn.execute(
            """
            INSERT INTO mart_feature_null_policy (
                policy_key, table_name, column_pattern, match_type,
                null_class, null_reason, source_family,
                train_blocking, production_allowed, enabled, notes, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test_source_gap_blocking",
                "fact_feature_panel",
                "source_gap_feature",
                "exact",
                "fixture_source_gap_requires_backfill",
                "test fixture source gap remains a training blocker",
                "fixture",
                True,
                False,
                True,
                "unit",
                "2026-05-06T00:00:00",
            ),
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_train_blocking_registry",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert (
            "feature_panel_nulls:classified_train_blocking_nulls:"
            "fact_feature_panel:source_gap_feature"
        ) in result["blockers"]


def test_global_data_quality_gate_blocks_event_window_nulls_that_should_be_encoded() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                lhb_inst_buy_count_30d DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', NULL)")

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_event_encoded_null",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert (
            "feature_panel_nulls:availability_contract_null_rule_violation:"
            "fact_feature_panel:lhb_inst_buy_count_30d"
        ) in result["blockers"]


def test_global_data_quality_gate_accepts_event_window_zero_encoding() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                lhb_inst_buy_count_30d DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 0.0)")

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_event_encoded_zero",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "pass"
        assert result["blockers"] == []


def test_global_data_quality_gate_blocks_rolling_null_after_first_valid_value() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE
            )
            """
        )
        conn.executemany(
            "INSERT INTO fact_feature_panel VALUES (?, ?, ?)",
            [
                ("000001", "2026-01-02", None),
                ("000001", "2026-01-05", 0.03),
                ("000001", "2026-01-06", None),
            ],
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_rolling_post_warmup_null",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert (
            "feature_panel_nulls:availability_contract_temporal_null_breach:"
            "fact_feature_panel:ret_20d"
        ) in result["blockers"]


def test_global_data_quality_gate_accepts_rolling_warmup_null_before_first_valid_value() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE
            )
            """
        )
        conn.executemany(
            "INSERT INTO fact_feature_panel VALUES (?, ?, ?)",
            [
                ("000001", "2026-01-02", None),
                ("000001", "2026-01-05", 0.03),
                ("000001", "2026-01-06", 0.04),
            ],
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_rolling_warmup_null",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "pass"
        assert result["blockers"] == []


def test_global_data_quality_gate_classifies_excluded_source_gap_without_training_block() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                rz_balance DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', NULL)")

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_excluded_source_gap",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )
        detail = conn.execute(
            """
            SELECT status, check_name, violation_count, reason
              FROM mart_global_data_quality_detail
             WHERE gate_run_id = 'global_dq_excluded_source_gap'
               AND column_name = 'rz_balance'
            """
        ).fetchone()

        assert result["gate_status"] == "pass"
        assert detail["status"] == "pass"
        assert detail["check_name"] == "availability_contract_excluded_source_gap"
        assert detail["violation_count"] == 0
        assert "source/backfill gaps" in detail["reason"]


def test_global_data_quality_gate_allows_industry_relative_null_when_base_is_classified() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE,
                ret_20d_tdx_l1_rel DOUBLE
            )
            """
        )
        conn.executemany(
            "INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?)",
            [
                ("000001", "2026-01-02", None, None),
                ("000001", "2026-01-05", 0.10, 0.0),
                ("000001", "2026-01-06", 0.12, 0.02),
            ],
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_industry_rel_base_null",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )
        detail = conn.execute(
            """
            SELECT status, check_name, violation_count, reason
              FROM mart_global_data_quality_detail
             WHERE gate_run_id = 'global_dq_industry_rel_base_null'
               AND column_name = 'ret_20d_tdx_l1_rel'
            """
        ).fetchone()

        assert result["gate_status"] == "pass"
        assert detail["status"] == "pass"
        assert detail["check_name"] == "classified_nulls"
        assert detail["violation_count"] == 0
        assert "ret_20d" in detail["reason"]


def test_global_data_quality_gate_blocks_industry_relative_null_when_base_exists() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE,
                ret_20d_tdx_l1_rel DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 0.10, NULL)")

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_industry_rel_base_present",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert (
            "feature_panel_nulls:derived_feature_null_with_base_present:"
            "fact_feature_panel:ret_20d_tdx_l1_rel"
        ) in result["blockers"]


def test_global_data_quality_gate_blocks_slow_pipeline_without_stage_timing() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0)")
        record_pipeline_run(
            conn,
            run_id="slow_eval_no_timing",
            pipeline_name="evaluate_champion_candidate",
            status="failed",
            duration_s=61.0,
            perf_summary={"config": {"model_id": "m1"}},
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_slow_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=True,
        )

        assert result["gate_status"] == "blocked"
        assert any("pipeline_performance:slow_run_has_stage_timing" in item for item in result["blockers"])


def test_global_data_quality_gate_accepts_slow_pipeline_with_stage_timing() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0)")
        record_pipeline_run(
            conn,
            run_id="slow_feature_with_timing",
            pipeline_name="build_feature_panel_duck",
            status="failed",
            duration_s=61.0,
            perf_summary={"stage_timings": {"calendar_preflight_s": 1.0, "write_panel_s": 60.0}},
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_slow_timed_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=True,
        )

        assert result["gate_status"] == "pass"
        assert result["blockers"] == []


def test_global_data_quality_gate_accepts_slow_pipeline_with_timings_key() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0)")
        record_pipeline_run(
            conn,
            run_id="slow_train_with_timings",
            pipeline_name="train_multidim_model",
            status="success",
            duration_s=90.0,
            perf_summary={"timings": {"load_panel_s": 10.0, "optuna_s": 50.0, "total_s": 90.0}},
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_slow_timings_key_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=True,
        )

        assert result["gate_status"] == "pass"
        assert result["blockers"] == []


def test_global_data_quality_gate_blocks_retired_primary_recommendation_output() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            );
            INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0);
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('old_m', 'retired'),
                ('champion_m', 'champion');
            CREATE TABLE mart_daily_recommendation (
                snapshot_date TEXT,
                stock_code TEXT,
                model_id TEXT,
                is_primary BOOLEAN,
                run_mode TEXT
            );
            INSERT INTO mart_daily_recommendation VALUES
                ('2026-01-02', '000001', 'old_m', TRUE, 'champion'),
                ('2026-01-02', '000002', 'champion_m', TRUE, 'champion');
            """
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_retired_primary_rec_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert (
            "recommendation_output:primary_outputs_use_lifecycle_champion:"
            "mart_daily_recommendation:model_id"
        ) in result["blockers"]


def test_global_data_quality_gate_accepts_champion_primary_recommendation_output() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            );
            INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0);
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES ('champion_m', 'champion');
            CREATE TABLE mart_daily_recommendation (
                snapshot_date TEXT,
                stock_code TEXT,
                model_id TEXT,
                is_primary BOOLEAN,
                run_mode TEXT
            );
            INSERT INTO mart_daily_recommendation VALUES
                ('2026-01-02', '000001', 'champion_m', TRUE, 'champion'),
                ('2026-01-02', '000002', 'shadow_m', FALSE, 'shadow');
            """
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_champion_primary_rec_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "pass"
        assert result["blockers"] == []


def test_global_data_quality_gate_blocks_non_investable_primary_recommendation() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            );
            INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0);
            CREATE TABLE dim_active_a_stock (
                stock_code TEXT PRIMARY KEY,
                stock_name TEXT
            );
            INSERT INTO dim_active_a_stock VALUES
                ('000001', '*ST测试'),
                ('000002', '正常股票');
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES ('champion_m', 'champion');
            CREATE TABLE mart_daily_recommendation (
                snapshot_date TEXT,
                stock_code TEXT,
                model_id TEXT,
                is_primary BOOLEAN,
                run_mode TEXT
            );
            INSERT INTO mart_daily_recommendation VALUES
                ('2026-01-02', '000001', 'champion_m', TRUE, 'champion'),
                ('2026-01-02', '000002', 'champion_m', TRUE, 'champion');
            """
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_non_investable_primary_rec_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert (
            "recommendation_output:primary_outputs_use_investable_universe:"
            "mart_daily_recommendation:stock_code"
        ) in result["blockers"]


def test_global_data_quality_gate_blocks_cleanup_backup_tables() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            );
            INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0);
            CREATE TABLE backup_storage_cleanup_unit (
                model_id TEXT
            );
            """
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_cleanup_backup_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert "cleanup_policy:direct_delete_no_archive:mart_data_deletion_record" in result["blockers"]
        assert result["evidence"]["cleanup_policy"]["backup_table_count"] == 1


def test_global_data_quality_gate_blocks_workspace_cleanup_artifacts(tmp_path, monkeypatch) -> None:
    (tmp_path / "old_archive").mkdir()
    deep_cleanup_file = tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "stale.orig"
    deep_cleanup_file.parent.mkdir(parents=True)
    deep_cleanup_file.write_text("obsolete", encoding="utf-8")
    monkeypatch.setattr(data_quality, "WORKSPACE_ROOT", tmp_path)
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            );
            INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0);
            """
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_cleanup_artifact_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert "cleanup_policy:direct_delete_no_archive:mart_data_deletion_record" in result["blockers"]
        cleanup = result["evidence"]["cleanup_policy"]
        assert cleanup["cleanup_artifact_scan_root"] == str(tmp_path)
        assert cleanup["forbidden_artifact_count"] == 2
        assert cleanup["forbidden_dir_count"] == 1
        assert cleanup["forbidden_file_count"] == 1
        assert str(deep_cleanup_file) in [item["path"] for item in cleanup["examples"]]


def test_global_data_quality_gate_blocks_current_policy_model_bad_features() -> None:
    policy = load_pricing_label_policy()
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            );
            INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0);
            CREATE TABLE mart_multidim_model (
                model_id TEXT,
                notes TEXT,
                feature_cols_json TEXT,
                pricing_policy_hash TEXT,
                created_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO mart_multidim_model VALUES (?, ?, ?, ?, ?)",
            (
                "bad_current_policy_model",
                None,
                '["ret_60d", "inst_count_qoq"]',
                policy.policy_hash(),
                "2026-05-06T00:00:00",
            ),
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_current_model_feature_contract_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert (
            "model_feature_contract:current_policy_models_use_allowed_features:"
            "mart_multidim_model:feature_cols_json"
        ) in result["blockers"]


def test_global_data_quality_gate_blocks_dangling_model_lifecycle_reference() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            );
            INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0);
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT,
                promoted_from TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('champion_m', 'champion', 'deleted_old_m');
            CREATE TABLE mart_multidim_model (
                model_id TEXT,
                notes TEXT,
                feature_cols_json TEXT,
                pricing_policy_hash TEXT,
                created_at TEXT
            );
            INSERT INTO mart_multidim_model VALUES
                ('champion_m', NULL, '["ret_60d"]', NULL, '2026-05-06T00:00:00');
            """
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_dangling_model_lifecycle_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert (
            "model_lifecycle:lifecycle_model_row_integrity:"
            "mart_model_lifecycle"
        ) in result["blockers"]


def test_global_data_quality_gate_blocks_unclassified_processing_rejections() -> None:
    with duck_mem() as conn:
        _seed_calendar(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                amount_20d DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 10.0)")
        ensure_data_processing_monitor_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_data_processing_tool_run (
                run_id, tool_name, policy_id, source_name, status,
                input_rows, accepted_rows, rejected_rows, reason_counts_json,
                started_at, ended_at
            ) VALUES (
                'bad_tool_run', 'kline_cleaner', 'unit', 'tdxhub', 'completed',
                10, 9, 1, NULL, '2026-05-06T00:00:00', '2026-05-06T00:00:01'
            )
            """
        )

        result = record_global_data_quality_gate(
            conn,
            gate_run_id="global_dq_processing_monitor_unit",
            feature_tables=["fact_feature_panel"],
            include_market=False,
            include_institution_events=False,
            include_pipeline_performance=False,
        )

        assert result["gate_status"] == "blocked"
        assert any(
            item.startswith("data_processing_monitor:rejected_rows_have_reason")
            for item in result["blockers"]
        )
