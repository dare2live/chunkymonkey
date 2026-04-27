"""orm: 从 fetch 结果反推 schema, 生成可入库 DDL.

设计目标:
- 输入: list[dict] 样本 (fetch_report 返回的 rows)
- 输出: DuckDB / SQLite / Postgres 兼容的 CREATE TABLE 语句
- 主键来自 ReportSpec.key, 索引来自 date_field

不做事:
- 不连数据库 (纯字符串生成)
- 不去重 / 不 upsert (调用方自己决定)
"""
from .type_infer import infer_column_type, infer_schema, ColumnType
from .ddl import generate_ddl, generate_ddl_for_report

__all__ = [
    "infer_column_type",
    "infer_schema",
    "ColumnType",
    "generate_ddl",
    "generate_ddl_for_report",
]
