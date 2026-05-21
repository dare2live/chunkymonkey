"""Phase δ D4 — v3_paper router 单测。

5 endpoint: /nav, /holdings, /kpis, /signal-ic, /pl-attr
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from routers.v3_paper import _latest_picture_by_code


@pytest.fixture(scope="module")
def client():
    import sys
    from pathlib import Path
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from main import app
    return TestClient(app)


class _Rows:
    def __init__(self, *, one=None, all_rows=None):
        self._one = one
        self._all = all_rows or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class _PictureConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "information_schema.tables" in sql:
            return _Rows(one=(1,))
        return _Rows(
            all_rows=[
                ("000001", 12.3, "stage_a"),
                ("000002", 8.8, "stage_b"),
            ]
        )


def test_latest_picture_by_code_batches_unique_codes():
    conn = _PictureConn()

    out = _latest_picture_by_code(conn, ["000002", "000001", "000001"])

    assert out == {"000001": (12.3, "stage_a"), "000002": (8.8, "stage_b")}
    assert len(conn.calls) == 2
    assert "stock_code IN (?,?)" in conn.calls[1][0]
    assert conn.calls[1][1] == ["000001", "000002"]


def test_latest_picture_by_code_empty_input_skips_query():
    conn = _PictureConn()

    assert _latest_picture_by_code(conn, []) == {}
    assert conn.calls == []


class TestPaperNAV:
    def test_nav_returns_ok(self, client):
        r = client.get("/api/v3/paper/nav")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        # 若有数据, 验证字段
        if body["data"]:
            row = body["data"][0]
            required = {"snapshot_date", "nav", "nav_value", "daily_ret", "cum_ret"}
            assert required.issubset(set(row.keys()))

    def test_nav_with_from_date(self, client):
        r = client.get("/api/v3/paper/nav?from=2026-01-01")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        # latest 字段存在
        assert "latest" in body


class TestPaperHoldings:
    def test_holdings_returns_ok(self, client):
        r = client.get("/api/v3/paper/holdings")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        if body["data"]:
            row = body["data"][0]
            required = {"code", "open_price", "current_close", "qty", "ret_pct", "holding_days"}
            assert required.issubset(set(row.keys()))
            assert isinstance(row["ret_pct"], (int, float))


class TestPaperKPIs:
    def test_kpis_returns_ok(self, client):
        r = client.get("/api/v3/paper/kpis")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True

    def test_kpis_window_bounds(self, client):
        r = client.get("/api/v3/paper/kpis?window=0")
        assert r.status_code == 422
        r = client.get("/api/v3/paper/kpis?window=1000")
        assert r.status_code == 422


class TestPaperSignalIC:
    def test_signal_ic_returns_list(self, client):
        r = client.get("/api/v3/paper/signal-ic")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        if body["data"]:
            row = body["data"][0]
            assert "signal" in row
            assert "ic" in row

    def test_signal_ic_window(self, client):
        r = client.get("/api/v3/paper/signal-ic?window=30&horizon=5")
        assert r.status_code == 200

    def test_signal_ic_horizon_valid(self, client):
        r = client.get("/api/v3/paper/signal-ic?horizon=10")
        assert r.status_code == 200


class TestPaperPLAttr:
    def test_pl_attr_returns_by_formula(self, client):
        r = client.get("/api/v3/paper/pl-attr")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "by_formula" in body["data"]
        assert isinstance(body["data"]["by_formula"], list)
