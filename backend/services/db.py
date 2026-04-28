"""
数据库服务 — Chunky Monkey v2

数据分层：
  原始层（只追加）: raw_tdx_f10_holder_research, raw_fetch_batch
  维度层: dim_active_a_stock, dim_stock_tdx_industry, dim_trading_calendar,
          inst_institutions, inst_name_aliases, dim_holder_alias
  事实层: fact_top10_holder_period (替代 market_raw_holdings, A/H 强制拆分),
          fact_shareholder_plan, fact_shareholder_trade,
          fact_controlling_shareholder,
          inst_holdings, fact_institution_event, stock_watchlist
  集市层（可重算）: mart_institution_profile, mart_institution_industry_stat, mart_stock_trend
  系统层: sys_schema_version, excluded_stocks, exclusion_categories, app_settings

退役路径（2026-04-28 起）：
  market_raw_holdings → fact_top10_holder_period
  - market_raw_holdings 由 miaoxiang RPT_F10_EH_FREEHOLDERS 写入 (源 P6.1)；
    A+H 持仓被合并、缺 share_class、缺 source_tier。
  - fact_top10_holder_period 由 tdxhub.holders 写入 (源 P7+)，A/H 严格拆分，
    带 source_tier (1=tdxhub primary / 2=miaoxiang fallback) + raw_hash 链回。
  - 老表保留为只读兼容层 (P8 之后删除)。
"""

import logging
from datetime import datetime
from pathlib import Path

from services.duck_adapter import connect as _duck_connect, DuckConn

logger = logging.getLogger("cm-api")

DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
# Phase 7 切换: DuckDB 为唯一主库
DB_PATH = DB_DIR / "smartmoney.duckdb"


