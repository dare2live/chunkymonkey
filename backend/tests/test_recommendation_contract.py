from fastapi.testclient import TestClient

from conftest import duck_mem
from main import app
from routers import recommendation


def test_daily_topk_items_include_model_trace(monkeypatch):
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE mart_model_lifecycle (
            model_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            deployed_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        CREATE TABLE mart_multidim_model (
            model_id TEXT,
            holdout_ic DOUBLE,
            holdout_rank_ic DOUBLE,
            holdout_top_decile_avg DOUBLE,
            holdout_long_short_spread DOUBLE,
            holdout_winrate_top DOUBLE,
            n_features INTEGER,
            created_at TEXT
        );
        CREATE TABLE mart_daily_recommendation (
            snapshot_date TEXT,
            stock_code TEXT,
            model_id TEXT,
            rank_in_date INTEGER,
            pred_score DOUBLE,
            percentile DOUBLE,
            regime_flag TEXT,
            run_mode TEXT,
            key_features_json TEXT,
            track_id TEXT,
            is_primary BOOLEAN
        );
        CREATE TABLE fact_institution_event (
            stock_code TEXT,
            stock_name TEXT
        );
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT
        );
        INSERT INTO mart_model_lifecycle VALUES (
            'model_a', 'champion', TIMESTAMP '2026-05-04 09:00:00', TIMESTAMP '2026-05-04 09:00:00'
        );
        INSERT INTO mart_multidim_model VALUES ('model_a', 0.1, 0.2, 0.3, 0.4, 0.5, 12, '2026-05-04');
        INSERT INTO mart_daily_recommendation VALUES (
            '2026-05-04', '000001', 'model_a', 1, 0.42, 0.99, 'up', 'champion',
            '{"model_top_features":[]}', 'primary', TRUE
        );
        INSERT INTO fact_institution_event VALUES ('000001', '平安银行');
        INSERT INTO dim_stock_tdx_industry VALUES ('000001', '金融', '银行');
        """
    )
    monkeypatch.setattr(recommendation, "get_conn", lambda: conn)

    response = TestClient(app).get("/api/rec/daily-topk?limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    item = payload["items"][0]
    assert item["stock_code"] == "000001"
    assert item["snapshot_date"] == "2026-05-04"
    assert item["model_id"] == "model_a"
    assert item["track_id"] == "primary"
    assert item["is_primary"] is True
