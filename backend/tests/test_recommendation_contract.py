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
        CREATE TABLE dim_active_a_stock (
            stock_code TEXT,
            stock_name TEXT
        );
        CREATE TABLE mart_stock_trend (
            stock_code TEXT,
            stock_name TEXT
        );
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT
        );
        CREATE TABLE mart_stock_horizon_selection (
            run_id TEXT,
            stock_code TEXT,
            baseline_label TEXT,
            baseline_horizon_days INTEGER,
            selected_label TEXT,
            selected_horizon_days INTEGER,
            selected_horizon_confidence DOUBLE,
            selected_horizon_score DOUBLE,
            baseline_horizon_score DOUBLE,
            score_advantage DOUBLE,
            avg_return_advantage DOUBLE,
            selected_max_drawdown DOUBLE,
            baseline_max_drawdown DOUBLE,
            selected_obs_count INTEGER,
            baseline_obs_count INTEGER,
            gate_status TEXT,
            fallback_reason TEXT,
            built_at TEXT
        );
        CREATE TABLE mart_stock_horizon_feature_effect (
            run_id TEXT,
            stock_code TEXT,
            label_name TEXT,
            horizon_days INTEGER,
            feature_name TEXT,
            obs_count INTEGER,
            corr DOUBLE,
            abs_corr_rank INTEGER,
            effect_direction TEXT,
            built_at TEXT
        );
        CREATE TABLE mart_stock_horizon_candidate_gate (
            run_id TEXT,
            stock_code TEXT,
            label_name TEXT,
            horizon_days INTEGER,
            avg_return DOUBLE,
            max_drawdown DOUBLE,
            win_rate DOUBLE,
            volatility DOUBLE,
            obs_count INTEGER,
            candidate_status TEXT,
            reason_code TEXT
        );
        INSERT INTO mart_model_lifecycle VALUES (
            'model_a', 'champion', TIMESTAMP '2026-05-04 09:00:00', TIMESTAMP '2026-05-04 09:00:00'
        );
        INSERT INTO mart_multidim_model VALUES ('model_a', 0.1, 0.2, 0.3, 0.4, 0.5, 12, '2026-05-04');
        INSERT INTO mart_daily_recommendation VALUES (
            '2026-05-04', '000001', 'model_a', 1, 0.42, 0.99, 'up', 'champion',
            '{"model_top_features":[{"name":"ret_20d"}],"stock_feature_values":[{"name":"ret_20d","raw_value":0.12,"model_value":0.12}]}',
            'primary', TRUE
        );
        INSERT INTO fact_institution_event VALUES ('000001', '平安银行');
        INSERT INTO dim_active_a_stock VALUES ('000001', '平安银行A股');
        INSERT INTO mart_stock_trend VALUES ('000001', '平安银行趋势');
        INSERT INTO dim_stock_tdx_industry VALUES ('000001', '金融', '银行');
        INSERT INTO mart_stock_horizon_selection VALUES (
            'stock_horizon_latest', '000001', 'follow_net_return_60d', 60,
            'follow_net_return_90d', 90, 0.72, 0.18, 0.12, 0.06, 0.03,
            -0.08, -0.13, 90, 90, 'selected', NULL, '2026-05-06T10:00:00'
        );
        INSERT INTO mart_stock_horizon_feature_effect VALUES (
            'stock_horizon_latest', '000001', 'follow_net_return_90d', 90,
            'ma_ratio_60', 90, -0.42, 1, 'negative', '2026-05-06T10:00:00'
        );
        INSERT INTO mart_stock_horizon_candidate_gate VALUES
            ('stock_horizon_latest', '000001', 'follow_net_return_60d', 60,
             0.01, -0.13, 0.52, 0.12, 90, 'baseline', 'baseline_60d'),
            ('stock_horizon_latest', '000001', 'follow_net_return_90d', 90,
             0.04, -0.08, 0.60, 0.11, 90, 'candidate_pass', 'candidate_pass');
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
    assert item["stock_name"] == "平安银行A股"
    assert item["track_id"] == "primary"
    assert item["is_primary"] is True
    assert item["selected_horizon_days"] == 90
    assert item["horizon_selection_run_id"] == "stock_horizon_latest"
    assert item["horizon_evidence"]["avg_return_advantage"] == 0.03
    assert [row["horizon_days"] for row in item["horizon_evidence"]["horizon_comparison"]] == [60, 90]
    assert item["horizon_evidence"]["top_feature_effects"][0]["feature_name"] == "ma_ratio_60"
    assert item["top_feature_values"][0]["name"] == "ret_20d"
    assert item["top_feature_values"][0]["model_value"] == 0.12


