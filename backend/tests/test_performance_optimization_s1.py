import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem  # noqa: E402
from services.ml_lifecycle.drift import (  # noqa: E402
    _histogram_counts_sql,
    compute_psi_cached,
    ensure_drift_histogram_schema,
)
from scripts.build_feature_retention_decisions import build_feature_candidate_coverage  # noqa: E402
from scripts.run_daily_topk import DDL as TOPK_DDL, _xueqiu_symbol, write_topk_view_cache  # noqa: E402
from services.source_watermarks import (  # noqa: E402
    ensure_source_watermark_schema,
    list_source_failures,
    record_source_failure,
    resolve_source_failures,
)
from services.pipeline_lock import (  # noqa: E402
    PipelineLockError,
    acquire_pipeline_lock,
    get_pipeline_lock,
    heartbeat_pipeline_lock,
    release_pipeline_lock,
)
from services.feature_retention import load_production_keep_features  # noqa: E402
from services.model_feature_schema import BASE_FEATURE_COLS  # noqa: E402
from routers.updater import _critical_daily_plan, _plan_with_budgets  # noqa: E402
from scripts.train_multidim_model import resolve_feature_group_from_columns  # noqa: E402


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


def test_histogram_counts_sql_matches_python_bucket_semantics():
    conn = duck_mem()
    try:
        conn.execute("CREATE TABLE feature_panel (date TEXT, f DOUBLE)")
        conn.executemany(
            "INSERT INTO feature_panel VALUES (?, ?)",
            [
                ("2026-04-01", -1.0),
                ("2026-04-30", 0.0),
                ("2026-05-01", 0.5),
                ("2026-05-02", 1.0),
                ("2026-05-03", 2.0),
                ("2026-05-04", 3.0),
            ],
        )

        counts, n_recent = _histogram_counts_sql(
            conn,
            feature_table="feature_panel",
            date_col="date",
            feature="f",
            edges=[0.0, 1.0, 2.0],
            recent_window_days=30,
        )

        assert counts == [2, 2]
        assert n_recent >= 5
    finally:
        conn.close()


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


def test_source_failure_queue_records_and_resolves_failures():
    conn = duck_mem()
    try:
        ensure_source_watermark_schema(conn)
        failure_id = record_source_failure(
            conn,
            data_domain="financial_gpcw_8q",
            source_name="tdxhub_gpcw",
            source_tier=1,
            stock_code="000001",
            error_type="unit_error",
            last_error="boom",
        )
        open_rows = list_source_failures(conn)
        assert open_rows[0]["failure_id"] == failure_id
        assert open_rows[0]["occurrence_count"] == 1

        record_source_failure(
            conn,
            data_domain="financial_gpcw_8q",
            source_name="tdxhub_gpcw",
            source_tier=1,
            stock_code="000001",
            error_type="unit_error",
            last_error="boom again",
        )
        open_rows = list_source_failures(conn)
        assert open_rows[0]["occurrence_count"] == 2

        resolve_source_failures(conn, data_domain="financial_gpcw_8q", source_name="tdxhub_gpcw", stock_code="000001")
        assert list_source_failures(conn) == []
    finally:
        conn.close()


def test_smart_plan_budget_annotation_is_explicit():
    plan = _plan_with_budgets({"steps": ["sync_financial", "build_stage_features"], "reason": []})

    assert plan["budgets"]["sync_financial"] == 45
    assert plan["budgets"]["build_stage_features"] == 45
    assert plan["estimated_budget_s"] == 90


def test_critical_daily_plan_filters_noncritical_dashboard_steps():
    plan = _critical_daily_plan({
        "steps": ["sync_financial", "calc_financial_derived", "build_stage_features", "calc_stock_scores"],
        "reason": ["unit"],
    })

    assert plan["steps"] == ["sync_financial"]
    assert plan["budgets"] == {"sync_financial": 45}
    assert plan["estimated_budget_s"] == 45
    assert "calc_financial_derived" in plan["critical_only_removed_steps"]
    assert "calc_stock_scores" in plan["skip_reasons"]


def test_pipeline_lock_blocks_active_holder_and_releases_stale_lock():
    conn = duck_mem()
    try:
        first = acquire_pipeline_lock(
            conn,
            lock_name="cron_daily",
            owner_run_id="run-1",
            phase="sync",
            stale_after_s=1,
        )
        assert first["owner_run_id"] == "run-1"

        with pytest.raises(PipelineLockError):
            acquire_pipeline_lock(conn, lock_name="cron_daily", owner_run_id="run-2", phase="sync", stale_after_s=1)

        conn.execute("UPDATE mart_pipeline_lock SET heartbeat_at = '1970-01-01T00:00:00' WHERE lock_name = 'cron_daily'")
        second = acquire_pipeline_lock(
            conn,
            lock_name="cron_daily",
            owner_run_id="run-2",
            phase="watermarks",
            stale_after_s=1,
        )
        assert second["stale_released_previous"]

        heartbeat_pipeline_lock(conn, lock_name="cron_daily", owner_run_id="run-2", phase="topk")
        current = get_pipeline_lock(conn, lock_name="cron_daily")
        assert current["owner_run_id"] == "run-2"
        assert current["phase"] == "topk"

        release_pipeline_lock(conn, lock_name="cron_daily", owner_run_id="run-2", status="released_success")
        current = get_pipeline_lock(conn, lock_name="cron_daily")
        assert current["status"] == "released_success"
        assert current["released_at"] is not None
    finally:
        conn.close()


def test_base_retention_keep_uses_coverage_gated_keep_features_only():
    keep_features = ["forecast_profit_yoy_mid", "forecast_range_width"]
    panel_cols = set(BASE_FEATURE_COLS + keep_features + ["ret_20d_rank"])

    cols, tag = resolve_feature_group_from_columns(
        "base_retention_keep",
        panel_cols,
        regime_aware=False,
        retention_keep_features=keep_features,
        retention_schema_tag="retention_keep_unit",
    )

    assert tag == "retention_keep_unit"
    assert cols == BASE_FEATURE_COLS + keep_features
    assert "ret_20d_rank" not in cols


def test_load_production_keep_features_reads_latest_decision_run():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE mart_feature_retention_decision (
                decision_run_id TEXT,
                feature_set_id TEXT,
                feature_name TEXT,
                feature_group TEXT,
                decision TEXT,
                built_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO mart_feature_retention_decision VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("old_run", "tdx_f10_gpcw_v1", "old_feature", "tdx", "keep", "2026-05-04T00:00:00"),
                ("new_run", "tdx_f10_gpcw_v1", "forecast_range_width", "tdx", "keep", "2026-05-05T00:00:00"),
                ("new_run", "tdx_f10_gpcw_v1", "qfii_shares_qoq", "tdx", "drop", "2026-05-05T00:00:00"),
            ],
        )

        features, run_id = load_production_keep_features(conn, feature_set_id="tdx_f10_gpcw_v1")

        assert run_id == "new_run"
        assert features == ["forecast_range_width"]
    finally:
        conn.close()
