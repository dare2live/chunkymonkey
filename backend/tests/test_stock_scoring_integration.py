import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import market_db
from services.scoring import calculate_stock_scores


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );

        CREATE TABLE mart_stock_trend (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            latest_events TEXT,
            latest_report_date TEXT,
            latest_notice_date TEXT,
            price_1m_pct REAL,
            price_20d_pct REAL,
            price_trend TEXT,
            qlib_rank INTEGER,
            qlib_score REAL,
            qlib_percentile REAL,
            action_score REAL,
            leader_inst TEXT,
            leader_score REAL,
            consensus_count INTEGER,
            path_state TEXT,
            setup_tag TEXT,
            setup_priority INTEGER,
            setup_reason TEXT,
            setup_confidence TEXT,
            setup_level TEXT,
            setup_inst_id TEXT,
            setup_inst_name TEXT,
            setup_event_type TEXT,
            setup_industry_name TEXT,
            setup_score_raw REAL,
            setup_execution_gate TEXT,
            setup_execution_reason TEXT,
            industry_skill_raw REAL,
            industry_skill_grade INTEGER,
            followability_grade INTEGER,
            premium_grade INTEGER,
            report_recency_grade INTEGER,
            reliability_grade INTEGER,
            crowding_bucket TEXT,
            crowding_yield_raw REAL,
            crowding_yield_grade INTEGER,
            crowding_stability_raw REAL,
            crowding_stability_grade INTEGER,
            crowding_fit_raw REAL,
            crowding_fit_grade INTEGER,
            crowding_fit_sample INTEGER,
            crowding_fit_source TEXT,
            report_age_days INTEGER,
            discovery_score REAL,
            company_quality_score REAL,
            company_quality_score_source TEXT,
            quality_feature_snapshot_date TEXT,
            stage_score REAL,
            forecast_score REAL,
            forecast_score_effective REAL,
            raw_composite_priority_score REAL,
            composite_priority_score REAL,
            composite_cap_score REAL,
            composite_cap_reason TEXT,
            stock_archetype TEXT,
            priority_pool TEXT,
            priority_pool_reason TEXT,
            stock_gate TEXT,
            stock_gate_reason TEXT,
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
            score_highlights TEXT,
            score_risks TEXT,
            updated_at TEXT
        );

        CREATE TABLE mart_institution_profile (
            institution_id TEXT PRIMARY KEY,
            quality_score REAL,
            followability_score REAL,
            total_events INTEGER,
            buy_event_count INTEGER,
            buy_avg_gain_30d REAL,
            buy_win_rate_30d REAL,
            buy_median_max_drawdown_30d REAL
        );

        CREATE TABLE mart_institution_industry_stat (
            institution_id TEXT NOT NULL,
            industry_level TEXT NOT NULL,
            industry_name TEXT NOT NULL,
            sample_events INTEGER,
            avg_gain_30d REAL,
            win_rate_30d REAL,
            max_drawdown_30d REAL,
            PRIMARY KEY (institution_id, industry_level, industry_name)
        );

        CREATE TABLE mart_current_relationship (
            institution_id TEXT NOT NULL,
            display_name TEXT,
            stock_code TEXT NOT NULL,
            event_type TEXT,
            notice_date TEXT,
            report_date TEXT,
            holder_rank INTEGER,
            hold_ratio REAL,
            hold_market_cap REAL,
            change_pct REAL,
            premium_pct REAL,
            follow_gate TEXT,
            PRIMARY KEY (institution_id, stock_code)
        );

        CREATE TABLE fact_institution_event (
            institution_id TEXT,
            stock_code TEXT,
            report_date TEXT,
            event_type TEXT,
            premium_pct REAL,
            gain_30d REAL,
            max_drawdown_30d REAL
        );

        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT PRIMARY KEY,
            tdx_l1 TEXT,
            tdx_l2 TEXT,
            tdx_l3 TEXT
        );

        CREATE TABLE dim_financial_latest (
            stock_code TEXT PRIMARY KEY,
            latest_report_date TEXT,
            roe REAL,
            debt_ratio REAL,
            current_ratio REAL,
            gross_margin REAL,
            ocf_to_profit REAL,
            contract_to_revenue REAL,
            holder_count REAL,
            holder_count_change_pct REAL,
            float_shares REAL,
            total_shares REAL
        );

        CREATE TABLE dim_stock_quality_latest (
            stock_code TEXT PRIMARY KEY,
            snapshot_date TEXT,
            latest_financial_report_date TEXT,
            latest_indicator_report_date TEXT,
            quality_profit_raw REAL,
            quality_cash_raw REAL,
            quality_balance_raw REAL,
            quality_margin_raw REAL,
            quality_contract_raw REAL,
            quality_freshness_raw REAL,
            quality_capital_raw REAL,
            quality_efficiency_raw REAL,
            quality_growth_raw REAL,
            quality_score_v1 REAL
        );

        CREATE TABLE dim_stock_stage_latest (
            stock_code TEXT PRIMARY KEY,
            path_state TEXT,
            path_max_gain_pct REAL,
            path_max_drawdown_pct REAL,
            return_1m REAL,
            return_3m REAL,
            return_6m REAL,
            return_12m REAL,
            dist_ma120_pct REAL,
            dist_ma250_pct REAL,
            above_ma250 INTEGER,
            max_drawdown_60d REAL,
            amount_ratio_20_120 REAL,
            volatility_20d REAL,
            amplitude_20d REAL,
            stock_gate TEXT,
            generic_stage_raw REAL,
            stage_type_adjust_raw REAL,
            stage_quality_overheat_penalty REAL,
            stage_growth_slowdown_penalty REAL,
            stage_growth_stretch_penalty REAL,
            stage_cycle_realization_penalty REAL,
            stage_cycle_uncertainty_penalty REAL,
            stage_score_v1 REAL,
            stage_reason TEXT
        );

        CREATE TABLE dim_stock_forecast_latest (
            stock_code TEXT PRIMARY KEY,
            model_id TEXT,
            qlib_score REAL,
            qlib_rank INTEGER,
            qlib_percentile REAL,
            industry_qlib_percentile REAL,
            forecast_20d_score REAL,
            forecast_60d_excess_score REAL,
            forecast_risk_adjusted_score REAL,
            forecast_score_v1 REAL,
            forecast_reason TEXT
        );

        CREATE TABLE dim_stock_turtle_latest (
            stock_code TEXT PRIMARY KEY,
            preferred_system TEXT,
            turtle_setup_state TEXT,
            turtle_breakout_score REAL,
            turtle_risk_score REAL,
            turtle_execution_score_v1 REAL,
            turtle_reason TEXT,
            entry_signal_20 INTEGER,
            entry_signal_55 INTEGER,
            exit_signal_10 INTEGER,
            exit_signal_20 INTEGER
        );

        CREATE TABLE dim_stock_attention_latest (
            stock_code TEXT PRIMARY KEY,
            comment_trade_date TEXT,
            turnover_rate REAL,
            institution_participation REAL,
            composite_score REAL,
            rank_change REAL,
            focus_index REAL,
            survey_count_30d INTEGER,
            survey_count_90d INTEGER,
            survey_org_total_30d INTEGER,
            survey_org_total_90d INTEGER,
            comment_available INTEGER,
            survey_available INTEGER
        );
        """
    )
    return conn


def _make_market_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE price_kline (
            code TEXT,
            date TEXT,
            freq TEXT,
            adjust TEXT,
            open REAL,
            close REAL,
            high REAL,
            low REAL,
            volume REAL,
            amount REAL
        )
        """
    )
    return conn


