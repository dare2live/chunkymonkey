"""Layer 6 · Institution Drift Monitor.

每周/每月触发. 对每个机构:
- 近 6 个月 chain α 分布 vs 历史 24 个月分布
- KS 2-sample test p-value
- PSI (Population Stability Index, 10 bins)

分级阈值:
- PSI > 0.25 or KS p < 0.05 → severe, confidence_mult = 0.3
- PSI > 0.10              → mild,   confidence_mult = 0.7
- else                   → stable, confidence_mult = 1.0

输出到已有表 institution_drift_log.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from ._dates import to_iso

logger = logging.getLogger("cm-api.sef.drift")


def _ks_two_sample_p(a: np.ndarray, b: np.ndarray) -> float:
    """Scipy ks_2samp 的 p-value（若无 scipy 则回退到简化的 D*sqrt(n) 近似）."""
    try:
        from scipy.stats import ks_2samp
        return float(ks_2samp(a, b).pvalue)
    except ImportError:
        if len(a) < 2 or len(b) < 2:
            return 1.0
        # Approximate via empirical CDF max gap
        n1, n2 = len(a), len(b)
        combined = np.concatenate([a, b])
        a_sorted = np.sort(a)
        b_sorted = np.sort(b)
        pivots = np.sort(combined)
        cdf_a = np.searchsorted(a_sorted, pivots, side="right") / n1
        cdf_b = np.searchsorted(b_sorted, pivots, side="right") / n2
        D = float(np.max(np.abs(cdf_a - cdf_b)))
        # 近似 p-value
        nn = n1 * n2 / (n1 + n2)
        z = D * np.sqrt(nn)
        return float(np.exp(-2.0 * z ** 2))


def _psi(recent: np.ndarray, historical: np.ndarray, n_bins: int = 10) -> float:
    """Population Stability Index."""
    if len(recent) < n_bins or len(historical) < n_bins:
        return 0.0
    bins = np.quantile(historical, np.linspace(0, 1, n_bins + 1))
    bins = np.unique(bins)
    if len(bins) < 3:
        return 0.0
    r_hist, _ = np.histogram(recent, bins=bins)
    h_hist, _ = np.histogram(historical, bins=bins)
    r_pct = np.clip(r_hist / max(len(recent), 1), 1e-6, None)
    h_pct = np.clip(h_hist / max(len(historical), 1), 1e-6, None)
    return float(np.sum((r_pct - h_pct) * np.log(r_pct / h_pct)))


def _classify(psi: float, ks_p: float) -> tuple[str, float]:
    if psi > 0.25 or ks_p < 0.05:
        return "severe", 0.3
    if psi > 0.10:
        return "mild", 0.7
    return "stable", 1.0


def run_drift_monitor(
    conn: sqlite3.Connection,
    *,
    eval_date: Optional[str] = None,
    recent_months: int = 6,
    history_months: int = 24,
    min_samples: int = 10,
) -> dict:
    """对 fact_chain_alpha_truth 里每个机构计算 PSI + KS, 写入 institution_drift_log."""
    if eval_date is None:
        row = conn.execute("SELECT MAX(eval_date) FROM fact_chain_alpha_truth").fetchone()
        eval_date = row[0] if row and row[0] else datetime.now().strftime("%Y-%m-%d")
    eval_iso = to_iso(eval_date) or eval_date

    recent_start = (datetime.strptime(eval_iso, "%Y-%m-%d") - timedelta(days=recent_months * 30)).strftime("%Y-%m-%d")
    history_start = (datetime.strptime(eval_iso, "%Y-%m-%d") - timedelta(days=history_months * 30)).strftime("%Y-%m-%d")

    logger.info(
        "[SEF Drift] eval=%s recent>=%s hist 范围 [%s, %s)",
        eval_iso, recent_start, history_start, recent_start,
    )

    # 拉每个机构的 chain α 序列
    rows = conn.execute(
        """
        SELECT institution_id, entry_date, chain_follow_pnl
        FROM fact_chain_alpha_truth
        WHERE chain_follow_pnl IS NOT NULL AND entry_date >= ?
        """,
        (history_start,),
    ).fetchall()

    # 按机构分组
    from collections import defaultdict

    inst_recent: dict[str, list[float]] = defaultdict(list)
    inst_hist: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        inst, entry, pnl = r[0], to_iso(r[1]), float(r[2])
        if not entry:
            continue
        if entry >= recent_start:
            inst_recent[inst].append(pnl)
        else:
            inst_hist[inst].append(pnl)

    # 计算 + 写入
    # 幂等：清本次 eval_date 的记录再写
    conn.execute("DELETE FROM institution_drift_log WHERE eval_date=?", (eval_iso,))

    written = 0
    severe_cnt = mild_cnt = stable_cnt = 0
    skipped_small = 0
    insts = set(inst_recent) | set(inst_hist)
    for inst in insts:
        r = np.array(inst_recent.get(inst, []), dtype=float)
        h = np.array(inst_hist.get(inst, []), dtype=float)
        if len(r) < min_samples or len(h) < min_samples:
            skipped_small += 1
            continue
        psi = _psi(r, h)
        ks_p = _ks_two_sample_p(r, h)
        level, conf = _classify(psi, ks_p)
        conn.execute(
            """
            INSERT INTO institution_drift_log(
                institution_id, eval_date, psi, ks_pvalue,
                confidence_mult, alert_level
            ) VALUES(?,?,?,?,?,?)
            """,
            (inst, eval_iso, psi, ks_p, conf, level),
        )
        written += 1
        if level == "severe":
            severe_cnt += 1
        elif level == "mild":
            mild_cnt += 1
        else:
            stable_cnt += 1
    conn.commit()

    report = {
        "eval_date": eval_iso,
        "institutions_total": len(insts),
        "written": written,
        "skipped_small_sample": skipped_small,
        "severe": severe_cnt,
        "mild": mild_cnt,
        "stable": stable_cnt,
    }
    logger.info("[SEF Drift] %s", report)
    return report
