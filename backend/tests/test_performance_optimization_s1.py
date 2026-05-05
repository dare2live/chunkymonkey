import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem  # noqa: E402
from services.ml_lifecycle.drift import (  # noqa: E402
    compute_psi_cached,
    ensure_drift_histogram_schema,
)
from scripts.build_feature_retention_decisions import build_feature_candidate_coverage  # noqa: E402
from scripts.run_daily_topk import DDL as TOPK_DDL, _xueqiu_symbol, write_topk_view_cache  # noqa: E402


def test_compute_psi_cached_exposes_reusable_histogram_state():
    train = list(range(100))
    recent = list(range(50, 150))

    psi, n_train, n_recent, edges, train_counts, recent_counts = compute_psi_cached(
        train,
        recent,
        n_bins=10,
    )

    assert n_train == 100
    assert n_recent == 100
    assert psi > 0
    assert len(edges) == len(train_counts) + 1
    assert len(train_counts) == len(recent_counts)


def test_drift_histogram_schema_can_be_created():
    conn = duck_mem()
    try:
        ensure_drift_histogram_schema(conn)
        conn.execute(
            """
            INSERT INTO mart_feature_drift_histogram
            VALUES ('m1', 'fact_feature_panel', 'f1', 'train', 'v1', 0, 0, 1, 10, 0, '2026-05-05')
            """
        )
        row = conn.execute("SELECT train_count FROM mart_feature_drift_histogram").fetchone()
        assert row["train_count"] == 10
    finally:
        conn.close()


def test_feature_candidate_coverage_marks_low_coverage_not_production_ready():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                forward_ret_20d DOUBLE,
                forecast_profit_yoy_mid DOUBLE,
                forecast_range_width DOUBLE
            )
            """
        )
        conn.execute("CREATE TABLE mart_feature_pit_audit (feature_set_id TEXT, feature_name TEXT, violation_rows INTEGER)")
        conn.execute(
            """
            INSERT INTO fact_feature_panel_candidate VALUES
            ('tdx_f10_gpcw_v1', '000001', '2026-01-01', 0.01, 1.0, NULL),
            ('tdx_f10_gpcw_v1', '000002', '2026-01-01', 0.02, 2.0, NULL),
            ('tdx_f10_gpcw_v1', '000003', '2026-01-01', 0.03, 3.0, 1.0),
            ('tdx_f10_gpcw_v1', '000004', '2026-01-01', 0.04, 4.0, NULL)
            """
        )

        coverage = build_feature_candidate_coverage(
            conn,
            feature_set_id="tdx_f10_gpcw_v1",
            audit_run_id="coverage_unit",
            min_coverage_pct=60.0,
        )

        assert coverage["forecast_profit_yoy_mid"]["production_ready"] is True
        assert coverage["forecast_range_width"]["production_ready"] is False
        assert coverage["forecast_range_width"]["coverage_pct"] == pytest.approx(25.0)
    finally:
        conn.close()


def test_write_topk_view_cache_materializes_identity_fields():
    conn = duck_mem()
    try:
        conn.executescript(TOPK_DDL)
        conn.execute("CREATE TABLE dim_active_a_stock (stock_code TEXT, stock_name TEXT)")
        conn.execute("CREATE TABLE mart_stock_trend (stock_code TEXT, stock_name TEXT)")
        conn.execute("CREATE TABLE fact_institution_event (stock_code TEXT, stock_name TEXT)")
        conn.execute("CREATE TABLE dim_stock_tdx_industry (stock_code TEXT, tdx_l1_name TEXT, tdx_l2_name TEXT)")
        conn.execute("INSERT INTO dim_active_a_stock VALUES ('000001', '平安银行')")
        conn.execute("INSERT INTO dim_stock_tdx_industry VALUES ('000001', '金融', '银行')")

        rows = [{
            "snapshot_date": "2026-05-05",
            "stock_code": "000001",
            "model_id": "m1",
            "rank_in_date": 1,
            "pred_score": 0.9,
            "percentile": 1.0,
            "regime_flag": "up",
            "key_features_json": "{}",
            "track_id": "primary",
            "is_primary": True,
            "run_mode": "champion",
            "built_at": "2026-05-05T00:00:00",
        }]

        assert write_topk_view_cache(conn, rows) == 1
        cached = conn.execute(
            "SELECT stock_name, xueqiu_symbol, tdx_l1_name FROM mart_daily_topk_view_cache"
        ).fetchone()
        assert cached["stock_name"] == "平安银行"
        assert cached["xueqiu_symbol"] == "SZ000001"
        assert cached["tdx_l1_name"] == "金融"
        assert _xueqiu_symbol("600000") == "SH600000"
    finally:
        conn.close()
