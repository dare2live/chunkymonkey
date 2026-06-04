"""Policy for approved raw ``duckdb.connect`` call sites."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "duckdb_connect_policy.yaml"


@dataclass(frozen=True)
class DuckdbConnectPolicy:
    allowed_raw_connect_paths: tuple[str, ...]
    block_data_duckdb_literals: bool = True
    database_manifest_path: str = "database_manifest.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def _require_paths(raw: dict[str, Any], key: str, raw_path: Path) -> tuple[str, ...]:
    value = raw[key]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{raw_path.name}: {key} must be a non-empty list")
    out: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, str) or not item.strip():
            raise ValueError(f"{raw_path.name}: {key}[{idx}] must be a non-empty string")
        path = item.strip()
        if path in seen:
            raise ValueError(f"{raw_path.name}: {key}[{idx}] duplicates {path}")
        seen.add(path)
        out.append(path)
    return tuple(out)


def _parse_db_path_literal_policy(raw: dict[str, Any], raw_path: Path) -> tuple[bool, str]:
    value = raw.get("db_path_literal_policy") or {}
    if not isinstance(value, dict):
        raise ValueError(f"{raw_path.name}: db_path_literal_policy must be a mapping")
    block_data_duckdb_literals = value.get("block_data_duckdb_literals", True)
    if not isinstance(block_data_duckdb_literals, bool):
        raise ValueError(f"{raw_path.name}: block_data_duckdb_literals must be a boolean")
    database_manifest_path = value.get("database_manifest_path", "database_manifest.yaml")
    if isinstance(database_manifest_path, bool) or not isinstance(database_manifest_path, str) or not database_manifest_path:
        raise ValueError(f"{raw_path.name}: database_manifest_path must be a non-empty string")
    return block_data_duckdb_literals, database_manifest_path


def load_duckdb_connect_policy(path: Path | None = None) -> DuckdbConnectPolicy:
    raw_path = path or CONFIG_PATH
    raw = _load_yaml(raw_path)
    try:
        allowed_raw_connect_paths = _require_paths(raw, "allowed_raw_connect_paths", raw_path)
    except KeyError as exc:
        raise ValueError(f"{raw_path.name}: missing duckdb connect policy key {exc.args[0]}") from exc
    block_data_duckdb_literals, database_manifest_path = _parse_db_path_literal_policy(raw, raw_path)
    return DuckdbConnectPolicy(
        allowed_raw_connect_paths=allowed_raw_connect_paths,
        block_data_duckdb_literals=block_data_duckdb_literals,
        database_manifest_path=database_manifest_path,
    )


DEFAULT_DUCKDB_CONNECT_POLICY = load_duckdb_connect_policy()
