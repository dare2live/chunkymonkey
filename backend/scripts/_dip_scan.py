"""全历史行数塌陷扫描。

现有 check_cross_section 只看近 60 交易日, 历史异常一旦滑出窗口就永久失查;
本函数提供全历史扫描能力。
"""
from __future__ import annotations


def scan_full_history(conn, table: str, date_col: str, *,
                       known_empty: set[str] | None = None,
                       window: int = 10,
                       ratio: float = 0.5) -> list[dict]:
    known_empty = known_empty or set()
    window = int(window)
    try:
        rows = conn.execute(
            f'''
            with per as (select "{date_col}" d, count(*) n from "{table}" group by 1),
                 w as (select d, n,
                              median(n) over (order by d rows between {window} preceding
                                               and {window} following) med
                       from per)
            select d, n, med from w order by d
            '''
        ).fetchall()
    except Exception:
        return []

    out: list[dict] = []
    for d, n, med in rows:
        day = str(d)
        if day in known_empty:
            continue
        if med is None or med <= 0:
            continue
        if n < med * ratio:
            out.append({
                "date": day,
                "rows": int(n),
                "neighbor_median": float(med),
                "ratio": float(ratio),
            })
    return out
