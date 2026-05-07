from fastapi.testclient import TestClient

from main import app
from routers import signals as signals_router
from services.signals_v2 import PolicyConfig


client = TestClient(app)


class DummyConn:
    def close(self):
        pass


def test_today_route_cache_miss_does_not_materialize(monkeypatch):
    materialize_calls = []

    monkeypatch.setattr(signals_router, "get_conn", lambda: DummyConn())
    monkeypatch.setattr(
        signals_router,
        "load_config",
        lambda _conn: PolicyConfig(signal_freshness_days=30),
    )
    monkeypatch.setattr(
        signals_router,
        "load_today_signal_cache",
        lambda _conn, *, config, freshness_days: None,
    )

    def fake_materialize(_conn, *, config, freshness_days):
        materialize_calls.append((config, freshness_days))
        return {"summary": {}, "signals": [], "cache": {"status": "refreshed"}}

    monkeypatch.setattr(signals_router, "materialize_today_signal_cache", fake_materialize)

    response = client.get("/api/signals/today?freshness_days=30")

    assert response.status_code == 200
    payload = response.json()
    assert materialize_calls == []
    assert payload["signals"] == []
    assert payload["summary"]["cache"]["status"] == "miss"
    assert payload["summary"]["cache"]["requires_refresh"] is True


def test_today_route_refresh_materializes_snapshot(monkeypatch):
    materialize_calls = []

    monkeypatch.setattr(signals_router, "get_conn", lambda: DummyConn())
    monkeypatch.setattr(
        signals_router,
        "load_config",
        lambda _conn: PolicyConfig(signal_freshness_days=30),
    )

    def fake_materialize(_conn, *, config, freshness_days):
        materialize_calls.append((config, freshness_days))
        return {
            "summary": {"cache": {"status": "refreshed"}},
            "signals": [{"stock_code": "000001", "action": "follow"}],
            "cache": {"status": "refreshed"},
        }

    monkeypatch.setattr(signals_router, "materialize_today_signal_cache", fake_materialize)

    response = client.get("/api/signals/today?freshness_days=30&refresh=true")

    assert response.status_code == 200
    payload = response.json()
    assert len(materialize_calls) == 1
    assert payload["signals"] == [{"stock_code": "000001", "action": "follow"}]
    assert payload["summary"]["cache"]["status"] == "refreshed"
