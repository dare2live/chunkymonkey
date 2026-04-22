"""Layer 3 · Bayesian Signal Updater.

对每个 (stock, industry) 维护 α 的高斯后验。

先验 (prior)::
    α_ij ~ N(μ_ind, σ²_ind)
    μ_ind / σ_ind 从 mart_institution_capability 按 (L1 industry) 聚合得到

似然 (likelihood)::
    观测 = 机构 i 对股票 j 的 DGTW selection α × 时效衰减
    - DGTW selection α ≈ mart_institution_capability.alpha_median
    - 时效衰减 = exp(-days_since / halflife_days)
    - 机构 drift: confidence_mult 从 institution_drift_log 读

后验 (posterior，Normal-Normal 共轭)::
    τ_prior  = 1 / σ²_prior
    τ_lik_i  = 1 / σ²_i  (per-institution信号精度)
    τ_post   = τ_prior + Σ τ_lik_i
    μ_post   = (τ_prior × μ_prior + Σ τ_lik_i × obs_i) / τ_post
    σ²_post  = 1 / τ_post

输出（新表 `mart_bayesian_posterior`）::
    stock_code / industry_l1 / as_of_date / μ_post / σ_post /
    n_signals / strongest_institution_id / dominant_holder_conf
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np

from ._dates import to_iso

logger = logging.getLogger("cm-api.sef.bayes")


def _ensure_posterior_table(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mart_bayesian_posterior (
            stock_code              TEXT NOT NULL,
            industry_l1             TEXT,
            as_of_date              TEXT NOT NULL,
            mu_prior                REAL,
            sigma_prior             REAL,
            mu_posterior            REAL,
            sigma_posterior         REAL,
            ci_lower_90             REAL,
            ci_upper_90             REAL,
            n_signals               INTEGER,
            dominant_institution    TEXT,
            dominant_weight         REAL,
            last_updated            TEXT,
            PRIMARY KEY(stock_code, as_of_date)
        );
        CREATE INDEX IF NOT EXISTS idx_bayes_date ON mart_bayesian_posterior(as_of_date);
        CREATE INDEX IF NOT EXISTS idx_bayes_stock ON mart_bayesian_posterior(stock_code);
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1) 先验：按 L1 行业聚合 α 分布（来自 mart_institution_capability）
# ---------------------------------------------------------------------------


def _build_industry_priors(conn: sqlite3.Connection) -> dict[str, tuple[float, float]]:
    """返回 {industry_l1: (mu, sigma)}.

    从 mart_institution_capability L1 级 α_median 聚合. L1 是 TDX 一级行业代码.
    """
    rows = conn.execute(
        """
        SELECT industry_code, AVG(alpha_median) AS mu, AVG(alpha_se) AS se,
               COUNT(*) AS n, MAX(alpha_median) AS max_a, MIN(alpha_median) AS min_a
        FROM mart_institution_capability
        WHERE industry_level='L1' AND alpha_median IS NOT NULL
        GROUP BY industry_code
        """
    ).fetchall()
    priors: dict[str, tuple[float, float]] = {}
    for r in rows:
        mu = float(r[1])
        # sigma_prior: 宽松，取 (max-min)/2 作为 95% CI 半幅的上限近似
        spread = (float(r[4]) - float(r[5])) / 2 if r[4] is not None and r[5] is not None else 10.0
        sigma = max(spread, 5.0)  # 最小 5pp 防过度收敛
        priors[r[0]] = (mu, sigma)
    # 无行业信息时的全局默认先验
    all_rows = conn.execute(
        "SELECT AVG(alpha_median) FROM mart_institution_capability WHERE alpha_median IS NOT NULL"
    ).fetchone()
    default_mu = float(all_rows[0]) if all_rows and all_rows[0] is not None else 0.0
    priors["_DEFAULT_"] = (default_mu, 15.0)
    return priors


# ---------------------------------------------------------------------------
# 2) 装载观测：每个 (stock, institution) 对的最新信号 + 历史 α 能力
# ---------------------------------------------------------------------------


def _load_recent_signals(
    conn: sqlite3.Connection, as_of_date: str, lookback_days: int = 180
) -> list[dict]:
    """近 N 天机构买入信号 + 其对应 capability."""
    rows = conn.execute(
        """
        SELECT
            e.institution_id, e.stock_code,
            COALESCE(e.notice_date, e.report_date) AS obs_date,
            e.event_type,
            s.tdx_l1 AS industry_l1, s.tdx_l2 AS industry_l2
        FROM fact_institution_event e
        LEFT JOIN dim_stock_tdx_industry s ON s.stock_code = e.stock_code
        WHERE e.event_type IN ('new_entry', 'increase')
          AND COALESCE(e.notice_date, e.report_date) >= ?
          AND COALESCE(e.notice_date, e.report_date) <= ?
        """,
        (_days_before(as_of_date, lookback_days), as_of_date.replace("-", "")),
    ).fetchall()
    return [dict(r) for r in rows]


def _days_before(iso_date: str, n: int) -> str:
    """as_of_date 往前 n 天（ZYYYYMMDD 格式，以匹配 fact_institution_event.notice_date）."""
    from datetime import timedelta

    iso = to_iso(iso_date) or iso_date
    dt = datetime.strptime(iso, "%Y-%m-%d") - timedelta(days=n)
    return dt.strftime("%Y%m%d")


def _load_capability_lookup(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    """{(institution_id, industry_code_L2 or L1): capability_dict}."""
    lookup: dict[tuple[str, str], dict] = {}
    rows = conn.execute(
        """
        SELECT institution_id, industry_level, industry_code,
               alpha_median, alpha_se, sample_count, expert_level, alpha_halflife_days
        FROM mart_institution_capability
        """
    ).fetchall()
    for r in rows:
        lookup[(r[0], r[2])] = {
            "level": r[1],
            "alpha_median": r[3],
            "alpha_se": r[4],
            "sample_count": r[5],
            "expert_level": r[6],
            "halflife_days": r[7],
        }
    return lookup


def _load_drift_multipliers(conn: sqlite3.Connection) -> dict[str, float]:
    """最近一次 drift_log 的置信度缩放系数."""
    rows = conn.execute(
        """
        SELECT institution_id, confidence_mult
        FROM institution_drift_log
        WHERE (institution_id, eval_date) IN (
            SELECT institution_id, MAX(eval_date) FROM institution_drift_log GROUP BY institution_id
        )
        """
    ).fetchall()
    return {r[0]: float(r[1]) for r in rows if r[1] is not None}


# ---------------------------------------------------------------------------
# 3) 共轭高斯更新
# ---------------------------------------------------------------------------


def _update_posterior(
    prior_mu: float, prior_sigma: float, observations: list[tuple[float, float]]
) -> tuple[float, float]:
    """Normal-Normal 共轭. observations = [(obs_mu, obs_sigma), ...]."""
    if prior_sigma <= 0:
        prior_sigma = 1.0
    tau_prior = 1.0 / (prior_sigma ** 2)
    tau_sum = tau_prior
    mu_sum = tau_prior * prior_mu
    for obs_mu, obs_sigma in observations:
        if obs_sigma is None or obs_sigma <= 0:
            continue
        tau = 1.0 / (obs_sigma ** 2)
        tau_sum += tau
        mu_sum += tau * obs_mu
    if tau_sum <= 0:
        return prior_mu, prior_sigma
    mu_post = mu_sum / tau_sum
    sigma_post = math.sqrt(1.0 / tau_sum)
    return mu_post, sigma_post


def _time_decay(obs_date: str, as_of_date: str, halflife_days: Optional[float]) -> float:
    """Exponential decay. 无 halflife → 不衰减."""
    if halflife_days is None or halflife_days <= 0:
        return 1.0
    obs_iso = to_iso(obs_date)
    as_iso = to_iso(as_of_date)
    if not obs_iso or not as_iso:
        return 1.0
    days = (
        datetime.strptime(as_iso, "%Y-%m-%d") - datetime.strptime(obs_iso, "%Y-%m-%d")
    ).days
    if days <= 0:
        return 1.0
    return 2.0 ** (-days / halflife_days)


# ---------------------------------------------------------------------------
# 4) 主入口
# ---------------------------------------------------------------------------


def build_bayesian_posterior(
    conn: sqlite3.Connection,
    *,
    as_of_date: Optional[str] = None,
    lookback_days: int = 180,
) -> dict:
    """Layer 3 主接口：把 (stock, as_of_date) 的后验写到 mart_bayesian_posterior."""
    _ensure_posterior_table(conn)

    if as_of_date is None:
        row = conn.execute(
            "SELECT MAX(COALESCE(notice_date, report_date)) FROM fact_institution_event"
        ).fetchone()
        raw = row[0]
        as_of_date = to_iso(raw) if raw else datetime.now().strftime("%Y-%m-%d")

    logger.info("[SEF Bayes] as_of_date=%s lookback=%dd", as_of_date, lookback_days)

    priors = _build_industry_priors(conn)
    logger.info("[SEF Bayes] 行业先验 %d", len(priors))
    cap_lookup = _load_capability_lookup(conn)
    drift_mult = _load_drift_multipliers(conn)

    signals = _load_recent_signals(conn, as_of_date, lookback_days)
    logger.info("[SEF Bayes] 近 %dd 信号 %d", lookback_days, len(signals))

    # stock → list of (obs_mu, obs_sigma, institution)
    stock_obs: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    stock_industry: dict[str, str] = {}

    for sig in signals:
        ind_l1 = sig.get("industry_l1")
        ind_l2 = sig.get("industry_l2")
        stock_industry[sig["stock_code"]] = ind_l1

        # 能力查找：先 L2，再 L1
        cap = cap_lookup.get((sig["institution_id"], ind_l2))
        if cap is None:
            cap = cap_lookup.get((sig["institution_id"], ind_l1))
        if cap is None or cap["alpha_median"] is None:
            continue

        alpha_median = float(cap["alpha_median"])
        alpha_se = cap["alpha_se"]
        if alpha_se is None or alpha_se <= 0:
            # 用 sample_count 构造 se：se ≈ 绝对值 / sqrt(n)
            n = max(cap["sample_count"] or 5, 2)
            alpha_se = abs(alpha_median) / math.sqrt(n) + 3.0

        decay = _time_decay(sig["obs_date"], as_of_date, cap.get("halflife_days"))
        conf = drift_mult.get(sig["institution_id"], 1.0)

        effective_mu = alpha_median * decay * conf
        # Uncertainty: 漂移越大 / 衰减越多 → σ 放大
        effective_sigma = alpha_se / max(decay * conf, 0.05)
        stock_obs[sig["stock_code"]].append(
            (effective_mu, effective_sigma, sig["institution_id"])
        )

    # 更新 + 写入
    conn.execute(
        "DELETE FROM mart_bayesian_posterior WHERE as_of_date=?",
        (as_of_date,),
    )
    now = datetime.utcnow().isoformat(timespec="seconds")
    written = 0

    for stock, obs_list in stock_obs.items():
        ind = stock_industry.get(stock)
        prior = priors.get(ind, priors["_DEFAULT_"])
        obs_tuples = [(m, s) for m, s, _ in obs_list]
        mu_post, sigma_post = _update_posterior(prior[0], prior[1], obs_tuples)

        # 主导机构（最大 τ 权重）
        dominant = max(obs_list, key=lambda x: 1.0 / (x[1] ** 2) if x[1] > 0 else 0, default=None)
        dom_inst = dominant[2] if dominant else None
        dom_tau = 1.0 / (dominant[1] ** 2) if dominant and dominant[1] > 0 else 0
        total_tau = 1.0 / (prior[1] ** 2) + sum(
            1.0 / (s ** 2) for _, s, _ in obs_list if s > 0
        )
        dom_weight = dom_tau / total_tau if total_tau > 0 else None

        ci_lo = mu_post - 1.645 * sigma_post
        ci_hi = mu_post + 1.645 * sigma_post

        conn.execute(
            """
            INSERT INTO mart_bayesian_posterior(
                stock_code, industry_l1, as_of_date,
                mu_prior, sigma_prior, mu_posterior, sigma_posterior,
                ci_lower_90, ci_upper_90, n_signals,
                dominant_institution, dominant_weight, last_updated
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                stock, ind, as_of_date,
                prior[0], prior[1], mu_post, sigma_post,
                ci_lo, ci_hi, len(obs_list),
                dom_inst, dom_weight, now,
            ),
        )
        written += 1

    conn.commit()

    report = {
        "as_of_date": as_of_date,
        "lookback_days": lookback_days,
        "industry_priors": len(priors) - 1,  # exclude _DEFAULT_
        "signals_loaded": len(signals),
        "stocks_with_posterior": written,
        "avg_signals_per_stock": (
            round(sum(len(v) for v in stock_obs.values()) / max(len(stock_obs), 1), 2)
        ),
    }
    logger.info("[SEF Bayes] 完成: %s", report)
    return report
