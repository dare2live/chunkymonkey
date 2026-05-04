from fastapi.testclient import TestClient
import main
from main import app

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


def test_index_injects_runtime_asset_version(monkeypatch):
    monkeypatch.setattr(main, "build_index_asset_version", lambda: "20260417abc")

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "text/html" in response.headers["content-type"]
    assert "window.CM_ASSET_VERSION = '20260417abc';" in response.text
