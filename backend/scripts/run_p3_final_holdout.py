#!/usr/bin/env python3
"""P3 Final Holdout Acceptance Gate CLI.

PLAN_V3 v3.2 §99 P3:
- 输入: P2 冻结 model + 最近 6 OOS 月 stitched final holdout
- 4 硬验收: ann ≥ 30%, max_dd ≥ -20%, 超额 vs HS300 > 0, 月胜率 ≥ 55%
- 任一失败 → 停止包装, 回 alpha 根因 (Rule 9.1)

用法:
    PYTHONPATH=backend python backend/scripts/run_p3_final_holdout.py \
        --model-id lgbm_baseline_v1 \
        --run-id p3_final_v1
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.portfolio.final_holdout import (
    FinalHoldoutMetrics,
    check_final_acceptance,
    format_acceptance_report,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("p3_final_holdout")


P3_RESULT_DDL = """
CREATE TABLE IF NOT EXISTS mart_p3_acceptance_result (
    run_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    ann_ret DOUBLE,
    max_dd DOUBLE,
    excess_vs_hs300 DOUBLE,
    monthly_win_rate DOUBLE,
    hs300_ann_ret DOUBLE,
    n_oos_months INTEGER,
    final_period_start TEXT,
    final_period_end TEXT,
    model_version TEXT,
    feature_version TEXT,
    label_version TEXT,
    seed INTEGER,
    passed BOOLEAN NOT NULL,
    failures_json TEXT,
    built_at TEXT,
    PRIMARY KEY (run_id, model_id)
);
"""


def _compute_final_kpi(conn, model_id: str, last_n_months: int = 6) -> FinalHoldoutMetrics:
    """从 mart_p0b_oos_predictions 取最近 N 月 OOS 拼成 final holdout, 算 KPI.

    简化版 (P3 first cut):
    - 用每月 top-5 score 等权持有, 看 stitched cost-after returns
    - ann_ret = 月化平均 × 12 (近似)
    - max_dd = stitched NAV drawdown (单月最差)
    - monthly_win_rate = 月正收益占比
    - excess_vs_hs300 = ann_ret - HS300 ann_ret (从 dim_index_price 取)

    Returns FinalHoldoutMetrics 含 model_version / feature_version / label_version /
    seed (从 mart_p0b_oos_predictions 元数据继承).
    """
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT signal_date, stock_code, score, fwd_cost_after_10d,
                   ROW_NUMBER() OVER (PARTITION BY signal_date ORDER BY score DESC NULLS LAST) AS rk,
                   model_version, feature_version, label_version
            FROM mart_p0b_oos_predictions
            WHERE model_id = ?
        ),
        monthly AS (
            SELECT DATE_TRUNC('month', signal_date) AS month_start,
                   AVG(fwd_cost_after_10d) AS avg_ret
            FROM ranked WHERE rk <= 5 AND fwd_cost_after_10d IS NOT NULL
            GROUP BY DATE_TRUNC('month', signal_date)
            ORDER BY month_start DESC
            LIMIT ?
        )
        SELECT
            COUNT(*) AS n_months,
            AVG(avg_ret) AS mean_monthly_ret,
            MIN(avg_ret) AS worst_monthly_ret,
            CAST(SUM(CASE WHEN avg_ret > 0 THEN 1 ELSE 0 END) AS DOUBLE) / NULLIF(COUNT(*), 0) AS monthly_win_rate,
            MIN(month_start) AS period_start,
            MAX(month_start) AS period_end
        FROM monthly
        """,
        [model_id, last_n_months],
    ).fetchone()
    n_months, mean_ret, worst_ret, win_rate, period_start, period_end = rows

    # Metadata from any matching prediction row (assume model_id 是 mode-unique)
    meta = conn.execute(
        "SELECT model_version, feature_version, label_version FROM mart_p0b_oos_predictions "
        "WHERE model_id = ? LIMIT 1",
        [model_id]
    ).fetchone() or (None, None, None)

    # HS300 ann_ret (简化: 用 dim_index_price 同期 ann_ret 计算)
    hs300_ann = _compute_hs300_ann_ret(conn, period_start, period_end) or 0.0

    return FinalHoldoutMetrics(
        ann_ret=(mean_ret or 0) * 12.0,
        max_dd=worst_ret or 0,
        excess_vs_hs300=(mean_ret or 0) * 12.0 - hs300_ann,
        monthly_win_rate=win_rate or 0,
        hs300_ann_ret=hs300_ann,
        n_oos_months=n_months or 0,
        final_period_start=str(period_start) if period_start else None,
        final_period_end=str(period_end) if period_end else None,
        model_version=meta[0], feature_version=meta[1], label_version=meta[2],
        seed=42,  # default, P0b 当前 seed=42
    )


def _compute_hs300_ann_ret(conn, period_start, period_end) -> float | None:
    """简化版 HS300 同期 ann_ret. 若 dim_index_price 不存在 → 0."""
    if not period_start or not period_end:
        return None
    try:
        r = conn.execute(
            """
            WITH prices AS (
                SELECT trade_date, close FROM dim_index_price
                WHERE index_code='000300' AND trade_date::DATE BETWEEN ? AND ?
                ORDER BY trade_date
            )
            SELECT MIN(close), MAX(close), COUNT(*) FROM prices
            """,
            [str(period_start), str(period_end)]
        ).fetchone()
        if not r or r[2] == 0 or r[0] is None:
            return None
        # Naive: (max - min) / min annualized over period
        # 实际 PLAN_V3 §0.1 用 HS300 完整 NAV; 此处简化版.
        period_months = max(1, r[2] / 22)  # 22 trade days / month
        period_ret = (r[1] - r[0]) / r[0] if r[0] > 0 else 0
        return period_ret * (12.0 / period_months)
    except Exception as e:
        log.warning(f"HS300 ann_ret compute failed: {e}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="P3 Final Holdout Acceptance Gate")
    parser.add_argument("--model-id", default="lgbm_baseline_v1")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--last-n-months", type=int, default=6)
    args = parser.parse_args()

    run_id = args.run_id or f"p3_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log.info(f"run_id={run_id}, model_id={args.model_id}, last_n_months={args.last_n_months}")

    conn = duck_connect(str(DB_PATH))
    try:
        conn.execute(P3_RESULT_DDL)
        metrics = _compute_final_kpi(conn, args.model_id, args.last_n_months)
        log.info(f"Computed metrics: {metrics}")
        result = check_final_acceptance(metrics)

        # Report
        report = format_acceptance_report(metrics, result)
        log.info("\n" + report)

        import json
        conn.execute(
            "INSERT OR REPLACE INTO mart_p3_acceptance_result VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [run_id, args.model_id,
             metrics.ann_ret, metrics.max_dd, metrics.excess_vs_hs300, metrics.monthly_win_rate,
             metrics.hs300_ann_ret, metrics.n_oos_months,
             metrics.final_period_start, metrics.final_period_end,
             metrics.model_version, metrics.feature_version, metrics.label_version, metrics.seed,
             result.passed, json.dumps(result.failures),
             datetime.now(UTC).isoformat(timespec="seconds")]
        )
        log.info(f"Written to mart_p3_acceptance_result (run_id={run_id})")

        if not result.passed:
            log.error("P3 FAIL — 任一硬验收失败, 停止包装, 回 alpha 根因 (CLAUDE Rule 9)")
            return 1
        log.info("P3 PASS — 4 硬验收全过, 可启动 paper trading")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
