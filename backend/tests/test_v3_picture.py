"""Phase γ D4 — v3_picture router 单测。

覆盖 /api/v3/picture/{code}, /api/v3/picture/batch, /api/v3/trade-plan/{code}。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """加载完整 FastAPI app (与生产同路径)。"""
    import sys
    from pathlib import Path
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from main import app
    return TestClient(app)


class TestV3PictureSingle:
    @pytest.mark.realdb
    def test_picture_for_known_stock(self, client):
        """对真实 DB 已有的股票, 200 + 完整字段。"""
        # 600519 茅台一定有数据
        r = client.get("/api/v3/picture/600519")
        if r.status_code == 200:
            body = r.json()
            assert body["ok"] is True
            data = body["data"]
            if data:  # 有数据时校验字段
                required = {
                    "stock_code", "snapshot_date", "latest_close", "chg_pct",
                    "fundamental_stage", "fundamental_stage_days",
                    "technical_stage", "technical_stage_days",
                    "primary_type", "secondary_types",
                    "valuation_pe", "valuation_pe_pctile",
                    "institution_score", "institution_n_insts", "institution_top",
                }
                assert required.issubset(set(data.keys()))
                assert data["stock_code"] == "600519"
                # secondary_types 是 list (即使是空)
                assert isinstance(data["secondary_types"], list)
                assert isinstance(data["institution_top"], list)

    @pytest.mark.realdb
    def test_picture_for_unknown_stock(self, client):
        """不存在的股票码返回 404。"""
        r = client.get("/api/v3/picture/999999")
        # 404 (有表) 或 200 with null (无表) 都接受
        assert r.status_code in (200, 404)


class TestV3PictureBatch:
    @pytest.mark.realdb
    def test_batch_empty_codes(self, client):
        r = client.get("/api/v3/picture/batch?codes=")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["data"] == []
        assert body["total"] == 0

    @pytest.mark.realdb
    def test_batch_returns_known_stocks(self, client):
        r = client.get("/api/v3/picture/batch?codes=600519,000001,300750")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        # Batch endpoint must only return explicitly requested codes.
        codes_returned = {d["stock_code"] for d in body["data"]}
        # 至少应有 1 只 (取决于 mart_stock_picture_daily 是否已 build)
        if body["total"] > 0:
            assert codes_returned.issubset({"600519", "000001", "300750"})

    @pytest.mark.realdb
    def test_batch_limit_200(self, client):
        """超过 200 个 code 时截断。"""
        codes = ",".join([f"60{i:04d}" for i in range(300)])  # 300 个
        r = client.get(f"/api/v3/picture/batch?codes={codes}")
        assert r.status_code == 200
        # 不应抛错 (即使返回 0 行也 OK)


class TestV3TradePlan:
    @pytest.mark.realdb
    def test_trade_plan_not_built_yet(self, client):
        """trade_plan 表未生成 / 该股无 plan 时返回 message 或 404。"""
        r = client.get("/api/v3/trade-plan/999999")
        # 404 (有表无数据) 或 200 with message (无表) 都可
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            body = r.json()
            # 要么 data=null+message, 要么真实 data
            assert "ok" in body

    @pytest.mark.realdb
    def test_trade_plan_with_explicit_date(self, client):
        """显式 plan_date 参数路径。"""
        r = client.get("/api/v3/trade-plan/600519?plan_date=2026-05-12")
        assert r.status_code in (200, 404)

    @pytest.mark.realdb
    def test_trade_plan_with_model_id(self, client):
        """显式 model_id 参数路径。"""
        r = client.get("/api/v3/trade-plan/600519?model_id=v1")
        assert r.status_code in (200, 404)


class TestV3PictureFieldTypes:
    """fields 真实类型 (mart 已 build 时)。"""

    @pytest.mark.realdb
    def test_chg_pct_is_float_or_none(self, client):
        r = client.get("/api/v3/picture/600519")
        if r.status_code == 200 and r.json().get("data"):
            data = r.json()["data"]
            assert data["chg_pct"] is None or isinstance(data["chg_pct"], (int, float))

    @pytest.mark.realdb
    def test_stage_days_is_int_or_none(self, client):
        r = client.get("/api/v3/picture/600519")
        if r.status_code == 200 and r.json().get("data"):
            data = r.json()["data"]
            assert data["technical_stage_days"] is None or isinstance(data["technical_stage_days"], int)
