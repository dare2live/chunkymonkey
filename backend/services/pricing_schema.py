"""DDL for pricing and label policy marts."""
from __future__ import annotations


PRICING_POLICY_DDL = """
CREATE TABLE IF NOT EXISTS mart_pricing_label_policy (
    policy_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    policy_hash TEXT NOT NULL,
    event_calc_version TEXT NOT NULL,
    follow_entry_price_mode TEXT NOT NULL,
    follow_entry_ref_price_mode TEXT NOT NULL,
    transaction_cost_bps DOUBLE,
    policy_json TEXT NOT NULL,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_pricing_label_policy_gate (
    gate_run_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    gate_scope TEXT NOT NULL,
    gate_status TEXT NOT NULL,
    blockers_json TEXT,
    warnings_json TEXT,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_pricing_label_data_readiness_gate (
    gate_run_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    gate_scope TEXT NOT NULL,
    gate_status TEXT NOT NULL,
    feature_tables_json TEXT,
    required_labels_json TEXT,
    blockers_json TEXT,
    warnings_json TEXT,
    evidence_json TEXT,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_follow_return_label_build (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    event_calc_version TEXT NOT NULL,
    price_adjustment TEXT NOT NULL,
    transaction_cost_bps DOUBLE NOT NULL,
    horizons_json TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    row_count BIGINT,
    label_non_null_json TEXT,
    label_coverage_json TEXT,
    min_date TEXT,
    max_date TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_table)
);

CREATE TABLE IF NOT EXISTS mart_follow_return_label_quality (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    event_calc_version TEXT NOT NULL,
    row_count BIGINT NOT NULL,
    non_null_count BIGINT NOT NULL,
    null_count BIGINT NOT NULL,
    immature_null_count BIGINT NOT NULL,
    mature_null_count BIGINT NOT NULL,
    missing_signal_kline_count BIGINT NOT NULL,
    missing_entry_price_count BIGINT NOT NULL,
    missing_exit_price_count BIGINT NOT NULL,
    unclassified_null_count BIGINT NOT NULL,
    min_date TEXT,
    max_date TEXT,
    stock_max_date_min TEXT,
    stock_max_date_max TEXT,
    global_market_max_date TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_table, label_name)
);
"""

