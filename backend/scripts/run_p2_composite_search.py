#!/usr/bin/env python3
"""P2 composite scoring weight search CLI.

读 mart_p0b_oos_predictions → 按 model_id 聚合每月 OOS metrics → grid/Optuna 搜
composite weights → 写 mart_p2_composite_result + 报告 best weights.

PLAN_V3 §2 P2: "权重 由 validation grid/Optuna 决定, 不预设最终权重".

简化版 (P2 first cut): grid search 5×5 = 25 组合, 找 Top 5 composite. P2.b 接 Optuna.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.portfolio.composite_score import (
    CompositeWeights,
    HpPenaltyMode,
    compute_composite_score,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("p2_composite")


P2_RESULT_DDL = """
CREATE TABLE IF NOT EXISTS mart_p2_composite_result (
    run_id TEXT NOT NULL,
    grid_idx INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    ret_w DOUBLE, dd_w DOUBLE, hp_w DOUBLE,
    turnover_w DOUBLE, cost_w DOUBLE, capacity_w DOUBLE,
    hp_penalty_mode TEXT,
    ann_ret DOUBLE, max_dd DOUBLE,
    avg_hp DOUBLE, turnover DOUBLE, tx_cost_pct DOUBLE,
    concentration DOUBLE,
    composite_score DOUBLE NOT NULL,
    built_at TEXT,
    PRIMARY KEY (run_id, grid_idx)
);
"""


def estimate_kpi_from_predictions(conn, model_id: str) -> dict:
    """从 mart_p0b_oos_predictions 简化估算 ann_ret / max_dd / turnover / cost.

    简化 (P2 first cut):
    - 每月 OOS 选 top-K (默认 5) by score → 等权持有 → 看 avg fwd_cost_after_10d
    - ann_ret = avg monthly return × 12 (近似)
    - max_dd = stitched OOS 累计 NAV 最大回撤
    - turnover ≈ 1.0 × n_months / 持有 hp (假设每月换一次)
    - tx_cost_pct ≈ round_trip × turnover (paper_sim 已扣)
    """
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT signal_date, stock_code, score, fwd_cost_after_10d,
                   ROW_NUMBER() OVER (PARTITION BY signal_date ORDER BY score DESC) AS rk
            FROM mart_p0b_oos_predictions
            WHERE model_id = ?
        ),
        monthly AS (
            SELECT DATE_TRUNC('month', signal_date) AS month_start,
                   AVG(fwd_cost_after_10d) AS avg_ret
            FROM ranked WHERE rk <= 5 AND fwd_cost_after_10d IS NOT NULL
            GROUP BY DATE_TRUNC('month', signal_date)
        )
        SELECT
            COUNT(*) AS n_months,
            AVG(avg_ret) AS mean_monthly_ret,
            MIN(avg_ret) AS min_monthly_ret,
            STDDEV(avg_ret) AS std_monthly_ret
        FROM monthly
        """,
        [model_id],
    ).fetchone()
    if not rows or rows[0] == 0:
        log.warning(f"No predictions for model_id={model_id}")
        return {"ann_ret": 0.0, "max_dd": 0.0, "avg_hp": 10.0,
                "turnover": 0.0, "tx_cost_pct": 0.0, "concentration": 0.0}
    n_months, mean_ret, min_ret, std_ret = rows
    return {
        "ann_ret": (mean_ret or 0) * 12.0,
        "max_dd": min_ret or 0,  # 简化: 单月最差作 proxy max_dd
        "avg_hp": 10.0,
        "turnover": 12.0 / 10.0 * 2,  # 每月换一次 × 2 (买卖)
        "tx_cost_pct": 0.00302 * 12.0 / 10.0 * 2,  # 跟 turnover 联动
        "concentration": 0.20,  # 5 仓位 = 0.20 集中度
        "n_months": n_months,
        "std_monthly_ret": std_ret,
    }


def _build_grid() -> list[CompositeWeights]:
    """简化 P2 grid: ret/dd/turnover/cost 4 维, 每维 3 选项."""
    ret_w_options = [0.8, 1.0, 1.2]
    dd_w_options = [0.8, 1.0, 1.5]
    turnover_w_options = [0.3, 0.5, 0.8]
    cost_w_options = [0.5, 1.0, 1.5]
    grid: list[CompositeWeights] = []
    for r in ret_w_options:
        for d in dd_w_options:
            for t in turnover_w_options:
                for c in cost_w_options:
                    grid.append(CompositeWeights(
                        ret_w=r, dd_w=d, hp_w=0.0,
                        turnover_w=t, cost_w=c, capacity_w=0.5,
                        hp_penalty_mode="linear",
                    ))
    return grid


def main() -> int:
    parser = argparse.ArgumentParser(description="P2 composite weight grid search")
    parser.add_argument("--model-id", default="lgbm_baseline_v1")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    run_id = args.run_id or f"p2_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log.info(f"run_id={run_id}, model_id={args.model_id}")

    conn = duck_connect(str(DB_PATH))
    try:
        conn.execute(P2_RESULT_DDL)
        kpi = estimate_kpi_from_predictions(conn, args.model_id)
        log.info(f"KPI estimate: ann_ret={kpi['ann_ret']:.4f} max_dd={kpi['max_dd']:.4f} "
                 f"n_months={kpi.get('n_months', 0)}")

        grid = _build_grid()
        log.info(f"Grid size: {len(grid)}")
        results = []
        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        for i, w in enumerate(grid):
            s = compute_composite_score(
                ann_ret=kpi["ann_ret"], max_dd=kpi["max_dd"],
                avg_hp=kpi["avg_hp"], turnover=kpi["turnover"],
                tx_cost_pct=kpi["tx_cost_pct"], concentration=kpi["concentration"],
                weights=w,
            )
            conn.execute(
                "INSERT OR REPLACE INTO mart_p2_composite_result VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, i, args.model_id, w.ret_w, w.dd_w, w.hp_w,
                 w.turnover_w, w.cost_w, w.capacity_w, w.hp_penalty_mode,
                 kpi["ann_ret"], kpi["max_dd"], kpi["avg_hp"],
                 kpi["turnover"], kpi["tx_cost_pct"], kpi["concentration"],
                 s, built_at]
            )
            results.append((i, w, s))

        # Top 5 by composite score
        results.sort(key=lambda x: x[2], reverse=True)
        log.info("")
        log.info("=== Top 5 composite weights ===")
        log.info(f"{'rank':>4s} {'ret_w':>6s} {'dd_w':>6s} {'to_w':>6s} {'cost_w':>7s} {'score':>8s}")
        for rk, (i, w, s) in enumerate(results[:5], 1):
            log.info(f"  #{rk:<3d} {w.ret_w:>6.2f} {w.dd_w:>6.2f} {w.turnover_w:>6.2f} {w.cost_w:>7.2f} {s:>8.4f}")
        log.info("")
        log.info(f"All {len(grid)} grid points written to mart_p2_composite_result (run_id={run_id})")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
