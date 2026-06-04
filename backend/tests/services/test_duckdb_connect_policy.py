from __future__ import annotations

from pathlib import Path

import pytest

from services.duckdb_connect_policy import load_duckdb_connect_policy


def _write_policy(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_duckdb_connect_policy_loads_db_literal_policy(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path / "duckdb_connect_policy.yaml",
        """
version: 1
db_path_literal_policy:
  block_data_duckdb_literals: false
  database_manifest_path: custom_manifest.yaml
allowed_raw_connect_paths:
  - services/duck_adapter.py
""",
    )

    policy = load_duckdb_connect_policy(policy_path)

    assert policy.allowed_raw_connect_paths == ("services/duck_adapter.py",)
    assert policy.block_data_duckdb_literals is False
    assert policy.database_manifest_path == "custom_manifest.yaml"


def test_duckdb_connect_policy_rejects_non_boolean_literal_switch(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path / "duckdb_connect_policy.yaml",
        """
version: 1
db_path_literal_policy:
  block_data_duckdb_literals: "yes"
allowed_raw_connect_paths:
  - services/duck_adapter.py
""",
    )

    with pytest.raises(ValueError, match="block_data_duckdb_literals"):
        load_duckdb_connect_policy(policy_path)
