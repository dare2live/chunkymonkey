from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import duck_mem
from services.duck_adapter import connect
from scripts import plan_architecture_cleanup as subject


pytestmark = pytest.mark.pipeline


def _seed_inventory(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE mart_architecture_inventory_summary (
            run_id TEXT,
            backend_asset_count INTEGER,
            frontend_asset_count INTEGER,
            duckdb_asset_count INTEGER,
            dependency_edge_count INTEGER,
            deletion_candidate_count INTEGER,
            classification_counts_json TEXT,
            module_counts_json TEXT,
            built_at TEXT
        );
        INSERT INTO mart_architecture_inventory_summary VALUES
            ('arch_unit', 1, 0, 3, 0, 1, '{}', '{}', '2026-05-06T00:00:00');

        CREATE TABLE mart_architecture_inventory_asset (
            run_id TEXT,
            asset_id TEXT,
            asset_type TEXT,
            path TEXT,
            module_area TEXT,
            classification TEXT,
            owner_module TEXT,
            current_call_paths_json TEXT,
            read_tables_json TEXT,
            write_tables_json TEXT,
            api_routes_json TEXT,
            frontend_api_calls_json TEXT,
            model_artifacts_json TEXT,
            blockers_json TEXT,
            notes TEXT,
            built_at TEXT
        );
        INSERT INTO mart_architecture_inventory_asset VALUES
            ('arch_unit', 'duckdb:main.v_l2_profile', 'duckdb_view', 'smartmoney.main.v_l2_profile',
             'storage_core', 'compatibility_shim', 'storage_core', '[]', '[]', '[]', '[]', '[]', '[]', '[]', 'view', '2026-05-06'),
            ('arch_unit', 'duckdb:main.mart_model_validation_fold', 'duckdb_view', 'smartmoney.main.mart_model_validation_fold',
             'model_research', 'compatibility_shim', 'model_research', '[]', '[]', '[]', '[]', '[]', '[]', '[]', 'view', '2026-05-06'),
            ('arch_unit', 'code:backend/services/runtime_patches.py', 'service', 'backend/services/runtime_patches.py',
             'other', 'compatibility_shim', 'other', '[]', '[]', '[]', '[]', '[]', '[]',
             '["has active dependency from backend/main.py"]', 'shim', '2026-05-06'),
            ('arch_unit', 'code:backend/scripts/cleanup_legacy_models.py', 'script', 'backend/scripts/cleanup_legacy_models.py',
             'feature_factory', 'deprecated_pending_cleanup', 'feature_factory', '[]', '[]', '[]', '[]', '[]', '[]',
             '[]', 'cleanup', '2026-05-06');
        """
    )


def test_architecture_cleanup_plan_classifies_safe_and_blocked_candidates():
    with duck_mem() as conn:
        _seed_inventory(conn)

        result = subject.plan_architecture_cleanup(conn, run_id="cleanup_unit", inventory_run_id="arch_unit")

        assert result["status_counts"] == {
            "blocked": 1,
            "manual_review": 1,
            "ready_for_copied_db_smoke": 2,
        }
        by_path = {row["path"]: row for row in result["candidates"]}
        assert by_path["smartmoney.main.v_l2_profile"]["action"] == "drop_view_in_copied_db_smoke"
        assert by_path["smartmoney.main.mart_model_validation_fold"]["status"] == "ready_for_copied_db_smoke"
        assert by_path["backend/services/runtime_patches.py"]["status"] == "blocked"
        assert by_path["backend/scripts/cleanup_legacy_models.py"]["status"] == "manual_review"

        persisted = conn.execute(
            """
            SELECT action, status
              FROM mart_architecture_cleanup_plan
             WHERE run_id = 'cleanup_unit'
               AND path = 'smartmoney.main.v_l2_profile'
            """
        ).fetchone()
        assert persisted["action"] == "drop_view_in_copied_db_smoke"
        assert persisted["status"] == "ready_for_copied_db_smoke"

        manifest = conn.execute(
            """
            SELECT pipeline_name, status, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'cleanup_unit'
            """
        ).fetchone()
        assert manifest["pipeline_name"] == "plan_architecture_cleanup"
        assert manifest["status"] == "success"
        assert json.loads(manifest["perf_summary_json"])["status_counts"]["blocked"] == 1

        version = conn.execute(
            """
            SELECT actual_version
              FROM dim_schema_version
             WHERE table_name = 'mart_architecture_cleanup_plan'
            """
        ).fetchone()
        assert version["actual_version"] == "v1"


def test_architecture_cleanup_plan_blocks_views_still_managed_by_schema_versions(monkeypatch):
    with duck_mem() as conn:
        _seed_inventory(conn)
        monkeypatch.setattr(subject, "_managed_recreate_views", lambda: {"mart_model_validation_fold"})

        result = subject.plan_architecture_cleanup(conn, run_id="cleanup_managed_view_unit", inventory_run_id="arch_unit")

        by_path = {row["path"]: row for row in result["candidates"]}
        assert by_path["smartmoney.main.mart_model_validation_fold"]["status"] == "blocked"
        assert "schema_versions.RECREATE_VIEWS" in by_path["smartmoney.main.mart_model_validation_fold"]["reason"]


def test_architecture_cleanup_plan_keeps_inventory_run_id_when_no_candidates():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_architecture_inventory_summary (
                run_id TEXT,
                backend_asset_count INTEGER,
                frontend_asset_count INTEGER,
                duckdb_asset_count INTEGER,
                dependency_edge_count INTEGER,
                deletion_candidate_count INTEGER,
                classification_counts_json TEXT,
                module_counts_json TEXT,
                built_at TEXT
            );
            CREATE TABLE mart_architecture_inventory_asset (
                run_id TEXT,
                asset_id TEXT,
                asset_type TEXT,
                path TEXT,
                module_area TEXT,
                classification TEXT,
                owner_module TEXT,
                current_call_paths_json TEXT,
                read_tables_json TEXT,
                write_tables_json TEXT,
                api_routes_json TEXT,
                frontend_api_calls_json TEXT,
                model_artifacts_json TEXT,
                blockers_json TEXT,
                notes TEXT,
                built_at TEXT
            );
            INSERT INTO mart_architecture_inventory_summary VALUES
                ('arch_empty', 1, 0, 0, 0, 0, '{}', '{}', '2026-05-06T00:00:00');
            INSERT INTO mart_architecture_inventory_asset VALUES
                ('arch_empty', 'code:backend/main.py', 'service', 'backend/main.py',
                 'api_workbench', 'production', 'api_workbench', '[]', '[]', '[]', '[]', '[]', '[]',
                 '[]', 'main', '2026-05-06');
            """
        )

        result = subject.plan_architecture_cleanup(conn, run_id="cleanup_empty_unit")

        assert result["inventory_run_id"] == "arch_empty"
        manifest = conn.execute(
            """
            SELECT perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'cleanup_empty_unit'
            """
        ).fetchone()
        assert json.loads(manifest["perf_summary_json"])["inventory_run_id"] == "arch_empty"


