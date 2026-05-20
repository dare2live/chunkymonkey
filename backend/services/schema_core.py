"""Core raw/dim/fact/system table creation DDL."""

from __future__ import annotations

CORE_SCHEMA_SQL = """
            CREATE TABLE IF NOT EXISTS raw_tdx_f10_holder_research (
                stock_code       TEXT NOT NULL,
                stock_name       TEXT,
                market           TEXT,
                fetched_at       TIMESTAMP NOT NULL,
                page_update_date DATE,
                raw_text         TEXT NOT NULL,
                raw_hash         VARCHAR(64) NOT NULL,
                bytes_len        INTEGER,
                server           TEXT,
                f10_format       TEXT,
                parser_version   TEXT DEFAULT 'v1',
                PRIMARY KEY (stock_code, raw_hash)
            );

            CREATE TABLE IF NOT EXISTS fact_top10_holder_period (
                stock_code        TEXT NOT NULL,
                stock_name        TEXT,
                market            TEXT,
                report_date       TEXT NOT NULL,
                holder_set        TEXT NOT NULL,            -- 'free' | 'all'
                holder_rank       INTEGER NOT NULL,
                row_seq           INTEGER NOT NULL DEFAULT 1,
                holder_name       TEXT NOT NULL,
                holder_name_norm  TEXT,                      -- alias-resolved (汇金公司→中央汇金投资有限责任公司)
                share_class       TEXT,                      -- 'A' | 'H' | 'B' | NULL
                is_secondary_class BOOLEAN DEFAULT FALSE,    -- TRUE = A/H 双重上市的副 leg
                is_exit_row       BOOLEAN DEFAULT FALSE,     -- TRUE = 来自 "退出前十大" 表
                shares_text       TEXT,                       -- raw "6.8128亿"
                shares_approx     BIGINT,                     -- 681282900
                shares_precision  TEXT,                       -- '亿' | '万' | '股'
                hold_amount       REAL,                       -- back-compat: == shares_approx (REAL)
                hold_ratio_float  DOUBLE,                     -- 占流通股比 % (free 表)
                hold_ratio_total  DOUBLE,                     -- 占总股本比 % (all 表)
                hold_ratio        REAL,                       -- back-compat: holder_set='free'→float, 'all'→total
                hold_market_cap   REAL,                       -- shares × period-end close
                holder_type       TEXT,                       -- raw display
                share_nature      TEXT,                       -- '无限售A股/...'
                change_status     TEXT,                       -- 新进/增持/减持/不变/退出
                change_shares_text TEXT,
                change_shares_approx BIGINT,
                hold_change       TEXT,                       -- back-compat: ''/新进/加仓/减仓
                hold_change_num   REAL,                       -- back-compat: signed shares delta
                notice_date       TEXT,
                effective_date    TEXT,                       -- 公告日 + 1 交易日 (回测 PIT)
                availability_source TEXT,                      -- source_notice | page_update_date | regulatory_deadline
                page_update_date  TEXT,                       -- F10 页头 "更新日期"
                source            TEXT NOT NULL,              -- 'tdx_f10' | 'miaoxiang' | 'akshare'
                source_tier       SMALLINT NOT NULL,          -- 1 / 2 / 3
                raw_hash          TEXT,                       -- → raw_tdx_f10_holder_research.raw_hash
                fetched_at        TEXT,
                created_at        TEXT,                       -- back-compat
                UNIQUE(stock_code, report_date, holder_set, source, is_exit_row,
                       holder_rank, row_seq, share_class)
            );

            CREATE TABLE IF NOT EXISTS fact_controlling_shareholder (
                stock_code       TEXT NOT NULL,
                stock_name       TEXT,
                market           TEXT,
                primary_label    TEXT,                       -- '控股股东' | '第一大股东'
                primary_name     TEXT,
                primary_ratio    DOUBLE,
                primary_raw      TEXT,
                actual_name      TEXT,
                actual_ratio     DOUBLE,
                actual_raw       TEXT,
                page_update_date TEXT,
                source           TEXT NOT NULL,
                source_tier      SMALLINT NOT NULL,
                raw_hash         TEXT,
                fetched_at       TEXT,
                PRIMARY KEY (stock_code, source)
            );

            CREATE TABLE IF NOT EXISTS fact_shareholder_plan (
                stock_code       TEXT NOT NULL,
                stock_name       TEXT,
                market           TEXT,
                announce_date    TEXT,
                subject          TEXT,
                direction        TEXT,                       -- 增持计划 / 减持计划
                progress         TEXT,                       -- 预案 / 实施 / 完成
                start_date       TEXT,
                end_date         TEXT,
                target_shares_text TEXT,
                target_shares    BIGINT,
                target_ratio_text TEXT,
                target_ratio     DOUBLE,
                reason           TEXT,
                narrative        TEXT,
                page_update_date TEXT,
                source           TEXT NOT NULL,
                source_tier      SMALLINT NOT NULL,
                raw_hash         TEXT,
                fetched_at       TEXT,
                plan_seq         INTEGER                     -- F10 内出现序号
            );

            CREATE TABLE IF NOT EXISTS fact_shareholder_trade (
                stock_code       TEXT NOT NULL,
                stock_name       TEXT,
                market           TEXT,
                change_date      TEXT,
                holder_name      TEXT,
                holder_name_norm TEXT,
                shares_before_text TEXT,
                shares_before    BIGINT,
                shares_change_text TEXT,
                shares_change    BIGINT,                     -- 带符号
                shares_after_text TEXT,
                shares_after     BIGINT,
                ratio_after      DOUBLE,
                change_type      TEXT,                       -- 二级市场买入/二级市场卖出/员工持股计划/...
                page_update_date TEXT,
                source           TEXT NOT NULL,
                source_tier      SMALLINT NOT NULL,
                raw_hash         TEXT,
                fetched_at       TEXT,
                trade_seq        INTEGER
            );

            CREATE TABLE IF NOT EXISTS fact_shareholder_trade_tdx_b (
                stock_code       TEXT NOT NULL,
                stock_name       TEXT,
                market           TEXT,
                change_period_text TEXT,
                change_start_date TEXT,
                change_end_date   TEXT,
                change_date      TEXT,
                holder_name      TEXT,
                holder_name_norm TEXT,
                shares_change_text TEXT,
                shares_change    BIGINT,
                average_price_text TEXT,
                average_price    DOUBLE,
                shares_after_text TEXT,
                shares_after     BIGINT,
                change_method    TEXT,
                page_update_date TEXT,
                source           TEXT NOT NULL,
                source_tier      SMALLINT NOT NULL DEFAULT 1,
                raw_hash         TEXT,
                fetched_at       TEXT,
                trade_seq        INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (stock_code, raw_hash, trade_seq)
            );

            CREATE TABLE IF NOT EXISTS fact_shareholder_plan_tdx_f10 (
                stock_code       TEXT NOT NULL,
                stock_name       TEXT,
                market           TEXT,
                announce_date    TEXT,
                latest_announce_date TEXT,
                first_announce_date TEXT,
                source_notice_date TEXT,
                source_available_date TEXT,
                source_date_quality TEXT,
                subject          TEXT,
                direction        TEXT,
                progress         TEXT,
                start_date       TEXT,
                end_date         TEXT,
                target_shares_min_text TEXT,
                target_shares_min BIGINT,
                target_shares_text TEXT,
                target_shares    BIGINT,
                target_ratio_text TEXT,
                target_ratio     DOUBLE,
                target_amount_min_text TEXT,
                target_amount_min BIGINT,
                target_amount_max_text TEXT,
                target_amount_max BIGINT,
                trade_method     TEXT,
                reason           TEXT,
                narrative        TEXT,
                page_update_date TEXT,
                source           TEXT NOT NULL,
                source_tier      SMALLINT NOT NULL DEFAULT 1,
                raw_hash         TEXT,
                fetched_at       TEXT,
                row_seq          INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (stock_code, raw_hash, row_seq)
            );

            CREATE TABLE IF NOT EXISTS raw_tdx_f10_holder_count_history (
                stock_code       TEXT NOT NULL,
                stock_name       TEXT,
                market           TEXT,
                report_date      TEXT,
                report_date_text TEXT,
                holder_count_text TEXT,
                holder_count     BIGINT,
                holder_count_change_text TEXT,
                holder_count_change BIGINT,
                holder_count_change_pct_text TEXT,
                holder_count_change_pct DOUBLE,
                avg_float_shares_text TEXT,
                avg_float_shares BIGINT,
                avg_float_shares_change_pct_text TEXT,
                avg_float_shares_change_pct DOUBLE,
                close_price_text TEXT,
                close_price      DOUBLE,
                page_update_date TEXT,
                source           TEXT NOT NULL,
                raw_hash         TEXT NOT NULL,
                fetched_at       TEXT,
                row_seq          INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (stock_code, raw_hash, row_seq)
            );

            CREATE TABLE IF NOT EXISTS fact_holder_count_period (
                stock_code       TEXT NOT NULL,
                stock_name       TEXT,
                market           TEXT,
                report_date      TEXT NOT NULL,
                holder_count     BIGINT,
                holder_count_change BIGINT,
                holder_count_change_pct DOUBLE,
                avg_float_shares BIGINT,
                avg_float_shares_change_pct DOUBLE,
                close_price      DOUBLE,
                page_update_date TEXT,
                source           TEXT NOT NULL,
                source_tier      SMALLINT NOT NULL DEFAULT 1,
                raw_hash         TEXT,
                fetched_at       TEXT,
                updated_at       TEXT,
                PRIMARY KEY (stock_code, report_date, source)
            );

            CREATE TABLE IF NOT EXISTS fact_common_major_holder_stock (
                stock_code       TEXT NOT NULL,
                stock_name       TEXT,
                market           TEXT,
                report_date      TEXT,
                report_date_text TEXT,
                major_holder_name TEXT NOT NULL,
                peer_stock_code  TEXT NOT NULL,
                peer_stock_name  TEXT,
                shares_text      TEXT,
                shares           BIGINT,
                hold_ratio_text  TEXT,
                hold_ratio       DOUBLE,
                change_text      TEXT,
                change_shares    BIGINT,
                net_profit_parent_text TEXT,
                net_profit_parent DOUBLE,
                net_profit_deducted_text TEXT,
                net_profit_deducted DOUBLE,
                page_update_date TEXT,
                source           TEXT NOT NULL,
                source_tier      SMALLINT NOT NULL DEFAULT 1,
                raw_hash         TEXT,
                fetched_at       TEXT,
                row_seq          INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (stock_code, major_holder_name, peer_stock_code, row_seq)
            );

            CREATE TABLE IF NOT EXISTS fact_fund_holding_tdx_f10 (
                stock_code       TEXT NOT NULL,
                stock_name       TEXT,
                market           TEXT,
                report_date      TEXT,
                report_date_text TEXT,
                fund_name        TEXT NOT NULL,
                shares_text      TEXT,
                shares           BIGINT,
                float_a_ratio_text TEXT,
                float_a_ratio    DOUBLE,
                market_value_text TEXT,
                market_value     DOUBLE,
                page_update_date TEXT,
                source           TEXT NOT NULL,
                source_tier      SMALLINT NOT NULL DEFAULT 1,
                raw_hash         TEXT,
                fetched_at       TEXT,
                row_seq          INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (stock_code, fund_name, report_date, row_seq)
            );

            CREATE TABLE IF NOT EXISTS raw_tdx_f10_extra_parse_status (
                stock_code       TEXT NOT NULL,
                raw_hash         TEXT NOT NULL,
                parsed_at        TEXT,
                holder_count_rows INTEGER DEFAULT 0,
                trade_b_rows     INTEGER DEFAULT 0,
                control_rows     INTEGER DEFAULT 0,
                common_major_holder_rows INTEGER DEFAULT 0,
                fund_holding_rows INTEGER DEFAULT 0,
                fund_holding_rejected_rows INTEGER DEFAULT 0,
                shareholder_plan_rows INTEGER DEFAULT 0,
                status           TEXT NOT NULL,
                status_reason    TEXT,
                parser_version   TEXT,
                error            TEXT,
                PRIMARY KEY (stock_code, raw_hash)
            );

            CREATE TABLE IF NOT EXISTS fact_holder_event (
                stock_code        TEXT NOT NULL,
                stock_name        TEXT,
                holder_name       TEXT NOT NULL,
                holder_name_norm  TEXT NOT NULL,
                share_class       TEXT,                       -- 'A' | 'H' | 'B' | NULL
                report_date       TEXT NOT NULL,              -- 本期报告期
                prev_report_date  TEXT,                       -- 上一期 (new_entry/exit 可空)
                event_type        TEXT NOT NULL,              -- new_entry / increase / decrease / unchanged / exit
                shares_before     BIGINT,
                shares_after      BIGINT,
                shares_delta      BIGINT,                     -- after - before (带符号)
                ratio_float_before DOUBLE,
                ratio_float_after  DOUBLE,
                ratio_total_before DOUBLE,
                ratio_total_after  DOUBLE,
                holder_type       TEXT,
                holder_set        TEXT NOT NULL,              -- 'free' | 'all'
                source            TEXT NOT NULL,
                source_tier       SMALLINT NOT NULL,
                raw_hash          TEXT,
                created_at        TEXT,
                PRIMARY KEY (stock_code, holder_name_norm, share_class, report_date, event_type, holder_set)
            );

            CREATE TABLE IF NOT EXISTS dim_holder_alias (
                alias            TEXT NOT NULL,              -- TDX 简称 / 别名
                canonical_name   TEXT NOT NULL,              -- 工商全称
                category         TEXT,                       -- '国家队/央企/外资/...' (可选)
                note             TEXT,
                created_at       TEXT,
                PRIMARY KEY (alias)
            );

            CREATE TABLE IF NOT EXISTS dim_data_asset (
                table_name        TEXT PRIMARY KEY,
                layer             TEXT NOT NULL,              -- raw / dim / fact / mart / sys / cache / other
                purpose           TEXT,                       -- 一句话用途 (manual fill 推荐)
                writer_module     TEXT,                       -- 写者文件路径, e.g. backend/scripts/ingest_holders_tdxhub.py
                reader_modules    TEXT,                       -- JSON 数组: 读者文件列表
                upstream_source   TEXT,                       -- 'tdxhub.holders' / 'akshare.X' / 'derived'
                source_tier       SMALLINT,                   -- 1=主, 2=备, 3=兜底, NULL=派生
                fallback_chain    TEXT,                       -- JSON 数组: [{tier, source, trigger}]
                expected_freshness TEXT,                      -- 't+0' / 't+1' / 'quarterly' / 'event' / 'static'
                sla_hours         INTEGER,                    -- 数据延迟超过 N 小时算 yellow
                consumed_by_views TEXT,                       -- JSON 数组: ['view-stocks', 'view-research']
                asset_grain       TEXT,                       -- stock_code+date / event / report_period / run_id
                asset_cadence     TEXT,                       -- trading_day_daily / event_driven / quarterly / on_demand
                coverage_policy   TEXT,                       -- dense_active_a_stock / sparse_event / periodic_report
                null_policy       TEXT,                       -- no_null / no_event_is_absence / classified_required
                pit_policy        TEXT,                       -- same_day_market / source_notice_date / registry_required
                intended_use      TEXT,                       -- model_training / context / attention / monitoring
                model_eligibility TEXT,                       -- registered_features_only / encoded_auxiliary_only / blocked
                strategy_eligibility TEXT,                    -- entry_exit_pricing / filter_context / diagnostics
                frontend_visibility TEXT,                     -- governance_visible / hidden_internal
                quality_gate_level TEXT,                      -- blocking / warning / monitor_only
                is_append_only    BOOLEAN DEFAULT FALSE,
                deprecation_status TEXT DEFAULT 'active',
                deprecated_at     TEXT,
                deprecated_reason TEXT,
                replacement_table TEXT,
                schema_version    TEXT DEFAULT 'v1',
                notes             TEXT,
                auto_discovered   BOOLEAN DEFAULT TRUE,       -- TRUE=auto-seed, FALSE=人工补
                last_updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS raw_tdx_gpcw_wide (
                stock_code       TEXT NOT NULL,
                report_date      TEXT NOT NULL,
                source_file      TEXT,
                field_values_json TEXT NOT NULL,
                parser_version   TEXT DEFAULT 'tdxhub_gpcw_v1',
                ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (stock_code, report_date)
            );

            CREATE TABLE IF NOT EXISTS dim_tdx_gpcw_field (
                field_key        TEXT PRIMARY KEY,
                field_index      INTEGER,
                zh_name          TEXT NOT NULL,
                db_column        TEXT,
                unit             TEXT,
                field_family     TEXT,
                model_candidate  BOOLEAN DEFAULT FALSE,
                verified         BOOLEAN DEFAULT FALSE,
                notes            TEXT,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dim_data_source_priority (
                data_domain TEXT PRIMARY KEY,
                preferred_source TEXT NOT NULL,
                fallback_1 TEXT,
                fallback_2 TEXT,
                reason TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS dim_tdx_gpcw_field_semantic (
                field_key TEXT PRIMARY KEY,
                zh_name TEXT,
                db_column TEXT,
                field_index INTEGER,
                unit TEXT,
                field_family TEXT,
                semantic_role TEXT,
                value_type TEXT,
                scale_rule TEXT,
                pit_date_field TEXT,
                candidate_priority TEXT,
                exclude_reason TEXT,
                source_profile_run_id TEXT,
                mapped_status TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS fact_tdx_gpcw_auto_feature_quarterly (
                feature_set_id TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                report_date TEXT NOT NULL,
                available_date TEXT NOT NULL,
                field_key TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                feature_family TEXT,
                transform TEXT NOT NULL,
                feature_value DOUBLE,
                source_value DOUBLE,
                coverage_group TEXT,
                built_at TEXT,
                PRIMARY KEY (feature_set_id, stock_code, report_date, feature_name)
            );

            CREATE TABLE IF NOT EXISTS fact_feature_panel_candidate (
                feature_set_id   TEXT NOT NULL,
                stock_code       TEXT NOT NULL,
                date             TEXT NOT NULL,
                close            REAL,
                forward_ret_5d   REAL,
                forward_ret_10d  REAL,
                forward_ret_20d  REAL,
                forward_ret_60d  REAL,
                forward_ret_90d  REAL,
                follow_net_return_5d REAL,
                follow_net_return_10d REAL,
                follow_net_return_20d REAL,
                follow_net_return_60d REAL,
                follow_net_return_90d REAL,
                common_holder_network_count REAL,
                fund_holding_shares_tdx_f10 REAL,
                fund_holding_float_a_ratio_tdx_f10 REAL,
                fund_holding_market_value_tdx_f10 REAL,
                holder_count_change_pct_tdx REAL,
                avg_float_shares_change_pct_tdx REAL,
                holder_count_acceleration_tdx REAL,
                top10_concentration_change REAL,
                tdx_inst_total_shares_qoq REAL,
                national_team_shares_qoq REAL,
                qfii_shares_qoq REAL,
                fund_shares_qoq REAL,
                social_security_shares_qoq REAL,
                contract_liabilities_to_revenue REAL,
                ocf_to_profit_tdx REAL,
                receivables_to_revenue REAL,
                inventory_to_revenue REAL,
                forecast_profit_yoy_mid REAL,
                forecast_range_width REAL,
                express_net_profit_yoy REAL,
                built_at         TEXT,
                PRIMARY KEY (feature_set_id, stock_code, date)
            );

CREATE TABLE IF NOT EXISTS fact_feature_panel_tdx_keep_challenger ( feature_set_id TEXT NOT NULL, stock_code TEXT NOT NULL, date TEXT NOT NULL, regime_flag TEXT, forward_ret_5d REAL, forward_ret_10d REAL, forward_ret_20d REAL, forward_ret_60d REAL, forward_ret_90d REAL, ret_1d REAL, ret_5d REAL, ret_20d REAL, ret_60d REAL, vol_z20d REAL, ma_ratio_5 REAL, ma_ratio_20 REAL, ma_ratio_60 REAL, ma_ratio_250 REAL, rz_balance REAL, rz_chg_5d_pct REAL, kmid REAL, klen REAL, kup REAL, klow REAL, ksft REAL, vol_ratio_5_20 REAL, vol_std_5d REAL, vol_std_20d REAL, range_pos_20 REAL, range_pos_60 REAL, momentum_diff REAL, amount_chg_5d REAL, inst_event_count_30d REAL, inst_event_count_60d REAL, exec_buy_count_90d REAL, exec_buy_ge1_count_90d REAL, lhb_inst_buy_count_30d REAL, lhb_inst_buy_count_60d REAL, jgdy_count_60d REAL, dzjy_count_60d REAL, days_since_exec_buy REAL, days_since_lhb REAL, shareholder_count_qoq REAL, inst_count_qoq REAL, fund_count_qoq REAL, qfii_count_qoq REAL, yjyg_lower_pct REAL, yjyg_upper_pct REAL, roe REAL, eps_basic REAL, hs300_ret_20d REAL, hs300_ret_60d REAL, ret_20d_rank REAL, ret_60d_rank REAL, vol_z20d_rank REAL, amount_chg_5d_rank REAL, rz_balance_rank REAL, rz_chg_5d_pct_rank REAL, ret_20d_tdx_l1_rel REAL, ret_60d_tdx_l1_rel REAL, vol_z20d_tdx_l1_rel REAL, amount_chg_5d_tdx_l1_rel REAL, rz_balance_to_amount20 REAL, forecast_profit_yoy_mid REAL, avg_float_shares_change_pct_tdx REAL, ocf_to_profit_tdx REAL, fund_shares_qoq REAL, forecast_range_width REAL, built_at TEXT, PRIMARY KEY (feature_set_id, stock_code, date) );

            CREATE TABLE IF NOT EXISTS dim_active_a_stock (
                stock_code       TEXT PRIMARY KEY,
                stock_name       TEXT,
                market           TEXT,
                source           TEXT,
                updated_at       TEXT
            );

            CREATE TABLE IF NOT EXISTS dim_stock_tdx_industry (
                stock_code    TEXT PRIMARY KEY,
                tdx_l1        TEXT,
                tdx_l2        TEXT,
                tdx_l3        TEXT,
                tdx_l1_name   TEXT,
                tdx_l2_name   TEXT,
                tdx_l3_name   TEXT,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dim_tdx_block_catalog (
                block_category TEXT NOT NULL,
                block_name     TEXT NOT NULL,
                block_file     TEXT NOT NULL,
                block_type     INTEGER,
                member_count   INTEGER DEFAULT 0,
                source         TEXT,
                updated_at     TEXT,
                PRIMARY KEY (block_category, block_name)
            );

            CREATE TABLE IF NOT EXISTS dim_stock_tdx_block (
                stock_code      TEXT NOT NULL,
                block_category  TEXT NOT NULL,
                block_name      TEXT NOT NULL,
                block_file      TEXT NOT NULL,
                block_type      INTEGER,
                code_index      INTEGER,
                source          TEXT,
                updated_at      TEXT,
                PRIMARY KEY (stock_code, block_category, block_name)
            );

            CREATE TABLE IF NOT EXISTS dim_trading_calendar (
                trade_date  TEXT PRIMARY KEY,
                is_trading  INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS inst_institutions (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                display_name TEXT,
                type         TEXT DEFAULT 'other',
                enabled      INTEGER DEFAULT 1,
                blacklisted  INTEGER DEFAULT 0,
                aliases      TEXT DEFAULT '[]',
                manual_type  TEXT,
                merged_into  TEXT,
                created_at   TEXT,
                updated_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS inst_holdings (
                institution_id  TEXT,
                holder_name     TEXT,
                holder_type     TEXT,
                stock_code      TEXT NOT NULL,
                stock_name      TEXT,
                report_date     TEXT NOT NULL,
                notice_date     TEXT,
                notice_date_source TEXT,                      -- source_notice | page_update_date | regulatory_deadline | unknown
                source_notice_date TEXT,                      -- true source disclosure date, NULL when unavailable
                availability_deadline TEXT,                   -- statutory/plannable fallback date, NULL when not used
                holder_rank     INTEGER,
                hold_amount     REAL,
                hold_market_cap REAL,
                hold_ratio      REAL,
                hold_change     TEXT,
                hold_change_num REAL,
                created_at      TEXT,
                UNIQUE(holder_name, stock_code, report_date)
            );

            CREATE TABLE IF NOT EXISTS fact_institution_event (
                institution_id    TEXT NOT NULL,
                holder_name       TEXT,
                stock_code        TEXT NOT NULL,
                stock_name        TEXT,
                report_date       TEXT NOT NULL,
                notice_date       TEXT,
                notice_date_source TEXT,                      -- source_notice | page_update_date | regulatory_deadline | unknown
                source_notice_date TEXT,                      -- true source disclosure date, NULL when unavailable
                availability_deadline TEXT,                   -- statutory/plannable fallback date, NULL when not used
                event_type        TEXT NOT NULL,
                hold_amount       REAL,
                prev_hold_amount  REAL,
                change_amount     REAL,
                change_pct        REAL,
                report_season     TEXT,
                cost_window_start TEXT,
                cost_window_end   TEXT,
                inst_ref_cost     REAL,
                inst_cost_method  TEXT,
                premium_pct       REAL,
                premium_bucket    TEXT,
                follow_gate       TEXT,
                follow_gate_reason TEXT,
                created_at        TEXT,
                PRIMARY KEY (institution_id, stock_code, report_date)
            );

            CREATE TABLE IF NOT EXISTS stock_watchlist (
                stock_code          TEXT NOT NULL,
                stock_name          TEXT,
                added_date          TEXT NOT NULL,
                added_price         REAL,
                added_reason        TEXT,
                source_institution  TEXT,
                source_event_type   TEXT,
                gain_since_added    REAL,
                max_gain            REAL,
                max_drawdown        REAL,
                current_price       REAL,
                status              TEXT DEFAULT 'active',
                closed_date         TEXT,
                closed_price        REAL,
                closed_reason       TEXT,
                updated_at          TEXT,
                PRIMARY KEY (stock_code, added_date)
            );

            CREATE TABLE IF NOT EXISTS fact_setup_snapshot (
                snapshot_date         TEXT NOT NULL,
                stock_code            TEXT NOT NULL,
                stock_name            TEXT,
                setup_tag             TEXT NOT NULL,
                setup_priority        INTEGER,
                setup_reason          TEXT,
                setup_confidence      TEXT,
                setup_level           TEXT,
                setup_inst_id         TEXT,
                setup_inst_name       TEXT,
                setup_event_type      TEXT,
                setup_industry_name   TEXT,
                snapshot_tdx_l1       TEXT,
                snapshot_tdx_l2       TEXT,
                snapshot_tdx_l3       TEXT,
                snapshot_tdx_l1_name  TEXT,
                snapshot_tdx_l2_name  TEXT,
                snapshot_tdx_l3_name  TEXT,
                action_score          REAL,
                discovery_score       REAL,
                company_quality_score REAL,
                company_quality_score_source TEXT,
                quality_feature_snapshot_date TEXT,
                stage_score           REAL,
                raw_composite_priority_score REAL,
                composite_priority_score REAL,
                composite_cap_score   REAL,
                composite_cap_reason  TEXT,
                stock_archetype       TEXT,
                priority_pool         TEXT,
                priority_pool_reason  TEXT,
                score_highlights      TEXT,
                score_risks           TEXT,
                latest_report_date    TEXT,
                latest_notice_date    TEXT,
                report_age_days       INTEGER,
                setup_score_raw       REAL,
                setup_execution_gate  TEXT,
                setup_execution_reason TEXT,
                industry_skill_raw    REAL,
                industry_skill_grade  INTEGER,
                followability_grade   INTEGER,
                premium_grade         INTEGER,
                report_recency_grade  INTEGER,
                reliability_grade     INTEGER,
                crowding_bucket       TEXT,
                crowding_yield_raw    REAL,
                crowding_yield_grade  INTEGER,
                crowding_stability_raw REAL,
                crowding_stability_grade INTEGER,
                crowding_fit_raw      REAL,
                crowding_fit_grade    INTEGER,
                crowding_fit_sample   INTEGER,
                crowding_fit_source   TEXT,
                entry_trade_date      TEXT,
                entry_price           REAL,
                current_trade_date    TEXT,
                current_price         REAL,
                gain_to_now           REAL,
                gain_10d              REAL,
                gain_30d              REAL,
                gain_60d              REAL,
                max_drawdown_10d      REAL,
                max_drawdown_30d      REAL,
                max_drawdown_60d      REAL,
                matured_10d           INTEGER DEFAULT 0,
                matured_30d           INTEGER DEFAULT 0,
                matured_60d           INTEGER DEFAULT 0,
                updated_at            TEXT,
                PRIMARY KEY (snapshot_date, stock_code, setup_tag, setup_inst_id)
            );

            CREATE TABLE IF NOT EXISTS sys_schema_version (
                layer       TEXT PRIMARY KEY,
                version     TEXT,
                updated_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS excluded_stocks (
                stock_code TEXT NOT NULL,
                category   TEXT NOT NULL,
                stock_name TEXT,
                reason     TEXT,
                created_at TEXT,
                PRIMARY KEY (stock_code, category)
            );

            CREATE TABLE IF NOT EXISTS exclusion_categories (
                category    TEXT PRIMARY KEY,
                label       TEXT NOT NULL,
                enabled     INTEGER DEFAULT 1,
                updated_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS step_status (
                step_id       TEXT PRIMARY KEY,
                group_name    TEXT,
                step_name     TEXT,
                step_order    INTEGER,
                status        TEXT DEFAULT 'idle',
                started_at    TEXT,
                finished_at   TEXT,
                error         TEXT,
                records       INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS scan_log (
                id               INTEGER PRIMARY KEY,
                scan_type        TEXT,
                update_date_from TEXT,
                update_date_to   TEXT,
                rows_fetched     INTEGER DEFAULT 0,
                rows_inserted    INTEGER DEFAULT 0,
                rows_updated     INTEGER DEFAULT 0,
                duration_sec     REAL,
                status           TEXT DEFAULT 'running',
                error            TEXT,
                created_at       TEXT
            );

            CREATE TABLE IF NOT EXISTS market_gap_queue (
                dataset         TEXT NOT NULL,
                stock_code      TEXT NOT NULL,
                stock_name      TEXT,
                status          TEXT DEFAULT 'pending',
                reason          TEXT,
                last_error      TEXT,
                source_attempts INTEGER DEFAULT 0,
                first_seen_at   TEXT,
                last_attempt_at TEXT,
                resolved_at     TEXT,
                updated_at      TEXT,
                PRIMARY KEY (dataset, stock_code)
            );
"""

__all__ = ["ensure_core_schema"]


def ensure_core_schema(conn) -> None:
    conn.executescript(CORE_SCHEMA_SQL)
