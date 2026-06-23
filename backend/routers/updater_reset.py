"""Reset helpers for updater-derived tables."""

from __future__ import annotations

from typing import Callable, Iterable, Sequence


ResetTable = tuple[str, str]


DERIVED_RESET_TABLES: list[ResetTable] = [
    ("events", "fact_institution_event"),
    ("current_rel", "mart_current_relationship"),
    ("profiles", "mart_institution_profile"),
    ("industry_stat", "mart_institution_industry_stat"),
    ("trends", "mart_stock_trend"),
    ("steps", "step_status"),
]


INDUSTRY_RESET_TABLES: list[ResetTable] = [
    ("setup_snapshots", "fact_setup_snapshot"),
    ("current_rel", "mart_current_relationship"),
    ("profiles", "mart_institution_profile"),
    ("industry_stat", "mart_institution_industry_stat"),
    ("trends", "mart_stock_trend"),
    ("sector_momentum", "mart_sector_momentum"),
    ("industry_context_latest", "dim_stock_industry_context_latest"),
    ("quality_latest", "dim_stock_quality_latest"),
    ("stage_latest", "dim_stock_stage_latest"),
    ("turtle_latest", "dim_stock_turtle_latest"),
    ("stock_archetype_fact", "fact_stock_archetype"),
    ("stock_archetype_latest", "dim_stock_archetype_latest"),
    ("steps", "step_status"),
]


PRESERVED_INDUSTRY_RESET_TABLES = [
    "fact_institution_event",
    "inst_holdings",
    "market_kline_daily",
    "raw_gpcw_financial",
    "fact_financial_derived",
]


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return bool(row)


def _existing_tables(conn, table_names: Sequence[str]) -> set[str]:
    if not table_names:
        return set()

    placeholders = ", ".join("?" for _ in table_names)
    rows = conn.execute(
        f"""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name IN ({placeholders})
        """,
        tuple(table_names),
    ).fetchall()
    return {row[0] for row in rows}


def _count_existing_tables(conn, tables: Sequence[ResetTable]) -> dict[str, int]:
    if not tables:
        return {}

    count_sql = "\nUNION ALL\n".join(
        f"SELECT '{key}' AS reset_key, COUNT(*) AS row_count FROM {_quote_identifier(table_name)}"
        for key, table_name in tables
    )
    rows = conn.execute(count_sql).fetchall()
    return {row[0]: int(row[1]) for row in rows}


def reset_tables(conn, tables: Iterable[ResetTable]) -> tuple[dict[str, int], list[str]]:
    reset_tables_list = list(tables)
    counts = {key: 0 for key, _ in reset_tables_list}
    table_names = [table_name for _, table_name in reset_tables_list]
    existing_table_names = _existing_tables(conn, table_names)
    existing_tables = [
        (key, table_name)
        for key, table_name in reset_tables_list
        if table_name in existing_table_names
    ]
    missing_tables = [
        table_name
        for _, table_name in reset_tables_list
        if table_name not in existing_table_names
    ]

    conn.execute("BEGIN TRANSACTION")
    try:
        counts.update(_count_existing_tables(conn, existing_tables))

        if existing_tables:
            conn.execute(";\n".join(f"DELETE FROM {_quote_identifier(table_name)}" for _, table_name in existing_tables))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts, missing_tables


def build_reset_derived_payload(conn) -> dict:
    counts, missing_tables = reset_tables(conn, DERIVED_RESET_TABLES)
    total = sum(counts.values())
    return {
        "ok": True,
        "message": f"已清空 {total} 条派生数据，请重新执行智能更新",
        "counts": counts,
        "missing_tables": missing_tables,
    }


def build_reset_derived_response(get_conn: Callable) -> dict:
    """Build `/update/reset-derived` response while owning the DB connection."""
    conn = get_conn(timeout=120)
    try:
        return build_reset_derived_payload(conn)
    finally:
        conn.close()


def build_reset_industry_payload(conn) -> dict:
    counts, missing_tables = reset_tables(conn, INDUSTRY_RESET_TABLES)
    total = sum(counts.values())
    return {
        "ok": True,
        "message": f"已清空 {total} 条行业相关派生/快照数据，请重新执行智能更新",
        "counts": counts,
        "missing_tables": missing_tables,
        "preserved_tables": list(PRESERVED_INDUSTRY_RESET_TABLES),
    }


def build_reset_industry_response(get_conn: Callable) -> dict:
    """Build `/update/reset-industry-derived` reset payload while owning the DB connection."""
    conn = get_conn(timeout=120)
    try:
        return build_reset_industry_payload(conn)
    finally:
        conn.close()
