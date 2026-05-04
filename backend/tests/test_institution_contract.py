import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
import routers.institution as institution_router
import services.holdings as holdings_service
from services.db import get_conn


client = TestClient(app)


def _has_stage_tables():
    """Step 5 任务 A：数据端点依赖 dim_stock_stage_latest，
    空 DB（data/ 未 symlink）时这些表不存在，跳过测试避免误报。"""
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_name IN ('dim_stock_stage_latest','mart_stock_trend')
            """
        ).fetchall()
        return len(row) >= 2
    finally:
        conn.close()


def test_stock_scoring_breakdown_exposes_shared_stage_turtle_payloads():
    if not _has_stage_tables():
        pytest.skip("dim_stock_stage_latest 不存在（空 DB）")
    response = client.get("/api/inst/scoring/breakdown/stock/603899")

    assert response.status_code == 200
    payload = response.json()

    assert payload["ok"] is True
    assert payload["card_type"] == "stock"
    assert payload["object_id"] == "603899"
    assert payload["stage"] is not None
    assert payload["turtle"] is not None


def test_institution_search_route_preserves_service_payload(monkeypatch):
    class _DummyConn:
        def close(self):
            return None

    seen = {}

    def _fake_search(conn, keywords, holder_type=""):
        seen["conn"] = conn
        seen["keywords"] = keywords
        seen["holder_type"] = holder_type
        return {
            "ok": True,
            "data": [{
                "holder_name": "高瓴 景林联合",
                "holder_type": "基金",
                "stock_count": 9,
                "latest_notice": "2026-04-12",
                "tracked": True,
            }],
            "total": 1,
            "keywords": ["高瓴 景林"],
        }

    monkeypatch.setattr(institution_router, "get_conn", lambda *args, **kwargs: _DummyConn())
    monkeypatch.setattr(institution_router, "search_institution_candidates", _fake_search)

    response = client.get(
        "/api/inst/institutions/search",
        params={"keywords": "高瓴 景林", "holder_type": "基金"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["total"] == 1
    assert payload["keywords"] == ["高瓴 景林"]
    assert payload["data"][0]["tracked"] is True
    assert seen["keywords"] == "高瓴 景林"
    assert seen["holder_type"] == "基金"


def test_stock_detail_route_preserves_shared_context_payload(monkeypatch):
    class _DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(institution_router, "get_conn", lambda *args, **kwargs: _DummyConn())
    monkeypatch.setattr(
        holdings_service,
        "get_stock_institutions",
        lambda _conn, _code: ([{"institution_id": "inst_a", "notice_date": "2026-04-16"}], "2026-03-31"),
    )
    monkeypatch.setattr(
        institution_router,
        "load_shareholder_change_payload",
        lambda _code: {"recent_180d": {"event_count": 2}},
    )

    async def _fake_detail_context(_conn, _code, institutions, shareholder_change_payload=None):
        assert shareholder_change_payload == {"recent_180d": {"event_count": 2}}
        return {
            "stock_name": "贵州茅台",
            "industry": {"industry_level2": "白酒"},
            "institutions": institutions,
            "setup": {"setup_tag": "观察"},
            "stage": {"path_state": "突破准备"},
            "forecast": {"forecast_cross_section_score": 82},
            "turtle": {"turtle_setup_state": "S1待突破"},
            "attention": {"ok": True},
            "price_timeline": {"points": []},
            "timeline_events": [{"title": "公告披露"}],
            "tdx_quarterly_overlay": {"series": []},
            "tdx_blocks": {"categories": [{"category": "gn", "label": "概念板块", "count": 1, "blocks": [{"name": "白酒概念", "member_count": 18, "block_type": 4}]}], "total_blocks": 1},
            "margin_balance_overlay": {"latest": 1},
            "shareholder_change_summary": {"event_count": 2},
            "qlib_tdx_association": {"summary": ["ok"]},
            "latest_close_date": "20260415",
            "latest_notice_date": "2026-04-16",
        }

    monkeypatch.setattr(institution_router, "load_stock_detail_context", _fake_detail_context)

    response = client.get("/api/inst/stocks/detail/600519")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["stock_code"] == "600519"
    assert payload["stock_name"] == "贵州茅台"
    assert payload["latest_report_date"] == "2026-03-31"
    assert payload["latest_notice_date"] == "2026-04-16"
    assert payload["total"] == 1
    assert payload["industry"]["industry_level2"] == "白酒"
    assert payload["setup"]["setup_tag"] == "观察"
    assert payload["stage"]["path_state"] == "突破准备"
    assert payload["forecast"]["forecast_cross_section_score"] == 82
    assert payload["turtle"]["turtle_setup_state"] == "S1待突破"
    assert payload["tdx_blocks"]["total_blocks"] == 1


def test_institution_openapi_hides_internal_replay_and_smart_plan_routes():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json().get("paths", {})

    assert "/api/inst/events" in paths
    assert "/api/inst/profiles/detail/{inst_id}" in paths
    # Setup / replay / validation 路由已废弃删除
    assert "/api/inst/setup-validation/report" not in paths
    assert "/api/inst/setup-tracking/snapshots" not in paths
    assert "/api/inst/setup-replay/summary" not in paths
    assert "/api/inst/setup-replay/factors" not in paths
    assert "/api/inst/setup-replay/events" not in paths
    assert "/api/inst/setup-tracking/summary" not in paths
    assert "/api/inst/stock-validation/report" not in paths
    assert "/api/inst/holdings" not in paths
    assert "/api/inst/stocks/attention/{stock_code}" not in paths
    assert "/api/inst/industry-stats" not in paths
    assert "/api/inst/update/smart-plan" not in paths
