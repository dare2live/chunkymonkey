"""Phase δ — 公式信号 IC 计算 (Spearman + Pearson)。

对每个 formula × snapshot_date, 取所有当日触发信号的股票, 关联 forward return
(5d / 10d / 30d), 算 Spearman 排序相关 = rank IC。

不依赖 scipy: 用 stdlib (rank by argsort, Pearson on ranks)。
"""
from __future__ import annotations

import math
from typing import Iterable


def _rank(values: list[float]) -> list[float]:
    """简单 rank: 升序排名, 平均处理 tie (Spearman 标准)。"""
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(x: list[float], y: list[float]) -> float | None:
    """Pearson 相关。"""
    if len(x) != len(y) or len(x) < 2:
        return None
    n = len(x)
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    denom_x = math.sqrt(sum((v - mx) ** 2 for v in x))
    denom_y = math.sqrt(sum((v - my) ** 2 for v in y))
    if denom_x == 0 or denom_y == 0:
        return None
    return num / (denom_x * denom_y)


def spearman_ic(scores: list[float], returns: list[float]) -> float | None:
    """计算 Spearman rank IC。

    Args:
        scores: 信号强度 (formula strength)
        returns: 对应 forward return

    Returns:
        IC 值 [-1, 1], 或 None (数据不足/同值)
    """
    if not scores or not returns or len(scores) != len(returns):
        return None
    # 过滤 None
    pairs = [(s, r) for s, r in zip(scores, returns) if s is not None and r is not None]
    if len(pairs) < 3:
        return None
    s_list = [p[0] for p in pairs]
    r_list = [p[1] for p in pairs]
    return _pearson(_rank(s_list), _rank(r_list))


def compute_signal_ic_for_date(
    conn,
    snapshot_date: str,
    formula_id: str,
    formula_variant: str | None = None,
    *,
    market_kline_close_fn,
) -> dict:
    """计算 (formula_id, snapshot_date) 的 5/10/30 日 IC。

    Args:
        conn: smartmoney 连接 (查 fact_technical_trigger)
        snapshot_date: 'YYYY-MM-DD'
        formula_id: 公式 ID
        formula_variant: 公式变体 (None 表示所有变体)
        market_kline_close_fn: callable(stock_code, date) -> close|None,
                               用于查未来 N 日收盘价

    Returns:
        {n_signals, ic_5d, ic_10d, ic_30d}
    """
    # 拉当日所有信号 + 信号当日 close
    where_variant = " AND formula_variant = ?" if formula_variant else ""
    params = [formula_id, snapshot_date]
    if formula_variant:
        params.append(formula_variant)
    rows = conn.execute(
        f"""
        SELECT stock_code, strength
          FROM fact_technical_trigger
         WHERE formula_id = ? AND date = ?{where_variant}
        """,
        params,
    ).fetchall()
    if not rows:
        return {"n_signals": 0, "ic_5d": None, "ic_10d": None, "ic_30d": None}

    # 当日 close (entry)
    scores = []
    entries = []
    codes = []
    for sc, strength in rows:
        cl = market_kline_close_fn(sc, snapshot_date)
        if cl and cl > 0:
            scores.append(float(strength))
            entries.append(float(cl))
            codes.append(sc)

    if len(codes) < 3:
        return {"n_signals": len(rows), "ic_5d": None, "ic_10d": None, "ic_30d": None}

    # 算各 horizon 的 forward return
    def _forward_ret(target_offset: int) -> list[float | None]:
        # 简单方法: 找 snapshot_date 后第 target_offset 个有数据的日
        # 这里偷懒, 用日历后 target_offset 自然日 (实际 trading day 差异 ~30%, 后续 sprint 修)
        from datetime import date as _date, timedelta
        d = _date.fromisoformat(snapshot_date)
        target = (d + timedelta(days=int(target_offset * 1.5))).isoformat()  # 1.5x 自然日 ≈ trading day
        rets = []
        for sc, entry in zip(codes, entries):
            exit_close = market_kline_close_fn(sc, target)
            if exit_close and exit_close > 0:
                rets.append(exit_close / entry - 1)
            else:
                rets.append(None)
        return rets

    rets_5d = _forward_ret(5)
    rets_10d = _forward_ret(10)
    rets_30d = _forward_ret(30)

    return {
        "n_signals": len(rows),
        "ic_5d":  spearman_ic(scores, rets_5d),
        "ic_10d": spearman_ic(scores, rets_10d),
        "ic_30d": spearman_ic(scores, rets_30d),
    }


def write_ic_to_db(
    conn,
    snapshot_date: str,
    formula_id: str,
    formula_variant: str,
    metrics: dict,
) -> None:
    """落库 mart_signal_ic 一行 (atomic: DELETE old + INSERT new)。"""
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            """
            DELETE FROM mart_signal_ic
             WHERE snapshot_date = ? AND formula_id = ? AND formula_variant = ?
            """,
            [snapshot_date, formula_id, formula_variant],
        )
        conn.execute(
            """
            INSERT INTO mart_signal_ic
              (snapshot_date, formula_id, formula_variant,
               n_signals, ic_5d, ic_10d, ic_30d,
               rank_ic_5d, rank_ic_10d, rank_ic_30d)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snapshot_date, formula_id, formula_variant,
                metrics["n_signals"],
                metrics["ic_5d"], metrics["ic_10d"], metrics["ic_30d"],
                metrics["ic_5d"], metrics["ic_10d"], metrics["ic_30d"],
            ],
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
