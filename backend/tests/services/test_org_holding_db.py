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
        "ingest_batch",
        "accepted_partition",
    }.issubset(spec.table_patterns)
