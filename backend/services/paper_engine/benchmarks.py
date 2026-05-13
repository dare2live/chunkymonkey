"""Phase δ — 基准 NAV 计算。

两条基准:
  1. HS300: market.duckdb price_kline code='000300' 的归一化 NAV (起点 1.0)
  2. 等权: 同一 universe 等权持有, 当日均收益累积

输入: dates 序列 (升序)
输出: dict[date → nav] (起点 nav=1.0)
"""
from __future__ import annotations

from typing import Iterable


def hs300_nav_series(mkt_conn, start_date: str, end_date: str) -> dict[str, float]:
    """归一化 HS300 NAV (起点 1.0)。

    Args:
        mkt_conn: market.duckdb 连接 (DuckConn 或原生)
        start_date / end_date: 'YYYY-MM-DD'

    Returns:
        {date: nav} (升序), 起点 1.0
    """
    rows = mkt_conn.execute(
        """
        SELECT date, close
          FROM price_kline
         WHERE code = '000300' AND date >= ? AND date <= ?
         ORDER BY date
        """,
        [start_date, end_date],
    ).fetchall()
    if not rows:
        return {}
    first_close = float(rows[0][1])
    if first_close <= 0:
        return {}
    return {str(r[0]): float(r[1]) / first_close for r in rows}


def equal_weight_nav_series(
    mkt_conn,
    codes: Iterable[str],
    start_date: str,
    end_date: str,
) -> dict[str, float]:
    """等权 NAV: codes 当日平均收益 累乘。

    Args:
        codes: 持有的股票 universe (固定列表, 不变)
        start_date / end_date: 区间

    Returns:
        {date: nav}, 起点 1.0
    """
    codes_list = [str(c) for c in codes]
    if not codes_list:
        return {}
    placeholders = ",".join(["?"] * len(codes_list))
    rows = mkt_conn.execute(
        f"""
        SELECT date, code, close
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily'
           AND code IN ({placeholders})
           AND date >= ? AND date <= ?
         ORDER BY date, code
        """,
        codes_list + [start_date, end_date],
    ).fetchall()
    if not rows:
        return {}

    # 按日期 groupby: 每日 mean(daily_return), 累乘
    by_date: dict[str, dict[str, float]] = {}
    for d, c, cl in rows:
        by_date.setdefault(str(d), {})[str(c)] = float(cl)

    dates_sorted = sorted(by_date.keys())
    if not dates_sorted:
        return {}

    nav = 1.0
    prev_closes: dict[str, float] = by_date[dates_sorted[0]].copy()
    out: dict[str, float] = {dates_sorted[0]: 1.0}
    for d in dates_sorted[1:]:
        today_closes = by_date[d]
        rets = []
        for code, today_p in today_closes.items():
            prev_p = prev_closes.get(code)
            if prev_p and prev_p > 0:
                rets.append(today_p / prev_p - 1)
        if rets:
            mean_ret = sum(rets) / len(rets)
            nav *= (1 + mean_ret)
        out[d] = nav
        # 更新 prev (用今日)
        for code, p in today_closes.items():
            prev_closes[code] = p
    return out


def combine_benchmarks(
    main_curve: list[dict],
    hs300_nav: dict[str, float],
    eqw_nav: dict[str, float],
) -> list[dict]:
    """把基准 nav 拼到主组合 equity_curve 上, 同时算 cum_ret / vs_*_cum_ret。

    Args:
        main_curve: list of {date, total, cash, position_count} (from portfolio_backtest)
        hs300_nav, eqw_nav: {date: nav}

    Returns:
        list of dict 含全套字段
    """
    if not main_curve:
        return []
    initial = main_curve[0]["total"]
    out = []
    prev_total = initial
    for i, e in enumerate(main_curve):
        d = e["date"]
        nav = e["total"] / initial if initial > 0 else 1.0
        daily_ret = (e["total"] / prev_total - 1) if prev_total > 0 else 0.0
        cum_ret = nav - 1.0
        hs_nav = hs300_nav.get(d)
        eq_nav = eqw_nav.get(d)
        hs_cum = (hs_nav - 1.0) if hs_nav else None
        eq_cum = (eq_nav - 1.0) if eq_nav else None
        out.append({
            "snapshot_date": d,
            "nav": nav,
            "nav_value": e["total"],
            "daily_ret": daily_ret,
            "cum_ret": cum_ret,
            "hs300_nav": hs_nav,
            "hs300_cum_ret": hs_cum,
            "vs_hs300_cum_ret": (cum_ret - hs_cum) if hs_cum is not None else None,
            "eqw_nav": eq_nav,
            "eqw_cum_ret": eq_cum,
            "vs_eqw_cum_ret": (cum_ret - eq_cum) if eq_cum is not None else None,
            "cash": e.get("cash"),
            "position_count": e.get("position_count"),
        })
        prev_total = e["total"]
    return out
