import sys
from pathlib import Path
import asyncio


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.market_signals as market_signals
import services.market_db as market_db
import services.stock_detail_read as stock_detail_read
import sqlite3


class _DummyMarketConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_load_stock_name_prefers_dim_active_then_market_raw_then_code():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dim_active_a_stock (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT
        );
        CREATE TABLE market_raw_holdings (
            stock_code TEXT,
            stock_name TEXT
        );
        """
    )
    try:
        conn.execute("INSERT INTO dim_active_a_stock VALUES (?, ?)", ("600001", "平安银行A"))
        conn.execute("INSERT INTO market_raw_holdings VALUES (?, ?)", ("600001", "平安银行B"))
        conn.execute("INSERT INTO market_raw_holdings VALUES (?, ?)", ("600002", "万科A"))
        conn.execute("INSERT INTO dim_active_a_stock VALUES (?, ?)", ("600003", ""))
        conn.commit()

        assert stock_detail_read.load_stock_name(conn, "600001") == "平安银行A"
        assert stock_detail_read.load_stock_name(conn, "600002") == "万科A"
        assert stock_detail_read.load_stock_name(conn, "600004") == "600004"
    finally:
        conn.close()


def test_load_stock_detail_timeline_merges_canonical_sources(monkeypatch):
    market_conn = _DummyMarketConn()
    monkeypatch.setattr(market_db, "get_market_conn", lambda: market_conn)
    monkeypatch.setattr(
        stock_detail_read,
        "_load_tdx_quarterly_overlay",
        lambda _conn, _code, years=3: {
            "series": [
                {
                    "date": "2024-03-31",
                    "holder_count": 12000,
                    "holder_count_delta_pct": 4.5,
                    "inst_total_count": 9,
                    "inst_total_count_delta": 2,
                    "fund_count": 3,
                    "fund_count_delta": 1,
                    "national_team_shares_wan": 2500,
                }
            ]
        },
    )
    monkeypatch.setattr(
        stock_detail_read,
        "_load_stock_timeline_events",
        lambda _conn, _code, years=3: [
            {"date": "2024-04-01", "lane": "notice", "title": "公告披露", "body": "1 家机构完成公告披露"}
        ],
    )
    monkeypatch.setattr(
        stock_detail_read,
        "_latest_daily_close",
        lambda _code, mkt_conn=None: {"date": "20240430", "close": 12.3},
    )
    monkeypatch.setattr(
        stock_detail_read,
        "_load_stock_price_timeline",
        lambda _code, mkt_conn=None, years=3, max_points=260: {
            "points": [{"date": "20240102", "close": 10.0}, {"date": "20240430", "close": 12.3}],
            "point_count": 2,
            "raw_point_count": 2,
            "start_date": "20240102",
            "end_date": "20240430",
            "start_close": 10.0,
            "end_close": 12.3,
            "high_close": 12.3,
            "low_close": 10.0,
            "change_pct": 23.0,
        },
    )
    monkeypatch.setattr(
        stock_detail_read,
        "_load_xdxr_timeline_events",
        lambda _code, mkt_conn=None, years=3: [
            {"date": "2024-02-01", "lane": "capital", "title": "送转", "body": "送转 1.00"}
        ],
    )

    payload = stock_detail_read.load_stock_detail_timeline(
        object(),
        "600519",
        {"events": [{"date": "2024-01-15", "lane": "holder", "title": "股东增减持", "body": "增持 1次"}]},
    )

    assert payload["latest_close_row"] == {"date": "20240430", "close": 12.3}
    assert payload["price_timeline"]["point_count"] == 2
    assert market_conn.closed is True
    assert [item["lane"] for item in payload["timeline_events"]] == ["holder", "capital", "tdx", "notice"]


def test_load_stock_detail_timeline_falls_back_without_market_data(monkeypatch):
    monkeypatch.setattr(market_db, "get_market_conn", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(
        stock_detail_read,
        "_load_tdx_quarterly_overlay",
        lambda _conn, _code, years=3: {
            "series": [{"date": "2024-03-31", "holder_count": 8000, "inst_total_count": 5}]
        },
    )
    monkeypatch.setattr(
        stock_detail_read,
        "_load_stock_timeline_events",
        lambda _conn, _code, years=3: [
            {"date": "2024-04-01", "lane": "notice", "title": "公告披露", "body": "1 家机构完成公告披露"}
        ],
    )

    payload = stock_detail_read.load_stock_detail_timeline(
        object(),
        "600519",
        {"events": [{"date": "2024-01-15", "lane": "holder", "title": "股东增减持", "body": "减持 1次"}]},
    )

    assert payload["latest_close_row"] is None
    assert payload["price_timeline"] == stock_detail_read.empty_price_timeline()
    assert [item["lane"] for item in payload["timeline_events"]] == ["holder", "tdx", "notice"]


def test_load_stock_tdx_block_memberships_groups_by_category():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dim_stock_tdx_block (
            stock_code TEXT,
            block_category TEXT,
            block_name TEXT,
            block_file TEXT,
            block_type INTEGER,
            code_index INTEGER,
            source TEXT,
            updated_at TEXT
        );
        CREATE TABLE dim_tdx_block_catalog (
            block_category TEXT,
            block_name TEXT,
            block_file TEXT,
            block_type INTEGER,
            member_count INTEGER,
            source TEXT,
            updated_at TEXT
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO dim_stock_tdx_block VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("600519", "gn", "白酒概念", "block_gn.dat", 4, 1, "tdxhub_1.1.1.1:7709", "2026-04-17T09:00:00"),
                ("600519", "gn", "消费升级", "block_gn.dat", 4, 2, "tdxhub_1.1.1.1:7709", "2026-04-17T09:00:00"),
                ("600519", "fg", "机构重仓", "block_fg.dat", 5, 1, "tdxhub_1.1.1.1:7709", "2026-04-17T09:00:00"),
                ("000001", "gn", "白酒概念", "block_gn.dat", 4, 1, "tdxhub_1.1.1.1:7709", "2026-04-17T09:00:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO dim_tdx_block_catalog VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("gn", "白酒概念", "block_gn.dat", 4, 18, "tdxhub_1.1.1.1:7709", "2026-04-17T09:00:00"),
                ("gn", "消费升级", "block_gn.dat", 4, 42, "tdxhub_1.1.1.1:7709", "2026-04-17T09:00:00"),
                ("fg", "机构重仓", "block_fg.dat", 5, 96, "tdxhub_1.1.1.1:7709", "2026-04-17T09:00:00"),
            ],
        )
        conn.commit()

        payload = stock_detail_read.load_stock_tdx_block_memberships(conn, "600519")

        assert payload["total_blocks"] == 3
        assert payload["source"] == "tdxhub_1.1.1.1:7709"
        assert payload["updated_at"] == "2026-04-17T09:00:00"
        assert [item["category"] for item in payload["categories"]] == ["gn", "fg"]
        assert payload["categories"][0]["label"] == "概念板块"
        assert payload["categories"][0]["blocks"][0] == {"name": "消费升级", "member_count": 42, "block_type": 4}
        assert payload["categories"][1]["blocks"][0]["name"] == "机构重仓"
    finally:
        conn.close()


def test_load_stock_detail_context_builds_canonical_payload(monkeypatch):
    monkeypatch.setattr(stock_detail_read, "load_stock_name", lambda _conn, _code: "贵州茅台")
    monkeypatch.setattr(stock_detail_read, "resolve_industry", lambda _conn, _code: {"sw_level2": "白酒"})
    monkeypatch.setattr(
        stock_detail_read,
        "load_stock_detail_timeline",
        lambda _conn, _code, shareholder_change_payload=None: {
            "latest_close_row": {"date": "20260415", "close": 11.2},
            "price_timeline": {"points": [1], "point_count": 1},
            "timeline_events": [{"title": "公告披露"}],
            "tdx_quarterly_overlay": {"series": [1]},
        },
    )
    monkeypatch.setattr(stock_detail_read, "load_margin_balance_overlay", lambda _code: {"latest": 1})
    monkeypatch.setattr(
        stock_detail_read,
        "load_stock_tdx_block_memberships",
        lambda _conn, _code: {
            "categories": [{"category": "gn", "label": "概念板块", "count": 1, "blocks": [{"name": "白酒概念", "member_count": 18, "block_type": 4}]}],
            "total_blocks": 1,
            "source": "tdxhub",
            "updated_at": "2026-04-17T09:00:00",
        },
    )
    monkeypatch.setattr(stock_detail_read, "load_stock_qlib_tdx_association", lambda *_args, **_kwargs: {"summary": ["ok"]})
    monkeypatch.setattr(
        stock_detail_read,
        "enrich_stock_institutions",
        lambda institutions, _latest_close_row: [dict(inst, latest_close_date="20260415") for inst in institutions],
    )
    monkeypatch.setattr(stock_detail_read, "load_stock_setup_row", lambda _conn, _code: {"setup_inst_id": "inst_a"})
    monkeypatch.setattr(
        stock_detail_read,
        "build_stock_setup_payload",
        lambda _setup, institutions: {
            "setup": {"setup_inst_id": "inst_a", "institution_count": len(institutions)},
            "stage": {"path_state": "突破准备"},
            "forecast": {"forecast_cross_section_score": 82},
            "turtle": {"turtle_setup_state": "S1待突破"},
        },
    )

    async def _fake_attention(_conn, _code):
        return {"ok": True, "stock_name": "贵州茅台"}

    monkeypatch.setattr(stock_detail_read, "load_stock_attention_payload", _fake_attention)
    monkeypatch.setattr(
        stock_detail_read,
        "merge_stock_attention_timeline_events",
        lambda timeline_events, _attention, _setup: timeline_events + [{"title": "外部关注"}],
    )

    payload = asyncio.run(
        stock_detail_read.load_stock_detail_context(
            object(),
            "600519",
            [{"institution_id": "inst_a", "notice_date": "2026-04-16"}],
            shareholder_change_payload={"recent_180d": {"event_count": 3}},
        )
    )

    assert payload["stock_name"] == "贵州茅台"
    assert payload["industry"]["sw_level2"] == "白酒"
    assert payload["industry"]["industry_level2"] == "白酒"
    assert payload["institutions"][0]["latest_close_date"] == "20260415"
    assert payload["setup"]["institution_count"] == 1
    assert payload["stage"]["path_state"] == "突破准备"
    assert payload["turtle"]["turtle_setup_state"] == "S1待突破"
    assert payload["attention"]["stock_name"] == "贵州茅台"
    assert payload["timeline_events"][-1]["title"] == "外部关注"
    assert payload["tdx_blocks"]["total_blocks"] == 1
    assert payload["shareholder_change_summary"] == {"event_count": 3}
    assert payload["latest_close_date"] == "20260415"
    assert payload["latest_notice_date"] == "2026-04-16"


def test_merge_stock_attention_timeline_events_uses_backend_attention_semantics():
    merged = stock_detail_read.merge_stock_attention_timeline_events(
        [{"date": "2024-01-10", "lane": "notice", "title": "公告披露", "body": "1 家机构完成公告披露"}],
        {
            "snapshot": {
                "composite_score": 78.2,
                "focus_index": 81.5,
                "last_survey_date": "20240112",
            },
            "research": {"latest_date": "20240113", "count_30d": 2, "count_90d": 4},
            "news": {"latest_time": "20240114", "count_30d": 5},
            "timeline_events": [],
        },
        {
            "attention_comment_trade_date": "20240111",
            "external_attention_signal": "关注抬升",
            "attention_survey_count_30d": 1,
        },
    )

    assert [item["title"] for item in merged] == ["公告披露", "外部关注", "机构调研", "个股研报", "新闻脉冲"]
    assert merged[1]["body"] == "关注抬升 · 确认 78.2 · 关注 81.5"
    assert merged[2]["body"] == "30天 1 次"


def test_enrich_stock_institutions_adds_latest_close_metrics():
    institutions = [
        {
            "institution_id": "inst_a",
            "inst_ref_cost": 10,
            "price_entry": 9,
            "return_to_now": None,
        },
        {
            "institution_id": "inst_b",
            "inst_ref_cost": None,
            "price_entry": None,
            "return_to_now": 12.345,
        },
    ]

    stock_detail_read.enrich_stock_institutions(institutions, {"date": "20260415", "close": 11})

    assert institutions[0]["report_return_to_now"] == 10.0
    assert institutions[0]["notice_return_to_now"] == 22.22
    assert institutions[0]["notice_return_status"] is None
    assert institutions[0]["latest_close_date"] == "20260415"
    assert institutions[1]["report_return_to_now"] is None
    assert institutions[1]["notice_return_to_now"] == 12.35


def test_load_stock_setup_row_uses_shared_detail_query():
    expected_row = {"setup_tag": "观察", "forecast_snapshot_date": "2024-04-11"}

    class _DummyCursor:
        def fetchone(self):
            return expected_row

    class _DummyConn:
        def __init__(self):
            self.sql = None
            self.params = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return _DummyCursor()

    conn = _DummyConn()

    payload = stock_detail_read.load_stock_setup_row(conn, "600519")

    assert payload == expected_row
    assert conn.params == ("600519",)
    assert "FROM mart_stock_trend t" in conn.sql
    assert "LEFT JOIN dim_stock_stage_latest st" in conn.sql
    assert "LEFT JOIN dim_stock_forecast_latest ff" in conn.sql
    assert "LEFT JOIN dim_stock_quality_latest q" in conn.sql
    assert "LEFT JOIN dim_stock_turtle_latest tf" in conn.sql


def test_load_stock_qlib_tdx_association_builds_canonical_payload(monkeypatch):
    class _DummyCursor:
        def __init__(self, one=None, rows=None):
            self._one = one
            self._rows = rows or []

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._rows

    class _DummyConn:
        def execute(self, sql, params=()):
            if "ORDER BY predict_date DESC" in sql:
                return _DummyCursor(one={"model_id": "model-x", "predict_date": "2024-04-12"})
            if "SELECT stock_code, qlib_percentile FROM qlib_predictions" in sql:
                return _DummyCursor(rows=[{"stock_code": "600519", "qlib_percentile": 98.2}])
            raise AssertionError(sql)

    monkeypatch.setattr(stock_detail_read, "_load_gpcw_report_coverage", lambda _conn, limit=12: [])
    monkeypatch.setattr(market_signals, "load_shareholder_change_universe_summary", lambda days: {"stocks": {}})

    payload = stock_detail_read.load_stock_qlib_tdx_association(
        _DummyConn(),
        "600519",
        {"capability_note": "tdx note"},
        None,
        {
            "note": "holder note",
            "recent_180d": {
                "event_count": 3,
                "increase_count": 2,
                "decrease_count": 1,
                "net_event_count": 1,
            },
        },
    )

    assert payload["model_id"] == "model-x"
    assert payload["sample_count"] == 1
    assert payload["sample_breakdown"] == ["增减持 1"]
    assert payload["coverage_note"] == "tdx note holder note"
    assert payload["summary"] == ["当前样本不足以给出稳定的 Qlib 联动结论"]
    assert [row["label"] for row in payload["rows"]] == ["近180天增持", "近180天减持"]
    assert payload["rows"][0]["position_text"] == "位于最高分位"
    assert payload["rows"][0]["delta_text"] == "净方向 +1次"


def test_build_stock_setup_payload_normalizes_setup_and_child_payloads():
    payload = stock_detail_read.build_stock_setup_payload(
        {
            "setup_inst_id": "inst_a",
            "quality_latest_financial_report_date": "2025-12-31",
            "latest_report_date": "2025-12-31",
            "quality_score_v1": 88.0,
            "company_quality_score": 88.0,
            "path_state": "未充分演绎",
            "forecast_20d_score": 83.0,
            "forecast_reason": "Qlib 截面较强",
            "turtle_setup_state": "S1待突破",
            "turtle_execution_score": 66.0,
        },
        [
            {
                "institution_id": "inst_a",
                "follow_gate": "follow",
                "follow_gate_reason": "near_cost",
                "premium_pct": 1.2,
                "premium_bucket": "near_cost",
                "report_return_to_now": 8.8,
                "notice_return_to_now": 5.5,
            }
        ],
    )

    assert payload["setup"]["company_quality_score_source"] == "quality_feature_v1"
    assert payload["setup"]["setup_follow_gate"] == "follow"
    assert payload["setup"]["setup_report_return_to_now"] == 8.8
    assert payload["stage"]["path_state"] == "未充分演绎"
    assert payload["forecast"]["forecast_cross_section_score"] == 83.0
    assert payload["turtle"]["turtle_setup_state"] == "S1待突破"


def test_build_stock_scoring_breakdown_payload_defaults_quality_source_and_keeps_factors():
    payload = stock_detail_read.build_stock_scoring_breakdown_payload(
        {
            "stock_code": "000001",
            "leader_inst": "测试机构",
            "leader_score": 88.0,
            "consensus_count": 3,
            "latest_notice_date": "2024-04-10",
            "notice_age_days": 7,
            "company_quality_score": 71.5,
            "company_quality_score_source": None,
            "quality_feature_snapshot_date": None,
            "discovery_score": 76.0,
            "stage_score": 58.0,
            "forecast_score": 62.0,
            "forecast_score_effective": 59.0,
            "raw_composite_priority_score": 81.2,
            "composite_priority_score": 64.0,
            "composite_cap_score": 64.0,
            "composite_cap_reason": "阶段封顶",
            "stock_archetype": "成长型",
            "priority_pool": "B池",
            "priority_pool_reason": "阶段封顶",
            "score_highlights": "盈利质量稳定",
            "score_risks": "财报覆盖仍需观察",
            "path_state": "突破准备",
            "data_completeness": 92.0,
            "sw_level2": "半导体",
            "price_entry": 12.3,
            "return_to_now": 8.6,
            "inst_ref_cost": 11.8,
            "inst_cost_method": "weighted",
            "premium_pct": 4.2,
            "premium_bucket": "温和",
            "follow_gate": "follow",
            "setup_tag": "观察",
            "setup_priority": 2,
            "setup_reason": "等待突破",
            "setup_confidence": 0.8,
            "setup_level": "L2",
            "setup_inst_name": "测试机构",
            "setup_event_type": "increase",
            "setup_industry_name": "半导体",
            "setup_score_raw": 77.0,
            "setup_execution_gate": "watch",
            "setup_execution_reason": "等待趋势确认",
            "industry_skill_raw": 68.0,
            "industry_skill_grade": "A",
            "followability_grade": "A",
            "premium_grade": "B",
            "report_recency_grade": "A",
            "reliability_grade": "A",
            "crowding_bucket": "中等",
            "crowding_yield_raw": 3.5,
            "crowding_yield_grade": "B",
            "crowding_stability_raw": 4.2,
            "crowding_stability_grade": "A",
            "crowding_fit_raw": 5.1,
            "crowding_fit_grade": "A",
            "crowding_fit_sample": 12,
            "crowding_fit_source": "fit_v1",
            "report_age_days": 9,
            "path_max_gain_pct": 18.0,
            "path_max_drawdown_pct": -7.0,
            "generic_stage_raw": 61.0,
            "stage_type_adjust_raw": -3.0,
            "stage_reason": "阶段仍需确认",
            "max_drawdown_60d": -12.0,
            "dist_ma250_pct": 4.5,
            "above_ma250": 1,
            "forecast_20d_score": 63.0,
            "forecast_60d_excess_score": 58.0,
            "forecast_risk_adjusted_score": 55.0,
            "forecast_reason": "模型相对行业占优",
            "forecast_model_id": "model-x",
            "forecast_predict_date": "2024-04-11",
            "forecast_industry_relative_group": "前10%",
            "turtle_execution_score": 54.0,
            "turtle_breakout_score": 52.0,
            "turtle_risk_score": 48.0,
            "turtle_score_delta": 6.0,
            "turtle_setup_state": "watch",
            "turtle_preferred_system": "S1",
            "turtle_reason": "等待突破",
        },
        object_id="000001",
    )

    assert payload["company_quality_score_source"] == "stock_scoring_v2"
    assert payload["quality_snapshot_date"] is None
    assert payload["stage"]["path_state"] == "突破准备"
    assert payload["forecast"]["forecast_cross_section_score"] == 63.0
    assert payload["turtle"]["turtle_preferred_system"] == "S1"
    assert payload["factors"]["quality"]["source_type"] == "stock_scoring_v2"
    assert payload["factors"]["price_path"]["follow_gate"] == "follow"
    assert payload["factors"]["setup"]["crowding_fit_source"] == "fit_v1"