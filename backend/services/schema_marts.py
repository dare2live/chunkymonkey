"""Mart table creation DDL."""

from __future__ import annotations

MART_SCHEMA_SQL = """
            CREATE TABLE IF NOT EXISTS mart_data_health (
                table_name         TEXT NOT NULL,
                snapshot_at        TIMESTAMP NOT NULL,
                row_count          BIGINT,
                last_data_date     TEXT,                      -- MAX(date_column) 实测
                last_writer_at     TIMESTAMP,                 -- 最近 writer 成功时间 (推: step_status)
                null_rate_pct      DOUBLE,                    -- 关键字段 NULL 比例
                source_tier_dist   TEXT,                      -- JSON: {1: 5179, 2: 21, 3: 0}
                freshness_hours    DOUBLE,                    -- 数据距 now() 时长
                freshness_ok       BOOLEAN,                   -- 是否在 SLA 内
                severity           TEXT NOT NULL,             -- 'green' / 'yellow' / 'red'
                issue_summary      TEXT,                      -- 红/黄时填具体原因
                PRIMARY KEY (table_name, snapshot_at)
            );

            CREATE TABLE IF NOT EXISTS mart_pipeline_run_manifest (
                run_id                TEXT PRIMARY KEY,
                pipeline_name         TEXT NOT NULL,
                status                TEXT NOT NULL,          -- success / failed / skipped / running
                started_at            TIMESTAMP,
                ended_at              TIMESTAMP,
                duration_s            DOUBLE,
                commit_sha            TEXT,
                command               TEXT,
                cwd                   TEXT,
                input_tables_json     TEXT,
                output_tables_json    TEXT,
                input_row_counts_json TEXT,
                output_row_counts_json TEXT,
                model_id              TEXT,
                feature_group         TEXT,
                label_name            TEXT,
                holding_period        INTEGER,
                gate_result           TEXT,
                blockers_json         TEXT,
                perf_summary_json     TEXT,
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS mart_data_source_watermark (
                data_domain          TEXT NOT NULL,
                source_name          TEXT NOT NULL,
                source_tier          SMALLINT NOT NULL,
                last_success_at      TIMESTAMP,
                last_data_date       TEXT,
                last_raw_hash        TEXT,
                next_check_at        TIMESTAMP,
                consecutive_failures INTEGER DEFAULT 0,
                fallback_active      BOOLEAN DEFAULT FALSE,
                fallback_reason      TEXT,
                row_count            BIGINT,
                parser_version       TEXT,
                updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (data_domain, source_name, source_tier)
            );

            CREATE TABLE IF NOT EXISTS mart_market_perception_daily (
                snapshot_date       DATE NOT NULL,
                regime_score        DOUBLE,
                breadth_state       VARCHAR,
                volatility_state    VARCHAR,
                sentiment_phase     VARCHAR,
                hs300_ret_60d       DOUBLE,
                hs300_vol_20d       DOUBLE,
                breadth_ratio       DOUBLE,
                breadth_p75_90d     DOUBLE,
                limit_up_count      INTEGER,
                lhb_event_count     INTEGER,
                n_obs_days          INTEGER,
                source_engines      VARCHAR,
                pit_cutoff_date     DATE NOT NULL,
                built_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (snapshot_date)
            );
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

            CREATE TABLE IF NOT EXISTS mart_lineage (
                lineage_id         TEXT PRIMARY KEY,            -- e.g. 'mart_daily_recommendation/topk_v1'
                output_table       TEXT NOT NULL,
                input_tables       TEXT,                        -- JSON 数组
                sql_text           TEXT,                        -- 完整 SQL (或脚本入口)
                sql_hash           TEXT,                        -- sha256(sql_text)[:16] — 变更检测
                version            TEXT DEFAULT 'v1',
                owner              TEXT,                        -- 模块路径或责任人
                description        TEXT,
                last_run_at        TIMESTAMP,
                last_row_count     BIGINT,
                last_status        TEXT,                        -- 'ok' / 'failed' / 'pending'
                last_error         TEXT,
                last_runtime_s     DOUBLE,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS mart_model_lifecycle (
                model_id              TEXT PRIMARY KEY,
                status                TEXT NOT NULL,            -- 'champion' / 'challenger' / 'retired'
                deployed_at           TIMESTAMP,                -- 进入 champion 时间
                retired_at            TIMESTAMP,
                promoted_from         TEXT,                     -- 上一个 champion model_id
                ic_holdout            DOUBLE,                   -- holdout 段 RankIC
                ic_walkforward_avg    DOUBLE,                   -- walkforward 平均 RankIC
                ic_walkforward_std    DOUBLE,                   -- walkforward 稳定性
                drift_score           DOUBLE,                   -- 平均 PSI (越大越漂)
                deploy_decision_notes TEXT,                     -- 部署决策原因
                training_config       TEXT,                     -- JSON: {n_features, optuna_trials, ...}
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS mart_feature_drift (
                snapshot_at      TIMESTAMP NOT NULL,
                model_id         TEXT,                          -- 关联 mart_model_lifecycle (可空 = 全局基线)
                feature_set_id   TEXT,                          -- 候选 feature_set 过滤后的漂移证据
                feature          TEXT NOT NULL,
                psi              DOUBLE,                        -- Population Stability Index
                n_train          BIGINT,
                n_recent         BIGINT,
                window_days      INTEGER,                       -- 最近 N 天作为 recent 样本
                severity         TEXT NOT NULL,                 -- 'ok' / 'warn' / 'critical'
                notes            TEXT,
                PRIMARY KEY (snapshot_at, model_id, feature)
            );

            CREATE TABLE IF NOT EXISTS mart_tdx_data_need_coverage (
                need_id TEXT PRIMARY KEY,
                need_name TEXT NOT NULL,
                consumer TEXT,
                current_source TEXT,
                tdxhub_capability TEXT,
                tdx_coverage_level TEXT,
                preferred_source TEXT NOT NULL,
                fallback_source TEXT,
                action TEXT NOT NULL,
                notes TEXT,
                built_at TEXT
            );

            CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_pit_audit (
                audit_run_id TEXT NOT NULL,
                feature_set_id TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                checked_rows INTEGER,
                violation_rows INTEGER,
                status TEXT NOT NULL,
                notes TEXT,
                built_at TEXT,
                PRIMARY KEY (audit_run_id, feature_set_id, feature_name)
            );

            CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_feature_score (
                run_id TEXT NOT NULL,
                feature_set_id TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                feature_family TEXT,
                coverage_pct DOUBLE,
                rank_ic DOUBLE,
                fold_same_sign_rate DOUBLE,
                horizon_sensitivity TEXT,
                selected BOOLEAN DEFAULT FALSE,
                rejection_reason TEXT,
                built_at TEXT,
                PRIMARY KEY (run_id, feature_name)
            );

            CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_feature_cluster (
                run_id TEXT NOT NULL,
                feature_set_id TEXT NOT NULL,
                cluster_id TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                representative_feature TEXT,
                corr_to_representative DOUBLE,
                built_at TEXT,
                PRIMARY KEY (run_id, feature_name)
            );

            CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_optuna_run (
                run_id TEXT PRIMARY KEY,
                feature_set_id TEXT NOT NULL,
                trials INTEGER,
                objective_score DOUBLE,
                selected_features_json TEXT,
                rejected_features_json TEXT,
                promote_to_champion BOOLEAN DEFAULT FALSE,
                notes TEXT,
                built_at TEXT
            );

            CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_retention_decision (
                decision_run_id TEXT NOT NULL,
                feature_set_id TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                feature_family TEXT,
                decision TEXT NOT NULL,
                primary_reason TEXT,
                coverage_pct DOUBLE,
                pit_violation_rows INTEGER,
                mean_rank_ic DOUBLE,
                fold_same_sign_rate DOUBLE,
                notes TEXT,
                built_at TEXT,
                PRIMARY KEY (decision_run_id, feature_set_id, feature_name)
            );

            CREATE TABLE IF NOT EXISTS mart_data_source_reassignment_proposal (
                table_name TEXT PRIMARY KEY,
                current_source TEXT,
                proposed_primary_source TEXT NOT NULL,
                fallback_source TEXT,
                migration_required BOOLEAN DEFAULT FALSE,
                risk TEXT,
                reason TEXT,
                built_at TEXT
            );

            CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_challenger_report (
                challenger_run_id TEXT PRIMARY KEY,
                feature_set_id TEXT NOT NULL,
                decision_run_id TEXT NOT NULL,
                n_features INTEGER,
                rank_ic DOUBLE,
                long_short_return DOUBLE,
                max_drawdown DOUBLE,
                turnover DOUBLE,
                top_features_json TEXT,
                promote_to_champion BOOLEAN DEFAULT FALSE,
                built_at TEXT
            );

            CREATE TABLE IF NOT EXISTS mart_feature_candidate_score (
                run_id           TEXT NOT NULL,
                feature_set_id   TEXT NOT NULL,
                feature_name     TEXT NOT NULL,
                feature_group    TEXT,
                coverage_pct     DOUBLE,
                missing_pct      DOUBLE,
                rank_ic          DOUBLE,
                fold_same_sign_rate DOUBLE,
                fold_count       INTEGER,
                sensitivity_json TEXT,
                selected         BOOLEAN DEFAULT FALSE,
                rejection_reason TEXT,
                built_at         TEXT,
                PRIMARY KEY (run_id, feature_name)
            );

            CREATE TABLE IF NOT EXISTS mart_feature_group_ablation (
                run_id           TEXT NOT NULL,
                feature_set_id   TEXT NOT NULL,
                group_name       TEXT NOT NULL,
                n_features       INTEGER,
                rank_ic_full     DOUBLE,
                rank_ic_without_group DOUBLE,
                rank_ic_delta    DOUBLE,
                rank_ic_5d       DOUBLE,
                rank_ic_10d      DOUBLE,
                rank_ic_60d      DOUBLE,
                rank_ic_90d      DOUBLE,
                feature_cols_json TEXT,
                built_at         TEXT,
                PRIMARY KEY (run_id, group_name)
            );

            CREATE TABLE IF NOT EXISTS mart_model_selection_run (
                run_id           TEXT PRIMARY KEY,
                feature_set_id   TEXT NOT NULL,
                method           TEXT NOT NULL,
                label_name       TEXT,
                objective_score  DOUBLE,
                selected_features_json TEXT,
                rejected_features_json TEXT,
                trials           INTEGER,
                promote_to_champion BOOLEAN DEFAULT FALSE,
                notes            TEXT,
                built_at         TEXT
            );

            CREATE TABLE IF NOT EXISTS mart_feature_pit_audit (
                audit_run_id TEXT NOT NULL,
                feature_set_id TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                source_table TEXT NOT NULL,
                sample_rows INTEGER,
                checked_rows INTEGER,
                violation_rows INTEGER,
                max_source_available_date TEXT,
                max_signal_date TEXT,
                status TEXT NOT NULL,
                notes TEXT,
                audited_at TEXT,
                PRIMARY KEY (audit_run_id, feature_set_id, feature_name, source_table)
            );

            CREATE TABLE IF NOT EXISTS mart_candidate_walkforward_eval (
                run_id TEXT NOT NULL,
                feature_set_id TEXT NOT NULL,
                fold_id TEXT NOT NULL,
                train_start TEXT,
                train_end TEXT,
                valid_start TEXT,
                valid_end TEXT,
                holdout_start TEXT,
                holdout_end TEXT,
                feature_name TEXT NOT NULL,
                feature_group TEXT,
                rank_ic DOUBLE,
                icir DOUBLE,
                same_sign BOOLEAN,
                long_short_return DOUBLE,
                turnover DOUBLE,
                turnover_adjusted_return DOUBLE,
                max_drawdown DOUBLE,
                label_name TEXT NOT NULL,
                built_at TEXT,
                PRIMARY KEY (run_id, fold_id, feature_name, label_name)
            );

            CREATE TABLE IF NOT EXISTS mart_model_holding_topk_eval (
                run_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                feature_table TEXT NOT NULL,
                feature_set_id TEXT,
                label_name TEXT NOT NULL,
                holding_period INTEGER NOT NULL,
                top_k INTEGER NOT NULL,
                cost_bps DOUBLE NOT NULL,
                n_dates INTEGER,
                n_signals INTEGER,
                ic_mean DOUBLE,
                rank_ic_mean DOUBLE,
                avg_top_return DOUBLE,
                avg_benchmark_return DOUBLE,
                avg_excess_return DOUBLE,
                long_short_spread DOUBLE,
                winrate DOUBLE,
                avg_turnover DOUBLE,
                max_drawdown DOUBLE,
                after_cost_return DOUBLE,
                avg_industry_hhi DOUBLE,
                recommendation TEXT,
                notes TEXT,
                built_at TEXT NOT NULL,
                PRIMARY KEY (run_id, model_id, label_name, top_k, cost_bps)
            );

            CREATE TABLE IF NOT EXISTS mart_model_feature_lineage (
                model_id TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                feature_group TEXT NOT NULL,
                source_table TEXT NOT NULL,
                upstream_source TEXT,
                source_tier SMALLINT,
                source_date_col TEXT,
                available_date_col TEXT,
                parser_version TEXT,
                pit_required BOOLEAN,
                lineage_status TEXT NOT NULL,
                notes TEXT,
                built_at TEXT NOT NULL,
                PRIMARY KEY (model_id, feature_name)
            );

            CREATE TABLE IF NOT EXISTS mart_feature_retention_decision (
                decision_run_id TEXT NOT NULL,
                feature_set_id TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                feature_group TEXT,
                decision TEXT NOT NULL,
                primary_reason TEXT,
                coverage_pct DOUBLE,
                pit_violation_rows INTEGER,
                mean_rank_ic DOUBLE,
                fold_same_sign_rate DOUBLE,
                group_ablation_delta DOUBLE,
                max_corr_with_kept_feature DOUBLE,
                corr_peer_feature TEXT,
                drift_status TEXT,
                notes TEXT,
                built_at TEXT,
                PRIMARY KEY (decision_run_id, feature_set_id, feature_name)
            );

            CREATE TABLE IF NOT EXISTS mart_tdx_challenger_report (
                challenger_run_id TEXT PRIMARY KEY,
                feature_set_id TEXT NOT NULL,
                decision_run_id TEXT NOT NULL,
                model_type TEXT NOT NULL,
                selected_features_json TEXT,
                train_window_json TEXT,
                valid_window_json TEXT,
                holdout_window_json TEXT,
                rank_ic DOUBLE,
                long_short_return DOUBLE,
                turnover_adjusted_return DOUBLE,
                max_drawdown DOUBLE,
                baseline_rank_ic DOUBLE,
                baseline_long_short_return DOUBLE,
                promote_to_champion BOOLEAN DEFAULT FALSE,
                notes TEXT,
                built_at TEXT
            );

            CREATE TABLE IF NOT EXISTS mart_tdx_keep_promotion_gate (
                gate_run_id TEXT PRIMARY KEY,
                challenger_model_id TEXT NOT NULL,
                champion_model_id TEXT,
                promotion_status TEXT NOT NULL,
                decision TEXT NOT NULL,
                gate_results_json TEXT NOT NULL,
                blockers_json TEXT NOT NULL,
                rank_ic_challenger DOUBLE,
                rank_ic_champion DOUBLE,
                long_short_challenger DOUBLE,
                long_short_champion DOUBLE,
                max_drawdown_challenger DOUBLE,
                max_drawdown_champion DOUBLE,
                evaluated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mart_data_deprecation_record (
                record_id        TEXT PRIMARY KEY,
                table_name       TEXT NOT NULL,
                deprecation_status TEXT NOT NULL,
                replacement_table TEXT,
                reason           TEXT,
                recorded_at      TEXT NOT NULL,
                dry_run          BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS mart_data_deletion_record (
                record_id TEXT PRIMARY KEY,
                deletion_run_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                delete_scope TEXT NOT NULL,
                key_column TEXT,
                key_value TEXT,
                deleted_rows BIGINT DEFAULT 0,
                deleted_files BIGINT DEFAULT 0,
                deleted_bytes BIGINT DEFAULT 0,
                reason TEXT NOT NULL,
                verification_json TEXT,
                deleted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mart_institution_profile (
                institution_id          TEXT PRIMARY KEY,
                institution_name        TEXT,
                display_name            TEXT,
                inst_type               TEXT,
                total_events            INTEGER,
                total_stocks            INTEGER,
                total_periods           INTEGER,
                avg_gain_10d            REAL,
                avg_gain_30d            REAL,
                avg_gain_60d            REAL,
                avg_gain_120d           REAL,
                avg_excess_30d          REAL,
                avg_excess_60d          REAL,
                win_rate_30d            REAL,
                win_rate_60d            REAL,
                win_rate_90d            REAL,
                win_rate_120d           REAL,
                total_win_rate          REAL,
                median_gain_30d         REAL,
                median_gain_60d         REAL,
                median_max_drawdown_30d REAL,
                median_max_drawdown_60d REAL,
                top_industry_1          TEXT,
                top_industry_2          TEXT,
                top_industry_3          TEXT,
                main_industry_1         TEXT,
                main_industry_2         TEXT,
                main_industry_3         TEXT,
                best_industry_1         TEXT,
                best_industry_2         TEXT,
                best_industry_3         TEXT,
                concentration           REAL,
                current_stock_count     INTEGER,
                current_total_cap       REAL,
                latest_notice_date      TEXT,
                recent_new_entry_count  INTEGER,
                recent_increase_count   INTEGER,
                recent_exit_count       INTEGER DEFAULT 0,
                quality_score           REAL,
                score_basis             TEXT,
                score_confidence        TEXT,
                historical_median_holding_days INTEGER,
                current_avg_held_days   INTEGER,
                buy_event_count         INTEGER,
                buy_avg_gain_30d        REAL,
                buy_avg_gain_60d        REAL,
                buy_avg_gain_120d       REAL,
                buy_win_rate_30d        REAL,
                buy_win_rate_60d        REAL,
                buy_win_rate_120d       REAL,
                buy_median_max_drawdown_30d REAL,
                buy_median_max_drawdown_60d REAL,
                avg_premium_pct         REAL,
                safe_follow_event_count INTEGER,
                safe_follow_win_rate_30d REAL,
                safe_follow_avg_gain_30d REAL,
                safe_follow_avg_drawdown_30d REAL,
                premium_discount_event_count INTEGER,
                premium_discount_win_rate_30d REAL,
                premium_near_cost_event_count INTEGER,
                premium_near_cost_win_rate_30d REAL,
                premium_premium_event_count INTEGER,
                premium_premium_win_rate_30d REAL,
                premium_high_event_count INTEGER,
                premium_high_win_rate_30d REAL,
                signal_transfer_efficiency_30d REAL,
                followability_hint      TEXT,
                followability_score     REAL,
                followability_confidence TEXT,
                data_completeness       TEXT DEFAULT 'complete',
                pricing_policy_id       TEXT,
                pricing_policy_hash     TEXT,
                updated_at              TEXT
            );

            CREATE TABLE IF NOT EXISTS mart_institution_industry_stat (
                institution_id TEXT NOT NULL,
                industry_level TEXT NOT NULL,
                industry_name  TEXT NOT NULL,
                tdx_code       TEXT,
                sample_events  INTEGER DEFAULT 0,
                avg_gain_30d   REAL,
                avg_gain_60d   REAL,
                avg_gain_90d   REAL,
                avg_gain_120d  REAL,
                win_rate_30d   REAL,
                win_rate_60d   REAL,
                win_rate_90d   REAL,
                total_win_rate REAL,
                max_drawdown_30d REAL,
                max_drawdown_60d REAL,
                updated_at     TEXT,
                PRIMARY KEY (institution_id, industry_level, industry_name)
            );

            CREATE TABLE IF NOT EXISTS mart_stock_trend (
                stock_code         TEXT PRIMARY KEY,
                stock_name         TEXT,
                inst_count_t0      INTEGER,
                inst_count_t1      INTEGER,
                inst_count_t2      INTEGER,
                inst_cap_t0        REAL,
                inst_cap_t1        REAL,
                inst_cap_t2        REAL,
                inst_trend         TEXT,
                cap_trend          TEXT,
                latest_events      TEXT,
                latest_report_date TEXT,
                latest_notice_date TEXT,
                price_1m_pct       REAL,
                price_20d_pct      REAL,
                price_trend        TEXT,
                setup_tag          TEXT,
                setup_priority     INTEGER,
                setup_reason       TEXT,
                setup_confidence   TEXT,
                setup_level        TEXT,
                setup_inst_id      TEXT,
                setup_inst_name    TEXT,
                setup_event_type   TEXT,
                setup_industry_name TEXT,
                setup_score_raw    REAL,
                setup_execution_gate TEXT,
                setup_execution_reason TEXT,
                industry_skill_raw REAL,
                industry_skill_grade INTEGER,
                followability_grade INTEGER,
                premium_grade      INTEGER,
                report_recency_grade INTEGER,
                reliability_grade  INTEGER,
                crowding_bucket    TEXT,
                crowding_yield_raw REAL,
                crowding_yield_grade INTEGER,
                crowding_stability_raw REAL,
                crowding_stability_grade INTEGER,
                crowding_fit_raw   REAL,
                crowding_fit_grade INTEGER,
                crowding_fit_sample INTEGER,
                crowding_fit_source TEXT,
                report_age_days    INTEGER,
                discovery_score    REAL,
                company_quality_score REAL,
                company_quality_score_source TEXT,
                quality_feature_snapshot_date TEXT,
                stage_score        REAL,
                raw_composite_priority_score REAL,
                composite_priority_score REAL,
                composite_cap_score REAL,
                composite_cap_reason TEXT,
                stock_archetype    TEXT,
                priority_pool      TEXT,
                priority_pool_reason TEXT,
                stock_gate         TEXT,
                stock_gate_reason  TEXT,
                attention_comment_trade_date TEXT,
                attention_focus_index REAL,
                attention_composite_score REAL,
                attention_institution_participation REAL,
                attention_turnover_rate REAL,
                attention_rank_change REAL,
                attention_survey_count_30d INTEGER,
                attention_survey_count_90d INTEGER,
                attention_survey_org_total_30d INTEGER,
                attention_survey_org_total_90d INTEGER,
                external_attention_score REAL,
                external_crowding_penalty REAL,
                external_attention_signal TEXT,
                score_highlights   TEXT,
                score_risks        TEXT,
                updated_at         TEXT
            );

            CREATE TABLE IF NOT EXISTS mart_current_relationship (
                institution_id    TEXT NOT NULL,
                institution_name  TEXT,
                display_name      TEXT,
                inst_type         TEXT,
                stock_code        TEXT NOT NULL,
                stock_name        TEXT,
                report_date       TEXT NOT NULL,
                notice_date       TEXT,
                holder_rank       INTEGER,
                hold_amount       REAL,
                hold_market_cap   REAL,
                hold_ratio        REAL,
                hold_change       TEXT,
                event_type        TEXT,
                change_pct        REAL,
                gain_10d          REAL,
                gain_30d          REAL,
                gain_60d          REAL,
                gain_90d          REAL,
                gain_120d         REAL,
                max_drawdown_30d  REAL,
                max_drawdown_60d  REAL,
                report_season     TEXT,
                inst_ref_cost     REAL,
                inst_cost_method  TEXT,
                premium_pct       REAL,
                premium_bucket    TEXT,
                follow_gate       TEXT,
                follow_gate_reason TEXT,
                price_entry       REAL,
                return_to_now     REAL,
                path_state        TEXT,
                entry_report_date TEXT,
                entry_notice_date TEXT,
                notice_age_days   INTEGER,
                disclosure_lag_days INTEGER,
                current_held_days INTEGER,
                tdx_l1            TEXT,
                tdx_l2            TEXT,
                tdx_l3            TEXT,
                tdx_l1_name       TEXT,
                tdx_l2_name       TEXT,
                tdx_l3_name       TEXT,
                has_return_data   INTEGER DEFAULT 0,
                has_industry_data INTEGER DEFAULT 0,
                updated_at        TEXT,
                PRIMARY KEY (institution_id, stock_code)
            );

            CREATE TABLE IF NOT EXISTS mart_etf_snapshot_latest (
                code            TEXT PRIMARY KEY,
                snapshot_id     TEXT NOT NULL,
                category        TEXT,
                factor_rank     INTEGER,
                factor_score    REAL,
                rotation_score  REAL,
                strategy_type   TEXT,
                payload_json    TEXT NOT NULL,
                updated_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS mart_etf_snapshot_state (
                state_key               TEXT PRIMARY KEY,
                snapshot_id             TEXT,
                schema_version          INTEGER DEFAULT 1,
                computed_at             TEXT,
                etf_count               INTEGER DEFAULT 0,
                history_start           TEXT,
                history_end             TEXT,
                overview_json           TEXT,
                factor_snapshot_json    TEXT,
                mining_snapshot_json    TEXT,
                source_status_json      TEXT
            );

            CREATE TABLE IF NOT EXISTS mart_audit_snapshot_state (
                state_key       TEXT PRIMARY KEY,
                schema_version  INTEGER DEFAULT 1,
                computed_at     TEXT,
                source          TEXT,
                audit_json      TEXT
            );
"""

__all__ = ["ensure_mart_schema", "ensure_schema"]


def ensure_mart_schema(conn) -> None:
    conn.executescript(MART_SCHEMA_SQL)


def ensure_schema(conn) -> None:
    ensure_mart_schema(conn)
