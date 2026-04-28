import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
import services.stock_watchlist_read as stock_watchlist_read


def test_load_manual_stock_blacklist_rows_returns_sorted_dicts():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE excluded_stocks (
            stock_code TEXT,
            category TEXT,
            stock_name TEXT,
            reason TEXT,
            created_at TEXT
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO excluded_stocks (stock_code, category, stock_name, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("600002", "MANUAL", "样本B", "原因B", "2026-04-15T09:00:00"),
                ("600001", "MANUAL", "样本A", "原因A", "2026-04-16T09:00:00"),
                ("600003", "AUTO", "忽略", "忽略", "2026-04-17T09:00:00"),
            ],
        )
        conn.commit()

        rows = stock_watchlist_read.load_manual_stock_blacklist_rows(conn)

        assert rows == [
            {
                "stock_code": "600001",
                "stock_name": "样本A",
                "reason": "原因A",
                "created_at": "2026-04-16T09:00:00",
            },
            {
                "stock_code": "600002",
                "stock_name": "样本B",
                "reason": "原因B",
                "created_at": "2026-04-15T09:00:00",
            },
        ]
    finally:
        conn.close()


def test_load_candidate_setup_rows_filters_manual_exclusions_and_adds_stock_gate(monkeypatch):
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE excluded_stocks (
            stock_code TEXT,
            category TEXT
        );
        CREATE TABLE mart_stock_trend (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            latest_report_date TEXT,
            latest_notice_date TEXT,
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
            industry_skill_grade TEXT,
            followability_grade TEXT,
            premium_grade TEXT,
            report_recency_grade TEXT,
            reliability_grade TEXT,
            report_age_days INTEGER,
            discovery_score REAL,
            company_quality_score REAL,
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
            score_highlights TEXT,
            score_risks TEXT,
            crowding_bucket TEXT,
            crowding_yield_raw REAL,
            crowding_yield_grade TEXT,
            crowding_stability_raw REAL,
            crowding_stability_grade TEXT,
            crowding_fit_raw REAL,
            crowding_fit_grade TEXT,
            crowding_fit_sample INTEGER,
            crowding_fit_source TEXT,
            qlib_rank INTEGER
        );
        """
    )
    try:
        conn.execute("INSERT INTO excluded_stocks (stock_code, category) VALUES (?, ?)", ("600002", "MANUAL"))
        conn.execute(
            """
            INSERT INTO mart_stock_trend (
                stock_code, stock_name, latest_report_date, latest_notice_date, path_state,
                setup_tag, setup_priority, setup_reason, setup_confidence, setup_inst_name,
                setup_industry_name, setup_score_raw, discovery_score, company_quality_score,
                stage_score, forecast_score, forecast_score_effective,
                raw_composite_priority_score, composite_priority_score,
                stock_archetype, priority_pool, priority_pool_reason, qlib_rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "600001", "样本A", "2026-03-31", "2026-04-10", "突破准备",
                "industry_expert_entry", 1, "等待突破", "high", "机构甲",
                "半导体", 81.0, 74.0, 70.0,
                68.0, 62.0, 60.0,
                83.0, 78.0,
                "成长型", "A池", "综合评分 78，进入 A池", 123,
            ),
        )
        conn.execute(
            """
            INSERT INTO mart_stock_trend (
                stock_code, stock_name, setup_tag, setup_priority,
                composite_priority_score, priority_pool, priority_pool_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("600002", "已拉黑样本", "industry_expert_entry", 2, 70.0, "B池", "进入 B池"),
        )
        conn.commit()

        monkeypatch.setattr(
            stock_watchlist_read,
            "load_industry_map",
            lambda _conn: {
                "600001": {"tdx_l1": "T10", "tdx_l2": "T1001", "tdx_l3": "T100101"},
                "600002": {"tdx_l1": "T20", "tdx_l2": "T2001", "tdx_l3": "T200101"},
            },
        )

        rows = stock_watchlist_read.load_candidate_setup_rows(conn, limit=10)

        assert len(rows) == 1
        item = rows[0]
        assert item["stock_code"] == "600001"
        assert item["tdx_l2"] == "T1001"
        assert item["stock_gate"] == "follow"
        assert "A池" in (item["stock_gate_reason"] or "")
    finally:
        conn.close()


def test_load_watchlist_rows_keeps_missing_trend_null_and_defaults_quality_source():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE stock_watchlist (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            added_date TEXT,
            added_price REAL,
            added_reason TEXT,
            source_institution TEXT,
            source_event_type TEXT,
            status TEXT,
            updated_at TEXT,
            gain_since_added REAL,
            max_gain REAL,
            max_drawdown REAL
        );
        CREATE TABLE mart_stock_trend (
            stock_code TEXT PRIMARY KEY,
            setup_tag TEXT,
            setup_priority INTEGER,
            setup_reason TEXT,
            setup_confidence TEXT,
            discovery_score REAL,
            company_quality_score REAL,
            company_quality_score_source TEXT,
            quality_feature_snapshot_date TEXT,
            stage_score REAL,
            forecast_score REAL,
            raw_composite_priority_score REAL,
            composite_priority_score REAL,
            priority_pool TEXT,
            priority_pool_reason TEXT,
            composite_cap_reason TEXT,
            external_attention_score REAL,
            external_crowding_penalty REAL,
            external_attention_signal TEXT,
            score_highlights TEXT,
            score_risks TEXT
        );
        """
    )
    try:
        conn.executemany(
            """
            INSERT INTO stock_watchlist (
                stock_code, stock_name, added_date, added_price, added_reason,
                source_institution, source_event_type, status, updated_at,
                gain_since_added, max_gain, max_drawdown
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("600036", "招行", "2026-04-10", 10.0, "手工加入", "测试机构", "increase", "active", "2026-04-15T10:00:00", 5.2, 8.4, 1.3),
                ("000001", "平安银行", "2026-04-09", 9.5, "观察", "测试机构", "increase", "active", "2026-04-15T09:00:00", 1.2, 2.4, 0.8),
            ],
        )
        conn.execute(
            """
            INSERT INTO mart_stock_trend (
                stock_code, setup_tag, setup_priority, setup_reason, setup_confidence,
                discovery_score, company_quality_score, company_quality_score_source,
                quality_feature_snapshot_date, stage_score, forecast_score,
                raw_composite_priority_score, composite_priority_score, priority_pool,
                priority_pool_reason, composite_cap_reason, external_attention_score,
                external_crowding_penalty, external_attention_signal, score_highlights,
                score_risks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "600036", "industry_expert_entry", 1, "等待突破", "high",
                75.0, 71.0, None,
                None, 58.0, 62.0,
                77.0, 74.0, "B池",
                "阶段未完全打开", "阶段封顶", 68.0,
                3.0, "关注度抬升", "亮点", "风险",
            ),
        )
        conn.commit()

        rows = stock_watchlist_read.load_watchlist_rows(conn)
        row_map = {row["stock_code"]: row for row in rows}

        assert row_map["600036"]["company_quality_score_source"] == "stock_scoring_v2"
        assert row_map["600036"]["stock_gate"] == "watch"
        assert row_map["000001"]["company_quality_score_source"] is None
        assert row_map["000001"]["stock_gate"] is None
        assert row_map["000001"]["stock_gate_reason"] is None
    finally:
        conn.close()