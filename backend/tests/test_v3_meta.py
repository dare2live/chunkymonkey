"""Tests for v3 设计稿专用聚合 API (routers/v3_meta.py).

覆盖:
  - /api/v3/health
  - /api/v3/run-meta
  - /api/v3/significant
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """加载完整 FastAPI app（与生产同路径）。"""
    import sys
    from pathlib import Path
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from main import app  # noqa: WPS433
    return TestClient(app)


class TestV3Health:
    @pytest.mark.realdb
    def test_health_returns_ok(self, client):
        r = client.get("/api/v3/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["service"] == "v3-meta"
        assert "ts" in body and len(body["ts"]) >= 10  # ISO datetime


class TestV3RunMeta:
    @pytest.mark.realdb
    def test_run_meta_structure(self, client):
        r = client.get("/api/v3/run-meta")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        data = body["data"]

        # 强制 keys 存在（mock 默认值都是 None / 0）
        required_keys = {
            "plan_date", "signal_date", "built_at", "duration_min",
            "nav", "nav_chg_pct", "vs_hs300_pct", "vs_eq_pct",
            "challenger_pending", "system_alerts",
        }
        assert required_keys.issubset(set(data.keys()))

    @pytest.mark.realdb
    def test_run_meta_signal_date_format(self, client):
        r = client.get("/api/v3/run-meta")
        data = r.json()["data"]
        # 当 mart_daily_recommendation 有数据时，signal_date 必为 YYYY-MM-DD
        if data["signal_date"]:
            assert len(data["signal_date"]) == 10
            assert data["signal_date"][4] == "-" and data["signal_date"][7] == "-"

    @pytest.mark.realdb
    def test_run_meta_plan_date_after_signal_date(self, client):
        r = client.get("/api/v3/run-meta")
        data = r.json()["data"]
        if data["plan_date"] and data["signal_date"]:
            assert data["plan_date"] >= data["signal_date"], \
                "plan_date 必须 >= signal_date (T+1)"

    @pytest.mark.realdb
    def test_run_meta_challenger_pending_is_int(self, client):
        r = client.get("/api/v3/run-meta")
        data = r.json()["data"]
        assert isinstance(data["challenger_pending"], int)
        assert data["challenger_pending"] >= 0

    @pytest.mark.realdb
    def test_run_meta_system_alerts_is_list(self, client):
        r = client.get("/api/v3/run-meta")
        data = r.json()["data"]
        assert isinstance(data["system_alerts"], list)


class TestV3Significant:
    @pytest.mark.realdb
    def test_significant_default_limit(self, client):
        r = client.get("/api/v3/significant")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        assert "total" in body

    @pytest.mark.realdb
    def test_significant_limit_param(self, client):
        r = client.get("/api/v3/significant?limit=3")
        body = r.json()
        assert len(body["data"]) <= 3

    @pytest.mark.realdb
    def test_significant_limit_bounds(self, client):
        # FastAPI Query(ge=1, le=100) 会自动拒绝越界
        r = client.get("/api/v3/significant?limit=0")
        assert r.status_code == 422
        r = client.get("/api/v3/significant?limit=200")
        assert r.status_code == 422

    @pytest.mark.realdb
    def test_significant_row_shape(self, client):
        r = client.get("/api/v3/significant?limit=5")
        body = r.json()
        if body["data"]:
            row = body["data"][0]
            # v3 设计稿 CMV3.SIGNIFICANT_HOLDERS 字段
            required = {"id", "name", "type", "holdings", "win60", "stability", "last_action", "tracked"}
            assert required.issubset(set(row.keys()))
            # 类型校验
            assert isinstance(row["id"], str)
            assert isinstance(row["holdings"], int)
            assert isinstance(row["win60"], (int, float))
            assert 0.0 <= row["win60"] <= 1.0, "win60 必须是 0-1 ratio (后端 /100 后)"
            assert isinstance(row["tracked"], bool)


class TestV3Formulas:
    @pytest.mark.realdb
    def test_formulas_returns_list(self, client):
        r = client.get("/api/v3/formulas")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        # 至少 4 个公式 (Phase β W2 阶段)
        assert body["total"] >= 4
        # latest_date 字段存在 (mart_formula_horizon_evidence 有数据时)
        assert "latest_date" in body

    @pytest.mark.realdb
    def test_formula_row_shape(self, client):
        r = client.get("/api/v3/formulas")
        body = r.json()
        assert body["data"], "至少要有一个公式"
        f = body["data"][0]
        required = {"id", "name", "tag", "hit_today", "win_rate", "horizon", "n_signals_total"}
        assert required.issubset(set(f.keys()))
        assert isinstance(f["hit_today"], int)
        assert 0.0 <= f["win_rate"] <= 1.0
        assert isinstance(f["horizon"], int) and f["horizon"] > 0

    @pytest.mark.realdb
    def test_formula_ids_known(self, client):
        # 公式 id 应在已注册 FORMULA_METADATA 列表里
        from routers.v3_meta import FORMULA_METADATA
        known_ids = {f["id"] for f in FORMULA_METADATA}
        body = client.get("/api/v3/formulas").json()
        returned_ids = {f["id"] for f in body["data"]}
        assert returned_ids == known_ids

    def test_formula_stats_are_batched_per_source_table(self, monkeypatch):
        from routers import v3_meta

        calls = []
        table_checks = []

        class FakeConn:
            def __init__(self):
                self.kind = None

            def execute(self, sql, params=None):
                normalized = " ".join(str(sql).split())
                params_tuple = tuple(params or ())
                calls.append((normalized, params_tuple))
                if "SELECT MAX(date) FROM fact_technical_trigger" in normalized:
                    self.kind = "latest"
                elif "COUNT(*) AS hit_today" in normalized:
                    self.kind = "hit_today"
                elif "COUNT(*) AS n_signals_total" in normalized:
                    self.kind = "total"
                elif "ROW_NUMBER() OVER" in normalized:
                    self.kind = "horizon"
                else:
                    raise AssertionError(f"unexpected query: {normalized}")
                return self

            def fetchone(self):
                assert self.kind == "latest"
                return ("2026-05-12",)

            def fetchall(self):
                if self.kind == "hit_today":
                    return [
                        {"formula_id": "macd_golden_cross", "hit_today": 3},
                        {"formula_id": "turtle_breakout_20", "hit_today": 1},
                    ]
                if self.kind == "total":
                    return [
                        {"formula_id": "macd_golden_cross", "n_signals_total": 30},
                        {"formula_id": "turtle_breakout_20", "n_signals_total": 10},
                    ]
                if self.kind == "horizon":
                    return [
                        {"formula_id": "macd_golden_cross", "holding_days": 20, "win_rate": 0.61},
                        {"formula_id": "turtle_breakout_20", "holding_days": 55, "win_rate": 0.52},
                    ]
                raise AssertionError(f"unexpected fetchall kind: {self.kind}")

            def close(self):
                calls.append(("close", ()))

        fake_conn = FakeConn()

        def fake_table_exists(conn, table):
            assert conn is fake_conn
            table_checks.append(table)
            return table in {"fact_technical_trigger", "mart_formula_horizon_evidence"}

        monkeypatch.setattr(v3_meta, "get_conn", lambda: fake_conn)
        monkeypatch.setattr(v3_meta, "_table_exists", fake_table_exists)

        payload = asyncio.run(v3_meta.get_formulas())

        assert payload["ok"] is True
        by_id = {row["id"]: row for row in payload["data"]}
        assert by_id["macd_golden_cross"]["hit_today"] == 3
        assert by_id["macd_golden_cross"]["n_signals_total"] == 30
        assert by_id["macd_golden_cross"]["horizon"] == 20
        assert by_id["macd_golden_cross"]["win_rate"] == pytest.approx(0.61)
        assert by_id["dynamic_ma_iterative_cross"]["hit_today"] == 0
        assert by_id["dynamic_ma_iterative_cross"]["n_signals_total"] == 0
        assert by_id["dynamic_ma_iterative_cross"]["horizon"] == 20

        assert table_checks == ["fact_technical_trigger", "mart_formula_horizon_evidence"]
        query_texts = [call[0] for call in calls if call[0] != "close"]
        assert len(query_texts) == 4
        assert sum("fact_technical_trigger" in text for text in query_texts) == 3
        assert sum("mart_formula_horizon_evidence" in text for text in query_texts) == 1


class TestV3Fitness:
    @pytest.mark.realdb
    def test_fitness_default(self, client):
        r = client.get("/api/v3/fitness")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)

    @pytest.mark.realdb
    def test_fitness_row_shape(self, client):
        r = client.get("/api/v3/fitness?limit=3")
        body = r.json()
        if body["data"]:
            row = body["data"][0]
            required = {"fund", "tech", "formula_id", "holding_days", "n_signals",
                        "win_rate", "avg_ret", "avg_dd", "sharpe", "calmar", "is_recommended"}
            assert required.issubset(set(row.keys()))
            assert 0.0 <= row["win_rate"] <= 1.0
            assert isinstance(row["holding_days"], int)
            assert isinstance(row["is_recommended"], bool)
            assert row["n_signals"] >= 1

    @pytest.mark.realdb
    def test_fitness_limit_bounds(self, client):
        # 422 超界
        r = client.get("/api/v3/fitness?limit=0")
        assert r.status_code == 422
        r = client.get("/api/v3/fitness?limit=3000")
        assert r.status_code == 422

    @pytest.mark.realdb
    def test_fitness_sorted_by_win_rate_desc(self, client):
        body = client.get("/api/v3/fitness?limit=20").json()
        rates = [r["win_rate"] for r in body["data"]]
        # 升序检查: 应该 NaN/None 在最后, 但 win_rate 都是 float >= 0
        for i in range(len(rates) - 1):
            assert rates[i] >= rates[i + 1] - 1e-9, f"行 {i} 与 {i+1} 不按 win_rate desc 排序"


class TestV3Selections:
    @pytest.mark.realdb
    def test_selections_default(self, client):
        r = client.get("/api/v3/selections")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        assert "latest_date" in body

    @pytest.mark.realdb
    def test_selections_row_shape(self, client):
        r = client.get("/api/v3/selections?limit=3")
        body = r.json()
        if body["data"]:
            row = body["data"][0]
            required = {"code", "name", "hit_today", "n_total", "n30", "last_formula", "last_date"}
            assert required.issubset(set(row.keys()))
            assert isinstance(row["hit_today"], int)
            assert isinstance(row["n_total"], int)


class TestV3MountedStatic:
    """确认 design/ 静态文件 mount 工作。"""

    @pytest.mark.realdb
    def test_design_html_accessible(self, client):
        r = client.get("/v3/Chunky%20Monkey%20v3.html")
        assert r.status_code == 200
        assert b"Chunky Monkey" in r.content
        assert b"v3-data-live.jsx" in r.content, "v3.html 必须加载 live 数据脚本"

    @pytest.mark.realdb
    def test_v3_redirect(self, client):
        r = client.get("/v3", follow_redirects=False)
        assert r.status_code in (307, 308)
        assert "Chunky" in r.headers.get("location", "")

    @pytest.mark.realdb
    def test_tokens_css_accessible(self, client):
        r = client.get("/v3/tokens.css")
        assert r.status_code == 200
        assert b"--c-bg" in r.content
        assert b"--c-up" in r.content
        assert b"--c-down" in r.content

    @pytest.mark.realdb
    def test_v3_data_live_accessible(self, client):
        r = client.get("/v3/v3-data-live.jsx")
        assert r.status_code == 200
        assert b"loadLiveData" in r.content
        assert b"/api/inst/profiles" in r.content
        assert b"/api/v3/run-meta" in r.content

    @pytest.mark.realdb
    def test_v3_data_live_includes_picture_fetch(self, client):
        """Phase γ D5: v3-data-live.jsx 必须二次 fetch /picture/batch + /trade-plan。"""
        r = client.get("/v3/v3-data-live.jsx")
        assert r.status_code == 200
        # picture/batch 端点
        assert b"/api/v3/picture/batch" in r.content, \
            "v3-data-live.jsx 必须 fetch /api/v3/picture/batch (Phase γ D5)"
        # trade-plan 端点
        assert b"/api/v3/trade-plan/" in r.content
        # 字段合并到 STOCKS
        assert b"primary_type" in r.content
        assert b"fundamental_stage" in r.content
        assert b"technical_stage" in r.content
        assert b"entry_target_price" in r.content

    @pytest.mark.realdb
    def test_v3_data_live_includes_paper_fetch(self, client):
        """Phase δ D5: v3-data-live.jsx 必须三次 fetch paper engine 5 端点。"""
        r = client.get("/v3/v3-data-live.jsx")
        assert r.status_code == 200
        # 5 paper engine 端点都要 fetch
        for endpoint in [b"/api/v3/paper/nav", b"/api/v3/paper/holdings",
                         b"/api/v3/paper/kpis", b"/api/v3/paper/signal-ic",
                         b"/api/v3/paper/pl-attr"]:
            assert endpoint in r.content, f"v3-data-live.jsx 缺 {endpoint.decode()} (Phase δ)"
        # CMV3 字段被覆盖
        assert b"NAV_SERIES" in r.content
        assert b"HOLDINGS" in r.content
        assert b"KPIS" in r.content
        assert b"SIGNAL_IC" in r.content
        assert b"PL_ATTR" in r.content

    @pytest.mark.realdb
    def test_v3_data_live_includes_selection_fetch(self, client):
        """Phase ε D4: v3-data-live.jsx 必须四次 fetch selection 端点。"""
        r = client.get("/v3/v3-data-live.jsx")
        assert r.status_code == 200
        # selection 端点
        for endpoint in [b"/api/v3/selection/board", b"/api/v3/selection/weights",
                         b"/api/v3/selection/summary"]:
            assert endpoint in r.content, f"v3-data-live.jsx 缺 {endpoint.decode()} (Phase ε)"
        # CMV3 字段
        assert b"SELECTION_BOARD" in r.content
        assert b"FORMULA_WEIGHTS" in r.content
        # 每股 selection_* 字段
        assert b"selection_30d" in r.content
        assert b"selection_total" in r.content
        assert b"selection_win_rate" in r.content
