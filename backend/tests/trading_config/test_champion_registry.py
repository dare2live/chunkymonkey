"""防回退: champion model_id 单一真相源 (yaml 声明 vs DB lifecycle 对账).

2026-06-11 体检 HIGH 修复防回退: champion 散在 daily_update.sh / SESSION_HANDOFF /
DB lifecycle 三处冲突. champion_registry.yaml 是治理声明, DB lifecycle 是运行时真相,
reader 对账不一致即 raise.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from services.trading_config.champion_registry import (
    ChampionRegistry,
    ChampionRegistryError,
    assert_consistent_with_db,
    get_db_champion_model_id,
    get_expected_champion_model_id,
    load_champion_registry,
)


def _write_yaml(tmp_path: Path, model_id: str, db_name: str = "fixture.duckdb") -> Path:
    p = tmp_path / "champion_registry.yaml"
    p.write_text(
        f"""schema_version: 1
expected:
  model_id: {model_id}
  promoted_at: "2026-05-07"
  source_commit: deadbeef
roles:
  f2_training_model:
    model_id: some_f2_model
db_truth_source:
  database: {db_name}
  table: mart_model_lifecycle
  status_value: champion
""",
        encoding="utf-8",
    )
    return p


def _write_lifecycle_db(tmp_path: Path, champion_id: str, db_name: str = "fixture.duckdb") -> Path:
    db = tmp_path / db_name
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE mart_model_lifecycle (model_id TEXT, status TEXT)")
    con.execute(
        "INSERT INTO mart_model_lifecycle VALUES (?, 'champion'), ('old_model', 'retired')",
        [champion_id],
    )
    con.close()
    return db


def test_real_yaml_loads_and_has_expected():
    """生产 champion_registry.yaml 可加载且有 expected.model_id."""
    reg = load_champion_registry()
    assert reg.expected_model_id
    assert get_expected_champion_model_id() == reg.expected_model_id


def test_reader_returns_db_champion(tmp_path):
    _write_yaml(tmp_path, "model_X")
    db = _write_lifecycle_db(tmp_path, "model_X")
    reg = load_champion_registry(tmp_path / "champion_registry.yaml")
    assert get_db_champion_model_id(reg, db_path=db) == "model_X"


def test_consistent_passes_when_matching(tmp_path):
    _write_yaml(tmp_path, "model_X")
    db = _write_lifecycle_db(tmp_path, "model_X")
    reg = load_champion_registry(tmp_path / "champion_registry.yaml")
    assert_consistent_with_db(reg, db_path=db)  # 不应 raise


def test_consistent_raises_on_drift(tmp_path):
    """yaml 声明 != DB lifecycle champion → raise (核心防回退)."""
    _write_yaml(tmp_path, "declared_model")
    db = _write_lifecycle_db(tmp_path, "actual_db_model")
    reg = load_champion_registry(tmp_path / "champion_registry.yaml")
    with pytest.raises(ChampionRegistryError, match="真相源分裂"):
        assert_consistent_with_db(reg, db_path=db)


def test_missing_db_raises_when_required(tmp_path):
    _write_yaml(tmp_path, "model_X")
    reg = load_champion_registry(tmp_path / "champion_registry.yaml")
    missing = tmp_path / "nonexistent.duckdb"
    with pytest.raises(ChampionRegistryError, match="无法对账"):
        assert_consistent_with_db(reg, db_path=missing, require_db=True)


def test_missing_db_skips_when_not_required(tmp_path):
    _write_yaml(tmp_path, "model_X")
    reg = load_champion_registry(tmp_path / "champion_registry.yaml")
    missing = tmp_path / "nonexistent.duckdb"
    assert_consistent_with_db(reg, db_path=missing, require_db=False)  # 不应 raise


def test_yaml_missing_expected_raises(tmp_path):
    p = tmp_path / "champion_registry.yaml"
    p.write_text("schema_version: 1\nexpected: {}\n", encoding="utf-8")
    with pytest.raises(ChampionRegistryError, match="model_id 缺失"):
        load_champion_registry(p)
