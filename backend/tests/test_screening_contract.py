import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


client = TestClient(app)


def test_screening_openapi_only_exposes_industry_overview_product_contract():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json().get("paths", {})

    assert "/api/screening/industry-overview" in paths
    assert "/api/screening/sector-momentum" not in paths
    assert "/api/screening/dual-confirm" not in paths
    assert "/api/screening/results" not in paths
    assert "/api/screening/detail/{stock_code}" not in paths
    assert "/api/screening/summary" not in paths