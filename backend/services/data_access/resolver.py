"""库路由 + read_only 连接 + schema 自校验 (不变量#2 读写边界: reader 永远 read_only)。

db 别名 → database_manifest 路由 (单一真相源, 不 hardcode .duckdb)。
preflight: entity.table/columns/asof_col 真去 schema 核对, 漂移=raise (data_access_preflight 门,
防 dim_all_ever_listed 式失真: config 声称的列/表与物理库脱节而静默出错)。
"""
from __future__ import annotations

from functools import lru_cache

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

from .spec import EntitySpec

_MANIFEST = get_database_manifest()


def db_path(alias: str) -> str:
    """db 别名 → 物理路径 (manifest 路由)。"""
    return str(_MANIFEST.path_for(alias))


def connect_ro(alias: str):
    """读层连接铁律: read_only=True (writer 独占, reader 永不写)。"""
    return duck_connect(db_path(alias), read_only=True)


@lru_cache(maxsize=256)
def _table_columns(db_path_str: str, table: str) -> frozenset[str]:
    c = duck_connect(db_path_str, read_only=True)
    try:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info('{table}')").fetchall()]
    finally:
        c.close()
    return frozenset(cols)


def preflight(spec: EntitySpec, conn=None) -> None:
    """schema 自校验: table 存在 + 声明 columns/asof_col 真在表里。漂移=ValueError。"""
    if conn is not None:
        cols = frozenset(r[1] for r in conn.execute(f"PRAGMA table_info('{spec.table}')").fetchall())
    else:
        cols = _table_columns(db_path(spec.db), spec.table)
    if not cols:
        raise ValueError(f"data_access preflight: entity {spec.name!r} 表 {spec.db}.{spec.table} 不存在或空 schema")
    declared = set(spec.columns) | {spec.code_col, spec.asof_col}
    missing = declared - cols
    if missing:
        raise ValueError(
            f"data_access preflight: entity {spec.name!r} 声明列 {sorted(missing)} 不在 {spec.db}.{spec.table} "
            f"(schema 漂移; 实有 {sorted(cols)[:20]}...)"
        )
