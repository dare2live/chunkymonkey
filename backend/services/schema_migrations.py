"""Schema migration and initialization orchestration."""

from __future__ import annotations

import logging
from datetime import datetime

from .db_connection import get_conn
from .schema_core import ensure_core_schema
from .schema_marts import ensure_mart_schema

logger = logging.getLogger("cm-api")

SCHEMA_MAINTENANCE_SQL = """
DROP TABLE IF EXISTS market_raw_holdings;
CREATE INDEX IF NOT EXISTS idx_raw_tdx_f10_fetched ON raw_tdx_f10_holder_research(fetched_at);
CREATE INDEX IF NOT EXISTS idx_raw_tdx_f10_stock ON raw_tdx_f10_holder_research(stock_code, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_t10_stock ON fact_top10_holder_period(stock_code, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_t10_holder ON fact_top10_holder_period(holder_name);
CREATE INDEX IF NOT EXISTS idx_t10_holder_norm ON fact_top10_holder_period(holder_name_norm);
CREATE INDEX IF NOT EXISTS idx_t10_effective ON fact_top10_holder_period(effective_date);
CREATE INDEX IF NOT EXISTS idx_t10_set_class ON fact_top10_holder_period(holder_set, share_class);
CREATE INDEX IF NOT EXISTS idx_plan_stock_announce ON fact_shareholder_plan(stock_code, announce_date DESC);
CREATE INDEX IF NOT EXISTS idx_plan_raw_hash ON fact_shareholder_plan(stock_code, raw_hash);
CREATE INDEX IF NOT EXISTS idx_trade_stock_date ON fact_shareholder_trade(stock_code, change_date DESC);
CREATE INDEX IF NOT EXISTS idx_trade_raw_hash ON fact_shareholder_trade(stock_code, raw_hash);
CREATE INDEX IF NOT EXISTS idx_trade_b_stock_date ON fact_shareholder_trade_tdx_b(stock_code, change_date DESC);
CREATE INDEX IF NOT EXISTS idx_trade_b_holder ON fact_shareholder_trade_tdx_b(holder_name_norm);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_stock_notice ON fact_shareholder_plan_tdx_f10(stock_code, source_available_date DESC);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_subject ON fact_shareholder_plan_tdx_f10(subject);
CREATE INDEX IF NOT EXISTS idx_raw_holder_count_stock_date ON raw_tdx_f10_holder_count_history(stock_code, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_fact_holder_count_stock_date ON fact_holder_count_period(stock_code, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_common_holder_name ON fact_common_major_holder_stock(major_holder_name);
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS report_date_text TEXT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS hold_ratio_text TEXT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS change_shares BIGINT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS net_profit_parent_text TEXT;
ALTER TABLE fact_common_major_holder_stock ADD COLUMN IF NOT EXISTS net_profit_deducted_text TEXT;
CREATE INDEX IF NOT EXISTS idx_fund_holding_name ON fact_fund_holding_tdx_f10(fund_name);
ALTER TABLE fact_fund_holding_tdx_f10 ADD COLUMN IF NOT EXISTS report_date_text TEXT;
ALTER TABLE fact_fund_holding_tdx_f10 ADD COLUMN IF NOT EXISTS float_a_ratio_text TEXT;
ALTER TABLE fact_fund_holding_tdx_f10 ADD COLUMN IF NOT EXISTS market_value_text TEXT;
CREATE INDEX IF NOT EXISTS idx_f10_extra_status_status ON raw_tdx_f10_extra_parse_status(status);
ALTER TABLE raw_tdx_f10_extra_parse_status ADD COLUMN IF NOT EXISTS fund_holding_rejected_rows INTEGER DEFAULT 0;
ALTER TABLE raw_tdx_f10_extra_parse_status ADD COLUMN IF NOT EXISTS shareholder_plan_rows INTEGER DEFAULT 0;
ALTER TABLE raw_tdx_f10_extra_parse_status ADD COLUMN IF NOT EXISTS status_reason TEXT;
ALTER TABLE raw_tdx_f10_extra_parse_status ADD COLUMN IF NOT EXISTS parser_version TEXT;
ALTER TABLE fact_controlling_shareholder ADD COLUMN control_chain_text TEXT;
CREATE INDEX IF NOT EXISTS idx_he_stock ON fact_holder_event(stock_code, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_he_holder ON fact_holder_event(holder_name_norm);
CREATE INDEX IF NOT EXISTS idx_he_event_type ON fact_holder_event(event_type);
CREATE INDEX IF NOT EXISTS idx_dim_data_asset_layer ON dim_data_asset(layer);
ALTER TABLE dim_data_asset ADD COLUMN deprecation_status TEXT DEFAULT 'active';
ALTER TABLE dim_data_asset ADD COLUMN deprecated_at TEXT;
ALTER TABLE dim_data_asset ADD COLUMN deprecated_reason TEXT;
ALTER TABLE dim_data_asset ADD COLUMN replacement_table TEXT;
ALTER TABLE dim_data_asset ADD COLUMN asset_grain TEXT;
ALTER TABLE dim_data_asset ADD COLUMN asset_cadence TEXT;
ALTER TABLE dim_data_asset ADD COLUMN coverage_policy TEXT;
ALTER TABLE dim_data_asset ADD COLUMN null_policy TEXT;
ALTER TABLE dim_data_asset ADD COLUMN pit_policy TEXT;
ALTER TABLE dim_data_asset ADD COLUMN intended_use TEXT;
ALTER TABLE dim_data_asset ADD COLUMN model_eligibility TEXT;
ALTER TABLE dim_data_asset ADD COLUMN strategy_eligibility TEXT;
ALTER TABLE dim_data_asset ADD COLUMN frontend_visibility TEXT;
ALTER TABLE dim_data_asset ADD COLUMN quality_gate_level TEXT;
CREATE INDEX IF NOT EXISTS idx_mart_data_health_snapshot ON mart_data_health(snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_mart_data_health_severity ON mart_data_health(severity, snapshot_at DESC);
ALTER TABLE mart_market_perception_daily ADD COLUMN IF NOT EXISTS hs300_ret_60d DOUBLE;
ALTER TABLE mart_market_perception_daily ADD COLUMN IF NOT EXISTS hs300_vol_20d DOUBLE;
ALTER TABLE mart_market_perception_daily ADD COLUMN IF NOT EXISTS breadth_ratio DOUBLE;
ALTER TABLE mart_market_perception_daily ADD COLUMN IF NOT EXISTS breadth_p75_90d DOUBLE;
ALTER TABLE mart_market_perception_daily ADD COLUMN IF NOT EXISTS limit_up_count INTEGER;
ALTER TABLE mart_market_perception_daily ADD COLUMN IF NOT EXISTS lhb_event_count INTEGER;
CREATE INDEX IF NOT EXISTS idx_mmp_date ON mart_market_perception_daily(snapshot_date);
CREATE TABLE IF NOT EXISTS mart_market_perception_audit_log (
    run_id                 TEXT PRIMARY KEY,
    started_at             TIMESTAMP NOT NULL,
    ended_at               TIMESTAMP,
    status                 VARCHAR NOT NULL,
    start_date             DATE NOT NULL,
    end_date               DATE NOT NULL,
    trading_days_requested INTEGER,
    rows_written           INTEGER,
    missing_days           INTEGER,
    score_min              DOUBLE,
    score_max              DOUBLE,
    guard_status           VARCHAR,
    input_row_counts_json  VARCHAR,
    notes                  VARCHAR,
    built_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mmp_audit_started ON mart_market_perception_audit_log(started_at DESC);
CREATE TABLE IF NOT EXISTS mart_market_perception_emotion_daily (
    snapshot_date              DATE NOT NULL,
    emotion_score              DOUBLE,
    emotion_state              VARCHAR,
    action_bias                VARCHAR,
    cycle_phase                VARCHAR,
    market_breadth             DOUBLE,
    up_count                   INTEGER,
    down_count                 INTEGER,
    limit_up_count             INTEGER,
    limit_down_count           INTEGER,
    first_board_count          INTEGER,
    second_board_count         INTEGER,
    third_plus_count           INTEGER,
    promotion_rate_1_to_2      DOUBLE,
    promotion_rate_2_to_3      DOUBLE,
    open_board_rate            DOUBLE,
    next_day_premium           DOUBLE,
    turnover_concentration     DOUBLE,
    lhb_event_count            INTEGER,
    n_stocks                   INTEGER,
    unknown_metrics            VARCHAR,
    source_engines             VARCHAR,
    pit_cutoff_date            DATE NOT NULL,
    built_at                   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_mmp_emotion_date ON mart_market_perception_emotion_daily(snapshot_date);
CREATE TABLE IF NOT EXISTS mart_market_perception_theme_daily (
    snapshot_date              DATE NOT NULL,
    theme_name                 VARCHAR NOT NULL,
    theme_score                DOUBLE,
    lifecycle_stage            VARCHAR,
    mainline_rank              INTEGER,
    is_mainline                BOOLEAN,
    diffusion_state            VARCHAR,
    sector_breadth             DOUBLE,
    sector_ret_20d             DOUBLE,
    sector_ret_60d             DOUBLE,
    sector_excess_20d          DOUBLE,
    sector_excess_60d          DOUBLE,
    price_vs_ma20              DOUBLE,
    price_vs_ma60              DOUBLE,
    limit_up_count             INTEGER,
    n_stocks                   INTEGER,
    top3_turnover_share        DOUBLE,
    pit_member_confidence      VARCHAR,
    source_engines             VARCHAR,
    pit_cutoff_date            DATE NOT NULL,
    built_at                   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, theme_name)
);
CREATE INDEX IF NOT EXISTS idx_mmp_theme_date ON mart_market_perception_theme_daily(snapshot_date);
CREATE TABLE IF NOT EXISTS mart_market_perception_under_reaction_daily (
    snapshot_date              DATE NOT NULL,
    stock_code                 VARCHAR NOT NULL,
    under_reaction_score       DOUBLE,
    fund_anomaly_score         DOUBLE,
    price_reaction_score       DOUBLE,
    capital_flow_score         DOUBLE,
    amount_expansion_score     DOUBLE,
    crowding_penalty           DOUBLE,
    ret_5d                     DOUBLE,
    ret_20d                    DOUBLE,
    amount_ratio_5_20          DOUBLE,
    lhb_count_30d              INTEGER,
    lhb_inst_buy_30d           INTEGER,
    lhb_net_buy_pct_30d        DOUBLE,
    exec_net_signal            DOUBLE,
    holder_count_change_q_pct  DOUBLE,
    theme_name                 VARCHAR,
    theme_score                DOUBLE,
    lifecycle_stage            VARCHAR,
    pit_cutoff_date            DATE NOT NULL,
    source_engines             VARCHAR,
    built_at                   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_mmp_under_date_score ON mart_market_perception_under_reaction_daily(snapshot_date, under_reaction_score DESC);
CREATE TABLE IF NOT EXISTS mart_market_perception_leader_follower_daily (
    snapshot_date              DATE NOT NULL,
    theme_name                 VARCHAR NOT NULL,
    leader_stock_code          VARCHAR NOT NULL,
    follower_stock_code        VARCHAR NOT NULL,
    relation_type              VARCHAR,
    lag_days                   INTEGER,
    leader_strength_score      DOUBLE,
    follower_lag_score         DOUBLE,
    diffusion_score            DOUBLE,
    leader_ret_5d              DOUBLE,
    leader_ret_20d             DOUBLE,
    follower_ret_1d            DOUBLE,
    follower_ret_3d            DOUBLE,
    follower_ret_5d            DOUBLE,
    follower_ret_20d           DOUBLE,
    follower_amount_ratio_5_20 DOUBLE,
    theme_score                DOUBLE,
    lifecycle_stage            VARCHAR,
    pit_member_confidence      VARCHAR,
    pit_cutoff_date            DATE NOT NULL,
    source_engines             VARCHAR,
    built_at                   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, theme_name, leader_stock_code, follower_stock_code)
);
CREATE INDEX IF NOT EXISTS idx_mmp_leader_follower_date_score ON mart_market_perception_leader_follower_daily(snapshot_date, diffusion_score DESC);
CREATE TABLE IF NOT EXISTS mart_market_perception_style_daily (
    snapshot_date              DATE NOT NULL,
    style_rotation_score       DOUBLE,
    style_bias                 VARCHAR,
    size_preference_score      DOUBLE,
    trend_preference_score     DOUBLE,
    crowding_risk_score        DOUBLE,
    overheat_reversal_risk     DOUBLE,
    small_ret_1d               DOUBLE,
    mid_ret_1d                 DOUBLE,
    large_ret_1d               DOUBLE,
    trend_ret_1d               DOUBLE,
    reversal_ret_1d            DOUBLE,
    top_decile_turnover_share  DOUBLE,
    hot_stock_share            DOUBLE,
    style_source               VARCHAR,
    emotion_score              DOUBLE,
    emotion_state              VARCHAR,
    pit_cutoff_date            DATE NOT NULL,
    source_engines             VARCHAR,
    built_at                   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_mmp_style_date ON mart_market_perception_style_daily(snapshot_date);
CREATE TABLE IF NOT EXISTS mart_market_perception_stock_context_daily (
    snapshot_date              DATE NOT NULL,
    stock_code                 VARCHAR NOT NULL,
    context_score              DOUBLE,
    context_state              VARCHAR,
    market_regime_score        DOUBLE,
    emotion_score              DOUBLE,
    emotion_state              VARCHAR,
    theme_name                 VARCHAR,
    theme_score                DOUBLE,
    lifecycle_stage            VARCHAR,
    under_reaction_score       DOUBLE,
    fund_anomaly_score         DOUBLE,
    leader_follow_score        DOUBLE,
    leader_stock_code          VARCHAR,
    chain_diffusion_score      DOUBLE,
    style_rotation_score       DOUBLE,
    style_bias                 VARCHAR,
    crowding_risk_score        DOUBLE,
    overheat_reversal_risk     DOUBLE,
    data_completeness_score    DOUBLE,
    missing_context_fields     VARCHAR,
    pit_cutoff_date            DATE NOT NULL,
    source_engines             VARCHAR,
    built_at                   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_mmp_stock_context_date_score ON mart_market_perception_stock_context_daily(snapshot_date, context_score DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_manifest_started ON mart_pipeline_run_manifest(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_manifest_name_status ON mart_pipeline_run_manifest(pipeline_name, status);
CREATE INDEX IF NOT EXISTS idx_source_watermark_domain ON mart_data_source_watermark(data_domain, source_tier);
CREATE INDEX IF NOT EXISTS idx_raw_tdx_gpcw_wide_report ON raw_tdx_gpcw_wide(report_date);
CREATE INDEX IF NOT EXISTS idx_mart_lineage_output ON mart_lineage(output_table);
CREATE INDEX IF NOT EXISTS idx_mart_lineage_status ON mart_lineage(last_status, last_run_at DESC);
CREATE INDEX IF NOT EXISTS idx_mart_model_lifecycle_status ON mart_model_lifecycle(status, deployed_at DESC);
CREATE INDEX IF NOT EXISTS idx_mart_feature_drift_snapshot ON mart_feature_drift(snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_mart_feature_drift_severity ON mart_feature_drift(severity, psi DESC);
CREATE INDEX IF NOT EXISTS idx_mart_feature_drift_feature_set ON mart_feature_drift(model_id, feature_set_id, snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_tdx_gpcw_auto_quarterly_feature ON fact_tdx_gpcw_auto_feature_quarterly(feature_set_id, feature_name);
CREATE INDEX IF NOT EXISTS idx_tdx_gpcw_auto_quarterly_asof ON fact_tdx_gpcw_auto_feature_quarterly(feature_set_id, stock_code, available_date);
CREATE INDEX IF NOT EXISTS idx_feature_candidate_date ON fact_feature_panel_candidate(feature_set_id, date);
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS close REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS forward_ret_5d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS forward_ret_10d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS forward_ret_60d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS forward_ret_90d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS follow_net_return_5d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS follow_net_return_10d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS follow_net_return_20d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS follow_net_return_60d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS follow_net_return_90d REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS common_holder_network_count REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS fund_holding_shares_tdx_f10 REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS fund_holding_float_a_ratio_tdx_f10 REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS fund_holding_market_value_tdx_f10 REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS holder_count_acceleration_tdx REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS social_security_shares_qoq REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS receivables_to_revenue REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS inventory_to_revenue REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS forecast_range_width REAL;
ALTER TABLE fact_feature_panel_candidate ADD COLUMN IF NOT EXISTS express_net_profit_yoy REAL;
CREATE INDEX IF NOT EXISTS idx_tdx_keep_panel_date ON fact_feature_panel_tdx_keep_challenger(feature_set_id, date);
ALTER TABLE mart_feature_candidate_score ADD COLUMN IF NOT EXISTS fold_same_sign_rate DOUBLE;
ALTER TABLE mart_feature_candidate_score ADD COLUMN IF NOT EXISTS fold_count INTEGER;
ALTER TABLE mart_feature_candidate_score ADD COLUMN IF NOT EXISTS sensitivity_json TEXT;
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_5d DOUBLE;
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_10d DOUBLE;
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_60d DOUBLE;
ALTER TABLE mart_feature_group_ablation ADD COLUMN IF NOT EXISTS rank_ic_90d DOUBLE;
CREATE INDEX IF NOT EXISTS idx_holding_topk_model ON mart_model_holding_topk_eval(model_id, label_name, top_k);
CREATE INDEX IF NOT EXISTS idx_model_feature_lineage_model ON mart_model_feature_lineage(model_id, lineage_status);
CREATE INDEX IF NOT EXISTS idx_data_deletion_run ON mart_data_deletion_record(deletion_run_id);
CREATE INDEX IF NOT EXISTS idx_data_deletion_table ON mart_data_deletion_record(table_name, delete_scope);
DROP TABLE IF EXISTS raw_fetch_batch;
CREATE INDEX IF NOT EXISTS idx_daas_updated ON dim_active_a_stock(updated_at);
CREATE INDEX IF NOT EXISTS idx_tdx_industry_l1 ON dim_stock_tdx_industry(tdx_l1);
CREATE INDEX IF NOT EXISTS idx_tdx_industry_l2 ON dim_stock_tdx_industry(tdx_l2);
CREATE INDEX IF NOT EXISTS idx_tdx_industry_l3 ON dim_stock_tdx_industry(tdx_l3);
CREATE INDEX IF NOT EXISTS idx_tdx_block_type ON dim_tdx_block_catalog(block_type);
CREATE INDEX IF NOT EXISTS idx_stock_tdx_block_name ON dim_stock_tdx_block(block_name);
CREATE INDEX IF NOT EXISTS idx_stock_tdx_block_cat ON dim_stock_tdx_block(block_category);
CREATE INDEX IF NOT EXISTS idx_inst_type ON inst_institutions(type);
CREATE INDEX IF NOT EXISTS idx_inst_enabled ON inst_institutions(enabled);
DROP TABLE IF EXISTS inst_name_aliases;
CREATE INDEX IF NOT EXISTS idx_ih_inst ON inst_holdings(institution_id);
CREATE INDEX IF NOT EXISTS idx_ih_stock ON inst_holdings(stock_code);
CREATE INDEX IF NOT EXISTS idx_ih_report ON inst_holdings(report_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ih_unique_holder_stock_report ON inst_holdings(holder_name, stock_code, report_date);
CREATE INDEX IF NOT EXISTS idx_event_type ON fact_institution_event(event_type);
CREATE INDEX IF NOT EXISTS idx_event_date ON fact_institution_event(report_date);
CREATE INDEX IF NOT EXISTS idx_event_notice ON fact_institution_event(notice_date);
CREATE INDEX IF NOT EXISTS idx_setup_snapshot_date ON fact_setup_snapshot(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_setup_snapshot_tag ON fact_setup_snapshot(setup_tag, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_setup_snapshot_stock ON fact_setup_snapshot(stock_code);
CREATE INDEX IF NOT EXISTS idx_gap_queue_dataset_status ON market_gap_queue(dataset, status);
CREATE INDEX IF NOT EXISTS idx_gap_queue_status_updated ON market_gap_queue(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_mcr_inst ON mart_current_relationship(institution_id);
CREATE INDEX IF NOT EXISTS idx_mcr_stock ON mart_current_relationship(stock_code);
CREATE INDEX IF NOT EXISTS idx_metf_snapshot ON mart_etf_snapshot_latest(snapshot_id);
"""

