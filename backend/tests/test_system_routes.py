from fastapi.testclient import TestClient
import main
from main import app
from routers import workbench as workbench_router

client = TestClient(app)


def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "etf" in payload["available_modules"]


def test_inst_health_summary_alias_matches_health_contract():
    response = client.get("/api/inst/health/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "enabled_modules" in payload
    assert "etf" in payload["available_modules"]


def test_root_redirects_to_v3():
    """Phase ζ: 根路径默认重定向到 v3 设计稿。"""
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (307, 308)
    assert "v3" in response.headers.get("location", "").lower()


def test_legacy_returns_410_gone():
    """Phase ζ 收尾: 旧 vanilla 前端正式退役, /legacy 返回 410 Gone。"""
    response = client.get("/legacy", follow_redirects=False)
    assert response.status_code == 410
    body = response.json()
    assert body["error"] == "legacy_retired"
    assert "/v3" in body["redirect"]


def test_workbench_storage_route_defaults_to_persisted_read_model(monkeypatch):
    calls = []

    class DummyConn:
        def close(self):
            pass

    def fake_storage(_conn, *, include_live_plan=True):
        calls.append(include_live_plan)
        return {"retention": {"mode": "unavailable"}, "latest_manifest": {}}

    monkeypatch.setattr(workbench_router, "get_conn", lambda: DummyConn())
    monkeypatch.setattr(workbench_router, "build_workbench_storage", fake_storage)

    response = client.get("/api/workbench/storage")
    explicit = client.get("/api/workbench/storage?include_live_plan=true")

    assert response.status_code == 200
    assert explicit.status_code == 200
    assert calls == [False, True]
