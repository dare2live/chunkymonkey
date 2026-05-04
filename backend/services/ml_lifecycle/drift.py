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

from services.db import get_conn

logger = logging.getLogger("cm-drift")

PSI_OK_THRESHOLD = 0.10
PSI_WARN_THRESHOLD = 0.25


def _finite_values(values: Iterable[float]) -> list[float]:
    out = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            out.append(number)
    return out


def _linear_quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return sorted_values[int(pos)]
    weight = pos - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _unique_sorted(values: Iterable[float]) -> list[float]:
    out = []
    previous = None
    for value in values:
        if previous is None or value != previous:
            out.append(value)
            previous = value
    return out


def _histogram(values: list[float], edges: list[float]) -> list[int]:
    counts = [0 for _ in range(len(edges) - 1)]
    if not counts:
        return counts
    first = edges[0]
    last = edges[-1]
    for value in values:
        if value < first or value > last:
            continue
        for idx in range(len(edges) - 1):
            left = edges[idx]
            right = edges[idx + 1]
            is_last_bin = idx == len(edges) - 2
            if left <= value < right or (is_last_bin and value == right):
                counts[idx] += 1
                break
    return counts


def compute_psi(
    train_values: Iterable[float],
    recent_values: Iterable[float],
    *,
    n_bins: int = 10,
) -> tuple[float, int, int]:
    """计算 PSI. 返回 (psi, n_train, n_recent).

    用 train_values 的分位数 (q0,q10,...,q100) 切桶, 然后比较两组在每个桶里的占比.
    """
    t = _finite_values(train_values)
    r = _finite_values(recent_values)

    n_t, n_r = len(t), len(r)
    if n_t < n_bins or n_r < n_bins:
        return float("nan"), n_t, n_r

    # 按 train 的分位数切桶
    sorted_train = sorted(t)
    qs = [idx / n_bins for idx in range(n_bins + 1)]
    edges = [_linear_quantile(sorted_train, q) for q in qs]
    # 微小扰动避免边界相等
    edges = _unique_sorted(edges)
    if len(edges) < 3:
        return float("nan"), n_t, n_r

    # bin 计数
    t_counts = _histogram(t, edges)
    r_counts = _histogram(r, edges)

    # 防 0
    eps = 1e-6
    t_total = sum(t_counts)
    r_total = sum(r_counts)
    p_t = [(count + eps) / (t_total + eps * len(edges)) for count in t_counts]
    p_r = [(count + eps) / (r_total + eps * len(edges)) for count in r_counts]

    psi = sum((recent - train) * math.log(recent / train) for train, recent in zip(p_t, p_r))
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
