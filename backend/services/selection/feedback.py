"""Phase ε — 反馈环: signal IC → formula weight。

读 mart_signal_ic (Phase δ) 最近 60 日, 算每公式 raw_score → softmax → clip → hysteresis →
写 mart_formula_weight_history。

注: Phase ε 只产权重表; 是否真在 daily-topk 用 (consumer 接入), 留 Phase ε.5 / Phase ζ stretch。
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date as _date, timedelta
from typing import Iterable


log = logging.getLogger("selection.feedback")


# Softmax 温度: 越高越平 (1.0 = 强分化, 5.0 = 接近均匀)
TEMPERATURE = 2.0
# 单公式权重上下限
WEIGHT_MIN = 0.02
WEIGHT_MAX = 0.40
# 历史 hysteresis: w_new = 0.7×w_prev + 0.3×w_today (避免日变化太大)
HYSTERESIS = 0.7


def _softmax(scores: list[float], temperature: float = 1.0) -> list[float]:
    """温度 softmax. 全 0 score 返回均匀分布。"""
    if not scores:
        return []
    if temperature <= 0:
        temperature = 1.0
    # 数值稳定: subtract max
    scaled = [s / temperature for s in scores]
    m = max(scaled)
    exps = [math.exp(s - m) for s in scaled]
    total = sum(exps)
    if total <= 0:
        return [1.0 / len(scores)] * len(scores)
    return [e / total for e in exps]


def _clip_and_renormalize(weights: list[float], lo: float, hi: float) -> list[float]:
    """每个权重 clip 到 [lo, hi], 然后整体 renormalize 到 sum=1。"""
    clipped = [min(max(w, lo), hi) for w in weights]
    total = sum(clipped)
    if total <= 0:
        return [1.0 / len(weights)] * len(weights)
    return [w / total for w in clipped]


def derive_formula_weights(
    conn,
    asof_date: str,
    ic_window_days: int = 60,
) -> int:
    """读 mart_signal_ic, 派生权重 + 写 mart_formula_weight_history。

    Returns:
        写入行数
    """
    t0 = time.time()
    cutoff = (_date.fromisoformat(asof_date) - timedelta(days=ic_window_days)).isoformat()

    # 1. 聚合 IC by (formula_id, variant)
    ic_rows = conn.execute(
        """
        SELECT formula_id,
               COALESCE(formula_variant, formula_id) AS variant,
               -- 加权平均 (w 5d=0.2, 10d=0.5, 30d=0.3)
               AVG(ic_5d) AS mean_ic_5d,
               AVG(ic_10d) AS mean_ic_10d,
               AVG(ic_30d) AS mean_ic_30d,
               SUM(n_signals) AS n_obs,
               COUNT(*) AS n_dates
          FROM mart_signal_ic
         WHERE snapshot_date >= ? AND snapshot_date <= ?
         GROUP BY formula_id, COALESCE(formula_variant, formula_id)
        """,
        [cutoff, asof_date],
    ).fetchall()
    if not ic_rows:
        log.warning("  无 mart_signal_ic 数据")
        return 0

    log.info(f"  IC 行 {len(ic_rows)} 组合, window={ic_window_days}d")

    # 2. 算 raw_score: weighted_ic × sqrt(n_obs)
    formulas = []
    raw_scores = []
    for r in ic_rows:
        fid, fvar, ic5, ic10, ic30, n_obs, _ = r
        w_ic = (
            (0.2 * (ic5 if ic5 is not None else 0)) +
            (0.5 * (ic10 if ic10 is not None else 0)) +
            (0.3 * (ic30 if ic30 is not None else 0))
        )
        # variance-adjusted: 多信号公式更有信心
        n = max(1, int(n_obs or 0))
        score = w_ic * math.sqrt(n)
        # 负 IC: 仍允许进 softmax, 但 clip 后下限保护
        formulas.append((fid, fvar, ic5, ic10, ic30, n))
        raw_scores.append(score)

    # 3. softmax + clip + renormalize
    weights_raw = _softmax(raw_scores, TEMPERATURE)
    weights = _clip_and_renormalize(weights_raw, WEIGHT_MIN, WEIGHT_MAX)

    # 4. Hysteresis: 读昨日权重
    prev = conn.execute(
        """
        SELECT formula_id, formula_variant, weight
          FROM mart_formula_weight_history
         WHERE snapshot_date < ?
         ORDER BY snapshot_date DESC LIMIT ?
        """,
        [asof_date, len(formulas)],
    ).fetchall()
    prev_by_key = {(r[0], r[1]): float(r[2]) for r in prev} if prev else {}

    final_weights = []
    for i, (fid, fvar, _, _, _, _) in enumerate(formulas):
        w_new = weights[i]
        w_prev = prev_by_key.get((fid, fvar), w_new)  # 首日无 prev: 用 today
        w_final = HYSTERESIS * w_prev + (1 - HYSTERESIS) * w_new
        final_weights.append(w_final)
    # renormalize (hysteresis 可能使 sum ≠ 1)
    total = sum(final_weights)
    if total > 0:
        final_weights = [w / total for w in final_weights]

    # 5. 落库
    out_rows = []
    for i, (fid, fvar, ic5, ic10, ic30, n_obs) in enumerate(formulas):
        out_rows.append((
            fid, fvar, asof_date,
            float(final_weights[i]),
            float(ic5) if ic5 is not None else None,
            float(ic10) if ic10 is not None else None,
            n_obs,
            True,
        ))

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "DELETE FROM mart_formula_weight_history WHERE snapshot_date = ?",
            [asof_date],
        )
        conn.executemany(
            """INSERT INTO mart_formula_weight_history
               (formula_id, formula_variant, snapshot_date,
                weight, rolling_ic_30d, rolling_ic_60d, n_obs, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            out_rows,
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    log.info(f"完成: {len(out_rows)} 公式权重 (耗时 {time.time()-t0:.1f}s)")
    return len(out_rows)
