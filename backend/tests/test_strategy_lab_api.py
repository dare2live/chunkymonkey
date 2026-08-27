"""Strategy-lab observation API — compact, unclaimable, no partition dumps."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import strategy_lab as lab_api


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(lab_api.router, prefix="/api/v3/lab")
    return TestClient(app)


def test_status_never_claimable():
    r = _client().get("/api/v3/lab/status")
    assert r.status_code == 200
    body = r.json()
    assert body["claimable"] is False
    assert body["strategy_release"] is False
    assert body["framework"]["claimable"] is False
    assert "accepted_nominal_partitions" not in json.dumps(body)


def test_experiments_list_is_compact():
    r = _client().get("/api/v3/lab/experiments")
    assert r.status_code == 200
    body = r.json()
    blob = json.dumps(body)
    assert "accepted_nominal_partitions" not in blob
    assert body["n"] >= 7
    assert all(row["claimable"] is False for row in body["experiments"])
    assert all(row.get("not_strategy_spec") is True for row in body["experiments"] if row.get("readable"))


def test_experiment_detail_and_404():
    client = _client()
    r = client.get("/api/v3/lab/experiments/main_rally_v1/b0")
    assert r.status_code == 200
    exp = r.json()["experiment"]
    assert exp["family"] == "main_rally_v1"
    assert exp["block"] == "b0"
    assert exp["verdict"] == "reject"
    assert exp["claimable"] is False
    missing = client.get("/api/v3/lab/experiments/main_rally_v1/b9")
    assert missing.status_code == 404


def test_overview_and_packages_and_release_and_snapshots():
    client = _client()
    overview = client.get("/api/v3/lab/overview").json()
    assert overview["claimable"] is False
    assert overview["framework"]["claimable"] is False
    assert "accepted_nominal_partitions" not in json.dumps(overview)
    packages = client.get("/api/v3/lab/packages").json()
    assert packages["loaded"] is True
    ids = {item["package_id"] for item in packages["packages"]}
    assert "institution_follow_v1" in ids
    release = client.get("/api/v3/lab/release").json()
    assert release["any_accept"] is False
    assert release["strategy_release"] is False
    snaps = client.get("/api/v3/lab/snapshots").json()
    assert '"accepted":' not in json.dumps(snaps)
    kinds = {card["kind"] for card in snaps["cards"]}
    assert "holdout_seal" in kinds
