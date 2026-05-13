"""Phase ε — 每股 rolling 统计 (mart_stock_selection_summary)。

输入: fact_stock_selection_log + mart_stock_selection_outcome JOIN
输出: 每股 1 行 (snapshot_date)
"""
from __future__ import annotations

import logging
import time
from datetime import date as _date, timedelta


log = logging.getLogger("selection.summary")


def recompute_all_summaries(conn, asof_date: str) -> int:
    """全量重算 (snapshot_date=asof_date), 每股 1 行。

    Returns:
        写入行数
    """
    t0 = time.time()
    # 计算 30d / 90d cutoff
    asof = _date.fromisoformat(asof_date)
    cutoff_30 = (asof - timedelta(days=30)).isoformat()
    cutoff_90 = (asof - timedelta(days=90)).isoformat()

    # 单一 SQL 聚合 (DuckDB 支持 FILTER 子句 + ARG_MAX)
    rows = conn.execute(
        """
        WITH base AS (
          SELECT l.stock_code, l.select_date, l.source_id AS last_formula,
                 o.outcome_30d, o.fwd_ret_30d, o.fwd_max_dd_30d
            FROM fact_stock_selection_log l
            LEFT JOIN mart_stock_selection_outcome o
              ON o.select_date = l.select_date
             AND o.stock_code  = l.stock_code
             AND o.select_source = l.select_source
             AND o.source_id     = l.source_id
        )
        SELECT
          stock_code,
          COUNT(*) AS n_total,
          COUNT(*) FILTER (WHERE select_date >= ?) AS n_30d,
          COUNT(*) FILTER (WHERE select_date >= ?) AS n_90d,
          AVG(CASE WHEN outcome_30d='win' THEN 1.0
                   WHEN outcome_30d IN ('loss','flat') THEN 0.0 END) AS win_rate,
          AVG(CASE WHEN select_date >= ? AND outcome_30d='win' THEN 1.0
                   WHEN select_date >= ? AND outcome_30d IN ('loss','flat') THEN 0.0 END) AS win_rate_30d,
          AVG(CASE WHEN select_date >= ? AND outcome_30d='win' THEN 1.0
                   WHEN select_date >= ? AND outcome_30d IN ('loss','flat') THEN 0.0 END) AS win_rate_90d,
          AVG(fwd_ret_30d)    AS avg_ret,
          AVG(fwd_ret_30d) FILTER (WHERE select_date >= ?)  AS avg_ret_30d,
          AVG(fwd_max_dd_30d) AS avg_dd,
          MAX(select_date) AS last_select_date,
          ARG_MAX(last_formula, select_date) AS last_formula,
          ARG_MAX(COALESCE(outcome_30d, 'active'), select_date) AS last_outcome
        FROM base
        GROUP BY stock_code
        """,
        [cutoff_30, cutoff_90, cutoff_30, cutoff_30, cutoff_90, cutoff_90, cutoff_30],
    ).fetchall()

    if not rows:
        log.warning("  无 selection 数据")
        return 0
    log.info(f"  聚合: {len(rows):,} 股")

    # 写库 (DELETE asof + INSERT, atomic)
    out_rows = []
    for r in rows:
        out_rows.append((
            r[0], asof_date,
            int(r[1] or 0), int(r[2] or 0), int(r[3] or 0),
            float(r[4]) if r[4] is not None else None,
            float(r[5]) if r[5] is not None else None,
            float(r[6]) if r[6] is not None else None,
            float(r[7]) if r[7] is not None else None,
            float(r[8]) if r[8] is not None else None,
            float(r[9]) if r[9] is not None else None,
            r[10], r[11], r[12],
        ))

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM mart_stock_selection_summary WHERE snapshot_date = ?", [asof_date])
        conn.executemany(
            """INSERT INTO mart_stock_selection_summary
               (stock_code, snapshot_date,
                n_total, n_30d, n_90d,
                win_rate, win_rate_30d, win_rate_90d,
                avg_ret, avg_ret_30d, avg_dd,
                last_select_date, last_formula, last_outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            out_rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    log.info(f"完成: {len(out_rows):,} 行 (耗时 {time.time()-t0:.1f}s)")
    return len(out_rows)
