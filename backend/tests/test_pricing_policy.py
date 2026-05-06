from __future__ import annotations

import json

from conftest import duck_mem
from services.pricing_policy import (
    ensure_pricing_policy_table,
    load_pricing_label_policy,
    record_pricing_label_data_readiness_gate,
    record_pricing_label_policy,
    record_pricing_label_policy_gate,
)


def test_pricing_label_policy_loads_vwap_follow_contract():
    policy = load_pricing_label_policy()

    assert policy.policy_id == "pricing_label_policy_vwap_follow_v1"
    assert policy.event_calc_version == "v4_qfq_factor_adjusted_vwap_entry_dual_cost"
    assert policy.follow_entry_price_mode == "entry_day_vwap_qfq"
    assert policy.follow_entry_ref_price_mode == "entry_day_vwap_qfq_fallback_open"
    assert policy.transaction_cost_bps == 10.0
    assert policy.to_dict()["portfolio_transaction_cost"]["meaning"] == "execution_friction_only_not_entry_price"
    assert policy.to_dict()["follow_entry"]["qfq_factor_adjustment_required_for_hand_volume"] is True
    assert policy.follow_exit_default == "horizon_end_vwap_qfq"
    assert policy.follow_exit_needs_definition is False
    assert policy.alpha_forward_label_needs_migration_review is False
    assert policy.definition_sections["model_training"]["primary_label_family"] == "follow_return_label"
    assert policy.definition_sections["promotion_gate"]["prohibit_global_90d_replacement"] is True
    assert policy.definition_sections["signal_policy"]["executable_date"] == "signal_date"
    assert policy.definition_sections["signal_policy"]["same_day_execution_allowed"] is True
    assert policy.definition_sections["data_source_policy"]["kline_primary_source"] == "tdxhub"
    assert policy.definition_sections["feature_policy"]["pit_required_for_training"] is True
    assert policy.definition_sections["data_quality_policy"]["missing_ratio_threshold_for_blocking"] == 0.0
    assert policy.definition_sections["data_quality_policy"]["any_null_or_missing_requires_root_cause"] is True
    assert policy.definition_sections["performance_policy"]["progress_heartbeat_required_after_s"] == 30
    assert policy.definition_sections["ranking_policy"]["rank_normalization"] == "percentile_rank_by_signal_date"
    assert policy.definition_sections["portfolio_construction"]["overlapping_positions_same_stock"] == (
        "prohibit_duplicate_active_position"
    )
    assert policy.definition_sections["explainability"]["additivity_check_required"] is True
    assert policy.definition_sections["champion_policy"]["global_90d_replacement_allowed"] is False
    assert len(policy.policy_hash()) == 16


def test_pricing_label_policy_records_db_snapshot():
    with duck_mem() as conn:
        payload = record_pricing_label_policy(conn)
        row = conn.execute("SELECT * FROM mart_pricing_label_policy").fetchone()

        assert payload["policy_id"] == "pricing_label_policy_vwap_follow_v1"
        assert row["policy_id"] == "pricing_label_policy_vwap_follow_v1"
        assert row["event_calc_version"] == "v4_qfq_factor_adjusted_vwap_entry_dual_cost"
        assert row["follow_entry_price_mode"] == "entry_day_vwap_qfq"
        assert row["follow_entry_ref_price_mode"] == "entry_day_vwap_qfq_fallback_open"
        assert json.loads(row["policy_json"])["production_rules"]["stale_on_policy_change"] is True
        assert json.loads(row["policy_json"])["definition_sections"]["holding_period"]["baseline_days"] == 60


