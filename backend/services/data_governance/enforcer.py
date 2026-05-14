"""Phase ψ.γ.dict.2 — 字段字典 runtime enforce.

⚠ ETL/sync/mart 入库前调用 `enforce_dictionary(record, table_name)`,
    违反 → raise DictionaryViolation. 不 silent fallback (Rule 5).

设计:
  - V0 (本文件): 校验 pk/pit-key NOT NULL + enum + sign + outlier_cap + type 粗校.
    暂不校 unit (informational), 暂不校 跨表 PIT JOIN (那是 join_templates 的事).
  - V1 (后续): 加 unit normalize / cross-table consistency.

用法:
    from services.data_governance import enforce_dictionary, DictionaryViolation

    record = {"date": "2026-05-14", "stock_code": "600036", ...}
    enforce_dictionary(record, "fact_signal_context")    # raise if invalid

    # 批量 / 非 strict 模式收集 violations
    result = enforce_dictionary_batch(rows, "fact_signal_context", strict_per_row=False)
    # result["failed_rows"][0]["violations"] → list[str]
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Optional

from services.data_governance.config import (
    FieldDictionary,
    FieldSpec,
    TableSpec,
    get_field_dictionary,
)


class DictionaryViolation(ValueError):
    """字段字典违反, raise 不 silent (Rule 5)."""


# Roles that require NOT NULL
_NOT_NULL_ROLES = ("pk", "pit-key")


def _role_requires_not_null(role: Optional[str]) -> bool:
    if not role:
        return False
    return any(r in role for r in _NOT_NULL_ROLES)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _check_field(fname: str, value: Any, spec: FieldSpec) -> list[str]:
    """单字段校验. 返回 violations list (空 = pass)."""
    violations: list[str] = []

    is_null = value is None or _is_nan(value)
    if is_null:
        if _role_requires_not_null(spec.role):
            violations.append(f"{fname}: NULL violates role={spec.role}")
        return violations   # NULL 时不做后续校验

    # Type 粗校 (常用类型)
    expected = (spec.type or "").upper()
    if expected == "DOUBLE":
        if not _is_number(value):
            violations.append(f"{fname}: expected DOUBLE, got {type(value).__name__}")
    elif expected == "INTEGER":
        # bool 是 int 的 subclass, 但不算 INTEGER
        if not isinstance(value, int) or isinstance(value, bool):
            violations.append(f"{fname}: expected INTEGER, got {type(value).__name__}")
    elif expected == "TEXT":
        if not isinstance(value, str):
            violations.append(f"{fname}: expected TEXT, got {type(value).__name__}")
    elif expected == "BOOL":
        if not isinstance(value, bool):
            violations.append(f"{fname}: expected BOOL, got {type(value).__name__}")
    elif expected == "DATE":
        # 允许 str (YYYY-MM-DD) 或 has isoformat() (datetime.date / Timestamp)
        if isinstance(value, str):
            if len(value) < 10 or value[4] != "-":
                violations.append(f"{fname}: expected DATE string YYYY-MM-DD, got {value!r}")
        elif not hasattr(value, "isoformat"):
            violations.append(f"{fname}: expected DATE, got {type(value).__name__}")
    elif expected == "TIMESTAMP":
        if not isinstance(value, str) and not hasattr(value, "isoformat"):
            violations.append(f"{fname}: expected TIMESTAMP, got {type(value).__name__}")
    # JSON: 允许 dict / list / str (JSON string), 不强校
    # 未知 type: 不校 (兼容)

    # Enum
    if spec.enum is not None and value not in spec.enum:
        violations.append(
            f"{fname}: value={value!r} not in enum={list(spec.enum)}"
        )

    # Sign
    if spec.sign and _is_number(value):
        if spec.sign == "positive" and value < 0:
            violations.append(f"{fname}: value={value} violates sign=positive")
        elif spec.sign == "negative" and value > 0:
            violations.append(f"{fname}: value={value} violates sign=negative")

    # Outlier cap
    if spec.outlier_cap is not None and _is_number(value):
        cap = spec.outlier_cap
        if isinstance(cap, tuple) and len(cap) == 2:
            lo, hi = cap
            if value < lo or value > hi:
                violations.append(
                    f"{fname}: value={value} outside cap=[{lo}, {hi}]"
                )
        elif isinstance(cap, (int, float)):
            if abs(value) > cap:
                violations.append(f"{fname}: |value={value}| > cap={cap}")

    return violations


def enforce_dictionary(
    record: dict,
    table_name: str,
    *,
    strict: bool = True,
    skip_missing_table: bool = False,
    dictionary: Optional[FieldDictionary] = None,
) -> list[str]:
    """单行 record 入库前校验 (Phase ψ.γ.dict.2).

    Args:
        record: 待入库行.
        table_name: 'fact_signal_context' 或 'smartmoney.duckdb.fact_signal_context'.
        strict: True → 违反时 raise DictionaryViolation. False → 返回 violations list.
        skip_missing_table: True → 表不在字典时 pass (兼容期, 不强制全表入字典).
                            False → raise DictionaryViolation (强制 Rule 9.5).
        dictionary: 单测注入. 默认 get_field_dictionary().

    Returns:
        Violations list (strict=False 时返回; strict=True 时 pass 返回空 list).

    Raises:
        DictionaryViolation: strict=True 且有违反 / 表不在字典 (skip_missing_table=False).
    """
    fd = dictionary or get_field_dictionary()
    table_spec = fd.lookup_table(table_name)

    if table_spec is None:
        if skip_missing_table:
            return []
        raise DictionaryViolation(
            f"Table '{table_name}' 不在 field_dictionary.yaml. "
            f"新表必须先入字典 (Rule 9.5). 临时绕过传 skip_missing_table=True."
        )

    violations: list[str] = []
    for fname, spec in table_spec.fields.items():
        violations.extend(_check_field(fname, record.get(fname), spec))

    if strict and violations:
        raise DictionaryViolation(
            f"Table '{table_spec.full_name}': " + "; ".join(violations)
        )
    return violations


def enforce_dictionary_batch(
    records: Iterable[dict],
    table_name: str,
    *,
    strict_per_row: bool = False,
    skip_missing_table: bool = False,
    dictionary: Optional[FieldDictionary] = None,
) -> dict:
    """批量校验. 默认 strict_per_row=False 收集所有 violations 不 raise.

    Args:
        records: 待入库行 iterable.
        table_name: 表名.
        strict_per_row: True → 任一行违反立即 raise. False → 收集 violations 返回.
        skip_missing_table: 表不在字典时 pass (跟单行版相同语义).

    Returns:
        {
            "total":      int,
            "passed":     int,
            "failed":     int,
            "failed_rows": [{"row_idx": int, "violations": list[str], "sample": dict}],
        }
    """
    fd = dictionary or get_field_dictionary()
    rows = list(records)
    total = len(rows)
    failed_rows = []

    for idx, row in enumerate(rows):
        violations = enforce_dictionary(
            row, table_name,
            strict=strict_per_row,
            skip_missing_table=skip_missing_table,
            dictionary=fd,
        )
        if violations:
            failed_rows.append({
                "row_idx": idx,
                "violations": violations,
                "sample": {k: row.get(k) for k in list(row.keys())[:6]},   # 头 6 字段做诊断
            })

    return {
        "total": total,
        "passed": total - len(failed_rows),
        "failed": len(failed_rows),
        "failed_rows": failed_rows,
    }
