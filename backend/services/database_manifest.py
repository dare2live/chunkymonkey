"""Database ownership and attachment manifest loader."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_ROOT.parent
_CONFIG_PATH = _BACKEND_ROOT / "config" / "database_manifest.yaml"

_READ_ONLY_MODES = {"read_only", "readonly", "ro"}
_READ_WRITE_MODES = {"read_write", "readwrite", "rw", "write"}


def _normalize_mode(value: Any, *, field_name: str) -> str:
    mode = str(value or "read_only").strip().lower().replace("-", "_")
    if mode in _READ_ONLY_MODES:
        return "read_only"
    if mode in _READ_WRITE_MODES:
        return "read_write"
    raise ValueError(f"invalid {field_name}: {value!r}")


def _as_tuple(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


@dataclass(frozen=True)
class DatabaseSpec:
    """Single database entry from database_manifest.yaml."""

    alias: str
    path: Optional[str] = None
    path_glob: Optional[str] = None
    domain: str = ""
    owner: str = ""
    role: str = ""
    default_open_mode: str = "read_only"
    default_attach_mode: str = "read_only"
    online: bool = True
    status: str = "active"
    retention_class: str = ""
    table_patterns: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def default_attach_read_only(self) -> bool:
        return self.default_attach_mode == "read_only"

    def resolve_path(self, repo_root: Path | None = None) -> Path:
        """Return the concrete DB path for single-file entries."""

        if not self.path:
            raise ValueError(f"database alias {self.alias!r} has no concrete path")
        root = repo_root or _REPO_ROOT
        path = Path(self.path)
        return path if path.is_absolute() else root / path

    def attach_spec(self, repo_root: Path | None = None) -> dict[str, Any]:
        return {
            "path": str(self.resolve_path(repo_root=repo_root)),
            "read_only": self.default_attach_read_only,
        }


@dataclass(frozen=True)
class DatabaseManifest:
    """Parsed database manifest with lookup helpers."""

    version: int
    repo_root: Path
    defaults: dict[str, Any]
    databases: dict[str, DatabaseSpec]

    def require(self, alias: str) -> DatabaseSpec:
        try:
            return self.databases[alias]
        except KeyError as exc:
            known = ", ".join(sorted(self.databases))
            raise KeyError(f"unknown database alias {alias!r}; known aliases: {known}") from exc

    def path_for(self, alias: str) -> Path:
        return self.require(alias).resolve_path(repo_root=self.repo_root)

    def attach_spec(self, alias: str) -> dict[str, Any]:
        return self.require(alias).attach_spec(repo_root=self.repo_root)

    def attach_map(self, *aliases: str) -> dict[str, dict[str, Any]]:
        return {alias: self.attach_spec(alias) for alias in aliases}


def _parse_database(alias: str, raw: dict[str, Any]) -> DatabaseSpec:
    if not alias:
        raise ValueError("database alias must be non-empty")
    if not raw.get("path") and not raw.get("path_glob"):
        raise ValueError(f"database alias {alias!r} must define path or path_glob")
    return DatabaseSpec(
        alias=alias,
        path=raw.get("path"),
        path_glob=raw.get("path_glob"),
        domain=str(raw.get("domain") or ""),
        owner=str(raw.get("owner") or ""),
        role=str(raw.get("role") or ""),
        default_open_mode=_normalize_mode(raw.get("default_open_mode"), field_name="default_open_mode"),
        default_attach_mode=_normalize_mode(raw.get("default_attach_mode"), field_name="default_attach_mode"),
        online=bool(raw.get("online", True)),
        status=str(raw.get("status") or "active"),
        retention_class=str(raw.get("retention_class") or ""),
        table_patterns=tuple(str(item) for item in _as_tuple(raw.get("table_patterns"))),
        notes=tuple(str(item) for item in _as_tuple(raw.get("notes"))),
    )


def load_database_manifest(
    path: Path | None = None,
    *,
    repo_root: Path | None = None,
) -> DatabaseManifest:
    """Load backend/config/database_manifest.yaml."""

    p = path or _CONFIG_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    databases = {
        alias: _parse_database(alias, spec or {})
        for alias, spec in (raw.get("databases") or {}).items()
    }
    return DatabaseManifest(
        version=int(raw.get("version") or 1),
        repo_root=repo_root or _REPO_ROOT,
        defaults=raw.get("defaults") or {},
        databases=databases,
    )


_CACHED: Optional[DatabaseManifest] = None


def get_database_manifest() -> DatabaseManifest:
    global _CACHED
    if _CACHED is None:
        _CACHED = load_database_manifest()
    return _CACHED


def reload_database_manifest() -> DatabaseManifest:
    global _CACHED
    _CACHED = None
    return get_database_manifest()
