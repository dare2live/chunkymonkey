"""特征漂移监控 (PSI).

PSI = sum_i (p_recent_i - p_train_i) * ln(p_recent_i / p_train_i)
    p_train_i / p_recent_i 各分位区间在 train/recent 样本中的占比.

经验阈值:
  PSI < 0.10           — 分布稳定
  0.10 ≤ PSI < 0.25    — 轻微漂移
  PSI ≥ 0.25           — 显著漂移

写入 mart_feature_drift 给 UI Tab 5 显示.
"""
from __future__ import annotations

import logging
import math
from typing import Iterable, Optional

import numpy as np

from services.db import get_conn

logger = logging.getLogger("cm-drift")

PSI_OK_THRESHOLD = 0.10
PSI_WARN_THRESHOLD = 0.25


def compute_psi(
    train_values: Iterable[float],
    recent_values: Iterable[float],
    *,
    n_bins: int = 10,
) -> tuple[float, int, int]:
    """计算 PSI. 返回 (psi, n_train, n_recent).

    用 train_values 的分位数 (q0,q10,...,q100) 切桶, 然后比较两组在每个桶里的占比.
    """
    t = np.asarray(list(train_values), dtype=float)
    r = np.asarray(list(recent_values), dtype=float)
    t = t[np.isfinite(t)]
    r = r[np.isfinite(r)]

    n_t, n_r = len(t), len(r)
    if n_t < n_bins or n_r < n_bins:
        return float("nan"), n_t, n_r

    # 按 train 的分位数切桶
    qs = np.linspace(0.0, 1.0, n_bins + 1)
    edges = np.quantile(t, qs)
    # 微小扰动避免边界相等
    edges = np.unique(edges)
    if len(edges) < 3:
        return float("nan"), n_t, n_r

    # bin 计数
    t_counts, _ = np.histogram(t, bins=edges)
    r_counts, _ = np.histogram(r, bins=edges)

    # 防 0
    eps = 1e-6
    p_t = (t_counts + eps) / (t_counts.sum() + eps * len(edges))
    p_r = (r_counts + eps) / (r_counts.sum() + eps * len(edges))

    psi = float(np.sum((p_r - p_t) * np.log(p_r / p_t)))
    return psi, n_t, n_r


def severity_for_psi(psi: float) -> str:
    if psi is None or (isinstance(psi, float) and math.isnan(psi)):
        return "unknown"
    if psi < PSI_OK_THRESHOLD:
        return "ok"
    if psi < PSI_WARN_THRESHOLD:
        return "warn"
    return "critical"


def compute_feature_drift(
    *,
    feature_table: str = "fact_feature_panel",
    feature_columns: Optional[list[str]] = None,
    train_window_days: int = 365,
    recent_window_days: int = 30,
    model_id: Optional[str] = None,
) -> list[dict]:
    """对 feature_table 的每个数值列算 PSI.

    train_window: 历史 [today-train_window-recent, today-recent] 区间作为 train 基线
    recent_window: 最近 recent_window 天作为 recent 样本
    """
    with get_conn() as conn:
        # 拉所有数值列名
        if feature_columns is None:
            cols = conn.execute(
                f"SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE table_name = '{feature_table}' "
                f"  AND data_type IN ('DOUBLE','FLOAT','REAL','BIGINT','INTEGER')"
            ).fetchall()
            feature_columns = [c[0] for c in cols if not c[0].startswith(('stock_', 'date', 'snapshot'))]

        # 找日期列
        date_col_row = conn.execute(
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{feature_table}' "
            f"  AND column_name IN ('trade_date','date','snapshot_date','as_of_date') "
            f"LIMIT 1"
        ).fetchone()
        if not date_col_row:
            logger.warning(f"[drift] {feature_table} 没有可识别的日期列, 跳过")
            return []
        date_col = date_col_row[0]

        results = []
        for col in feature_columns:
            try:
                # train 段
                t_rows = conn.execute(f"""
                    SELECT {col} FROM {feature_table}
                     WHERE {date_col} BETWEEN
                           strftime(now() - INTERVAL ({train_window_days + recent_window_days}) DAY, '%Y-%m-%d')
                       AND strftime(now() - INTERVAL ({recent_window_days}) DAY, '%Y-%m-%d')
                       AND {col} IS NOT NULL
                """).fetchall()
                # recent 段
                r_rows = conn.execute(f"""
                    SELECT {col} FROM {feature_table}
                     WHERE {date_col} >= strftime(now() - INTERVAL ({recent_window_days}) DAY, '%Y-%m-%d')
                       AND {col} IS NOT NULL
                """).fetchall()
                t_vals = [r[0] for r in t_rows]
                r_vals = [r[0] for r in r_rows]
                psi, n_t, n_r = compute_psi(t_vals, r_vals)
                severity = severity_for_psi(psi)
                results.append({
                    "feature": col, "psi": psi,
                    "n_train": n_t, "n_recent": n_r,
                    "severity": severity, "model_id": model_id,
                })
            except Exception as exc:
                logger.warning(f"[drift] {col} 失败: {exc}")
                continue

        return results


def write_drift_snapshot(
    drift_results: list[dict],
    *,
    snapshot_at: Optional[str] = None,
    window_days: int = 30,
) -> int:
    """把 compute_feature_drift 结果写入 mart_feature_drift."""
    if not drift_results:
        return 0
    with get_conn() as conn:
        # 用 SQL 端的 now() 保证 snapshot_at 一致
        if snapshot_at is None:
            snapshot_at = conn.execute("SELECT now()").fetchone()[0]
        for r in drift_results:
            conn.execute("""
                INSERT INTO mart_feature_drift
                  (snapshot_at, model_id, feature, psi, n_train, n_recent, window_days, severity, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT (snapshot_at, model_id, feature) DO UPDATE SET
                    psi = EXCLUDED.psi,
                    n_train = EXCLUDED.n_train,
                    n_recent = EXCLUDED.n_recent,
                    window_days = EXCLUDED.window_days,
                    severity = EXCLUDED.severity
            """, (
                snapshot_at, r.get("model_id"), r["feature"],
                None if (r["psi"] != r["psi"]) else r["psi"],  # 过滤 NaN
                r["n_train"], r["n_recent"], window_days, r["severity"],
            ))
        conn.commit()
    return len(drift_results)
