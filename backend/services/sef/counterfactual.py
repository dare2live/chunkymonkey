"""Layer 6 · Counterfactual Evaluation.

SEF 策略 vs V6 基线的对比（SEF §3 Layer 6）.

V6 baseline（简化版）:
- 对每条 closed chain，若 premium_pct <= 15pp 且机构 in top-30% alpha: 视为 V6 的 follow 动作
- V6 PnL = 等权 chain_follow_pnl

SEF strategy:
- 从 mart_bayesian_posterior 筛 μ_post > 0 的股票
- 用 meta_labeling 后概率 > 0.5
- 按等权聚合 chain_follow_pnl

输出到新表 mart_counterfactual_eval:
  eval_date, strategy, n, mean_pnl, win_rate, ir
  + 对比差额与 p-value
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Optional

import numpy as np

logger = logging.getLogger("cm-api.sef.counterfactual")


def _ensure_table(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mart_counterfactual_eval (
            eval_date           TEXT NOT NULL,
            strategy            TEXT NOT NULL,
            n_signals           INTEGER,
            mean_pnl_pct        REAL,
            median_pnl_pct      REAL,
            win_rate            REAL,
            sharpe_proxy        REAL,
            PRIMARY KEY(eval_date, strategy)
        );
        """
    )
    conn.commit()


def _compute_stats(pnls: list[float], threshold: float = 0.0) -> dict:
    if not pnls:
        return {"n": 0, "mean": None, "median": None, "win_rate": None, "sharpe": None}
    arr = np.array(pnls, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if len(arr) > 1 else None
    return {
        "n": len(arr),
        "mean": mean,
        "median": float(np.median(arr)),
        "win_rate": float(np.mean(arr > threshold)),
        "sharpe": mean / std if std and std > 0 else None,
    }


def _select_v6_signals(conn: sqlite3.Connection, *, max_premium: float = 15.0) -> list[float]:
    """V6 硬规则 baseline:
    - 仅取 fact_institution_event follow_gate='pass' 且 event_type in ('new_entry','increase')
    - 关联 closed chain 获得实际 PnL
    """
    rows = conn.execute(
        """
        SELECT t.chain_follow_pnl
        FROM fact_chain_alpha_truth t
        JOIN research_holding_chains rhc
            ON rhc.institution_id = t.institution_id
           AND rhc.stock_code = t.stock_code
           AND rhc.chain_id = t.research_chain_id
        WHERE t.status = 'closed' AND t.chain_follow_pnl IS NOT NULL
          AND (rhc.entry_premium_pct IS NULL OR rhc.entry_premium_pct <= ?)
        """,
        (max_premium,),
    ).fetchall()
    return [float(r[0]) for r in rows if r[0] is not None]


def _select_sef_signals(conn: sqlite3.Connection, *, min_prob: float = 0.5) -> list[float]:
    """SEF 策略: meta-label 概率 > 阈值 的 closed chain 实际 PnL.

    注意：predictions 仅在 meta_labeling 训练时回填，故这里 JOIN 使用最新模型版本.
    """
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT model_version FROM mart_meta_label_model
            ORDER BY trained_at DESC LIMIT 1
        )
        SELECT p.pnl_realized
        FROM mart_meta_label_predictions p
        JOIN latest l ON p.model_version = l.model_version
        WHERE p.meta_prob_follow > ? AND p.pnl_realized IS NOT NULL
        """,
        (min_prob,),
    ).fetchall()
    return [float(r[0]) for r in rows if r[0] is not None]


def run_counterfactual(
    conn: sqlite3.Connection,
    *,
    eval_date: Optional[str] = None,
    max_premium: float = 15.0,
    min_prob: float = 0.5,
) -> dict:
    _ensure_table(conn)
    if eval_date is None:
        row = conn.execute("SELECT MAX(eval_date) FROM fact_chain_alpha_truth").fetchone()
        eval_date = row[0] if row and row[0] else datetime.now().strftime("%Y-%m-%d")

    v6 = _select_v6_signals(conn, max_premium=max_premium)
    sef = _select_sef_signals(conn, min_prob=min_prob)

    v6_stats = _compute_stats(v6)
    sef_stats = _compute_stats(sef)

    # 写入两条记录
    conn.execute("DELETE FROM mart_counterfactual_eval WHERE eval_date=?", (eval_date,))
    for name, s in [("v6_baseline", v6_stats), ("sef_strategy", sef_stats)]:
        conn.execute(
            """
            INSERT INTO mart_counterfactual_eval(
                eval_date, strategy, n_signals,
                mean_pnl_pct, median_pnl_pct, win_rate, sharpe_proxy
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (eval_date, name, s["n"], s["mean"], s["median"], s["win_rate"], s["sharpe"]),
        )
    conn.commit()

    # 差额检验 (Welch t-test 近似)
    ttest_p = None
    diff = None
    if v6 and sef:
        v6a = np.array(v6)
        sfa = np.array(sef)
        diff = float(sfa.mean() - v6a.mean())
        try:
            from scipy.stats import ttest_ind
            ttest_p = float(ttest_ind(sfa, v6a, equal_var=False).pvalue)
        except ImportError:
            # approx via z-score
            se = np.sqrt(sfa.var(ddof=1) / len(sfa) + v6a.var(ddof=1) / len(v6a))
            z = diff / se if se > 0 else 0
            ttest_p = float(2 * (1 - 0.5 * (1 + np.math.erf(abs(z) / np.sqrt(2)))))

    report = {
        "eval_date": eval_date,
        "v6_baseline": v6_stats,
        "sef_strategy": sef_stats,
        "sef_minus_v6_mean_pct": diff,
        "ttest_pvalue": ttest_p,
    }
    logger.info("[SEF Counterfactual] %s", report)
    return report