__all__ = ["init_db", "get_enabled_modules"]


def _table_columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(f"DESCRIBE {table_name}").fetchall()
    return {row["column_name"] if hasattr(row, "keys") else row[0] for row in rows}


def _apply_schema_maintenance(conn) -> None:
    conn.executescript(SCHEMA_MAINTENANCE_SQL)


def _execute_optional_ddl(conn, sql: str) -> None:
    try:
        conn.execute(sql)
    except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
        pass


def _rename_columns_if_present(
    conn,
    table_name: str,
    renames: tuple[tuple[str, str], ...],
) -> None:
    try:
        cols = _table_columns(conn, table_name)
    except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
        return
    statements = [
        f"ALTER TABLE {table_name} RENAME COLUMN {old_name} TO {new_name};"
        for old_name, new_name in renames
        if old_name in cols and new_name not in cols
    ]
    if not statements:
        return
    try:
        conn.executescript("\n".join(statements))
    except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
        pass


def init_db():
    conn = get_conn()
    try:
        ensure_core_schema(conn)
        ensure_mart_schema(conn)
        _apply_schema_maintenance(conn)
        conn.commit()
        try:
            conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN win_rate_90d REAL")
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        try:
            conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN win_rate_120d REAL")
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        for col_def in [
            "exit_event_count INTEGER",
            "exit_post_avg_gain_30d REAL",
            "exit_post_avg_gain_60d REAL",
            "exit_post_avg_gain_120d REAL",
            "exit_avoid_loss_rate_30d REAL",
            "exit_avoid_loss_rate_60d REAL",
            "exit_avoid_loss_rate_120d REAL",
        ]:
            try:
                conn.execute(f"ALTER TABLE mart_institution_profile ADD COLUMN {col_def}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        try:
            conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN total_win_rate REAL")
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        try:
            conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN quality_score REAL")
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        try:
            conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN recent_exit_count INTEGER DEFAULT 0")
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        for col in [
            "availability_source TEXT",
        ]:
            try:
                conn.execute(f"ALTER TABLE fact_top10_holder_period ADD COLUMN {col}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        for col in [
            "notice_date_source TEXT",
            "source_notice_date TEXT",
            "availability_deadline TEXT",
        ]:
            try:
                conn.execute(f"ALTER TABLE inst_holdings ADD COLUMN {col}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        for col in [
            "pricing_policy_id TEXT",
            "pricing_policy_hash TEXT",
        ]:
            try:
                conn.execute(f"ALTER TABLE mart_institution_profile ADD COLUMN {col}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        for col in [
            "stock_name TEXT",
            "reason TEXT",
            "last_error TEXT",
            "source_attempts INTEGER DEFAULT 0",
            "first_seen_at TEXT",
            "last_attempt_at TEXT",
            "resolved_at TEXT",
            "updated_at TEXT",
        ]:
            try:
                conn.execute(f"ALTER TABLE market_gap_queue ADD COLUMN {col}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        for col in ["action_score REAL", "leader_inst TEXT",
                     "leader_score REAL", "consensus_count INTEGER", "path_state TEXT",
                     "setup_tag TEXT", "setup_priority INTEGER", "setup_reason TEXT",
                     "setup_confidence TEXT", "setup_level TEXT", "setup_inst_id TEXT",
                     "setup_inst_name TEXT", "setup_event_type TEXT", "setup_industry_name TEXT",
                     "setup_score_raw REAL", "setup_execution_gate TEXT", "setup_execution_reason TEXT",
                     "industry_skill_raw REAL",
                     "industry_skill_grade INTEGER", "followability_grade INTEGER",
                     "premium_grade INTEGER", "report_recency_grade INTEGER",
                     "reliability_grade INTEGER", "crowding_bucket TEXT",
                     "crowding_yield_raw REAL", "crowding_yield_grade INTEGER",
                     "crowding_stability_raw REAL", "crowding_stability_grade INTEGER",
                     "crowding_fit_raw REAL", "crowding_fit_grade INTEGER",
                     "crowding_fit_sample INTEGER", "crowding_fit_source TEXT",
                     "report_age_days INTEGER",
                     "discovery_score REAL", "company_quality_score REAL",
                     "company_quality_score_source TEXT", "quality_feature_snapshot_date TEXT",
                     "stage_score REAL",
                     "raw_composite_priority_score REAL",
                     "composite_priority_score REAL", "composite_cap_score REAL",
                     "composite_cap_reason TEXT", "stock_archetype TEXT",
                     "priority_pool TEXT", "priority_pool_reason TEXT",
                     "stock_gate TEXT", "stock_gate_reason TEXT",
                     "score_highlights TEXT", "score_risks TEXT"]:
            try:
                conn.execute(f"ALTER TABLE mart_stock_trend ADD COLUMN {col}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        for col in [
            "report_season TEXT",
            "cost_window_start TEXT",
            "cost_window_end TEXT",
            "inst_ref_cost REAL",
            "inst_cost_method TEXT",
            "premium_pct REAL",
            "premium_bucket TEXT",
            "follow_gate TEXT",
            "follow_gate_reason TEXT",
            "tradable_date TEXT",
            "price_entry REAL",
            "price_entry_status TEXT",
            "notice_date_source TEXT",
            "source_notice_date TEXT",
            "availability_deadline TEXT",
            "gain_10d REAL", "gain_30d REAL", "gain_60d REAL",
            "gain_90d REAL", "gain_120d REAL",
            "excess_30d REAL", "excess_60d REAL", "excess_120d REAL",
            "max_drawdown_30d REAL", "max_drawdown_60d REAL",
            "return_to_now REAL",
            "max_rally_to_now REAL",
            "max_drawdown_to_now REAL",
            "path_state TEXT",
            "date_quality TEXT",
            "calc_version TEXT",
            "calc_ref_price_mode TEXT",
            "calc_completed_at TEXT",
        ]:
            try:
                conn.execute(f"ALTER TABLE fact_institution_event ADD COLUMN {col}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        for col in [
            "stock_name TEXT",
            "setup_priority INTEGER",
            "setup_reason TEXT",
            "setup_confidence TEXT",
            "setup_level TEXT",
            "setup_inst_name TEXT",
            "setup_event_type TEXT",
            "setup_industry_name TEXT",
            "snapshot_tdx_l1 TEXT",
            "snapshot_tdx_l2 TEXT",
            "snapshot_tdx_l3 TEXT",
            "snapshot_tdx_l1_name TEXT",
            "snapshot_tdx_l2_name TEXT",
            "snapshot_tdx_l3_name TEXT",
            "action_score REAL",
            "discovery_score REAL",
            "company_quality_score REAL",
            "company_quality_score_source TEXT",
            "quality_feature_snapshot_date TEXT",
            "stage_score REAL",
            "raw_composite_priority_score REAL",
            "composite_priority_score REAL",
            "composite_cap_score REAL",
            "composite_cap_reason TEXT",
            "stock_archetype TEXT",
            "priority_pool TEXT",
            "priority_pool_reason TEXT",
            "score_highlights TEXT",
            "score_risks TEXT",
            "latest_report_date TEXT",
            "latest_notice_date TEXT",
            "report_age_days INTEGER",
            "setup_score_raw REAL",
            "setup_execution_gate TEXT",
            "setup_execution_reason TEXT",
            "industry_skill_raw REAL",
            "industry_skill_grade INTEGER",
            "followability_grade INTEGER",
            "premium_grade INTEGER",
            "report_recency_grade INTEGER",
            "reliability_grade INTEGER",
            "crowding_bucket TEXT",
            "crowding_yield_raw REAL",
            "crowding_yield_grade INTEGER",
            "crowding_stability_raw REAL",
            "crowding_stability_grade INTEGER",
            "crowding_fit_raw REAL",
            "crowding_fit_grade INTEGER",
            "crowding_fit_sample INTEGER",
            "crowding_fit_source TEXT",
            "entry_trade_date TEXT",
            "entry_price REAL",
            "current_trade_date TEXT",
            "current_price REAL",
            "gain_to_now REAL",
            "gain_10d REAL",
            "gain_30d REAL",
            "gain_60d REAL",
            "max_drawdown_10d REAL",
            "max_drawdown_30d REAL",
            "max_drawdown_60d REAL",
            "matured_10d INTEGER DEFAULT 0",
            "matured_30d INTEGER DEFAULT 0",
            "matured_60d INTEGER DEFAULT 0",
        ]:
            try:
                conn.execute(f"ALTER TABLE fact_setup_snapshot ADD COLUMN {col}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_setup_snapshot_tdx1_date "
                "ON fact_setup_snapshot(snapshot_tdx_l1, snapshot_date)"
            )
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        conn.executescript(
            """
            DROP INDEX IF EXISTS idx_setup_snapshot_sw1_date;
            DROP INDEX IF EXISTS idx_dsi_l1;
            DROP INDEX IF EXISTS idx_dsi_l2;
            """
        )
        sw_drop_plan = [
            ("fact_setup_snapshot", "snapshot_sw_level1"),
            ("fact_setup_snapshot", "snapshot_sw_level2"),
            ("fact_setup_snapshot", "snapshot_sw_level3"),
            ("mart_current_relationship", "sw_level1"),
            ("mart_current_relationship", "sw_level2"),
            ("mart_current_relationship", "sw_level3"),
            ("dim_stock_tdx_industry", "sw_x_legacy"),
        ]
        for tbl, col in sw_drop_plan:
            _execute_optional_ddl(conn, f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS {col}")
        try:
            conn.execute("DROP TABLE IF EXISTS dim_stock_industry")
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        try:
            conn.execute("DROP TABLE IF EXISTS fact_institution_event_industry_snapshot")
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        for tbl in ["mart_institution_profile", "mart_institution_industry_stat",
                     "mart_stock_trend"]:
            try:
                conn.execute(
                    f"ALTER TABLE {tbl} ADD COLUMN data_completeness TEXT DEFAULT 'complete'"
                )
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        try:
            conn.execute(
                "ALTER TABLE mart_institution_industry_stat ADD COLUMN tdx_code TEXT"
            )
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        try:
            cols = _table_columns(conn, "mart_institution_industry_stat")
            if "sw_level" in cols and "industry_level" not in cols:
                conn.execute(
                    "ALTER TABLE mart_institution_industry_stat RENAME COLUMN sw_level TO industry_level"
                )
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        conn.executescript(
            """
            DROP INDEX IF EXISTS idx_event_industry_snapshot_l1;
            DROP INDEX IF EXISTS idx_event_industry_snapshot_l2;
            """
        )
        try:
            conn.execute("DROP TABLE IF EXISTS fact_institution_event_industry_snapshot")
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        for table in ("fact_stock_archetype", "dim_stock_archetype_latest"):
            _rename_columns_if_present(
                conn,
                table,
                (
                    ("sw_level1", "tdx_l1_name"),
                    ("sw_level2", "tdx_l2_name"),
                ),
            )
        quality_tables = ("fact_stock_quality_features", "dim_stock_quality_latest")
        for table in quality_tables:
            _rename_columns_if_present(
                conn,
                table,
                (
                    ("sw_level1", "tdx_l1"),
                    ("sw_level2", "tdx_l2"),
                ),
            )
        turtle_tables = ("fact_stock_turtle_features", "dim_stock_turtle_latest")
        for table in turtle_tables:
            _rename_columns_if_present(
                conn,
                table,
                (
                    ("sw_level1", "tdx_l1_name"),
                    ("sw_level2", "tdx_l2_name"),
                ),
            )
        for col in ["score_basis TEXT", "score_confidence TEXT",
                     "historical_median_holding_days INTEGER",
                     "current_avg_held_days INTEGER"]:
            try:
                conn.execute(f"ALTER TABLE mart_institution_profile ADD COLUMN {col}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        for col in [
            "buy_event_count INTEGER",
            "buy_avg_gain_30d REAL", "buy_avg_gain_60d REAL", "buy_avg_gain_120d REAL",
            "buy_win_rate_30d REAL", "buy_win_rate_60d REAL", "buy_win_rate_120d REAL",
            "buy_median_max_drawdown_30d REAL", "buy_median_max_drawdown_60d REAL",
            "avg_premium_pct REAL",
            "safe_follow_event_count INTEGER",
            "safe_follow_win_rate_30d REAL",
            "safe_follow_avg_gain_30d REAL",
            "safe_follow_avg_drawdown_30d REAL",
            "premium_discount_event_count INTEGER",
            "premium_discount_win_rate_30d REAL",
            "premium_near_cost_event_count INTEGER",
            "premium_near_cost_win_rate_30d REAL",
            "premium_premium_event_count INTEGER",
            "premium_premium_win_rate_30d REAL",
            "premium_high_event_count INTEGER",
            "premium_high_win_rate_30d REAL",
            "signal_transfer_efficiency_30d REAL",
            "followability_hint TEXT",
            "followability_score REAL",
            "followability_confidence TEXT",
            "main_industry_1 TEXT", "main_industry_2 TEXT", "main_industry_3 TEXT",
            "best_industry_1 TEXT", "best_industry_2 TEXT", "best_industry_3 TEXT",
        ]:
            try:
                conn.execute(f"ALTER TABLE mart_institution_profile ADD COLUMN {col}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        for tbl in (
            "mart_current_relationship",
            "dim_stock_industry_context_latest",
            "fact_stock_industry_context",
            "fact_stock_archetype", "dim_stock_archetype_latest",
            "fact_stock_quality_features", "dim_stock_quality_latest",
            "fact_stock_turtle_features", "dim_stock_turtle_latest",
        ):
            try:
                cols = _table_columns(conn, tbl)
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                continue
            for old, new in (
                ("sw_l1", "tdx_l1"), ("sw_l2", "tdx_l2"), ("sw_l3", "tdx_l3"),
                ("sw_l1_name", "tdx_l1_name"), ("sw_l2_name", "tdx_l2_name"), ("sw_l3_name", "tdx_l3_name"),
            ):
                if old in cols and new not in cols:
                    try:
                        conn.execute(f"ALTER TABLE {tbl} RENAME COLUMN {old} TO {new}")
                        cols.discard(old)
                        cols.add(new)
                    except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                        pass
        try:
            cols = _table_columns(conn, "mart_institution_industry_stat")
            if "industry_code" in cols and "tdx_code" in cols:
                conn.execute(
                    "UPDATE mart_institution_industry_stat "
                    "SET tdx_code = COALESCE(tdx_code, industry_code) "
                    "WHERE tdx_code IS NULL AND industry_code IS NOT NULL"
                )
                conn.execute("ALTER TABLE mart_institution_industry_stat DROP COLUMN industry_code")
            elif "industry_code" in cols and "tdx_code" not in cols:
                conn.execute("ALTER TABLE mart_institution_industry_stat RENAME COLUMN industry_code TO tdx_code")
        except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
            pass
        for col in [
            "report_season TEXT",
            "inst_ref_cost REAL",
            "inst_cost_method TEXT",
            "premium_pct REAL",
            "premium_bucket TEXT",
            "follow_gate TEXT",
            "follow_gate_reason TEXT",
            "tdx_l1 TEXT",
            "tdx_l2 TEXT",
            "tdx_l3 TEXT",
            "tdx_l1_name TEXT",
            "tdx_l2_name TEXT",
            "tdx_l3_name TEXT",
        ]:
            try:
                conn.execute(f"ALTER TABLE mart_current_relationship ADD COLUMN {col}")
            except Exception:  # rule-compliance: ok evidence=db-py-split-schema-defensive
                pass
        conn.commit()
        existing = conn.execute("SELECT COUNT(*) FROM exclusion_categories").fetchone()[0]
        if existing == 0:
            now = datetime.now().isoformat()
            categories = [
                ("ST", "ST/*ST 股票", 1),
                ("BSE", "北交所 (8/9开头)", 1),
                ("NEEQ", "新三板 (4开头)", 1),
                ("OTC", "老三板 (400开头)", 1),
                ("B_SHARE", "B股 (200/900开头)", 1),
                ("CDR", "CDR 存托凭证", 1),
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO exclusion_categories (category, label, enabled, updated_at) VALUES (?, ?, ?, ?)",
                [(cat, label, enabled, now) for cat, label, enabled in categories],
            )
            conn.commit()
            logger.info(f"[DB] 初始化 {len(categories)} 个排除类别")
        from services.financial_client import ensure_tables as _ensure_fin_tables
        _ensure_fin_tables(conn)
        from services.financial_indicator_client import ensure_tables as _ensure_fin_indicator_tables
        _ensure_fin_indicator_tables(conn)
        from services.capital_client import ensure_tables as _ensure_capital_tables
        _ensure_capital_tables(conn)
        from services.industry_context_engine import ensure_tables as _ensure_industry_context_tables
        _ensure_industry_context_tables(conn)
        from services.screening_engine import ensure_tables as _ensure_screen_tables
        _ensure_screen_tables(conn)
        from services.sector_momentum import ensure_tables as _ensure_sector_tables
        _ensure_sector_tables(conn)
        conn.execute("DROP TABLE IF EXISTS dim_asset_universe")
        conn.commit()
        conn.execute("""
            INSERT OR IGNORE INTO app_settings (key, value, updated_at)
            VALUES ('module_etf_enabled', '1', CURRENT_TIMESTAMP),
                   ('module_akquant_enabled', '0', CURRENT_TIMESTAMP)
        """)
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'scoring.stock.%'")
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'scoring.timing.%'")
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'scoring.path.%'")
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'scoring.event_type.%'")
        conn.commit()
        try:
            for sql in (
                "CREATE INDEX IF NOT EXISTS idx_fie_stock ON fact_institution_event(stock_code, report_date DESC)",
                "CREATE INDEX IF NOT EXISTS idx_fie_holder ON fact_institution_event(holder_name, report_date DESC)",
                "CREATE INDEX IF NOT EXISTS idx_fie_notice_source ON fact_institution_event(notice_date_source)",
                "CREATE INDEX IF NOT EXISTS idx_mdr_date_rank ON mart_daily_recommendation(snapshot_date DESC, rank_in_date)",
                "CREATE INDEX IF NOT EXISTS idx_rf_calc_date ON fact_risk_factors(calc_date DESC)",
                "CREATE INDEX IF NOT EXISTS idx_po_snap_model ON mart_prediction_outcome(snapshot_date DESC, model_id)",
            ):
                try:
                    conn.execute(sql)
                except Exception as exc:
                    logger.debug(f"[index] 跳过 {sql[:60]}...: {exc}")
            conn.commit()
        except Exception as exc:
            logger.warning(f"[DB] 关键索引创建失败 (非致命): {exc}")
        try:
            from services.schema_versions import (
                ensure_schema_version_table,
                recreate_views,
                detect_drift,
                record_all_baselines,
            )
            ensure_schema_version_table(conn)
            view_results = recreate_views(conn)
            for view_name, result in view_results.items():
                if result != "ok":
                    logger.warning(f"[schema] view {view_name}: {result}")
            n_recorded = conn.execute(
                "SELECT COUNT(*) FROM dim_schema_version"
            ).fetchone()[0]
            if n_recorded == 0:
                n_baseline = record_all_baselines(conn)
                logger.info(f"[schema] 首次启动 baseline: {n_baseline} 张派生表标记为当前期望版本")
            drifts = detect_drift(conn)
            if drifts:
                logger.warning(
                    f"[schema] 检测到 {len(drifts)} 张派生表 schema drift (启动后请去系统页 → 派生层版本 查看):"
                )
                for d in drifts[:5]:  # 头 5 个详细打
                    logger.warning(
                        f"  - {d['table_name']}: expected={d['expected']} "
                        f"actual={d['actual']} ({d['drift_type']})"
                    )
                if len(drifts) > 5:
                    logger.warning(f"  ... 及 {len(drifts) - 5} 张其他")
        except Exception as exc:
            logger.warning(f"[DB] schema_versions 初始化失败 (非致命): {exc}")
        logger.info("[DB] 数据库初始化完成")
    finally:
        conn.close()

def get_enabled_modules(conn) -> dict:
    rows = conn.execute("SELECT key, value FROM app_settings WHERE key LIKE 'module_%_enabled'").fetchall()
    modules = {"etf": True, "akquant": False}
    for r in rows:
        key = r["key"].replace("module_", "").replace("_enabled", "")
        modules[key] = str(r["value"]) == "1"
    return modules
