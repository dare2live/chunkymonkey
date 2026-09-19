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
        "landing_tushare_margin",
        "canonical_margin_exchange_daily",
    }.issubset(spec.table_patterns)
    # ingest_batch/accepted_partition 2026-09-18 cut_lineage_drift §2.1 移入顶层
    # shared_bookkeeping_tables (运行时记账表, 每个在线库可选出现一份, 不再靠
    # table_patterns 字面量假装它只属于这一个库) —— 不应再在这里留字面量副本。
    assert "ingest_batch" not in spec.table_patterns
    assert "accepted_partition" not in spec.table_patterns
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


# ── shared_bookkeeping_tables (2026-09-18 cut_lineage_drift §2.1) ──────────────


def test_shared_bookkeeping_tables_parses_from_real_manifest():
    manifest = load_database_manifest()
    assert set(manifest.shared_bookkeeping_tables) == {
        "mart_data_deletion_record",
        "dim_schema_version",
        "accepted_partition",
        "ingest_batch",
    }


def test_shared_bookkeeping_tables_defaults_to_empty_when_key_absent(tmp_path):
    """未设这个新顶层键的 manifest 必须与新增前行为一致 (空 tuple, 不 raise)."""
    config_path = tmp_path / "database_manifest.yaml"
    config_path.write_text(
        """
version: 1
databases:
  primary:
    path: data/primary.duckdb
""",
        encoding="utf-8",
    )
    manifest = load_database_manifest(config_path, repo_root=tmp_path)
    assert manifest.shared_bookkeeping_tables == ()


def test_shared_bookkeeping_tables_rejects_non_list_shape(tmp_path):
    """typed 顶层键必须显式解析, 而不是"未知键静默忽略" —— 给错形状 (非 list) 必须
    loudly ValueError, 不能悄悄变成空名单 (CLAUDE.md #11: 未知键/悬空引用 fail-closed)。"""
    config_path = tmp_path / "database_manifest.yaml"
    config_path.write_text(
        """
version: 1
shared_bookkeeping_tables: not_a_list
databases:
  primary:
    path: data/primary.duckdb
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="shared_bookkeeping_tables"):
        load_database_manifest(config_path, repo_root=tmp_path)
