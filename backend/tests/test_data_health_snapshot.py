import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts.data_health_snapshot import compute_health_for_table
from services import workbench_read
from services.workbench_read import build_workbench_data_sources, build_workbench_storage


def test_raw_source_freshness_uses_writer_time_and_trading_calendar():
    conn = duck_mem()
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT, is_trading INTEGER)")
        conn.execute(
            """
            INSERT INTO dim_trading_calendar VALUES
            ('2026-04-28', 1), ('2026-04-29', 1), ('2026-04-30', 1),
            ('2026-05-01', 0), ('2026-05-04', 0), ('2026-05-05', 0)
            """
        )
        conn.execute(
            """
            CREATE TABLE raw_lhb_daily (
                trade_date TEXT,
                stock_code TEXT,
                ingested_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO raw_lhb_daily VALUES ('2026-04-28', '000001', TIMESTAMP '2026-04-28 21:19:11')"
        )

        health = compute_health_for_table(
            conn,
            {
                "table_name": "raw_lhb_daily",
                "layer": "raw",
                "writer_module": "services.lhb_client",
                "upstream_source": "aif10:RPT_DAILYBILLBOARD_DETAILSNEW",
                "expected_freshness": "t+1",
                "sla_hours": 48,
            },
            datetime(2026, 5, 4, 12, 0, 0),
        )

        assert health["severity"] == "green"
        assert health["freshness_hours"] == 48
        assert health["last_writer_at"].startswith("2026-04-28")
    finally:
        conn.close()


def test_on_demand_table_without_date_column_is_green():
    conn = duck_mem()
    try:
        conn.execute("CREATE TABLE research_cache (id INTEGER, value TEXT)")
        conn.execute("INSERT INTO research_cache VALUES (1, 'ok')")

        health = compute_health_for_table(
            conn,
            {
                "table_name": "research_cache",
                "layer": "research",
                "writer_module": None,
                "upstream_source": None,
                "expected_freshness": "on-demand",
                "sla_hours": 720,
            },
            datetime(2026, 5, 4, 12, 0, 0),
        )

        assert health["severity"] == "green"
        assert health["issue_summary"] is None
    finally:
        conn.close()


def test_event_fact_freshness_uses_writer_time():
    conn = duck_mem()
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT, is_trading INTEGER)")
        conn.execute(
            """
            INSERT INTO dim_trading_calendar VALUES
            ('2026-04-28', 1), ('2026-04-29', 1), ('2026-04-30', 1),
            ('2026-05-01', 0), ('2026-05-04', 0)
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_shareholder_trade (
                stock_code TEXT,
                change_date TEXT,
                fetched_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO fact_shareholder_trade VALUES ('000001', '20260420', '2026-04-30T08:15:28+00:00')"
        )

        health = compute_health_for_table(
            conn,
            {
                "table_name": "fact_shareholder_trade",
                "layer": "fact",
                "writer_module": "scripts/ingest_holders_tdxhub.py",
                "upstream_source": "tdxhub.holders",
                "expected_freshness": "event",
                "sla_hours": 48,
            },
            datetime(2026, 5, 4, 12, 0, 0),
        )

        assert health["severity"] == "green"
        assert health["freshness_hours"] == 0
        assert health["last_writer_at"].startswith("2026-04-30")
    finally:
        conn.close()



def test_workbench_source_health_excludes_derived_and_deprecated_assets():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE dim_data_asset (
                table_name TEXT,
                layer TEXT,
                purpose TEXT,
                writer_module TEXT,
                upstream_source TEXT,
                source_tier INTEGER,
                expected_freshness TEXT,
                sla_hours DOUBLE,
                deprecation_status TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_data_health (
                table_name TEXT,
                snapshot_at TEXT,
                row_count INTEGER,
                last_data_date TEXT,
                freshness_hours DOUBLE,
                freshness_ok BOOLEAN,
                severity TEXT,
                issue_summary TEXT,
                source_tier_dist TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO dim_data_asset VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("raw_lhb_daily", "raw", "lhb", "writer", "aif10:RPT_DAILYBILLBOARD_DETAILSNEW", 2, "daily", 24, "active"),
                ("fact_feature_panel", "fact", "features", "feature_builder", "derived: kline + fundamentals", 99, "daily", 24, "active"),
                ("legacy_hsgt", "raw", "legacy", "old_writer", "akshare:stale_hsgt", 3, "daily", 24, "deprecated"),
            ],
        )
        conn.executemany(
            "INSERT INTO mart_data_health VALUES (?, '2026-05-04T12:00:00', ?, NULL, ?, TRUE, ?, NULL, ?)",
            [
                ("raw_lhb_daily", 10, 0, "green", '{"2":10}'),
                ("fact_feature_panel", 10, 120, "red", '{"99":10}'),
                ("legacy_hsgt", 10, 999, "red", '{"3":10}'),
            ],
        )

        result = build_workbench_data_sources(conn)

        assert result["source_health"]["sources"] == [
            {
                "upstream_source": "aif10:RPT_DAILYBILLBOARD_DETAILSNEW",
                "source_tier": 2,
                "asset_count": 1,
                "total_rows": 10,
                "red_count": 0,
                "yellow_count": 0,
                "green_count": 1,
                "max_freshness_h": 0.0,
            }
        ]
        assert result["asset_health"]["summary"]["total"] == 3
    finally:
        try:
            conn.close()
        except Exception:
            pass


def test_workbench_storage_uses_retention_dry_run(monkeypatch):
    conn = duck_mem()
    try:
        policy = object()

        def fake_load_policy():
            return policy

        def fake_plan_storage_cleanup(seen_conn, seen_policy):
            assert seen_conn is conn
            assert seen_policy is policy
            return {
                "mode": "dry_run",
                "candidate_count": 3,
                "candidates": [
                    {
                        "kind": "candidate_feature_panel",
                        "table": "fact_feature_panel_candidate",
                        "key_column": "feature_set_id",
                        "key_value": "old_set",
                        "row_count": 1200,
                        "last_built_at": "2026-05-01T00:00:00",
                        "reason": "older than latest 3 feature_set_id values",
                    },
                    {
                        "kind": "model_prediction_rows",
                        "table": "mart_multidim_prediction",
                        "model_id": "retired_m",
                        "row_count": 34,
                        "reason": "model is not champion/challenger/shadow",
                    },
                    {
                        "kind": "model_file",
                        "path": "/tmp/retired_m.pkl",
                        "model_id": "retired_m",
                        "bytes": 4096,
                        "reason": "pkl model file is not protected by lifecycle status",
                    },
                ],
                "protected_model_ids": ["champion_m"],
                "protected_model_reasons": {"champion_m": ["lifecycle_status:champion"]},
                "active_optuna_study_count": 1,
                "active_optuna_study_artifacts": [{"path": "data/optuna/study.sqlite3", "bytes": 10}],
                "compaction": {"recommended": True, "estimated_large_delete_rows": 1200},
                "delete_policy": "verified_direct_delete_no_archive",
            }

        monkeypatch.setattr(workbench_read, "load_storage_retention_policy", fake_load_policy)
        monkeypatch.setattr(workbench_read, "plan_storage_cleanup", fake_plan_storage_cleanup)

        result = build_workbench_storage(conn)
        retention = result["retention"]

        assert retention["mode"] == "dry_run"
        assert retention["candidate_count"] == 3
        assert retention["protected_model_count"] == 1
        assert retention["active_optuna_study_count"] == 1
        assert retention["compaction"]["recommended"] is True
        assert retention["delete_policy"] == "verified_direct_delete_no_archive"
        assert [row["kind"] for row in retention["candidates"]] == [
            "candidate_feature_panel",
            "model_prediction_rows",
            "model_file",
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass
