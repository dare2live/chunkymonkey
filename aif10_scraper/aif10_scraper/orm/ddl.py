"""DDL 生成: 把 ReportSpec + 样本 rows 翻译成 CREATE TABLE.

输出三种风格:
- duckdb (默认): JSON 类型原生支持, IF NOT EXISTS
- sqlite: 退化, JSON 转 TEXT, 没有 BOOLEAN (用 INTEGER), TIMESTAMP 转 TEXT
- postgres: 标准, JSONB
"""
from __future__ import annotations

from typing import Literal

from ..registry import ReportSpec, get_report
from .type_infer import ColumnInfo, infer_schema, ColumnType


Dialect = Literal["duckdb", "sqlite", "postgres"]


# 各方言的类型 mapping
_TYPE_MAP: dict[Dialect, dict[ColumnType, str]] = {
    "duckdb": {
        "BOOLEAN": "BOOLEAN",
        "INTEGER": "INTEGER",
        "BIGINT": "BIGINT",
        "DOUBLE": "DOUBLE",
        "DATE": "DATE",
        "TIMESTAMP": "TIMESTAMP",
        "VARCHAR": "VARCHAR",
        "TEXT": "VARCHAR",
        "JSON": "JSON",
    },
    "sqlite": {
        "BOOLEAN": "INTEGER",
        "INTEGER": "INTEGER",
        "BIGINT": "INTEGER",
        "DOUBLE": "REAL",
        "DATE": "TEXT",
        "TIMESTAMP": "TEXT",
        "VARCHAR": "TEXT",
        "TEXT": "TEXT",
        "JSON": "TEXT",
    },
    "postgres": {
        "BOOLEAN": "BOOLEAN",
        "INTEGER": "INTEGER",
        "BIGINT": "BIGINT",
        "DOUBLE": "DOUBLE PRECISION",
        "DATE": "DATE",
        "TIMESTAMP": "TIMESTAMP",
        "VARCHAR": "VARCHAR(255)",
        "TEXT": "TEXT",
        "JSON": "JSONB",
    },
}


def _table_name(report_name: str) -> str:
    """RPT_XXX → rpt_xxx (小写, 数据库友好)."""
    return report_name.lower()


def _column_def(info: ColumnInfo, dialect: Dialect) -> str:
    """单字段 DDL 行. notes 单独一行 (避免与 comma 冲突)."""
    sql_t = _TYPE_MAP[dialect][info.sql_type]
    null_clause = "" if info.nullable else " NOT NULL"
    line = f"  {info.name} {sql_t}{null_clause}"
    if info.notes:
        # 注意: SQL 注释占整行, 必须在列定义前. 不能在 comma 之前.
        line = f"  -- {info.name}: {info.notes}\n{line}"
    return line


def generate_ddl(
    report_name: str,
    rows: list[dict],
    *,
    spec: ReportSpec | None = None,
    dialect: Dialect = "duckdb",
    table_name: str | None = None,
    if_not_exists: bool = True,
    create_indexes: bool = True,
) -> str:
    """根据样本 rows 生成 CREATE TABLE.

    Args:
        report_name: reportName (用于注释 + spec 查询)
        rows: 样本数据, 越多越准 (推荐 ≥1 page = 500 行)
        spec: 可选 — 若不传则尝试从 registry 查
        dialect: duckdb / sqlite / postgres
        table_name: 表名 (默认 report_name 小写)
        if_not_exists: 加 IF NOT EXISTS
        create_indexes: 同时输出 date_field 索引

    Returns:
        SQL 字符串 (可能含多条语句, 用 ; 分隔)
    """
    if spec is None:
        try:
            spec = get_report(report_name)
        except KeyError:
            spec = None

    if not rows:
        return f"-- {report_name}: 样本为空, 无法推断 schema\n"

    schema = infer_schema(rows)
    tname = table_name or _table_name(report_name)
    pk_fields = list(spec.key) if spec else []

    # 如果有 PK, 那些字段必须 NOT NULL
    for k in pk_fields:
        if k in schema:
            schema[k].nullable = False

    lines = []
    lines.append(f"-- ============================================================")
    lines.append(f"-- {report_name}")
    if spec:
        lines.append(f"-- {spec.module}/{spec.subname} ({spec.frequency})")
        if spec.notes:
            lines.append(f"-- {spec.notes}")
    lines.append(f"-- 样本行数: {len(rows)}, 字段数: {len(schema)}")
    lines.append(f"-- ============================================================")
    lines.append("")

    ine = "IF NOT EXISTS " if if_not_exists else ""
    lines.append(f"CREATE TABLE {ine}{tname} (")

    col_lines = [_column_def(info, dialect) for info in schema.values()]
    if pk_fields:
        # PK 子句
        valid_pks = [k for k in pk_fields if k in schema]
        if valid_pks:
            col_lines.append(f"  PRIMARY KEY ({', '.join(valid_pks)})")
    lines.append(",\n".join(col_lines))
    lines.append(");")

    if create_indexes and spec and spec.date_field and spec.date_field in schema:
        idx_name = f"idx_{tname}_{spec.date_field.lower()}"
        lines.append("")
        lines.append(
            f"CREATE INDEX {ine}{idx_name} ON {tname}({spec.date_field});"
        )

    lines.append("")
    return "\n".join(lines)


def generate_ddl_for_report(
    report_name: str,
    *,
    sample_pages: int = 1,
    page_size: int = 500,
    secucode: str | None = None,
    dialect: Dialect = "duckdb",
    client=None,
) -> str:
    """一站式: 拉一页样本 + 生成 DDL.

    适合命令行快速用. 量大用 generate_ddl + 自己拉数据.
    """
    from ..batch import fetch_all_pages
    rows = fetch_all_pages(
        report_name,
        secucode=secucode,
        page_size=page_size,
        max_pages=sample_pages,
        client=client,
    )
    return generate_ddl(report_name, rows, dialect=dialect)
