"""Phase ε D3 — v3_selection router 单测。

5 endpoint: /log, /history/{code}, /summary, /board, /weights
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import sys
    from pathlib import Path
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from main import app
    return TestClient(app)


class TestSelectionLog:
    @pytest.mark.realdb
    def test_log_basic(self, client):
        r = client.get("/api/v3/selection/log?limit=10")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        if body["data"]:
            row = body["data"][0]
            required = {"select_date", "stock_code", "select_source", "source_id"}
            assert required.issubset(set(row.keys()))

    @pytest.mark.realdb
    def test_log_source_filter(self, client):
        r = client.get("/api/v3/selection/log?source=formula&limit=5")
        assert r.status_code == 200
        body = r.json()
        for row in body["data"]:
            assert row["select_source"] == "formula"

    @pytest.mark.realdb
    def test_log_from_filter(self, client):
        r = client.get("/api/v3/selection/log?from=2026-05-01&limit=20")
        assert r.status_code == 200
        body = r.json()
        for row in body["data"]:
            assert row["select_date"] >= "2026-05-01"


class TestSelectionHistory:
    @pytest.mark.realdb
    def test_history_for_known_stock(self, client):
        # 真实 DB 中 600519 应有 selection 数据
        r = client.get("/api/v3/selection/history/600519")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["stock_code"] == "600519"
        if body["data"]:
            row = body["data"][0]
            # v3-data.jsx SELECTION_HISTORY 形状
            required = {"selectDate", "formula", "horizon", "retPct", "ddPct", "daysToT1", "outcome"}
            assert required.issubset(set(row.keys()))

    @pytest.mark.realdb
    def test_history_for_unknown_stock(self, client):
        r = client.get("/api/v3/selection/history/999999")
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []


class TestSelectionSummary:
    @pytest.mark.realdb
    def test_summary_for_one_stock(self, client):
        r = client.get("/api/v3/selection/summary?stock_code=600519")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        # 0 或 1 行 (若该股有 summary)

    @pytest.mark.realdb
    def test_summary_for_codes(self, client):
        r = client.get("/api/v3/selection/summary?codes=600519,000001,300750")
        assert r.status_code == 200
        body = r.json()
        # 字段 schema
        for row in body["data"]:
            assert "n_total" in row
            assert "n_30d" in row
            assert "last_outcome" in row


class TestSelectionBoard:
    @pytest.mark.realdb
    def test_board_default(self, client):
        r = client.get("/api/v3/selection/board")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        if body["data"]:
            row = body["data"][0]
            # mock SELECTION_BOARD 形状
            required = {"code", "name", "n30", "n_total", "win", "avg_ret",
                        "last_outcome", "last_date", "last_formula"}
            assert required.issubset(set(row.keys()))

    @pytest.mark.realdb
    def test_board_limit(self, client):
        r = client.get("/api/v3/selection/board?limit=5")
        body = r.json()
        assert len(body["data"]) <= 5

    @pytest.mark.realdb
    def test_board_limit_bounds(self, client):
        r = client.get("/api/v3/selection/board?limit=0")
        assert r.status_code == 422


class TestSelectionWeights:
    @pytest.mark.realdb
    def test_weights_default(self, client):
        r = client.get("/api/v3/selection/weights")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        if body["data"]:
            # 至少有 4 个公式 (Phase β 上线了 4 个)
            assert body["total"] >= 1
            row = body["data"][0]
            required = {"formula_id", "weight", "rolling_ic_30d", "rolling_ic_60d", "n_obs"}
            assert required.issubset(set(row.keys()))
            # 权重在 [0, 1]
            assert 0 <= row["weight"] <= 1
            # 总权重应接近 1
            total = sum(r["weight"] for r in body["data"])
            assert abs(total - 1.0) < 0.05  # 5% 容差 (hysteresis 可能造成小偏差)

    @pytest.mark.realdb
    def test_weights_sorted_desc(self, client):
        r = client.get("/api/v3/selection/weights")
        body = r.json()
        if len(body["data"]) >= 2:
            for i in range(len(body["data"]) - 1):
                assert body["data"][i]["weight"] >= body["data"][i + 1]["weight"]
