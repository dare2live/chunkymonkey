"""Endpoint contract for blended recommendation without production DB state."""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import duck_mem
from routers import v3_selection
from services.selection.blended_recommendation import ensure_blended_table


def test_blended_endpoint_reads_mocked_duckdb_connection(monkeypatch):
    conn = duck_mem()
    ensure_blended_table(conn)
    conn.executemany(
        """
        INSERT INTO mart_daily_blended_recommendation
          (snapshot_date, stock_code, model_id,
           base_pred_score, formula_bonus, blended_score,
           rank_in_date, base_rank_in_date, formula_breakdown_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "2026-05-12",
                "B",
                "blended_v1",
                0.80,
                0.50,
                1.20,
                1,
                2,
                json.dumps([{"formula_id": "macd", "contrib": 0.5}]),
            ),
            (
                "2026-05-12",
                "A",
                "blended_v1",
                0.90,
                -0.50,
                0.45,
                2,
                1,
                json.dumps([{"formula_id": "turtle", "contrib": -0.5}]),
            ),
        ],
    )
    conn.commit()
    monkeypatch.setattr(v3_selection, "get_conn", lambda: conn)

    app = FastAPI()
    app.include_router(v3_selection.router, prefix="/api/v3/selection")
    client = TestClient(app)

    response = client.get("/api/v3/selection/blended?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["snapshot_date"] == "2026-05-12"
    assert body["total"] == 2

    rows = body["data"]
    assert [row["stock_code"] for row in rows] == ["B", "A"]
    assert rows[0]["rank_delta"] == 1
    assert rows[0]["formula_breakdown"][0]["formula_id"] == "macd"
    required = {
        "stock_code",
        "rank_in_date",
        "base_rank_in_date",
        "base_pred_score",
        "formula_bonus",
        "blended_score",
    }
    assert required.issubset(rows[0])
