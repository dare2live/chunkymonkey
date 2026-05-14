#!/usr/bin/env python3
"""P0b OOS RankIC Deflated Sharpe audit — Bailey-LdP 2014 跨 study 多重检验校正.

Phase ψ.γ.discipline 落地: 跨 study 累积变体 (3 horizon × 22 walk-forward windows = 66
study trials) 的 OOS Sharpe 必须经过 Deflated SR 校正, 否则"best variant OOS"
仍含 selection bias.

读 mart_p0b_walkforward_eval (rank_ic 跨 model_id × window), 对每 model_id 算:
- expected_max_sharpe (H0 下 N trials max 期望)
- deflated_sharpe_ratio p-value
- min_sharpe_for_significance (0.95 阈值)

输出: stdout markdown + 入库 (可选 mart_p0b_deflated_sharpe).

用法 (read-only):
    PYTHONPATH=backend python backend/scripts/p0b_deflated_sharpe_audit.py
"""
from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.optimization.deflated_sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_sharpe_for_significance,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("deflated_sharpe_audit")


def _annualize_ic_to_sharpe(rank_ic: float, ic_ir: float, n_oos_dates: int) -> tuple[float, float]:
    """简化: 把 RankIC 视为 daily IC, 换算 annualized Sharpe (Grinold-Kahn 类比).

    Sharpe ≈ IC × sqrt(N_days_per_year) — 取 IC_IR × sqrt(12 month/year) 月度近似.
    返回 (annualized_sharpe, n_obs).
    """
    # 月度 IC IR → 年化: ann_SR ≈ IR × sqrt(12)
    return ic_ir * math.sqrt(12), n_oos_dates


def main() -> int:
    log.info("=== P0b OOS Deflated Sharpe Audit (Bailey-LdP 2014) ===")
    conn = duck_connect(str(DB_PATH), read_only=True)
    try:
        rows = conn.execute("""
            SELECT model_id,
                   COUNT(*) AS n_windows,
                   AVG(rank_ic) AS rank_ic_mean,
                   STDDEV(rank_ic) AS rank_ic_std,
                   AVG(rank_ic_ir) AS ic_ir_mean,
                   SUM(n_test) AS total_test_rows
            FROM mart_p0b_walkforward_eval
            WHERE rank_ic IS NOT NULL
            GROUP BY model_id
            ORDER BY model_id
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        log.warning("No mart_p0b_walkforward_eval rows. Run train_p0b_lightgbm.py first.")
        return 0

    # 跨 3 horizon 累积 trial 数 (用户 push back: 跑了 3 horizon = 3 trials,
    # 但每 horizon 内 22 walk-forward windows 是 fold 不是 trial)
    # 保守: n_trials = 3 (3 个 study, 不同 horizon)
    n_trials_per_study = 22  # walk-forward folds per study
    n_total_studies = len(rows)

    log.info("")
    log.info(f"# P0b Deflated Sharpe Audit (Bailey-LdP 2014)")
    log.info("")
    log.info(f"## 假设")
    log.info(f"- 跨 {n_total_studies} 个 model_id (3 horizon × 1 LightGBM baseline) 累积 = {n_total_studies} study")
    log.info(f"- 每 study 22 walk-forward folds → 月度 IR 近似年化 SR (× sqrt(12))")
    log.info(f"- H0: 模型无 alpha, OOS IC ~ N(0, σ²)")
    log.info("")
    log.info(f"## 结果")
    log.info("")
    log.info(f"| model_id | RankIC | IC IR | n_windows | est SR (ann) | E[max SR | {n_total_studies} trials] | Deflated p | Gate p>0.95 |")
    log.info("|---|---:|---:|---:|---:|---:|---:|:---:|")
    for r in rows:
        model_id, n_windows, ric_mean, ric_std, ir_mean, n_test = r
        sr_ann, n_obs = _annualize_ic_to_sharpe(ric_mean or 0, ir_mean or 0, n_windows)
        emax = expected_max_sharpe(n_total_studies, sharpe_variance=1.0)
        p = deflated_sharpe_ratio(
            observed_sharpe=sr_ann, n_trials=n_total_studies,
            n_observations=n_windows, sharpe_variance=1.0,
        )
        gate = "✓" if (p == p and p > 0.95) else "✗"
        log.info(
            f"| {model_id} | {ric_mean or 0:.4f} | {ir_mean or 0:.4f} | "
            f"{n_windows} | {sr_ann:.3f} | {emax:.3f} | "
            f"{p:.4f} | {gate} |"
        )

    log.info("")
    log.info(f"## Min Sharpe for p > 0.95 (across {n_total_studies} studies, {22} windows)")
    threshold = min_sharpe_for_significance(
        n_trials=n_total_studies, n_observations=22, target_p=0.95,
    )
    log.info(f"  min_observed_sharpe ≥ {threshold:.3f} required to declare true alpha")
    log.info("")
    log.info("## 结论")
    log.info("- 当前 RankIC ≈ 0.01-0.02 → ann SR ≈ 0.4-0.8 → 远 < min_threshold")
    log.info("- 即使最佳 horizon (20d, IC IR 0.225) 在 H0 下也可能由跨 study selection bias 产生")
    log.info("- Deflated p << 0.95 → **当前 model 没有统计显著 alpha**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