def test_pricing_label_policy_gate_passes_after_definitions_are_frozen():
    with duck_mem() as conn:
        result = record_pricing_label_policy_gate(
            conn,
            gate_run_id="pricing_gate_unit",
            gate_scope="model_training",
        )
        row = conn.execute("SELECT * FROM mart_pricing_label_policy_gate WHERE gate_run_id = 'pricing_gate_unit'").fetchone()

        assert result["gate_status"] == "pass"
        assert result["blockers"] == []
        assert result["warnings"] == []
        assert row["gate_status"] == "pass"
        assert json.loads(row["blockers_json"]) == result["blockers"]


def test_pricing_label_data_readiness_gate_blocks_missing_follow_labels_and_stale_artifacts():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                stock_code TEXT,
                date TEXT,
                forward_ret_60d DOUBLE
            );
            INSERT INTO fact_feature_panel_candidate VALUES ('000001', '2026-01-02', 0.1);

            CREATE TABLE fact_institution_event (
                notice_date TEXT,
                calc_version TEXT,
                calc_ref_price_mode TEXT
            );
            INSERT INTO fact_institution_event VALUES
                ('2026-01-01', 'old_version', 'old_price_mode');

            CREATE TABLE mart_multidim_model (
                model_id TEXT
            );
            INSERT INTO mart_multidim_model VALUES ('old_model');
            """
        )

        result = record_pricing_label_data_readiness_gate(
            conn,
            gate_run_id="data_ready_unit",
            gate_scope="model_training",
            feature_tables=["fact_feature_panel_candidate"],
        )
        row = conn.execute(
            "SELECT * FROM mart_pricing_label_data_readiness_gate WHERE gate_run_id = 'data_ready_unit'"
        ).fetchone()

        assert result["gate_status"] == "blocked"
        assert "follow_return_labels_missing" in result["blockers"]
        assert "event_returns_stale_for_pricing_policy" in result["blockers"]
        assert "mart_multidim_model_missing_pricing_policy_hash" in result["warnings"]
        assert result["evidence"]["feature_tables"]["fact_feature_panel_candidate"][
            "missing_required_follow_labels"
        ] == result["required_labels"]
        assert row["gate_status"] == "blocked"
        assert "follow_return_labels_missing" in json.loads(row["blockers_json"])


def test_pricing_label_data_readiness_gate_passes_when_follow_labels_and_policy_hash_match():
    policy = load_pricing_label_policy()
    with duck_mem() as conn:
        labels = [
            "follow_net_return_5d",
            "follow_net_return_10d",
            "follow_net_return_20d",
            "follow_net_return_60d",
            "follow_net_return_90d",
        ]
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                stock_code TEXT,
                date TEXT,
                follow_net_return_5d DOUBLE,
                follow_net_return_10d DOUBLE,
                follow_net_return_20d DOUBLE,
                follow_net_return_60d DOUBLE,
                follow_net_return_90d DOUBLE
            );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('000001', '2026-01-02', 0.01, 0.02, 0.03, 0.04, 0.05);

            CREATE TABLE fact_institution_event (
                notice_date TEXT,
                calc_version TEXT,
                calc_ref_price_mode TEXT
            );
            INSERT INTO fact_institution_event VALUES
                ('2026-01-01', 'v4_qfq_factor_adjusted_vwap_entry_dual_cost', 'entry_day_vwap_qfq_fallback_open');

            CREATE TABLE mart_multidim_model (
                model_id TEXT,
                pricing_policy_hash TEXT
            );
            """
        )
        conn.execute("INSERT INTO mart_multidim_model VALUES ('fresh_model', ?)", (policy.policy_hash(),))
        ensure_pricing_policy_table(conn)
        conn.execute(
            """
            INSERT INTO mart_follow_return_label_build
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "label_build_unit",
                "fact_feature_panel_candidate",
                policy.policy_id,
                policy.policy_hash(),
                policy.event_calc_version,
                policy.price_adjustment,
                policy.transaction_cost_bps,
                "[5, 10, 20, 60, 90]",
                json.dumps(labels),
                1,
                json.dumps({label: 1 for label in labels}),
                json.dumps({label: 1.0 for label in labels}),
                "2026-01-02",
                "2026-01-02",
                "2026-05-06T00:00:00",
            ),
        )
        conn.executemany(
            """
            INSERT INTO mart_follow_return_label_quality (
                run_id, feature_table, label_name, horizon_days,
                policy_id, policy_hash, event_calc_version,
                row_count, non_null_count, null_count,
                immature_null_count, mature_null_count,
                missing_signal_kline_count, missing_entry_price_count,
                missing_exit_price_count, unclassified_null_count,
                min_date, max_date, stock_max_date_min, stock_max_date_max,
                global_market_max_date, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "label_build_unit",
                    "fact_feature_panel_candidate",
                    label,
                    int(label.removeprefix("follow_net_return_").removesuffix("d")),
                    policy.policy_id,
                    policy.policy_hash(),
                    policy.event_calc_version,
                    1,
                    1,
                    0,
                    0,
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
                )
                for label in labels
            ],
        )

        result = record_pricing_label_data_readiness_gate(
            conn,
            gate_run_id="data_ready_pass_unit",
            gate_scope="model_training",
            feature_tables=["fact_feature_panel_candidate"],
        )

        assert result["gate_status"] == "pass"
        assert result["blockers"] == []