def test_architecture_cleanup_plan_smoke_drops_ready_views_only():
    with duck_mem() as conn:
        _seed_inventory(conn)
        conn.execute("CREATE VIEW v_l2_profile AS SELECT 1 AS ok")
        conn.execute("CREATE VIEW mart_model_validation_fold AS SELECT 1 AS ok")

        result = subject.plan_architecture_cleanup(
            conn,
            run_id="cleanup_smoke_unit",
            inventory_run_id="arch_unit",
            smoke_drop_views=True,
        )

        by_path = {row["path"]: row for row in result["candidates"]}
        assert by_path["smartmoney.main.v_l2_profile"]["status"] == "smoke_passed"
        assert by_path["smartmoney.main.v_l2_profile"]["smoke_status"] == "passed"
        assert by_path["smartmoney.main.mart_model_validation_fold"]["status"] == "smoke_passed"
        assert by_path["smartmoney.main.mart_model_validation_fold"]["smoke_status"] == "passed"
        assert subject._view_exists(conn, "v_l2_profile") is False
        assert subject._view_exists(conn, "mart_model_validation_fold") is False


def test_import_smoke_results_records_copied_db_evidence(tmp_path):
    smoke_db = tmp_path / "smoke.duckdb"
    smoke_conn = connect(str(smoke_db))
    try:
        _seed_inventory(smoke_conn)
        smoke_conn.execute("CREATE VIEW v_l2_profile AS SELECT 1 AS ok")
        smoke_conn.execute("CREATE VIEW mart_model_validation_fold AS SELECT 1 AS ok")
        subject.plan_architecture_cleanup(
            smoke_conn,
            run_id="cleanup_smoke_source",
            inventory_run_id="arch_unit",
            smoke_drop_views=True,
        )
    finally:
        smoke_conn.close()

    with duck_mem() as conn:
        result = subject.import_smoke_results(
            conn,
            smoke_db_path=Path(smoke_db),
            smoke_run_id="cleanup_smoke_source",
            run_id="cleanup_smoke_import",
        )

        assert result["status_counts"]["smoke_passed"] == 2
        assert result["smoke_counts"]["passed"] == 2
        row = conn.execute(
            """
            SELECT status, smoke_status
              FROM mart_architecture_cleanup_plan
             WHERE run_id = 'cleanup_smoke_import'
               AND path = 'smartmoney.main.v_l2_profile'
            """
        ).fetchone()
        assert row["status"] == "smoke_passed"
        assert row["smoke_status"] == "passed"
        manifest = conn.execute(
            """
            SELECT pipeline_name, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'cleanup_smoke_import'
            """
        ).fetchone()
        assert manifest["pipeline_name"] == "import_architecture_cleanup_smoke"
        assert json.loads(manifest["perf_summary_json"])["source_smoke_run_id"] == "cleanup_smoke_source"


