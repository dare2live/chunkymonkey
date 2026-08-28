"""K1 serve-read identity views: typed column lists, not YAML-as-code.

``serve_read_views.yaml`` is the git DDL source. DuckDB views live in the
target database file (gitignored). Installer:
``backend/scripts/ensure_serve_read_views.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

SPECS_YAML = Path(__file__).resolve().parents[1] / "config" / "serve_read_views.yaml"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BASE_TABLE_TYPES = frozenset({"BASE TABLE", "TABLE"})


@dataclass(frozen=True)
class ColumnMap:
    """Projected view column. Identity when ``out == source``."""

    out: str
    source: str


@dataclass(frozen=True)
class ViewSpec:
    name: str
    db: str
    source_table: str
    entity: str
    columns: tuple[ColumnMap, ...]


def _ident(name: str) -> str:
    if not isinstance(name, str) or not _IDENT_RE.fullmatch(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return f'"{name}"'


def _qualify(name: str, schema: str | None) -> str:
    if schema and schema not in ("main",):
        return f"{_ident(schema)}.{_ident(name)}"
    return _ident(name)


def _parse_column(item: Any) -> ColumnMap:
    if isinstance(item, str):
        return ColumnMap(out=item, source=item)
    if isinstance(item, dict):
        out = item.get("out")
        source = item.get("source", out)
        if not isinstance(out, str) or not out:
            raise ValueError(f"column mapping missing out: {item!r}")
        if not isinstance(source, str) or not source:
            raise ValueError(f"column mapping missing source: {item!r}")
        return ColumnMap(out=out, source=source)
    raise ValueError(f"column must be a string or {{out, source}} mapping; got {item!r}")


def load_specs(path: str | Path | None = None) -> tuple[ViewSpec, ...]:
    p = Path(path) if path else SPECS_YAML
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"invalid serve_read_views.yaml root at {p}")
    if int(raw.get("version") or 0) != 1:
        raise ValueError(f"serve_read_views.yaml version must be 1 (got {raw.get('version')!r})")
    views = raw.get("views")
    if not isinstance(views, dict) or not views:
        raise ValueError("serve_read_views.yaml views: must be a non-empty mapping")
    out: list[ViewSpec] = []
    for name, meta in views.items():
        if not isinstance(name, str) or not _IDENT_RE.fullmatch(name):
            raise ValueError(f"invalid view name: {name!r}")
        if not isinstance(meta, dict):
            raise ValueError(f"view {name!r}: meta must be a mapping")
        db = meta.get("db")
        source_table = meta.get("source_table")
        entity = meta.get("entity")
        columns = meta.get("columns")
        if not isinstance(db, str) or not db:
            raise ValueError(f"view {name!r}: db must be a non-empty string")
        if not isinstance(source_table, str) or not _IDENT_RE.fullmatch(source_table):
            raise ValueError(f"view {name!r}: invalid source_table {source_table!r}")
        if not isinstance(entity, str) or not entity:
            raise ValueError(f"view {name!r}: entity must be a non-empty string")
        if not isinstance(columns, list) or not columns:
            raise ValueError(f"view {name!r}: columns must be a non-empty list")
        parsed = tuple(_parse_column(item) for item in columns)
        out.append(
            ViewSpec(
                name=name,
                db=db,
                source_table=source_table,
                entity=entity,
                columns=parsed,
            )
        )
    return tuple(out)


def view_ddl(spec: ViewSpec, *, schema: str | None = None) -> str:
    """CREATE OR REPLACE VIEW with an explicit column list (never SELECT *)."""
    proj: list[str] = []
    for col in spec.columns:
        if col.out == col.source:
            proj.append(_ident(col.out))
        else:
            proj.append(f"{_ident(col.source)} AS {_ident(col.out)}")
    view = _qualify(spec.name, schema)
    source = _qualify(spec.source_table, schema)
    return f"CREATE OR REPLACE VIEW {view} AS SELECT {', '.join(proj)} FROM {source}"


def _source_schema(conn, table: str) -> str | None:
    rows = conn.execute(
        """
        SELECT table_schema, table_type
        FROM information_schema.tables
        WHERE table_name = ?
        ORDER BY CASE table_schema WHEN 'main' THEN 0 WHEN 'tr' THEN 1 ELSE 2 END
        """,
        [table],
    ).fetchall()
    for row in rows:
        if str(row[1]).upper() in _BASE_TABLE_TYPES:
            return str(row[0])
    return None


def ensure_serve_read_views(conn, *, db: str | None = None) -> list[str]:
    """Install views whose source table exists on ``conn``.

    Skip (do not fail) when the source table is missing. Filter by ``db`` when
    given; ``db=None`` installs every spec that has a source on this connection
    (test mem DBs).
    """
    installed: list[str] = []
    for spec in load_specs():
        if db is not None and spec.db != db:
            continue
        schema = _source_schema(conn, spec.source_table)
        if schema is None:
            continue
        conn.execute(view_ddl(spec, schema=None if schema == "main" else schema))
        installed.append(spec.name)
    return installed


def iter_specs_for_db(db: str | None) -> Iterable[ViewSpec]:
    for spec in load_specs():
        if db is None or spec.db == db:
            yield spec
