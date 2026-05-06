from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import duck_mem
from scripts import build_architecture_inventory as subject


pytestmark = pytest.mark.pipeline


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _write(
        repo / "backend" / "main.py",
        """
from fastapi import FastAPI
from routers.workbench import router as workbench_router
app = FastAPI()
app.include_router(workbench_router, prefix="/api/workbench", tags=["workbench"])
@app.get("/api/direct")
def direct():
    return {}
""",
    )
    _write(
        repo / "backend" / "routers" / "workbench.py",
        """
from fastapi import APIRouter
router = APIRouter()
@router.get("/overview")
def overview():
    return {}
@router.get("/data-sources")
def sources():
    return {}
""",
    )
    _write(
        repo / "backend" / "services" / "runtime_patches.py",
        "def apply_runtime_patches():\n    return None\n",
    )
    _write(
        repo / "backend" / "services" / "source_policy.py",
        '''
from services.runtime_patches import apply_runtime_patches

SQL = """
SELECT *
FROM dim_trading_calendar
JOIN fact_feature_panel USING(date)
"""

DDL = """
CREATE TABLE IF NOT EXISTS mart_source_policy_check AS
SELECT * FROM dim_trading_calendar
"""
''',
    )
    _write(
        repo / "backend" / "scripts" / "run_optuna_model_stability_search.py",
        '''
from services.source_policy import SQL

def run(conn):
    conn.execute("INSERT INTO mart_model_stability_search_trial VALUES (1)")
''',
    )
    _write(
        repo / "assets" / "js" / "app.js",
        """
fetch('/api/workbench/data-sources');
apiCached('/api/rec/daily-topk?limit=20');
""",
    )
    _write(
        repo / "assets" / "js" / "old-panel-view.js",
        """
fetch('/api/legacy_status/sources');
""",
    )
    _write(repo / "assets" / "css" / "main.css", "body { margin: 0; }\n")
    _write(
        repo / "index.html",
        """
<section id="view-old-panel" data-legacy-surface="old-panel"></section>
<script>
  ['assets/js/app.js', 'assets/js/old-panel-view.js'].forEach(function (src) {});
</script>
""",
    )
    return repo


def test_scan_code_inventory_extracts_routes_tables_imports_and_calls(tmp_path):
    repo = _fake_repo(tmp_path)

    backend = subject.scan_backend_assets(repo)
    frontend = subject.scan_frontend_assets(repo)
    assets = {asset.path: asset for asset in backend.assets + frontend.assets}

    assert "GET /api/workbench/overview" in assets["backend/routers/workbench.py"].api_routes
    assert "GET /api/direct" in assets["backend/main.py"].api_routes
    assert assets["backend/services/source_policy.py"].read_tables == [
        "dim_trading_calendar",
        "fact_feature_panel",
    ]
    assert "mart_source_policy_check" in assets["backend/services/source_policy.py"].write_tables
    assert assets["backend/services/runtime_patches.py"].classification == "compatibility_shim"
    assert assets["backend/scripts/run_optuna_model_stability_search.py"].classification == "research"
    assert "/api/workbench/data-sources" in assets["assets/js/app.js"].frontend_api_calls
    assert assets["assets/js/old-panel-view.js"].classification == "compatibility_shim"
    assert assets["assets/js/old-panel-view.js"].notes == "legacy frontend surface isolated behind workbench replacement"

    import_edges = [edge for edge in backend.edges if edge.dependency_type == "python_import"]
    assert any(
        edge.source_asset_id == "code:backend/services/source_policy.py"
        and edge.target_asset_id == "code:backend/services/runtime_patches.py"
        for edge in import_edges
    )
    frontend_ref_edges = [edge for edge in frontend.edges if edge.dependency_type == "frontend_asset_ref"]
    assert any(
        edge.source_asset_id == "frontend:index.html"
        and edge.target_asset_id == "frontend:assets/js/old-panel-view.js"
        for edge in frontend_ref_edges
    )


