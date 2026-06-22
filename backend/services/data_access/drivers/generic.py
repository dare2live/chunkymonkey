"""generic driver: 简单 entity (code IN + asof≤t + 列投影 + clean) 的取数实现。

table/columns 来自可信 config (非用户输入) → f-string 内插; codes/as_of/start 参数化 (防注入)。
"""
from __future__ import annotations

from ..asof import asof_clause
from ..cleaner import clean_rows
from ..keys import code_to_ts_code
from ..spec import EntitySpec
from .. import resolver


class GenericDriver:
    def fetch(self, spec: EntitySpec, codes=None, start=None, as_of=None, conn=None) -> list[dict]:
        own = conn is None
        c = conn or resolver.connect_ro(spec.db)
        try:
            resolver.preflight(spec, conn=c)
            sql, params = self._build(spec, codes, start, as_of)
            rows = c.execute(sql, params).fetchall()
        finally:
            if own:
                c.close()
        # SELECT 严格按 spec.columns 列序投影 → 结果列名即 spec.columns (免依赖 cursor.description)
        return clean_rows(spec, list(spec.columns), rows)

    @staticmethod
    def _build(spec: EntitySpec, codes, start, as_of) -> tuple[str, list]:
        cols_sql = ", ".join(spec.columns)
        where: list[str] = []
        params: list = []
        if codes:
            # 输入统一 6 位; ts_code 列需转换后过滤
            vals = [code_to_ts_code(x) for x in codes] if spec.code_is_ts else list(codes)
            where.append(f"{spec.code_col} IN ({','.join('?' for _ in vals)})")
            params.extend(vals)
        asof_sql, asof_params = asof_clause(spec, as_of, start)
        if asof_sql:
            where.append(asof_sql)
            params.extend(asof_params)
        sql = f"SELECT {cols_sql} FROM {spec.table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {spec.code_col}, {spec.asof_col}"
        return sql, params
