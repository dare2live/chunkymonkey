import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


client = TestClient(app)


PUBLIC_PATHS = [
    "/api/inst/events",
    "/api/inst/stocks/detail/{stock_code}",
    "/api/inst/update/status",
    "/api/inst/update/smart",
    "/api/screening/industry-overview",
]

HIDDEN_PATHS = [
    "/api/inst/holdings",
    "/api/inst/setup-tracking/summary",
    "/api/inst/setup-tracking/snapshots",
    "/api/inst/setup-validation/report",
    "/api/inst/setup-replay/summary",
    "/api/inst/setup-replay/factors",
    "/api/inst/setup-replay/events",
    "/api/inst/stock-validation/report",
    "/api/inst/stocks/attention/{stock_code}",
    "/api/inst/industry-stats",
    "/api/inst/update/smart-plan",
    "/api/screening/sector-momentum",
    "/api/screening/dual-confirm",
    "/api/screening/results",
    "/api/screening/detail/{stock_code}",
    "/api/screening/summary",
]


def test_openapi_contract_keeps_internal_routes_hidden():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json().get("paths", {})

    for path in PUBLIC_PATHS:
        assert path in paths

    for path in HIDDEN_PATHS:
        assert path not in paths
