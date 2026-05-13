"""Phase ε — 选股 outcome 计算 (复用 paper_engine.outcomes)。

对 fact_stock_selection_log 中每行, 算 5/10/30d forward return + max_dd + outcome,
写入 mart_stock_selection_outcome。

复用:
  - paper_engine.outcomes.compute_forward_returns
  - paper_engine.outcomes.classify_outcome
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date as _date, timedelta
from typing import Iterable

from services.paper_engine.outcomes import classify_outcome, compute_forward_returns


log = logging.getLogger("selection.outcome")


def _days_to_t1_threshold(future_closes: list[float | None], entry: float, threshold: float = 0.05) -> int | None:
    """到达 +5% 的天数 (target_1 简化定义), 没到达返回 None。"""
    if entry <= 0:
        return None
    target = entry * (1 + threshold)
    for i, c in enumerate(future_closes):
        if c is not None and c >= target:
            return i + 1  # D+1 是 index 0 → 第 1 天
    return None


def compute_outcomes_for_period(
    conn,
    mkt_conn,
    start_date: str,
    end_date: str,
) -> int:
    """对 [start_date, end_date] 间所有 selection_log 算 outcomes + 落库。

    Args:
        conn: smartmoney 主连接
        mkt_conn: market.duckdb 连接 (K 线源)
        start_date, end_date: 'YYYY-MM-DD'

    Returns:
        写入行数
    """
    t0 = time.time()
    # 1. 拉 selection_log 行 (PIT)
    log_rows = conn.execute(
        """
        SELECT select_date, stock_code, select_source, source_id, horizon_days
          FROM fact_stock_selection_log
         WHERE select_date >= ? AND select_date <= ?
        """,
        [start_date, end_date],
    ).fetchall()
    if not log_rows:
        log.warning("  无 selection 事件")
        return 0
    log.info(f"  selection 行 {len(log_rows):,}")

    # 2. 一次性拉这些股票的 K 线 (start ~ end+45d)
    codes = list({r[1] for r in log_rows})
    placeholders = ",".join(["?"] * len(codes))
    extend_end = (_date.fromisoformat(end_date) + timedelta(days=45)).isoformat()
    kl_rows = mkt_conn.execute(
        f"""
        SELECT code, date, close
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily'
           AND code IN ({placeholders})
           AND date >= ? AND date <= ?
         ORDER BY code, date
        """,
        codes + [start_date, extend_end],
    ).fetchall()
    kl_by_code: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for c, d, cl in kl_rows:
        if cl and float(cl) > 0:
            kl_by_code[c].append((str(d), float(cl)))
    log.info(f"  K 线 {len(kl_rows):,} 行 / {len(kl_by_code):,} 股")

    # 3. 对每行算 outcome
    out_rows = []
    for select_date, stock_code, select_source, source_id, horizon in log_rows:
        kl = kl_by_code.get(stock_code, [])
        if not kl:
            continue
        # 找 entry_price (select_date 当日 close)
        entry_idx = None
        for i, (d, cl) in enumerate(kl):
            if d == str(select_date):
                entry_idx = i
                break
        if entry_idx is None:
            continue
        entry_price = kl[entry_idx][1]
        # 后续 30+ 个日的 close 序列
        future_closes = [cl for _, cl in kl[entry_idx + 1:entry_idx + 31]]
        # forward returns
        fwd = compute_forward_returns(entry_price, future_closes)
        # days to +5% threshold
        d2t1 = _days_to_t1_threshold(future_closes, entry_price, 0.05)

        out_rows.append((
            select_date, stock_code, select_source, source_id, entry_price,
            fwd["fwd_ret_5d"], fwd["fwd_ret_10d"], fwd["fwd_ret_30d"], fwd["fwd_max_dd_30d"],
            d2t1,
            classify_outcome(fwd["fwd_ret_5d"]),
            classify_outcome(fwd["fwd_ret_10d"]),
            classify_outcome(fwd["fwd_ret_30d"]),
            int(horizon) if horizon else None,
        ))

    # 4. 写库 (事务原子)
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "DELETE FROM mart_stock_selection_outcome WHERE select_date >= ? AND select_date <= ?",
            [start_date, end_date],
        )
        conn.executemany(
            """INSERT INTO mart_stock_selection_outcome
               (select_date, stock_code, select_source, source_id, entry_price,
                fwd_ret_5d, fwd_ret_10d, fwd_ret_30d, fwd_max_dd_30d,
                days_to_t1, outcome_5d, outcome_10d, outcome_30d, horizon_days)
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
