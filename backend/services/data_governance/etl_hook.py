"""Phase ψ.γ.dict.2 — ETL INSERT 前字典 enforce hook.

⚠ 用法 (在任何 backfill_*/build_* 脚本 INSERT 前):

    from services.data_governance.etl_hook import validate_rows_before_insert

    COLUMNS = ["stock_code", "calc_date", "vol_30d", ...]   # 跟 INSERT 列顺序对齐
    validate_rows_before_insert(rows, COLUMNS, "fact_risk_factors")
    conn.executemany("INSERT INTO fact_risk_factors (...) VALUES (...)", rows)

⚠ 设计:
  - rows 是 list of tuples (跟 INSERT 同款), columns 是列名顺序, 内部 zip 成 dict 验证.
  - 违反率 ≤ max_violation_rate (默认 0.1%) → log warn 继续; 超过 → raise.
  - log 前 N 条违反 sample 帮 debug.

⚠ 失败处理 (Rule 5 root cause):
  - 违反率 > threshold 是真问题, 必须排查源头, 不许 silent pass.
  - log 中给出违反字段名 + 违反类型 (NULL/enum/sign/cap/type), 跟字典 yaml 对齐排查.
"""
from __future__ import annotations

import logging
from typing import Sequence

from services.data_governance.enforcer import (
    DictionaryViolation,
    enforce_dictionary_batch,
)

log = logging.getLogger("data_governance.etl_hook")


def validate_rows_before_insert(
    rows: list,
    columns: Sequence[str],
    table_name: str,
    *,
    max_violation_rate: float = 0.001,
    sample_for_log: int = 5,
    skip_missing_table: bool = False,
) -> dict:
    """ETL INSERT 前字典 enforce.

    Args:
        rows: List of tuples (rec-style), 跟 INSERT 列顺序对齐.
        columns: 列名 sequence (跟 rows tuple 同 index 顺序).
        table_name: 字典 lookup key ('fact_risk_factors' 或 'smartmoney.duckdb.fact_risk_factors').
        max_violation_rate: 违反率上限 (默认 0.001 = 0.1%). 超过 raise RuntimeError.
        sample_for_log: log warn 时显示前 N 条违反.
        skip_missing_table: 表不在字典时是否 skip (默认 False, 强制入字典).

    Returns:
        {
            "total":     int,    # 总行数
            "passed":    int,
            "failed":    int,
            "rate":      float,  # 违反率
            "violations_sample": list,  # 前 N 条违反详情
        }

    Raises:
        RuntimeError: 违反率 > max_violation_rate.
        DictionaryViolation: 表不在字典 (skip_missing_table=False) 或字典调用错误.
    """
    if not rows:
        log.info(f"  [data_governance] {table_name}: 空 rows, skip")
        return {"total": 0, "passed": 0, "failed": 0, "rate": 0.0,
                "violations_sample": []}

    dict_rows = (dict(zip(columns, r)) for r in rows)
    result = enforce_dictionary_batch(
        dict_rows, table_name,
        skip_missing_table=skip_missing_table,
    )

    total = result["total"]
    failed = result["failed"]
    rate = failed / max(total, 1)

    if failed > 0:
        log.warning(
            f"  [data_governance] {table_name}: "
            f"{failed:,}/{total:,} rows ({rate:.4%}) 违反字典"
        )
        for fr in result["failed_rows"][:sample_for_log]:
            v_str = "; ".join(fr["violations"][:3])
            log.warning(f"    row #{fr['row_idx']}: {v_str}")

    if rate > max_violation_rate:
        raise RuntimeError(
            f"[data_governance] {table_name}: 违反率 {rate:.4%} > "
            f"max={max_violation_rate:.4%}, 拒绝 INSERT. 排查源头 ETL 计算逻辑."
        )

    if total > 0 and failed == 0:
        log.info(f"  [data_governance] {table_name}: {total:,} rows 全过字典 enforce ✓")
    elif total > 0:
        log.info(f"  [data_governance] {table_name}: {total:,} rows "
                 f"({total - failed:,} pass / {failed:,} 违反 但 {rate:.4%} < {max_violation_rate:.4%}, 允许写)")

    return {
        "total": total,
        "passed": total - failed,
        "failed": failed,
        "rate": rate,
        "violations_sample": result["failed_rows"][:sample_for_log],
    }
