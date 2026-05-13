"""Phase ε — selection 事件 logger (insert helpers)。

两种来源:
  1. daily-topk 选中: log_topk_selection(conn, snapshot_date, rows)
     - rows: [{stock_code, rank_in_date, pred_score, model_id, baseline_horizon_days}]
  2. 公式触发:        log_formula_selection(conn, signal_date, rows)
     - rows: [{stock_code, formula_id, strength, state, horizon_days}]

均做 INSERT OR REPLACE 幂等 (按 PRIMARY KEY 重写)。
"""
from __future__ import annotations

import logging
from typing import Iterable


log = logging.getLogger("selection.logger")


def log_topk_selection(
    conn,
    snapshot_date: str,
    rows: Iterable[dict],
    *,
    horizon_days_default: int = 20,
) -> int:
    """daily-topk 选中事件入库。

    Args:
        conn: smartmoney 主连接
        snapshot_date: YYYY-MM-DD
        rows: dict iterable, 至少含 stock_code; 可选 rank_in_date / pred_score / model_id / baseline_horizon_days

    Returns:
        写入行数
    """
    payload = []
    for r in rows:
        sc = r.get("stock_code")
        if not sc:
            continue
        payload.append((
            snapshot_date, sc, "daily_topk",
            str(r.get("model_id") or "champion"),
            r.get("rank_in_date"),
            float(r.get("pred_score")) if r.get("pred_score") is not None else None,
            None, None,
            int(r.get("horizon_days") or r.get("baseline_horizon_days") or horizon_days_default),
        ))
    if not payload:
        return 0
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "DELETE FROM fact_stock_selection_log WHERE select_date = ? AND select_source = 'daily_topk'",
            [snapshot_date],
        )
        conn.executemany(
            """
            INSERT INTO fact_stock_selection_log
              (select_date, stock_code, select_source, source_id,
               rank_in_date, pred_score, strength, state, horizon_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    return len(payload)


def log_formula_selection(
    conn,
    signal_date: str,
    rows: Iterable[dict],
    *,
    horizon_days_default: int = 20,
) -> int:
    """公式触发事件入库。

    Args:
        conn: smartmoney 主连接
        signal_date: YYYY-MM-DD
        rows: dict iterable, 含 stock_code + formula_id; 可选 strength / state / horizon_days

    Returns:
        写入行数
    """
    payload = []
    for r in rows:
        sc = r.get("stock_code")
        fid = r.get("formula_id")
        if not sc or not fid:
            continue
        payload.append((
            signal_date, sc, "formula", fid,
            None, None,
            float(r.get("strength")) if r.get("strength") is not None else None,
            r.get("state"),
            int(r.get("horizon_days") or horizon_days_default),
        ))
    if not payload:
        return 0
    conn.execute("BEGIN TRANSACTION")
    try:
        # 注: formula 触发可能同日多 formula, 不能整日全删
        # 只删本批的 (date, formula_id) 组合
        formula_ids = list({r["formula_id"] for r in rows if r.get("formula_id")})
        placeholders = ",".join(["?"] * len(formula_ids))
        conn.execute(
            f"""DELETE FROM fact_stock_selection_log
                WHERE select_date = ? AND select_source = 'formula'
                  AND source_id IN ({placeholders})""",
            [signal_date] + formula_ids,
        )
        conn.executemany(
            """
            INSERT INTO fact_stock_selection_log
              (select_date, stock_code, select_source, source_id,
               rank_in_date, pred_score, strength, state, horizon_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    return len(payload)


def backfill_from_existing_tables(conn) -> dict[str, int]:
    """一次性回填:
       - fact_technical_trigger → log_formula_selection
       - mart_daily_recommendation → log_topk_selection
    返回 {表名: 写入行数}.
    """
    # 1. 回填 formula 触发
    sigs = conn.execute(
        """
        SELECT date, stock_code, formula_id, strength, state
          FROM fact_technical_trigger
         ORDER BY date, formula_id
        """
    ).fetchall()
    # 按日期+formula 分桶, 批量 insert
    from collections import defaultdict
    by_dated_formula: dict[tuple, list[dict]] = defaultdict(list)
    for d, sc, fid, strength, state in sigs:
        by_dated_formula[(str(d), fid)].append({
            "stock_code": sc, "formula_id": fid,
            "strength": float(strength) if strength else None, "state": state,
        })
    log.info(f"  formula triggers: {len(sigs):,} signals × {len(by_dated_formula):,} (date, formula) 桶")

    n_formula = 0
    # 整事务一次性写, 避免多次 BEGIN/COMMIT (DuckDB ≥1.0 性能差异)
    conn.execute("BEGIN TRANSACTION")
    try:
        # 清空 formula 类
        conn.execute("DELETE FROM fact_stock_selection_log WHERE select_source = 'formula'")
        payload = []
        for (d, fid), rs in by_dated_formula.items():
            for r in rs:
                payload.append((
                    d, r["stock_code"], "formula", fid,
                    None, None, r["strength"], r["state"], 20,
                ))
        conn.executemany(
            """INSERT INTO fact_stock_selection_log
               (select_date, stock_code, select_source, source_id,
                rank_in_date, pred_score, strength, state, horizon_days)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            payload,
        )
        n_formula = len(payload)
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    # 2. 回填 daily-topk (只有 7 天数据可回)
    topk_rows = conn.execute(
        """
        SELECT snapshot_date, stock_code, model_id, rank_in_date, pred_score, baseline_horizon_days
          FROM mart_daily_recommendation
        """
    ).fetchall()
    by_date: dict[str, list[dict]] = defaultdict(list)
    for d, sc, mid, rank, score, horiz in topk_rows:
        by_date[str(d)].append({
            "stock_code": sc, "model_id": mid,
            "rank_in_date": rank, "pred_score": score,
            "baseline_horizon_days": horiz,
        })
    n_topk = 0
    for d, rs in by_date.items():
        n_topk += log_topk_selection(conn, d, rs)

    log.info(f"  topk: {n_topk:,} 行 / formula: {n_formula:,} 行")
    return {"formula": n_formula, "daily_topk": n_topk}