def get_conn(timeout: int = 30) -> DuckConn:
    """返回 DuckDB 连接。"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    return _duck_connect(str(DB_PATH), timeout=timeout)


def init_db():
    conn = get_conn()
    try:
        conn.executescript("""
            -- ============================================================
            -- 原始层（只追加不改）
            -- ============================================================

            -- P7/P8 (2026-04-28): market_raw_holdings 整表退役.
            -- 数据已迁移至 fact_top10_holder_period (A/H 拆分 + source_tier).
            -- 老表 + 5 个旧索引一并删除.
            DROP TABLE IF EXISTS market_raw_holdings;

            -- ============================================================
            -- 原始层 (新): tdxhub F10 「股东研究」 原文
            -- ============================================================
            -- raw_tdx_f10_holder_research: 每只股票每次抓取的 F10 GBK 文本.
            -- raw_hash 唯一约束保证同一文本只入库一次, fetched_at 用于回溯.
            -- 解析层 (fact_top10_holder_period 等) 通过 raw_hash 链回此处.
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
            CREATE INDEX IF NOT EXISTS idx_raw_tdx_f10_fetched
                ON raw_tdx_f10_holder_research(fetched_at);
            CREATE INDEX IF NOT EXISTS idx_raw_tdx_f10_stock
                ON raw_tdx_f10_holder_research(stock_code, fetched_at DESC);

            -- ============================================================
            -- 事实层 (新): 十大流通股东 / 十大股东 (A/H 拆分版)
            -- ============================================================
            -- fact_top10_holder_period: tdxhub.holders 解析后的 canonical 事实.
            -- 每行 = 一只股票 × 报告期 × holder_set × holder_rank × share_class.
            -- 替代 market_raw_holdings (P7/P8 已删除).
            --
            -- 关键 schema 决策:
            --  1. holder_set ∈ ('free','all') 显式区分流通 vs 全量 (老表只覆盖 free).
            --  2. share_class ∈ ('A','H','B',NULL) — A/H 双重上市股票分两行,
            --     by is_secondary_class=TRUE 标记副 leg.
            --  3. is_exit_row=TRUE 表示来自 "退出前十大" 表 — 老表无此能力.
            --  4. source_tier 1=tdxhub primary, 2=miaoxiang fallback, 3=akshare.
            --  5. 老字段 (hold_amount/hold_ratio/hold_change/hold_change_num/
            --     raw_json/created_at/notice_date) 保留为 back-compat 别名,
            --     让现存 SQL 能通过 sed 替换表名平滑切换.
            CREATE TABLE IF NOT EXISTS fact_top10_holder_period (
                -- entity / period keys
                stock_code        TEXT NOT NULL,
                stock_name        TEXT,
                market            TEXT,
                report_date       TEXT NOT NULL,
                holder_set        TEXT NOT NULL,            -- 'free' | 'all'
                holder_rank       INTEGER NOT NULL,
                row_seq           INTEGER NOT NULL DEFAULT 1,

                -- holder identity
                holder_name       TEXT NOT NULL,
                holder_name_norm  TEXT,                      -- alias-resolved (汇金公司→中央汇金投资有限责任公司)
                share_class       TEXT,                      -- 'A' | 'H' | 'B' | NULL
                is_secondary_class BOOLEAN DEFAULT FALSE,    -- TRUE = A/H 双重上市的副 leg
                is_exit_row       BOOLEAN DEFAULT FALSE,     -- TRUE = 来自 "退出前十大" 表

                -- shares
                shares_text       TEXT,                       -- raw "6.8128亿"
                shares_approx     BIGINT,                     -- 681282900
                shares_precision  TEXT,                       -- '亿' | '万' | '股'
                hold_amount       REAL,                       -- back-compat: == shares_approx (REAL)

                -- ratio (双口径并存, 模型层选)
                hold_ratio_float  DOUBLE,                     -- 占流通股比 % (free 表)
                hold_ratio_total  DOUBLE,                     -- 占总股本比 % (all 表)
                hold_ratio        REAL,                       -- back-compat: holder_set='free'→float, 'all'→total

                -- derived
                hold_market_cap   REAL,                       -- shares × period-end close
                holder_type       TEXT,                       -- raw display
                share_nature      TEXT,                       -- '无限售A股/...'

                -- change
                change_status     TEXT,                       -- 新进/增持/减持/不变/退出
                change_shares_text TEXT,
                change_shares_approx BIGINT,
                hold_change       TEXT,                       -- back-compat: ''/新进/加仓/减仓
                hold_change_num   REAL,                       -- back-compat: signed shares delta

                -- temporal
                notice_date       TEXT,
                effective_date    TEXT,                       -- 公告日 + 1 交易日 (回测 PIT)
                page_update_date  TEXT,                       -- F10 页头 "更新日期"

                -- provenance
                source            TEXT NOT NULL,              -- 'tdx_f10' | 'miaoxiang' | 'akshare'
                source_tier       SMALLINT NOT NULL,          -- 1 / 2 / 3
                raw_hash          TEXT,                       -- → raw_tdx_f10_holder_research.raw_hash
                fetched_at        TEXT,
                created_at        TEXT,                       -- back-compat

                UNIQUE(stock_code, report_date, holder_set, source, is_exit_row,
                       holder_rank, row_seq, share_class)
            );
            CREATE INDEX IF NOT EXISTS idx_t10_stock
                ON fact_top10_holder_period(stock_code, report_date DESC);
            CREATE INDEX IF NOT EXISTS idx_t10_holder
                ON fact_top10_holder_period(holder_name);
            CREATE INDEX IF NOT EXISTS idx_t10_holder_norm
                ON fact_top10_holder_period(holder_name_norm);
            CREATE INDEX IF NOT EXISTS idx_t10_effective
                ON fact_top10_holder_period(effective_date);
            CREATE INDEX IF NOT EXISTS idx_t10_set_class
                ON fact_top10_holder_period(holder_set, share_class);

            -- ============================================================
            -- 事实层 (新): 控股股东 / 增减持计划 / 单笔变动
            -- ============================================================
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

            -- fact_shareholder_plan 不设 UNIQUE: F10 同一公告日同股东可能列出多条
            -- progress 更新 (预案 → 部分实施 → 完成), target_shares 也会变动.
            -- 幂等性靠 (stock_code, raw_hash) 在 ingest 时检查.
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
            CREATE INDEX IF NOT EXISTS idx_plan_stock_announce
                ON fact_shareholder_plan(stock_code, announce_date DESC);
            CREATE INDEX IF NOT EXISTS idx_plan_raw_hash
                ON fact_shareholder_plan(stock_code, raw_hash);

            -- fact_shareholder_trade 不设 UNIQUE: 同日同股东可能多笔交易 (拆单).
            -- 幂等性靠 (stock_code, raw_hash) 在 ingest 时检查.
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
            CREATE INDEX IF NOT EXISTS idx_trade_stock_date
                ON fact_shareholder_trade(stock_code, change_date DESC);
            CREATE INDEX IF NOT EXISTS idx_trade_raw_hash
                ON fact_shareholder_trade(stock_code, raw_hash);

            -- ============================================================
            -- 维度层 (新): 股东别名映射
            -- ============================================================
            -- TDX F10 用简称 (汇金公司 / 财政部 / 社保基金会 等),
            -- miaoxiang / akshare 用全称. dim_holder_alias 让跨源 join 名一致.
            CREATE TABLE IF NOT EXISTS dim_holder_alias (
                alias            TEXT NOT NULL,              -- TDX 简称 / 别名
                canonical_name   TEXT NOT NULL,              -- 工商全称
                category         TEXT,                       -- '国家队/央企/外资/...' (可选)
                note             TEXT,
                created_at       TEXT,
                PRIMARY KEY (alias)
            );

            -- K线已迁移到独立的 market.duckdb.price_kline

            CREATE TABLE IF NOT EXISTS raw_fetch_batch (
                batch_id        TEXT PRIMARY KEY,
                source          TEXT,
                fetch_type      TEXT,
                status          TEXT DEFAULT 'running',
                started_at      TEXT,
                finished_at     TEXT,
                rows_fetched    INTEGER DEFAULT 0,
                data_range_from TEXT,
                data_range_to   TEXT,
                error           TEXT
            );

            -- ============================================================
            -- 维度层
            -- ============================================================

            -- dim_stock 已退役（2026-04-08）：曾因从未被任何 sync 步骤填充导致
            -- sync_financial / calc_financial_derived / calc_screening 静默 0 行；
            -- 当前可交易 A 股主数据统一走 dim_active_a_stock（security_master 维护）。

            CREATE TABLE IF NOT EXISTS dim_active_a_stock (
                stock_code       TEXT PRIMARY KEY,
                stock_name       TEXT,
                market           TEXT,
                source           TEXT,
                updated_at       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_daas_updated ON dim_active_a_stock(updated_at);

            -- dim_stock_industry (申万三级) 已于 Phase 2 (TDX 迁移) 退役；
            -- 统一使用 dim_stock_tdx_industry 作为唯一行业真相源
            -- (同步维护: backend/services/tdx_industry_client.py::_ensure_table)
            CREATE TABLE IF NOT EXISTS dim_stock_tdx_industry (
                stock_code    TEXT PRIMARY KEY,
                tdx_l1        TEXT,
                tdx_l2        TEXT,
                tdx_l3        TEXT,
                tdx_l1_name   TEXT,
                tdx_l2_name   TEXT,
                tdx_l3_name   TEXT,
                sw_x_legacy   TEXT,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_tdx_industry_l1 ON dim_stock_tdx_industry(tdx_l1);
            CREATE INDEX IF NOT EXISTS idx_tdx_industry_l2 ON dim_stock_tdx_industry(tdx_l2);
            CREATE INDEX IF NOT EXISTS idx_tdx_industry_l3 ON dim_stock_tdx_industry(tdx_l3);

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
            CREATE INDEX IF NOT EXISTS idx_tdx_block_type ON dim_tdx_block_catalog(block_type);

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
            CREATE INDEX IF NOT EXISTS idx_stock_tdx_block_name ON dim_stock_tdx_block(block_name);
            CREATE INDEX IF NOT EXISTS idx_stock_tdx_block_cat ON dim_stock_tdx_block(block_category);

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
            CREATE INDEX IF NOT EXISTS idx_inst_type ON inst_institutions(type);
            CREATE INDEX IF NOT EXISTS idx_inst_enabled ON inst_institutions(enabled);

            CREATE TABLE IF NOT EXISTS inst_name_aliases (
                institution_id TEXT PRIMARY KEY,
                aliases        TEXT,
                created_at     TEXT,
                updated_at     TEXT
            );

            -- ============================================================
            -- 事实层
            -- ============================================================

            CREATE TABLE IF NOT EXISTS inst_holdings (
                institution_id  TEXT,
                holder_name     TEXT,
                holder_type     TEXT,
                stock_code      TEXT NOT NULL,
                stock_name      TEXT,
                report_date     TEXT NOT NULL,
                notice_date     TEXT,
                holder_rank     INTEGER,
                hold_amount     REAL,
                hold_market_cap REAL,
                hold_ratio      REAL,
                hold_change     TEXT,
                hold_change_num REAL,
                created_at      TEXT,
                UNIQUE(holder_name, stock_code, report_date)
            );
            CREATE INDEX IF NOT EXISTS idx_ih_inst ON inst_holdings(institution_id);
            CREATE INDEX IF NOT EXISTS idx_ih_stock ON inst_holdings(stock_code);
            CREATE INDEX IF NOT EXISTS idx_ih_report ON inst_holdings(report_date);
            -- Older DuckDB files may have been created before the UNIQUE clause above existed.
            -- ON CONFLICT needs an actual unique index on existing databases too.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ih_unique_holder_stock_report
                ON inst_holdings(holder_name, stock_code, report_date);

            CREATE TABLE IF NOT EXISTS fact_institution_event (
                institution_id    TEXT NOT NULL,
                holder_name       TEXT,
                stock_code        TEXT NOT NULL,
                stock_name        TEXT,
                report_date       TEXT NOT NULL,
                notice_date       TEXT,
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
            CREATE INDEX IF NOT EXISTS idx_event_type ON fact_institution_event(event_type);
            CREATE INDEX IF NOT EXISTS idx_event_date ON fact_institution_event(report_date);
            CREATE INDEX IF NOT EXISTS idx_event_notice ON fact_institution_event(notice_date);

            -- Phase 3b-3: fact_institution_event_industry_snapshot 已退役
            -- (原申万行业快照口径被 dim_stock_tdx_industry 直连聚合替代;
            --  backtest_engine / scoring 的 crowding_fit 口径也已同步)
            -- 收益字段已合并入 fact_institution_event

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
            CREATE INDEX IF NOT EXISTS idx_setup_snapshot_date
                ON fact_setup_snapshot(snapshot_date);
            CREATE INDEX IF NOT EXISTS idx_setup_snapshot_tag
                ON fact_setup_snapshot(setup_tag, snapshot_date);
            CREATE INDEX IF NOT EXISTS idx_setup_snapshot_stock
                ON fact_setup_snapshot(stock_code);

            -- ============================================================
            -- 集市层（派生，可重算）
            -- ============================================================

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

            -- ============================================================
            -- 系统层
            -- ============================================================

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

            -- 更新管线状态
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

            -- 抓取日志
            CREATE TABLE IF NOT EXISTS scan_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
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
            CREATE INDEX IF NOT EXISTS idx_gap_queue_dataset_status
                ON market_gap_queue(dataset, status);
            CREATE INDEX IF NOT EXISTS idx_gap_queue_status_updated
                ON market_gap_queue(status, updated_at DESC);
        """)
        conn.commit()

        # 增量添加新列。
        # 收益字段已直接维护在 fact_institution_event 上
        try:
            conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN win_rate_90d REAL")
        except Exception:
            pass
        try:
            # 审计报告 4.2: 补齐通用 120 日胜率列（之前仅 buy_win_rate_120d，fallback 路径错配）
            conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN win_rate_120d REAL")
        except Exception:
            pass
        # 审计报告 5.2: 把 institution_read.exit_stats 的 CTE 即席计算沉淀到 mart
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
            except Exception:
                pass
        try:
            conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN total_win_rate REAL")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN quality_score REAL")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE mart_institution_profile ADD COLUMN recent_exit_count INTEGER DEFAULT 0")
        except Exception:
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
            except Exception:
                pass
        # mart_stock_trend 新列（评分系统）
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
            except Exception:
                pass

        # fact_institution_event 增强：承载事件收益与路径分析字段
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
            except Exception:
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
            except Exception:
                pass

        # TDX 行业索引：必须在 ALTER TABLE 补齐列之后才能建
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_setup_snapshot_tdx1_date "
                "ON fact_setup_snapshot(snapshot_tdx_l1, snapshot_date)"
            )
        except Exception:
            pass

        # ─────────────────────────────────────────────────────────────
        # TDX 迁移: 退役申万 SW 列与 dim_stock_industry 表
        # (执行 Phase 2 迁移后, sw_level* 列从 schema 中移除;
        #  老库残留列通过 DROP COLUMN 清理)
        # 注意: 必须先 DROP 相关索引, 否则 DROP COLUMN 会因索引依赖失败
        # ─────────────────────────────────────────────────────────────
        for idx in ("idx_setup_snapshot_sw1_date", "idx_dsi_l1", "idx_dsi_l2"):
            try:
                conn.execute(f"DROP INDEX IF EXISTS {idx}")
            except Exception:
                pass
        sw_drop_plan = [
            ("fact_setup_snapshot", "snapshot_sw_level1"),
            ("fact_setup_snapshot", "snapshot_sw_level2"),
            ("fact_setup_snapshot", "snapshot_sw_level3"),
            ("mart_current_relationship", "sw_level1"),
            ("mart_current_relationship", "sw_level2"),
            ("mart_current_relationship", "sw_level3"),
        ]
        for tbl, col in sw_drop_plan:
            try:
                conn.execute(f"ALTER TABLE {tbl} DROP COLUMN {col}")
            except Exception:
                pass
        try:
            conn.execute("DROP TABLE IF EXISTS dim_stock_industry")
        except Exception:
            pass

        # Phase 0: mart 表增加 data_completeness 列
        for tbl in ["mart_institution_profile", "mart_institution_industry_stat",
                     "mart_stock_trend"]:
            try:
                conn.execute(
                    f"ALTER TABLE {tbl} ADD COLUMN data_completeness TEXT DEFAULT 'complete'"
                )
            except Exception:
                pass

        # Phase 3b-1: mart_institution_industry_stat 增加 tdx_code 列
        # 用于记录每行聚合的 TDX 行业代码 (T01 / T0401 / T040101); industry_name 仍存中文名。
        try:
            conn.execute(
                "ALTER TABLE mart_institution_industry_stat ADD COLUMN tdx_code TEXT"
            )
        except Exception:
            pass

        # Phase 3b-2: mart_institution_industry_stat.sw_level → industry_level
        # 原列名在 Phase 2 申万退役后语义已漂移 (值仍是 level1/level2/level3),
        # 重命名以解除 "sw" 字面与 TDX 真相源的混淆。
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(mart_institution_industry_stat)").fetchall()}
            if "sw_level" in cols and "industry_level" not in cols:
                conn.execute(
                    "ALTER TABLE mart_institution_industry_stat RENAME COLUMN sw_level TO industry_level"
                )
        except Exception:
            pass

        # Phase 3b-3: DROP 退役表 fact_institution_event_industry_snapshot
        # 申万快照已被 dim_stock_tdx_industry 直 JOIN 替代, 相关 SQL helper 亦已删除。
        for idx in ("idx_event_industry_snapshot_l1", "idx_event_industry_snapshot_l2"):
            try:
                conn.execute(f"DROP INDEX IF EXISTS {idx}")
            except Exception:
                pass
        try:
            conn.execute("DROP TABLE IF EXISTS fact_institution_event_industry_snapshot")
        except Exception:
            pass

        # Phase 3d-1: fact_stock_archetype / dim_stock_archetype_latest 列名正名
        # 原列 sw_level1/sw_level2 实际存的是通达信一级/二级中文名 (非申万代码),
        # 字面上与 TDX 真相源冲突, 重命名为 tdx_l1_name/tdx_l2_name 消歧。
        for table in ("fact_stock_archetype", "dim_stock_archetype_latest"):
            try:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if "sw_level1" in cols and "tdx_l1_name" not in cols:
                    conn.execute(f"ALTER TABLE {table} RENAME COLUMN sw_level1 TO tdx_l1_name")
                if "sw_level2" in cols and "tdx_l2_name" not in cols:
                    conn.execute(f"ALTER TABLE {table} RENAME COLUMN sw_level2 TO tdx_l2_name")
            except Exception:
                pass

        # Phase 3d-2: quality/turtle 表列名正名
        # - quality_features/quality_latest: sw_level1/2 → tdx_l1/tdx_l2 (存 TDX 代码)
        # - turtle_features/turtle_latest:   sw_level1/2 → tdx_l1_name/tdx_l2_name
        #   (当前经 dim_stock_forecast_latest 来源一路传递中文名, 保持语义一致)
        quality_tables = ("fact_stock_quality_features", "dim_stock_quality_latest")
        for table in quality_tables:
            try:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if "sw_level1" in cols and "tdx_l1" not in cols:
                    conn.execute(f"ALTER TABLE {table} RENAME COLUMN sw_level1 TO tdx_l1")
                if "sw_level2" in cols and "tdx_l2" not in cols:
                    conn.execute(f"ALTER TABLE {table} RENAME COLUMN sw_level2 TO tdx_l2")
            except Exception:
                pass
        turtle_tables = ("fact_stock_turtle_features", "dim_stock_turtle_latest")
        for table in turtle_tables:
            try:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if "sw_level1" in cols and "tdx_l1_name" not in cols:
                    conn.execute(f"ALTER TABLE {table} RENAME COLUMN sw_level1 TO tdx_l1_name")
                if "sw_level2" in cols and "tdx_l2_name" not in cols:
                    conn.execute(f"ALTER TABLE {table} RENAME COLUMN sw_level2 TO tdx_l2_name")
            except Exception:
                pass

        # Phase 1: mart_institution_profile 买入类评分字段 + 评分元数据
        for col in ["score_basis TEXT", "score_confidence TEXT",
                     "historical_median_holding_days INTEGER",
                     "current_avg_held_days INTEGER"]:
            try:
                conn.execute(f"ALTER TABLE mart_institution_profile ADD COLUMN {col}")
            except Exception:
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
            except Exception:
                pass

        # Phase 0: mart_current_relationship 物化表
        conn.execute("""
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
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcr_inst "
            "ON mart_current_relationship(institution_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcr_stock "
            "ON mart_current_relationship(stock_code)"
        )

        for tbl in (
            "mart_current_relationship",
            "dim_stock_industry_context_latest",
            "fact_stock_industry_context",
            "fact_stock_archetype", "dim_stock_archetype_latest",
            "fact_stock_quality_features", "dim_stock_quality_latest",
            "fact_stock_turtle_features", "dim_stock_turtle_latest",
        ):
            try:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            except Exception:
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
                    except Exception:
                        pass

        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(mart_institution_industry_stat)").fetchall()}
            if "industry_code" in cols and "tdx_code" in cols:
                conn.execute(
                    "UPDATE mart_institution_industry_stat "
                    "SET tdx_code = COALESCE(tdx_code, industry_code) "
                    "WHERE tdx_code IS NULL AND industry_code IS NOT NULL"
                )
                conn.execute("ALTER TABLE mart_institution_industry_stat DROP COLUMN industry_code")
            elif "industry_code" in cols and "tdx_code" not in cols:
                conn.execute("ALTER TABLE mart_institution_industry_stat RENAME COLUMN industry_code TO tdx_code")
        except Exception:
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
            except Exception:
                pass

        conn.commit()

        # 初始化排除类别（如果为空）
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
            for cat, label, enabled in categories:
                conn.execute(
                    "INSERT OR IGNORE INTO exclusion_categories (category, label, enabled, updated_at) VALUES (?, ?, ?, ?)",
                    (cat, label, enabled, now)
                )
            conn.commit()
            logger.info(f"[DB] 初始化 {len(categories)} 个排除类别")

        # ============================================================
        # Phase 2: 新增表 — 财务/选股/资产
        # ============================================================

        # 财务数据表（由 financial_client.py ensure_tables 管理，此处确保存在）
        from services.financial_client import ensure_tables as _ensure_fin_tables
        _ensure_fin_tables(conn)

        # 财务指标增强表（由 financial_indicator_client.py ensure_tables 管理）
        from services.financial_indicator_client import ensure_tables as _ensure_fin_indicator_tables
        _ensure_fin_indicator_tables(conn)

        # 资本行为增强表（由 capital_client.py ensure_tables 管理）
        from services.capital_client import ensure_tables as _ensure_capital_tables
        _ensure_capital_tables(conn)

        # 行业上下文中间层（由 industry_context_engine.py ensure_tables 管理）
        from services.industry_context_engine import ensure_tables as _ensure_industry_context_tables
        _ensure_industry_context_tables(conn)

        # 选股结果表（由 screening_engine.py ensure_tables 管理）
        from services.screening_engine import ensure_tables as _ensure_screen_tables
        _ensure_screen_tables(conn)

        # 板块动量表（由 sector_momentum.py ensure_tables 管理）
        from services.sector_momentum import ensure_tables as _ensure_sector_tables
        _ensure_sector_tables(conn)

        # 资产池维度表（ETF / 指数 / 股票统一管理）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dim_asset_universe (
                code        TEXT PRIMARY KEY,
                name        TEXT,
                asset_type  TEXT DEFAULT 'stock',
                market      TEXT,
                category    TEXT,
                list_date   TEXT,
                is_active   INTEGER DEFAULT 1,
                updated_at  TEXT
            )
        """)
        try:
            conn.execute("ALTER TABLE dim_asset_universe ADD COLUMN category TEXT")
        except Exception:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dau_type ON dim_asset_universe(asset_type)"
        )
        conn.execute("""
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
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_metf_snapshot ON mart_etf_snapshot_latest(snapshot_id)"
        )
        conn.execute("""
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
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mart_audit_snapshot_state (
                state_key       TEXT PRIMARY KEY,
                schema_version  INTEGER DEFAULT 1,
                computed_at     TEXT,
                source          TEXT,
                audit_json      TEXT
            )
        """)
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

        # P3.10 (2026-04-28): 关键大表索引 (DuckDB 用得着)
        # P7 (2026-04-28): 老 idx_mrh_* 索引随 market_raw_holdings 退役一并删除.
        try:
            for sql in (
                # fact_top10_holder_period 替代旧 mrh, 索引在 init_db 主块已建.
                # fact_institution_event 高频查 by stock_code + holder_name
                "CREATE INDEX IF NOT EXISTS idx_fie_stock ON fact_institution_event(stock_code, report_date DESC)",
                "CREATE INDEX IF NOT EXISTS idx_fie_holder ON fact_institution_event(holder_name, report_date DESC)",
                # mart_daily_recommendation 按日期查 topK
                "CREATE INDEX IF NOT EXISTS idx_mdr_date_rank ON mart_daily_recommendation(snapshot_date DESC, rank_in_date)",
                # fact_risk_factors P1.6
                "CREATE INDEX IF NOT EXISTS idx_rf_calc_date ON fact_risk_factors(calc_date DESC)",
                # mart_prediction_outcome P2.8
                "CREATE INDEX IF NOT EXISTS idx_po_snap_model ON mart_prediction_outcome(snapshot_date DESC, model_id)",
            ):
                try:
                    conn.execute(sql)
                except Exception as exc:
                    logger.debug(f"[index] 跳过 {sql[:60]}...: {exc}")
            conn.commit()
        except Exception as exc:
            logger.warning(f"[DB] 关键索引创建失败 (非致命): {exc}")

        # P0.1 (2026-04-28): 派生层 schema 版本管理
        # - 建 dim_schema_version 单点元数据表
        # - 重建所有 view (防底表 schema drift, 含历史踩雷的 mart_model_validation_fold)
        # - 检测 expected != actual 的派生表, 启动时 log WARN
        # - 首次启动把所有现存表的 actual 设为 expected (建立 baseline)
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

            # 看 dim_schema_version 是否为空 (首次启动)
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
