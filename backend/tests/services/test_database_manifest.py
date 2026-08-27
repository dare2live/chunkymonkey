from pathlib import Path

import pytest

from services.database_manifest import load_database_manifest


def test_database_manifest_resolves_repo_relative_paths():
    manifest = load_database_manifest()
    repo_root = Path(__file__).resolve().parents[3]

    assert manifest.path_for("smartmoney") == repo_root / "data" / "smartmoney.duckdb"
    assert manifest.path_for("market") == repo_root / "data" / "market.duckdb"
    assert manifest.path_for("org_holding") == repo_root / "data" / "org_holding.duckdb"
    assert manifest.require("market").default_attach_read_only is True


def test_tushare_store_manifest_uses_durable_tier0_boundary():
    spec = load_database_manifest().require("tushare_raw")

    assert spec.domain == "tier0_market_data"
    assert spec.owner == "tier0.market_data"
    assert spec.retention_class == "canonical_source_store"
    assert {
        "raw_tushare_*",
        "ingest_batch",
        "landing_tushare_margin",
        "canonical_margin_exchange_daily",
        "accepted_partition",
    }.issubset(spec.table_patterns)
    assert any("sync_runner" in note for note in spec.notes)
    assert any("permanent evidence" in note for note in spec.notes)


def test_database_manifest_builds_read_only_attach_map(tmp_path):
    config_path = tmp_path / "database_manifest.yaml"
    config_path.write_text(
        """
version: 1
databases:
  primary:
    path: data/primary.duckdb
    default_attach_mode: read_write
  market:
    path: data/market.duckdb
    default_attach_mode: read_only
""",
        encoding="utf-8",
    )

    manifest = load_database_manifest(config_path, repo_root=tmp_path)

    assert manifest.path_for("primary") == tmp_path / "data" / "primary.duckdb"
    assert manifest.attach_map("market") == {
        "market": {"path": str(tmp_path / "data" / "market.duckdb"), "read_only": True}
    }


def test_database_manifest_rejects_unknown_attach_modes(tmp_path):
    config_path = tmp_path / "database_manifest.yaml"
    config_path.write_text(
        """
version: 1
databases:
  bad:
    path: data/bad.duckdb
    default_attach_mode: sometimes
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="default_attach_mode"):
        load_database_manifest(config_path, repo_root=tmp_path)