def test_pricing_label_data_readiness_allows_zero_row_candidate_panel_build():
    policy = load_pricing_label_policy()
    labels = [
        "follow_net_return_5d",
        "follow_net_return_10d",
        "follow_net_return_20d",
        "follow_net_return_60d",
        "follow_net_return_90d",
    ]
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                stock_code TEXT,
                date TEXT,
                follow_net_return_5d DOUBLE,
                follow_net_return_10d DOUBLE,
                follow_net_return_20d DOUBLE,
                follow_net_return_60d DOUBLE,
                follow_net_return_90d DOUBLE
            );
            CREATE TABLE fact_institution_event (
                notice_date TEXT,
                calc_version TEXT,
                calc_ref_price_mode TEXT
            );
            """
        )
        ensure_pricing_policy_table(conn)
        conn.execute(
            """
            INSERT INTO mart_follow_return_label_build
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "empty_label_build_unit",
                "fact_feature_panel_candidate",
                policy.policy_id,
                policy.policy_hash(),
                policy.event_calc_version,
                policy.price_adjustment,
                policy.transaction_cost_bps,
                "[5, 10, 20, 60, 90]",
                json.dumps(labels),
                0,
                json.dumps({label: 0 for label in labels}),
                json.dumps({label: 0.0 for label in labels}),
                None,
                None,
                "2026-05-06T00:00:00",
            ),
        )
        conn.executemany(
            """
            INSERT INTO mart_follow_return_label_quality (
                run_id, feature_table, label_name, horizon_days,
                policy_id, policy_hash, event_calc_version,
                row_count, non_null_count, null_count,
                immature_null_count, mature_null_count,
                missing_signal_kline_count, missing_entry_price_count,
                missing_exit_price_count, unclassified_null_count,
                min_date, max_date, stock_max_date_min, stock_max_date_max,
                global_market_max_date, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "empty_label_build_unit",
                    "fact_feature_panel_candidate",
                    label,
                    int(label.removeprefix("follow_net_return_").removesuffix("d")),
                    policy.policy_id,
                    policy.policy_hash(),
                    policy.event_calc_version,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "2026-05-06T00:00:00",
                )
                for label in labels
            ],
        )

        result = record_pricing_label_data_readiness_gate(
            conn,
            gate_run_id="data_ready_empty_candidate_unit",
            gate_scope="model_training",
            feature_tables=["fact_feature_panel_candidate"],
        )

        assert result["gate_status"] == "pass"
        assert result["blockers"] == []
        build = result["evidence"]["feature_tables"]["fact_feature_panel_candidate"]["follow_label_build"]
        assert build["row_count"] == 0
        assert build["zero_non_null_labels"] == []
