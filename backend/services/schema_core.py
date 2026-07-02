"""Core raw/dim/fact/system table creation DDL."""

from __future__ import annotations

CORE_SCHEMA_SQL = """
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
                source            TEXT NOT NULL,              -- 现役唯一 'miaoxiang' (tdx_f10/akshare 源已退役)
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


            -- fact_common_major_holder_stock DDL 已删 (2026-06-29 批3c: tdx F10 机构持仓明细旧表退役物删
            --   76300行 source=tdx_f10, 已被 aif10 raw_org_holding_aif10(336077行 更全)取代, 0 SERVE/读消费方
            --   用户 2026-06-29 拍板一并退役, archive parquet 留底)

            -- fact_holder_event DDL 已删 2026-06-28 (Phase 0 机构+事件 serving 退役: 表已物删,
            --   rebuild_holder_events 派生脚本不存在/run_portfolio_mvp simulator 消费者已退役,
            --   Phase 3 机构档案以 as-of 跟随口径 披露日 T+1 进出 重建进出事件。注: SQL 注释禁用分号)
            -- dim_holder_alias DDL 已删 2026-06-29 (批3e: TDX holder 别名孤儿维表 30行 0消费方物删, TDX 退役)

            -- dim_data_asset DDL 已删 2026-06-28 (F4 退役: 烂登记表[67stale/68漏/0强制]职责归并 —
            --   layer/asset_class→data_layers.yaml, freshness→layer_health_defaults+sync_registry+
            --   table_health_overrides, producer/consumer→lineage, 退役态→mart_data_deprecation_record)
            -- raw_tdx_gpcw_wide / dim_tdx_gpcw_field DDL 已删 (2026-06-27 通达信全删 gpcw物删)

            -- dim_data_source_priority DDL 已删 2026-06-29 (批3e: 多源优先级孤儿表, §4.3 单源体制概念已废, 10行 0消费方物删)

            -- dim_tdx_gpcw_field_semantic / fact_tdx_gpcw_auto_feature_quarterly DDL 已删 (2026-06-27 通达信全删 gpcw物删, auto_features 流水线 dead)

            -- fact_feature_panel_candidate DDL 已删 (2026-06-28 加工层清空: 派生 L2 候选/挑战表退役)

            -- fact_feature_panel_tdx_keep_challenger DDL 已删 (2026-06-28 加工层清空: 派生 L2 候选/挑战表退役)

            -- §9 reference 拆库 Stage E (2026-06-27): active 主数据 与 trading_calendar 的 DDL 已移除  -- rule-compliance: ok evidence=stage-e-ddl-removed (注释非真用, 表迁 reference)
            --   (迁 reference 库后 smartmoney 副本物删, 删 DDL 防 init_db 重建循环 undo 物删)
            --   reference 侧 schema owner = migrate_reference_db 加 security_master._DIM_ACTIVE_DDL 加 build_dim_listing_status
            -- 通达信(tdx)行业 dim_stock_tdx_industry / 板块 dim_tdx_block_catalog / dim_stock_tdx_block DDL
            -- 已删 2026-06-23 东财全套迁移 Stage四 行业概念切东财 dim_stock_dc_industry/dc_concept

            -- inst_institutions / inst_holdings DDL 已删 (2026-06-29 批3c 机构旧表退役):
            --   inst_institutions(240 机构 registry) 加 inst_holdings(34994 机构持仓明细) = 孤儿表
            --   writer institution_write 模块已 Phase0 物删, 0 真实读消费方 (security_master 仅注释提及)
            --   机构持仓明细主源切 aif10 raw_org_holding_aif10 (org_holding_aif10 模块), 两表 archive 物删留底

            -- fact_institution_event DDL 已删 (2026-06-28 重建: 策略层退役)

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

            -- market_gap_queue DDL 已删 2026-06-28 (残留清理批1: gap_queue.py 退役, 表两库全物删, 0 消费方)
"""

__all__ = ["ensure_core_schema"]


def ensure_core_schema(conn) -> None:
    from .schema_layer_filter import filter_schema_sql
    conn.executescript(filter_schema_sql(CORE_SCHEMA_SQL))
