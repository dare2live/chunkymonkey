"""Layer 4 · Black-Litterman Portfolio Optimizer.

Prior (market equilibrium)::
    Π = risk_aversion × Σ × w_market  (隐含均衡收益)

Views (from Layer 3 Bayesian posterior)::
    P: K×N 视角矩阵（N 股，K 视角数，每个视角对应某只股票）
    Q: K×1 视角收益向量（Layer 3 μ_post，日度 → 年化）
    Ω: K×K 视角不确定性（Layer 3 σ_post² 对角矩阵）

Posterior (combined)::
    E[R] = [ (τ Σ)⁻¹ + P'Ω⁻¹P ]⁻¹ [ (τ Σ)⁻¹ Π + P'Ω⁻¹Q ]

Optimize::
    max_w  E[R]' w - 0.5 λ w' Σ w
    s.t.   sum(w) = 1
           0 ≤ w_i ≤ 0.10         (单股上限 10%)
           Σ_sec w ≤ 0.25         (行业上限 25%)
           |w - w_prev|_1 ≤ 0.30  (月度换手 30%)

输出: portfolio_recommendation_daily 每天一份推荐.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np

from ._dates import to_iso

logger = logging.getLogger("cm-api.sef.bl")


# ---------------------------------------------------------------------------
# 1) 市场协方差 + 隐含均衡收益
# ---------------------------------------------------------------------------


def _load_stock_returns_matrix(
    mkt_conn: sqlite3.Connection, stock_codes: list[str], *, min_days: int = 120
) -> tuple[np.ndarray, list[str], list[str]]:
    """返回 (R, stocks_kept, dates) 矩阵，R[t, i] 是 stock i 在 date t 的日收益."""
    placeholders = ",".join("?" for _ in stock_codes)
    rows = mkt_conn.execute(
        f"""
        WITH daily AS (
            SELECT code, date,
                   close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
            FROM price_kline
            WHERE code IN ({placeholders}) AND freq='daily' AND adjust='qfq'
        )
        SELECT code, date, ret FROM daily WHERE ret IS NOT NULL
        """,
        stock_codes,
    ).fetchall()
    by_stock: dict[str, dict[str, float]] = defaultdict(dict)
    for code, date, ret in rows:
        by_stock[code][to_iso(date)] = float(ret)
    # 只保留有足够样本的股票
    keep = [c for c in stock_codes if len(by_stock[c]) >= min_days]
    if not keep:
        return np.empty((0, 0)), [], []
    all_dates = sorted(set().union(*(by_stock[c].keys() for c in keep)))
    R = np.full((len(all_dates), len(keep)), np.nan)
    for j, code in enumerate(keep):
        for t, d in enumerate(all_dates):
            if d in by_stock[code]:
                R[t, j] = by_stock[code][d]
    return R, keep, all_dates


def _covariance_and_market(
    R: np.ndarray, mkt_cap: Optional[dict[str, float]] = None,
    stocks: Optional[list[str]] = None
) -> tuple[np.ndarray, np.ndarray]:
    """Ledoit-Wolf shrinkage 协方差 + 市值加权市场权重."""
    from sklearn.covariance import LedoitWolf

    # 填补 NaN 为 0（代表未交易日）
    R_clean = np.where(np.isnan(R), 0.0, R)
    lw = LedoitWolf().fit(R_clean)
    Sigma = lw.covariance_
    n = Sigma.shape[0]
    if mkt_cap and stocks:
        w = np.array([mkt_cap.get(s, 1.0) for s in stocks], dtype=float)
        w_market = w / w.sum()
    else:
        w_market = np.ones(n) / n
    return Sigma, w_market


# ---------------------------------------------------------------------------
# 2) Black-Litterman 后验
# ---------------------------------------------------------------------------


def _bl_posterior(
    Pi: np.ndarray, Sigma: np.ndarray, P: np.ndarray, Q: np.ndarray,
    Omega: np.ndarray, tau: float = 0.05,
) -> np.ndarray:
    """返回 posterior expected returns E[R]."""
    inv_tauSigma = np.linalg.inv(tau * Sigma)
    if P.size == 0:
        return Pi
    inv_Omega = np.linalg.inv(Omega)
    # (τΣ)⁻¹ + P'Ω⁻¹P
    A = inv_tauSigma + P.T @ inv_Omega @ P
    # (τΣ)⁻¹ Π + P'Ω⁻¹Q
    b = inv_tauSigma @ Pi + P.T @ inv_Omega @ Q
    return np.linalg.solve(A, b)


# ---------------------------------------------------------------------------
# 3) 组合优化
# ---------------------------------------------------------------------------


def _solve_portfolio(
    E_R: np.ndarray, Sigma: np.ndarray, *,
    risk_aversion: float = 3.0,
    max_weight: float = 0.10,
    sector_ids: Optional[list[str]] = None,
    max_sector: float = 0.50,
    prev_weights: Optional[np.ndarray] = None,
    max_turnover: float = 0.30,
    max_holdings: int = 30,
) -> Optional[np.ndarray]:
    """cvxpy 求解带约束的 Markowitz 优化."""
    try:
        import cvxpy as cp
    except ImportError:
        return None

    n = len(E_R)
    w = cp.Variable(n, nonneg=True)
    utility = E_R @ w - 0.5 * risk_aversion * cp.quad_form(w, cp.psd_wrap(Sigma))

    constraints = [cp.sum(w) == 1.0, w <= max_weight]

    if sector_ids:
        unique_secs = sorted(set(sector_ids))
        for sec in unique_secs:
            mask = np.array([1.0 if s == sec else 0.0 for s in sector_ids])
            constraints.append(mask @ w <= max_sector)

    if prev_weights is not None and prev_weights.shape[0] == n:
        constraints.append(cp.norm(w - prev_weights, 1) <= max_turnover)

    prob = cp.Problem(cp.Maximize(utility), constraints)
    try:
        prob.solve(solver=cp.CLARABEL, verbose=False)
    except Exception:  # noqa: BLE001
        try:
            prob.solve(solver=cp.OSQP, verbose=False)
        except Exception as e2:  # noqa: BLE001
            logger.warning("[SEF BL] 优化失败: %s", e2)
            return None
    if w.value is None:
        return None

    w_arr = np.asarray(w.value)
    # 截断小权重 & 选前 max_holdings
    w_arr = np.where(w_arr < 0.005, 0.0, w_arr)
    if (w_arr > 0).sum() > max_holdings:
        top = np.argsort(-w_arr)[:max_holdings]
        mask = np.zeros(n, dtype=bool)
        mask[top] = True
        w_arr = np.where(mask, w_arr, 0.0)
    if w_arr.sum() > 0:
        w_arr = w_arr / w_arr.sum()
    return w_arr


# ---------------------------------------------------------------------------
# 4) 主入口
# ---------------------------------------------------------------------------


def build_daily_portfolio(
    conn: sqlite3.Connection,
    mkt_conn: sqlite3.Connection,
    *,
    as_of_date: Optional[str] = None,
    max_candidates: int = 80,
    tau: float = 0.05,
    risk_aversion: float = 3.0,
) -> dict:
    """生成 as_of_date 的组合推荐，写入 portfolio_recommendation_daily."""

    # as_of_date 默认取 Layer 3 最新日
    if as_of_date is None:
        row = conn.execute(
            "SELECT MAX(as_of_date) FROM mart_bayesian_posterior"
        ).fetchone()
        as_of_date = row[0] if row and row[0] else None
    if as_of_date is None:
        return {"error": "no bayesian posterior available"}

    # 1) 拉候选：posterior μ>0 且按 sector diversify（每 sector 最多 max_per_sector）
    max_per_sector = max(3, max_candidates // 4)
    cand_rows = conn.execute(
        """
        WITH ranked AS (
            SELECT b.stock_code, b.mu_posterior, b.sigma_posterior, b.industry_l1,
                   ROW_NUMBER() OVER (PARTITION BY b.industry_l1 ORDER BY b.mu_posterior DESC) AS rk
            FROM mart_bayesian_posterior b
            WHERE b.as_of_date=? AND b.mu_posterior > 0
        )
        SELECT stock_code, mu_posterior, sigma_posterior, industry_l1
        FROM ranked WHERE rk <= ?
        ORDER BY mu_posterior DESC
        LIMIT ?
        """,
        (as_of_date, max_per_sector, max_candidates),
    ).fetchall()
    if not cand_rows:
        return {"error": "no positive-mu candidates"}

    stocks = [r[0] for r in cand_rows]
    sector_ids = [r[3] for r in cand_rows]
    mu_post_daily = np.array([float(r[1]) for r in cand_rows])
    sigma_post_daily = np.array([float(r[2]) for r in cand_rows])
    # μ_post 是 "chain_follow_pnl %"，先转 decimal，再除以 60（平均 chain 持有日）得日收益预期
    mu_daily = mu_post_daily / 100.0 / 60.0
    sigma_daily = sigma_post_daily / 100.0 / math.sqrt(60.0)

    # 2) 协方差 + 先验（市场均衡）
    R, kept, dates = _load_stock_returns_matrix(mkt_conn, stocks, min_days=120)
    if not kept:
        return {"error": "no stocks with enough kline history"}

    logger.info(
        "[SEF BL] candidates=%d kept=%d dates=%d",
        len(stocks), len(kept), len(dates),
    )

    # 对齐到 kept
    keep_idx = [stocks.index(c) for c in kept]
    mu_daily = mu_daily[keep_idx]
    sigma_daily = sigma_daily[keep_idx]
    sector_ids = [sector_ids[i] for i in keep_idx]

    Sigma, w_market = _covariance_and_market(R)
    Pi = risk_aversion * Sigma @ w_market  # 隐含均衡

    # 3) Views (每只股票一个 view)
    n = len(kept)
    P = np.eye(n)
    Q = mu_daily.copy()
    Omega = np.diag(np.maximum(sigma_daily ** 2, 1e-8))

    E_R = _bl_posterior(Pi, Sigma, P, Q, Omega, tau=tau)

    # 4) 优化
    w = _solve_portfolio(
        E_R, Sigma,
        risk_aversion=risk_aversion,
        sector_ids=sector_ids,
    )
    if w is None:
        return {"error": "portfolio optimization failed"}

    # 5) 写入
    conn.execute(
        "DELETE FROM portfolio_recommendation_daily WHERE signal_date=?",
        (as_of_date,),
    )
    written = 0
    for i, code in enumerate(kept):
        if w[i] <= 0:
            continue
        rationale = {
            "mu_post_pct": round(float(mu_post_daily[keep_idx[i]]), 3),
            "sigma_post_pct": round(float(sigma_post_daily[keep_idx[i]]), 3),
            "E_R_daily": round(float(E_R[i]), 6),
        }
        conn.execute(
            """
            INSERT INTO portfolio_recommendation_daily(
                signal_date, stock_code, weight,
                expected_alpha, expected_sigma, sector, rationale_json
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                as_of_date, code, float(w[i]),
                float(E_R[i]), float(np.sqrt(Sigma[i, i])),
                sector_ids[i], json.dumps(rationale),
            ),
        )
        written += 1
    conn.commit()

    # 估算组合统计
    portfolio_mu = float(E_R @ w)
    portfolio_sigma = float(math.sqrt(w @ Sigma @ w))
    ex_ante_sharpe = portfolio_mu / portfolio_sigma * math.sqrt(252) if portfolio_sigma > 0 else None
    n_holdings = int((w > 0).sum())

    report = {
        "as_of_date": as_of_date,
        "candidates": len(stocks),
        "kept": len(kept),
        "holdings": n_holdings,
        "portfolio_mu_daily": round(portfolio_mu, 6),
        "portfolio_sigma_daily": round(portfolio_sigma, 6),
        "ex_ante_sharpe_annualized": round(ex_ante_sharpe, 3) if ex_ante_sharpe else None,
        "max_weight": round(float(np.max(w)), 3),
        "written": written,
    }
    logger.info("[SEF BL] 完成: %s", report)
    return report


