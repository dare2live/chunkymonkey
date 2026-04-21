"""Layer 2B · Sharpe Style Analysis (机构风格暴露).

Sharpe (1992): 机构 chain PnL 对 N 个行业指数做约束回归:
    r_inst,t = Σ_i w_i · r_sector_i,t + α + ε
    s.t. Σw = 1, w >= 0  (multiple-choice)

输出每个机构 13 维 TDX L1 行业暴露 vector + 纯 α + R².

数据准备:
1. 13 个 L1 行业等权指数日收益（从 price_kline + dim_stock_tdx_industry 聚合）
2. 每个机构的日度组合收益:
   - 对该机构每天所有 open chain，按 entry_follow_price → today close 浮动 PnL
   - 等权平均成机构当日组合收益
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np

from ._dates import to_iso

logger = logging.getLogger("cm-api.sef.inst_style")

TDX_L1_CODES = [f"T{str(i).zfill(2)}" for i in range(1, 14)]  # T01..T13


# ---------------------------------------------------------------------------
# 1) 构建 13 个 L1 行业等权日收益
# ---------------------------------------------------------------------------


def _build_sector_index(
    conn: sqlite3.Connection, mkt_conn: sqlite3.Connection
) -> dict[str, dict[str, float]]:
    """{sector_code: {date: ret}}."""
    # 先拿到每个 sector 的股票清单
    sec_to_codes: dict[str, list[str]] = defaultdict(list)
    rows = conn.execute(
        "SELECT stock_code, tdx_l1 FROM dim_stock_tdx_industry WHERE tdx_l1 IS NOT NULL"
    ).fetchall()
    for r in rows:
        sec_to_codes[r[1]].append(r[0])

    # 用大 JOIN + GROUP BY 一次聚合全部，避免逐行计算
    # 先把行业表写入临时表，便于 JOIN
    with mkt_conn:  # 单事务
        mkt_conn.execute("DROP TABLE IF EXISTS temp.sector_map")
        mkt_conn.execute("CREATE TEMP TABLE sector_map(code TEXT PRIMARY KEY, sector TEXT)")
        mkt_conn.executemany(
            "INSERT INTO temp.sector_map(code, sector) VALUES(?, ?)",
            [(c, s) for s, codes in sec_to_codes.items() for c in codes],
        )

    logger.info("[SEF Layer 2B] 行业→股票映射 %d 行", sum(len(v) for v in sec_to_codes.values()))

    # 一次性计算所有股票日收益
    mkt_conn.execute("DROP TABLE IF EXISTS temp.stock_daily_ret")
    mkt_conn.execute(
        """
        CREATE TEMP TABLE stock_daily_ret AS
        SELECT code, date,
               close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
        FROM price_kline
        WHERE freq='daily' AND adjust='qfq'
        """
    )

    sector_ret: dict[str, dict[str, float]] = defaultdict(dict)
    rows = mkt_conn.execute(
        """
        SELECT s.sector, r.date, AVG(r.ret) AS avg_ret
        FROM stock_daily_ret r JOIN temp.sector_map s ON r.code = s.code
        WHERE r.ret IS NOT NULL
        GROUP BY s.sector, r.date
        ORDER BY s.sector, r.date
        """
    ).fetchall()
    for r in rows:
        sector_ret[r[0]][to_iso(r[1])] = float(r[2])

    for sec, seri in sector_ret.items():
        logger.info("[SEF Layer 2B] sector %s: %d days", sec, len(seri))
    return dict(sector_ret)


# ---------------------------------------------------------------------------
# 2) 每个机构的日度组合收益
# ---------------------------------------------------------------------------


def _build_institution_daily_return(
    conn: sqlite3.Connection, mkt_conn: sqlite3.Connection
) -> dict[str, dict[str, float]]:
    """每个机构每天的"平均 chain 浮动日收益"（含所有 open/closed 链）.

    性能优化：一次性加载全市场日收益 + 分组聚合（避免 N+1 query）.
    """
    chains = conn.execute(
        """
        SELECT institution_id, stock_code, entry_date,
               COALESCE(exit_date, eval_date) AS end_date
        FROM fact_chain_alpha_truth
        WHERE entry_date IS NOT NULL
        """
    ).fetchall()
    logger.info("[SEF Layer 2B] 机构 chain 总数 %d", len(chains))

    # 一次性加载所有 chain 涉及的股票日收益
    involved_codes = {r[1] for r in chains}
    logger.info("[SEF Layer 2B] 涉及股票 %d 只，开始批量加载日收益 ...", len(involved_codes))

    # 用 CTE 一次性计算所有股票日收益
    all_rets_rows = mkt_conn.execute(
        """
        WITH daily_ret AS (
            SELECT code, date,
                   close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
            FROM price_kline
            WHERE freq='daily' AND adjust='qfq'
        )
        SELECT code, date, ret FROM daily_ret WHERE ret IS NOT NULL
        """
    ).fetchall()
    # stock_ret[code][date] = ret
    stock_ret_all: dict[str, dict[str, float]] = defaultdict(dict)
    for code, date, ret in all_rets_rows:
        if code in involved_codes:
            stock_ret_all[code][to_iso(date)] = float(ret)
    logger.info("[SEF Layer 2B] 日收益加载完成（%d 股）", len(stock_ret_all))

    # inst_day_rets[inst][date] = list of returns
    inst_day_rets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in chains:
        inst_id, code, ed, end_d = r[0], r[1], to_iso(r[2]), to_iso(r[3]) if r[3] else None
        if not ed:
            continue
        rets = stock_ret_all.get(code, {})
        for d, ret in rets.items():
            if d < ed:
                continue
            if end_d and d > end_d:
                continue
            inst_day_rets[inst_id][d].append(ret)

    # 取每日平均
    out: dict[str, dict[str, float]] = {}
    for inst, by_day in inst_day_rets.items():
        if len(by_day) < 60:
            continue
        out[inst] = {d: float(np.mean(vs)) for d, vs in by_day.items()}
    logger.info("[SEF Layer 2B] 有效机构（>60 日）%d", len(out))
    return out


# ---------------------------------------------------------------------------
# 3) 约束 QP 回归
# ---------------------------------------------------------------------------


def _fit_sharpe_style(
    inst_series: dict[str, float], sector_ret: dict[str, dict[str, float]]
) -> Optional[dict]:
    """对单个机构做 Sharpe Style Analysis."""
    try:
        import cvxpy as cp
    except ImportError:
        return None

    # 找共同交易日
    sector_codes = list(sector_ret.keys())
    common_dates = set(inst_series.keys())
    for sec in sector_codes:
        common_dates &= set(sector_ret[sec].keys())
    common_dates = sorted(common_dates)
    if len(common_dates) < 60:
        return None

    y = np.array([inst_series[d] for d in common_dates], dtype=float)
    X = np.array(
        [[sector_ret[sec].get(d, 0.0) for sec in sector_codes] for d in common_dates],
        dtype=float,
    )

    w = cp.Variable(X.shape[1], nonneg=True)
    alpha = cp.Variable()
    resid = y - X @ w - alpha
    prob = cp.Problem(
        cp.Minimize(cp.sum_squares(resid)),
        [cp.sum(w) == 1.0],
    )
    try:
        prob.solve(solver=cp.CLARABEL, verbose=False)
    except Exception as e:  # noqa: BLE001
        logger.debug("[SEF Layer 2B] CLARABEL 失败，试 OSQP: %s", e)
        try:
            prob.solve(solver=cp.OSQP, verbose=False)
        except Exception as e2:  # noqa: BLE001
            logger.warning("[SEF Layer 2B] 拟合失败: %s", e2)
            return None
    if w.value is None or alpha.value is None:
        return None

    y_hat = X @ np.asarray(w.value) + float(alpha.value)
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else None

    return {
        "exposure": {sec: float(wv) for sec, wv in zip(sector_codes, w.value)},
        "alpha_pure": float(alpha.value),
        "r2": float(r2) if r2 is not None else None,
        "n_days": len(common_dates),
    }


# ---------------------------------------------------------------------------
# 4) 主入口
# ---------------------------------------------------------------------------


def build_institution_style(
    conn: sqlite3.Connection,
    mkt_conn: sqlite3.Connection,
    *,
    limit_inst: Optional[int] = None,
) -> dict:
    sector_ret = _build_sector_index(conn, mkt_conn)
    if not sector_ret:
        return {"error": "sector returns empty"}

    inst_day_ret = _build_institution_daily_return(conn, mkt_conn)

    # 幂等
    conn.execute("DELETE FROM mart_institution_style")
    now = datetime.utcnow().isoformat(timespec="seconds")

    items = list(inst_day_ret.items())
    if limit_inst:
        items = items[:limit_inst]

    written = 0
    fails = 0
    for i, (inst, series) in enumerate(items):
        fit = _fit_sharpe_style(series, sector_ret)
        if fit is None:
            fails += 1
            continue
        conn.execute(
            """
            INSERT INTO mart_institution_style(
                institution_id, style_exposure_json, style_alpha_pure,
                style_r2, last_updated
            ) VALUES(?,?,?,?,?)
            """,
            (
                inst,
                json.dumps(fit["exposure"]),
                fit["alpha_pure"],
                fit["r2"],
                now,
            ),
        )
        written += 1
        if (i + 1) % 50 == 0:
            conn.commit()
            logger.info("[SEF Layer 2B] 进度 %d / %d", i + 1, len(items))
    conn.commit()

    report = {
        "institutions_total": len(inst_day_ret),
        "written": written,
        "fit_failed": fails,
        "sectors": list(sector_ret.keys()),
    }
    logger.info("[SEF Layer 2B] 完成: %s", report)
    return report
