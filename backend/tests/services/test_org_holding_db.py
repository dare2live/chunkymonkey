from pathlib import Path

from services.database_manifest import load_database_manifest
from services.org_holding_db import ALIAS, org_holding_db_path


def test_org_holding_alias_resolves_own_file():
    manifest = load_database_manifest()
    repo_root = Path(__file__).resolve().parents[3]
    spec = manifest.require(ALIAS)
    assert spec.path == "data/org_holding.duckdb"
    assert spec.domain == "tier0_disclosure"
    assert spec.retention_class == "canonical_source_store"
    assert org_holding_db_path() == repo_root / "data" / "org_holding.duckdb"
    assert {
        "landing_miaoxiang_org_holding",
        "canonical_org_holding_detail_period",
        "raw_org_holding_aif10",
        "org_holding_source_probe",
    }.issubset(spec.table_patterns)
    # ingest_batch/accepted_partition 2026-09-18 cut_lineage_drift §2.1 移入顶层
    # shared_bookkeeping_tables — 不应再在这里留字面量副本 (同一参数只在一处定义)。
    assert "ingest_batch" not in spec.table_patterns
    assert "accepted_partition" not in spec.table_patterns