def test_execute_approved_cleanup_drops_only_smoke_passed_views():
    with duck_mem() as conn:
        _seed_inventory(conn)
        conn.execute("CREATE VIEW v_l2_profile AS SELECT 1 AS ok")
        conn.execute("CREATE VIEW mart_model_validation_fold AS SELECT 1 AS ok")
        subject.plan_architecture_cleanup(
            conn,
            run_id="cleanup_smoke_source",
            inventory_run_id="arch_unit",
            smoke_drop_views=True,
        )
        conn.execute("CREATE VIEW v_l2_profile AS SELECT 1 AS ok")

        result = subject.execute_approved_cleanup(
            conn,
            source_run_id="cleanup_smoke_source",
            run_id="cleanup_execute_unit",
        )

        assert result["executed_count"] == 2
        assert result["status_counts"]["executed"] == 2
        assert subject._view_exists(conn, "v_l2_profile") is False
        assert subject._view_exists(conn, "mart_model_validation_fold") is False
        row = conn.execute(
            """
            SELECT status, reason
              FROM mart_architecture_cleanup_plan
             WHERE run_id = 'cleanup_execute_unit'
               AND path = 'smartmoney.main.v_l2_profile'
            """
        ).fetchone()
        assert row["status"] == "executed"
        assert "production" in row["reason"]
        manifest = conn.execute(
            """
            SELECT pipeline_name, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'cleanup_execute_unit'
            """
        ).fetchone()
        assert manifest["pipeline_name"] == "execute_architecture_cleanup"
        executed_views = {
            item["view_name"]
            for item in json.loads(manifest["perf_summary_json"])["executed"]
        }
        assert executed_views == {"v_l2_profile", "mart_model_validation_fold"}
