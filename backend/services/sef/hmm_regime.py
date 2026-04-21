"""HMM Regime Detection · Hamilton 1989 (Layer 0 补充).

2-3 state Gaussian HMM over 大盘日收益 + 波动率。
A 股经验：state 0 = 熊 / 1 = 震荡 / 2 = 牛，平均持续 ~9 个月。

Feature:
- daily_ret: 等权全市场日收益
- vol_20d: 滚动 20 日波动率

写入 fact_regime_state: {trade_date, regime_id, regime_label, regime_prob_json, transition_signal}
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

import numpy as np

from ._dates import to_iso

logger = logging.getLogger("cm-api.sef.hmm_regime")


def _load_market_series(mkt_conn: sqlite3.Connection) -> list[tuple[str, float]]:
    """等权全市场日收益率."""
    rows = mkt_conn.execute(
        """
        WITH daily_ret AS (
            SELECT code, date,
                   close / LAG(close) OVER (PARTITION BY code ORDER BY date) - 1 AS ret
            FROM price_kline
            WHERE freq='daily' AND adjust='qfq'
        )
        SELECT date, AVG(ret) FROM daily_ret WHERE ret IS NOT NULL
        GROUP BY date ORDER BY date
        """
    ).fetchall()
    return [(to_iso(r[0]), float(r[1])) for r in rows if r[1] is not None]


def _label_regimes_by_mean(means: np.ndarray) -> list[str]:
    """按各 state mean 收益排序，从低到高依次为 bear/sideways/bull."""
    order = np.argsort(means)
    labels = ["?"] * len(means)
    if len(means) == 2:
        labels[order[0]] = "bear"
        labels[order[1]] = "bull"
    elif len(means) == 3:
        labels[order[0]] = "bear"
        labels[order[1]] = "sideways"
        labels[order[2]] = "bull"
    else:
        for i, idx in enumerate(order):
            labels[idx] = f"state{i}"
    return labels


def build_regime_state(
    mkt_conn: sqlite3.Connection,
    conn: sqlite3.Connection,
    *,
    n_states: int = 3,
    vol_window: int = 20,
    random_state: int = 42,
) -> dict:
    """拟合 HMM，写入 fact_regime_state (全部历史日)."""
    try:
        from hmmlearn import hmm
    except ImportError:
        return {"error": "hmmlearn not installed"}

    series = _load_market_series(mkt_conn)
    if len(series) < 200:
        return {"error": f"need >= 200 days, got {len(series)}"}

    dates = [s[0] for s in series]
    rets = np.array([s[1] for s in series], dtype=float)

    # 计算滚动波动率
    vols = np.full_like(rets, np.nan)
    for i in range(vol_window, len(rets)):
        vols[i] = float(np.std(rets[i - vol_window : i], ddof=1))
    # 丢弃起始 vol_window 天
    valid = slice(vol_window, None)
    X = np.column_stack([rets[valid], vols[valid]])
    valid_dates = dates[vol_window:]

    # 拟合 HMM
    model = hmm.GaussianHMM(
        n_components=n_states,
        covariance_type="full",
        n_iter=100,
        random_state=random_state,
    )
    try:
        model.fit(X)
    except Exception as e:  # noqa: BLE001
        return {"error": f"hmm fit failed: {e}"}

    states = model.predict(X)
    probs = model.predict_proba(X)
    labels = _label_regimes_by_mean(model.means_[:, 0])

    # 写入
    conn.execute("DELETE FROM fact_regime_state")
    for i, d in enumerate(valid_dates):
        sid = int(states[i])
        prob_list = [float(x) for x in probs[i]]
        # 转移信号: 非主态概率 > 40%
        main_prob = max(prob_list)
        transition = 1 if main_prob < 0.6 else 0
        conn.execute(
            """
            INSERT INTO fact_regime_state(
                trade_date, regime_id, regime_label, regime_prob_json, transition_signal
            ) VALUES(?,?,?,?,?)
            """,
            (d, sid, labels[sid], json.dumps(prob_list), transition),
        )
    conn.commit()

    # 统计
    unique, counts = np.unique(states, return_counts=True)
    state_summary = {
        labels[int(s)]: {
            "count": int(c),
            "mean_ret": float(model.means_[int(s), 0]),
            "mean_vol": float(model.means_[int(s), 1]),
        }
        for s, c in zip(unique, counts)
    }
    report = {
        "n_states": n_states,
        "fit_days": len(valid_dates),
        "date_range": [valid_dates[0], valid_dates[-1]],
        "state_summary": state_summary,
        "transmat": model.transmat_.tolist(),
    }
    logger.info("[SEF HMM] 完成: %s", state_summary)
    return report
