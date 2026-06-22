"""读时口径清洗 (不变量#1 统一主键): 输出归一 — code→6位, asof→ISO。单一执行点。

物化清洗 (复权/视图) 在写侧 builder; 此处只做读时归一 (主键/日期口径), 不重算因子。
"""
from __future__ import annotations

from .asof import asof_to_iso
from .keys import ts_code_to_code
from .spec import EntitySpec


def clean_rows(spec: EntitySpec, columns: list[str], rows: list[tuple]) -> list[dict]:
    """原始行 → 归一 dict 行 (code 6位, asof_col ISO)。"""
    out: list[dict] = []
    for r in rows:
        d = dict(zip(columns, r))
        # 主键归一: ts_code → 6 位 (项目内部统一口径)
        if spec.code_is_ts and spec.code_col in d and d[spec.code_col] is not None:
            d[spec.code_col] = ts_code_to_code(d[spec.code_col])
        # asof 锚归一: YYYYMMDD → ISO (与决策日同口径)
        if spec.asof_col in d and d[spec.asof_col] is not None:
            d[spec.asof_col] = asof_to_iso(spec, d[spec.asof_col])
        out.append(d)
    return out
