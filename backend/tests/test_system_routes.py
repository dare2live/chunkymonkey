from fastapi.testclient import TestClient
import pytest

pytestmark = pytest.mark.realdb


@pytest.fixture
def client():
    from main import app

    return TestClient(app)


def test_health_check(client):
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "akquant" in payload["available_modules"]


def test_inst_health_summary_alias_matches_health_contract(client):
    response = client.get("/api/inst/health/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "enabled_modules" in payload
    assert "akquant" in payload["available_modules"]


def test_root_redirects_to_edge_app(client):
    """2026-07-07 更新: 根路径重定向到现行唯一前端 /app/ (edge React), 旧 v3 设计稿已退役。"""
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (307, 308)
    assert "/app" in response.headers.get("location", "").lower()


def test_legacy_and_v3_return_410_gone(client):
    """旧前端(vanilla /legacy + 已归档 /v3 设计稿)正式退役, 均返回 410 Gone 指向现行 /app/。"""
    for path in ("/legacy", "/v3"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 410
        body = response.json()
        assert body["error"] == "legacy_retired"
        assert "/app" in body["redirect"]


def test_toggle_modules_batches_allowed_settings(client, monkeypatch):
    import main

    class DummyConn:
        def __init__(self):
            self.rows = None
            self.committed = False
            self.closed = False

        def executemany(self, sql, rows):
            self.sql = sql
            self.rows = rows

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    conn = DummyConn()
    monkeypatch.setattr(main, "get_conn", lambda: conn)

    response = client.post("/api/settings/modules", json={"etf": True, "akquant": False, "unknown": True})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert conn.rows == [("module_akquant_enabled", "0")]
    assert conn.committed is True
    assert conn.closed is True


