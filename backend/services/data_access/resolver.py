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


def dim_read_conn(conn, table: str):
    """§9 dim 读路由 (通用): conn 有 table (测试 fixture / 过渡期 smartmoney dual 副本) → 用它;
    否则开 reference RO (Stage E 物删 smartmoney 副本后, 全 reader 原子 fall reference)。返 (conn, own_flag)。

    dim 表 (active/calendar/...) 迁 reference 库的统一读路 (security_master/calendar 共用)。
    """
    if conn is not None:
        try:
            has = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1", [table]
            ).fetchone()
        except Exception:
            has = None
        if has:
            return conn, False
    return connect_ro("reference"), True


def connect_rw(alias: str):
    """写侧连接 (限 dim 真相源 writer, 如 reference universe/calendar 刷新)。

    §9 reference 拆库 (2026-06-27): dim 表迁 reference 后, 其 writer (refresh_active_a_stock_master /
    build_dim_listing_status / calendar sync) 需写 reference RW。read 侧仍 connect_ro (不变量#2 读写分离:
    reader 永不写, 但 dim writer 是显式写侧, 用本函数路由到 reference 库 RW 句柄)。
    """
    return duck_connect(db_path(alias), read_only=False)


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
