"""Ops observation endpoints are file-based and stay off DuckDB."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import ops_manual_run
from services import foundation_obs_serve


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ops_manual_run.router, prefix="/api/v3/ops")
    return TestClient(app)


def test_matrix_and_health_endpoints(tmp_path: Path, monkeypatch):
    audit = tmp_path / "data" / "audit"
    audit.mkdir(parents=True)
    (audit / "watermark_sla_20260824.json").write_text(
        json.dumps(
            {
                "run_at": "2026-08-24T06:00:00Z",
                "today": "20260824",
                "n_alerts": 0,
                "sources": [
                    {
                        "data_domain": "sync:daily",
                        "watermark_date": "20260820",
                        "watermark_days_ago": 3,
                        "sla_days": 1,
                        "status": "OK",
                        "alert": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    flags = tmp_path / "flags"
    flags.mkdir()
    monkeypatch.setattr(foundation_obs_serve, "REPO", tmp_path)
    monkeypatch.setattr(foundation_obs_serve, "FLAG_DIR", flags)
    client = _client()
    matrix = client.get("/api/v3/ops/matrix")
    assert matrix.status_code == 200
    body = matrix.json()
    assert body["n_domains"] == 1
    assert body["groups"][0]["domains"][0]["domain"] == "daily"
    one = client.get("/api/v3/ops/matrix/daily")
    assert one.status_code == 200
    assert one.json()["item"]["domain"] == "daily"
    health = client.get("/api/v3/ops/health")
    assert health.status_code == 200
    assert "alert_flags" in health.json()
    assert "DIFF_CORRECTNESS" not in json.dumps(health.json())