def test_model_comparison_uses_latest_gated_challenger(monkeypatch):
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
            feature_schema_version TEXT,
            n_features INTEGER,
            holdout_ic DOUBLE,
            holdout_rank_ic DOUBLE,
            holdout_top_decile_avg DOUBLE,
            holdout_long_short_spread DOUBLE,
            holdout_winrate_top DOUBLE,
            created_at TEXT,
            feature_cols_json TEXT
        );
        CREATE TABLE mart_tdx_keep_promotion_gate (
            gate_run_id TEXT PRIMARY KEY,
            challenger_model_id TEXT,
            champion_model_id TEXT,
            promotion_status TEXT,
            decision TEXT,
            gate_results_json TEXT,
            blockers_json TEXT,
            rank_ic_challenger DOUBLE,
            rank_ic_champion DOUBLE,
            long_short_challenger DOUBLE,
            long_short_champion DOUBLE,
            max_drawdown_challenger DOUBLE,
            max_drawdown_champion DOUBLE,
            evaluated_at TEXT
        );
        CREATE TABLE mart_daily_recommendation (
            snapshot_date TEXT,
            stock_code TEXT,
            model_id TEXT,
            run_mode TEXT
        );
        INSERT INTO mart_model_lifecycle VALUES
            ('champion_model', 'champion', TIMESTAMP '2026-05-01 09:00:00', TIMESTAMP '2026-05-01 09:00:00'),
            ('tdx_keep_challenger_old', 'challenger', NULL, TIMESTAMP '2026-05-02 09:00:00'),
            ('perf_base_dense_v2_new', 'challenger', NULL, TIMESTAMP '2026-05-03 09:00:00');
        INSERT INTO mart_multidim_model VALUES
            ('champion_model', 'base_dense_v2', 10, 0.01, 0.02, 0.03, 0.04, 0.50, '2026-05-01', '["base"]'),
            ('tdx_keep_challenger_old', 'tdx_keep', 12, 0.02, 0.03, 0.04, 0.05, 0.51, '2026-05-02', '["tdx"]'),
            ('perf_base_dense_v2_new', 'base_dense_v2', 11, 0.03, 0.04, 0.05, 0.06, 0.52, '2026-05-03', '["base"]');
        INSERT INTO mart_tdx_keep_promotion_gate VALUES
            ('gate_old', 'tdx_keep_challenger_old', 'champion_model', 'FAIL', 'reject',
             '{}', '[{"gate":"drift","status":"FAIL","reason":"old"}]',
             0.03, 0.02, 0.05, 0.04, NULL, NULL, '2026-05-02T09:00:00'),
            ('gate_new', 'perf_base_dense_v2_new', 'champion_model', 'FAIL', 'reject',
             '{}', '[{"gate":"coverage","status":"FAIL","reason":"missing coverage"}]',
             0.04, 0.02, 0.06, 0.04, NULL, NULL, '2026-05-03T09:00:00');
        INSERT INTO mart_daily_recommendation VALUES ('2026-05-04', '000001', 'perf_base_dense_v2_new', 'shadow');
        """
    )
    monkeypatch.setattr(recommendation, "get_conn", lambda: conn)

    response = TestClient(app).get("/api/rec/model-comparison")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["challenger"]["model_id"] == "perf_base_dense_v2_new"
    assert payload["promotion_gate"]["blockers"][0]["gate"] == "coverage"
    assert payload["shadow_topk"]["rows"] == 1
