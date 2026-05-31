import sys
from pathlib import Path
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from main import app
from routers import updater_connectivity
from routers.updater import check_connectivity

client = TestClient(app)


def _reset_connectivity_cache():
    updater_connectivity._connectivity_cache["checked_at"] = 0.0
    updater_connectivity._connectivity_cache["data"] = None


def test_updater_reexports_connectivity_helper():
    assert check_connectivity is updater_connectivity.check_connectivity


def test_get_cached_connectivity_reports_pending_when_empty():
    _reset_connectivity_cache()

    payload = updater_connectivity.get_cached_connectivity()

    assert payload["pending"] is True
    assert payload["cached"] is True
    assert payload["checked_at"] is None


@pytest.mark.asyncio
async def test_check_connectivity_uses_cached_probe(monkeypatch):
    _reset_connectivity_cache()
    calls = []

    async def _fake_compute_connectivity():
        calls.append("probe")
        return {
            "holdings_source": True,
            "kline_source": True,
            "industry_source": True,
            "message": "所有数据源正常",
        }

    monkeypatch.setattr(updater_connectivity, "_compute_connectivity", _fake_compute_connectivity)

    first = await updater_connectivity.check_connectivity(force=True)
    second = await updater_connectivity.check_connectivity()

    assert calls == ["probe"]
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["cache_age_seconds"] >= 0


@pytest.mark.asyncio
async def test_api_check_connectivity():
    with patch("routers.updater.check_connectivity", new_callable=AsyncMock) as mock_comp:
        mock_comp.return_value = {
            "all_healthy": True,
            "reports": {"em": True},
            "holdings": {"sina": True},
            "kline": {"tx": True},
            "industry": {"sw": True}
        }
        res = client.get("/api/inst/update/connectivity")
        assert res.status_code == 200
        data = res.json()
        assert data["all_healthy"] is True
