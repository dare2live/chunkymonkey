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


def load_duckdb_connect_policy(path: Path | None = None) -> DuckdbConnectPolicy:
    raw_path = path or CONFIG_PATH
    raw = _load_yaml(raw_path)
    try:
        allowed_raw_connect_paths = _require_paths(raw, "allowed_raw_connect_paths", raw_path)
    except KeyError as exc:
        raise ValueError(f"{raw_path.name}: missing duckdb connect policy key {exc.args[0]}") from exc
    return DuckdbConnectPolicy(allowed_raw_connect_paths=allowed_raw_connect_paths)


DEFAULT_DUCKDB_CONNECT_POLICY = load_duckdb_connect_policy()
