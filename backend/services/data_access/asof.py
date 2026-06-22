"""PIT asof 门 (不变量#1 PIT锚): 决策 t 只见 asof_col <= t。

读层是 asof 强制的**单一执行点** (consumer 禁自写 ann_date<= ; check_serve_read_layer D2 守)。
as_of 入参统一 ISO (决策日口径); 按 entity.asof_format 归一后再比较 (跨格式串比较无意义)。
"""
from __future__ import annotations

from .keys import to_iso, to_yyyymmdd
from .spec import EntitySpec


def normalize_cutoff(spec: EntitySpec, as_of_iso: str) -> str:
    """as_of (ISO 决策日) → entity.asof_col 的原生格式, 供 SQL <= 比较。"""
    if spec.asof_format == "yyyymmdd":
        return to_yyyymmdd(as_of_iso)
    return as_of_iso  # iso


def asof_clause(spec: EntitySpec, as_of_iso: str | None, start_iso: str | None) -> tuple[str, list]:
    """构造 PIT WHERE 片段 (asof_col <= cutoff [AND >= start])。返回 (sql_片段, params)。"""
    clauses: list[str] = []
    params: list = []
    col = f'"{spec.asof_col}"'    # 引号化防保留字
    if as_of_iso:
        clauses.append(f"{col} <= ?")
        params.append(normalize_cutoff(spec, as_of_iso))
    if start_iso:
        clauses.append(f"{col} >= ?")
        params.append(normalize_cutoff(spec, start_iso))
    return (" AND ".join(clauses), params)


def asof_to_iso(spec: EntitySpec, raw_value) -> str:
    """asof_col 原生值 → ISO (输出归一, 与决策日同口径可比)。"""
    return to_iso(raw_value) if spec.asof_format == "yyyymmdd" else str(raw_value)
