import sys
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import duck_mem

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from main import app
from routers import market as market_router

client = TestClient(app)


def test_market_status(monkeypatch):
    conn = duck_mem()
    monkeypatch.setattr(market_router, "get_conn", lambda: conn)

    import services.audit as audit_service

    monkeypatch.setattr(
        audit_service,
        "load_quality_audit_snapshot",
        lambda _conn: {
            "layers": {
                "raw": {
                    "count": 2,
                    "latest_notice": "2026-05-26",
                    "stocks": 2,
                    "total_periods": 1,
                },
                "holdings": {"stocks": 1},
                "current_relationship": {"stocks": 1},
            },
            "snapshot_meta": {"source": "unit"},
        },
    )

    res = client.get("/api/inst/market/status")

    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["total_records"] == 2
    assert "current_stocks" in data
    assert "latest_notice_date" in data