def test_calculate_stock_scores_ranks_strong_new_entry_above_weak_signal(monkeypatch):
    conn = _make_conn()
    market_conn = _make_market_conn()
    today = date.today()
    recent_notice = (today - timedelta(days=5)).strftime("%Y-%m-%d")
    recent_report = (today - timedelta(days=18)).strftime("%Y-%m-%d")
    old_notice = (today - timedelta(days=140)).strftime("%Y-%m-%d")
    old_report = (today - timedelta(days=160)).strftime("%Y-%m-%d")

    monkeypatch.setattr(market_db, "get_market_conn", lambda timeout=30: market_conn)

    try:
        conn.executemany(
            """
            INSERT INTO mart_stock_trend (
                stock_code, stock_name, latest_events, latest_report_date, latest_notice_date,
                price_1m_pct, price_20d_pct, price_trend, qlib_rank, qlib_score, qlib_percentile
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "强势股", "[]", recent_report, recent_notice, 8.0, 12.0, "震荡", 1, 0.92, 95.0),
                ("000002", "弱势股", "[]", old_report, old_notice, -18.0, -24.0, "连跌", 20, 0.21, 20.0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO mart_institution_profile (
                institution_id, quality_score, followability_score, total_events,
                buy_event_count, buy_avg_gain_30d, buy_win_rate_30d, buy_median_max_drawdown_30d
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("inst_strong", 92.0, 88.0, 25, 25, 18.0, 66.0, 7.0),
                ("inst_quality_only", 98.0, 42.0, 30, 12, 6.0, 51.0, 15.0),
                ("inst_weak", 35.0, 24.0, 12, 12, 2.0, 38.0, 18.0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO mart_institution_industry_stat (
                institution_id, industry_level, industry_name, sample_events,
                avg_gain_30d, win_rate_30d, max_drawdown_30d
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("inst_strong", "level2", "半导体", 12, 20.0, 72.0, 8.0),
                ("inst_weak", "level2", "传媒", 6, -3.0, 35.0, 22.0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO mart_current_relationship (
                institution_id, display_name, stock_code, event_type, notice_date, report_date,
                holder_rank, hold_ratio, hold_market_cap, change_pct, premium_pct, follow_gate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("inst_strong", "强机构", "000001", "new_entry", recent_notice, recent_report, 1, 6.0, 200000000.0, 15.0, 2.0, "follow"),
                ("inst_quality_only", "高分老机构", "000001", "unchanged", recent_notice, recent_report, 4, 1.1, 30000000.0, 0.0, 8.0, "observe"),
                ("inst_weak", "弱机构", "000002", "decrease", old_notice, old_report, 9, 0.8, 12000000.0, -6.0, 25.0, "avoid"),
            ],
        )
        conn.executemany(
            "INSERT INTO fact_institution_event (institution_id, stock_code, report_date, event_type, premium_pct, gain_30d, max_drawdown_30d) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("inst_strong", "000001", recent_report, "new_entry", 2.0, 15.0, 5.0),
                ("inst_weak", "000002", old_report, "increase", 25.0, -8.0, 22.0),
            ],
        )
        conn.executemany(
            "INSERT INTO dim_stock_tdx_industry (stock_code, tdx_l1, tdx_l2, tdx_l3) VALUES (?, ?, ?, ?)",
            [
                ("000001", "电子", "半导体", "芯片设计"),
                ("000002", "传媒", "传媒", "广告营销"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO dim_financial_latest (
                stock_code, latest_report_date, roe, debt_ratio, current_ratio,
                gross_margin, ocf_to_profit, contract_to_revenue,
                holder_count, holder_count_change_pct, float_shares, total_shares
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", recent_report, 0.22, 0.22, 2.4, 0.48, 1.35, 0.03, 10000, -0.08, 100000000.0, 110000000.0),
                ("000002", old_report, 0.01, 0.82, 0.9, 0.12, 0.20, 0.35, 12000, 0.18, 150000000.0, 210000000.0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO dim_stock_quality_latest (
                stock_code, snapshot_date, latest_financial_report_date, latest_indicator_report_date,
                quality_profit_raw, quality_cash_raw, quality_balance_raw,
                quality_margin_raw, quality_contract_raw, quality_freshness_raw,
                quality_capital_raw, quality_efficiency_raw, quality_growth_raw,
                quality_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", recent_notice, recent_report, recent_report, 20.0, 20.0, 14.0, 8.0, 4.0, 5.0, 5.0, 10.0, 6.0, 82.0),
                ("000002", old_notice, old_report, old_report, 4.0, 3.0, 2.0, 2.0, 1.0, 1.0, -2.0, 3.0, 1.0, 32.0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO dim_stock_stage_latest (
                stock_code, path_state, path_max_gain_pct, path_max_drawdown_pct,
                return_1m, return_3m, return_6m, return_12m,
                dist_ma120_pct, dist_ma250_pct, above_ma250, max_drawdown_60d,
                amount_ratio_20_120, volatility_20d, amplitude_20d,
                stock_gate, generic_stage_raw, stage_type_adjust_raw,
                stage_quality_overheat_penalty, stage_growth_slowdown_penalty,
                stage_growth_stretch_penalty, stage_cycle_realization_penalty,
                stage_cycle_uncertainty_penalty, stage_score_v1, stage_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "温和验证", 18.0, 6.0, 8.0, 15.0, 20.0, 35.0, 5.0, 10.0, 1, 8.0, 1.2, 18.0, 5.0, "avoid", 75.0, 6.0, 0.0, 0.0, 0.0, 0.0, 0.0, 78.0, "阶段顺风"),
                ("000002", "失效破坏", -12.0, 28.0, -18.0, -25.0, -30.0, -40.0, -15.0, -20.0, 0, 25.0, 0.6, 45.0, 18.0, "follow", 28.0, -7.0, 0.0, 0.0, 0.0, 0.0, 0.0, 32.0, "阶段转坏"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO dim_stock_forecast_latest (
                stock_code, model_id, qlib_score, qlib_rank, qlib_percentile,
                industry_qlib_percentile, forecast_20d_score, forecast_60d_excess_score,
                forecast_risk_adjusted_score, forecast_score_v1, forecast_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "model_1", 0.92, 1, 95.0, 90.0, 90.0, 84.0, 86.0, 88.0, "Qlib截面排序较强"),
                ("000002", "model_1", 0.21, 20, 20.0, 18.0, 20.0, 22.0, 25.0, 24.0, "Qlib排序结构中性"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO dim_stock_turtle_latest (
                stock_code, preferred_system, turtle_setup_state,
                turtle_breakout_score, turtle_risk_score, turtle_execution_score_v1,
                turtle_reason, entry_signal_20, entry_signal_55, exit_signal_10, exit_signal_20
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "S2", "S2突破触发", 82.0, 69.0, 76.0, "55日突破已触发", 1, 1, 0, 0),
                ("000002", "S1", "20日退出触发", 24.0, 30.0, 32.0, "20日退出触发", 0, 0, 1, 1),
            ],
        )
        conn.execute(
            """
            INSERT INTO dim_stock_attention_latest (
                stock_code, comment_trade_date, turnover_rate, institution_participation,
                composite_score, rank_change, focus_index,
                survey_count_30d, survey_count_90d,
                survey_org_total_30d, survey_org_total_90d,
                comment_available, survey_available
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("000001", recent_notice, 2.0, 0.62, 72.0, 120.0, 66.0, 2, 4, 18, 40, 1, 1),
        )
        conn.commit()

        scored = calculate_stock_scores(conn)

        assert scored == 2
        rows = {
            row["stock_code"]: row
            for row in conn.execute(
                "SELECT * FROM mart_stock_trend ORDER BY stock_code"
            ).fetchall()
        }
        strong = rows["000001"]
        weak = rows["000002"]
        stage_rows = {
            row["stock_code"]: row
            for row in conn.execute(
                "SELECT stock_code, stock_gate FROM dim_stock_stage_latest ORDER BY stock_code"
            ).fetchall()
        }

        assert strong["priority_pool"] == "A池"
        assert strong["stock_gate"] == "follow"
        assert "综合评分" in (strong["stock_gate_reason"] or "")
        assert stage_rows["000001"]["stock_gate"] == "follow"
        assert strong["leader_inst"] == "inst_strong"
        assert strong["stock_archetype"] == "高质量稳健型"
        assert strong["composite_priority_score"] >= 75
        assert strong["discovery_score"] > strong["stage_score"] - 5
        assert strong["company_quality_score"] == 82.0
        assert strong["company_quality_score_source"] == "quality_feature_v1"
        assert strong["quality_feature_snapshot_date"] == recent_notice
        assert strong["external_attention_score"] is not None
        assert strong["forecast_score"] == 88.0
        assert strong["forecast_score_effective"] == 88.0
        assert strong["turtle_setup_state"] == "S2突破触发"
        assert strong["turtle_score_delta"] > 0
        assert "海龟" in (strong["score_highlights"] or "")

        assert weak["priority_pool"] == "D池"
        assert weak["stock_gate"] == "avoid"
        assert "综合评分" in (weak["stock_gate_reason"] or "")
        assert stage_rows["000002"]["stock_gate"] == "avoid"
        assert weak["stage_score"] < 40
        assert weak["company_quality_score"] == 32.0
        assert weak["company_quality_score"] < 45
        assert weak["composite_priority_score"] < 45
        assert weak["turtle_setup_state"] == "20日退出触发"
        assert weak["turtle_score_delta"] < 0
        assert "海龟" in (weak["score_risks"] or "")

        assert strong["action_score"] > weak["action_score"]
        assert strong["composite_priority_score"] > weak["composite_priority_score"]
    finally:
        conn.close()


def test_calculate_stock_scores_ignores_stale_quality_feature_snapshot(monkeypatch):
    conn = _make_conn()
    market_conn = _make_market_conn()
    today = date.today()
    recent_notice = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    recent_report = (today - timedelta(days=20)).strftime("%Y-%m-%d")
    stale_notice = (today - timedelta(days=80)).strftime("%Y-%m-%d")
    stale_report = (today - timedelta(days=120)).strftime("%Y-%m-%d")

    monkeypatch.setattr(market_db, "get_market_conn", lambda timeout=30: market_conn)

    try:
        conn.execute(
            """
            INSERT INTO mart_stock_trend (
                stock_code, stock_name, latest_events, latest_report_date, latest_notice_date,
                price_1m_pct, price_20d_pct, price_trend, qlib_rank, qlib_score, qlib_percentile
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("000009", "错位质量股", "[]", recent_report, recent_notice, 6.0, 10.0, "震荡", 3, 0.85, 88.0),
        )
        conn.execute(
            """
            INSERT INTO mart_institution_profile (
                institution_id, quality_score, followability_score, total_events,
                buy_event_count, buy_avg_gain_30d, buy_win_rate_30d, buy_median_max_drawdown_30d
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("inst_quality", 90.0, 84.0, 18, 18, 14.0, 63.0, 6.0),
        )
        conn.execute(
            """
            INSERT INTO mart_institution_industry_stat (
                institution_id, industry_level, industry_name, sample_events,
                avg_gain_30d, win_rate_30d, max_drawdown_30d
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("inst_quality", "level2", "半导体", 10, 16.0, 68.0, 7.0),
        )
        conn.execute(
            """
            INSERT INTO mart_current_relationship (
                institution_id, display_name, stock_code, event_type, notice_date, report_date,
                holder_rank, hold_ratio, hold_market_cap, change_pct, premium_pct, follow_gate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("inst_quality", "质量机构", "000009", "new_entry", recent_notice, recent_report, 1, 4.5, 160000000.0, 9.0, 1.8, "follow"),
        )
        conn.execute(
            "INSERT INTO fact_institution_event (institution_id, stock_code, report_date, event_type, premium_pct, gain_30d, max_drawdown_30d) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("inst_quality", "000009", recent_report, "new_entry", 1.8, 12.0, 6.0),
        )
        conn.execute(
            "INSERT INTO dim_stock_industry (stock_code, sw_level1, sw_level2, sw_level3) VALUES (?, ?, ?, ?)",
            ("000009", "电子", "半导体", "芯片设计"),
        )
        conn.execute(
            """
            INSERT INTO dim_financial_latest (
                stock_code, latest_report_date, roe, debt_ratio, current_ratio,
                gross_margin, ocf_to_profit, contract_to_revenue,
                holder_count, holder_count_change_pct, float_shares, total_shares
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("000009", recent_report, 0.20, 0.28, 2.1, 0.44, 1.18, 0.04, 9500, -0.06, 100000000.0, 108000000.0),
        )
        conn.execute(
            """
            INSERT INTO dim_stock_quality_latest (
                stock_code, snapshot_date, latest_financial_report_date, latest_indicator_report_date,
                quality_profit_raw, quality_cash_raw, quality_balance_raw,
                quality_margin_raw, quality_contract_raw, quality_freshness_raw,
                quality_capital_raw, quality_efficiency_raw, quality_growth_raw,
                quality_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("000009", stale_notice, stale_report, stale_report, 1.0, 1.0, 1.0, 1.0, 0.5, 0.5, -1.0, 1.0, 1.0, 5.0),
        )

        scored = calculate_stock_scores(conn)

        assert scored == 1
        row = conn.execute(
            "SELECT company_quality_score, company_quality_score_source, quality_feature_snapshot_date FROM mart_stock_trend WHERE stock_code = ?",
            ("000009",),
        ).fetchone()
        assert row["company_quality_score"] > 50.0
        assert row["company_quality_score"] != 5.0
        assert row["company_quality_score_source"] == "stock_scoring_v2"
        assert row["quality_feature_snapshot_date"] is None
    finally:
        conn.close()
        market_conn.close()
