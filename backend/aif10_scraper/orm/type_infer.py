"""字段类型推断.

策略:
1. 扫描所有 sample rows, 收集每字段的非空值
2. 按 Python 类型 + 字符串模式判断:
   - bool → BOOLEAN
   - int (所有非空都 int) → INTEGER / BIGINT (按数值范围)
   - float / int 混合 → DOUBLE
   - 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM:SS' 字符串 → TIMESTAMP
   - 其他字符串 → VARCHAR(N) / TEXT (按最大长度)
   - dict/list (嵌套) → JSON
3. 类型 promotion: 任何冲突向"更宽"靠拢 (int + float = DOUBLE, 长字符串退化 TEXT)

不做精确十进制 — eastmoney 浮点本身就是 IEEE754 字符串/数字, DOUBLE 够用.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal


ColumnType = Literal[
    "BOOLEAN",
    "INTEGER",
    "BIGINT",
    "DOUBLE",
    "DATE",
    "TIMESTAMP",
    "VARCHAR",     # 短字符串 (≤ 255)
    "TEXT",        # 长文本 (> 255 或不确定)
    "JSON",        # dict / list 嵌套
]


# 'YYYY-MM-DD' 或 'YYYY/MM/DD'
DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
# 'YYYY-MM-DD HH:MM:SS[.ms]' 或 ISO 'YYYY-MM-DDTHH:MM:SS'
TIMESTAMP_RE = re.compile(
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?$"
)
# 整数 (含负号)
INT_STR_RE = re.compile(r"^-?\d+$")
# 浮点
FLOAT_STR_RE = re.compile(r"^-?\d+\.\d+([eE][-+]?\d+)?$")


@dataclass
class ColumnInfo:
    """单字段类型分析结果."""
    name: str
    sql_type: ColumnType
    nullable: bool
    max_str_len: int = 0
    sample_count: int = 0
    null_count: int = 0
    notes: str = ""


def _classify_value(v: Any) -> ColumnType | None:
    """给一个非空值, 推断其最窄合理类型. None → None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "BOOLEAN"
    if isinstance(v, int):
        # INTEGER fits 32-bit signed, 否则 BIGINT
        if -2_147_483_648 <= v <= 2_147_483_647:
            return "INTEGER"
        return "BIGINT"
    if isinstance(v, float):
        return "DOUBLE"
    if isinstance(v, (dict, list)):
        return "JSON"
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None  # 视作 null
        if TIMESTAMP_RE.match(s):
            return "TIMESTAMP"
        if DATE_RE.match(s):
            return "DATE"
        # 数字字符串按字符串处理 (东财常把 SECUCODE / 股票代码当字符串, 别误判)
        return "VARCHAR" if len(s) <= 255 else "TEXT"
    # 兜底
    return "TEXT"


# 类型 promotion: 两个类型合并出更宽的
_PROMOTION_ORDER: list[ColumnType] = [
    "BOOLEAN", "INTEGER", "BIGINT", "DOUBLE",
    "DATE", "TIMESTAMP",
    "VARCHAR", "TEXT", "JSON",
]


def _promote(a: ColumnType, b: ColumnType) -> ColumnType:
    """两个类型合并: 数值类合并到 DOUBLE, 日期/字符串混合退化 TEXT."""
    if a == b:
        return a
    numeric = {"BOOLEAN", "INTEGER", "BIGINT", "DOUBLE"}
    if a in numeric and b in numeric:
        # 数值类: 优先 DOUBLE, 次之 BIGINT
        if "DOUBLE" in (a, b):
            return "DOUBLE"
        if "BIGINT" in (a, b):
            return "BIGINT"
        return "INTEGER"
    date_like = {"DATE", "TIMESTAMP"}
    if a in date_like and b in date_like:
        return "TIMESTAMP"  # 总是用 TIMESTAMP, 信息更全
    string_like = {"VARCHAR", "TEXT"}
    if a in string_like and b in string_like:
        return "TEXT"
    # 其他混合 (日期 + 字符串 / 数值 + 字符串) → TEXT
    if "JSON" in (a, b):
        return "JSON"
    return "TEXT"


def infer_column_type(values: Iterable[Any]) -> ColumnInfo:
    """对单字段所有样本值推断类型."""
    types: list[ColumnType] = []
    null_count = 0
    sample_count = 0
    max_len = 0
    name = ""
    for v in values:
        sample_count += 1
        if v is None or (isinstance(v, str) and v.strip() == ""):
            null_count += 1
            continue
        t = _classify_value(v)
        if t is None:
            null_count += 1
            continue
        types.append(t)
        if isinstance(v, str):
            max_len = max(max_len, len(v))

    if not types:
        # 全 null, 默认 TEXT
        return ColumnInfo(
            name=name, sql_type="TEXT", nullable=True,
            max_str_len=0, sample_count=sample_count, null_count=null_count,
            notes="全部样本为空, 默认 TEXT",
        )

    sql_type: ColumnType = types[0]
    for t in types[1:]:
        sql_type = _promote(sql_type, t)

    # 字符串长度调整: 如果当前 VARCHAR 但最长接近 255, 升级 TEXT
    if sql_type == "VARCHAR" and max_len > 255:
        sql_type = "TEXT"

    return ColumnInfo(
        name=name, sql_type=sql_type, nullable=(null_count > 0),
        max_str_len=max_len, sample_count=sample_count, null_count=null_count,
    )


def infer_schema(rows: list[dict]) -> dict[str, ColumnInfo]:
    """输入: list[dict] 样本; 输出: {字段名: ColumnInfo}.

    保持字段顺序以第一个 row 出现顺序为准, 后续 row 新增字段附加在末尾.
    """
    if not rows:
        return {}

    # 收集所有字段, 保持顺序
    field_order: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                field_order.append(k)

    schema: dict[str, ColumnInfo] = {}
    for field_name in field_order:
        values = [row.get(field_name) for row in rows]
        info = infer_column_type(values)
        info.name = field_name
        schema[field_name] = info
    return schema