def test_build_architecture_inventory_persists_tables_manifest_and_report(tmp_path):
    repo = _fake_repo(tmp_path)
    report = tmp_path / "architecture_inventory.md"

    with duck_mem() as conn:
        conn.execute("CREATE TABLE dim_trading_calendar (date DATE)")
        conn.execute("INSERT INTO dim_trading_calendar VALUES ('2026-05-06')")
        conn.execute("CREATE TABLE fact_feature_panel (stock_code TEXT, date DATE)")
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-05-06')")

        summary = subject.build_architecture_inventory(
            conn,
            repo=repo,
            run_id="architecture_inventory_unit",
            output_path=report,
        )

        assert summary["backend_asset_count"] >= 5
        assert summary["frontend_asset_count"] == 4
        assert summary["duckdb_asset_count"] >= 2
        assert summary["dependency_edge_count"] > 0
        assert report.exists()
        report_text = report.read_text(encoding="utf-8")
        assert "Chunky Monkey Architecture Inventory" in report_text
        assert "## Target Module Map" in report_text

        asset_row = conn.execute(
            """
            SELECT read_tables_json, current_call_paths_json
              FROM mart_architecture_inventory_asset
             WHERE run_id = 'architecture_inventory_unit'
               AND path = 'backend/services/source_policy.py'
            """
        ).fetchone()
        assert json.loads(asset_row["read_tables_json"]) == [
            "dim_trading_calendar",
            "fact_feature_panel",
        ]
        assert "code:backend/scripts/run_optuna_model_stability_search.py" in json.loads(
            asset_row["current_call_paths_json"]
        )
        shim_row = conn.execute(
            """
            SELECT blockers_json
              FROM mart_architecture_inventory_asset
             WHERE run_id = 'architecture_inventory_unit'
               AND path = 'backend/services/runtime_patches.py'
            """
        ).fetchone()
        assert "has active dependency from backend/services/source_policy.py" in json.loads(
            shim_row["blockers_json"]
        )
        legacy_frontend_row = conn.execute(
            """
            SELECT classification, blockers_json, notes
              FROM mart_architecture_inventory_asset
             WHERE run_id = 'architecture_inventory_unit'
               AND path = 'assets/js/old-panel-view.js'
            """
        ).fetchone()
        assert legacy_frontend_row["classification"] == "compatibility_shim"
        assert "has active dependency from index.html" in json.loads(
            legacy_frontend_row["blockers_json"]
        )
        assert legacy_frontend_row["notes"] == "legacy frontend surface isolated behind workbench replacement"

        manifest = conn.execute(
            """
            SELECT pipeline_name, status, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'architecture_inventory_unit'
            """
        ).fetchone()
        assert manifest["pipeline_name"] == "build_architecture_inventory"
        assert manifest["status"] == "success"
        assert json.loads(manifest["perf_summary_json"])["dependency_edge_count"] > 0

        version_rows = conn.execute(
            """
            SELECT table_name, actual_version
              FROM dim_schema_version
             WHERE table_name LIKE 'mart_architecture_%'
             ORDER BY table_name
            """
        ).fetchall()
        assert {row["table_name"]: row["actual_version"] for row in version_rows} == {
            "mart_architecture_dependency_edge": "v1",
            "mart_architecture_inventory_asset": "v1",
            "mart_architecture_inventory_summary": "v1",
        }


def test_test_references_do_not_block_cleanup_candidates(tmp_path):
    repo = tmp_path / "repo"
    _write(
        repo / "backend" / "services" / "runtime_patches.py",
        "def apply_runtime_patches():\n    return None\n",
    )
    _write(
        repo / "backend" / "tests" / "test_runtime_patches.py",
        "from services.runtime_patches import apply_runtime_patches\n",
    )

    with duck_mem() as conn:
        subject.build_architecture_inventory(
            conn,
            repo=repo,
            run_id="architecture_inventory_test_only_unit",
            output_path=None,
        )

        row = conn.execute(
            """
            SELECT current_call_paths_json, blockers_json
              FROM mart_architecture_inventory_asset
             WHERE run_id = 'architecture_inventory_test_only_unit'
               AND path = 'backend/services/runtime_patches.py'
            """
        ).fetchone()
        assert "code:backend/tests/test_runtime_patches.py" in json.loads(row["current_call_paths_json"])
        assert json.loads(row["blockers_json"]) == []


def test_data_deprecation_registry_is_not_classified_as_cleanup_candidate(tmp_path):
    repo = tmp_path / "repo"
    _write(
        repo / "backend" / "services" / "data_deprecation.py",
        'ASSETS = [{"table_name": "old_table", "replacement_table": "new_table", "reason": "deprecated"}]\n',
    )
    _write(
        repo / "backend" / "scripts" / "record_data_deprecations.py",
        "from services.data_deprecation import ASSETS\n",
    )

    assets = {asset.path: asset for asset in subject.scan_backend_assets(repo).assets}

    assert assets["backend/services/data_deprecation.py"].classification == "production"
    assert assets["backend/scripts/record_data_deprecations.py"].classification == "production"


def test_test_fixture_legacy_text_does_not_create_cleanup_candidate(tmp_path):
    repo = tmp_path / "repo"
    _write(
        repo / "backend" / "tests" / "test_frontend_contract.py",
        """
def test_contract():
    assert 'data-legacy-surface="data-health"' in '<section data-legacy-surface="data-health"></section>'
    assert 'replacement' in 'workbench replacement'
""",
    )

    assets = {asset.path: asset for asset in subject.scan_backend_assets(repo).assets}

    assert assets["backend/tests/test_frontend_contract.py"].classification == "production"
    assert assets["backend/tests/test_frontend_contract.py"].notes == (
        "test coverage path; fixture literals are not cleanup markers"
    )
